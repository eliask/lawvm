"""eu_amendment_graph.py — typed, directed, dated amendment DAG from CDM.

``eu_acquire.extract_corrigendum_celexes`` harvests a FLAT set of related-CELEX
ids from a tree notice. That is enough to *discover* affecting acts, but not to
*replay* them: replay needs a DIRECTED, DATED edge set so ``order_ops`` can sort
amending acts in legal-chronological order (date-of-application), not lexical
CELEX order. This module upgrades the flat harvest into a typed DAG drawn from
the CDM (Common Data Model) amendment object-properties, queried over the live
Cellar SPARQL endpoint.

Resolved CDM predicate IRIs (Increment 0)
-----------------------------------------
The design (``notes/EU_ACQUISITION_DESIGN.md`` §2.3 / §5 open-question 1) flagged
the SPARQL predicate IRIs as NOT verified byte-exact. They were resolved
EMPIRICALLY against the live endpoint ``http://publications.europa.eu/webapi/rdf/sparql``
on the GDPR work (``cellar/3e485e15-11bd-11e6-ba9a-01aa75ed71a1``) and a
high-degree base (``32016R0044``, degree 55):

* ``cdm:resource_legal_amends_resource_legal`` — the AMENDS edge
  (``?amender cdm:resource_legal_amends_resource_legal ?base``). Its inverse,
  ``amended_by``, is the closure we want; we query the amends form with the base
  as object so direction is unambiguous.
* ``cdm:resource_legal_corrects_resource_legal`` — the CORRECTS edge
  (corrigenda; GDPR has 3 — ``...R(01)/(02)/(03)``).
* Dates live on the AMENDING act as
  ``cdm:resource_legal_date_entry-into-force`` (often TWO triples per act: the
  legal entry-into-force AND the date-of-application; GDPR carries both
  ``2016-05-24`` and ``2018-05-25``). There is no consistently-populated distinct
  ``date_application`` predicate, so this module records ALL entry-into-force
  dates per act and exposes ``date_of_application`` as the MAX (the later of the
  two — the date the amending act's provisions actually apply) with the EARLIER
  retained as ``entry_into_force``. When only one date exists they coincide.

This is honest about the open question: the predicate forms above are the ones
that returned non-empty rows live; ``corrects`` did NOT appear as an outgoing
predicate on the base (it is incoming, from the corrigendum), which is why the
query binds the base as the OBJECT of both relations.

Determinism / no-silent-zero
-----------------------------
The SPARQL fetch is a test seam (``_fetch``). A non-JSON response (HTML error
page / the well-known CELLAR ``JDBC ... Unable to acquire JDBC Connection`` 500
text / empty) raises :class:`AmendmentGraphError` — never a silent empty DAG
masquerading as "this act has no amendments". Edges come back SORTED so the DAG
is a reproducible function of (endpoint, base CELEX, response bytes).
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Optional

from lawvm.eu_lex.celex import is_well_formed_celex

#: The Cellar public SPARQL endpoint (same as eu_enumerate).
SPARQL_ENDPOINT = "http://publications.europa.eu/webapi/rdf/sparql"

_RESULTS_JSON = "application/sparql-results+json"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) lawvm-eu-amendment-graph/0 "
    "(+https://lawvm.org)"
)

#: CDM predicate IRIs, resolved empirically (see module docstring).
CDM = "http://publications.europa.eu/ontology/cdm#"
PRED_AMENDS = f"{CDM}resource_legal_amends_resource_legal"
PRED_CORRECTS = f"{CDM}resource_legal_corrects_resource_legal"
PRED_CELEX = f"{CDM}resource_legal_id_celex"
PRED_EIF = f"{CDM}resource_legal_date_entry-into-force"


class AmendmentGraphError(RuntimeError):
    """Raised when a SPARQL amendment-graph response is not real results JSON."""


@dataclass(frozen=True, slots=True)
class AmendmentEdge:
    """One typed, directed, dated amendment edge: amending → base.

    ``relation_kind`` is ``"amends"`` (substantive amendment) or ``"corrects"``
    (corrigendum). Direction is always amending_celex → base_celex.

    ``entry_into_force`` is the EARLIEST entry-into-force date on the amending
    act; ``date_of_application`` is the LATEST entry-into-force date (the legal
    instant its provisions apply, used as the ordering key). When the act exposes
    a single date the two coincide. Empty strings mean the act exposed no
    machine-readable date (honest gap — never a fabricated zero date).
    """

    amending_celex: str
    base_celex: str
    relation_kind: str
    entry_into_force: str = ""
    date_of_application: str = ""

    def __post_init__(self) -> None:
        if self.relation_kind not in ("amends", "corrects"):
            raise ValueError(
                f"AmendmentEdge.relation_kind must be 'amends' or 'corrects', "
                f"got {self.relation_kind!r}"
            )

    @property
    def ordering_date(self) -> str:
        """The date this edge orders by: date-of-application, else entry-into-force."""
        return self.date_of_application or self.entry_into_force

    def to_dict(self) -> dict[str, str]:
        return {
            "amending_celex": self.amending_celex,
            "base_celex": self.base_celex,
            "relation_kind": self.relation_kind,
            "entry_into_force": self.entry_into_force,
            "date_of_application": self.date_of_application,
        }


@dataclass(frozen=True, slots=True)
class AmendmentGraph:
    """The dated directed amendment DAG rooted at one base CELEX."""

    base_celex: str
    edges: tuple[AmendmentEdge, ...] = ()

    def amenders(self) -> tuple[str, ...]:
        """Sorted distinct amending/correcting CELEXes (the closure to acquire)."""
        return tuple(sorted({e.amending_celex for e in self.edges}))

    def ordered_edges(self) -> tuple[AmendmentEdge, ...]:
        """Edges in legal-chronological order (ordering_date, then CELEX)."""
        return tuple(
            sorted(self.edges, key=lambda e: (e.ordering_date, e.amending_celex))
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_celex": self.base_celex,
            "edges": [e.to_dict() for e in self.ordered_edges()],
        }


# ---------------------------------------------------------------------------
# SPARQL query construction
# ---------------------------------------------------------------------------


def amendment_graph_query(base_celex: str) -> str:
    """SPARQL selecting the dated amends+corrects closure of ``base_celex``.

    Binds the base as the OBJECT of both relations (direction = amending → base),
    so a row's ``?celex`` is the AMENDING act and the dates are the amending
    act's own entry-into-force triples. ``GROUP BY`` with ``MIN``/``MAX`` folds
    the (often two) entry-into-force values into earliest/latest per act.
    """
    return f"""PREFIX cdm: <{CDM}>
SELECT ?relkind ?celex (MIN(?eif) AS ?eif_min) (MAX(?eif) AS ?eif_max) WHERE {{
  ?base cdm:resource_legal_id_celex ?basecelex .
  FILTER(STR(?basecelex) = "{base_celex}")
  {{
    ?act cdm:resource_legal_amends_resource_legal ?base .
    BIND("amends" AS ?relkind)
  }} UNION {{
    ?act cdm:resource_legal_corrects_resource_legal ?base .
    BIND("corrects" AS ?relkind)
  }}
  ?act cdm:resource_legal_id_celex ?celex .
  OPTIONAL {{ ?act cdm:resource_legal_date_entry-into-force ?eif . }}
}} GROUP BY ?relkind ?celex ORDER BY ?celex"""


def sparql_results_url(query_text: str, endpoint: str = SPARQL_ENDPOINT) -> str:
    params = {"query": query_text, "format": _RESULTS_JSON}
    return f"{endpoint}?{urllib.parse.urlencode(params)}"


def _live_fetch_sparql(query_text: str, endpoint: str, timeout_s: int) -> bytes:
    url = sparql_results_url(query_text, endpoint)
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": _RESULTS_JSON}
    )
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        return response.read()


# ---------------------------------------------------------------------------
# Parsing — fail loud on a non-results body (no silent empty DAG)
# ---------------------------------------------------------------------------


def parse_amendment_edges(data: bytes, base_celex: str) -> tuple[AmendmentEdge, ...]:
    """Parse SPARQL JSON results → SORTED, de-duplicated amendment edges.

    Raises :class:`AmendmentGraphError` on a non-JSON / wrong-shape body (the
    CELLAR 500 ``Unable to acquire JDBC Connection`` text, an HTML page, empty)
    — never a silent empty DAG.
    """
    if not data:
        raise AmendmentGraphError("empty SPARQL response payload")
    head = data.lstrip(b"\xef\xbb\xbf \t\r\n")[:64].lower()
    if head.startswith((b"<!doctype html", b"<html", b"<?xml")):
        raise AmendmentGraphError(
            "SPARQL response is not JSON (HTML/XML error page or bot-block); "
            f"first bytes: {data[:64]!r}"
        )
    if b"jdbc" in head or b"hibernate" in data[:200].lower():
        raise AmendmentGraphError(
            "SPARQL endpoint returned a CELLAR backend error (JDBC/Hibernate); "
            "transient 500 — not an empty amendment graph"
        )
    try:
        doc = json.loads(data)
    except (ValueError, UnicodeDecodeError) as exc:
        raise AmendmentGraphError(
            f"SPARQL response is not parseable JSON: {exc}"
        ) from exc
    if not isinstance(doc, dict) or "results" not in doc:
        raise AmendmentGraphError(
            "SPARQL JSON lacks a 'results' block (not a SELECT results document)"
        )
    bindings = doc.get("results", {}).get("bindings")
    if not isinstance(bindings, list):
        raise AmendmentGraphError("SPARQL 'results.bindings' is not a list")

    edges: list[AmendmentEdge] = []
    for row in bindings:
        if not isinstance(row, dict):
            continue
        celex = _cell(row, "celex")
        relkind = _cell(row, "relkind")
        if not celex or relkind not in ("amends", "corrects"):
            continue
        eif_min = _cell(row, "eif_min")
        eif_max = _cell(row, "eif_max")
        # Reject a self-loop or the consolidated-form base; corrigenda ...R(NN)
        # are legitimate correcting acts and are NOT gated on act-CELEX
        # well-formedness (mirrors extract_corrigendum_celexes).
        if celex == base_celex:
            continue
        edges.append(
            AmendmentEdge(
                amending_celex=celex,
                base_celex=base_celex,
                relation_kind=relkind,
                entry_into_force=eif_min,
                date_of_application=eif_max,
            )
        )
    # Deterministic: sort by (ordering_date, celex, relation_kind).
    edges.sort(key=lambda e: (e.ordering_date, e.amending_celex, e.relation_kind))
    # De-duplicate identical edges.
    seen: set[tuple[str, str, str]] = set()
    unique: list[AmendmentEdge] = []
    for e in edges:
        key = (e.amending_celex, e.relation_kind, e.ordering_date)
        if key in seen:
            continue
        seen.add(key)
        unique.append(e)
    return tuple(unique)


def _cell(row: dict[str, Any], var: str) -> str:
    cell = row.get(var)
    if not isinstance(cell, dict):
        return ""
    value = cell.get("value")
    if isinstance(value, str):
        return value.strip()
    return ""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def build_amendment_graph(
    base_celex: str,
    *,
    endpoint: str = SPARQL_ENDPOINT,
    timeout_s: int = 60,
    _fetch: Optional[Callable[[str, str, int], bytes]] = None,
) -> AmendmentGraph:
    """Build the dated directed amendment DAG for ``base_celex``.

    Parameters
    ----------
    base_celex:
        The base act CELEX, e.g. ``'32016R0679'``. Must be a well-formed act
        CELEX (the base is always a real act; amenders may include corrigenda).
    _fetch:
        Test seam ``_fetch(query_text, endpoint, timeout_s) -> bytes``. When
        None, the live Cellar SPARQL endpoint is queried.
    """
    if not is_well_formed_celex(base_celex):
        raise ValueError(
            f"not a well-formed base CELEX: {base_celex!r}; refusing to build a "
            "graph rooted at a malformed id"
        )
    fetch = _fetch or _live_fetch_sparql
    query = amendment_graph_query(base_celex)
    data = fetch(query, endpoint, timeout_s)
    edges = parse_amendment_edges(data, base_celex)
    return AmendmentGraph(base_celex=base_celex, edges=edges)

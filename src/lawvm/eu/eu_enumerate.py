"""eu_enumerate.py — ENUMERATION-driven acquisition for the EU lane.

``eu_acquire.py`` is *demand* mode: a CELEX is requested on demand, and the only
honest universe claim is ``curated_slice`` / ``closed_world_claim=false`` (see
``eu_acquire.default_universe``). That can never bound the *uncited-but-operative*
regulation: a directly-applicable EU regulation is operative Finnish law even if
no Finnish statute cites it, so citation-closure never reaches it.

This module closes that gap. It drives an **enumeration** of "regulations in
force" off the official EUR-Lex Cellar SPARQL registry, freezes the result into a
dated, content-hashed :class:`EnumerationSnapshot`, and wires that snapshot into a
:class:`~lawvm.substrate.corpus_totality.CorpusTotalityUniverse` whose
``universe_kind`` is a *manifest/registry* kind — the only kinds for which
``closed_world_claim=true`` is legal. The completeness claim is now grounded in a
dated registry snapshot, not a guess.

The SPARQL registry
-------------------
Endpoint: ``http://publications.europa.eu/webapi/rdf/sparql`` (the Cellar public
SPARQL endpoint). The query selects works whose ``cdm:work_has_resource-type`` is
the requested resource-type authority (REG = regulation, DIR = directive, …) and
whose ``cdm:resource_legal_in-force`` is the requested boolean, returning each
work's ``cdm:resource_legal_id_celex``. The resource-type and in-force parameters
are explicit (:class:`EnumerationQuery`) so directives / decisions / repealed
acts can be enumerated later by changing the parameters, not the code.

Provenance / reuse
------------------
EUR-Lex / Cellar data is freely reusable under Commission Decision 2011/833/EU.
The OJ is the authentic text; this enumeration is *for information only*. Both
facts are recorded in the witness provenance (:data:`_EU_REUSE_NOTICE`).

Determinism
-----------
No ``datetime.now()`` / ``random`` in pure logic. The snapshot date is PASSED IN;
the CELEX list is SORTED + de-duplicated before hashing, so the snapshot id is a
reproducible function of (endpoint, query text, date, parameters, CELEX set).

Honest sampling
---------------
Enumerating the FULL regulations-in-force list is ~thousands of works; the
snapshot + count is the completeness artifact, acquisition can be lazy.
:func:`enumerate_and_acquire` drives ``eu_acquire.acquire_celex`` over a BOUNDED
sample and records that the acquisition was sampled
(:attr:`EnumerateAcquireRun.acquisition_sampled` + ``sample_limit``) — never a
silent truncation.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from lawvm.eu import eu_acquire
from lawvm.eu_lex.celex import is_well_formed_celex
from lawvm.substrate.canonical_json import canonical_json_bytes, semantic_hash
from lawvm.substrate.corpus_totality import CorpusTotalityUniverse

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

#: The Cellar public SPARQL endpoint (registry source).
SPARQL_ENDPOINT = "http://publications.europa.eu/webapi/rdf/sparql"

#: Realistic UA — the human operator's agent (Cellar tolerates a named UA).
USER_AGENT = "LawVM EU Enum/0.1 (+https://op.europa.eu/en/web/cellar/home)"

#: SPARQL JSON results media type.
_RESULTS_JSON = "application/sparql-results+json"

DEFAULT_TIMEOUT_S = 180

#: Resource-type authority URIs (the CELEX 2nd-letter classes). Explicit so the
#: enumeration parameterizes by changing the value, not the query body.
RESOURCE_TYPE_AUTHORITY_BASE = (
    "http://publications.europa.eu/resource/authority/resource-type"
)
RESOURCE_TYPE_REGULATION = f"{RESOURCE_TYPE_AUTHORITY_BASE}/REG"
RESOURCE_TYPE_DIRECTIVE = f"{RESOURCE_TYPE_AUTHORITY_BASE}/DIR"
RESOURCE_TYPE_DECISION = f"{RESOURCE_TYPE_AUTHORITY_BASE}/DEC"

#: EU reuse provenance (Commission Decision 2011/833/EU). The OJ is authentic;
#: this enumeration is for-information-only.
_EU_REUSE_NOTICE = (
    "© European Union, http://eur-lex.europa.eu, reused under Commission "
    "Decision 2011/833/EU. The Official Journal is the authentic text; this "
    "enumeration is provided for information only."
)

#: Schema id for the enumeration snapshot canonical body.
_SCHEMA_ENUMERATION_SNAPSHOT = "lawvm.eu_enumeration_snapshot.v0"

#: Enumeration policy id recorded on the universe (manifest/registry mode).
ENUMERATION_POLICY_ID = "lawvm.enumeration.eu_cellar.regulations_in_force.v0"


class EnumerationError(RuntimeError):
    """The SPARQL enumeration response is not real SPARQL JSON results."""


# --------------------------------------------------------------------------- #
# Query parameterization
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class EnumerationQuery:
    """The explicit, parameterized SPARQL enumeration over the Cellar registry.

    ``resource_type_uri`` and ``in_force`` are the knobs; ``directives`` /
    ``decisions`` / ``repealed`` are enumerated later by flipping them, NOT by
    rewriting the query body. :meth:`sparql_text` renders the deterministic
    query string (the exact text that is hashed into the snapshot).
    """

    resource_type_uri: str = RESOURCE_TYPE_REGULATION
    in_force: bool = True

    def sparql_text(self) -> str:
        """Render the deterministic SPARQL query text for these parameters."""
        in_force_lit = "true" if self.in_force else "false"
        return (
            "PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>\n"
            "PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>\n"
            "SELECT DISTINCT ?celex WHERE {\n"
            "  ?work cdm:work_has_resource-type "
            f"<{self.resource_type_uri}> .\n"
            "  ?work cdm:resource_legal_in-force "
            f'"{in_force_lit}"^^xsd:boolean .\n'
            "  ?work cdm:resource_legal_id_celex ?celex .\n"
            "} ORDER BY ?celex"
        )

    @property
    def query_sha256(self) -> str:
        """``sha256:…`` of the exact query text (a snapshot enumeration ref)."""
        return semantic_hash(self.sparql_text())


# --------------------------------------------------------------------------- #
# The dated, content-hashed snapshot
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class EnumerationSnapshot:
    """A dated, reproducible snapshot of an enumeration over the Cellar registry.

    Carries the endpoint, the exact query text, the snapshot date (PASSED IN —
    no ``datetime.now()``), the resource-type / in-force parameters, and the
    SORTED + de-duplicated CELEX list with its count. :attr:`snapshot_id` is the
    canonical-JSON content hash, so the universe is a checkable, reproducible
    object: identical (endpoint, query, date, params, CELEX set) → identical id.
    """

    endpoint: str
    query_text: str
    snapshot_date: str
    """ISO date 'YYYY-MM-DD' of the snapshot (caller-supplied)."""
    resource_type_uri: str
    in_force: bool
    celexes: tuple[str, ...]
    """SORTED, de-duplicated CELEX ids (normalized at construction)."""

    def __post_init__(self) -> None:
        # Normalize at construction: sorted + de-duplicated. The snapshot id must
        # not depend on the wire order or on duplicate bindings (a CELEX can be
        # bound by several manifestations).
        normalized = tuple(sorted({c for c in self.celexes if c}))
        object.__setattr__(self, "celexes", normalized)

    @property
    def count(self) -> int:
        return len(self.celexes)

    def to_canonical_dict(self) -> dict[str, Any]:
        """The hashed body (NO snapshot_id member — §1.3 of canonical_json)."""
        return {
            "schema": _SCHEMA_ENUMERATION_SNAPSHOT,
            "endpoint": self.endpoint,
            "query_text": self.query_text,
            "snapshot_date": self.snapshot_date,
            "resource_type_uri": self.resource_type_uri,
            "in_force": self.in_force,
            "celexes": list(self.celexes),
            "count": self.count,
        }

    @property
    def snapshot_id(self) -> str:
        """``sha256:…`` content hash over the canonical body (reproducible)."""
        return semantic_hash(self.to_canonical_dict())

    @property
    def witness_locator(self) -> str:
        """Identity-keyed witness locator for the snapshot in the farchive.

        e.g. ``eu-enumeration://regulations-in-force/2026-06-22`` for an
        in-force REG enumeration; the resource-type slug varies for other kinds.
        """
        slug = _resource_type_slug(self.resource_type_uri)
        state = "in-force" if self.in_force else "not-in-force"
        return f"eu-enumeration://{slug}-{state}/{self.snapshot_date}"

    def witness_bytes(self) -> bytes:
        """The witness payload stored in farchive: the canonical snapshot JSON."""
        return canonical_json_bytes(self.to_canonical_dict())


def _resource_type_slug(resource_type_uri: str) -> str:
    """Map a resource-type authority URI → a stable lower-case slug."""
    tail = resource_type_uri.rstrip("/").rsplit("/", 1)[-1].upper()
    return {
        "REG": "regulations",
        "DIR": "directives",
        "DEC": "decisions",
    }.get(tail, tail.lower())


# --------------------------------------------------------------------------- #
# SPARQL fetch + parse
# --------------------------------------------------------------------------- #


def sparql_results_url(query_text: str, endpoint: str = SPARQL_ENDPOINT) -> str:
    """The GET URL for a SPARQL query returning JSON results."""
    params = {"query": query_text, "format": _RESULTS_JSON}
    return f"{endpoint}?{urllib.parse.urlencode(params)}"


def _live_fetch_sparql(query_text: str, endpoint: str, timeout_s: int) -> bytes:
    """Fetch SPARQL JSON results over HTTP (realistic UA). Read-only."""
    url = sparql_results_url(query_text, endpoint)
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": _RESULTS_JSON}
    )
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        return response.read()


def parse_sparql_celexes(data: bytes) -> tuple[str, ...]:
    """Parse a SPARQL JSON results payload → SORTED, de-duplicated CELEX list.

    Fails loudly (:class:`EnumerationError`) on anything that is not real SPARQL
    JSON results (an HTML error page / bot-block / empty / wrong shape) — never
    a silent empty list masquerading as "zero regulations".
    """
    if not data:
        raise EnumerationError("empty SPARQL response payload")
    head = data.lstrip(b"\xef\xbb\xbf \t\r\n")[:64].lower()
    if head.startswith((b"<!doctype html", b"<html", b"<?xml")):
        raise EnumerationError(
            "SPARQL response is not JSON (HTML/XML error page or bot-block); "
            f"first bytes: {data[:64]!r}"
        )
    try:
        doc = json.loads(data)
    except (ValueError, UnicodeDecodeError) as exc:
        raise EnumerationError(f"SPARQL response is not parseable JSON: {exc}") from exc
    if not isinstance(doc, dict) or "results" not in doc:
        raise EnumerationError(
            "SPARQL JSON lacks a 'results' block (not a SELECT results document)"
        )
    bindings = doc.get("results", {}).get("bindings")
    if not isinstance(bindings, list):
        raise EnumerationError("SPARQL 'results.bindings' is not a list")
    celexes: set[str] = set()
    for row in bindings:
        cell = row.get("celex") if isinstance(row, dict) else None
        if not isinstance(cell, dict):
            continue
        value = cell.get("value")
        if isinstance(value, str) and value.strip():
            celexes.add(value.strip())
    return tuple(sorted(celexes))


def enumerate_snapshot(
    *,
    snapshot_date: str,
    query: EnumerationQuery | None = None,
    endpoint: str = SPARQL_ENDPOINT,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    _fetch: Any = None,
) -> EnumerationSnapshot:
    """Run the enumeration and freeze the result into an :class:`EnumerationSnapshot`.

    Parameters
    ----------
    snapshot_date:
        ISO 'YYYY-MM-DD' (caller-supplied; NEVER ``datetime.now()`` here).
    query:
        The parameterized :class:`EnumerationQuery`. Default: regulations
        in force.
    endpoint:
        SPARQL endpoint. Default: :data:`SPARQL_ENDPOINT`.
    _fetch:
        Test seam ``_fetch(query_text, endpoint, timeout_s) -> bytes``. When
        None, the live HTTP fetch is used.
    """
    q = query or EnumerationQuery()
    query_text = q.sparql_text()
    fetch = _fetch or _live_fetch_sparql
    data = fetch(query_text, endpoint, timeout_s)
    celexes = parse_sparql_celexes(data)
    return EnumerationSnapshot(
        endpoint=endpoint,
        query_text=query_text,
        snapshot_date=snapshot_date,
        resource_type_uri=q.resource_type_uri,
        in_force=q.in_force,
        celexes=celexes,
    )


# --------------------------------------------------------------------------- #
# Universe wiring (the whole point: closed_world_claim=TRUE)
# --------------------------------------------------------------------------- #


def snapshot_universe(
    snapshot: EnumerationSnapshot,
    *,
    universe_kind: str = "static_manifest",
) -> CorpusTotalityUniverse:
    """Build the closed-world universe grounded in a dated registry snapshot.

    A dated enumeration from the official Cellar registry is a *static manifest*
    (a fixed listing as of the snapshot date), so ``closed_world_claim=true`` is
    LEGAL here (it is illegal only for ``observed_crawl``). The
    ``enumeration_source_refs`` carry the snapshot witness locator + the snapshot
    content id + the query hash, so the completeness claim is auditable back to
    the exact dated registry state.

    ``universe_kind`` may be ``"official_signed_registry"`` once a Cellar-signed
    enumeration root is captured; ``"static_manifest"`` is the honest default
    (we hold the dated snapshot, not yet an OP signature over it).
    """
    if universe_kind not in ("static_manifest", "official_signed_registry"):
        raise ValueError(
            "snapshot_universe requires a manifest/registry universe_kind for a "
            f"closed-world claim; got {universe_kind!r}"
        )
    return CorpusTotalityUniverse(
        universe_kind=universe_kind,
        enumeration_source_refs=(
            snapshot.witness_locator,
            snapshot.snapshot_id,
            _snapshot_query_sha256(snapshot),
        ),
        enumeration_policy_id=ENUMERATION_POLICY_ID,
        closed_world_claim=True,
    )


def _snapshot_query_sha256(snapshot: EnumerationSnapshot) -> str:
    """``sha256:…`` of the snapshot's frozen query text (an enumeration ref).

    The snapshot stores the EXACT query text it ran, so the query hash is a
    function of the snapshot alone (the query object need not be retained).
    """
    return semantic_hash(snapshot.query_text)


# --------------------------------------------------------------------------- #
# Snapshot persistence (store the snapshot itself as a witness)
# --------------------------------------------------------------------------- #


def store_snapshot(
    farchive: Any,
    snapshot: EnumerationSnapshot,
    *,
    observed_at: datetime,
) -> str:
    """Store the snapshot's canonical JSON as a content-addressed witness.

    Returns the witness locator. Idempotent at the farchive layer (identical
    canonical bytes → same digest). Records the EU reuse provenance.
    """
    locator = snapshot.witness_locator
    farchive.store(
        locator,
        snapshot.witness_bytes(),
        storage_class="json",
        metadata={
            "source_surface": "eu-cellar-sparql-enumeration",
            "schema": _SCHEMA_ENUMERATION_SNAPSHOT,
            "snapshot_id": snapshot.snapshot_id,
            "snapshot_date": snapshot.snapshot_date,
            "endpoint": snapshot.endpoint,
            "resource_type_uri": snapshot.resource_type_uri,
            "in_force": "true" if snapshot.in_force else "false",
            "count": str(snapshot.count),
            "query_sha256": _snapshot_query_sha256(snapshot),
            "reuse_notice": _EU_REUSE_NOTICE,
            "copyright": "© European Union",
        },
        observed_at=observed_at,
    )
    return locator


# --------------------------------------------------------------------------- #
# Orchestration: enumerate (full) + acquire (bounded sample)
# --------------------------------------------------------------------------- #


@dataclass
class EnumerateAcquireRun:
    """Provenance for one enumerate-then-acquire orchestration run."""

    snapshot: EnumerationSnapshot
    universe: CorpusTotalityUniverse
    snapshot_locator: str
    enumerated_count: int
    acquisition_sampled: bool
    sample_limit: int | None
    acquired_celexes: tuple[str, ...] = ()
    acquire_runs: list[eu_acquire.CelexIngestRun] = field(default_factory=list)
    non_act_celexes_skipped: tuple[str, ...] = ()
    """Enumerated ids in the sample window that are not well-formed ACT CELEX
    (e.g. corrigendum ``...R(NN)`` ids). They REMAIN in the snapshot (real
    registry members), but ``acquire_celex`` only walks act-level works, so they
    are skipped for acquisition and recorded here — never silently dropped."""


def enumerate_and_acquire(
    snapshot: EnumerationSnapshot,
    *,
    fetched_at: datetime,
    farchive: Any,
    universe_kind: str = "static_manifest",
    sample_limit: int | None = 5,
    language: str = "fin",
    fmt: str = "fmx4",
    acquire: bool = True,
    _acquire_celex: Any = None,
) -> EnumerateAcquireRun:
    """Store the snapshot, build the closed-world universe, and acquire a SAMPLE.

    The FULL enumeration (``snapshot.count``) is the completeness artifact;
    acquisition is lazy and BOUNDED by ``sample_limit`` (None = acquire all —
    use only off-line / on a small snapshot). When the enumerated set exceeds
    the limit, ``acquisition_sampled`` is True and ``sample_limit`` is recorded
    on the run, so the cap is OWNED, never a silent truncation.

    ``_acquire_celex`` is a test seam with the signature of
    :func:`eu_acquire.acquire_celex`.
    """
    snapshot_locator = store_snapshot(farchive, snapshot, observed_at=fetched_at)
    universe = snapshot_universe(snapshot, universe_kind=universe_kind)

    enumerated = snapshot.celexes
    # The snapshot keeps EVERY enumerated id (including corrigendum ``...R(NN)``
    # ids — real registry members). Acquisition only walks act-level works, so we
    # partition: well-formed act CELEX are acquirable; the rest are recorded as
    # skipped (never silently dropped).
    acquirable = tuple(c for c in enumerated if is_well_formed_celex(c))

    if sample_limit is None:
        window = acquirable
        sampled = False
    else:
        window = acquirable[:sample_limit]
        sampled = len(acquirable) > sample_limit

    # Non-act ids that fell within the same enumeration prefix as the acquired
    # window — owned, not silently dropped.
    window_set = set(window)
    last_window = window[-1] if window else ""
    non_act_in_window = tuple(
        c
        for c in enumerated
        if not is_well_formed_celex(c)
        and c not in window_set
        and (not last_window or c <= last_window)
    )

    run = EnumerateAcquireRun(
        snapshot=snapshot,
        universe=universe,
        snapshot_locator=snapshot_locator,
        enumerated_count=len(enumerated),
        acquisition_sampled=sampled,
        sample_limit=sample_limit,
        non_act_celexes_skipped=non_act_in_window,
    )

    if not acquire:
        return run

    acquire_fn = _acquire_celex or eu_acquire.acquire_celex
    acquired: list[str] = []
    for celex in window:
        ingest = acquire_fn(
            celex,
            fetched_at=fetched_at,
            language=language,
            fmt=fmt,
            farchive=farchive,
            universe=universe,
        )
        run.acquire_runs.append(ingest)
        acquired.append(celex)
    run.acquired_celexes = tuple(acquired)
    return run


# --------------------------------------------------------------------------- #
# Convenience: the canonical regulations-in-force query
# --------------------------------------------------------------------------- #


def regulations_in_force_query() -> EnumerationQuery:
    """The canonical 'regulations in force' enumeration query."""
    return EnumerationQuery(
        resource_type_uri=RESOURCE_TYPE_REGULATION, in_force=True
    )


def directives_in_force_query() -> EnumerationQuery:
    """The 'directives in force' enumeration query (parameter flip demo)."""
    return EnumerationQuery(
        resource_type_uri=RESOURCE_TYPE_DIRECTIVE, in_force=True
    )

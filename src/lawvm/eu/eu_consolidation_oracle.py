"""eu_consolidation_oracle.py — acquire a sector-0 consolidation at a PIT + diff.

Increment 1 goal 2 (the real consolidation-PIT oracle). The native replay
(``fmx4_amendment_grammar`` → ``eu_ordering`` → ``apply_eu_ops_conserved``)
reconstructs a point-in-time body; the EUR-Lex SECTOR-0 consolidation is the
Office's editorial rendering of the same PIT. This module acquires that
consolidation and feeds it to the existing non-repairing comparator
(``eu_oracle_divergence.compare_replay_to_consolidation``).

Consolidated CELEX form (design §2.2): ``0YYYY<LETTER><NNNN>-YYYYMMDD`` — the
basic act's number with sector ``0`` and the date-of-application of the last
incorporated amending act as a suffix (e.g. ``02016R0044-20160401``). These texts
have "no legal value … no guarantee [of] the latest state" (EUR-Lex), so the
comparator NEVER repairs the replay toward them — divergence is a first-class
finding (the ``authoritative oracle ≠ correct`` regime already first-class in
LawVM; cf. EE oracle_suspect).

Two-lane honesty (design §4 / observed live in Increment 0+1): the SPARQL lane
that ENUMERATES the consolidation series is up while the REST byte lane that
FETCHES the consolidated FMX4 intermittently returns HTTP 5xx
(``502 Bad Gateway`` / ``Unable to acquire JDBC Connection``). A REST failure is
recorded as a typed :class:`ConsolidationAcquisitionFailure` — NEVER a silent
empty oracle masquerading as "the consolidation matches the replay".
"""

from __future__ import annotations

import re
from typing import Callable, Optional

from lawvm.core.ir import IRStatute
from lawvm.eu.eu_oracle_divergence import (
    OracleComparison,
    compare_replay_to_consolidation,
)

__all__ = [
    "ConsolidationAcquisitionFailure",
    "consolidated_celex",
    "parse_consolidation_date",
    "build_consolidation_oracle",
    "enumerate_consolidation_series",
    "fetch_consolidation_bytes",
]

#: A consolidated CELEX: sector 0, year, descriptor letter, number, ``-`` date.
_CONSOLIDATED_RE = re.compile(r"^0\d{4}[A-Z]\d+-(?P<date>\d{8})$")

#: The CDM predicate a consolidated act uses to name the base act it consolidates
#: — resolved EMPIRICALLY against the live Cellar endpoint (the design flagged the
#: consolidation predicate as unverified; ``resource_legal_consolidates_resource_legal``
#: returns 0 rows, ``act_consolidated_consolidates_resource_legal`` is the one that
#: binds, e.g. ``02022R2309-*`` → ``32022R2309``). Its outgoing form (consolidated →
#: base) is queried with the base as OBJECT so direction is unambiguous.
_CDM = "http://publications.europa.eu/ontology/cdm#"
_PRED_CONSOLIDATES = f"{_CDM}act_consolidated_consolidates_resource_legal"
_PRED_CELEX = f"{_CDM}resource_legal_id_celex"


class ConsolidationAcquisitionFailure(RuntimeError):
    """Raised when the consolidated manifestation could not be acquired/parsed.

    Carries the typed reason (REST 5xx, non-XML body, parse failure) so the
    caller records a recorded gap, never a fabricated agreement.
    """


def consolidated_celex(base_celex: str, as_of: str) -> str:
    """Build the sector-0 consolidated CELEX for ``base_celex`` at ``as_of``.

    ``base_celex`` is a well-formed act CELEX (``32016R0044``); ``as_of`` is a
    PIT date ``YYYY-MM-DD`` or ``YYYYMMDD``. Returns ``02016R0044-20160401``.
    """
    digits = as_of.replace("-", "")
    if not re.fullmatch(r"\d{8}", digits):
        raise ValueError(
            f"as_of must be YYYY-MM-DD or YYYYMMDD, got {as_of!r}"
        )
    if not re.fullmatch(r"[1-9]\d{4}[A-Z]\d+", base_celex):
        raise ValueError(
            f"base_celex must be a well-formed act CELEX, got {base_celex!r}"
        )
    # Sector 0 = consolidated; replace the leading sector digit with 0.
    return f"0{base_celex[1:]}-{digits}"


def parse_consolidation_date(consolidated: str) -> str:
    """Extract the ``YYYYMMDD`` date suffix of a consolidated CELEX, or raise."""
    m = _CONSOLIDATED_RE.match(consolidated)
    if not m:
        raise ValueError(
            f"not a consolidated CELEX (0YYYY<L><N>-YYYYMMDD): {consolidated!r}"
        )
    return m.group("date")


def build_consolidation_oracle(
    replayed: IRStatute,
    *,
    base_celex: str,
    as_of: str,
    fetch_consolidation: Callable[[str], bytes],
    parse_fmx4_bytes: Optional[Callable[[bytes, str], IRStatute]] = None,
) -> OracleComparison:
    """Acquire the sector-0 consolidation at ``as_of`` and diff it vs ``replayed``.

    Parameters
    ----------
    replayed:
        The native-replay PIT body (the LawVM-native reconstruction).
    base_celex / as_of:
        Identify the consolidated manifestation (``consolidated_celex``).
    fetch_consolidation:
        ``fetch_consolidation(consolidated_celex) -> fmx4_bytes``. The byte lane.
        Raise (any exception) on a REST failure; it is wrapped into a typed
        :class:`ConsolidationAcquisitionFailure` (never a silent empty oracle).
    parse_fmx4_bytes:
        ``parse_fmx4_bytes(bytes, celex) -> IRStatute``. Defaults to the grafter
        (``parse_eu_regulation_ir`` over a temp file). A parse failure is also a
        typed acquisition failure.

    Returns
    -------
    An :class:`OracleComparison` — the per-article evidence ledger. NEVER repairs
    the replay toward the consolidation.
    """
    cons_celex = consolidated_celex(base_celex, as_of)
    try:
        raw = fetch_consolidation(cons_celex)
    except Exception as exc:  # noqa: BLE001 — any byte-lane failure is a typed gap
        raise ConsolidationAcquisitionFailure(
            f"could not acquire consolidation {cons_celex}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    if not raw:
        raise ConsolidationAcquisitionFailure(
            f"consolidation {cons_celex} returned empty bytes (not an empty "
            "oracle — a recorded acquisition gap)"
        )

    parser = parse_fmx4_bytes or _default_parse_fmx4_bytes
    try:
        consolidated = parser(raw, cons_celex)
    except Exception as exc:  # noqa: BLE001
        raise ConsolidationAcquisitionFailure(
            f"could not parse consolidation {cons_celex}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc

    return compare_replay_to_consolidation(
        replayed, consolidated, as_of=as_of, base_celex=base_celex
    )


def enumerate_consolidation_series(
    base_celex: str,
    *,
    endpoint: str | None = None,
    timeout_s: int = 60,
    _fetch: Optional[Callable[[str, str, int], bytes]] = None,
) -> tuple[str, ...]:
    """Enumerate the SORTED dated sector-0 consolidated CELEXes of ``base_celex``.

    Queries the live Cellar SPARQL endpoint for every act that
    ``act_consolidated_consolidates`` the base, returning the consolidated
    CELEXes (``0YYYY<L><N>-YYYYMMDD``) whose base is ``base_celex`` — the
    published consolidation snapshots EUR-Lex actually produced (NOT one per
    amender: EUR-Lex chooses its own snapshot dates). The returned dates are the
    honest, addressable PITs an oracle-touch score can run against; a base with
    zero published consolidations returns ``()`` (no oracle exists — the
    conservation-invariant lane is the correct fallback).

    Reuses the amendment-graph module's fail-loud SPARQL parse discipline (a
    non-JSON / CELLAR-500 body raises, never a silent empty series).

    Note: this is the LIVE enumeration lane (mirrors ``eu_amendment_graph`` which
    is also live-only). The consolidated bytes themselves are acquired by
    :func:`fetch_consolidation_bytes` and stored under the dated locator.
    """
    from lawvm.eu.eu_amendment_graph import (
        SPARQL_ENDPOINT,
        AmendmentGraphError,
        _live_fetch_sparql,
        sparql_results_url,  # noqa: F401 — kept import parity with the sibling
    )
    import json as _json

    ep = endpoint or SPARQL_ENDPOINT
    query = f"""PREFIX cdm: <{_CDM}>
SELECT ?conscelex WHERE {{
  ?base <{_PRED_CELEX}> ?bc . FILTER(STR(?bc) = "{base_celex}")
  ?cons <{_PRED_CONSOLIDATES}> ?base .
  ?cons <{_PRED_CELEX}> ?conscelex .
}} ORDER BY ?conscelex"""
    fetch = _fetch or _live_fetch_sparql
    data = fetch(query, ep, timeout_s)
    if not data:
        raise AmendmentGraphError("empty SPARQL consolidation-series response")
    head = data.lstrip(b"\xef\xbb\xbf \t\r\n")[:64].lower()
    if head.startswith((b"<!doctype html", b"<html", b"<?xml")) or b"jdbc" in head:
        raise AmendmentGraphError(
            "SPARQL consolidation-series response is not JSON (HTML/CELLAR-500); "
            f"first bytes: {data[:64]!r}"
        )
    doc = _json.loads(data)
    bindings = doc.get("results", {}).get("bindings", [])
    out: set[str] = set()
    for row in bindings:
        cell = row.get("conscelex", {})
        val = cell.get("value", "") if isinstance(cell, dict) else ""
        # Keep only well-formed dated consolidations of THIS base (guard against a
        # co-consolidated sibling act sharing a bundled notice, e.g. 02007R0715-*
        # appearing alongside 02008R0692-* — its date suffix must parse AND its
        # numeric root must equal the base's).
        m = _CONSOLIDATED_RE.match(val)  # lawvm-regex: witness_only shape-validates an already-acquired consolidated CELEX id from a SPARQL result cell (source-plane id census), not a post-parse semantic recognizer over statute text
        if m and val[1:].split("-", 1)[0] == base_celex[1:]:
            out.add(val)
    return tuple(sorted(out))


def fetch_consolidation_bytes(
    base_celex: str,
    as_of: str,
    *,
    language: str = "eng",
    timeout_s: int = 120,
    _fetch_notice: Optional[Callable[[str, str, int], tuple[bytes, dict]]] = None,
    _fetch_item: Optional[Callable[[str, int], tuple[bytes, dict]]] = None,
) -> bytes:
    """Acquire the primary Formex bytes of the sector-0 consolidation at ``as_of``.

    The offline byte lane the ``_doc`` said was "not in the archive": it fetches
    the DATED consolidated CELEX's own tree notice (``02022R2309-20240115`` — the
    bare base CELEX 404s; the consolidation is addressed by the dated form),
    selects the ``(language, fmx4)`` manifestation item that carries a real
    ``/DOC_N`` item URL (via the production ``_select_item_from_notice`` walker,
    which skips the item-less sibling manifestations a consolidated notice
    bundles), fetches it, and unwraps the ZIP to its primary ``CONS.ACT`` member.

    Returns the primary Formex XML bytes (graftable by ``parse_eu_regulation_ir``
    — its ``CONS.ACT``/``CONS.DOC`` branch grafts the consolidated shape). Raises
    :class:`ConsolidationAcquisitionFailure` on any transport / no-item / no-XML
    failure — NEVER an empty-oracle masquerade (the EU honesty regime).

    This is the ``fetch_consolidation`` callable :func:`build_consolidation_oracle`
    expects, curried on ``(base_celex, as_of)`` and returning bytes for its
    consolidated CELEX.
    """
    from urllib.error import HTTPError, URLError

    from lawvm.eu import cellar
    from lawvm.eu.eu_acquire import _select_item_from_notice

    cons_celex = consolidated_celex(base_celex, as_of)
    fetch_notice = _fetch_notice or _default_fetch_cons_notice
    fetch_item = _fetch_item or (lambda url, t: cellar._request_url(url, timeout_s=t))

    try:
        notice_bytes, _ = fetch_notice(cons_celex, language, timeout_s)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise ConsolidationAcquisitionFailure(
            f"consolidation {cons_celex} tree-notice fetch failed: "
            f"{type(exc).__name__}: {exc}"
        ) from exc

    item_url, _man_uri = _select_item_from_notice(notice_bytes, language, "fmx4")
    if not item_url:
        raise ConsolidationAcquisitionFailure(
            f"consolidation {cons_celex} exposes no {language} fmx4 manifestation "
            "item with a resolvable DOC url (not an empty oracle — a recorded gap)"
        )
    try:
        item_bytes, _ = fetch_item(item_url, timeout_s)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise ConsolidationAcquisitionFailure(
            f"consolidation {cons_celex} item fetch failed ({item_url}): "
            f"{type(exc).__name__}: {exc}"
        ) from exc

    body = _unwrap_formex_body(item_bytes, item_url, cons_celex)
    root = cellar._xml_root_local_tag(body)
    if root in _ACCEPTABLE_CONS_ROOTS:
        return body

    # MULTI-DOC manifestation shape (the real 02008R0692-20130319 pathology):
    # the notice-selected ``DOC_N`` item is the publication ENVELOPE (a tiny
    # ``<DOC>`` table-of-contents pointing at the real body member) while a
    # SIBLING ``DOC_M`` of the same manifestation carries the actual
    # ``CONS.ACT`` (observed both ways: envelope at DOC_2 with body at DOC_1,
    # and envelope at DOC_1 with the body higher up). Storing the envelope
    # would be a silent empty oracle (11/75 of the first acquisition run did
    # exactly that). Probe the siblings before failing; a manifestation with NO
    # act-rooted member is a typed acquisition gap.
    m = re.match(r"^(?P<stem>.+/)DOC_(?P<n>\d+)$", item_url)  # lawvm-regex: witness_only shape-parses an already-acquired Cellar item URL (source-plane locator census) to derive sibling DOC member URLs, not a post-parse semantic recognizer over statute text
    if m:
        selected_n = int(m.group("n"))
        misses = 0
        for n in range(1, _MAX_SIBLING_DOC_PROBES + 1):
            if n == selected_n:
                continue
            sibling_url = f"{m.group('stem')}DOC_{n}"
            try:
                sibling_bytes, _ = fetch_item(sibling_url, timeout_s)
            except (HTTPError, URLError, TimeoutError, OSError):
                # A missing sibling index is expected (the DOC_N series is
                # finite); two consecutive misses end the probe.
                misses += 1
                if misses >= 2:
                    break
                continue
            misses = 0
            sibling_body = _unwrap_formex_body(sibling_bytes, sibling_url, cons_celex)
            if cellar._xml_root_local_tag(sibling_body) in _ACCEPTABLE_CONS_ROOTS:
                return sibling_body
    raise ConsolidationAcquisitionFailure(
        f"consolidation {cons_celex} item {item_url} has root {root!r} and no "
        "sibling DOC member carries a consolidated ACT body (recorded "
        "acquisition gap, not an empty oracle)"
    )


#: XML roots that ARE a consolidated (or plain) act body the grafter can parse.
_ACCEPTABLE_CONS_ROOTS = ("CONS.ACT", "CONS.DOC", "ACT")

#: How many sibling ``DOC_N`` members to probe for the act body when the
#: notice-selected item is a publication envelope (multi-DOC manifestations).
_MAX_SIBLING_DOC_PROBES = 12


def _unwrap_formex_body(item_bytes: bytes, item_url: str, cons_celex: str) -> bytes:
    """Unwrap a fetched manifestation item to its primary Formex XML bytes."""
    from lawvm.eu import cellar

    if cellar.looks_like_zip(item_bytes):
        extracted = cellar.extract_primary_formex_from_zip(
            item_bytes, archive_hint=item_url
        )
        if extracted is None:
            raise ConsolidationAcquisitionFailure(
                f"consolidation {cons_celex} item is a ZIP with no parseable "
                f"Formex member ({item_url})"
            )
        return extracted.primary_xml
    return item_bytes


def _default_fetch_cons_notice(
    cons_celex: str, language: str, timeout_s: int
) -> tuple[bytes, dict]:
    """Fetch the DATED consolidated CELEX's tree notice (live Cellar)."""
    from lawvm.eu import cellar

    notice = cellar.NoticeRequest(
        celex=cons_celex,
        notice_format="xml",
        notice_type="tree",
        decode_language=language,
    )
    return cellar._request_notice(notice, timeout_s=timeout_s)


def _default_parse_fmx4_bytes(raw: bytes, celex: str) -> IRStatute:
    """Parse consolidated FMX4 bytes into an IRStatute via the grafter.

    The grafter parses from a path, so the bytes are written to a NamedTemporary
    file (consolidated bytes are NOT persisted to the farchive or git — design
    discipline: acquired data stays out of the tree).
    """
    import tempfile
    from pathlib import Path

    from lawvm.eu.grafter import parse_eu_regulation_ir

    with tempfile.NamedTemporaryFile(suffix=".xml", delete=True) as tf:
        tf.write(raw)
        tf.flush()
        return parse_eu_regulation_ir(Path(tf.name), celex=celex)

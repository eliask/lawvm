"""Sweden official-source acquisition helpers.

The official SFS doc page is used as a locator/provenance layer only.
The primary archived source artifact is the official PDF plus a derived
plain-text extraction produced by `pdftotext`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import date, timedelta
from enum import StrEnum
import html as _html
import json
import re
import time
from pathlib import Path
import subprocess
from typing import Any, Callable, Literal, Protocol, Optional, assert_never, cast
from urllib.parse import urlencode, urljoin

from lawvm.core.comparison_normalization import ComparisonNormalizationRule, normalize_comparison_text
from lawvm.core.diagnostic_records import diagnostic_detail
from lawvm.core.ir import IRNode, IRStatute, LegalOperation
from lawvm.core.ir_helpers import ir_statute_from_dict
from lawvm.core.semantic_types import FacetKind, IRNodeKind, StructuralAction
from lawvm.core.source_lane import SourceLaneSelectionEvidence, source_lane_attempt_from_mapping

JsonObject = dict[str, Any]
JsonObjectList = list[JsonObject]
from lawvm.core import tree_ops
from lawvm.core.adjudication_evidence import adjudication_finding_evidence_rows
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.sweden.grafter import SESourceRecord, parse_se_source_record, parse_se_statute
from lawvm.sweden.se_agreement_residuals import se_replay_agreement_residuals
from lawvm.sweden.se_coverage_universe import se_coverage_universe_entry, se_coverage_universe_root
from lawvm.sweden.se_overwrite_event_ledger import (
    SEOverwriteEvent,
    se_store_with_overwrite_event,
)
from lawvm.sweden.grafter import (
    apply_se_ops,
    build_se_official_base_statute,
    canonicalize_se_table_section_text,
    compile_se_official_act_ops,
    enrich_se_source_record_with_doc_page,
    extract_se_current_section_texts,
    materialize_se_statute_as_of,
    se_appendix_text_map,
    se_official_clause_surface_to_dict,
    se_official_elaboration_to_dict,
    se_official_effect_plan_to_dict,
    se_official_payload_surface_to_dict,
    se_heading_before_section_map,
    parse_se_official_act_text,
    parse_se_official_pdf_url,
    _build_se_official_clause_surface,
    _build_se_official_elaboration,
    _build_se_official_effects_plan,
    _build_se_official_payload_surface,
    _infer_amended_act_sfs_id_from_clause,
    _coerce_official_act,
    _lower_se_official_effects_plan,
    se_legal_operation_from_dict,
    se_section_text_map,
    se_statute_invariant_violation_records,
    se_legal_operation_to_dict,
    se_statute_invariant_violations,
    se_official_doc_url,
    se_official_act_text_to_dict,
    se_pdf_bytes_to_text,
)


_DEFAULT_CACHE = Path(__file__).parent.parent.parent.parent / "data" / "sweden.farchive"
_IMMUTABLE_CACHE_HOURS = float("inf")
_CURRENT_SURFACE_CACHE_HOURS = 24.0


class SeOpsStatus(StrEnum):
    """Closed set of per-act ops-compilation outcomes for a rebuild-chain step.

    A ``StrEnum`` so the value flows through the serialized ``ops_status`` dict
    key / persisted rows / test ``== "..."`` comparisons byte-for-byte while the
    value set is closed and rebuild-chain dispatch can be made exhaustive.
    """

    COMPILED = "compiled"
    """The prior act compiled into replayable operations."""

    MISSING_OFFICIAL_ACT = "missing_official_act"
    """The prior amendment's official act is unavailable (acquisition gap)."""

    UNSUPPORTED = "unsupported"
    """The act uses an effect shape not yet lowerable."""

    INVALID_OFFICIAL_ACT = "invalid_official_act"
    """The act could not be parsed into replayable operations."""


class _ArchiveLike(Protocol):
    def store(self, locator: str, data: bytes, *, storage_class: str | None = None) -> str: ...

    def get(self, locator: str) -> bytes | None: ...

    def has(self, locator: str, *, max_age_hours: float = ...) -> bool: ...


class _EnumerableArchiveLike(_ArchiveLike, Protocol):
    def locators(self, pattern: str = ...) -> list[str]: ...


def _se_archive_is_writable(archive: _ArchiveLike) -> bool:
    """Probe whether ``archive`` accepts ``store()``.

    Real :class:`farchive.Farchive` instances expose ``_readonly``; in-memory
    test doubles (``_FakeArchive``) and any other mapping-backed archive accept
    writes by default. Used by the analyze path so the read-only scan worker
    does not crash against the shared readonly Farchive despite the
    cache-refresh side effect the writable CLI paths rely on.
    """
    return not bool(getattr(archive, "_readonly", False))


@dataclass(frozen=True)
class SEOfficialArtifacts:
    sfs_id: str
    doc_url: str
    doc_locator: str
    pdf_url: str
    pdf_locator: str
    pdf_text_url: str
    pdf_cleaned_text_url: str


@dataclass(frozen=True)
class SESourceBundle:
    source_record: SESourceRecord
    current_statute: IRStatute
    official_artifacts: Optional[SEOfficialArtifacts] = None


_WS_RE = re.compile(r"\s+")
_PAGE_NUMBER_RE = re.compile(r"^\d+$")
_SFS_HEADER_RE = re.compile(r"^SFS\s+\d{4}:\d+[a-zA-Z]?$", re.IGNORECASE)
_PAGE_FURNITURE_RE = re.compile(r"^(Sida|Page)\s+\d+(\s+av\s+\d+)?$", re.IGNORECASE)
_DIGIT_GARBAGE_RE = re.compile(r"^[0-9:;.,()\-\s]{8,}$")
# Swedish SFS statute-citation reference line — the standard cross-reference
# shape ``\((\d{4}:\d+)\)\.?`` as a bare standalone line: optional leading
# opening parenthesis, the four-digit year, a colon, the running statute
# number, optional closing parenthesis, optional trailing period. Exempts
# legitimate short citation-reference lines from the ``_DIGIT_GARBAGE_RE``
# page-furniture filter; without this exemption, Swedish statutory text
# wraps that put the "(YEAR:N)." cross-reference on its own line get
# silently stripped from the cleaned PDF text, truncating the surrounding
# provision's body (real witness: SFS 2001:223 §2a replacement text ended
# "...i gymnasieförordningen\n(1992:394)." — that "(1992:394)." line was
# treated as digit garbage and dropped).
_SE_SFS_CITATION_REFERENCE_LINE_RE = re.compile(
    r"^\(?\d{4}:\d+\)?\.?\s*$",
    re.IGNORECASE,
)
_RK_UTFARDAD_RE = re.compile(r"Utfärdad:</span>\s*([0-9]{4}-[0-9]{2}-[0-9]{2})", re.IGNORECASE)
_SE_ATTRIBUTION_SFS_RE = re.compile(r"(?:Förordning|Lag)\s+\((\d{4}:\d+)\)\.?\s*$", re.IGNORECASE)
_SE_RENUMBER_PLACEHOLDER_SFS_RE = re.compile(
    r"Har betecknats\s+.+?\s+genom\s+(?:förordning|lag)\s+\((\d{4}:\d+)\)\.?",
    re.IGNORECASE,
)
_CLOUDFLARE_BLOCK_RE = re.compile(rb"(?:Attention Required|cloudflare|cf-browser-verification)", re.IGNORECASE)
_LEGACY_SFSPDF_PDF_RE = re.compile(r'href="(?P<href>/SFSdoc/\d{2}/\d+\.PDF)"', re.IGNORECASE)
_SE_DOC_URL_RE = re.compile(r"^https://svenskforfattningssamling\.se/doc/(?P<year>\d{4})(?P<number>\d+)\.html$")
_SE_FETCH_RETRY_ATTEMPTS = 5
_SE_FETCH_RETRY_INITIAL_DELAY_SECONDS = 0.5
_SE_FETCH_RETRY_MAX_DELAY_SECONDS = 8.0


def _se_archive_fetch(
    archive: _ArchiveLike,
    url: str,
    *,
    max_age_hours: float = _IMMUTABLE_CACHE_HOURS,
    storage_class: str | None = None,
) -> bytes | None:
    """Fetch URL with Farchive caching: return cached content if fresh, else HTTP-fetch and store."""
    import math

    if math.isinf(max_age_hours):
        cached = archive.get(url)
        if cached is not None:
            return cached
    else:
        if archive.has(url, max_age_hours=max_age_hours):
            cached = archive.get(url)
            if cached is not None:
                return cached
    fetch_method = getattr(archive, "fetch", None)

    def _attempt() -> bytes | None:
        if callable(fetch_method):
            try:
                data = fetch_method(
                    url,
                    max_age_hours=max_age_hours,
                    content_type=storage_class or "auto",
                )
            except TypeError:
                data = fetch_method(url)
            return data if data else None

        try:
            import urllib.request

            req = urllib.request.Request(
                url,
                headers={"User-Agent": "LawVM-SE/1.0 (+https://lawvm.org)"},
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
        except Exception:
            return None
        return data if data else None

    # Sweden acquisition is network-flaky enough that a small retry loop is
    # worth the cost, but the archive contract remains unchanged: success still
    # stores the fetched bytes under the same real URL locator.
    data = _retry_bytes_fetch(_attempt, label=url)
    if data:
        archive.store(url, data, storage_class=storage_class)
    return data


def _retry_bytes_fetch(
    fetch: Callable[[], bytes | None], *, label: str, attempts: int = _SE_FETCH_RETRY_ATTEMPTS
) -> bytes | None:
    last_delay = 0.0
    for attempt in range(1, attempts + 1):
        data = fetch()
        if data:
            return data
        if attempt >= attempts:
            break
        last_delay = min(
            _SE_FETCH_RETRY_INITIAL_DELAY_SECONDS * (2 ** (attempt - 1)),
            _SE_FETCH_RETRY_MAX_DELAY_SECONDS,
        )
        time.sleep(last_delay)
    return None


def se_rk_current_url(sfs_id: str) -> str:
    return f"https://rkrattsbaser.gov.se/sfst?bet={sfs_id}"


_SE_SFST_BODY_RE = re.compile(
    r'<div class="result-box-text body-text">(?P<body>.*?)</div>',
    re.DOTALL,
)
_SE_SFST_UTFARDAD_RE = re.compile(r"Utf\xe4rdad\s*:?\s*(\d{4}-\d{2}-\d{2})")
_SE_SFST_ANDRING_RE = re.compile(r"\xc4ndring inf\xf6rd\s*:?\s*(t\.o\.m\. SFS [\d:]+)")
_SE_SFST_IKRAFT_RE = re.compile(r"Ikraft\s*:?\s*(\d{4}-\d{2}-\d{2})")
_SE_SFST_UPPHAVD_RE = re.compile(r"Upph\xe4vd\s*:?\s*(\d{4}-\d{2}-\d{2})")
_SE_SFST_UPPHAVD_GENOM_RE = re.compile(r"upph\xe4vts genom\s*:?\s*(SFS [\d:]+)")
_SE_SFST_RUBRIK_RE = re.compile(
    r'<span class="bold">\s*(?P<rubrik>[^<:]*?\(\d{4}:\d+\)[^<]*?)\s*</span>'
)


def parse_se_sfst_html_to_rk_current(
    raw_html: bytes | str, sfs_id: str
) -> Optional[JsonObject]:
    """Build an RK-style ``rk.current.json`` document from an archived sfst page.

    The Regeringskansliet *rättsbaser* SFST fulltext page
    (``https://rkrattsbaser.gov.se/sfst?bet=...``) renders the same current
    consolidated ``forfattningstext`` the ``beta`` ElasticSearch endpoint
    returns as JSON. This deterministically lifts the rendered statute body and
    its header metadata into the same RK document shape that
    :func:`parse_se_statute` / :func:`archive_se_source_bundle` consume, so the
    archived HTML pages can seed the replay-vs-current agreement oracle without
    a fresh network fetch.

    Returns ``None`` when the page carries no statute body — most archived sfst
    pages are empty ``Totalt 0 träffar`` search responses (the bet= id is not in
    the SFST fulltext DB), which this filters out by the absence of a populated
    body container.
    """
    text = raw_html.decode("utf-8", "replace") if isinstance(raw_html, bytes) else raw_html
    body_match = _SE_SFST_BODY_RE.search(text)
    if body_match is None:
        return None
    body_html = re.sub(r"<br\s*/?>", "\n", body_match.group("body"), flags=re.IGNORECASE)
    body_html = re.sub(r"<[^>]+>", " ", body_html)
    forfattningstext = _html.unescape(body_html).strip()
    if not forfattningstext:
        return None

    flat = _html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)))

    fulltext: JsonObject = {"forfattningstext": forfattningstext}
    utf = _SE_SFST_UTFARDAD_RE.search(flat)
    if utf is not None:
        fulltext["utfardadDateTime"] = f"{utf.group(1)}T00:00:00"
    andring = _SE_SFST_ANDRING_RE.search(flat)
    if andring is not None:
        fulltext["andringInford"] = andring.group(1)
    upphavd_genom = _SE_SFST_UPPHAVD_GENOM_RE.search(flat)
    if upphavd_genom is not None:
        fulltext["upphavdGenom"] = upphavd_genom.group(1)

    document: JsonObject = {
        "beteckning": sfs_id,
        "publicerad": True,
        "fulltext": fulltext,
    }
    rubrik_match = _SE_SFST_RUBRIK_RE.search(text)
    if rubrik_match is not None:
        rubrik = _html.unescape(rubrik_match.group("rubrik")).strip()
        if rubrik:
            document["rubrik"] = rubrik
    ikraft = _SE_SFST_IKRAFT_RE.search(flat)
    if ikraft is not None:
        document["ikraftDateTime"] = f"{ikraft.group(1)}T00:00:00"
    upphavd = _SE_SFST_UPPHAVD_RE.search(flat)
    if upphavd is not None:
        document["upphavdDateTime"] = f"{upphavd.group(1)}T00:00:00"
    return document


def ingest_se_rk_current_from_sfst_archive(
    archive: _ArchiveLike, sfs_id: str
) -> bool:
    """Seed the current-text oracle for ``sfs_id`` from its archived sfst page.

    Returns ``True`` when the archived ``sfst?bet=`` page contained real
    consolidated fulltext and the RK-current bundle was stored; ``False`` when
    no sfst page is archived or the page is an empty search response.
    """
    raw = archive.get(se_rk_current_url(sfs_id))
    if raw is None:
        return False
    document = parse_se_sfst_html_to_rk_current(raw, sfs_id)
    if document is None:
        return False
    archive_se_source_bundle(document, archive)
    return True


_SE_OFFICIAL_OPS_LOCATOR_RE = re.compile(
    r"^se://sfs/(?P<sfs_id>\d{4}:\d+[a-zA-Z]?)/official\.ops\.json$"
)


def se_amending_sfs_ids_with_compiled_ops(archive: _EnumerableArchiveLike) -> list[str]:
    """Return the sorted SFS IDs of amending acts that carry compiled official ops."""
    ids: set[str] = set()
    for locator in archive.locators("se://sfs/%/official.ops.json"):
        match = _SE_OFFICIAL_OPS_LOCATOR_RE.fullmatch(str(locator))
        if match is not None:
            ids.add(match.group("sfs_id"))
    return sorted(ids, key=_se_sfs_sort_key)


def _se_sfs_sort_key(sfs_id: str) -> tuple[int, int, str]:
    match = re.fullmatch(r"(\d{4}):(\d+)([a-zA-Z]?)", sfs_id)
    if match is None:
        return (0, 0, sfs_id)
    return (int(match.group(1)), int(match.group(2)), match.group(3))


# The RK/sfst current-surface oracle carries an "Ändring införd: t.o.m. SFS
# YYYY:N" consolidation stamp recording the LAST amendment folded into the
# rendered text. The historical replay lane reconstructs the post-state of an
# EARLIER amendment, so when this stamp names an SFS strictly later than the
# amendment being replayed, an oracle disagreement is a dating artifact (correct
# replay vs a later consolidation), not a content error.
_SE_ANDRING_INFORD_SFS_RE = re.compile(r"SFS\s+(\d{4}:\d+[a-zA-Z]?)")


def _se_parse_andring_inford_sfs(stamp: str | None) -> str | None:
    """Extract the SFS id from an "Ändring införd: t.o.m. SFS YYYY:N" stamp.

    Returns ``None`` when the stamp is missing or carries no parseable SFS id,
    so callers can classify the row honestly as ``unknown`` rather than guess.
    """
    if not stamp:
        return None
    match = _SE_ANDRING_INFORD_SFS_RE.search(stamp)
    if match is None:
        return None
    return match.group(1)


def _se_oracle_version_relation(
    amending_sfs_id: str, oracle_stamp_sfs: str | None
) -> Literal["later", "same_or_earlier", "unknown"]:
    """Classify the current-surface oracle's consolidation stamp vs the replay.

    - ``later``: the oracle folds an SFS strictly later than ``amending_sfs_id``
      (a version-timing mismatch — correct replay against a later consolidation).
    - ``same_or_earlier``: the oracle is contemporaneous with or older than the
      replayed amendment, so a disagreement is a genuine surface drift.
    - ``unknown``: the stamp is missing or unparseable, or ``amending_sfs_id`` is
      not a well-formed SFS id, so the relation cannot be trusted.
    """
    if oracle_stamp_sfs is None:
        return "unknown"
    amending_key = _se_sfs_sort_key(amending_sfs_id)
    oracle_key = _se_sfs_sort_key(oracle_stamp_sfs)
    if amending_key == (0, 0, amending_sfs_id) or oracle_key == (0, 0, oracle_stamp_sfs):
        return "unknown"
    if oracle_key > amending_key:
        return "later"
    return "same_or_earlier"


def se_amending_act_base_sfs_id(archive: _ArchiveLike, amending_sfs_id: str) -> str:
    """Resolve the base statute an amending act targets, or ``""`` if unknown.

    Reads the archived ``official.act.json`` recorded base, falling back to the
    enacting-clause inference used by the replay lane.
    """
    official_act = load_se_official_act_from_archive(archive, amending_sfs_id)
    if official_act is None:
        return ""
    base = str(official_act.get("amended_act_sfs_id") or "")
    if base:
        return base
    return _infer_amended_act_sfs_id_from_clause(_coerce_official_act(official_act))


def enumerate_se_sfst_oracle_gain_bases(
    archive: _EnumerableArchiveLike,
) -> dict[str, Any]:
    """Enumerate the base statutes whose oracle can be seeded from a real sfst page.

    A *gain base* is a base statute that is (a) targeted by at least one compiled
    amending act, (b) backed by a REAL consolidated ``sfst?bet=`` page (not an
    empty ``Totalt 0 träffar`` search response), and (c) does not already carry an
    ``rk.current.json`` oracle blob. Running the sfst ingest over these bases is
    Sweden's corpus-level replay-agreement unlock.

    Returns a deterministic report: the sorted gain-base SFS IDs plus typed
    counts for every base that did NOT qualify (already-oracle, empty sfst page,
    no archived sfst page, undetermined base).
    """
    amending_ids = se_amending_sfs_ids_with_compiled_ops(archive)
    base_to_amending: dict[str, list[str]] = {}
    undetermined_base_amending: list[str] = []
    for amending_id in amending_ids:
        base = se_amending_act_base_sfs_id(archive, amending_id)
        if not base:
            undetermined_base_amending.append(amending_id)
            continue
        base_to_amending.setdefault(base, []).append(amending_id)

    gain_bases: list[str] = []
    already_oracle: list[str] = []
    empty_sfst_page: list[str] = []
    no_sfst_page: list[str] = []
    for base in sorted(base_to_amending, key=_se_sfs_sort_key):
        if archive.get(se_rk_current_json_locator(base)) is not None:
            already_oracle.append(base)
            continue
        raw = archive.get(se_rk_current_url(base))
        if raw is None:
            no_sfst_page.append(base)
            continue
        if parse_se_sfst_html_to_rk_current(raw, base) is None:
            empty_sfst_page.append(base)
            continue
        gain_bases.append(base)

    return {
        "amending_acts_with_ops": len(amending_ids),
        "distinct_bases_targeted": len(base_to_amending),
        "undetermined_base_amending_count": len(undetermined_base_amending),
        "gain_bases": gain_bases,
        "gain_base_count": len(gain_bases),
        "already_oracle_bases": already_oracle,
        "already_oracle_count": len(already_oracle),
        "empty_sfst_page_bases": empty_sfst_page,
        "empty_sfst_page_count": len(empty_sfst_page),
        "no_sfst_page_bases": no_sfst_page,
        "no_sfst_page_count": len(no_sfst_page),
    }


def scaled_ingest_se_sfst_oracles(
    archive: _EnumerableArchiveLike,
    *,
    gain_bases: list[str] | None = None,
    progress: Callable[[int, int, str], None] | None = None,
) -> dict[str, Any]:
    """Seed the current-text oracle for every sfst-backed gain base.

    Idempotent and safe to re-run: bases that already carry an ``rk.current.json``
    oracle are skipped (never overwritten — this protects the real RK-API blobs),
    and bases whose archived sfst page is an empty ``0 träffar`` search response
    are skipped without writing. Only bases whose archived sfst page contains real
    consolidated fulltext have a bundle written, via the validated
    :func:`ingest_se_rk_current_from_sfst_archive`.

    When ``gain_bases`` is omitted the gain bases are enumerated from the archive.
    Returns deterministic typed counts and any failures (with a typed reason).
    """
    if gain_bases is None:
        gain_bases = enumerate_se_sfst_oracle_gain_bases(archive)["gain_bases"]
    ordered = sorted(set(gain_bases), key=_se_sfs_sort_key)
    total = len(ordered)

    added: list[str] = []
    skipped_existing_oracle: list[str] = []
    skipped_empty_or_missing: list[str] = []
    failed: list[dict[str, str]] = []
    for index, base in enumerate(ordered, start=1):
        if progress is not None:
            progress(index, total, base)
        # Never overwrite an existing oracle (protects the real RK-API blobs).
        if archive.get(se_rk_current_json_locator(base)) is not None:
            skipped_existing_oracle.append(base)
            continue
        try:
            ingested = ingest_se_rk_current_from_sfst_archive(archive, base)
        except (ValueError, KeyError, TypeError) as exc:
            failed.append(
                {
                    "sfs_id": base,
                    "reason": "ingest_parse_error",
                    "exception_type": type(exc).__name__,
                    "detail": str(exc),
                }
            )
            continue
        if ingested:
            added.append(base)
        else:
            # Empty "0 träffar" page or no archived sfst page — never write.
            skipped_empty_or_missing.append(base)

    return {
        "considered": total,
        "added_count": len(added),
        "added_bases": added,
        "skipped_existing_oracle_count": len(skipped_existing_oracle),
        "skipped_empty_or_missing_count": len(skipped_empty_or_missing),
        "failed_count": len(failed),
        "failed": failed,
    }


def se_legacy_sfspdf_index_url() -> str:
    return "https://rkrattsdb.gov.se/sfspdf/"


def se_legacy_sfspdf_search_url() -> str:
    return "https://rkrattsdb.gov.se/sfspdf/sql_search_rsp.asp"


def open_se_archive(db_path: Path | None = None, *, readonly: bool = True):  # returns Farchive
    from farchive import Farchive
    from lawvm.corpus_store import validate_farchive_create_path

    path = db_path or _DEFAULT_CACHE
    if not readonly and not path.exists() and not path.suffix:
        validate_farchive_create_path(path)
    return Farchive(path, readonly=readonly)


def se_official_doc_locator(sfs_id: str) -> str:
    return f"se://sfs/{sfs_id}/official.doc.html"


def se_official_pdf_locator(sfs_id: str) -> str:
    return f"se://sfs/{sfs_id}/official.pdf"


def se_rk_current_json_locator(sfs_id: str) -> str:
    return f"se://sfs/{sfs_id}/rk.current.json"


def se_source_record_locator(sfs_id: str) -> str:
    return f"se://sfs/{sfs_id}/source_record.json"


def se_current_ir_locator(sfs_id: str) -> str:
    return f"se://sfs/{sfs_id}/current.ir.json"


def se_bundle_manifest_locator(sfs_id: str) -> str:
    return f"se://sfs/{sfs_id}/bundle.json"


def se_pdf_text_locator(sfs_id: str) -> str:
    """Canonical archive locator for text extracted from the official SFS PDF."""
    return f"se://sfs/{sfs_id}/official.pdf.txt"


def se_pdf_cleanup_locator(sfs_id: str) -> str:
    """Reserved locator for future deterministic cleanup over extracted PDF text."""
    return f"se://sfs/{sfs_id}/official.cleaned.txt"


def se_official_act_locator(sfs_id: str) -> str:
    """Canonical archive locator for structured text parsed from the official SFS PDF."""
    return f"se://sfs/{sfs_id}/official.act.json"


def se_official_base_ir_locator(sfs_id: str) -> str:
    """Canonical archive locator for a non-amending official-act IR seed."""
    return f"se://sfs/{sfs_id}/official.base.ir.json"


def se_official_ops_locator(sfs_id: str) -> str:
    """Canonical archive locator for compiled first-pass ops from the official act."""
    return f"se://sfs/{sfs_id}/official.ops.json"


def se_official_ops_adjudications_locator(sfs_id: str) -> str:
    """Canonical archive locator for official-op compile adjudications."""
    return f"se://sfs/{sfs_id}/official.ops.adjudications.json"


def se_official_clause_surface_locator(sfs_id: str) -> str:
    """Canonical archive locator for the Sweden official-act clause surface."""
    return f"se://sfs/{sfs_id}/official.clause.json"


def se_official_payload_surface_locator(sfs_id: str) -> str:
    """Canonical archive locator for the Sweden official-act payload surface."""
    return f"se://sfs/{sfs_id}/official.payload.json"


def se_official_elaboration_locator(sfs_id: str) -> str:
    """Canonical archive locator for the Sweden official-act elaboration waist."""
    return f"se://sfs/{sfs_id}/official.elaboration.json"


def se_official_effects_plan_locator(sfs_id: str) -> str:
    """Canonical archive locator for the Sweden canonical-effects plan waist."""
    return f"se://sfs/{sfs_id}/official.effects.plan.json"


def se_backfill_official_checkpoint_locator() -> str:
    """Canonical archive locator for Sweden official backfill run-state."""
    return "se://sweden/backfill-official/checkpoint.json"


def se_backfill_official_status_locator() -> str:
    """Canonical archive locator for Sweden official backfill live status."""
    return "se://sweden/backfill-official/status.json"


def se_backfill_official_history_locator() -> str:
    """Canonical archive locator for Sweden official backfill run history."""
    return "se://sweden/backfill-official/history.json"


def se_backfill_official_completeness_locator() -> str:
    """Canonical archive locator for Sweden official backfill completeness."""
    return "se://sweden/backfill-official/completeness.json"


def se_backfill_official_gap_report_locator() -> str:
    """Canonical archive locator for Sweden official backfill year/range gaps."""
    return "se://sweden/backfill-official/gap-report.json"


def se_backfill_official_chunk_plan_locator() -> str:
    """Canonical archive locator for Sweden official backfill chunk planning."""
    return "se://sweden/backfill-official/chunk-plan.json"


def se_sfs_id_from_doc_url(doc_url: str) -> str | None:
    match = _SE_DOC_URL_RE.fullmatch(doc_url.strip())
    if not match:
        return None
    return f"{match.group('year')}:{int(match.group('number'))}"


def clean_se_pdf_text(pdf_text: str) -> str:
    """Apply conservative deterministic cleanup to `pdftotext` output.

    The goal is not perfect reconstruction. It is to remove obvious page
    furniture while preserving legal wording and paragraph boundaries.
    """
    normalized = pdf_text.replace("\r\n", "\n").replace("\r", "\n").replace("\f", "\n")
    out_lines: list[str] = []
    previous_blank = True
    for raw_line in normalized.split("\n"):
        line = raw_line.strip()
        if not line:
            if not previous_blank:
                out_lines.append("")
            previous_blank = True
            continue
        if _PAGE_NUMBER_RE.fullmatch(line):
            continue
        if _SFS_HEADER_RE.fullmatch(line):
            continue
        if _PAGE_FURNITURE_RE.fullmatch(line):
            continue
        if _DIGIT_GARBAGE_RE.fullmatch(line):
            # Exempt short citation-reference lines: a Swedish SFS statute
            # citation wrapped in parentheses ("(1992:394).") or with a
            # trailing period ("1985:1100.") will look like pure digit
            # garbage to the ``_DIGIT_GARBAGE_RE`` shape (matched because
            # it is short and only digits/punctuation), but the parenthesis
            # or trailing-period citation shape is genuinely part of the
            # law text (the "(YEAR:N)" form is the standard SFS statute
            # cross-reference) — NOT page furniture. Real witness: SFS
            # 2001:223 §2a replacement statement ends "...institut finnas i
            # gymnasieförordningen\n(1992:394)." — that "(1992:394)." line
            # was silently stripped, truncating the §2a provision text and
            # forcing the replay-vs-oracle lookup into a row that compared
            # against the wrong (shorter) cached-act text vs. the
            # full-body current consolidation. Exempt here so the citation
            # reference survives the cleanup and stays in the section body.
            if not _SE_SFS_CITATION_REFERENCE_LINE_RE.fullmatch(line):
                continue
        line = _WS_RE.sub(" ", line)
        out_lines.append(line)
        previous_blank = False
    while out_lines and not out_lines[-1]:
        out_lines.pop()
    cleaned = "\n".join(out_lines)

    # Superscript-like footnote digits sometimes get glued onto 4-digit years
    # in headings/titles, e.g. "år 20311" when footnote "1 ..." appears later.
    footnote_ids = {m.group(1) for m in re.finditer(r"(?m)^([1-9])\s", cleaned)}
    for footnote_id in footnote_ids:
        cleaned = re.sub(rf"(\b\d{{4}}){footnote_id}\b", r"\1", cleaned)
    return cleaned


def _json_bytes(data: object) -> bytes:
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")


_SE_COMPARE_NORMALIZATION_RULES = (
    ComparisonNormalizationRule(
        name="se_compare_dash_glyph_equivalence",
        rule_class="presentation_cleanup",
        kind="translation",
        description="Project Swedish comparison text dash variants to ASCII hyphen.",
        translation=str.maketrans({"–": "-", "—": "-", "\u2212": "-"}),
    ),
    ComparisonNormalizationRule(
        name="se_compare_editorial_attribution_suffix",
        rule_class="presentation_cleanup",
        kind="regex",
        description="Ignore trailing Förordning attribution suffixes in comparison text.",
        pattern=re.compile(r"\s*Förordning\s+\(\d{4}:\d+\)\.\s*$"),
    ),
    ComparisonNormalizationRule(
        name="se_compare_leading_section_number",
        rule_class="presentation_cleanup",
        kind="regex",
        description="Ignore publisher-leading section numbers before capitalized text.",
        pattern=re.compile(r"^\d+\s+(?=[A-ZÅÄÖ])"),
    ),
    ComparisonNormalizationRule(
        name="se_compare_inline_list_numbering",
        rule_class="presentation_cleanup",
        kind="regex",
        description=(
            "Ignore inline list numbering inserted after whitespace before lower- or "
            "uppercase body text. Replaying turns a provision body into IR ITEM "
            "children whose rendered text omits the leading '<N>. ' enumerator; the "
            "cached official-act raw provision text preserves it as plain "
            "'<N>. <Body>'. Without ignoring the enumerator the replay-vs-cached-"
            "oracle fallback mismatches any section whose body enumerates items "
            "(real witness: SFS 1999:1134 §2 of 2001:1004 — the §2 enumerated-items "
            "body replayed as 'Väg En sådan väg...' while cached text kept "
            "'Väg 1. En sådan väg...')."
        ),
        pattern=re.compile(r"(?<=\s)\d+\.\s+(?=[a-zåäöA-ZÅÄÖ])"),
    ),
    # Mirror of the Förordning trailing-attribution rule for the Lag counterpart.
    # A consolidated RK surface tags each amended provision with the amending
    # act's own short citation, e.g. a paragraph ending in "... Lag (2018:221)."
    # The historical replay payload renders the same provision without that
    # editorial provenance tag. Anchored to the END of the comparison text so it
    # can only fold a trailing tag, never alter substantive body text.
    ComparisonNormalizationRule(
        name="se_compare_editorial_lag_attribution_suffix",
        rule_class="presentation_cleanup",
        kind="regex",
        description="Ignore trailing Lag attribution suffixes in comparison text.",
        pattern=re.compile(r"\s*Lag\s+\(\d{4}:\d+\)\.\s*$"),
    ),
    # Trailing preparatory-work provenance citations ("Prop." / "Jfr prop."),
    # e.g. "... skall tillämpas. Prop. 2001/02:1." or "Jfr prop. 1999/2000:23.".
    # These are editorial cross-references to the bill that introduced the text,
    # not part of the operative provision. Anchored to the END of the text and
    # requiring the prop. citation shape, so substantive references to a
    # proposition inside body text are left untouched.
    ComparisonNormalizationRule(
        name="se_compare_editorial_prop_provenance_suffix",
        rule_class="presentation_cleanup",
        kind="regex",
        description="Ignore trailing 'Prop.'/'Jfr prop.' preparatory-work citations in comparison text.",
        pattern=re.compile(r"\s*(?:Jfr\s+)?[Pp]rop\.\s*\d{4}(?:/\d{2,4})?:\d+\.?\s*$"),
    ),
    # List-enumerator presentation: a consolidated surface may render an
    # alphabetic list item label with a different case or with/without a leading
    # space, e.g. "a) ..." vs "A) ..." vs " a) ...". Fold the label to a single
    # canonical lower-case form. Only the one- or two-letter enumerator token
    # before ')' is touched (after whitespace or at string start), never the list
    # item's body text, so genuinely different list content still differs.
    ComparisonNormalizationRule(
        name="se_compare_list_enumerator_case",
        rule_class="presentation_cleanup",
        kind="regex",
        description="Normalize alphabetic list-enumerator label case/spacing (a)/A)) for comparison.",
        pattern=re.compile(r"(?:(?<=\s)|^)([A-Za-z]{1,2})\)\s+"),
        replacement=lambda m: f"{m.group(1).lower()}) ",
    ),
)


def _normalize_compare_text(text: str) -> str:
    normalized = normalize_comparison_text(text.strip(), _SE_COMPARE_NORMALIZATION_RULES).text
    return " ".join(normalized.split())


# Editorial repeal-stub convention Svensk författningssamling carries vs the
# section's true (absent) post-state after a repeal: the current-text oracle
# keeps a one-line stub "Har upphävts genom <förordning|lag> (YEAR:N)." in
# place of the repealed section text, while the replay-fold (correctly)
# produces an empty/absent section because the section was structurally
# repealed. Treating the two as a content mismatch inflates the
# genuine-mismatch rate without telling LawVM anything about replay quality —
# the auditor just sees a stub-vs-empty diff for a section the official
# consolidation decided to keep as a tombstone.
_SE_REPEAL_STUB_RE = re.compile(
    r"^\s*Har\s+upphävts\s+genom\s+(?:förordning|lag)\s+\(\d{4}:\d+\)\.?\s*$",
    re.IGNORECASE,
)


def _is_oracle_repeal_stub(text: str) -> bool:
    """True when ``text`` is an editorial repeal-stub (SFS tombstone convention).

    The replay-fold produces an empty post-section because the section is
    structurally repealed; the official oracle keeps the title set with a
    one-line ``Har upphävts genom <förordning|lag> (YEAR:N).`` tombstone in
    its place. Classify the diff as an editorial-stub match rather than a
    genuine content disagreement.
    """
    return bool(_SE_REPEAL_STUB_RE.match(text or ""))


def _classify_replay_row(replay_text: str, post_text: str) -> str:
    replay_editorial = " ".join(re.sub(r"\s*Förordning\s+\(\d{4}:\d+\)\.\s*$", "", replay_text.strip()).split())
    post_editorial = " ".join(re.sub(r"\s*Förordning\s+\(\d{4}:\d+\)\.\s*$", "", post_text.strip()).split())
    if replay_editorial == post_editorial and replay_text.strip() != post_text.strip():
        return "editorial_attribution_only"
    replay_norm = _normalize_compare_text(replay_text)
    post_norm = _normalize_compare_text(post_text)
    if replay_norm == post_norm and replay_text.strip() != post_text.strip():
        return "inline_numbering_only"
    table_markers = ("Uppgift lämnas av", "Uppgift lämnas om")
    if any(marker in replay_text for marker in table_markers) and any(marker in post_text for marker in table_markers):
        return "table_layout_mismatch"
    return "content_mismatch"


def _normalize_appendix_compare_text(text: str) -> str:
    normalized = _normalize_compare_text(text)
    normalized = re.sub(r"(?:(?<=\s)|^)\d+\.\s+(?=[A-ZÅÄÖ])", "", normalized)
    return " ".join(normalized.split())


def _normalize_jsonable(value: object) -> object:
    if isinstance(value, dict):
        return {str(k): _normalize_jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize_jsonable(v) for v in value]
    if isinstance(value, tuple):
        return [_normalize_jsonable(v) for v in value]
    if hasattr(value, "value"):
        enum_value = getattr(value, "value", None)
        if isinstance(enum_value, str):
            return enum_value
    return value


def se_source_record_to_dict(source_record: SESourceRecord) -> dict[str, Any]:
    return cast(dict[str, Any], _normalize_jsonable(asdict(source_record)))


def se_source_bundle_to_dict(bundle: SESourceBundle) -> dict[str, Any]:
    return {
        "source_record": se_source_record_to_dict(bundle.source_record),
        "current_statute": bundle.current_statute.to_jsonable_dict(),
        "official_artifacts": _normalize_jsonable(asdict(bundle.official_artifacts))
        if bundle.official_artifacts is not None
        else None,
    }


def _curl_json_post(url: str, *, headers: list[str], payload: JsonObject) -> Optional[bytes]:
    result = subprocess.run(
        [
            "curl",
            "-s",
            "--max-time",
            "30",
            url,
            *headers,
            "--data",
            json.dumps(payload, ensure_ascii=False),
        ],
        capture_output=True,
    )
    if result.returncode != 0 or not result.stdout:
        return None
    return result.stdout


def _curl_form_post(url: str, *, payload: dict[str, str]) -> Optional[bytes]:
    result = subprocess.run(
        [
            "curl",
            "-L",
            "-s",
            "--max-time",
            "30",
            "-H",
            "content-type: application/x-www-form-urlencoded",
            "--data",
            urlencode(payload),
            url,
        ],
        capture_output=True,
    )
    if result.returncode != 0 or not result.stdout:
        return None
    return result.stdout


def _curl_probe_bytes(url: str, *, byte_range: str | None = None) -> tuple[int | None, bytes]:
    cmd = ["curl", "-L", "-s", "--max-time", "20"]
    if byte_range:
        cmd.extend(["-r", byte_range])
    cmd.extend(["-o", "-", "-w", "\n%{http_code}", url])
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        return (None, b"")
    stdout = result.stdout
    if b"\n" not in stdout:
        return (None, stdout)
    body, _, code_bytes = stdout.rpartition(b"\n")
    try:
        status_code = int(code_bytes.decode("ascii", errors="replace").strip())
    except ValueError:
        status_code = None
    return (status_code, body)


def parse_se_rk_issue_date(rk_html: bytes | str) -> Optional[str]:
    if isinstance(rk_html, bytes):
        rk_html = rk_html.decode("utf-8", errors="replace")
    match = _RK_UTFARDAD_RE.search(rk_html)
    return match.group(1) if match else None


def guess_se_official_pdf_url(sfs_id: str, issue_date: str) -> str:
    year, number = sfs_id.split(":", 1)
    month = issue_date[:7]
    return f"https://svenskforfattningssamling.se/sites/default/files/sfs/{month}/SFS{year}-{int(number)}.pdf"


def guess_se_official_pdf_url_candidates(sfs_id: str) -> list[str]:
    match = re.fullmatch(r"(?P<year>\d{4}):(?P<number>\d+)", sfs_id.strip())
    if not match:
        return []
    year = match.group("year")
    number = int(match.group("number"))
    return [
        f"https://svenskforfattningssamling.se/sites/default/files/sfs/{year}-{month:02d}/SFS{year}-{number}.pdf"
        for month in range(1, 13)
    ]


def guess_se_legacy_pdf_url(sfs_id: str) -> str:
    match = re.fullmatch(r"(?P<year>\d{4}):(?P<number>\d+)", sfs_id.strip())
    if not match:
        raise ValueError(f"invalid Sweden SFS ID: {sfs_id!r}")
    year = match.group("year")
    year_short = year[2:]
    number = int(match.group("number"))
    return f"https://rkrattsdb.gov.se/SFSdoc/{year_short}/{year_short}{number:04d}.PDF"


def parse_se_legacy_pdf_url(search_html: bytes | str) -> str | None:
    if isinstance(search_html, bytes):
        text = search_html.decode("latin-1", errors="replace")
    else:
        text = search_html
    match = _LEGACY_SFSPDF_PDF_RE.search(text)
    if not match:
        return None
    return urljoin(se_legacy_sfspdf_index_url(), match.group("href"))


def search_se_legacy_pdf_url(sfs_id: str) -> str | None:
    html = _curl_form_post(
        se_legacy_sfspdf_search_url(),
        payload={
            "SFS_nr": sfs_id,
            "title": "",
            "author": "",
            "departement": "",
            "ACTION": "  Sök  ",
        },
    )
    if not html:
        return None
    return parse_se_legacy_pdf_url(html)


def _looks_like_pdf_bytes(data: bytes | None) -> bool:
    if not data:
        return False
    return data[:1024].lstrip().startswith(b"%PDF-")


def has_valid_se_official_pdf(archive: _ArchiveLike, sfs_id: str) -> bool:
    return _looks_like_pdf_bytes(archive.get(se_official_pdf_locator(sfs_id)))


def ingest_se_scraped_doc_html_map(
    payload: bytes | str | dict[str, str],
    archive: _ArchiveLike,
) -> dict[str, Any]:
    if isinstance(payload, dict):
        data = payload
    else:
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")
        decoded = json.loads(payload)
        if not isinstance(decoded, dict):
            raise ValueError("expected scraped Sweden doc payload to decode to a JSON object")
        data = decoded

    imported = 0
    skipped = 0
    resolved_pdf_links = 0
    sfs_ids: list[str] = []
    skipped_entries: list[dict[str, Any]] = []
    for entry_index, (doc_url, html) in enumerate(data.items()):
        if not isinstance(doc_url, str) or not isinstance(html, str):
            skipped += 1
            skipped_entries.append(
                diagnostic_detail(
                    rule_id="se_scraped_doc_entry_invalid_shape",
                    phase="acquisition",
                    family="source_pathology",
                    blocking=True,
                    reason="scraped Sweden document map entry did not have string URL and HTML values",
                    entry_index=entry_index,
                    doc_url_type=type(doc_url).__name__,
                    html_type=type(html).__name__,
                )
            )
            continue
        sfs_id = se_sfs_id_from_doc_url(doc_url)
        if not sfs_id:
            skipped += 1
            skipped_entries.append(
                diagnostic_detail(
                    rule_id="se_scraped_doc_entry_unrecognized_url",
                    phase="acquisition",
                    family="source_pathology",
                    blocking=True,
                    reason="scraped Sweden document URL did not resolve to an SFS id",
                    entry_index=entry_index,
                    doc_url=doc_url,
                )
            )
            continue
        html_bytes = html.encode("utf-8")
        archive.store(doc_url, html_bytes, storage_class="html")
        archive.store(se_official_doc_locator(sfs_id), html_bytes, storage_class="html")
        imported += 1
        sfs_ids.append(sfs_id)
        if parse_se_official_pdf_url(html_bytes, doc_url):
            resolved_pdf_links += 1

    return {
        "entry_count": len(data),
        "imported_count": imported,
        "skipped_count": skipped,
        "skipped_entries": skipped_entries,
        "resolved_pdf_link_count": resolved_pdf_links,
        "sfs_ids": sfs_ids,
    }


def probe_se_public_source_status(sfs_id: str) -> dict[str, Any]:
    doc_url = se_official_doc_url(sfs_id)
    doc_status_code, doc_body = _curl_probe_bytes(doc_url)
    doc_status = "unreachable"
    parsed_pdf_url = ""
    if doc_status_code == 200:
        parsed_pdf_url = parse_se_official_pdf_url(doc_body, doc_url) or ""
        doc_status = "pdf_link" if parsed_pdf_url else "html_no_pdf_link"
    elif doc_status_code == 403 and _CLOUDFLARE_BLOCK_RE.search(doc_body):
        doc_status = "cloudflare_blocked"
    elif doc_status_code == 404:
        doc_status = "not_found"
    elif doc_status_code is not None:
        doc_status = f"http_{doc_status_code}"

    pdf_status = "unreachable"
    resolved_pdf_url = ""
    candidate_urls = [parsed_pdf_url] if parsed_pdf_url else []
    candidate_urls.extend(
        candidate for candidate in guess_se_official_pdf_url_candidates(sfs_id) if candidate not in candidate_urls
    )
    for candidate_url in candidate_urls:
        status_code, body = _curl_probe_bytes(candidate_url, byte_range="0-1023")
        if status_code == 200 and _looks_like_pdf_bytes(body):
            resolved_pdf_url = candidate_url
            pdf_status = "valid_pdf"
            break
        if status_code == 404:
            pdf_status = "not_found"
            continue
        if status_code == 403 and _CLOUDFLARE_BLOCK_RE.search(body):
            pdf_status = "cloudflare_blocked"
            continue
        if status_code is not None:
            pdf_status = f"http_{status_code}"
    if not candidate_urls and pdf_status == "unreachable":
        pdf_status = "no_candidate"
    return {
        "doc_url": doc_url,
        "doc_status": doc_status,
        "pdf_status": pdf_status,
        "resolved_pdf_url": resolved_pdf_url,
        "public_source_viable": pdf_status == "valid_pdf",
    }


def attach_official_artifacts_to_bundle(
    bundle: SESourceBundle,
    official_artifacts: Optional[SEOfficialArtifacts],
) -> SESourceBundle:
    if official_artifacts is None:
        return bundle
    source_record = bundle.source_record
    if source_record.sfs_id == official_artifacts.sfs_id:
        source_record = replace(
            source_record,
            source_urls=replace(
                source_record.source_urls,
                official_sfs_doc_url=official_artifacts.doc_url,
                official_sfs_pdf_url=official_artifacts.pdf_url,
            ),
        )
    return SESourceBundle(
        source_record=source_record,
        current_statute=bundle.current_statute,
        official_artifacts=official_artifacts,
    )


def fetch_se_official_artifacts(
    sfs_id: str,
    archive: _ArchiveLike,
    *,
    max_age_hours: float = _IMMUTABLE_CACHE_HOURS,
    force_reextract: bool = False,
    pdf_url_override: str | None = None,
    diagnostics_out: list[dict[str, Any]] | None = None,
    overwrite_events_out: list[SEOverwriteEvent] | None = None,
) -> Optional[SEOfficialArtifacts]:
    """Fetch Sweden official doc page + PDF and archive extracted text.

    Cache policy:
    - official original-promulgation sources are treated as immutable by default
    - TTLs should only be used for list/current/consolidated surfaces

    Storage policy:
    - doc page HTML is cached by real HTTP URL and mirrored to `se://.../official.doc.html`
    - official PDF is cached by real HTTP URL and mirrored to `se://.../official.pdf`
    - extracted raw text is archived at `se://.../official.pdf.txt`
    - deterministic cleaned text is archived at `se://.../official.cleaned.txt`
    - structured parsed act text is archived at `se://.../official.act.json`
    """
    doc_url = se_official_doc_url(sfs_id)
    pdf_source_attempts: list[dict[str, str]] = []
    selected_pdf_lane = ""
    doc_html = _se_archive_fetch(archive, doc_url, max_age_hours=max_age_hours, storage_class="html")
    parsed_doc_pdf_url = parse_se_official_pdf_url(doc_html, doc_url) if doc_html else None
    doc_status = "pdf_link_found" if parsed_doc_pdf_url else ("no_pdf_link" if doc_html else "missing")
    if doc_html and parsed_doc_pdf_url:
        archive.store(se_official_doc_locator(sfs_id), doc_html, storage_class="html")

    pdf_url = parsed_doc_pdf_url
    if pdf_url:
        selected_pdf_lane = "official_doc_pdf_link"
    if not pdf_url and pdf_url_override:
        pdf_url = pdf_url_override
        selected_pdf_lane = "explicit_pdf_url_override"
    if not pdf_url:
        rk_html = _se_archive_fetch(
            archive,
            se_rk_current_url(sfs_id),
            max_age_hours=_CURRENT_SURFACE_CACHE_HOURS,
            storage_class="html",
        )
        if rk_html:
            issue_date = parse_se_rk_issue_date(rk_html)
            if issue_date:
                pdf_url = guess_se_official_pdf_url(sfs_id, issue_date)
                selected_pdf_lane = "rk_issue_date_guess"
    pdf_bytes = (
        _se_archive_fetch(archive, pdf_url, max_age_hours=max_age_hours, storage_class="pdf") if pdf_url else None
    )
    if pdf_url:
        pdf_source_attempts.append(
            {
                "lane": selected_pdf_lane or "unknown",
                "url": pdf_url,
                "lane_attempt_status": "valid_pdf" if _looks_like_pdf_bytes(pdf_bytes) else "missing_or_non_pdf",
            }
        )
    if pdf_bytes is not None and not _looks_like_pdf_bytes(pdf_bytes):
        pdf_bytes = None
    if not pdf_bytes:
        legacy_direct_url = guess_se_legacy_pdf_url(sfs_id)
        legacy_direct_bytes = _se_archive_fetch(
            archive, legacy_direct_url, max_age_hours=max_age_hours, storage_class="pdf"
        )
        pdf_source_attempts.append(
            {
                "lane": "legacy_direct_guess",
                "url": legacy_direct_url,
                "lane_attempt_status": "valid_pdf" if _looks_like_pdf_bytes(legacy_direct_bytes) else "missing_or_non_pdf",
            }
        )
        if _looks_like_pdf_bytes(legacy_direct_bytes):
            doc_url = se_legacy_sfspdf_index_url()
            pdf_url = legacy_direct_url
            pdf_bytes = legacy_direct_bytes
            selected_pdf_lane = "legacy_direct_guess"
    if not pdf_bytes:
        legacy_search_pdf_url = search_se_legacy_pdf_url(sfs_id)
        if not legacy_search_pdf_url:
            pdf_source_attempts.append(
                {
                    "lane": "legacy_search_result",
                    "url": se_legacy_sfspdf_search_url(),
                    "lane_attempt_status": "no_result",
                }
            )
        else:
            legacy_search_bytes = _se_archive_fetch(
                archive, legacy_search_pdf_url, max_age_hours=max_age_hours, storage_class="pdf"
            )
            pdf_source_attempts.append(
                {
                    "lane": "legacy_search_result",
                    "url": legacy_search_pdf_url,
                    "lane_attempt_status": "valid_pdf" if _looks_like_pdf_bytes(legacy_search_bytes) else "missing_or_non_pdf",
                }
            )
            if _looks_like_pdf_bytes(legacy_search_bytes):
                doc_url = se_legacy_sfspdf_search_url()
                pdf_url = legacy_search_pdf_url
                pdf_bytes = legacy_search_bytes
                selected_pdf_lane = "legacy_search_result"
    if not pdf_bytes:
        for candidate_url in guess_se_official_pdf_url_candidates(sfs_id):
            if candidate_url == pdf_url:
                continue
            candidate_bytes = _se_archive_fetch(
                archive, candidate_url, max_age_hours=max_age_hours, storage_class="pdf"
            )
            pdf_source_attempts.append(
                {
                    "lane": "official_month_probe",
                    "url": candidate_url,
                    "lane_attempt_status": "valid_pdf" if _looks_like_pdf_bytes(candidate_bytes) else "missing_or_non_pdf",
                }
            )
            if _looks_like_pdf_bytes(candidate_bytes):
                pdf_url = candidate_url
                pdf_bytes = candidate_bytes
                selected_pdf_lane = "official_month_probe"
                break
    if not pdf_url or not pdf_bytes:
        _record_se_official_artifacts_diagnostic(
            diagnostics_out,
            rule_id="se_official_artifacts_unavailable",
            sfs_id=sfs_id,
            locator=se_official_pdf_locator(sfs_id),
            reason="Sweden official SFS PDF artifact could not be located or fetched",
            doc_url=doc_url,
            pdf_url=pdf_url,
            pdf_source_attempts=tuple(pdf_source_attempts),
        )
        return None
    if selected_pdf_lane not in {"", "official_doc_pdf_link", "explicit_pdf_url_override"}:
        _record_se_official_artifacts_diagnostic(
            diagnostics_out,
            rule_id="se_official_pdf_source_lane_fallback",
            sfs_id=sfs_id,
            locator=se_official_pdf_locator(sfs_id),
            reason="Sweden official SFS PDF was recovered through a fallback source lane",
            doc_url=doc_url,
            pdf_url=pdf_url,
            blocking=False,
            doc_status=doc_status,
            selected_pdf_lane=selected_pdf_lane,
            pdf_source_attempts=tuple(pdf_source_attempts),
        )
    archive.store(se_official_pdf_locator(sfs_id), pdf_bytes, storage_class="pdf")

    text_url = se_pdf_text_locator(sfs_id)
    cleaned_text_url = se_pdf_cleanup_locator(sfs_id)
    existing_text = archive.get(text_url)
    existing_cleaned = archive.get(cleaned_text_url)
    act_json_url = se_official_act_locator(sfs_id)
    if existing_text is not None and existing_cleaned is None and not force_reextract:
        archive.store(
            cleaned_text_url,
            clean_se_pdf_text(existing_text.decode("utf-8", errors="replace")).encode("utf-8"),
            storage_class="text",
        )
    elif existing_text is None or force_reextract or existing_cleaned is None:
        pdf_text = se_pdf_bytes_to_text(pdf_bytes)
        if pdf_text:
            # KNOW-01 monotonicity wrap: the force_reextract path overwrites
            # prior text/cleaned bytes (the cached source-footing mutates).
            # Pass through se_store_with_overwrite_event so the prior bytes'
            # sha256 lands in the caller-passed overwrite_events_out ledger
            # (when provided); the wrapper transparently stores + emits.
            source_trigger = "force_reextract" if force_reextract else "manual_reingest"
            se_store_with_overwrite_event(
                archive,
                text_url,
                pdf_text.encode("utf-8"),
                sfs_id=sfs_id,
                source_trigger=source_trigger,
                events_out=overwrite_events_out,
                storage_class="text",
            )
            se_store_with_overwrite_event(
                archive,
                cleaned_text_url,
                clean_se_pdf_text(pdf_text).encode("utf-8"),
                sfs_id=sfs_id,
                source_trigger=source_trigger,
                events_out=overwrite_events_out,
                storage_class="text",
            )
        else:
            _record_se_official_artifacts_diagnostic(
                diagnostics_out,
                rule_id="se_official_pdf_text_extraction_failed",
                sfs_id=sfs_id,
                locator=text_url,
                reason="Sweden official SFS PDF was fetched but text extraction produced no payload",
                doc_url=doc_url,
                pdf_url=pdf_url,
                phase="extraction",
            )

    cleaned_bytes = archive.get(cleaned_text_url)
    if cleaned_bytes is not None:
        act_text = parse_se_official_act_text(
            cleaned_bytes.decode("utf-8", errors="replace"),
            sfs_id=sfs_id,
        )
        # KNOW-01 wrap (downstream-derived): act_json is derived from
        # cleaned_bytes (which force_reextract just (re-)stored), so a re-extract
        # of the cleaned text also re-store()s the cached parsed act text — and
        # if a prior act_json occupies the locator it is overwritten in place.
        # The wrapper mirrors the force_reextract branch above: when force_reextract
        # did NOT fire (e.g. existing_cleaned was None triggering re-derivation
        # without force_reextract), still scribe the overwrite with the
        # "manual_reingest" trigger so the audit is complete — the prior content
        # is recorded either way.
        se_store_with_overwrite_event(
            archive,
            act_json_url,
            _json_bytes(se_official_act_text_to_dict(act_text)),
            sfs_id=sfs_id,
            source_trigger="force_reextract" if force_reextract else "manual_reingest",
            events_out=overwrite_events_out,
            storage_class="json",
        )
        if not act_text.is_amending_act:
            try:
                base_statute = build_se_official_base_statute(se_official_act_text_to_dict(act_text), statute_id=sfs_id)
            except ValueError as exc:
                _record_se_official_artifacts_diagnostic(
                    diagnostics_out,
                    rule_id="se_official_base_ir_build_failed",
                    sfs_id=sfs_id,
                    locator=se_official_base_ir_locator(sfs_id),
                    reason="Sweden official act text was parsed but base IR construction failed",
                    doc_url=doc_url,
                    pdf_url=pdf_url,
                    phase="extraction",
                    exception_type=type(exc).__name__,
                )
            else:
                # KNOW-01 wrap (downstream-derived): the base_ir locator is
                # written from the act text just (re-)derived above. Same
                # force-reextract / manual-reingest trigger semantics as act_json.
                se_store_with_overwrite_event(
                    archive,
                    se_official_base_ir_locator(sfs_id),
                    _json_bytes(base_statute.to_jsonable_dict()),
                    sfs_id=sfs_id,
                    source_trigger="force_reextract" if force_reextract else "manual_reingest",
                    events_out=overwrite_events_out,
                    storage_class="json",
                )

    artifacts = SEOfficialArtifacts(
        sfs_id=sfs_id,
        doc_url=doc_url,
        doc_locator=se_official_doc_locator(sfs_id),
        pdf_url=pdf_url,
        pdf_locator=se_official_pdf_locator(sfs_id),
        pdf_text_url=text_url,
        pdf_cleaned_text_url=cleaned_text_url,
    )
    archive_se_official_artifacts_manifest(archive, artifacts)
    return artifacts


def _record_se_official_artifacts_diagnostic(
    diagnostics_out: list[dict[str, Any]] | None,
    *,
    rule_id: str,
    sfs_id: str,
    locator: str,
    reason: str,
    doc_url: str,
    pdf_url: str | None,
    phase: str = "acquisition",
    exception_type: str = "",
    blocking: bool = True,
    doc_status: str = "",
    selected_pdf_lane: str = "",
    pdf_source_attempts: tuple[dict[str, str], ...] = (),
) -> None:
    if diagnostics_out is None:
        return
    detail: dict[str, Any] = {
        "sfs_id": sfs_id,
        "locator": locator,
        "doc_url": doc_url,
        "pdf_url": pdf_url or "",
    }
    if exception_type:
        detail["exception_type"] = exception_type
    if doc_status:
        detail["doc_status"] = doc_status
    if selected_pdf_lane:
        detail["selected_pdf_lane"] = selected_pdf_lane
    if pdf_source_attempts:
        detail["pdf_source_attempts"] = pdf_source_attempts
    if selected_pdf_lane:
        diagnostics_out.append(
            SourceLaneSelectionEvidence(
                rule_id=rule_id,
                phase=phase,
                reason=reason,
                selected_lane=selected_pdf_lane,
                selected_locator=pdf_url or locator,
                blocking=blocking,
                strict_disposition="block" if blocking else "record",
                quirks_disposition="record",
                attempts=tuple(source_lane_attempt_from_mapping(row) for row in pdf_source_attempts),
                detail=detail,
            ).to_diagnostic_detail()
        )
        return
    diagnostic = diagnostic_detail(
        rule_id=rule_id,
        family="source_pathology",
        phase=phase,
        reason=reason,
        blocking=blocking,
        detail=detail,
    )
    diagnostics_out.append(diagnostic)


def fetch_se_rk_current_json(
    sfs_id: str,
    archive: _ArchiveLike,
    *,
    max_age_hours: float = _CURRENT_SURFACE_CACHE_HOURS,
    diagnostics_out: list[dict[str, Any]] | None = None,
) -> Optional[bytes]:
    locator = se_rk_current_json_locator(sfs_id)
    if archive.has(locator, max_age_hours=max_age_hours):
        cached = archive.get(locator)
        if cached is not None:
            return cached

    url = "https://beta.rkrattsbaser.gov.se/elasticsearch/SearchEsByRawJson"
    payload = {
        "searchIndexes": ["Sfs"],
        "api": "search",
        "json": {
            "query": {
                "bool": {
                    "must": [
                        {"term": {"beteckning.keyword": sfs_id}},
                        {"term": {"publicerad": True}},
                    ]
                }
            },
            "size": 1,
        },
    }
    raw = _curl_json_post(
        url,
        headers=[
            "-H",
            "content-type: application/json",
            "-H",
            f"referer: https://beta.rkrattsbaser.gov.se/sfs/item?bet={sfs_id.replace(':', '%3A')}&tab=forfattningstext",
        ],
        payload=payload,
    )
    if raw is None:
        _record_se_rk_current_diagnostic(
            diagnostics_out,
            rule_id="se_rk_current_fetch_failed",
            sfs_id=sfs_id,
            locator=locator,
            phase="acquisition",
            reason="Sweden RK current JSON request returned no payload",
        )
        return None
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _record_se_rk_current_diagnostic(
            diagnostics_out,
            rule_id="se_rk_current_invalid_json",
            sfs_id=sfs_id,
            locator=locator,
            phase="parse",
            reason="Sweden RK current JSON response could not be decoded",
        )
        return None
    if not isinstance(decoded, dict):
        _record_se_rk_current_diagnostic(
            diagnostics_out,
            rule_id="se_rk_current_invalid_root",
            sfs_id=sfs_id,
            locator=locator,
            phase="parse",
            reason="Sweden RK current JSON response root was not an object",
        )
        return None
    hits_parent = decoded.get("hits")
    if not isinstance(hits_parent, dict):
        _record_se_rk_current_diagnostic(
            diagnostics_out,
            rule_id="se_rk_current_missing_hits_container",
            sfs_id=sfs_id,
            locator=locator,
            phase="parse",
            reason="Sweden RK current JSON response did not contain a hits object",
        )
        return None
    hits = hits_parent.get("hits")
    if not isinstance(hits, list) or not hits:
        _record_se_rk_current_diagnostic(
            diagnostics_out,
            rule_id="se_rk_current_no_hits",
            sfs_id=sfs_id,
            locator=locator,
            phase="acquisition",
            reason="Sweden RK current JSON response contained no published SFS hit",
        )
        return None
    first_hit = hits[0]
    if not isinstance(first_hit, dict):
        _record_se_rk_current_diagnostic(
            diagnostics_out,
            rule_id="se_rk_current_invalid_hit",
            sfs_id=sfs_id,
            locator=locator,
            phase="parse",
            reason="Sweden RK current JSON response first hit was not an object",
        )
        return None
    source = first_hit.get("_source")
    if not isinstance(source, dict):
        _record_se_rk_current_diagnostic(
            diagnostics_out,
            rule_id="se_rk_current_invalid_source",
            sfs_id=sfs_id,
            locator=locator,
            phase="parse",
            reason="Sweden RK current JSON response first hit did not contain an object _source",
        )
        return None

    current_json = _json_bytes(source)
    archive.store(locator, current_json, storage_class="json")
    return current_json


def _record_se_rk_current_diagnostic(
    diagnostics_out: list[dict[str, Any]] | None,
    *,
    rule_id: str,
    sfs_id: str,
    locator: str,
    phase: str,
    reason: str,
) -> None:
    if diagnostics_out is None:
        return
    diagnostics_out.append(
        diagnostic_detail(
            rule_id=rule_id,
            family="source_pathology",
            phase=phase,
            reason=reason,
            blocking=True,
            sfs_id=sfs_id,
            locator=locator,
        )
    )


def build_se_source_bundle(
    payload: bytes | str | JsonObject,
    *,
    doc_html: bytes | str | None = None,
) -> SESourceBundle:
    """Build the first Sweden bundle from current-text JSON and optional doc HTML."""
    source_record = parse_se_source_record(payload)
    if doc_html is not None:
        source_record = enrich_se_source_record_with_doc_page(source_record, doc_html)
    current_statute = parse_se_statute(payload)
    return SESourceBundle(
        source_record=source_record,
        current_statute=current_statute,
        official_artifacts=None,
    )


def archive_se_source_bundle(
    payload: bytes | str | JsonObject,
    archive: _ArchiveLike,
    *,
    doc_html: bytes | str | None = None,
) -> SESourceBundle:
    """Archive Sweden current-source artifacts from local JSON and optional doc HTML.

    Stored artifacts:
    - `se://.../rk.current.json`
    - `se://.../source_record.json`
    - `se://.../current.ir.json`
    - `se://.../bundle.json`
    - `se://.../official.doc.html` when doc HTML is provided
    """
    bundle = build_se_source_bundle(payload, doc_html=doc_html)
    sfs_id = bundle.source_record.sfs_id

    archive.store(
        se_rk_current_json_locator(sfs_id),
        _json_bytes(_normalize_jsonable(_coerce_payload_to_dict(payload))),
        storage_class="json",
    )
    archive.store(
        se_source_record_locator(sfs_id),
        _json_bytes(se_source_record_to_dict(bundle.source_record)),
        storage_class="json",
    )
    archive.store(
        se_current_ir_locator(sfs_id),
        _json_bytes(bundle.current_statute.to_jsonable_dict()),
        storage_class="json",
    )
    archive.store(
        se_bundle_manifest_locator(sfs_id), _json_bytes(se_source_bundle_to_dict(bundle)), storage_class="json"
    )

    if doc_html is not None:
        if isinstance(doc_html, str):
            doc_bytes = doc_html.encode("utf-8")
        else:
            doc_bytes = doc_html
        archive.store(se_official_doc_locator(sfs_id), doc_bytes, storage_class="html")

    return bundle


def _read_json_locator(archive: _ArchiveLike, locator: str) -> Optional[JsonObject]:
    raw = archive.get(locator)
    if raw is None:
        return None
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"archive locator {locator} did not decode to a JSON object")
    return data


def _migrate_legacy_se_ir_blob(blob: JsonObject) -> JsonObject:
    """Normalize a legacy Sweden IR blob to the current core schema on read.

    Some archived Sweden ``current.ir.json`` / ``official.base.ir.json`` blobs
    predate the core rename of the bare-statute ``schedules`` field to
    ``supplements``. Core's ``ir_statute_from_dict`` now rejects the legacy
    ``schedules`` key outright, so the rename must happen SE-locally before the
    payload reaches core. The two fields hold the identical shape — a list of
    serialized ``IRNode`` supplements (appendices/transition containers) — so
    this is a faithful key rename, not a lossy restructure.

    If a blob already carries ``supplements`` it is returned untouched. If a
    legacy blob somehow carries both keys, that is an ambiguous half-migrated
    payload and we refuse to guess rather than silently drop data.
    """
    if "schedules" not in blob:
        return blob
    if "supplements" in blob:
        raise ValueError(
            "Sweden IR blob carries both legacy 'schedules' and current "
            "'supplements' keys; refusing to guess which holds the live "
            "supplements. Re-derive the archived IR for this act."
        )
    migrated = dict(blob)
    migrated["supplements"] = migrated.pop("schedules")
    return migrated


def _read_se_ir_blob(archive: _ArchiveLike, locator: str) -> Optional[JsonObject]:
    """Read a Sweden IR blob, migrating the legacy ``schedules`` key on read."""
    blob = _read_json_locator(archive, locator)
    if blob is None:
        return None
    return _migrate_legacy_se_ir_blob(blob)


def load_se_source_record_from_archive(archive: _ArchiveLike, sfs_id: str) -> Optional[JsonObject]:
    return _read_json_locator(archive, se_source_record_locator(sfs_id))


def load_se_current_ir_from_archive(archive: _ArchiveLike, sfs_id: str) -> Optional[JsonObject]:
    return _read_se_ir_blob(archive, se_current_ir_locator(sfs_id))


def load_se_bundle_from_archive(archive: _ArchiveLike, sfs_id: str) -> Optional[JsonObject]:
    return _read_json_locator(archive, se_bundle_manifest_locator(sfs_id))


def load_se_official_act_from_archive(archive: _ArchiveLike, sfs_id: str) -> Optional[JsonObject]:
    return _read_json_locator(archive, se_official_act_locator(sfs_id))


def load_se_official_base_ir_from_archive(archive: _ArchiveLike, sfs_id: str) -> Optional[JsonObject]:
    return _read_se_ir_blob(archive, se_official_base_ir_locator(sfs_id))


def load_se_official_clause_surface_from_archive(archive: _ArchiveLike, sfs_id: str) -> Optional[JsonObject]:
    return _read_json_locator(archive, se_official_clause_surface_locator(sfs_id))


def load_se_official_payload_surface_from_archive(archive: _ArchiveLike, sfs_id: str) -> Optional[JsonObject]:
    return _read_json_locator(archive, se_official_payload_surface_locator(sfs_id))


def load_se_official_elaboration_from_archive(archive: _ArchiveLike, sfs_id: str) -> Optional[JsonObject]:
    return _read_json_locator(archive, se_official_elaboration_locator(sfs_id))


def load_se_official_effects_plan_from_archive(archive: _ArchiveLike, sfs_id: str) -> Optional[JsonObject]:
    return _read_json_locator(archive, se_official_effects_plan_locator(sfs_id))


def load_se_backfill_official_checkpoint_from_archive(archive: _ArchiveLike) -> Optional[JsonObject]:
    return _read_json_locator(archive, se_backfill_official_checkpoint_locator())


def load_se_backfill_official_status_from_archive(archive: _ArchiveLike) -> Optional[JsonObject]:
    return _read_json_locator(archive, se_backfill_official_status_locator())


def load_se_backfill_official_history_from_archive(archive: _ArchiveLike) -> Optional[JsonObjectList]:
    raw = archive.get(se_backfill_official_history_locator())
    if raw is None:
        return None
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"archive locator {se_backfill_official_history_locator()} did not decode to a JSON array")
    malformed_indexes = [index for index, item in enumerate(data) if not isinstance(item, dict)]
    if malformed_indexes:
        indexes = ", ".join(str(index) for index in malformed_indexes)
        raise ValueError(
            f"archive locator {se_backfill_official_history_locator()} contains non-object entries at indexes: {indexes}"
        )
    return data


def load_se_backfill_official_completeness_from_archive(archive: _ArchiveLike) -> Optional[JsonObject]:
    return _read_json_locator(archive, se_backfill_official_completeness_locator())


def load_se_backfill_official_gap_report_from_archive(archive: _ArchiveLike) -> Optional[JsonObject]:
    return _read_json_locator(archive, se_backfill_official_gap_report_locator())


def load_se_backfill_official_chunk_plan_from_archive(archive: _ArchiveLike) -> Optional[JsonObject]:
    return _read_json_locator(archive, se_backfill_official_chunk_plan_locator())


def compile_se_official_ops_to_archive(archive: _ArchiveLike, sfs_id: str) -> JsonObjectList:
    act = load_se_official_act_from_archive(archive, sfs_id)
    if act is None:
        raise FileNotFoundError(f"no archived official act surface for {sfs_id}")
    act_surface = _coerce_official_act(act)
    clause_surface = _build_se_official_clause_surface(act_surface)
    payload_surface = _build_se_official_payload_surface(act_surface)
    elaboration = _build_se_official_elaboration(act_surface)
    effects_plan = _build_se_official_effects_plan(elaboration)
    archive.store(
        se_official_clause_surface_locator(sfs_id),
        _json_bytes(_normalize_jsonable(se_official_clause_surface_to_dict(clause_surface))),
        storage_class="json",
    )
    archive.store(
        se_official_payload_surface_locator(sfs_id),
        _json_bytes(_normalize_jsonable(se_official_payload_surface_to_dict(payload_surface))),
        storage_class="json",
    )
    archive.store(
        se_official_elaboration_locator(sfs_id),
        _json_bytes(_normalize_jsonable(se_official_elaboration_to_dict(elaboration))),
        storage_class="json",
    )
    archive.store(
        se_official_effects_plan_locator(sfs_id),
        _json_bytes(_normalize_jsonable(se_official_effect_plan_to_dict(effects_plan))),
        storage_class="json",
    )
    adjudications: list[CompileAdjudication] = []
    try:
        ops = _lower_se_official_effects_plan(effects_plan, source_id=sfs_id, adjudications_out=adjudications)
    except NotImplementedError:
        archive.store(
            se_official_ops_adjudications_locator(sfs_id),
            _json_bytes(_normalize_jsonable([asdict(item) for item in adjudications])),
            storage_class="json",
        )
        archive_se_official_phase_artifacts_manifest(archive, sfs_id)
        raise
    ops_json = [se_legal_operation_to_dict(op) for op in ops]
    archive.store(se_official_ops_locator(sfs_id), _json_bytes(ops_json), storage_class="json")
    archive.store(
        se_official_ops_adjudications_locator(sfs_id),
        _json_bytes(_normalize_jsonable([asdict(item) for item in adjudications])),
        storage_class="json",
    )
    archive_se_official_phase_artifacts_manifest(archive, sfs_id)
    return ops_json


def archive_se_backfill_official_checkpoint(archive: _ArchiveLike, checkpoint: dict[str, Any]) -> None:
    archive.store(
        se_backfill_official_checkpoint_locator(),
        _json_bytes(_normalize_jsonable(checkpoint)),
        storage_class="json",
    )


def archive_se_backfill_official_status(archive: _ArchiveLike, official_status: dict[str, Any]) -> None:
    archive.store(
        se_backfill_official_status_locator(),
        _json_bytes(_normalize_jsonable(official_status)),
        storage_class="json",
    )


def archive_se_backfill_official_history(archive: _ArchiveLike, history: list[dict[str, Any]]) -> None:
    archive.store(
        se_backfill_official_history_locator(),
        _json_bytes(_normalize_jsonable(history)),
        storage_class="json",
    )


def archive_se_backfill_official_completeness(archive: _ArchiveLike, completeness: dict[str, Any]) -> None:
    archive.store(
        se_backfill_official_completeness_locator(),
        _json_bytes(_normalize_jsonable(completeness)),
        storage_class="json",
    )


def archive_se_backfill_official_gap_report(archive: _ArchiveLike, gap_report: dict[str, Any]) -> None:
    archive.store(
        se_backfill_official_gap_report_locator(),
        _json_bytes(_normalize_jsonable(gap_report)),
        storage_class="json",
    )


def archive_se_backfill_official_chunk_plan(archive: _ArchiveLike, chunk_plan: dict[str, Any]) -> None:
    archive.store(
        se_backfill_official_chunk_plan_locator(),
        _json_bytes(_normalize_jsonable(chunk_plan)),
        storage_class="json",
    )


def load_se_official_ops_from_archive(archive: _ArchiveLike, sfs_id: str) -> Optional[JsonObjectList]:
    raw = archive.get(se_official_ops_locator(sfs_id))
    if raw is None:
        return None
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"archive locator {se_official_ops_locator(sfs_id)} did not decode to a JSON array")
    non_object_indexes = [
        str(index)
        for index, item in enumerate(data)
        if not isinstance(item, dict)
    ]
    if non_object_indexes:
        indexes = ", ".join(non_object_indexes)
        raise ValueError(
            f"archive locator {se_official_ops_locator(sfs_id)} contained non-object op entries at indexes: {indexes}"
        )
    return data


def load_se_official_ops_adjudications_from_archive(archive: _ArchiveLike, sfs_id: str) -> Optional[JsonObjectList]:
    raw = archive.get(se_official_ops_adjudications_locator(sfs_id))
    if raw is None:
        return None
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, list):
        raise ValueError(
            f"archive locator {se_official_ops_adjudications_locator(sfs_id)} did not decode to a JSON array"
        )
    non_object_indexes = [
        str(index)
        for index, item in enumerate(data)
        if not isinstance(item, dict)
    ]
    if non_object_indexes:
        indexes = ", ".join(non_object_indexes)
        raise ValueError(
            f"archive locator {se_official_ops_adjudications_locator(sfs_id)} "
            f"contained non-object adjudication entries at indexes: {indexes}"
        )
    return data


def _detect_se_current_surface_contamination(
    pre_statute: IRStatute,
    ops: list[LegalOperation],
    *,
    amending_sfs_id: str = "",
) -> list[dict[str, str]]:
    pre_section_texts = se_section_text_map(pre_statute)
    pre_sections = set(pre_section_texts)
    pre_headings = set(se_heading_before_section_map(pre_statute))
    pre_appendix_texts = se_appendix_text_map(pre_statute)
    pre_appendices = set(pre_appendix_texts)

    def _provenance_fields(text: str) -> dict[str, str]:
        normalized = " ".join(text.split())
        placeholder_match = _SE_RENUMBER_PLACEHOLDER_SFS_RE.search(normalized)
        attribution_match = _SE_ATTRIBUTION_SFS_RE.search(normalized)
        source_sfs_id = ""
        origin_hint = "unknown"
        if placeholder_match:
            source_sfs_id = placeholder_match.group(1)
            origin_hint = "renumber_placeholder"
        elif attribution_match:
            source_sfs_id = attribution_match.group(1)
            origin_hint = "trailing_attribution"
        reverse_patch_candidate = (
            "yes"
            if amending_sfs_id and source_sfs_id == amending_sfs_id
            else ("unknown" if not source_sfs_id else "no")
        )
        return {
            "source_sfs_id": source_sfs_id,
            "origin_hint": origin_hint,
            "reverse_patch_candidate": reverse_patch_candidate,
        }

    issues: list[dict[str, str]] = []
    for op in ops:
        if op.target.leaf_kind() == "section":
            if op.action is StructuralAction.INSERT and op.target.special is not FacetKind.HEADING:
                label = op.target.leaf_label()
                if label in pre_sections:
                    issues.append(
                        {
                            "target_kind": "section",
                            "label": label,
                            "issue": "preexisting_insert_target",
                            "action": op.action.value,
                            **_provenance_fields(pre_section_texts.get(label, "")),
                        }
                    )
            elif op.action is StructuralAction.RENUMBER and op.destination is not None:
                destination_label = op.destination.leaf_label()
                if destination_label in pre_sections:
                    issues.append(
                        {
                            "target_kind": "section",
                            "label": destination_label,
                            "issue": "preexisting_renumber_destination",
                            "action": op.action.value,
                            **_provenance_fields(pre_section_texts.get(destination_label, "")),
                        }
                    )
            elif op.target.special is FacetKind.HEADING and op.action is StructuralAction.INSERT:
                label = op.target.leaf_label()
                if label in pre_headings:
                    issues.append(
                        {
                            "target_kind": "heading",
                            "label": label,
                            "issue": "preexisting_insert_target",
                            "action": op.action.value,
                            "source_sfs_id": "",
                            "origin_hint": "unknown",
                            "reverse_patch_candidate": "unknown",
                        }
                    )
        elif op.target.leaf_kind() == "appendix" and op.action is StructuralAction.INSERT:
            label = op.target.leaf_label()
            if label in pre_appendices:
                issues.append(
                    {
                        "target_kind": "appendix",
                        "label": label,
                        "issue": "preexisting_insert_target",
                        "action": op.action.value,
                        **_provenance_fields(pre_appendix_texts.get(label, "")),
                    }
                )
    return issues


def _parse_se_sfs_sort_key(sfs_id: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d{4}):(\d+)", sfs_id.strip())
    if not match:
        return (0, 0)
    return (int(match.group(1)), int(match.group(2)))


def _classify_se_historical_recovery_strategy(
    amending_sfs_id: str,
    contamination: list[dict[str, str]],
    self_reverse_residual: list[dict[str, str]],
    later_reverse_residual: list[dict[str, str]],
) -> str:
    if not contamination:
        return "direct_replay"
    if not self_reverse_residual:
        return "self_reverse_only"
    if not later_reverse_residual:
        return "available_later_reverse_chain"
    residual_sources = {
        str(item.get("source_sfs_id") or "") for item in later_reverse_residual if str(item.get("source_sfs_id") or "")
    }
    if residual_sources and all(
        _parse_se_sfs_sort_key(source) > _parse_se_sfs_sort_key(amending_sfs_id) for source in residual_sources
    ):
        return "later_reverse_chain"
    return "older_base_required"


def _build_se_later_chain_hints(
    archive: _ArchiveLike,
    amending_sfs_id: str,
    self_reverse_residual: list[dict[str, str]],
) -> list[dict[str, Any]]:
    later_sources = sorted(
        {
            source
            for item in self_reverse_residual
            if (source := str(item.get("source_sfs_id") or ""))
            and _parse_se_sfs_sort_key(source) > _parse_se_sfs_sort_key(amending_sfs_id)
        },
        key=_parse_se_sfs_sort_key,
    )
    hints: list[dict[str, Any]] = []
    for source in later_sources:
        hints.append(
            {
                "sfs_id": source,
                "official_act_available": load_se_official_act_from_archive(archive, source) is not None,
                "pdf_available": has_valid_se_official_pdf(archive, source),
                "doc_available": archive.get(se_official_doc_locator(source)) is not None,
            }
        )
    return hints


def _clone_irnode_with_label(node: IRNode, label: str) -> IRNode:
    return IRNode(
        kind=node.kind,
        label=label,
        text=node.text,
        attrs=dict(node.attrs),
        children=tuple(node.children),
    )


def _reverse_patch_se_self_contamination(
    pre_statute: IRStatute,
    ops: list[LegalOperation],
    contamination: list[dict[str, str]],
) -> IRStatute:
    body = pre_statute.body
    supplements = list(pre_statute.supplements)
    for item in contamination:
        if str(item.get("reverse_patch_candidate") or "") != "yes":
            continue
        target_kind = str(item.get("target_kind") or "")
        label = str(item.get("label") or "")
        issue = str(item.get("issue") or "")
        action = str(item.get("action") or "")
        if target_kind == "section" and issue == "preexisting_insert_target" and action == "insert":
            path = tree_ops.find(body, "section", label)
            if path is not None:
                body = tree_ops.remove_at(body, path)
            continue
        if target_kind == "section" and issue == "preexisting_renumber_destination" and action == "renumber":
            matching = next(
                (
                    op
                    for op in ops
                    if op.action is StructuralAction.RENUMBER and op.destination is not None and op.destination.leaf_label() == label
                ),
                None,
            )
            if matching is None:
                continue
            path = tree_ops.find(body, "section", label)
            if path is None:
                continue
            node = tree_ops.resolve(body, path)
            if node is None:
                continue
            body = tree_ops.replace_at(body, path, _clone_irnode_with_label(node, matching.target.leaf_label()))
            continue
        if target_kind == "appendix" and issue == "preexisting_insert_target" and action == "insert":
            supplements = [
                supplement
                for supplement in supplements
                if not (supplement.kind is IRNodeKind.APPENDIX and (supplement.label or "") == label)
            ]
    metadata = dict(pre_statute.metadata)
    metadata["self_reverse_patch_applied"] = True
    return IRStatute(
        statute_id=pre_statute.statute_id,
        title=pre_statute.title,
        body=body,
        supplements=supplements,
        metadata=metadata,
    )


def _invert_se_reversible_ops(ops: list[LegalOperation], *, source_sfs_id: str) -> list[LegalOperation]:
    inverse_ops: list[LegalOperation] = []
    next_sequence = 1
    for op in reversed(ops):
        inverse: LegalOperation | None = None
        if op.target.leaf_kind() == "section":
            if op.target.special is FacetKind.HEADING and op.action is StructuralAction.INSERT:
                inverse = LegalOperation(
                    op_id=f"se_reverse_heading_{source_sfs_id}_{next_sequence}",
                    sequence=next_sequence,
                    action=StructuralAction.REPEAL,
                    target=op.target,
                    source=op.source,
                    provenance_tags=("sweden_later_chain_reverse_v1", f"source_sfs_id={source_sfs_id}"),
                    group_id=f"se_reverse_chain::{source_sfs_id}",
                )
            elif op.action is StructuralAction.INSERT:
                inverse = LegalOperation(
                    op_id=f"se_reverse_insert_{source_sfs_id}_{op.target.leaf_label()}_{next_sequence}",
                    sequence=next_sequence,
                    action=StructuralAction.REPEAL,
                    target=op.target,
                    source=op.source,
                    provenance_tags=("sweden_later_chain_reverse_v1", f"source_sfs_id={source_sfs_id}"),
                    group_id=f"se_reverse_chain::{source_sfs_id}",
                )
            elif op.action is StructuralAction.RENUMBER and op.destination is not None:
                inverse = LegalOperation(
                    op_id=f"se_reverse_renumber_{source_sfs_id}_{op.destination.leaf_label()}_{next_sequence}",
                    sequence=next_sequence,
                    action=StructuralAction.RENUMBER,
                    target=op.destination,
                    destination=op.target,
                    source=op.source,
                    provenance_tags=("sweden_later_chain_reverse_v1", f"source_sfs_id={source_sfs_id}"),
                    group_id=f"se_reverse_chain::{source_sfs_id}",
                )
        elif op.target.leaf_kind() == "appendix" and op.action is StructuralAction.INSERT:
            inverse = LegalOperation(
                op_id=f"se_reverse_appendix_{source_sfs_id}_{op.target.leaf_label()}_{next_sequence}",
                sequence=next_sequence,
                action=StructuralAction.REPEAL,
                target=op.target,
                source=op.source,
                provenance_tags=("sweden_later_chain_reverse_v1", f"source_sfs_id={source_sfs_id}"),
                group_id=f"se_reverse_chain::{source_sfs_id}",
            )
        if inverse is not None:
            inverse_ops.append(inverse)
            next_sequence += 1
    return inverse_ops


def _reverse_patch_se_available_later_chain(
    archive: _ArchiveLike,
    pre_statute: IRStatute,
    amending_sfs_id: str,
    self_reverse_residual: list[dict[str, str]],
) -> IRStatute:
    later_sources = sorted(
        {
            source
            for item in self_reverse_residual
            if (source := str(item.get("source_sfs_id") or ""))
            and _parse_se_sfs_sort_key(source) > _parse_se_sfs_sort_key(amending_sfs_id)
        },
        key=_parse_se_sfs_sort_key,
        reverse=True,
    )
    statute = pre_statute
    reverse_adjudications: list[CompileAdjudication] = []
    for source in later_sources:
        ops_json = load_se_official_ops_from_archive(archive, source)
        if ops_json is None:
            loaded_act = load_se_official_act_from_archive(archive, source)
            if loaded_act is None:
                continue
            try:
                # Persist typed waists/ops only when the archive accepts writes;
                # the readonly-path branch inlines the compile via the pure
                # ``compile_se_official_act_ops`` so coverage-scan workers
                # (shared readonly Farchive) do not crash with
                # ``sqlite3.OperationalError: attempt to write a readonly database``.
                if _se_archive_is_writable(archive):
                    ops_json = compile_se_official_ops_to_archive(archive, source)
                else:
                    ops_json = [
                        se_legal_operation_to_dict(op)
                        for op in compile_se_official_act_ops(loaded_act, source_id=source)
                    ]
            except (FileNotFoundError, NotImplementedError, ValueError):
                ops_json = None
        if not ops_json:
            continue
        later_ops = [se_legal_operation_from_dict(op) for op in ops_json]
        inverse_ops = _invert_se_reversible_ops(later_ops, source_sfs_id=source)
        if not inverse_ops:
            continue
        for inverse_op in inverse_ops:
            if inverse_op.action is StructuralAction.RENUMBER and inverse_op.destination is not None:
                destination_label = inverse_op.destination.leaf_label()
                existing_text = se_section_text_map(statute).get(destination_label, "")
                placeholder_match = _SE_RENUMBER_PLACEHOLDER_SFS_RE.search(" ".join(existing_text.split()))
                if placeholder_match and placeholder_match.group(1) == source:
                    placeholder_path = tree_ops.find(statute.body, "section", destination_label)
                    if placeholder_path is not None:
                        body = tree_ops.remove_at(statute.body, placeholder_path)
                        statute = IRStatute(
                            statute_id=statute.statute_id,
                            title=statute.title,
                            body=body,
                            supplements=list(statute.supplements),
                            metadata=dict(statute.metadata),
                        )
            try:
                before_adjudication_count = len(reverse_adjudications)
                statute = apply_se_ops(statute, [inverse_op], adjudications_out=reverse_adjudications)
                if len(reverse_adjudications) > before_adjudication_count:
                    latest = reverse_adjudications[-1]
                    latest_detail = dict(latest.detail)
                    latest_detail.setdefault("reverse_source_sfs_id", source)
                    reverse_adjudications[-1] = CompileAdjudication(
                        kind=latest.kind,
                        message=latest.message,
                        source_statute=latest.source_statute,
                        op_id=latest.op_id,
                        blocking=latest.blocking,
                        phase=latest.phase,
                        detail=latest_detail,
                    )
            except (LookupError, NotImplementedError, ValueError) as exc:
                reverse_adjudications.append(
                    CompileAdjudication(
                        kind="se_later_chain_reverse_op_exception",
                        message="Sweden later-chain reverse patch skipped an inverse operation after replay raised.",
                        source_statute=f"se/{source}",
                        op_id=inverse_op.op_id,
                        blocking=True,
                        phase="replay",
                        detail=diagnostic_detail(
                            rule_id="se_later_chain_reverse_op_exception",
                            phase="replay",
                            family="target_resolution_recovery",
                            blocking=True,
                            reverse_source_sfs_id=source,
                            action=inverse_op.action.value,
                            target=inverse_op.target.leaf_label(),
                            exception_type=type(exc).__name__,
                            error=str(exc),
                        ),
                    )
                )
                continue
    metadata = dict(statute.metadata)
    metadata["later_chain_reverse_applied"] = True
    if reverse_adjudications:
        metadata["later_chain_reverse_adjudications"] = [
            {
                "kind": adjudication.kind,
                "message": adjudication.message,
                "source_statute": adjudication.source_statute,
                "op_id": adjudication.op_id,
                "detail": adjudication.detail,
            }
            for adjudication in reverse_adjudications
        ]
    return IRStatute(
        statute_id=statute.statute_id,
        title=statute.title,
        body=statute.body,
        supplements=list(statute.supplements),
        metadata=metadata,
    )


def _has_se_noninvertible_placeholder_blocker(
    archive: _ArchiveLike,
    amending_sfs_id: str,
    residual_items: list[dict[str, str]],
) -> bool:
    for item in residual_items:
        source_sfs_id = str(item.get("source_sfs_id") or "")
        if not source_sfs_id or _parse_se_sfs_sort_key(source_sfs_id) <= _parse_se_sfs_sort_key(amending_sfs_id):
            continue
        label = str(item.get("label") or "")
        ops_json = load_se_official_ops_from_archive(archive, source_sfs_id)
        if ops_json is None:
            loaded_act = load_se_official_act_from_archive(archive, source_sfs_id)
            if loaded_act is None:
                continue
            try:
                # Same readonly-archive bridge as above: persist via the
                # archive-mutating path only when the archive accepts writes,
                # otherwise inline through ``compile_se_official_act_ops`` so
                # readonly scan workers do not crash mid-stream.
                if _se_archive_is_writable(archive):
                    ops_json = compile_se_official_ops_to_archive(archive, source_sfs_id)
                else:
                    ops_json = [
                        se_legal_operation_to_dict(op)
                        for op in compile_se_official_act_ops(loaded_act, source_id=source_sfs_id)
                    ]
            except (FileNotFoundError, NotImplementedError, ValueError):
                ops_json = None
        if not ops_json:
            continue
        later_ops = [se_legal_operation_from_dict(op) for op in ops_json]
        destination_labels = {
            op.destination.leaf_label()
            for op in later_ops
            if op.action is StructuralAction.RENUMBER and op.destination is not None and op.target.leaf_label() == label
        }
        if not destination_labels:
            continue
        repealed_labels = {
            op.target.leaf_label() for op in later_ops if op.action is StructuralAction.REPEAL and op.target.leaf_kind() == "section"
        }
        if destination_labels & repealed_labels:
            return True
    return False


def _detect_se_replay_precondition_issues(
    pre_statute: IRStatute,
    ops: list[LegalOperation],
) -> list[dict[str, str]]:
    section_labels = set(se_section_text_map(pre_statute))
    appendix_labels = set(se_appendix_text_map(pre_statute))
    issues: list[dict[str, str]] = []
    for op in ops:
        leaf_kind = op.target.leaf_kind()
        label = op.target.leaf_label()
        if leaf_kind == "section":
            if op.target.special is FacetKind.HEADING:
                if label not in section_labels:
                    issues.append(
                        {
                            "target_kind": "heading",
                            "label": label,
                            "issue": "missing_heading_anchor_section",
                            "action": op.action.value,
                        }
                    )
                continue
            if op.action is StructuralAction.INSERT:
                if label in section_labels:
                    issues.append(
                        {
                            "target_kind": "section",
                            "label": label,
                            "issue": "preexisting_insert_target",
                            "action": op.action.value,
                        }
                    )
                else:
                    section_labels.add(label)
                continue
            if op.action is StructuralAction.RENUMBER:
                destination_label = op.destination.leaf_label() if op.destination is not None else ""
                if label not in section_labels:
                    issues.append(
                        {
                            "target_kind": "section",
                            "label": label,
                            "issue": "missing_renumber_source",
                            "action": op.action.value,
                        }
                    )
                else:
                    section_labels.discard(label)
                    if destination_label:
                        section_labels.add(destination_label)
                continue
            if op.action.value in {"replace", "repeal"} and label not in section_labels:
                issues.append(
                    {
                        "target_kind": "section",
                        "label": label,
                        "issue": f"missing_{op.action.value}_source",
                        "action": op.action.value,
                    }
                )
            elif op.action.value == "repeal":
                section_labels.discard(label)
                continue
        elif leaf_kind == "appendix":
            if op.action.value == "insert":
                if label in appendix_labels:
                    issues.append(
                        {
                            "target_kind": "appendix",
                            "label": label,
                            "issue": "preexisting_insert_target",
                            "action": op.action.value,
                        }
                    )
                else:
                    appendix_labels.add(label)
                continue
            if op.action.value in {"replace", "repeal"} and label not in appendix_labels:
                issues.append(
                    {
                        "target_kind": "appendix",
                        "label": label,
                        "issue": f"missing_{op.action.value}_source",
                        "action": op.action.value,
                    }
                )
            elif op.action.value == "repeal":
                appendix_labels.discard(label)
    return issues


def _scope_mentions_se_label(scope_text: str, label: str) -> bool:
    normalized = " ".join(scope_text.lower().replace("§", " ").split())
    match = re.fullmatch(r"(\d+)([a-z]?)", label.lower())
    if not match:
        return False
    number = match.group(1)
    suffix = match.group(2)
    if suffix:
        return re.search(rf"\b{number}\s*{suffix}\b", normalized) is not None
    return re.search(rf"\b{number}\b", normalized) is not None


def _build_se_replay_precondition_ancestry_hints(
    archive: _ArchiveLike,
    current_json: bytes,
    amending_sfs_id: str,
    effective_date: str,
    precondition_issues: list[dict[str, str]],
    later_chain_hints: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    source_record = parse_se_source_record(current_json)
    hints: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for chain_hint in later_chain_hints:
        source_sfs_id = str(chain_hint.get("sfs_id") or "")
        if not source_sfs_id or not bool(chain_hint.get("official_act_available")):
            continue
        ops_json = load_se_official_ops_from_archive(archive, source_sfs_id)
        if ops_json is None:
            continue
        later_ops = [se_legal_operation_from_dict(op) for op in ops_json]
        renumber_map = {
            op.destination.leaf_label(): op.target.leaf_label()
            for op in later_ops
            if op.action.value == "renumber" and op.destination is not None
        }
        for issue in precondition_issues:
            label = str(issue.get("label") or "")
            derived_from_label = renumber_map.get(label, "")
            direct_later_actions = sorted(
                {
                    (
                        "renumber_destination"
                        if op.action.value == "renumber"
                        and op.destination is not None
                        and op.destination.leaf_label() == label
                        else op.action.value
                    )
                    for op in later_ops
                    if (
                        op.target.leaf_kind() == str(issue.get("target_kind") or "") and op.target.leaf_label() == label
                    )
                    or (
                        op.action.value == "renumber"
                        and op.destination is not None
                        and op.destination.leaf_label() == label
                    )
                }
            )
            if not derived_from_label:
                if not direct_later_actions:
                    continue
                derived_from_label = ""
            candidate_chain_sfs_ids = [
                entry.amending_sfs_id
                for entry in source_record.amendment_register
                if entry.amending_sfs_id
                and entry.amending_sfs_id != source_sfs_id
                and entry.effective_date
                and entry.effective_date > effective_date
                and _scope_mentions_se_label(entry.scope_text, derived_from_label)
            ]
            key = (label, derived_from_label, source_sfs_id)
            if key in seen:
                continue
            seen.add(key)
            hints.append(
                {
                    "label": label,
                    "issue": str(issue.get("issue") or ""),
                    "derived_from_label": derived_from_label,
                    "via_later_source": source_sfs_id,
                    "direct_later_actions": direct_later_actions,
                    "noninvertible_blocker": any(action in {"repeal", "replace"} for action in direct_later_actions),
                    "candidate_chain_sfs_ids": candidate_chain_sfs_ids,
                }
            )
    return hints


def _infer_se_effective_date_from_base_register(
    current_json: bytes,
    amending_sfs_id: str,
) -> str:
    source_record = parse_se_source_record(current_json)
    for entry in source_record.amendment_register:
        if entry.amending_sfs_id == amending_sfs_id and entry.effective_date:
            return entry.effective_date
    return ""


def _older_base_chain_entries(
    current_json: bytes,
    *,
    pre_date: str,
    exclude_sfs_id: str,
) -> list[dict[str, str]]:
    source_record = parse_se_source_record(current_json)
    rows: list[dict[str, str]] = []
    for entry in source_record.amendment_register:
        if not entry.amending_sfs_id or entry.amending_sfs_id == exclude_sfs_id:
            continue
        if not entry.effective_date or entry.effective_date > pre_date:
            continue
        rows.append(
            {
                "sfs_id": entry.amending_sfs_id,
                "effective_date": entry.effective_date,
                "title": entry.amending_title,
                "scope_text": entry.scope_text,
            }
        )
    rows.sort(
        key=lambda item: (str(item.get("effective_date") or ""), _parse_se_sfs_sort_key(str(item.get("sfs_id") or "")))
    )
    return rows


def _se_rebuild_chain_blocker_diagnostic(row: dict[str, Any]) -> dict[str, Any] | None:
    raw_ops_status = str(row.get("ops_status") or "")
    # The row arrives deserialized from JSON, so coerce to the closed enum. An
    # unrecognized value is schema drift, surfaced as a typed "unknown" diagnostic
    # (§1.10 named-failure discipline) rather than silently dropped; the recognized
    # set below is then exhaustive (assert_never).
    try:
        ops_status: SeOpsStatus | None = SeOpsStatus(raw_ops_status)
    except ValueError:
        ops_status = None
    if ops_status is SeOpsStatus.COMPILED:
        return None
    if ops_status is None:
        rule_id = "se_official_rebuild_chain_unknown_ops_status"
        phase = "replay_planning"
        reason = "prior Sweden amendment has an unknown rebuild-chain status"
    else:
        match ops_status:
            case SeOpsStatus.MISSING_OFFICIAL_ACT:
                rule_id = "se_official_rebuild_chain_missing_official_act"
                phase = "acquisition"
                reason = "prior Sweden amendment official act is unavailable"
            case SeOpsStatus.UNSUPPORTED:
                rule_id = "se_official_rebuild_chain_ops_unsupported"
                phase = "lowering"
                reason = "prior Sweden amendment official act uses unsupported effect shape"
            case SeOpsStatus.INVALID_OFFICIAL_ACT:
                rule_id = "se_official_rebuild_chain_invalid_official_act"
                phase = "extraction"
                reason = "prior Sweden amendment official act could not be parsed into replayable operations"
            case SeOpsStatus.COMPILED:  # handled above; kept for exhaustiveness
                return None
            case _ as unreachable:
                assert_never(unreachable)
    return diagnostic_detail(
        rule_id=rule_id,
        phase=phase,
        family="source_pathology",
        blocking=True,
        reason=reason,
        sfs_id=str(row.get("sfs_id") or ""),
        effective_date=str(row.get("effective_date") or ""),
        scope_text=str(row.get("scope_text") or ""),
        ops_status=raw_ops_status,
        error=str(row.get("error") or ""),
    )


def plan_se_older_base_rebuild(
    archive: _ArchiveLike,
    amending_sfs_id: str,
    *,
    base_sfs_id: str | None = None,
    as_of: str | None = None,
    fetch_missing: bool = False,
    probe_sources: bool = False,
) -> dict[str, Any]:
    analysis = analyze_se_official_replay_feasibility(
        archive,
        amending_sfs_id,
        base_sfs_id=base_sfs_id,
        as_of=as_of,
    )
    resolved_base_sfs_id = str(analysis["base_sfs_id"])
    current_json = archive.get(se_rk_current_json_locator(resolved_base_sfs_id))
    if current_json is None:
        raise FileNotFoundError(f"no archived RK current JSON for base statute {resolved_base_sfs_id}")

    # ``fetch_missing=True`` is a best-effort acquisition lane: a network or
    # parser failure inside ``fetch_se_official_artifacts`` must NOT crash the
    # replay, but it also must not vanish silently — §1.10 forbids swallowing
    # an exception that would otherwise let replay proceed against an empty
    # base_seed (``official_act_available=False`` with no diagnostic, masking
    # an acquisition fault as "no archived act"). Surface each acquisition
    # failure as a named diagnostic on ``base_seed`` so the replay outcome
    # carries the sfs_id + exception type + message instead of an empty slot.
    acquisition_failures: list[dict[str, str]] = []

    def _ensure_official_artifacts(sfs_id: str) -> None:
        if not fetch_missing:
            return
        if load_se_official_act_from_archive(archive, sfs_id) is not None:
            return
        try:
            fetch_se_official_artifacts(sfs_id, archive)
        except Exception as exc:  # noqa: BLE001 — acquisition boundary; surfaced as a typed residual below
            acquisition_failures.append(
                {
                    "rule_id": "se_official_artifacts_fetch_failed",
                    "sfs_id": sfs_id,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                }
            )

    _ensure_official_artifacts(resolved_base_sfs_id)
    base_seed: dict[str, Any] = {
        "sfs_id": resolved_base_sfs_id,
        "official_act_available": load_se_official_act_from_archive(archive, resolved_base_sfs_id) is not None,
        "official_base_ir_available": load_se_official_base_ir_from_archive(archive, resolved_base_sfs_id) is not None,
        "pdf_available": has_valid_se_official_pdf(archive, resolved_base_sfs_id),
        "doc_available": archive.get(se_official_doc_locator(resolved_base_sfs_id)) is not None,
    }
    if acquisition_failures:
        # §1.10 named diagnostic: an identifiable failure record (not a generic
        # "missing act" 404). The caller bakes ``base_seed`` into the
        # replay-outcome row, so the acquisition fault is observable downstream
        # rather than disguised as "no archived act."
        base_seed["official_act_acquisition_failures"] = list(acquisition_failures)
    if probe_sources and not (base_seed["official_act_available"] or base_seed["pdf_available"]):
        base_seed["public_source_probe"] = probe_se_public_source_status(resolved_base_sfs_id)

    chain_rows: list[dict[str, Any]] = []
    for item in _older_base_chain_entries(
        current_json,
        pre_date=str(analysis["pre_date"]),
        exclude_sfs_id=amending_sfs_id,
    ):
        sfs_id = str(item["sfs_id"])
        _ensure_official_artifacts(sfs_id)
        loaded_act = load_se_official_act_from_archive(archive, sfs_id)
        official_act_available = loaded_act is not None
        pdf_available = has_valid_se_official_pdf(archive, sfs_id)
        doc_available = archive.get(se_official_doc_locator(sfs_id)) is not None
        ops_status = SeOpsStatus.MISSING_OFFICIAL_ACT
        op_count = 0
        error = ""
        if official_act_available:
            ops_json = load_se_official_ops_from_archive(archive, sfs_id)
            if ops_json is None:
                try:
                    # Same readonly-archive bridge as above: persist the typed
                    # waists/ops via the mutating path only when the archive
                    # accepts writes; the coverage-scan worker opens the shared
                    # ``sweden.farchive`` readonly.
                    if _se_archive_is_writable(archive):
                        ops_json = compile_se_official_ops_to_archive(archive, sfs_id)
                    else:
                        ops_json = [
                            se_legal_operation_to_dict(op)
                            for op in compile_se_official_act_ops(loaded_act, source_id=sfs_id)
                        ]
                except FileNotFoundError as exc:
                    error = str(exc)
                    ops_status = SeOpsStatus.MISSING_OFFICIAL_ACT
                except NotImplementedError as exc:
                    error = str(exc)
                    ops_status = SeOpsStatus.UNSUPPORTED
                except ValueError as exc:
                    error = str(exc)
                    ops_status = SeOpsStatus.INVALID_OFFICIAL_ACT
            if ops_json is not None:
                op_count = len(ops_json)
                ops_status = SeOpsStatus.COMPILED
        chain_rows.append(
            {
                **item,
                "official_act_available": official_act_available,
                "pdf_available": pdf_available,
                "doc_available": doc_available,
                "ops_status": ops_status,
                "op_count": op_count,
                "error": error,
            }
        )
        if probe_sources and ops_status is SeOpsStatus.MISSING_OFFICIAL_ACT:
            chain_rows[-1]["public_source_probe"] = probe_se_public_source_status(sfs_id)

    compiled_count = sum(1 for item in chain_rows if item["ops_status"] == "compiled")
    missing_count = sum(1 for item in chain_rows if item["ops_status"] == "missing_official_act")
    unsupported_count = sum(1 for item in chain_rows if item["ops_status"] == "unsupported")
    invalid_count = sum(1 for item in chain_rows if item["ops_status"] == "invalid_official_act")
    chain_diagnostics = tuple(
        diagnostic
        for item in chain_rows
        if (diagnostic := _se_rebuild_chain_blocker_diagnostic(item)) is not None
    )
    return {
        "amending_sfs_id": amending_sfs_id,
        "base_sfs_id": resolved_base_sfs_id,
        "effective_date": analysis["effective_date"],
        "pre_date": analysis["pre_date"],
        "recovery_strategy": analysis["recovery_strategy"],
        "base_seed": base_seed,
        "prior_amendment_count": len(chain_rows),
        "compiled_count": compiled_count,
        "missing_official_count": missing_count,
        "unsupported_count": unsupported_count,
        "invalid_count": invalid_count,
        "chain_diagnostics": chain_diagnostics,
        "official_chain_ready": bool(base_seed["official_act_available"])
        and all(item["ops_status"] == "compiled" for item in chain_rows),
        "seed_ready": bool(base_seed["official_base_ir_available"]),
        "rebuild_ready": bool(base_seed["official_base_ir_available"])
        and all(item["ops_status"] == "compiled" for item in chain_rows),
        "chain": chain_rows,
        "note": (
            "official_chain_ready measures source and compiler coverage; "
            "rebuild_ready additionally requires a non-amending base IR seed"
        ),
    }


def rebuild_se_older_base_from_official_chain(
    archive: _ArchiveLike,
    amending_sfs_id: str,
    *,
    base_sfs_id: str | None = None,
    as_of: str | None = None,
    plan: dict[str, Any] | None = None,
) -> IRStatute:
    if plan is None:
        plan = plan_se_older_base_rebuild(
            archive,
            amending_sfs_id,
            base_sfs_id=base_sfs_id,
            as_of=as_of,
        )
    if not bool(plan.get("rebuild_ready")):
        raise NotImplementedError(f"older-base rebuild prerequisites not met for {amending_sfs_id}")

    resolved_base_sfs_id = str(plan.get("base_sfs_id") or base_sfs_id or "")
    base_ir_json = load_se_official_base_ir_from_archive(archive, resolved_base_sfs_id)
    if base_ir_json is None:
        raise FileNotFoundError(f"no archived official base IR for {resolved_base_sfs_id}")
    statute = ir_statute_from_dict(base_ir_json)

    chain_rows = cast(list[Any], plan.get("chain") or [])
    for item in chain_rows:
        sfs_id = str(item.get("sfs_id") or "")
        if str(item.get("ops_status") or "") != "compiled" or not sfs_id:
            raise NotImplementedError(f"older-base chain for {amending_sfs_id} is not fully compiled")
        ops_json = load_se_official_ops_from_archive(archive, sfs_id)
        if ops_json is None:
            # Same readonly-archive bridge as the analyze path uses: persist
            # the typed waists/ops only when the archive accepts writes (CLI
            # compile / hydrate paths). The coverage-scan worker opens the
            # shared ``sweden.farchive`` readonly; an unconditional
            # ``compile_se_official_ops_to_archive`` here would crash with
            # ``sqlite3.OperationalError: attempt to write a readonly database``
            # whenever a chain step's ops cache was missing.
            if _se_archive_is_writable(archive):
                ops_json = compile_se_official_ops_to_archive(archive, sfs_id)
            else:
                act_payload = load_se_official_act_from_archive(archive, sfs_id)
                if act_payload is None:
                    raise FileNotFoundError(
                        f"no archived official act surface for chain step {sfs_id}"
                    )
                ops_json = [
                    se_legal_operation_to_dict(op)
                    for op in compile_se_official_act_ops(act_payload, source_id=sfs_id)
                ]
        statute = apply_se_ops(
            statute,
            [se_legal_operation_from_dict(op) for op in ops_json],
        )

    metadata = dict(statute.metadata)
    metadata["historical_rebuild_for"] = amending_sfs_id
    metadata["historical_rebuild_pre_date"] = str(plan.get("pre_date") or "")
    return IRStatute(
        statute_id=statute.statute_id,
        title=statute.title,
        body=statute.body,
        supplements=list(statute.supplements),
        metadata=metadata,
    )


def analyze_se_official_replay_feasibility(
    archive: _ArchiveLike,
    amending_sfs_id: str,
    *,
    base_sfs_id: str | None = None,
    as_of: str | None = None,
) -> dict[str, Any]:
    """Assess whether archived Sweden sources are sufficient for trusted replay.

    This is a feasibility/evidence function, not the trusted historical path
    itself. Current consolidated text may still be used here as an oracle,
    contamination detector, or temporary recovery aid while older-base rebuild
    infrastructure matures.
    """
    official_act = load_se_official_act_from_archive(archive, amending_sfs_id)
    if official_act is None:
        raise FileNotFoundError(f"no archived official act surface for {amending_sfs_id}")

    resolved_base_sfs_id = base_sfs_id or str(official_act.get("amended_act_sfs_id") or "")
    if not resolved_base_sfs_id:
        resolved_base_sfs_id = _infer_amended_act_sfs_id_from_clause(_coerce_official_act(official_act))
    if not resolved_base_sfs_id:
        raise ValueError(f"could not determine base SFS ID for {amending_sfs_id}")

    current_json = archive.get(se_rk_current_json_locator(resolved_base_sfs_id))
    if current_json is None:
        raise FileNotFoundError(f"no archived RK current JSON for base statute {resolved_base_sfs_id}")

    ops_json = load_se_official_ops_from_archive(archive, amending_sfs_id)
    # Cache-load then refresh-on-miss path. When the cached ops are missing OR
    # detected stale (no source.effective while the official act carries an
    # effective_clause), the canonical effects plan is rebuilt and the persistable
    # waists/ops/adjudications refreshed. Persist only when the archive is
    # writable (CLI compile / probe paths open writable): the read-only scan
    # worker opens the shared Farchive in readonly mode, where an
    # archive.store() would raise `sqlite3.OperationalError: attempt to write a
    # readonly database` (previously crashed coverage-scan).
    def _stale_cache_needs_refresh() -> bool:
        if ops_json is None:
            return True
        if not as_of:
            first_source = (ops_json[0].get("source") or {}) if ops_json else {}
            if not str(first_source.get("effective") or "") and str(official_act.get("effective_clause") or ""):
                return True
        # Ghost-op staleness: a cached ops row may reference a section target
        # that the coerced official_act's provisions do not enumerate — the
        # classic signature of an archaeic cached ops payload built before
        # the parser learned to fold wrapped cross-reference continuations
        # back into their host section (real witness: 2001:416 §31 — the
        # cached INSERT op reference an empty-payload §31 ghost that the
        # runtime-coerced act no longer carries). Detecting this signature
        # and forcing a fresh in-memory compile keeps the replay from
        # applying a ghost INSERT that has no legitimate payload, and the
        # fresh compile now (post-parser-fix) emits only the legitimate
        # REPLACE op for the §11 target named in the enacting clause.
        coerced_act = _coerce_official_act(official_act)
        coerced_provision_labels = {p.label for p in coerced_act.provisions}
        coerced_heading_labels = {h.before_label for h in coerced_act.inserted_headings}
        coerced_appendix_labels = {a.label for a in coerced_act.appendices}
        # Per-op coerced-text lookup: the cached op's payload text (sum of
        # the section IRNode child texts, mirroring how the runtime
        # ``_parse_se_official_provision_payload`` shapes the payload) MUST
        # agree with the coerced official_act's provision text length within
        # a small editorial tail allowance. A materially shorter payload
        # means the cached op was built before the runtime coercion learned
        # to fold wrapped cross-reference continuations back into the host
        # section, so the cached §72 REPLACE carries the truncated half that
        # the parser left before the wrap break (real witness: 2001:606 §72
        # — cached payload child-text-len=148 vs coerced provision text-len=995).
        coerced_provision_text_by_label = {p.label: p.text for p in coerced_act.provisions}
        seen_target_keys: set[tuple[str, str]] = set()
        for cached_op in ops_json:
            target_path = cached_op.get("target", {}).get("path", []) if isinstance(cached_op, dict) else []
            if not target_path:
                continue
            leaf = target_path[-1] if isinstance(target_path, list) and target_path else None
            if not (isinstance(leaf, list) and len(leaf) >= 1):
                continue
            kind, label = str(leaf[0]), str(leaf[-1])
            target_key = (kind, label)
            # Duplicate-target cached ops: the current compiler no longer
            # emits duplicate (kind, label) REPLACE/INSERT ops for a single
            # amending act (the runtime coercion folds duplicate-label
            # provisions into the prior host). Cached archaeic ops built
            # before that fix can carry the same target twice — once for
            # the legitimate provision and once for the wrap-leftover ghost
            # (real witness: 2001:606 §64 — two REPLACE §64 cached ops).
            # Either recompile fresh, or accept that one of the duplicates
            # was a no-op ghost. Either way the staleness check fires here
            # so the cached controller does not silently pick the wrong
            # half of a split-section payload when the duplicate's second
            # occurrence is rendered.
            if target_key in seen_target_keys:
                return True
            seen_target_keys.add(target_key)
            if kind == "section" and label and label not in coerced_provision_labels and label not in coerced_heading_labels:
                return True
            if kind == "appendix" and label and label not in coerced_appendix_labels:
                return True
            # Payload-text length staleness: if the cached op's section IRNode
            # payload carries substantially less text than the coerced
            # provision (more than a small drift threshold), the cached op
            # was built from the pre-coercion truncated text. Recompile so
            # the fresh compile uses the coerced (cross-ref-folded) act's
            # provisions and produces the full-body payload.
            if (
                kind == "section"
                and label in coerced_provision_text_by_label
                and cached_op.get("action") in {"replace", "insert"}
            ):
                cached_text_len = 0
                payload = cached_op.get("payload") if isinstance(cached_op, dict) else None
                if isinstance(payload, dict):
                    cached_text_len = sum(
                        len(str(child.get("text") or ""))
                        for child in payload.get("children", [])
                        if isinstance(child, dict)
                    )
                coerced_text_len = len(coerced_provision_text_by_label[label])
                # Editorially-trimmed trailing whitespace / wrapping could
                # leave a small length offset; flag only a substantial
                # material shortfall (the cached op is missing >25% of the
                # coerced body text, with at least a 50-char absolute gap
                # so a one-character artifact does not trigger a refresh).
                if (
                    coerced_text_len - cached_text_len > 50
                    and cached_text_len < coerced_text_len * 0.75
                ):
                    return True
        return False

    if _stale_cache_needs_refresh():
        if _se_archive_is_writable(archive):
            ops_json = compile_se_official_ops_to_archive(archive, amending_sfs_id)
        else:
            # Read-only path: still answer the question with a freshly compiled
            # in-memory ops set, surfaced as a typed adjudication so the cache-miss
            # is observable downstream rather than silently substitution-curried.
            ops_json = [
                se_legal_operation_to_dict(op)
                for op in compile_se_official_act_ops(official_act, source_id=amending_sfs_id)
            ]

    # After the refresh block ops_json is guaranteed non-None (the only None
    # source is the cache load above; refresh fires whenever the cache yielded
    # None). Narrow explicitly for the type checker and for readers — the
    # downstream `ops` deserialization depends on it.
    assert ops_json is not None

    effective_date = as_of
    if effective_date is None:
        first_source = (ops_json[0].get("source") or {}) if ops_json else {}
        effective_date = str(first_source.get("effective") or "")
    if not effective_date:
        effective_date = _infer_se_effective_date_from_base_register(current_json, amending_sfs_id)
    effective_date_inference_rule = ""
    if not effective_date:
        # Manual-compilation frontier rungs: when an act carries no entry-into-force
        # clause and the base register does not link back to it, prefer the
        # publisher-stated ``published_date`` (i.e. "Utkom från trycket") over the
        # issuance date. Neither is the *legal* default (the Swedish SFS default
        # is publication + 7 days, see SFS 1976:651); the assumption is surfaced
        # as a typed finding so the manual-compilation frontier remains visible
        # rather than silently substituted. ``published_date`` is populated by
        # ``parse_se_official_act_text`` (incl. the legacy ``Utkom från trycket``
        # header path); ``issued_date`` is the conservative lower bound on the
        # same legal fact.
        fallback_date = str(official_act.get("published_date") or "")
        if fallback_date:
            effective_date_inference_rule = "se_official_effective_date_inferred_from_published_date"
        else:
            fallback_date = str(official_act.get("issued_date") or "")
            effective_date_inference_rule = "se_official_effective_date_inferred_from_issued_date"
        if fallback_date:
            effective_date = fallback_date
    if not effective_date:
        raise ValueError(f"could not determine effective date for {amending_sfs_id}")

    try:
        pre_date = (date.fromisoformat(effective_date) - timedelta(days=1)).isoformat()
    except ValueError as exc:
        raise ValueError(f"invalid effective date {effective_date!r}") from exc

    base_current = parse_se_statute(current_json, statute_id=resolved_base_sfs_id)
    pre_statute = materialize_se_statute_as_of(base_current, pre_date)
    ops = [se_legal_operation_from_dict(op) for op in ops_json]
    contamination = _detect_se_current_surface_contamination(
        pre_statute,
        ops,
        amending_sfs_id=amending_sfs_id,
    )
    self_reverse_pre_statute = _reverse_patch_se_self_contamination(pre_statute, ops, contamination)
    self_reverse_residual = _detect_se_current_surface_contamination(
        self_reverse_pre_statute,
        ops,
        amending_sfs_id=amending_sfs_id,
    )
    later_reverse_pre_statute = _reverse_patch_se_available_later_chain(
        archive,
        self_reverse_pre_statute,
        amending_sfs_id,
        self_reverse_residual,
    )
    later_reverse_residual = _detect_se_current_surface_contamination(
        later_reverse_pre_statute,
        ops,
        amending_sfs_id=amending_sfs_id,
    )
    recovery_strategy = _classify_se_historical_recovery_strategy(
        amending_sfs_id,
        contamination,
        self_reverse_residual,
        later_reverse_residual,
    )
    later_chain_hints = _build_se_later_chain_hints(
        archive,
        amending_sfs_id,
        self_reverse_residual,
    )
    recovered_pre_statute = pre_statute
    recovery_mode = "direct"
    if contamination:
        if not self_reverse_residual:
            recovered_pre_statute = self_reverse_pre_statute
            recovery_mode = "self_reverse"
        elif not later_reverse_residual:
            recovered_pre_statute = later_reverse_pre_statute
            recovery_mode = "later_reverse_chain"
    replay_precondition_issues = (
        _detect_se_replay_precondition_issues(recovered_pre_statute, ops) if not later_reverse_residual else []
    )
    replay_precondition_ancestry_hints = _build_se_replay_precondition_ancestry_hints(
        archive,
        current_json,
        amending_sfs_id,
        effective_date,
        replay_precondition_issues,
        later_chain_hints,
    )
    if replay_precondition_issues and any(
        bool(item.get("noninvertible_blocker")) for item in replay_precondition_ancestry_hints
    ):
        recovery_strategy = "older_base_required"
    elif later_reverse_residual and _has_se_noninvertible_placeholder_blocker(
        archive,
        amending_sfs_id,
        later_reverse_residual,
    ):
        recovery_strategy = "older_base_required"
    return {
        "amending_sfs_id": amending_sfs_id,
        "base_sfs_id": resolved_base_sfs_id,
        "effective_date": effective_date,
        "effective_date_inference_rule": effective_date_inference_rule,
        "pre_date": pre_date,
        "op_count": len(ops),
        "ops_json": ops_json,
        "contamination": contamination,
        "replay_feasible": not contamination,
        "self_reverse_feasible": not self_reverse_residual,
        "self_reverse_residual_contamination": self_reverse_residual,
        "later_chain_reverse_feasible": not later_reverse_residual,
        "later_chain_residual_contamination": later_reverse_residual,
        "recovery_mode": recovery_mode,
        "replay_ready": not contamination and not replay_precondition_issues
        if not contamination
        else (not later_reverse_residual and not replay_precondition_issues),
        "replay_precondition_issues": replay_precondition_issues,
        "replay_precondition_ancestry_hints": replay_precondition_ancestry_hints,
        "reverse_patchable_count": sum(
            1 for item in contamination if str(item.get("reverse_patch_candidate") or "") == "yes"
        ),
        "recovery_strategy": recovery_strategy,
        "later_chain_hints": later_chain_hints,
    }


#: Outcome constants surfaced by :func:`check_se_official_replay` so callers
#: can dispatch on a typed field instead of catching ``NotImplementedError``
#: and string-matching the message (the previous control flow was
#: exception-driven; the new flow returns a structured dict).
SE_REPLAY_OUTCOME_REPLAY_FEASIBLE = "replay_feasible"
SE_REPLAY_OUTCOME_OLDER_BASE_REQUIRED = "older_base_required"
SE_REPLAY_OUTCOME_PRECONDITION_ISSUES_BLOCKING = "precondition_issues_blocking"


def _se_replay_unresolved_outcome(
    *,
    amending_sfs_id: str,
    base_sfs_id: str,
    effective_date: str,
    pre_date: str,
    recovery_mode: str,
    outcome: str,
    reason_code: str,
    message: str,
    outcome_detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a structured ``outcome != replay_feasible`` return dict.

    Replaces the previous ``raise NotImplementedError(...)`` control flow at
    the two ``check_se_official_replay`` raise sites. The returned dict carries
    the typed ``outcome`` / ``reason_code`` / ``message`` fields so callers can
    dispatch on the structured signal rather than catching
    :class:`NotImplementedError` and substring-matching the message (which is
    what :func:`scan_se_official_replay_act` did previously).

    The dict also carries empty-default fields for ``rows`` / ``target_count``
    / ``match_count`` so defensive readers that accessed those fields (and
    would otherwise have ``KeyError``'d on the new return shape) keep working.
    """
    return {
        "amending_sfs_id": amending_sfs_id,
        "base_sfs_id": base_sfs_id,
        "effective_date": effective_date,
        "pre_date": pre_date,
        "recovery_mode": recovery_mode,
        "outcome": outcome,
        "reason_code": reason_code,
        "message": message,
        "outcome_detail": dict(outcome_detail or {}),
        # Default-empty fields for defensive readers (the successful-return
        # shape includes these; reproducing them as empty keeps the contract
        # uniform across the two outcomes).
        "rows": [],
        "target_count": 0,
        "match_count": 0,
        "invariant_violations": [],
        "typed_invariant_violations": [],
        "adjudications": [],
        "evidence": {"finding_rows": []},
    }


def check_se_official_replay(
    archive: _ArchiveLike,
    amending_sfs_id: str,
    *,
    base_sfs_id: str | None = None,
    as_of: str | None = None,
) -> dict[str, Any]:
    analysis = analyze_se_official_replay_feasibility(
        archive,
        amending_sfs_id,
        base_sfs_id=base_sfs_id,
        as_of=as_of,
    )
    resolved_base_sfs_id = str(analysis["base_sfs_id"])
    effective_date = str(analysis["effective_date"])
    pre_date = str(analysis["pre_date"])
    official_act = load_se_official_act_from_archive(archive, amending_sfs_id)
    assert official_act is not None
    current_json = archive.get(se_rk_current_json_locator(resolved_base_sfs_id))
    assert current_json is not None
    # Reuse the ops an analyze() load-bearing step already computed (in-memory or
    # cached) instead of re-fetching the archive cache — that was a read-only
    # mismatch when the archive cache was empty (scan path).
    ops_json = analysis.get("ops_json")
    if ops_json is None:
        ops_json = load_se_official_ops_from_archive(archive, amending_sfs_id)
    assert ops_json is not None
    base_current = parse_se_statute(current_json, statute_id=resolved_base_sfs_id)
    pre_statute = materialize_se_statute_as_of(base_current, pre_date)
    post_statute = materialize_se_statute_as_of(base_current, effective_date)
    current_raw_sections = extract_se_current_section_texts(current_json, effective_date)
    # Project the per-act provision / heading / appendix oracle dicts through
    # the typed ``SEOfficialActText`` coercion (`_coerce_official_act`). That
    # path applies runtime repair rules (e.g. folding legacy cached
    # duplicate-label cross-reference continuations into their host section)
    # that the cached ``official_act`` raw dict does not, so the replay-vs-oracle
    # lookup returns the legitimate replacement text even when the archaeic
    # persisted ``official.act.json`` pre-dates the parser fix.
    coerced_act = _coerce_official_act(official_act)
    official_provisions = {
        provision.label: provision.text for provision in coerced_act.provisions
    }
    official_headings = {
        heading.before_label: heading.text for heading in coerced_act.inserted_headings
    }
    official_appendices = {
        appendix.label: " ".join(
            part
            for part in [
                appendix.title.strip(),
                appendix.text.strip(),
            ]
            if part
        )
        for appendix in coerced_act.appendices
    }
    ops = [se_legal_operation_from_dict(op) for op in ops_json]
    contamination = cast(list[Any], analysis["contamination"])
    precondition_issues = cast(list[Any], analysis.get("replay_precondition_issues") or [])
    rebuilt_pre_statute: IRStatute | None = None
    if str(analysis.get("recovery_strategy") or "") == "older_base_required":
        older_base_plan = plan_se_older_base_rebuild(
            archive,
            amending_sfs_id,
            base_sfs_id=resolved_base_sfs_id,
            as_of=effective_date,
        )
        if bool(older_base_plan.get("rebuild_ready")):
            rebuilt_pre_statute = rebuild_se_older_base_from_official_chain(
                archive,
                amending_sfs_id,
                base_sfs_id=resolved_base_sfs_id,
                as_of=effective_date,
                plan=older_base_plan,
            )
            pre_statute = rebuilt_pre_statute
            precondition_issues = _detect_se_replay_precondition_issues(pre_statute, ops)
            contamination = []
    if precondition_issues:
        issues_text = ", ".join(
            f"{item['target_kind']}:{item['label']}:{item['issue']}" for item in precondition_issues
        )
        # The recovered Sweden base still lacks replay targets the amending act
        # needs. Structured ``precondition_issues_blocking`` outcome (previously
        # a NotImplementedError that check_se_official_replay's callers had to
        # catch and string-match -- this return surfaces the structured signal).
        return _se_replay_unresolved_outcome(
            amending_sfs_id=amending_sfs_id,
            base_sfs_id=resolved_base_sfs_id,
            effective_date=effective_date,
            pre_date=pre_date,
            # We only reach this path inside the older_base_required branch of
            # ``analyze_se_official_replay_feasibility`` whose
            # ``recovery_strategy`` is ``"older_base_required"`` — so this
            # outcome carries ``older_base_rebuild`` as its recovery_mode
            # (the rebuilt surface still does not satisfy the preconditions
            # the amending act needs).
            recovery_mode="older_base_rebuild",
            outcome=SE_REPLAY_OUTCOME_PRECONDITION_ISSUES_BLOCKING,
            reason_code="se_replay_recovered_base_lacks_required_targets",
            message=(
                f"recovered Sweden base for {resolved_base_sfs_id} still lacks required replay targets "
                f"for {amending_sfs_id}: {issues_text}"
            ),
            outcome_detail={
                "precondition_issues": list(precondition_issues),
                "recovery_strategy": str(analysis.get("recovery_strategy") or ""),
            },
        )
    replay_base_statute = pre_statute
    comparison_post_statute = post_statute
    recovery_mode = "direct"
    if rebuilt_pre_statute is not None:
        recovery_mode = "older_base_rebuild"
        self_reverse_residual = cast(list[Any], analysis.get("self_reverse_residual_contamination") or [])
        if bool(analysis.get("later_chain_reverse_feasible")) and self_reverse_residual:
            comparison_post_statute = _reverse_patch_se_available_later_chain(
                archive,
                post_statute,
                amending_sfs_id,
                self_reverse_residual,
            )
    elif contamination:
        self_reverse_pre_statute = _reverse_patch_se_self_contamination(pre_statute, ops, contamination)
        self_reverse_residual = _detect_se_current_surface_contamination(
            self_reverse_pre_statute,
            ops,
            amending_sfs_id=amending_sfs_id,
        )
        if not self_reverse_residual:
            replay_base_statute = self_reverse_pre_statute
            recovery_mode = "self_reverse"
        elif bool(analysis.get("later_chain_reverse_feasible")):
            replay_base_statute = _reverse_patch_se_available_later_chain(
                archive,
                self_reverse_pre_statute,
                amending_sfs_id,
                self_reverse_residual,
            )
            comparison_post_statute = _reverse_patch_se_available_later_chain(
                archive,
                post_statute,
                amending_sfs_id,
                self_reverse_residual,
            )
            recovery_mode = "later_reverse_chain"
        else:
            contamination_text = ", ".join(
                f"{item['target_kind']}:{item['label']}:{item['issue']}" for item in contamination
            )
            # The base current surface already carries post-amendment state and
            # no reverse-patching rung (self / later-chain) can clear the residual
            # contamination. Structured ``older_base_required`` outcome
            # (previously a NotImplementedError that check_se_official_replay's
            # callers caught + substring-matched the message to classify the
            # row as ``older_base_required`` -- this return surfaces the
            # structured signal directly, no string matching required).
            return _se_replay_unresolved_outcome(
                amending_sfs_id=amending_sfs_id,
                base_sfs_id=resolved_base_sfs_id,
                effective_date=effective_date,
                pre_date=pre_date,
                # ``recovery_strategy`` here is ``older_base_required`` per the
                # analyze-path's classification (contamination is residual after
                # both the self-reverse and later-chain-reverse rungs were
                # tried). Surface the same value as the failure's recovery_mode
                # so the report lane's recovery_mode text does not regress to
                # the empty ``"direct"`` default the ``else`` branch would
                # otherwise leave bound.
                recovery_mode=str(analysis.get("recovery_strategy") or "older_base_required"),
                outcome=SE_REPLAY_OUTCOME_OLDER_BASE_REQUIRED,
                reason_code="se_replay_base_surface_contains_post_amendment_targets",
                message=(
                    f"base current surface for {resolved_base_sfs_id} already contains post-amendment targets "
                    f"before {effective_date}: {contamination_text}; "
                    "historical replay requires an older base surface or reverse patching"
                ),
                outcome_detail={
                    "contamination": list(contamination),
                    "self_reverse_feasible": bool(analysis.get("self_reverse_feasible")),
                    "later_chain_reverse_feasible": bool(analysis.get("later_chain_reverse_feasible")),
                },
            )
    baseline_invariants = set(se_statute_invariant_violations(replay_base_statute))
    baseline_typed_invariant_messages = {
        violation.message for violation in se_statute_invariant_violation_records(replay_base_statute)
    }
    replay_adjudications: list[CompileAdjudication] = []
    replayed = apply_se_ops(replay_base_statute, ops, adjudications_out=replay_adjudications)
    skipped_op_ids = {item.op_id for item in replay_adjudications if item.op_id}
    finding_rows = adjudication_finding_evidence_rows(
        replay_adjudications,
        frontend_id="sweden",
        base_id=resolved_base_sfs_id,
        as_of=effective_date,
    )

    post_sections = se_section_text_map(comparison_post_statute)
    replay_sections = se_section_text_map(replayed)
    post_headings = se_heading_before_section_map(comparison_post_statute)
    replay_headings = se_heading_before_section_map(replayed)
    post_appendices = se_appendix_text_map(comparison_post_statute)
    replay_appendices = se_appendix_text_map(replayed)
    covered_targets = {
        (
            op.target.leaf_kind(),
            op.target.special or "",
            op.target.leaf_label(),
        )
        for op in ops
        if op.action is not StructuralAction.RENUMBER
    }

    # Read the current-surface oracle's consolidation stamp once so the
    # oracle-fallback rows can be split into honest dating-vs-content buckets.
    current_doc = json.loads(current_json) if isinstance(current_json, (bytes, str)) else current_json
    current_fulltext = current_doc.get("fulltext") if isinstance(current_doc, dict) else None
    oracle_stamp = (
        str(current_fulltext.get("andringInford") or "") or None
        if isinstance(current_fulltext, dict)
        else None
    )
    oracle_stamp_sfs = _se_parse_andring_inford_sfs(oracle_stamp)
    oracle_version_relation = _se_oracle_version_relation(amending_sfs_id, oracle_stamp_sfs)

    def _official_oracle_classification(post_text: str) -> str:
        if not post_text.strip():
            return "official_oracle_match_missing_current_post"
        # The replay reproduced the amendment's own post-state and that state
        # equals the official-act oracle, but it disagrees with the current
        # surface. Distinguish a later-consolidation dating artifact (the current
        # surface is simply a newer version) from a genuine surface drift.
        if oracle_version_relation == "later":
            return "official_oracle_version_mismatch"
        if oracle_version_relation == "unknown":
            return "official_oracle_match_version_unknown"
        return "official_oracle_match_current_surface_drift"

    rows: list[dict[str, Any]] = []
    for op in ops:
        if op.op_id and op.op_id in skipped_op_ids:
            continue
        label = op.target.leaf_label()
        if op.action is StructuralAction.RENUMBER:
            destination_label = op.destination.leaf_label() if op.destination is not None else ""
            destination_key = (op.target.leaf_kind(), op.target.special or "", destination_label)
            if destination_label and destination_key in covered_targets:
                continue
            label = destination_label or label
        if op.target.leaf_kind() == "section" and op.target.special is FacetKind.HEADING:
            replay_text = replay_headings.get(label, "")
            post_text = post_headings.get(label, "")
            official_raw_text = official_headings.get(label, "")
            match = _normalize_compare_text(replay_text) == _normalize_compare_text(post_text)
            classification = "exact" if match else "content_mismatch"
            if (
                not match
                and official_raw_text
                and _normalize_compare_text(replay_text) == _normalize_compare_text(official_raw_text)
            ):
                match = True
                classification = _official_oracle_classification(post_text)
            rows.append(
                {
                    "target_kind": "heading",
                    "section": label,
                    "match": match,
                    "classification": classification,
                    "replay_text": replay_text,
                    "post_text": post_text,
                    "official_text": official_raw_text,
                }
            )
            continue
        if op.target.leaf_kind() == "appendix":
            replay_text = replay_appendices.get(label, "")
            post_text = post_appendices.get(label, "")
            official_raw_text = official_appendices.get(label, "")
            replay_norm = _normalize_appendix_compare_text(replay_text)
            post_norm = _normalize_appendix_compare_text(post_text)
            match = replay_norm == post_norm
            classification = "exact" if match else _classify_replay_row(replay_text, post_text)
            if not match and official_raw_text and replay_norm == _normalize_appendix_compare_text(official_raw_text):
                match = True
                classification = _official_oracle_classification(post_text)
            rows.append(
                {
                    "target_kind": "appendix",
                    "appendix": label,
                    "match": match,
                    "classification": classification,
                    "replay_text": replay_text,
                    "post_text": post_text,
                    "official_text": official_raw_text,
                }
            )
            continue
        replay_text = replay_sections.get(label, "")
        post_text = post_sections.get(label, "")
        current_raw_text = current_raw_sections.get(label, "")
        official_raw_text = official_provisions.get(label, "")
        if "Uppgift lämnas av" in current_raw_text and "Uppgift lämnas av" in official_raw_text:
            replay_canonical = canonicalize_se_table_section_text(official_raw_text)
            post_canonical = canonicalize_se_table_section_text(current_raw_text)
            match = replay_canonical == post_canonical
            classification = "table_rows_match" if match else "table_layout_mismatch"
        elif (
            op.action is StructuralAction.REPEAL
            and (replay_text or "").strip() == ""
            and _is_oracle_repeal_stub(post_text)
        ):
            # Editorial repeal-stub convention: the replay correctly produced an
            # empty post-section (the section was structurally repealed), but
            # the current-text oracle preserves a "Har upphävts genom
            # <förordning|lag> (YEAR:N)." tombstone. The two surfaces agree
            # on the fact of repeal — this is an editorial-stub match, not a
            # genuine content disagreement. Real witness: 2002:12 §17.
            match = True
            classification = "repeal_stub_oracle_only"
        elif (
            op.action is StructuralAction.REPEAL
            and (replay_text or "").strip() == ""
            and (post_text or "").strip() != ""
            and not _is_oracle_repeal_stub(post_text)
            and oracle_version_relation == "later"
        ):
            # Repealed-by-this-act-then-later-readded: the amending act's REPEAL
            # deterministically produced an empty post-section at its own
            # effective date (replay correctly reflects that), but the current
            # oracle is a strictly-later consolidation in which a later
            # amendment re-introduced the section with different content. The
            # replay is provably correct and the current oracle carries a newer
            # time-point version — this is the genuine ``oracle_version_mismatch``
            # bucket without the official-act oracle fallback (the amending act
            # has no replacement text to verify against; the determinism is
            # provided by the REPEAL op + the strictly-later stamp).
            # Real witness: SFS 2001:920 §5 — 2001:920 repealed §5, a later
            # amendment (2007:572, etc.) re-added it with the post text the
            # current consolidation carries.
            match = True
            classification = "repeal_then_later_replaced_oracle_only"
        else:
            match = _normalize_compare_text(replay_text) == _normalize_compare_text(post_text)
            classification = (
                "exact"
                if match and replay_text.strip() == post_text.strip()
                else (
                    _classify_replay_row(replay_text, post_text)
                    if not (match and replay_text.strip() == post_text.strip())
                    else "exact"
                )
            )
        if (
            not match
            and official_raw_text
            and _normalize_compare_text(replay_text) == _normalize_compare_text(official_raw_text)
        ):
            match = True
            classification = _official_oracle_classification(post_text)
        rows.append(
            {
                "target_kind": "section",
                "section": label,
                "match": match,
                "classification": classification,
                "replay_text": replay_text,
                "post_text": post_text,
            }
        )

    return {
        "amending_sfs_id": amending_sfs_id,
        "base_sfs_id": resolved_base_sfs_id,
        "effective_date": effective_date,
        "effective_date_inference_rule": str(analysis.get("effective_date_inference_rule") or ""),
        "pre_date": pre_date,
        "recovery_mode": recovery_mode,
        # Outcome signal (typed replacement for the previous NotImplementedError
        # raise control flow -- the successful path carries the structured
        # ``replay_feasible`` outcome so callers dispatch on the field rather
        # than catching exceptions).
        "outcome": SE_REPLAY_OUTCOME_REPLAY_FEASIBLE,
        "oracle_consolidation_stamp": oracle_stamp or "",
        "oracle_consolidation_sfs_id": oracle_stamp_sfs or "",
        "oracle_version_relation": oracle_version_relation,
        "target_count": len(rows),
        "match_count": sum(1 for row in rows if row["match"]),
        "invariant_violations": [
            violation
            for violation in replayed.metadata.get("invariant_violations", [])
            if violation not in baseline_invariants
        ],
        "typed_invariant_violations": [
            violation.to_dict()
            for violation in se_statute_invariant_violation_records(replayed)
            if violation.message not in baseline_typed_invariant_messages
        ],
        "adjudications": [asdict(item) for item in replay_adjudications],
        "evidence": {
            "finding_rows": [row.to_dict() for row in finding_rows],
            # Typed evidence-plane residuals (§2.10 projection re-derivable
            # from a committed dossier). Each replay row's classification is
            # projected to a content-addressed AgreementResidual carrying
            # family + status + missing_proofs; the residual_id is stable
            # across reruns so a missing or surplus residual between two
            # runs becomes detectable. The CLI/aggregate dict above is the
            # projection; this list IS the evidence-plane dossier it is
            # re-derived FROM.
            "agreement_residuals": [
                r.to_dict()
                for r in se_replay_agreement_residuals(
                    {
                        "amending_sfs_id": amending_sfs_id,
                        "base_sfs_id": resolved_base_sfs_id,
                        "rows": rows,
                    }
                )
            ],
        },
        "rows": rows,
    }


# Row classifications that represent a GENUINE section content match (replay text
# equals the post-amendment current text). Everything else that "matches" only
# does so through editorial/presentation projection or by falling back to the
# official-act oracle — those are NOT genuine content matches and must be
# reported in their own buckets so the agreement number is not flattered.
SE_GENUINE_CONTENT_MATCH_CLASSIFICATIONS = frozenset(
    {"exact", "table_rows_match"}
)
# Classifications that the replay marks as match=True but which are editorial /
# presentation drift, not genuine content equality.
SE_EDITORIAL_MATCH_CLASSIFICATIONS = frozenset(
    {"editorial_attribution_only", "inline_numbering_only", "repeal_stub_oracle_only"}
)
# Classifications that match=True only because replay fell back to the official
# act text as an oracle (the current surface diverged or was missing).
SE_OFFICIAL_ORACLE_MATCH_CLASSIFICATIONS = frozenset(
    {
        "official_oracle_version_mismatch",
        "official_oracle_match_version_unknown",
        "official_oracle_match_current_surface_drift",
        "official_oracle_match_missing_current_post",
    }
)
# The honest sub-split of the oracle-fallback bucket. ``oracle_version_mismatch``
# rows are correct replays measured against a LATER consolidation: the replay
# reproduced the amendment's own post-state, that state equals the official-act
# oracle, and the current surface only disagrees because its consolidation stamp
# ("Ändring införd: t.o.m. SFS YYYY:N") names a strictly later SFS. These are NOT
# content failures and must not be conflated with genuine surface drift.
SE_ORACLE_VERSION_MISMATCH_CLASSIFICATIONS = frozenset(
    {"official_oracle_version_mismatch", "repeal_then_later_replaced_oracle_only"}
)
# Oracle-fallback rows where the consolidation stamp is missing/unparseable, or
# the current surface is absent — the version relation cannot be trusted, so they
# are reported separately rather than counted as either honest match or mismatch.
SE_ORACLE_VERSION_UNKNOWN_CLASSIFICATIONS = frozenset(
    {
        "official_oracle_match_version_unknown",
        "official_oracle_match_missing_current_post",
    }
)
# Oracle-fallback rows whose stamp is contemporaneous with or older than the
# replayed amendment: a real current-surface drift, counted as genuine mismatch.
SE_ORACLE_SURFACE_DRIFT_CLASSIFICATIONS = frozenset(
    {"official_oracle_match_current_surface_drift"}
)

# The three honest headline buckets for the coverage report:
#   genuine_match        — replay text equals the post-amendment current text,
#                          modulo pure editorial/presentation projection.
#   oracle_version_mismatch — correct replay measured against a LATER
#                          consolidation (the current surface is a newer version).
#   genuine_mismatch     — the replay text genuinely disagrees with the
#                          contemporaneous oracle/current surface.
#   unknown              — the version relation cannot be trusted (missing or
#                          unparseable stamp, or no current surface to compare).
SE_THREE_BUCKET_GENUINE_MATCH = "genuine_match"
SE_THREE_BUCKET_ORACLE_VERSION_MISMATCH = "oracle_version_mismatch"
SE_THREE_BUCKET_GENUINE_MISMATCH = "genuine_mismatch"
SE_THREE_BUCKET_UNKNOWN = "unknown"


def se_three_bucket_for_classification(classification: str, *, matched: bool) -> str:
    """Map a per-row replay ``classification`` to its honest headline bucket.

    ``matched`` is the row's ``match`` flag. Genuine content matches and
    editorial-only matches both count as ``genuine_match`` (the latter differ
    only in presentation). The oracle-fallback bucket splits by version relation:
    a strictly-later consolidation stamp is ``oracle_version_mismatch``, a
    contemporaneous/older stamp is a real drift (``genuine_mismatch``), and an
    untrustworthy stamp is ``unknown``. Any non-matching row is ``genuine_mismatch``.
    """
    if matched and classification in SE_GENUINE_CONTENT_MATCH_CLASSIFICATIONS:
        return SE_THREE_BUCKET_GENUINE_MATCH
    if matched and classification in SE_EDITORIAL_MATCH_CLASSIFICATIONS:
        return SE_THREE_BUCKET_GENUINE_MATCH
    if classification in SE_ORACLE_VERSION_MISMATCH_CLASSIFICATIONS:
        return SE_THREE_BUCKET_ORACLE_VERSION_MISMATCH
    if classification in SE_ORACLE_VERSION_UNKNOWN_CLASSIFICATIONS:
        return SE_THREE_BUCKET_UNKNOWN
    # Same-or-earlier oracle drift and every genuine content disagreement.
    return SE_THREE_BUCKET_GENUINE_MISMATCH


def scan_se_official_replay_act(
    archive: _ArchiveLike, amending_sfs_id: str
) -> dict[str, Any]:
    """Run :func:`check_se_official_replay` for one act, classified for aggregation.

    Returns a flat, picklable summary (no IR objects) so it can be produced inside
    a worker process and aggregated in the parent. ``outcome`` is one of
    ``replay_ok`` / ``older_base_required`` / ``error``.
    """
    try:
        result = check_se_official_replay(archive, amending_sfs_id)
    except (FileNotFoundError, ValueError, KeyError, AssertionError) as exc:
        return {
            "amending_sfs_id": amending_sfs_id,
            "outcome": "error",
            "error_type": type(exc).__name__,
            "error_detail": str(exc),
        }
    typed_outcome = str(result.get("outcome") or SE_REPLAY_OUTCOME_REPLAY_FEASIBLE)
    if typed_outcome != SE_REPLAY_OUTCOME_REPLAY_FEASIBLE:
        # The structured ``outcome`` signal from check_se_official_replay
        # carries the typed  older_base_required / precondition_issues_blocking
        # outcome (previously NotImplementedError raises that this scan caught
        # and string-matched). For aggregate-compat the summary's top-level
        # ``outcome`` stays ``"older_base_required"`` (matching the previous
        # report-lane bucket name), and the typed ``reason_code`` /
        # ``outcome_detail`` fields distinguish the two unresolved cases.
        return {
            "amending_sfs_id": amending_sfs_id,
            "outcome": "older_base_required",
            "error_type": "NotImplementedError",  # legacy compat for the
            # ``aggregate_se_official_coverage`` error_examples bucket key —
            # stays "NotImplementedError" so existing report tooling keeps
            # bucketing older_base_required rows in the same lane.
            "error_detail": str(result.get("message") or ""),
            "typed_outcome": typed_outcome,
            "reason_code": str(result.get("reason_code") or ""),
            "outcome_detail": dict(result.get("outcome_detail") or {}),
            "base_sfs_id": str(result.get("base_sfs_id") or ""),
            "effective_date": str(result.get("effective_date") or ""),
            "recovery_mode": str(result.get("recovery_mode") or ""),
        }

    rows = list(result.get("rows") or [])
    classification_counts: dict[str, int] = {}
    genuine_match = 0
    editorial_match = 0
    oracle_match = 0
    # The honest three-bucket split (plus an explicit unknown bucket).
    bucket_genuine_match = 0
    bucket_oracle_version_mismatch = 0
    bucket_genuine_mismatch = 0
    bucket_unknown = 0
    for row in rows:
        classification = str(row.get("classification") or "")
        matched = bool(row.get("match"))
        classification_counts[classification] = classification_counts.get(classification, 0) + 1
        bucket = se_three_bucket_for_classification(classification, matched=matched)
        if bucket == SE_THREE_BUCKET_GENUINE_MATCH:
            bucket_genuine_match += 1
        elif bucket == SE_THREE_BUCKET_ORACLE_VERSION_MISMATCH:
            bucket_oracle_version_mismatch += 1
        elif bucket == SE_THREE_BUCKET_UNKNOWN:
            bucket_unknown += 1
        else:
            bucket_genuine_mismatch += 1
        if not matched:
            continue
        if classification in SE_GENUINE_CONTENT_MATCH_CLASSIFICATIONS:
            genuine_match += 1
        elif classification in SE_EDITORIAL_MATCH_CLASSIFICATIONS:
            editorial_match += 1
        elif classification in SE_OFFICIAL_ORACLE_MATCH_CLASSIFICATIONS:
            oracle_match += 1
    return {
        "amending_sfs_id": amending_sfs_id,
        "base_sfs_id": str(result.get("base_sfs_id") or ""),
        "outcome": "replay_ok",
        "recovery_mode": str(result.get("recovery_mode") or ""),
        "effective_date_inference_rule": str(result.get("effective_date_inference_rule") or ""),
        "oracle_version_relation": str(result.get("oracle_version_relation") or ""),
        "oracle_consolidation_sfs_id": str(result.get("oracle_consolidation_sfs_id") or ""),
        "target_count": int(result.get("target_count") or 0),
        "match_count": int(result.get("match_count") or 0),
        "genuine_content_match_count": genuine_match,
        "editorial_match_count": editorial_match,
        "official_oracle_match_count": oracle_match,
        "bucket_genuine_match_count": bucket_genuine_match,
        "bucket_oracle_version_mismatch_count": bucket_oracle_version_mismatch,
        "bucket_genuine_mismatch_count": bucket_genuine_mismatch,
        "bucket_unknown_count": bucket_unknown,
        "classification_counts": classification_counts,
    }


def aggregate_se_official_coverage(
    act_summaries: list[dict[str, Any]],
) -> dict[str, Any]:
    """Deterministically aggregate per-act replay summaries into a corpus report.

    No clock or randomness: sorting is by SFS id, dictionaries are emitted sorted
    by key. Distinguishes the genuine section-content match rate from the
    editorial-only and official-oracle fallback matches so a flattered
    "agreement" cannot hide behind presentation drift.
    """
    works_scanned = len(act_summaries)
    outcome_counts: dict[str, int] = {}
    classification_counts: dict[str, int] = {}
    total_targets = 0
    total_matches = 0
    genuine_matches = 0
    editorial_matches = 0
    oracle_matches = 0
    bucket_genuine_match = 0
    bucket_oracle_version_mismatch = 0
    bucket_genuine_mismatch = 0
    bucket_unknown = 0
    error_examples: dict[str, list[str]] = {}
    # Per-act typed entries for the committed universe root. Building one entry
    # per scanned act so adding/dropping an act or flipping its outcome
    # materially changes the root (pro-note §6 UniverseSpec — "no hidden
    # universe": the omitted-act signal is detectable on a second run).
    coverage_universe_entries: list[dict[str, Any]] = []
    for summary in act_summaries:
        outcome = str(summary.get("outcome") or "error")
        outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1
        # Build the committed-entry fields BEFORE the outcome branch so a
        # non-replay_ok act still contributes its (sfs_id, outcome, recovery_mode)
        # to the universe — a missing/surplus act is detectable even when no
        # bucket counts exist for it. Raises KeyError if outcome is non-empty
        # and outside the closed set (se_coverage_universe_entry validates).
        if outcome == "replay_ok":
            total_targets += int(summary.get("target_count") or 0)
            total_matches += int(summary.get("match_count") or 0)
            genuine_matches += int(summary.get("genuine_content_match_count") or 0)
            editorial_matches += int(summary.get("editorial_match_count") or 0)
            oracle_matches += int(summary.get("official_oracle_match_count") or 0)
            bucket_genuine_match += int(summary.get("bucket_genuine_match_count") or 0)
            bucket_oracle_version_mismatch += int(summary.get("bucket_oracle_version_mismatch_count") or 0)
            bucket_genuine_mismatch += int(summary.get("bucket_genuine_mismatch_count") or 0)
            bucket_unknown += int(summary.get("bucket_unknown_count") or 0)
            for name, count in (summary.get("classification_counts") or {}).items():
                classification_counts[str(name)] = classification_counts.get(str(name), 0) + int(count)
        else:
            key = str(summary.get("error_type") or outcome)
            bucket = error_examples.setdefault(key, [])
            if len(bucket) < 5:
                bucket.append(str(summary.get("amending_sfs_id") or ""))
        coverage_universe_entries.append(
            se_coverage_universe_entry(
                str(summary.get("amending_sfs_id") or ""),
                base_sfs_id=str(summary.get("base_sfs_id") or ""),
                outcome=outcome,
                bucket_genuine_match_count=int(summary.get("bucket_genuine_match_count") or 0),
                bucket_oracle_version_mismatch_count=int(summary.get("bucket_oracle_version_mismatch_count") or 0),
                bucket_genuine_mismatch_count=int(summary.get("bucket_genuine_mismatch_count") or 0),
                bucket_unknown_count=int(summary.get("bucket_unknown_count") or 0),
                recovery_mode=str(summary.get("recovery_mode") or ""),
            )
        )

    def _rate(numerator: int, denominator: int) -> float:
        return round(numerator / denominator, 4) if denominator else 0.0

    return {
        "works_scanned": works_scanned,
        "outcome_counts": dict(sorted(outcome_counts.items())),
        "replay_ok_count": outcome_counts.get("replay_ok", 0),
        "older_base_required_count": outcome_counts.get("older_base_required", 0),
        "error_count": outcome_counts.get("error", 0),
        "replay_ok_rate": _rate(outcome_counts.get("replay_ok", 0), works_scanned),
        "section_target_count": total_targets,
        "section_match_count": total_matches,
        "section_match_rate": _rate(total_matches, total_targets),
        "genuine_content_match_count": genuine_matches,
        "genuine_content_match_rate": _rate(genuine_matches, total_targets),
        "editorial_only_match_count": editorial_matches,
        "official_oracle_match_count": oracle_matches,
        # The honest three-bucket reframe: genuine_match / oracle_version_mismatch
        # (correct replay vs a later consolidation) / genuine_mismatch, plus an
        # explicit unknown bucket for rows whose version relation is untrustworthy.
        "three_bucket": {
            "genuine_match": bucket_genuine_match,
            "oracle_version_mismatch": bucket_oracle_version_mismatch,
            "genuine_mismatch": bucket_genuine_mismatch,
            "unknown": bucket_unknown,
        },
        "three_bucket_rate": {
            "genuine_match": _rate(bucket_genuine_match, total_targets),
            "oracle_version_mismatch": _rate(bucket_oracle_version_mismatch, total_targets),
            "genuine_mismatch": _rate(bucket_genuine_mismatch, total_targets),
            "unknown": _rate(bucket_unknown, total_targets),
        },
        "classification_counts": dict(sorted(classification_counts.items())),
        "error_examples": {key: error_examples[key] for key in sorted(error_examples)},
        # Committed content-addressed universe root over the per-act entries
        # (pro-note §6 UniverseSpec). Adding/dropping an SFS id, or changing
        # any per-act outcome/bucket, materially changes the root — so a
        # missing or surplus scanned act is re-detectable on a subsequent
        # run. The empty-scan case is a committed empty SetRoot (the v0
        # "declares nothing" omission is committed to, not skipped).
        "coverage_universe_root": se_coverage_universe_root(coverage_universe_entries),
    }


def archive_se_official_artifacts_manifest(
    archive: _ArchiveLike,
    official_artifacts: SEOfficialArtifacts,
) -> None:
    bundle_data = load_se_bundle_from_archive(archive, official_artifacts.sfs_id)
    if bundle_data is None:
        return
    bundle_data["official_artifacts"] = _normalize_jsonable(asdict(official_artifacts))
    source_record = bundle_data.get("source_record")
    if isinstance(source_record, dict):
        source_urls = source_record.get("source_urls")
        if isinstance(source_urls, dict):
            source_urls["official_sfs_doc_url"] = official_artifacts.doc_url
            source_urls["official_sfs_pdf_url"] = official_artifacts.pdf_url
        archive.store(
            se_source_record_locator(official_artifacts.sfs_id),
            _json_bytes(source_record),
            storage_class="json",
        )
    archive.store(
        se_bundle_manifest_locator(official_artifacts.sfs_id),
        _json_bytes(bundle_data),
        storage_class="json",
    )


def archive_se_official_phase_artifacts_manifest(archive: _ArchiveLike, sfs_id: str) -> None:
    bundle_data = load_se_bundle_from_archive(archive, sfs_id)
    if bundle_data is None:
        return
    bundle_data["official_phase_artifacts"] = {
        "clause_surface": se_official_clause_surface_locator(sfs_id),
        "payload_surface": se_official_payload_surface_locator(sfs_id),
        "elaboration": se_official_elaboration_locator(sfs_id),
        "effects_plan": se_official_effects_plan_locator(sfs_id),
        "effects": se_official_ops_locator(sfs_id),
        "effects_adjudications": se_official_ops_adjudications_locator(sfs_id),
    }
    archive.store(
        se_bundle_manifest_locator(sfs_id),
        _json_bytes(bundle_data),
        storage_class="json",
    )


def hydrate_se_bundle_live(
    sfs_id: str,
    archive: _ArchiveLike,
    *,
    pdf_url_override: str | None = None,
    current_max_age_hours: float = _CURRENT_SURFACE_CACHE_HOURS,
    official_max_age_hours: float = _IMMUTABLE_CACHE_HOURS,
    force_reextract: bool = False,
    diagnostics_out: list[dict[str, Any]] | None = None,
) -> Optional[SESourceBundle]:
    current_json = fetch_se_rk_current_json(
        sfs_id,
        archive,
        max_age_hours=current_max_age_hours,
        diagnostics_out=diagnostics_out,
    )
    if current_json is None:
        return None
    bundle = archive_se_source_bundle(current_json, archive)
    official = fetch_se_official_artifacts(
        sfs_id,
        archive,
        max_age_hours=official_max_age_hours,
        force_reextract=force_reextract,
        pdf_url_override=pdf_url_override,
    )
    return attach_official_artifacts_to_bundle(bundle, official)


def _coerce_payload_to_dict(payload: bytes | str | JsonObject) -> JsonObject:
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise ValueError("expected Sweden source document to decode to a JSON object")
    return data

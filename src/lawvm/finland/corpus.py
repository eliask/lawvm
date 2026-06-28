"""Finnish statute corpus access — oracle path, ground-truth text, and metadata.

Pure data-access functions.  No grafter replay logic, no XMLStatute dependency.
Depends on CorpusStore/Farchive and metadata helpers only.
"""

from __future__ import annotations

import importlib
import re
import weakref
from functools import lru_cache
import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Protocol, Set, Tuple, cast

import lxml.etree as etree

from lawvm.core.named_swallow import log_emitter, swallow_call
from lawvm.core.phase_result import Finding
from lawvm.core.xml_parse import parse_corpus_xml
from lawvm.corpus_store import get_corpus_store, CorpusStore, oracle_url, statute_url
from lawvm.finland.consolidated_artifacts import (
    build_consolidated_family_glob,
    ConsolidatedArtifactSelector,
)
from lawvm.finland.consolidated_store import SelectionProvenance
from lawvm.finland.consolidated_store import select_cached_consolidated_path_index
from lawvm.finland.consolidated_store import select_cached_consolidated_artifact_with_info
from lawvm.finland.helpers import _parse_iso_date
from lawvm.finland.oracle_comparison import normalize_finlex_oracle_comparison_text
from lawvm.finland.oracle_versioned_children import strip_prior_wording_sibling

importlib.import_module("lawvm.finland.inline_repeal_stub")
from lawvm.finland.metadata import (
    _amendment_effective_date,
    _amendment_expiry_date,
    _statute_id_sort_key,
)

_SELECTED_CONSOLIDATED_LOCATOR_CACHE: "weakref.WeakKeyDictionary[CorpusStore, dict[tuple[str, str, str, str], tuple[str, SelectionProvenance]]]" = weakref.WeakKeyDictionary()
_CONSOLIDATED_ORACLE_CONTEXT_CACHE: "weakref.WeakKeyDictionary[CorpusStore, dict[tuple[str, str, str, str], ConsolidatedOracleContext]]" = weakref.WeakKeyDictionary()


def _clear_selected_consolidated_locator_cache_for_tests() -> None:
    _SELECTED_CONSOLIDATED_LOCATOR_CACHE.clear()
    _CONSOLIDATED_ORACLE_CONTEXT_CACHE.clear()


def _consolidated_selector_cache_key(
    selector: ConsolidatedArtifactSelector | None,
) -> tuple[str, str, str]:
    effective = selector or ConsolidatedArtifactSelector.latest_cached_editorial()
    mode = effective.mode.value if hasattr(effective.mode, "value") else str(effective.mode)
    date_consolidated = getattr(effective, "date_consolidated", None)
    date_value = date_consolidated.isoformat() if date_consolidated is not None else ""
    return mode, str(getattr(effective, "version_tag", "") or ""), date_value


def _get_amendment_children_map() -> Dict[str, List[str]]:
    """Return cached amendment children mapping.

    Kept as a tiny boundary so cache-only oracle commensurability checks remain
    testable without reaching through an inner import site.
    """
    from lawvm.finland.amendment_index import get_amendment_children

    return get_amendment_children()


def _get_amendment_child_edges_map() -> Dict[str, List[Tuple[str, str]]]:
    """Return cached amendment-child edges with edge-kind metadata."""
    from lawvm.finland.amendment_index import get_amendment_child_edges

    return get_amendment_child_edges()


# ---------------------------------------------------------------------------
# Corpus store singleton
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _get_corpus_store() -> CorpusStore:
    """Singleton corpus store for the process."""
    return get_corpus_store()


@lru_cache(maxsize=1)
def _get_corpus_store_readonly() -> CorpusStore:
    """Readonly singleton corpus store for cache-only/reporting paths."""
    return get_corpus_store(readonly=True)


def get_corpus() -> CorpusStore:
    """Get the singleton corpus store."""
    return _get_corpus_store()


# ---------------------------------------------------------------------------
# Consolidated locator access
# ---------------------------------------------------------------------------


class _ArchiveWithLocators(Protocol):
    def locators(self, pattern: str) -> list[str]: ...


def _archive_from_source(source: object) -> object | None:
    """Return the archive-like object behind a CorpusStore or transparent store."""
    archive = getattr(source, "_archive", None)
    if archive is not None:
        return archive
    return source


def list_cached_consolidated_locators(
    source: object,
    sid: str | None = None,
    *,
    findings_out: Optional[List[Finding]] = None,
) -> list[str]:
    """Return cached consolidated artifact/media locators from a store or archive.

    The underlying ``archive.locators(pattern)`` call may raise across archive
    backends (Farchive, hdsearch, FileNotFoundError-lived caching). Previously
    the swallow returned ``[]`` silently (AGENTS.md §1.10 silent-fallback). Now
    routed through ``lawvm.core.named_swallow.swallow_call`` so a typed
    ``Finding(kind=UNEXPECTED_PHASE_FAILURE)`` is constructed with the offending
    ``pattern`` as ``clause_text`` and the ``sid`` as ``source_artifact``, then
    either appended to ``findings_out`` (when the caller plumbs a sink) or
    raised via ``NamedSwallowNonEmittingSinkError`` — never silently swallowed.
    """
    archive = _archive_from_source(source)
    if archive is None or not hasattr(archive, "locators"):
        return []
    archive_with_locators = cast(_ArchiveWithLocators, archive)
    pattern = build_consolidated_family_glob(sid=sid)
    # When the caller plumbs a findings_out sink, the typed Finding is appended
    # there (audit-trail path). When not, fall back to log_emitter so the
    # swallow is still VISIBLE (stderr WARNING) — never silent (§1.10).
    emit = None if findings_out is not None else log_emitter()
    locators = swallow_call(
        lambda: archive_with_locators.locators(pattern),
        rule_id="fi_corpus_list_cached_consolidated_locators",
        default=[],
        jurisdiction="fi",
        source_artifact=sid or "",
        clause_text=f"glob pattern={pattern}",
        emit=emit,
        findings_out=findings_out,
    )
    return sorted({locator for locator in locators if locator.endswith("/main.xml") or "/media/" in locator})


def list_cached_consolidated_pit_locators(source: object, sid: str) -> list[str]:
    """Return cached versioned consolidated main.xml locators for *sid*."""
    return [
        locator
        for locator in list_cached_consolidated_locators(source, sid)
        if locator.endswith("/main.xml") and "/fin@" in locator
    ]


def list_cached_corrigendum_locators(
    source: object,
    sid: str | None = None,
    filename: str | None = None,
) -> list[str]:
    """Return cached consolidated corrigendum PDF locators."""
    locators = [
        locator
        for locator in list_cached_consolidated_locators(source, sid)
        if "/media/corrigenda/" in locator and locator.endswith(".pdf")
    ]
    if filename:
        locators = [locator for locator in locators if Path(locator).name == filename]
    return locators


# ---------------------------------------------------------------------------
# Oracle path index
# ---------------------------------------------------------------------------


def _latest_consolidated_path_by_statute(corpus: Optional[CorpusStore] = None) -> Dict[str, str]:
    """Build {statute_id -> best oracle path} index from the consolidated corpus.

    Delegates to CorpusStore.oracle_path_index(), which owns canonical
    consolidated-artifact selection for the active backend.
    """
    if corpus is None:
        corpus = _get_corpus_store()
    return corpus.oracle_path_index()


def _is_default_latest_selector(
    selector: ConsolidatedArtifactSelector | None,
) -> bool:
    return selector is None or selector == ConsolidatedArtifactSelector.latest_cached_editorial()


def _selected_consolidated_path_by_statute(
    corpus: Optional[CorpusStore] = None,
    selector: ConsolidatedArtifactSelector | None = None,
) -> Dict[str, str]:
    """Build {statute_id -> selected oracle path} using an explicit selector."""
    if corpus is None:
        corpus = _get_corpus_store()
    if _is_default_latest_selector(selector):
        if not hasattr(corpus, "oracle_path_index"):
            return {}
        return corpus.oracle_path_index()
    assert selector is not None
    archive = getattr(corpus, "_archive", None)
    if archive is not None and hasattr(archive, "locators"):
        return select_cached_consolidated_path_index(archive, selector=selector)
    if not hasattr(corpus, "oracle_path_index"):
        return {}
    return corpus.oracle_path_index(selector=selector)


def _selected_consolidated_locator_and_provenance_for_statute(
    statute_id: str,
    corpus: Optional[CorpusStore] = None,
    selector: ConsolidatedArtifactSelector | None = None,
) -> tuple[str, SelectionProvenance | None]:
    """Return the selected consolidated locator/provenance without a global rescan."""
    if corpus is None:
        corpus = _get_corpus_store()
    archive = getattr(corpus, "_archive", None)
    if archive is not None and hasattr(archive, "locators"):
        mode, version_tag, date_value = _consolidated_selector_cache_key(selector)
        cache_key = (statute_id, mode, version_tag, date_value)
        corpus_cache = _SELECTED_CONSOLIDATED_LOCATOR_CACHE.setdefault(corpus, {})
        cached = corpus_cache.get(cache_key)
        if cached is not None:
            return cached
        artifact, provenance = select_cached_consolidated_artifact_with_info(
            archive,
            statute_id,
            selector=selector,
        )
        locator = artifact.canonical_locator if artifact is not None else ""
        selected = (locator, provenance)
        corpus_cache[cache_key] = selected
        return selected
    return (
        _selected_consolidated_path_by_statute(corpus, selector).get(statute_id, ""),
        None,
    )


def _selected_consolidated_locator_for_statute(
    statute_id: str,
    corpus: Optional[CorpusStore] = None,
    selector: ConsolidatedArtifactSelector | None = None,
) -> str:
    """Return one selected consolidated locator without forcing a global rescan."""
    locator, _provenance = _selected_consolidated_locator_and_provenance_for_statute(
        statute_id,
        corpus,
        selector,
    )
    return locator


def get_oracle_selection_provenance(
    statute_id: str,
    corpus: Optional[CorpusStore] = None,
    selector: ConsolidatedArtifactSelector | None = None,
) -> SelectionProvenance | None:
    """Return the :class:`SelectionProvenance` for *statute_id*'s selected oracle.

    Reuses the same per-statute selection (and its cache) that the oracle-path
    accessors use, so callers can surface tolerance/rejection qualifiers without
    re-running selection or affecting which oracle is chosen.  Returns ``None``
    when no provenance is available (e.g. the non-archive corpus path).
    """
    _locator, provenance = _selected_consolidated_locator_and_provenance_for_statute(
        statute_id,
        corpus,
        selector,
    )
    return provenance


def get_oracle_path(
    statute_id: str,
    corpus: Optional[CorpusStore] = None,
    selector: ConsolidatedArtifactSelector | None = None,
) -> Optional[str]:
    """Return a selected oracle path for *statute_id* within the consolidated corpus.

    Returns None if the statute has no versioned consolidated XML at all.

    If *selector* is omitted, the store's default latest-cached/editorial
    selector is used. Explicit selectors make bench/comparison runs honest:
    callers can ask for one embedded version or one date-consolidated cutoff
    without guessing from the raw path suffix.
    """
    locator = _selected_consolidated_locator_for_statute(statute_id, corpus, selector)
    return locator or None


# ---------------------------------------------------------------------------
# Consolidated version helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConsolidatedOracleContext:
    """Selected consolidated-oracle context for one statute.

    This packages the three values that most comparison/reporting surfaces need:
    - the selected canonical oracle locator
    - the oracle cutoff/editorial date
    - the embedded oracle amendment/version id
    """

    locator: str = ""
    cutoff_date: dt.date | None = None
    oracle_version_amendment_id: str = ""


@dataclass(frozen=True)
class ConsolidatedOracleInspection:
    """Selected consolidated-oracle context plus selector metadata."""

    locator: str = ""
    cutoff_date: dt.date | None = None
    oracle_version_amendment_id: str = ""
    selector_mode: str = ""


def _consolidated_oracle_version_amendment_id(path: str) -> Optional[str]:
    """Extract the amendment statute ID (YYYY/NNN) embedded in a fin@ path."""
    m = re.search(r"/fin@(\d{4})(\d+)/main\.xml$", path)
    if not m:
        return None
    return f"{m.group(1)}/{int(m.group(2))}"


def get_consolidated_oracle_context(
    statute_id: str,
    corpus: Optional[CorpusStore] = None,
    selector: ConsolidatedArtifactSelector | None = None,
) -> ConsolidatedOracleContext:
    """Return the selected consolidated-oracle context for *statute_id*."""
    if corpus is None:
        corpus = _get_corpus_store()
    mode, version_tag, date_value = _consolidated_selector_cache_key(selector)
    cache_key = (statute_id, mode, version_tag, date_value)
    corpus_cache = _CONSOLIDATED_ORACLE_CONTEXT_CACHE.setdefault(corpus, {})
    cached = corpus_cache.get(cache_key)
    if cached is not None:
        return cached
    locator = _selected_consolidated_locator_for_statute(statute_id, corpus, selector)
    oracle_version_amendment_id = _consolidated_oracle_version_amendment_id(locator) if locator else None
    if not locator:
        ctx = ConsolidatedOracleContext(
            locator=locator,
            cutoff_date=None,
            oracle_version_amendment_id=oracle_version_amendment_id or "",
        )
        corpus_cache[cache_key] = ctx
        return ctx
    oracle_bytes = corpus.read_locator(locator)
    if oracle_bytes is None:
        ctx = ConsolidatedOracleContext(
            locator=locator,
            cutoff_date=None,
            oracle_version_amendment_id=oracle_version_amendment_id or "",
        )
        corpus_cache[cache_key] = ctx
        return ctx
    tree = parse_corpus_xml(oracle_bytes)
    if oracle_version_amendment_id is None:
        for el in tree.findall(".//{*}FRBRthis"):
            val = el.get("value", "")
            m = re.search(r"/fin@(\d{4})(\d+)/", val)
            if m:
                oracle_version_amendment_id = f"{m.group(1)}/{int(m.group(2))}"
                break
    cutoff_date = None
    for el in tree.findall(".//{*}FRBRdate"):
        if el.get("name") == "dateConsolidated":
            cutoff_date = _parse_iso_date(el.get("date"))
            break
    ctx = ConsolidatedOracleContext(
        locator=locator,
        cutoff_date=cutoff_date,
        oracle_version_amendment_id=oracle_version_amendment_id or "",
    )
    corpus_cache[cache_key] = ctx
    return ctx


def get_consolidated_oracle_inspection(
    statute_id: str,
    corpus: Optional[CorpusStore] = None,
    selector: ConsolidatedArtifactSelector | None = None,
) -> ConsolidatedOracleInspection:
    """Return the selected consolidated-oracle context plus selector mode."""
    effective_selector = selector or ConsolidatedArtifactSelector.latest_cached_editorial()
    ctx = get_consolidated_oracle_context(statute_id, corpus, effective_selector)
    return ConsolidatedOracleInspection(
        locator=ctx.locator,
        cutoff_date=ctx.cutoff_date,
        oracle_version_amendment_id=ctx.oracle_version_amendment_id,
        selector_mode=effective_selector.mode.value,
    )


def get_consolidated_meta(
    statute_id: str,
    corpus: Optional[CorpusStore] = None,
    selector: ConsolidatedArtifactSelector | None = None,
) -> Tuple[Optional[dt.date], Optional[str]]:
    """Return (cutoff_date, oracle_version_amendment_id) for the consolidated oracle of *statute_id*.

    ``cutoff_date`` is the ``dateConsolidated`` value from the oracle XML, or
    ``None`` if absent.  ``oracle_version_amendment_id`` is the fin@ amendment statute ID
    (e.g. ``'2021/680'``), or ``None`` if no versioned oracle is available.
    """
    ctx = get_consolidated_oracle_context(statute_id, corpus, selector)
    return ctx.cutoff_date, ctx.oracle_version_amendment_id or None


def get_ground_truth_bytes(
    statute_id: str,
    corpus: Optional[CorpusStore] = None,
    pit_version: str = "",
    selector: ConsolidatedArtifactSelector | None = None,
) -> Optional[bytes]:
    """Return the selected consolidated oracle bytes for *statute_id*."""
    if corpus is None:
        corpus = _get_corpus_store()
    if pit_version:
        return _read_oracle_at_pit(statute_id, pit_version, corpus)
    oracle_path = get_oracle_path(statute_id, corpus=corpus, selector=selector)
    return corpus.read_locator(oracle_path) if oracle_path else None


def get_consolidated_oracle_reflected_source_vts_children(
    statute_id: str,
    corpus: Optional[CorpusStore] = None,
    selector: ConsolidatedArtifactSelector | None = None,
) -> set[str]:
    """Return late source-VTS amendments explicitly cited by the selected oracle bytes.

    Finlex occasionally serves consolidated bytes whose embedded ``fin@`` version pin
    is stale even though the body/preface already reflects a later cross-statute
    entry-into-force amendment. When that later amendment is already known in the
    amendment index as ``source_vts_explicit`` and the selected oracle bytes cite
    it directly, replay planning may treat it as part of the effective oracle
    surface instead of silently obeying the stale embedded version pin.
    """
    if corpus is None:
        corpus = _get_corpus_store()
    child_edges = _get_amendment_child_edges_map().get(statute_id, [])
    source_vts_children = {
        amendment_id for amendment_id, edge_kind in child_edges if edge_kind == "source_vts_explicit"
    }
    if not source_vts_children:
        return set()

    oracle_bytes = get_ground_truth_bytes(statute_id, corpus=corpus, selector=selector)
    if oracle_bytes is None:
        return set()
    try:
        tree = parse_corpus_xml(oracle_bytes)
    except etree.XMLSyntaxError:
        return set()

    cited_ids: set[str] = set()
    for ref_el in tree.findall(".//{*}ref"):
        if not _oracle_ref_is_body_surface(ref_el):
            continue
        href = str(ref_el.get("href", "") or "")
        m = re.search(r"/akn/fi/act/statute/(\d{4})/(\d+(?:-\d+)?)$", href)
        if m is not None:
            cited_ids.add(f"{m.group(1)}/{int(m.group(2))}" if "-" not in m.group(2) else f"{m.group(1)}/{m.group(2)}")
        ref_text = " ".join("".join(str(_t) for _t in ref_el.itertext()).split())
        m = re.fullmatch(r"(\d{1,4})/(\d{4})", ref_text)
        if m is not None:
            cited_ids.add(f"{m.group(2)}/{int(m.group(1))}")
    return source_vts_children & cited_ids


_FINLEX_ORIGINAL_VERSION_ATTR = "{http://data.finlex.fi/schema/finlex}originalVersion"
_FINLEX_VERSIONED_EID_SUFFIX_RE = re.compile(r"v(?P<version>\d{8})$")


def _oracle_text_mentions_future_commencement(text: str) -> bool:
    tokens = text.casefold().split()
    return any(left == "tulee" and right == "voimaan" for left, right in zip(tokens, tokens[1:], strict=False))


def _oracle_text_has_full_section_repeal_notice(text: str) -> bool:
    tokens = text.casefold().replace("§", " § ").split()
    for idx, token in enumerate(tokens):
        if token != "§" or idx + 2 >= len(tokens):
            continue
        if tokens[idx + 1] != "on" or tokens[idx + 2] != "kumottu":
            continue
        if idx >= 1 and tokens[idx - 1].isdigit():
            return True
        if idx >= 2 and tokens[idx - 2].isdigit() and tokens[idx - 1].isalpha() and len(tokens[idx - 1]) == 1:
            return True
    return False


def _finlex_original_version_to_statute_id(value: str) -> str:
    token = value.strip().lstrip("@")
    if len(token) < 5 or not token.isdigit():
        return ""
    return f"{token[:4]}/{int(token[4:])}"


def _finlex_section_eid_base(section_el: etree._Element) -> str:
    eid = str(section_el.get("eId") or "").strip()
    if not eid:
        return ""
    return _FINLEX_VERSIONED_EID_SUFFIX_RE.sub("", eid)


def _finlex_eid_version_to_statute_id(el: etree._Element) -> str:
    eid = str(el.get("eId") or "").strip()
    if not eid:
        return ""
    match = _FINLEX_VERSIONED_EID_SUFFIX_RE.search(eid)
    if match is None:
        return ""
    return _finlex_original_version_to_statute_id(match.group("version"))


def _finlex_el_has_versioned_eid(el: etree._Element) -> bool:
    eid = str(el.get("eId") or "").strip()
    return bool(eid and _FINLEX_VERSIONED_EID_SUFFIX_RE.search(eid))


def get_consolidated_oracle_reflected_section_original_versions(
    statute_id: str,
    corpus: Optional[CorpusStore] = None,
    selector: ConsolidatedArtifactSelector | None = None,
) -> set[str]:
    """Return source ids reflected by section/provision version markers.

    This is a narrow oracle-surface witness for consolidated bodies whose
    ``dateConsolidated`` lags behind the provision text actually present in the
    selected artifact.  We use section-level ``finlex:originalVersion`` and the
    equivalent Finlex versioned ``eId`` suffix on subsections inside an already
    versioned section.  The subsection case is needed for mixed-section bodies
    such as one subsection from a delayed amendment while the surrounding
    section remains otherwise sourced to an older version.  Future-repeal
    overlay sections ("tulee voimaan") are ignored because those are editorial
    notices for a future state rather than current body materialization.
    """
    if corpus is None:
        corpus = _get_corpus_store()
    oracle_bytes = get_ground_truth_bytes(statute_id, corpus=corpus, selector=selector)
    if oracle_bytes is None:
        return set()
    try:
        tree = parse_corpus_xml(oracle_bytes)
    except etree.XMLSyntaxError:
        return set()

    sections = tuple(tree.findall(".//{*}section"))
    current_section_eid_bases = {
        base
        for section_el in sections
        if not str(section_el.get(_FINLEX_ORIGINAL_VERSION_ATTR) or "")
        for base in (_finlex_section_eid_base(section_el),)
        if base
    }

    reflected: set[str] = set()
    for section_el in sections:
        original_version = str(section_el.get(_FINLEX_ORIGINAL_VERSION_ATTR) or "")
        section_eid_base = _finlex_section_eid_base(section_el)
        section_text = " ".join("".join(str(part) for part in section_el.itertext()).split())
        if _oracle_text_mentions_future_commencement(section_text):
            continue
        if original_version:
            if section_eid_base and section_eid_base in current_section_eid_bases:
                continue
            statute_id_from_version = _finlex_original_version_to_statute_id(original_version)
            if statute_id_from_version:
                reflected.add(statute_id_from_version)
        if _finlex_el_has_versioned_eid(section_el):
            for provision_el in section_el.findall(".//{*}subsection"):
                statute_id_from_eid = _finlex_eid_version_to_statute_id(provision_el)
                if statute_id_from_eid:
                    reflected.add(statute_id_from_eid)
    return reflected


def get_consolidated_oracle_single_future_repeal_overlay_versions(
    statute_id: str,
    corpus: Optional[CorpusStore] = None,
    selector: ConsolidatedArtifactSelector | None = None,
) -> set[str]:
    """Return source ids witnessed by exactly one Finlex future-repeal overlay section."""
    if corpus is None:
        corpus = _get_corpus_store()
    oracle_bytes = get_ground_truth_bytes(statute_id, corpus=corpus, selector=selector)
    if oracle_bytes is None:
        return set()
    try:
        tree = parse_corpus_xml(oracle_bytes)
    except etree.XMLSyntaxError:
        return set()

    overlay_counts: dict[str, int] = {}
    for section_el in tree.findall(".//{*}section"):
        section_text = " ".join("".join(str(part) for part in section_el.itertext()).split())
        if not _oracle_text_has_full_section_repeal_notice(section_text):
            continue
        if not _oracle_text_mentions_future_commencement(section_text):
            continue
        if "Aiempi sanamuoto kuuluu" not in section_text:
            continue
        section_sources: set[str] = set()
        statute_id_from_version = _finlex_eid_version_to_statute_id(section_el)
        if statute_id_from_version:
            section_sources.add(statute_id_from_version)
        original_version = str(section_el.get(_FINLEX_ORIGINAL_VERSION_ATTR) or "")
        statute_id_from_original_version = _finlex_original_version_to_statute_id(original_version)
        if statute_id_from_original_version:
            section_sources.add(statute_id_from_original_version)
        for source_id in section_sources:
            overlay_counts[source_id] = overlay_counts.get(source_id, 0) + 1
    return {source_id for source_id, count in overlay_counts.items() if count == 1}


_ORACLE_REF_METADATA_ANCESTOR_TAGS = frozenset(
    {
        "meta",
        "proprietary",
        "amendedBy",
        "corrigenda",
        "corrigendum",
    }
)


def _oracle_ref_is_body_surface(ref_el: etree._Element) -> bool:
    """Return True when an oracle source ref is body/preface evidence.

    ``source_vts_explicit`` readmission exists for stale embedded version pins
    where the selected consolidated text already reflects a later VTS amendment.
    A ref under AKN metadata only proves amendment-history/corrigendum citation,
    not that the body has been materialized at that later effective date.
    """
    current: etree._Element | None = ref_el
    while current is not None:
        tag = str(current.tag).split("}")[-1]
        if tag in _ORACLE_REF_METADATA_ANCESTOR_TAGS:
            return False
        current = current.getparent()
    return True


def _oracle_pending_amendment_suspect(
    oracle_tree: etree._Element,
    cutoff_date: dt.date,
) -> Optional[str]:
    """Return a suspect string if the oracle has an amendedBy entry whose inForce
    date is strictly after the oracle's cutoff_date.

    The backend no longer treats unversioned consolidated locators as
    authoritative, so a missing version pin now means the oracle is absent
    rather than a special case.
    """
    for inforce_el in oracle_tree.findall(".//{*}dateEntryIntoForce"):
        date_str = inforce_el.get("date", "")
        if not date_str:
            continue
        entry_date = _parse_iso_date(date_str)
        if entry_date is not None and entry_date > cutoff_date:
            # Suppress small gaps — Finlex often publishes metadata before
            # the amendment's effective date (see heuristic 1 comment).
            gap_days = (entry_date - cutoff_date).days
            if gap_days <= 180:
                continue
            # Walk up to the statuteReference element to find the sibling ref element.
            # Structure: amendedBy > statuteReference > [ref, inForce > dateEntryIntoForce]
            ref_el = inforce_el.find("../../{*}ref")
            ref_text = ""
            if ref_el is not None:
                href = ref_el.get("href", "")
                m = re.search(r"/statute/(\d{4})/(\d+)", href)
                if m:
                    ref_text = f"{m.group(1)}/{int(m.group(2))}"
                else:
                    ref_text = ref_el.text or href
            return f"pending: {ref_text} eff {entry_date.isoformat()} > cutoff {cutoff_date.isoformat()}"
    return None


def get_consolidated_oracle_suspect(
    statute_id: str,
    corpus: Optional[CorpusStore] = None,
    selector: ConsolidatedArtifactSelector | None = None,
) -> Optional[str]:
    """Flag likely Finlex-oracle PIT inconsistencies.

    Heuristic 1 (versioned oracle): read the consolidated artifact version id
    (`fin@YYYYNNN`) and compare that amendment statute's own effective date against
    the consolidated file's `dateConsolidated`. If the referenced amendment enters
    into force later than the stated cutoff, then Finlex is likely using a different
    editorial convention from strict PIT replay, or the upstream data is inconsistent.
    `2011/171` is the recurring motivating example for keeping this as explicit
    metadata instead of silently baking the mismatch into replay semantics.

    If the selected oracle artifact has no readable amendment-id pin, the
    consolidated metadata is insufficient for a commensurability judgment and
    this helper returns ``None`` instead of fabricating a base-oracle mode.
    """
    if corpus is None:
        corpus = _get_corpus_store()
    cutoff_date, oracle_version_amendment_id = get_consolidated_meta(
        statute_id,
        corpus,
        selector or ConsolidatedArtifactSelector.latest_cached_editorial(),
    )
    if cutoff_date is None:
        return None
    if not oracle_version_amendment_id:
        return None
    try:
        xml_bytes = corpus.read_source(oracle_version_amendment_id)
        if xml_bytes is None:
            return None
        tree = parse_corpus_xml(xml_bytes)
    except KeyError, FileNotFoundError:
        return None
    eff_date = _amendment_effective_date(tree)
    if eff_date is not None and eff_date > cutoff_date:
        return f"{oracle_version_amendment_id} eff {eff_date.isoformat()} > cutoff {cutoff_date.isoformat()}"
    expiry_date = _amendment_expiry_date(tree)
    if expiry_date is not None and expiry_date < cutoff_date:
        return f"{oracle_version_amendment_id} expires {expiry_date.isoformat()} < cutoff {cutoff_date.isoformat()}"
    return None


def get_consolidated_oracle_suspect_cache_only(
    statute_id: str,
    corpus: Optional[CorpusStore] = None,
) -> Tuple[str, str]:
    """Return (suspect_detail, pending_detail) using cached artifacts only.

    This is the cache-only commensurability gate used by tooling such as
    `bench-curate` and `frontier`: it should not trigger new network fetches.
    """
    if corpus is None:
        try:
            corpus = _get_corpus_store_readonly()
        except OSError, RuntimeError:
            return "", ""

    archive = getattr(corpus, "_archive", None)
    if archive is not None and hasattr(archive, "locators"):
        path = _selected_consolidated_locator_for_statute(
            statute_id,
            corpus,
            ConsolidatedArtifactSelector.latest_cached_editorial(),
        )
    else:
        oracle_index = corpus.oracle_path_index(
            selector=ConsolidatedArtifactSelector.latest_cached_editorial(),
        )
        path = oracle_index.get(statute_id, "")
    if not path:
        return "", ""

    oracle_bytes = corpus.read_locator(path)
    if oracle_bytes is None:
        return "", ""
    try:
        tree = parse_corpus_xml(oracle_bytes)
    except etree.XMLSyntaxError:
        return "", ""

    oracle_version_amendment_id = _consolidated_oracle_version_amendment_id(path)
    if oracle_version_amendment_id is None:
        for el in tree.findall(".//{*}FRBRthis"):
            val = el.get("value", "")
            m = re.search(r"/fin@(\d{4})(\d+)/", val)
            if m:
                oracle_version_amendment_id = f"{m.group(1)}/{int(m.group(2))}"
                break

    cutoff_date = None
    for el in tree.findall(".//{*}FRBRdate"):
        if el.get("name") == "dateConsolidated":
            cutoff_date = _parse_iso_date(el.get("date"))
            break

    if cutoff_date is None or not oracle_version_amendment_id:
        if cutoff_date is None:
            return "", ""

        children = sorted(
            _get_amendment_children_map().get(statute_id, ()),
            key=_statute_id_sort_key,
        )
        if not children:
            return "", ""

        first_pending_detail = ""
        for mid in children:
            source_url = statute_url(mid)
            xml_bytes = corpus.read_locator(source_url)
            if xml_bytes is None:
                if not first_pending_detail:
                    first_pending_detail = f"oracle_missing_version_pin_amendment_uncached:{mid}"
                continue
            try:
                source_tree = parse_corpus_xml(xml_bytes)
            except etree.XMLSyntaxError:
                if not first_pending_detail:
                    first_pending_detail = f"oracle_missing_version_pin_amendment_unparseable:{mid}"
                continue
            eff_date = _amendment_effective_date(source_tree)
            if eff_date is not None and eff_date <= cutoff_date:
                return (
                    f"oracle_missing_version_pin despite amendment {mid} eff "
                    f"{eff_date.isoformat()} <= cutoff {cutoff_date.isoformat()}",
                    "",
                )
        if first_pending_detail:
            return "", first_pending_detail
        return "", ""

    source_url = statute_url(oracle_version_amendment_id)
    xml_bytes = corpus.read_locator(source_url)
    if xml_bytes is None:
        return "", f"oracle_version_amendment_id_source_uncached:{oracle_version_amendment_id}"

    try:
        source_tree = parse_corpus_xml(xml_bytes)
    except etree.XMLSyntaxError:
        return "", f"oracle_version_amendment_id_source_unparseable:{oracle_version_amendment_id}"

    eff_date = _amendment_effective_date(source_tree)
    if eff_date is not None and eff_date > cutoff_date:
        return f"{oracle_version_amendment_id} eff {eff_date.isoformat()} > cutoff {cutoff_date.isoformat()}", ""
    expiry_date = _amendment_expiry_date(source_tree)
    if expiry_date is not None and expiry_date < cutoff_date:
        return f"{oracle_version_amendment_id} expires {expiry_date.isoformat()} < cutoff {cutoff_date.isoformat()}", ""
    return "", ""


def _oracle_mode_sort_key(statute_id: str) -> Tuple[int, int, str]:
    """Sort key for oracle-mode ordering (delegates to _statute_id_sort_key)."""
    return _statute_id_sort_key(statute_id)


# ---------------------------------------------------------------------------
# Oracle version label
# ---------------------------------------------------------------------------


def _oracle_version_label(path: str) -> str:
    """Return a human-readable label for the oracle version embedded in *path*.

    Examples:
        'akn/.../fin@20210680/main.xml'  -> 'fin@20210680 (PIT: 680/2021)'
        'akn/.../fin@YYYYNNNN/main.xml'  -> 'fin@YYYYNNNN (PIT: NNNN/YYYY)'
    """
    m = re.search(r"/fin@(\d{4})(\d+)/main\.xml$", path)
    if m:
        year, num = m.group(1), int(m.group(2))
        return f"fin@{m.group(1)}{m.group(2)} (PIT: {num}/{year})"
    return "fin@ (unknown)"


# ---------------------------------------------------------------------------
# Ground-truth text and tree
# ---------------------------------------------------------------------------


def _read_oracle_at_pit(
    statute_id: str,
    pit_version: str,
    corpus: CorpusStore,
) -> Optional[bytes]:
    """Read a specific PIT version from the archive."""
    locator = oracle_url(statute_id, version=pit_version)
    data = corpus.read_locator(locator)
    return data


def _strip_editorial_note_containers(root: "etree._Element") -> None:
    """Strip authorial/editorial note containers and elements from an oracle tree.

    Removes hcontainer/block/container with name in the usual set (noteAuthorial,
    signatures, ...) and any <authorialNote> elements. This is the canonical
    "strip first" for oracle XML/HTML markup that defines authorial notes etc.,
    so comparison paths (lev, structural, semantic diff) never see the note text
    as if it were provision content.

    Called from get_ground_truth, get_ground_truth_tree, and mirrored in
    section_keys._normalize_oracle_section for per-section clones.
    """
    from typing import cast, List

    _STRIP_NAMES = (
        "amendmentEntryIntoForceAndApplianceProvisions",
        "noteAuthorial",
        "signatures",
        "conclusions",
        "attachments",
    )
    for name in _STRIP_NAMES:
        for tag in ("hcontainer", "block", "container"):
            for el in cast(List["etree._Element"], root.xpath(f'//*[local-name()="{tag}" and @name="{name}"]')):
                strip_prior_wording_sibling(el)
                parent = el.getparent()
                if parent is not None:
                    parent.remove(el)
    for note in cast(List["etree._Element"], root.xpath(".//*[local-name()='authorialNote']")):
        parent = note.getparent()
        if parent is not None:
            parent.remove(note)


def get_ground_truth(
    statute_id: str,
    corpus: Optional[CorpusStore] = None,
    pit_version: str = "",
    selector: ConsolidatedArtifactSelector | None = None,
) -> str:
    """Return serialized body text of consolidated law, stripping voimaantulo footer.

    The consolidated AKN XML appends <hcontainer name="amendmentEntryIntoForceAndApplianceProvisions">
    to the body — this synthesizes all amendments' entry-into-force provisions and is NOT produced
    by our replay engine. Strip it so similarity metrics focus on actual law content.

    If *pit_version* is given (e.g. "20251018"), reads that exact PIT from the
    archive.  Otherwise uses the explicit consolidated selector (or the current
    default latest-cached/editorial selector when no selector is provided).
    """
    oracle_bytes = get_ground_truth_bytes(
        statute_id,
        corpus=corpus,
        pit_version=pit_version,
        selector=selector,
    )
    if oracle_bytes is None:
        return ""
    tree = parse_corpus_xml(oracle_bytes)
    body = tree.find(".//{*}body")
    root = body if body is not None else tree
    _strip_editorial_note_containers(root)
    # Strip historical duplicates that Finlex keeps for version history.
    # Sections: deduplicate by <num> text. Subsections/paragraphs use the
    # shared versioned-child helper, which preserves genuinely distinct same-slot
    # siblings such as 2012/316 sec_1/subsec_1v20150795 + subsec_1v20240859.
    from lawvm.finland.oracle_versioned_children import dedup_versioned_children

    def _norm_num(t: str | None) -> str:
        return re.sub(r"\s+", " ", (t or "").replace("\xa0", " ")).strip()

    def _dedup_children(parent, child_tag: str, key_fn):
        """Remove duplicate children of `child_tag`, keeping first by key_fn."""
        seen: Set[object] = set()
        for el in list(parent):
            if el.tag.split("}")[-1] != child_tag:
                continue
            key = key_fn(el)
            if key is None:
                continue
            if key in seen:
                parent.remove(el)
            else:
                seen.add(key)

    for parent in cast(
        List[etree._Element],
        root.xpath(
            './/*[local-name()="hcontainer"]'
            ' | .//*[local-name()="body"]'
            ' | .//*[local-name()="chapter"]'
            ' | .//*[local-name()="part"]'
            ' | .//*[local-name()="title"]'
        ),
    ):
        _dedup_children(
            parent,
            "section",
            lambda el: _norm_num((el.find("{*}num").text if el.find("{*}num") is not None else "")) or None,
        )
    for sec in root.findall(".//{*}section"):
        dedup_versioned_children(sec, "subsection")
        for sub in sec.findall("{*}subsection"):
            dedup_versioned_children(sub, "paragraph")
    text = etree.tostring(root, method="text", encoding="unicode").strip()
    # Strip consolidated-only annotations before scoring.
    return normalize_finlex_oracle_comparison_text(text)


def get_ground_truth_tree(
    statute_id: str,
    corpus: Optional[CorpusStore] = None,
    selector: ConsolidatedArtifactSelector | None = None,
) -> Optional["etree._Element"]:
    """Return the oracle body element for *statute_id*, or None if absent.

    Uses the explicit consolidated selector (or the current default latest-
    cached/editorial selector when no selector is provided).

    Editorial containers (noteAuthorial/huomautus blocks, signatures, etc.) and
    <authorialNote> elements are stripped first so that downstream structural
    comparison, semantic projection, and section extraction never see authorial
    notes or other non-law markup as content.
    """
    oracle_bytes = get_ground_truth_bytes(statute_id, corpus=corpus, selector=selector)
    if oracle_bytes is None:
        return None
    tree = parse_corpus_xml(oracle_bytes)
    body = tree.find(".//{*}body")
    root = body if body is not None else tree
    _strip_editorial_note_containers(root)
    return body if body is not None else tree

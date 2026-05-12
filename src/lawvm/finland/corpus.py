"""Finnish statute corpus access — oracle path, ground-truth text, and metadata.

Pure data-access functions.  No grafter replay logic, no XMLStatute dependency.
Depends on CorpusStore/Farchive and metadata helpers only.
"""
from __future__ import annotations

import re
from functools import lru_cache
import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, cast

import lxml.etree as etree

from lawvm.corpus_store import get_corpus_store, CorpusStore, oracle_url, statute_url
from lawvm.finland.consolidated_artifacts import (
    build_consolidated_family_glob,
    ConsolidatedArtifactSelector,
)
from lawvm.finland.consolidated_store import select_cached_consolidated_path_index
from lawvm.finland.consolidated_store import select_cached_consolidated_artifact
from lawvm.finland.helpers import _parse_iso_date
from lawvm.tools.editorial_hygiene import normalize_finlex_oracle_comparison_text
import lawvm.finland.inline_repeal_stub as _fi_stub_register  # noqa: F401 — registers detector
from lawvm.finland.metadata import (
    _amendment_effective_date,
    _amendment_expiry_date,
    _statute_id_sort_key,
)


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

def _archive_from_source(source: object) -> object | None:
    """Return the archive-like object behind a CorpusStore or transparent store."""
    archive = getattr(source, "_archive", None)
    if archive is not None:
        return archive
    return source


def list_cached_consolidated_locators(source: object, sid: str | None = None) -> list[str]:
    """Return cached consolidated artifact/media locators from a store or archive."""
    archive = _archive_from_source(source)
    if archive is None or not hasattr(archive, "locators"):
        return []
    pattern = build_consolidated_family_glob(sid=sid)
    try:
        locators = getattr(archive, "locators")(pattern)
    except Exception:
        return []
    return sorted(
        {
            locator
            for locator in locators
            if locator.endswith("/main.xml") or "/media/" in locator
        }
    )


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
        return corpus.oracle_path_index()
    assert selector is not None
    archive = getattr(corpus, "_archive", None)
    if archive is not None and hasattr(archive, "locators"):
        return select_cached_consolidated_path_index(archive, selector=selector)
    return corpus.oracle_path_index(selector=selector)


def _selected_consolidated_locator_for_statute(
    statute_id: str,
    corpus: Optional[CorpusStore] = None,
    selector: ConsolidatedArtifactSelector | None = None,
) -> str:
    """Return one selected consolidated locator without forcing a global rescan."""
    if corpus is None:
        corpus = _get_corpus_store()
    archive = getattr(corpus, "_archive", None)
    if archive is not None and hasattr(archive, "locators"):
        artifact = select_cached_consolidated_artifact(
            archive,
            statute_id,
            selector=selector,
        )
        return artifact.canonical_locator if artifact is not None else ""
    return _selected_consolidated_path_by_statute(corpus, selector).get(statute_id, "")


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
    m = re.search(r'/fin@(\d{4})(\d+)/main\.xml$', path)
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
    locator = _selected_consolidated_locator_for_statute(statute_id, corpus, selector)
    oracle_version_amendment_id = (
        _consolidated_oracle_version_amendment_id(locator) if locator else None
    )
    if not locator:
        return ConsolidatedOracleContext(
            locator=locator,
            cutoff_date=None,
            oracle_version_amendment_id=oracle_version_amendment_id or "",
        )
    oracle_bytes = corpus.read_locator(locator)
    if oracle_bytes is None:
        return ConsolidatedOracleContext(
            locator=locator,
            cutoff_date=None,
            oracle_version_amendment_id=oracle_version_amendment_id or "",
        )
    tree = etree.fromstring(oracle_bytes)
    if oracle_version_amendment_id is None:
        for el in tree.findall('.//{*}FRBRthis'):
            val = el.get('value', '')
            m = re.search(r'/fin@(\d{4})(\d+)/', val)
            if m:
                oracle_version_amendment_id = f"{m.group(1)}/{int(m.group(2))}"
                break
    cutoff_date = None
    for el in tree.findall('.//{*}FRBRdate'):
        if el.get('name') == 'dateConsolidated':
            cutoff_date = _parse_iso_date(el.get('date'))
            break
    return ConsolidatedOracleContext(
        locator=locator,
        cutoff_date=cutoff_date,
        oracle_version_amendment_id=oracle_version_amendment_id or "",
    )


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
        amendment_id
        for amendment_id, edge_kind in child_edges
        if edge_kind == "source_vts_explicit"
    }
    if not source_vts_children:
        return set()

    oracle_bytes = get_ground_truth_bytes(statute_id, corpus=corpus, selector=selector)
    if oracle_bytes is None:
        return set()
    try:
        tree = etree.fromstring(oracle_bytes)
    except etree.XMLSyntaxError:
        return set()

    cited_ids: set[str] = set()
    for ref_el in tree.findall('.//{*}ref'):
        href = str(ref_el.get("href", "") or "")
        m = re.search(r"/akn/fi/act/statute/(\d{4})/(\d+(?:-\d+)?)$", href)
        if m is not None:
            cited_ids.add(f"{m.group(1)}/{int(m.group(2))}" if "-" not in m.group(2) else f"{m.group(1)}/{m.group(2)}")
        ref_text = " ".join("".join(str(_t) for _t in ref_el.itertext()).split())
        m = re.fullmatch(r"(\d{1,4})/(\d{4})", ref_text)
        if m is not None:
            cited_ids.add(f"{m.group(2)}/{int(m.group(1))}")
    return source_vts_children & cited_ids


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
    for inforce_el in oracle_tree.findall('.//{*}dateEntryIntoForce'):
        date_str = inforce_el.get('date', '')
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
            ref_el = inforce_el.find('../../{*}ref')
            ref_text = ""
            if ref_el is not None:
                href = ref_el.get('href', '')
                m = re.search(r'/statute/(\d{4})/(\d+)', href)
                if m:
                    ref_text = f"{m.group(1)}/{int(m.group(2))}"
                else:
                    ref_text = ref_el.text or href
            return (
                f"pending: {ref_text} eff {entry_date.isoformat()}"
                f" > cutoff {cutoff_date.isoformat()}"
            )
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
        tree = etree.fromstring(xml_bytes)
    except (KeyError, FileNotFoundError):
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
        except (OSError, RuntimeError):
            return "", ""

    archive = getattr(corpus, "_archive", None)
    if archive is not None and hasattr(archive, "locators"):
        artifact = select_cached_consolidated_artifact(archive, statute_id)
        path = artifact.canonical_locator if artifact is not None else ""
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
        tree = etree.fromstring(oracle_bytes)
    except etree.XMLSyntaxError:
        return "", ""

    oracle_version_amendment_id = _consolidated_oracle_version_amendment_id(path)
    if oracle_version_amendment_id is None:
        for el in tree.findall('.//{*}FRBRthis'):
            val = el.get('value', '')
            m = re.search(r'/fin@(\d{4})(\d+)/', val)
            if m:
                oracle_version_amendment_id = f"{m.group(1)}/{int(m.group(2))}"
                break

    cutoff_date = None
    for el in tree.findall('.//{*}FRBRdate'):
        if el.get('name') == 'dateConsolidated':
            cutoff_date = _parse_iso_date(el.get('date'))
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

        first_uncached = ""
        for mid in children:
            source_url = statute_url(mid)
            xml_bytes = corpus.read_locator(source_url)
            if xml_bytes is None:
                if not first_uncached:
                    first_uncached = mid
                continue
            try:
                source_tree = etree.fromstring(xml_bytes)
            except etree.XMLSyntaxError:
                continue
            eff_date = _amendment_effective_date(source_tree)
            if eff_date is not None and eff_date <= cutoff_date:
                return (
                    f"oracle_missing_version_pin despite amendment {mid} eff "
                    f"{eff_date.isoformat()} <= cutoff {cutoff_date.isoformat()}",
                    "",
                )
        if first_uncached:
            return "", f"oracle_missing_version_pin_amendment_uncached:{first_uncached}"
        return "", ""

    source_url = statute_url(oracle_version_amendment_id)
    xml_bytes = corpus.read_locator(source_url)
    if xml_bytes is None:
        return "", f"oracle_version_amendment_id_source_uncached:{oracle_version_amendment_id}"

    try:
        source_tree = etree.fromstring(xml_bytes)
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
    m = re.search(r'/fin@(\d{4})(\d+)/main\.xml$', path)
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
    tree = etree.fromstring(oracle_bytes)
    body = tree.find(".//{*}body")
    root = body if body is not None else tree
    _STRIP_NAMES = (
        'amendmentEntryIntoForceAndApplianceProvisions',
        'noteAuthorial', 'signatures', 'conclusions', 'attachments',
    )
    for name in _STRIP_NAMES:
        for el in cast(List[etree._Element], root.xpath(f'//*[local-name()="hcontainer" and @name="{name}"]')):
            parent = el.getparent()
            if parent is not None:
                parent.remove(el)
    # Strip historical duplicates that Finlex keeps for version history.
    # Sections: deduplicate by <num> text. Subsections/paragraphs: by eId base
    # (strip version suffix like "v20210680").
    def _norm_num(t: str | None) -> str:
        return re.sub(r'\s+', ' ', (t or '').replace('\xa0', ' ')).strip()
    _ver_re = re.compile(r'v\d{8}$')

    def _dedup_children(parent, child_tag: str, key_fn):
        """Remove duplicate children of `child_tag`, keeping first by key_fn."""
        seen: Set[object] = set()
        for el in list(parent):
            if el.tag.split('}')[-1] != child_tag:
                continue
            key = key_fn(el)
            if key is None:
                continue
            if key in seen:
                parent.remove(el)
            else:
                seen.add(key)

    def _eid_base(el) -> Optional[str]:
        eid = el.get('eId', '')
        return _ver_re.sub('', eid.split('__')[-1]) if eid else None

    for parent in cast(List[etree._Element], root.xpath(
        './/*[local-name()="hcontainer"]'
        ' | .//*[local-name()="body"]'
        ' | .//*[local-name()="chapter"]'
        ' | .//*[local-name()="part"]'
        ' | .//*[local-name()="title"]'
    )):
        _dedup_children(
            parent, 'section',
            lambda el: _norm_num(
                (el.find('{*}num').text if el.find('{*}num') is not None else '')
            ) or None,
        )
    for sec in root.findall('.//{*}section'):
        _dedup_children(sec, 'subsection', _eid_base)
        for sub in sec.findall('{*}subsection'):
            _dedup_children(sub, 'paragraph', _eid_base)
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
    """
    oracle_bytes = get_ground_truth_bytes(statute_id, corpus=corpus, selector=selector)
    if oracle_bytes is None:
        return None
    tree = etree.fromstring(oracle_bytes)
    body = tree.find(".//{*}body")
    return body if body is not None else tree

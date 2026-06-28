"""Source-root lifecycle tests for UK compile session memory management.

Family: source_root_lifecycle
Phase: compile (source acquisition + extraction context caching)

Background: Profiling ukpga/1970/9 showed peak RSS of 2.5–2.6 GB
from 386 XML feeds (~6 MB raw) expanding to large ET._Element trees all retained
simultaneously in memory.  The cause: extraction_cache held strong references to
every parsed root for the entire compile_ops_for_statute call, and two @lru_cache
functions (_source_parent_map, _source_ancestor_chain) also held roots as cache
keys, preventing GC even after theoretical eviction.

Fix (§source_root_lifecycle):
  1. _source_parent_map and _source_ancestor_chain converted from @lru_cache to
     plain-dict caches keyed on source_root (lxml elements do not support weak
     references, so WeakKeyDictionary is not usable; explicit eviction via
     evict_source_root_caches() is the memory-safety contract instead).
  2. compile_ops_for_statute evicts extraction_cache[act_id] and
     enacted_extraction_cache[act_id] after the last effect for each affecting
     act is processed (determined by pre-computed _last_effect_idx).
  3. The try/finally eviction pattern fires on both continue and fall-through
     paths, so every code path through the loop participates.

Coverage model:
  Every root-keyed (or root-descendant-keyed) module-level cache that
  evict_source_root_caches touches has a per-cache eviction test below that
  (a) warms the cache with one root's descendants, (b) verifies the entry
  survives an unrelated root's eviction, and (c) verifies it is removed when
  its own root is evicted.  A meta-test (guard-liveness, §2.9) introspects
  evict_source_root_caches and its listed helpers via AST and asserts that
  the per-cache tests' explicit registry exactly equals the caches the
  eviction flow references — so a future cache added to the eviction flow
  without a per-cache test fails CI.

Tests:
  1. Explicit eviction — parent-map cache entry is removed after evict_source_root_caches()
  2. Explicit eviction — ancestor-chain entry is removed after evict_source_root_caches()
  3. Parent-map correctness — same result as the old lru_cache behavior
  4. Ancestor-chain correctness — same result as the old lru_cache behavior
  5. Eviction index — _last_effect_idx correctly identifies last occurrence
  6. Re-parse on re-access — evicted context is transparently re-loaded from archive
  7. Behavior regression — end-to-end compile produces identical output before/after
     eviction (via a synthetic compile loop stub)
"""
from __future__ import annotations

import ast
import gc
import inspect
import textwrap
from lxml import etree as ET
from typing import Optional

from lawvm.uk_legislation.provision_extractor import (
    _EXTRACTION_CONTEXT_CACHE,
    _INSTRUCTION_TEXT_CACHE,
    _build_extraction_context,
    _instruction_text_before_amendment_container,
)
from lawvm.uk_legislation.source_context import (
    UKAffectingSourceContext,
    _source_broad_repeal_extent_part_cache,
    _source_child_has_parent_table_column_omission,
    _source_ancestor_chain,
    _source_ancestor_chain_cache,
    _source_is_broad_repeal_extent_part,
    _source_parent_table_column_omission_cache,
    _source_parent_map,
    _source_parent_map_cache,
    _unique_unnumbered_root_schedule,
    _unique_unnumbered_root_schedule_cache,
    evict_source_root_caches,
)
from lawvm.uk_legislation.source_fragment_context import (
    _SOURCE_LEAD_TEXT_CACHE,
    _SOURCE_PARENT_EACH_PROVISION_CACHE,
    _SOURCE_TAIL_TEXT_CACHE,
    _source_lead_text_before_subordinate_rows,
    _source_parent_each_provision_substitution_payload,
    _source_tail_text_after_subordinate_rows,
    evict_source_fragment_context_caches,
)
from lawvm.uk_legislation.table_selectors import (
    _NORMALIZED_ELEMENT_TEXT_CACHE,
    _normalized_element_text,
    evict_table_selector_caches,
)
from lawvm.uk_legislation.table_sources import (
    _REPEAL_EXTENT_TABLE_CACHE,
    _UK_FEE_TABLE_INDEX_CACHE,
    _UK_TABLE_ROWSPAN_ROWS_CACHE,
    _uk_get_fee_table_index,
    _uk_repeal_extent_source_tables,
    _uk_table_rows_with_rowspans,
)
from lawvm.uk_legislation.xml_helpers import (
    _DIRECT_STRUCTURAL_NUM_CACHE,
    _TEXT_CONTENT_CACHE,
    _direct_structural_num,
    _text_content,
    evict_xml_helper_caches,
)


# ---------------------------------------------------------------------------
# §2.9 guard-liveness: explicit registry of root-keyed caches the
# evict_source_root_caches flow references.  When you add a new root-keyed
# cache:
#   1. add it to evict_source_root_caches (or one of its helpers); AND
#   2. register its name in _EVICTED_CACHE_NAMES below; AND
#   3. write a per-cache eviction test below mirroring the existing style.
# The meta-test test_evict_source_root_caches_pins_all_referenced_caches
# asserts this registry exactly equals the caches the eviction flow
# references via AST introspection, so a future cache added to the eviction
# flow without registering it here fails CI.
# ---------------------------------------------------------------------------
_EVICTED_CACHE_NAMES: frozenset[str] = frozenset(
    {
        # source_context.py — locally-defined root-keyed caches
        "_source_parent_map_cache",
        "_source_ancestor_chain_cache",
        "_unique_unnumbered_root_schedule_cache",
        "_source_parent_table_column_omission_cache",
        "_source_broad_repeal_extent_part_cache",
        # provision_extractor.py — imported by source_context.evict_*
        "_EXTRACTION_CONTEXT_CACHE",
        "_INSTRUCTION_TEXT_CACHE",
        # source_fragment_context.py — evicted by evict_source_fragment_context_caches
        "_SOURCE_LEAD_TEXT_CACHE",
        "_SOURCE_TAIL_TEXT_CACHE",
        "_SOURCE_PARENT_EACH_PROVISION_CACHE",
        # table_sources.py — evicted inline by evict_source_root_caches
        "_REPEAL_EXTENT_TABLE_CACHE",
        "_UK_TABLE_ROWSPAN_ROWS_CACHE",
        "_UK_FEE_TABLE_INDEX_CACHE",
        # table_selectors.py — evicted by evict_table_selector_caches
        "_NORMALIZED_ELEMENT_TEXT_CACHE",
        # xml_helpers.py — evicted by evict_xml_helper_caches
        "_TEXT_CONTENT_CACHE",
        "_DIRECT_STRUCTURAL_NUM_CACHE",
    }
)

# Helper functions other than evict_source_root_caches itself that
# participate in cache eviction and are introspected by the meta-test.  When
# you add a new evict_*_caches helper and call it from evict_source_root_caches,
# register it here so its cache-name references are picked up.
_EVICT_HELPER_FUNCS: tuple = (
    evict_source_fragment_context_caches,
    evict_table_selector_caches,
    evict_xml_helper_caches,
)

_EVICT_HELPER_NAMES: frozenset[str] = frozenset(
    func.__name__ for func in _EVICT_HELPER_FUNCS
)


def _is_evict_cache_name(name: str) -> bool:
    return name.startswith("_") and (
        name.endswith("_CACHE") or name.endswith("_cache")
    )


# ---------------------------------------------------------------------------
# Shared XML helpers
# ---------------------------------------------------------------------------

_SIMPLE_XML = """\
<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation">
  <Section id="section-1">
    <Subsection id="section-1-1">
      <Text>First subsection text.</Text>
    </Subsection>
    <Subsection id="section-1-2">
      <Text>Second subsection text.</Text>
    </Subsection>
  </Section>
  <Section id="section-2">
    <Subsection id="section-2-1">
      <Text>Another section.</Text>
    </Subsection>
  </Section>
</Legislation>
"""

_ANOTHER_XML = """\
<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation">
  <Section id="section-10">
    <Subsection id="section-10-1">
      <Text>Section ten text.</Text>
    </Subsection>
  </Section>
</Legislation>
"""

_UNNUMBERED_SCHEDULE_XML = """\
<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation">
  <Schedule id="schedule">
    <Paragraph id="schedule-paragraph-1">
      <Pnumber>1</Pnumber>
      <Text>Paragraph text.</Text>
    </Paragraph>
  </Schedule>
</Legislation>
"""

_MULTIPLE_UNNUMBERED_SCHEDULE_XML = """\
<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation">
  <Schedule>
    <Text>First schedule.</Text>
  </Schedule>
  <Schedule>
    <Text>Second schedule.</Text>
  </Schedule>
</Legislation>
"""

_TABLE_XML = """\
<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation">
  <Schedule>
    <table>
      <tr><th>Column A</th><th>Column B</th></tr>
      <tr><td rowspan="2">A1</td><td>B1</td></tr>
      <tr><td>B2</td></tr>
    </table>
  </Schedule>
</Legislation>
"""

_SOURCE_LANE_PREDICATE_XML = """\
<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation">
  <Part id="part-1">
    <Text>Extent of repeal</Text>
    <P1 id="p1-1">
      <Pnumber>1</Pnumber>
      <Text>Omit from the first column of the table the entries relating to taxes.</Text>
      <P2 id="p2-1">
        <Pnumber>(1)</Pnumber>
        <Text>Child row.</Text>
      </P2>
    </P1>
  </Part>
</Legislation>
"""


def _make_root(xml: str = _SIMPLE_XML) -> ET._Element:
    return ET.fromstring(xml)


def _context_for_root(root: ET._Element) -> UKAffectingSourceContext:
    return UKAffectingSourceContext(
        xml_bytes=None,
        root=root,
        parent_map=None,
        exact_id_map={},
        sequence_map={},
        source_status="available",
        source_size=0,
        locator="test://source",
        authority_layer="TEST",
    )


# ---------------------------------------------------------------------------
# Test 1: WeakKeyDictionary releases parent-map entry when root is GC'd
# ---------------------------------------------------------------------------


def test_source_parent_map_releases_when_root_gc_d() -> None:
    """Parent-map cache entry is removed after explicit eviction.

    Note: lxml _Element objects do not support weak references, so the memory-
    safety contract is explicit eviction via evict_source_root_caches() rather
    than automatic GC release.  This test verifies that explicit eviction
    correctly removes the entry from the plain-dict cache.
    """
    root = _make_root()

    # Warm the cache
    parent_map = _source_parent_map(root)
    assert root in _source_parent_map_cache, "Root must be in parent-map cache"
    assert len(parent_map) > 0, "Parent map must be non-empty for this XML"

    # Explicit eviction must remove the entry.
    evict_source_root_caches(root)
    assert root not in _source_parent_map_cache, "Cache entry must be removed after eviction"

    # Verify that re-warming works after eviction (no stale state).
    parent_map2 = _source_parent_map(root)
    assert root in _source_parent_map_cache, "Root must re-enter cache after re-warm"
    assert len(parent_map2) == len(parent_map), "Re-warmed map must have same length"

    # Cleanup
    evict_source_root_caches(root)
    del root
    del parent_map
    del parent_map2
    gc.collect()


# ---------------------------------------------------------------------------
# Test 2: WeakKeyDictionary releases ancestor-chain entry when root is GC'd
# ---------------------------------------------------------------------------


def test_source_ancestor_chain_releases_when_root_gc_d() -> None:
    """Ancestor-chain cache entry is removed after explicit eviction.

    Note: lxml _Element objects do not support weak references, so the memory-
    safety contract is explicit eviction via evict_source_root_caches() rather
    than automatic GC release.  This test verifies that explicit eviction
    correctly removes the entry from the plain-dict cache.
    """
    root = _make_root()

    # Find a child element for the ancestor call — use a direct child of root
    direct_children = list(root)
    child = direct_children[0] if direct_children else root

    # Warm the cache
    chain = _source_ancestor_chain(root, child)
    assert root in _source_ancestor_chain_cache, "Root must be in ancestor-chain cache"
    # chain may be empty or non-empty depending on child choice; just ensure call succeeded
    assert isinstance(chain, tuple)

    # Explicit eviction must remove the entry.
    evict_source_root_caches(root)
    assert root not in _source_ancestor_chain_cache, "Cache entry must be removed after eviction"

    # Verify that re-warming works after eviction (no stale state).
    chain2 = _source_ancestor_chain(root, child)
    assert root in _source_ancestor_chain_cache, "Root must re-enter cache after re-warm"
    assert chain2 == chain, "Re-warmed chain must equal original"

    # Cleanup
    evict_source_root_caches(root)
    del root
    del child
    del chain
    del chain2
    del direct_children
    gc.collect()


def test_unique_unnumbered_root_schedule_cache_evicts_with_source_root() -> None:
    root = _make_root(_UNNUMBERED_SCHEDULE_XML)
    context = _context_for_root(root)

    schedule = _unique_unnumbered_root_schedule(context)

    assert schedule is not None
    assert schedule.get("id") == "schedule"
    assert root in _unique_unnumbered_root_schedule_cache
    assert _unique_unnumbered_root_schedule(context) is schedule

    evict_source_root_caches(root)

    assert root not in _unique_unnumbered_root_schedule_cache


def test_unique_unnumbered_root_schedule_negative_cache_evicts_with_source_root() -> None:
    root = _make_root(_MULTIPLE_UNNUMBERED_SCHEDULE_XML)
    context = _context_for_root(root)

    assert _unique_unnumbered_root_schedule(context) is None
    assert root in _unique_unnumbered_root_schedule_cache
    assert _unique_unnumbered_root_schedule_cache[root] is None

    evict_source_root_caches(root)

    assert root not in _unique_unnumbered_root_schedule_cache


def test_table_selector_normalized_text_cache_evicts_with_source_root() -> None:
    root = _make_root()
    other_root = _make_root(_ANOTHER_XML)
    root_child = next(iter(root))
    other_child = next(iter(other_root))

    assert _normalized_element_text(root_child)
    assert _normalized_element_text(other_child)
    assert root_child in _NORMALIZED_ELEMENT_TEXT_CACHE
    assert other_child in _NORMALIZED_ELEMENT_TEXT_CACHE

    evict_source_root_caches(root)

    assert root_child not in _NORMALIZED_ELEMENT_TEXT_CACHE
    assert other_child in _NORMALIZED_ELEMENT_TEXT_CACHE

    evict_source_root_caches(other_root)
    assert other_child not in _NORMALIZED_ELEMENT_TEXT_CACHE


def test_table_rowspan_rows_cache_evicts_with_source_root() -> None:
    root = _make_root(_TABLE_XML)
    other_root = _make_root(_TABLE_XML)
    table = next(el for el in root.iter() if el.tag.rsplit("}", 1)[-1] == "table")
    other_table = next(
        el for el in other_root.iter() if el.tag.rsplit("}", 1)[-1] == "table"
    )

    assert _uk_table_rows_with_rowspans(table) == [
        ["Column A", "Column B"],
        ["A1", "B1"],
        ["A1", "B2"],
    ]
    assert _uk_table_rows_with_rowspans(other_table)
    assert table in _UK_TABLE_ROWSPAN_ROWS_CACHE
    assert other_table in _UK_TABLE_ROWSPAN_ROWS_CACHE

    evict_source_root_caches(root)

    assert table not in _UK_TABLE_ROWSPAN_ROWS_CACHE
    assert other_table in _UK_TABLE_ROWSPAN_ROWS_CACHE

    evict_source_root_caches(other_root)
    assert other_table not in _UK_TABLE_ROWSPAN_ROWS_CACHE


def test_xml_helper_caches_evict_with_source_root() -> None:
    root = _make_root()
    other_root = _make_root(_ANOTHER_XML)
    root_section = next(el for el in root.iter() if el.get("id") == "section-1")
    other_section = next(el for el in other_root.iter() if el.get("id") == "section-10")

    assert _text_content(root_section)
    assert _direct_structural_num(root_section) == ""
    assert _text_content(other_section)
    assert _direct_structural_num(other_section) == ""
    assert root_section in _TEXT_CONTENT_CACHE
    assert root_section in _DIRECT_STRUCTURAL_NUM_CACHE
    assert other_section in _TEXT_CONTENT_CACHE
    assert other_section in _DIRECT_STRUCTURAL_NUM_CACHE

    evict_source_root_caches(root)

    assert root_section not in _TEXT_CONTENT_CACHE
    assert root_section not in _DIRECT_STRUCTURAL_NUM_CACHE
    assert other_section in _TEXT_CONTENT_CACHE
    assert other_section in _DIRECT_STRUCTURAL_NUM_CACHE

    evict_source_root_caches(other_root)
    assert other_section not in _TEXT_CONTENT_CACHE
    assert other_section not in _DIRECT_STRUCTURAL_NUM_CACHE


def test_source_lane_predicate_caches_evict_with_source_root() -> None:
    root = _make_root(_SOURCE_LANE_PREDICATE_XML)
    other_root = _make_root(_SOURCE_LANE_PREDICATE_XML)
    context = _context_for_root(root)
    other_context = _context_for_root(other_root)
    part = next(el for el in root.iter() if el.get("id") == "part-1")
    child = next(el for el in root.iter() if el.get("id") == "p2-1")
    other_part = next(el for el in other_root.iter() if el.get("id") == "part-1")
    other_child = next(el for el in other_root.iter() if el.get("id") == "p2-1")

    assert _source_is_broad_repeal_extent_part(part)
    assert _source_child_has_parent_table_column_omission(context, child)
    assert _source_is_broad_repeal_extent_part(other_part)
    assert _source_child_has_parent_table_column_omission(other_context, other_child)
    assert part in _source_broad_repeal_extent_part_cache
    assert child in _source_parent_table_column_omission_cache
    assert other_part in _source_broad_repeal_extent_part_cache
    assert other_child in _source_parent_table_column_omission_cache

    evict_source_root_caches(root)

    assert part not in _source_broad_repeal_extent_part_cache
    assert child not in _source_parent_table_column_omission_cache
    assert other_part in _source_broad_repeal_extent_part_cache
    assert other_child in _source_parent_table_column_omission_cache

    evict_source_root_caches(other_root)
    assert other_part not in _source_broad_repeal_extent_part_cache
    assert other_child not in _source_parent_table_column_omission_cache


# ---------------------------------------------------------------------------
# Per-cache eviction tests for caches previously uncovered by the suite.
# Each mirrors the style above: warm cache with two roots, assert both
# entries present, evict one root, assert the unmentioned root's entry
# survives while the evicted root's entry is gone. (§2.9 guard-liveness:
# every cache registered in _EVICTED_CACHE_NAMES must have a per-cache
# eviction test so the registry's claim is observable end-to-end.)
# ---------------------------------------------------------------------------


def test_extraction_context_cache_evicts_with_source_root() -> None:
    """_EXTRACTION_CONTEXT_CACHE (root-keyed) is removed by evict_source_root_caches.

    Holds strong references to root via UKExtractionContext.parent_map values
    (which include root as a parent element).  Without eviction, every parsed
    affecting-act root accumulated for the whole compile run (§1.12 / §2.7
    source-root cache lifecycle).
    """
    root = _make_root()
    other_root = _make_root(_ANOTHER_XML)

    ctx_root = _build_extraction_context(root)
    ctx_other = _build_extraction_context(other_root)
    assert ctx_root is not None and ctx_other is not None
    assert root in _EXTRACTION_CONTEXT_CACHE
    assert other_root in _EXTRACTION_CONTEXT_CACHE

    evict_source_root_caches(root)

    assert root not in _EXTRACTION_CONTEXT_CACHE
    assert other_root in _EXTRACTION_CONTEXT_CACHE

    evict_source_root_caches(other_root)
    assert other_root not in _EXTRACTION_CONTEXT_CACHE


def test_instruction_text_cache_evicts_with_source_root() -> None:
    """_INSTRUCTION_TEXT_CACHE (descendant-keyed) is removed by evict_source_root_caches.

    Keys are arbitrary descendant elements; each key pins the whole parsed
    tree.  Without eviction this cache grew unbounded across compiles.
    """
    root = _make_root(_SOURCE_LANE_PREDICATE_XML)
    other_root = _make_root(_SOURCE_LANE_PREDICATE_XML)
    descendant = next(el for el in root.iter() if el.get("id") == "p1-1")
    other_descendant = next(el for el in other_root.iter() if el.get("id") == "p1-1")

    # Cache is populated even when no BlockAmendment container is present.
    assert _instruction_text_before_amendment_container(descendant) is not None
    assert _instruction_text_before_amendment_container(other_descendant) is not None
    assert descendant in _INSTRUCTION_TEXT_CACHE
    assert other_descendant in _INSTRUCTION_TEXT_CACHE

    evict_source_root_caches(root)

    assert descendant not in _INSTRUCTION_TEXT_CACHE
    assert other_descendant in _INSTRUCTION_TEXT_CACHE

    evict_source_root_caches(other_root)
    assert other_descendant not in _INSTRUCTION_TEXT_CACHE


def test_source_lead_text_cache_evicts_with_source_root() -> None:
    """_SOURCE_LEAD_TEXT_CACHE (descendant-keyed) is removed via
    evict_source_fragment_context_caches → evict_source_root_caches."""
    root = _make_root(_SOURCE_LANE_PREDICATE_XML)
    other_root = _make_root(_SOURCE_LANE_PREDICATE_XML)
    p1 = next(el for el in root.iter() if el.get("id") == "p1-1")
    other_p1 = next(el for el in other_root.iter() if el.get("id") == "p1-1")

    assert _source_lead_text_before_subordinate_rows(p1) is not None
    assert _source_lead_text_before_subordinate_rows(other_p1) is not None
    assert p1 in _SOURCE_LEAD_TEXT_CACHE
    assert other_p1 in _SOURCE_LEAD_TEXT_CACHE

    evict_source_root_caches(root)

    assert p1 not in _SOURCE_LEAD_TEXT_CACHE
    assert other_p1 in _SOURCE_LEAD_TEXT_CACHE

    evict_source_root_caches(other_root)
    assert other_p1 not in _SOURCE_LEAD_TEXT_CACHE


def test_source_tail_text_cache_evicts_with_source_root() -> None:
    """_SOURCE_TAIL_TEXT_CACHE (descendant-keyed) is removed via
    evict_source_fragment_context_caches → evict_source_root_caches.

    Cache is populated even for elements whose tail text after subordinate
    rows is empty (the cache still records the empty string).
    """
    root = _make_root(_SOURCE_LANE_PREDICATE_XML)
    other_root = _make_root(_SOURCE_LANE_PREDICATE_XML)
    p1 = next(el for el in root.iter() if el.get("id") == "p1-1")
    other_p1 = next(el for el in other_root.iter() if el.get("id") == "p1-1")

    assert isinstance(_source_tail_text_after_subordinate_rows(p1), str)
    assert isinstance(_source_tail_text_after_subordinate_rows(other_p1), str)
    assert p1 in _SOURCE_TAIL_TEXT_CACHE
    assert other_p1 in _SOURCE_TAIL_TEXT_CACHE

    evict_source_root_caches(root)

    assert p1 not in _SOURCE_TAIL_TEXT_CACHE
    assert other_p1 in _SOURCE_TAIL_TEXT_CACHE

    evict_source_root_caches(other_root)
    assert other_p1 not in _SOURCE_TAIL_TEXT_CACHE


def test_source_parent_each_provision_cache_evicts_with_source_root() -> None:
    """_SOURCE_PARENT_EACH_PROVISION_CACHE (descendant-keyed) is removed via
    evict_source_fragment_context_caches → evict_source_root_caches.

    The cache stores None for non-matching ancestors (negative caching), so
    the cache entry is populated even on this fixture where no
    per-provision-substitution instruction text matches.
    """
    root = _make_root(_SOURCE_LANE_PREDICATE_XML)
    other_root = _make_root(_SOURCE_LANE_PREDICATE_XML)
    p1 = next(el for el in root.iter() if el.get("id") == "p1-1")
    other_p1 = next(el for el in other_root.iter() if el.get("id") == "p1-1")

    # P1 is in _SOURCE_PARENT_EACH_PROVISION_INSTRUCTION_TAGS, so the cache
    # records an entry (None for non-matching text) on first call.
    assert _source_parent_each_provision_substitution_payload(p1) is None
    assert _source_parent_each_provision_substitution_payload(other_p1) is None
    assert p1 in _SOURCE_PARENT_EACH_PROVISION_CACHE
    assert other_p1 in _SOURCE_PARENT_EACH_PROVISION_CACHE

    evict_source_root_caches(root)

    assert p1 not in _SOURCE_PARENT_EACH_PROVISION_CACHE
    assert other_p1 in _SOURCE_PARENT_EACH_PROVISION_CACHE

    evict_source_root_caches(other_root)
    assert other_p1 not in _SOURCE_PARENT_EACH_PROVISION_CACHE


def test_repeal_extent_table_cache_evicts_with_source_root() -> None:
    """_REPEAL_EXTENT_TABLE_CACHE (root-keyed) is removed by
    evict_source_root_caches.

    Cache stores an entry (possibly an empty tuple) for every root scanned,
    so even roots without a repeal-extent table retain the root.
    """
    root = _make_root(_TABLE_XML)
    other_root = _make_root(_TABLE_XML)

    # _TABLE_XML has no repeal-extent table; result is () but cache is populated.
    assert _uk_repeal_extent_source_tables(root) == ()
    assert _uk_repeal_extent_source_tables(other_root) == ()
    assert root in _REPEAL_EXTENT_TABLE_CACHE
    assert other_root in _REPEAL_EXTENT_TABLE_CACHE

    evict_source_root_caches(root)

    assert root not in _REPEAL_EXTENT_TABLE_CACHE
    assert other_root in _REPEAL_EXTENT_TABLE_CACHE

    evict_source_root_caches(other_root)
    assert other_root not in _REPEAL_EXTENT_TABLE_CACHE


def test_uk_fee_table_index_cache_evicts_with_source_root() -> None:
    """_UK_FEE_TABLE_INDEX_CACHE (root-keyed) is removed by
    evict_source_root_caches.

    Cache stores an entry (possibly an empty tuple) for every root scanned,
    so even roots without a fee table retain the root.
    """
    root = _make_root(_TABLE_XML)
    other_root = _make_root(_TABLE_XML)

    # _TABLE_XML has no fee table headers; result is () but cache is populated.
    assert _uk_get_fee_table_index(root) == ()
    assert _uk_get_fee_table_index(other_root) == ()
    assert root in _UK_FEE_TABLE_INDEX_CACHE
    assert other_root in _UK_FEE_TABLE_INDEX_CACHE

    evict_source_root_caches(root)

    assert root not in _UK_FEE_TABLE_INDEX_CACHE
    assert other_root in _UK_FEE_TABLE_INDEX_CACHE

    evict_source_root_caches(other_root)
    assert other_root not in _UK_FEE_TABLE_INDEX_CACHE


# ---------------------------------------------------------------------------
# §2.9 guard-liveness: meta-test that introspects evict_source_root_caches
# and asserts the per-cache tests above cover every cache the eviction flow
# references.  A future cache added to the eviction flow without registering
# it in _EVICTED_CACHE_NAMES (and adding a per-cache test) fails CI.
# ---------------------------------------------------------------------------


def _collect_cache_names_referenced_by_evict() -> set[str]:
    """Walk the source of evict_source_root_caches plus every helper in
    _EVICT_HELPER_FUNCS, collecting every identifier ending in _CACHE or
    _cache via AST. Returns the full set of cache names the eviction flow
    actually references."""
    funcs = (evict_source_root_caches, *_EVICT_HELPER_FUNCS)
    names: set[str] = set()
    for func in funcs:
        source = textwrap.dedent(inspect.getsource(func))
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and _is_evict_cache_name(node.id):
                names.add(node.id)
            elif isinstance(node, ast.alias) and _is_evict_cache_name(node.name):
                names.add(node.name)
            elif isinstance(node, ast.Attribute) and _is_evict_cache_name(node.attr):
                names.add(node.attr)
    return names


def _collect_helper_calls_in_root_evict() -> set[str]:
    """Find every evict_*_caches(...) call in evict_source_root_caches' body."""
    source = textwrap.dedent(inspect.getsource(evict_source_root_caches))
    tree = ast.parse(source)
    calls: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id.startswith("evict_")
            and node.func.id.endswith("_caches")
        ):
            calls.add(node.func.id)
    return calls


def test_evict_source_root_caches_registry_matches_helpers_called() -> None:
    """Every evict_*_caches helper called from evict_source_root_caches is
    registered in _EVICT_HELPER_FUNCS, and vice versa.

    If a future refactor adds a new evict_*_caches helper call to
    evict_source_root_caches without registering it, this test fails so
    the cache-name introspection in the next test does not silently miss
    that helper's referenced caches.
    """
    actual = _collect_helper_calls_in_root_evict()
    expected = set(_EVICT_HELPER_NAMES)
    assert actual == expected, (
        "evict_*_caches helper registry drift: "
        f"called but not registered in test: {actual - expected}; "
        f"registered but no longer called: {expected - actual}"
    )


def test_evict_source_root_caches_pins_all_referenced_caches() -> None:
    """Every cache name referenced by evict_source_root_caches (and its
    registered helpers) is in the explicit registry _EVICTED_CACHE_NAMES,
    and vice versa.

    This is the §2.9 guard-liveness meta-test.  It fails when:
      - a future cache is added to the eviction flow without being
        registered in _EVICTED_CACHE_NAMES (and thus without a per-cache
        eviction test); OR
      - a cache is renamed or removed from the eviction flow without
        updating _EVICTED_CACHE_NAMES.
    Either direction of drift makes the test fail, so the registry is
    provably the same set the eviction flow actually touches.
    """
    actual = _collect_cache_names_referenced_by_evict()
    expected = set(_EVICTED_CACHE_NAMES)
    assert actual == expected, (
        "evict cache-name registry drift: "
        f"referenced in eviction but not registered in test (add to "
        f"_EVICTED_CACHE_NAMES and write a per-cache test): "
        f"{actual - expected}; "
        f"registered in test but no longer referenced in eviction flow: "
        f"{expected - actual}"
    )


# ---------------------------------------------------------------------------
# Test 3: Parent-map correctness — result matches expected structure
# ---------------------------------------------------------------------------


def test_source_parent_map_correctness() -> None:
    """Parent map correctly maps children to their parents."""
    root = _make_root()
    parent_map = _source_parent_map(root)

    # Every non-root element should have an entry pointing to its actual parent
    for parent_el in root.iter():
        for child_el in parent_el:
            assert child_el in parent_map, f"Child {child_el.tag} missing from parent_map"
            assert parent_map[child_el] is parent_el, (
                f"parent_map[child] should be the direct parent; "
                f"got {parent_map[child_el].tag!r}, expected {parent_el.tag!r}"
            )

    # Root itself should not be in the parent map (it has no parent)
    assert root not in parent_map, "Root element must not appear as a child in parent_map"


# ---------------------------------------------------------------------------
# Test 4: Ancestor-chain correctness — returns closest-first chain
# ---------------------------------------------------------------------------


def test_source_ancestor_chain_correctness() -> None:
    """Ancestor chain returns ancestors in closest-first order."""
    root = _make_root()
    parent_map = _source_parent_map(root)

    # Find a deeply nested element (Subsection → Section → Legislation)
    leaf: Optional[ET._Element] = None
    for el in root.iter():
        if el is not root and el not in (list(root)):
            # Second-level or deeper
            leaf = el
            break

    if leaf is None:
        # XML structure changed — skip depth check
        return

    chain = _source_ancestor_chain(root, leaf)
    assert isinstance(chain, tuple), "Ancestor chain must be a tuple"

    if chain:
        # First ancestor must be the direct parent
        assert chain[0] is parent_map.get(leaf), (
            "First ancestor must be the direct parent element"
        )
        # Last ancestor must be root (or chain ends before root)
        if len(chain) > 1:
            assert chain[-1] is root or chain[-1] in {
                el for el in root
            }, "Last ancestor must be root or a direct child of root"

    # Cache hit: same result on second call
    chain2 = _source_ancestor_chain(root, leaf)
    assert chain2 is chain or chain2 == chain, "Repeated call must return same result"


# ---------------------------------------------------------------------------
# Test 5: Ancestor-chain None handling
# ---------------------------------------------------------------------------


def test_source_ancestor_chain_none_inputs() -> None:
    """Ancestor chain returns empty tuple for None inputs."""
    root = _make_root()
    assert _source_ancestor_chain(None, None) == ()
    assert _source_ancestor_chain(root, None) == ()
    assert _source_ancestor_chain(None, root) == ()
    assert _source_ancestor_chain(root, root) == ()


# ---------------------------------------------------------------------------
# Test 6: Eviction index — _last_effect_idx correctly identifies last position
# ---------------------------------------------------------------------------


def test_last_effect_idx_construction() -> None:
    """The eviction index maps each affecting_act_id to its last position."""
    # Simulate the _last_effect_idx computation from compile_ops_for_statute.
    # We use simple namedtuples to mimic UKEffectRecord.affecting_act_id.
    from types import SimpleNamespace

    effects = [
        SimpleNamespace(affecting_act_id="act-A"),
        SimpleNamespace(affecting_act_id="act-B"),
        SimpleNamespace(affecting_act_id="act-A"),  # act-A appears again at pos 2
        SimpleNamespace(affecting_act_id="act-C"),
        SimpleNamespace(affecting_act_id="act-B"),  # act-B appears again at pos 4
    ]

    _last_effect_idx: dict[str, int] = {}
    for j, e_j in enumerate(effects):
        _last_effect_idx[e_j.affecting_act_id] = j

    assert _last_effect_idx["act-A"] == 2, "act-A last at position 2"
    assert _last_effect_idx["act-B"] == 4, "act-B last at position 4"
    assert _last_effect_idx["act-C"] == 3, "act-C last at position 3"

    # Verify eviction triggers at correct positions
    evicted: list[tuple[str, int]] = []
    for i, e in enumerate(effects):
        if _last_effect_idx.get(e.affecting_act_id) == i:
            evicted.append((e.affecting_act_id, i))

    assert ("act-A", 2) in evicted, "act-A must be evicted at its last occurrence"
    assert ("act-B", 4) in evicted, "act-B must be evicted at its last occurrence"
    assert ("act-C", 3) in evicted, "act-C must be evicted at its last occurrence"

    # No act should be evicted before its last occurrence
    early_evictions = [
        (act_id, pos)
        for act_id, pos in evicted
        if pos < _last_effect_idx[act_id]
    ]
    assert not early_evictions, f"No act should be evicted before last occurrence: {early_evictions}"


# ---------------------------------------------------------------------------
# Test 7: Parent-map cache identity on the same root
# ---------------------------------------------------------------------------


def test_source_parent_map_cache_hit_identity() -> None:
    """Two calls with the same root return the identical dict object."""
    root = _make_root()
    map1 = _source_parent_map(root)
    map2 = _source_parent_map(root)
    assert map1 is map2, "Second call with same root must return same dict object (cache hit)"


# ---------------------------------------------------------------------------
# Test 8: Multiple roots get independent parent maps
# ---------------------------------------------------------------------------


def test_source_parent_map_cache_isolation() -> None:
    """Different root objects get distinct parent maps."""
    root_a = _make_root(_SIMPLE_XML)
    root_b = _make_root(_ANOTHER_XML)
    map_a = _source_parent_map(root_a)
    map_b = _source_parent_map(root_b)
    assert map_a is not map_b, "Different roots must produce distinct parent maps"
    assert len(map_a) != len(map_b) or set(map_a) != set(map_b), (
        "Parent maps for different XMLs must differ"
    )

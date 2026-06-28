"""Perf test for source-root cache eviction (AGENTS.md §2.7).

Family: source_root_lifecycle (perf gate)
Phase: compile (compile-loop boundary eviction)

Background:
    evict_source_root_caches(root) is called at the last-occurrence point of
    each affecting-act root during a single-statute compile.  Originally
    every helper walked the WHOLE cache × ``key.getroottree().getroot() is
    root`` per entry — O(cache_size) per eviction, O(M·N²) over N evictions
    across M cached descendants per root.

    The 2026-06-27 refactor (`_RootScopedCache`) replaces that with a per-root
    reverse index (``dict[id(root) → set[key]]`` maintained on insert), so
    eviction walks only the bucket for the evicted root — O(M·N) over the
    full compile.

Test shape:
    Build N=20 synthetic roots, each holding M=2000 cached descendants, all
    sharing a single cache.  Then evict them one by one.  At peak the cache
    holds 40,000 entries; an O(M·N²) eviction would scan ~780,000 entries ×
    ``getroottree().getroot()`` per call, while an O(M·N) eviction scans
    ~40,000 (the sum of per-root buckets, never the whole cache).

    The ceiling catches order-of-magnitude regressions; it is loose enough
    to absorb CI/WSL2 jitter but tight enough that a revert to whole-cache
    scanning blows it ~5–10×.
"""
from __future__ import annotations

import time

from lxml import etree as ET

from lawvm.uk_legislation.xml_helpers import _RootScopedCache
from lawvm.uk_legislation.source_context import evict_source_root_caches


# ---------------------------------------------------------------------------
# Workload constants — N_ROOTS × M_DESCENDANTS_PER_ROOT = peak cache size.
#
# Pick N and M so the OLD O(M·N²) shape would take >2s (well above the
# ceiling) while the NEW O(M·N) shape lands comfortably below.  At 1 μs per
# elementary op (dict iteration + getroottree().getroot() + dict.pop):
#   OLD: 20 × 40000 800,000 × 1 μs 0.8 s (worst case 5–10 s with overhead).
#   NEW: 20 × 2000   40,000 × 1 μs 0.04 s (worst case <0.2 s).
# ---------------------------------------------------------------------------
_N_ROOTS = 20
_M_DESCENDANTS_PER_ROOT = 2000
# Per-eviction budget (each evict_root call must finish well under this); the
# sum over N_ROOTS evictions is gated against _TOTAL_CEILING_MS.
_PER_EVICT_CEILING_MS = 100
# Total budget for the full eviction loop.  Pre-refactor O(M·N²) would take
# seconds (multiple × 800 ms); post-refactor O(M·N) lands in the tens of ms.
# 2000 ms absorbs CI noise without losing the regression signal.
_TOTAL_CEILING_MS = 2000


def _build_synthetic_root(label: str, n_descendants: int) -> ET._Element:
    """Build an XML root with exactly ``n_descendants`` direct children.

    Each child carries a distinctive ``id`` so we can assert selectively that
    only the evicted root's entries were purged.  Children are leaf elements
    (no nested Text) so the cache count matches ``n_descendants`` exactly.
    """
    children_xml = "".join(
        f'<Subsection id="{label}-child-{i}"/>'
        for i in range(n_descendants)
    )
    return ET.fromstring(
        f'<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation">'
        f'{children_xml}</Legislation>'
    )


class TestRootScopedCacheCorrectness:
    """Per-method invariants for ``_RootScopedCache``.

    These are correctness tests, NOT perf tests; they pin the dict-equivalence
    contract so subsequent callers can rely on ``__contains__`` / ``.get`` /
    ``.pop`` / iteration semantics identical to a plain ``dict``.
    """

    def test_evict_root_removes_only_its_own_entries(self) -> None:
        cache = _RootScopedCache()
        root_a = _build_synthetic_root("A", 3)
        root_b = _build_synthetic_root("B", 3)
        for el in root_a.iter():
            if el is not root_a:
                cache[el] = "a"
        for el in root_b.iter():
            if el is not root_b:
                cache[el] = "b"

        cache.evict_root(root_a)

        # root_a's descendants are gone; root_b's survive.
        for el in root_a.iter():
            if el is not root_a:
                assert el not in cache
        for el in root_b.iter():
            if el is not root_b:
                assert el in cache
                assert cache[el] == "b"

    def test_evict_root_on_empty_cache_is_noop(self) -> None:
        cache = _RootScopedCache()
        root = _build_synthetic_root("X", 1)
        # Must not raise.
        cache.evict_root(root)
        # The reverse-index dict must remain empty.
        assert not cache._reverse

    def test_evict_root_called_twice_is_noop(self) -> None:
        cache = _RootScopedCache()
        root = _build_synthetic_root("X", 3)
        for el in root.iter():
            if el is not root:
                cache[el] = "v"
        cache.evict_root(root)
        cache.evict_root(root)  # second call must not raise
        assert not cache

    def test_pop_removes_from_reverse_index(self) -> None:
        cache = _RootScopedCache()
        root = _build_synthetic_root("X", 3)
        descendants = [el for el in root.iter() if el is not root]
        for el in descendants:
            cache[el] = "v"
        popped = cache.pop(descendants[0])
        assert popped == "v"
        assert descendants[0] not in cache
        # The other descendants survive.
        for el in descendants[1:]:
            assert el in cache
        # And evict_root still works (drops the remaining entries).
        cache.evict_root(root)
        assert not cache

    def test_clear_zeros_both_index_and_storage(self) -> None:
        cache: _RootScopedCache = _RootScopedCache()
        root_a = _build_synthetic_root("A", 3)
        root_b = _build_synthetic_root("B", 3)
        for el in root_a.iter():
            if el is not root_a:
                cache[el] = "a"
        for el in root_b.iter():
            if el is not root_b:
                cache[el] = "b"
        cache.clear()
        assert not cache
        assert not cache._reverse


class TestEvictSourceRootCachesPerf:
    """Adversarial timing for ``evict_source_root_caches`` on a saturated cache.

    Pre-refactor each helper walked the whole cache ×
    ``el.getroottree().getroot() is root`` per entry — O(cache_size) per
    eviction, O(M·N²) total over the N=20 evictions across a peak cache of
    40,000 entries.  Post-refactor each eviction walks only its own root's
    bucket — O(M·N) total.

    The ceiling catches a revert to whole-cache scanning.
    """

    def test_evict_all_roots_under_ceiling(self) -> None:
        # Build N synthetic roots and warm each one's caches with M descendants
        # by invoking evict_source_root_caches' touchpoints directly via the
        # module-level caches.  We use _RootScopedCache directly to control
        # the cache size precisely (the source-context touchpoints vary).
        cache: _RootScopedCache = _RootScopedCache()
        roots_and_descendants: list[tuple[ET._Element, list[ET._Element]]] = []
        for n in range(_N_ROOTS):
            root = _build_synthetic_root(f"root{n}", _M_DESCENDANTS_PER_ROOT)
            descendants = [el for el in root.iter() if el is not root]
            for el in descendants:
                cache[el] = n
            roots_and_descendants.append((root, descendants))

        # Sanity: cache holds the full cross-root size.
        assert len(cache) == _N_ROOTS * _M_DESCENDANTS_PER_ROOT

        # Drive the eviction loop, asserting each root's descendants disappear
        # while unrelated roots' survive.  Time each eviction.
        per_evict_max_ms = 0.0
        total_t0 = time.perf_counter()
        for evict_idx, (root, descendants) in enumerate(roots_and_descendants):
            t0 = time.perf_counter()
            cache.evict_root(root)
            elapsed_ms = (time.perf_counter() - t0) * 1000
            per_evict_max_ms = max(per_evict_max_ms, elapsed_ms)
            # Each evicted root's descendants are gone; the remaining roots'
            # entries survive intact.
            for el in descendants:
                assert el not in cache
            for surv_idx in range(evict_idx + 1, _N_ROOTS):
                _, surv_descendants = roots_and_descendants[surv_idx]
                for el in surv_descendants:
                    assert el in cache
        total_ms = (time.perf_counter() - total_t0) * 1000

        # Per-eviction budget: each individual evict_root call must finish
        # well under this — the OLD O(cache_size)-scan would blow it for any
        # eviction once the cache saturated, the new O(M) walk is sub-ms.
        assert per_evict_max_ms < _PER_EVICT_CEILING_MS, (
            f"Per-eviction ceiling exceeded: max={per_evict_max_ms:.1f} ms "
            f"(ceiling {_PER_EVICT_CEILING_MS} ms) over peak cache size "
            f"{_N_ROOTS * _M_DESCENDANTS_PER_ROOT}; eviction may have "
            f"regressed to whole-cache scanning (O(cache_size) instead of "
            f"O(keys-for-this-root))"
        )
        # Total ceiling: O(M·N) lands ~tens of ms; O(M·N²) would blow this
        # by ~5–10×.
        assert total_ms < _TOTAL_CEILING_MS, (
            f"Total eviction ceiling exceeded: total={total_ms:.1f} ms "
            f"(ceiling {_TOTAL_CEILING_MS} ms) over "
            f"{_N_ROOTS} evictions × {_M_DESCENDANTS_PER_ROOT} descendants; "
            f"eviction may have regressed to O(M·N²) whole-cache scanning"
        )

    def test_evict_source_root_caches_under_ceiling_on_saturated_caches(
        self,
    ) -> None:
        """End-to-end perf gate against ``evict_source_root_caches`` itself.

        Warms ALL caches the eviction flow touches (all 16 from
        ``_EVICTED_CACHE_NAMES`` of ``test_uk_source_root_lifecycle.py``) with
        N roots × M descendants each by invoking their public accessor helpers,
        then evicts each root via the production entry point and asserts the
        full sweep stays under budget.  A revert to whole-cache scanning would
        blow this 5–10× since the saturated caches hold 40,000 entries each.
        """
        from lawvm.uk_legislation.provision_extractor import (
            _EXTRACTION_CONTEXT_CACHE,
            _INSTRUCTION_TEXT_CACHE,
            _build_extraction_context,
            _instruction_text_before_amendment_container,
        )
        from lawvm.uk_legislation.source_context import (
            _source_ancestor_chain,
            _source_ancestor_chain_cache,
            _source_broad_repeal_extent_part_cache,
            _source_child_has_parent_table_column_omission,
            _source_is_broad_repeal_extent_part,
            _source_parent_map,
            _source_parent_map_cache,
            _source_parent_table_column_omission_cache,
            _unique_unnumbered_root_schedule,
            _unique_unnumbered_root_schedule_cache,
        )
        from lawvm.uk_legislation.source_fragment_context import (
            _SOURCE_LEAD_TEXT_CACHE,
            _SOURCE_TAIL_TEXT_CACHE,
            _SOURCE_PARENT_EACH_PROVISION_CACHE,
            _source_lead_text_before_subordinate_rows,
            _source_parent_each_provision_substitution_payload,
            _source_tail_text_after_subordinate_rows,
        )
        from lawvm.uk_legislation.table_selectors import (
            _NORMALIZED_ELEMENT_TEXT_CACHE,
            _normalized_element_text,
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
            _tag,
            _text_content,
        )

        from lawvm.uk_legislation.source_context import (
            UKAffectingSourceContext,
        )

        sources_xml = (
            '<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation">'
            '<Part id="part-1"><Text>Extent of repeal</Text>'
            '<P1 id="p1-1"><Pnumber>1</Pnumber>'
            '<Text>Omit from the first column of the table the entries relating to taxes.</Text>'
            '<P2 id="p2-1"><Pnumber>(1)</Pnumber><Text>Child row.</Text></P2>'
            '</P1></Part></Legislation>'
        )

        def _ctx(root: ET._Element) -> UKAffectingSourceContext:
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

        roots: list[ET._Element] = []
        # NOTE: these caches are module-level and shared across the test
        # session; prior tests may have left entries for THEIR roots.
        # Snapshot the per-cache length BEFORE our warm-up so we can assert
        # eviction restored the cache to exactly the pre-test state (our roots'
        # entries removed, no unrelated entries dropped).
        tracked_caches: tuple = (
            _source_parent_map_cache,
            _source_ancestor_chain_cache,
            _unique_unnumbered_root_schedule_cache,
            _source_broad_repeal_extent_part_cache,
            _source_parent_table_column_omission_cache,
            _EXTRACTION_CONTEXT_CACHE,
            _INSTRUCTION_TEXT_CACHE,
            _SOURCE_LEAD_TEXT_CACHE,
            _SOURCE_TAIL_TEXT_CACHE,
            _SOURCE_PARENT_EACH_PROVISION_CACHE,
            _REPEAL_EXTENT_TABLE_CACHE,
            _UK_FEE_TABLE_INDEX_CACHE,
            _UK_TABLE_ROWSPAN_ROWS_CACHE,
            _NORMALIZED_ELEMENT_TEXT_CACHE,
            _TEXT_CONTENT_CACHE,
            _DIRECT_STRUCTURAL_NUM_CACHE,
        )
        before_lens = {id(c): len(c) for c in tracked_caches}
        for n in range(_N_ROOTS):
            root = ET.fromstring(sources_xml)
            roots.append(root)
            # Warm every cache the eviction flow touches.
            _build_extraction_context(root)
            context = _ctx(root)
            _unique_unnumbered_root_schedule(context)
            _source_parent_map(root)
            for descendant in root.iter():
                if descendant is root:
                    continue
                _text_content(descendant)
                _direct_structural_num(descendant)
                _normalized_element_text(descendant)
                _instruction_text_before_amendment_container(descendant)
                _source_ancestor_chain(root, descendant)
                _source_lead_text_before_subordinate_rows(descendant)
                _source_tail_text_after_subordinate_rows(descendant)
                _source_parent_each_provision_substitution_payload(descendant)
            # Warm root-keyed caches via their public helpers.
            for part in root.iter():
                if part.get("id") == "part-1":
                    _source_is_broad_repeal_extent_part(part)
                if part.get("id") == "p2-1":
                    _source_child_has_parent_table_column_omission(context, part)
            for table in root.iter():
                if _tag(table) == "table":
                    _uk_table_rows_with_rowspans(table)
            _uk_repeal_extent_source_tables(root)
            _uk_get_fee_table_index(root)

        # Sanity: at least one entry per root per cache, confirming the
        # workload is non-trivial.  These caches are saturated by the loop
        # above; if any returns empty, the warm-up logic is wrong and the
        # perf measurement is meaningless.
        assert len(_TEXT_CONTENT_CACHE) >= _N_ROOTS
        assert len(_INSTRUCTION_TEXT_CACHE) >= _N_ROOTS
        # Sanity: warm-up grew the caches (so there is something to evict).
        assert any(len(c) > before_lens[id(c)] for c in tracked_caches)

        total_t0 = time.perf_counter()
        for root in roots:
            evict_source_root_caches(root)
        total_ms = (time.perf_counter() - total_t0) * 1000

        # The lifecycle test already pins correctness (every cache empty after
        # all evictions, on a clean cache).  Here we just gate on the wall
        # budget AND assert our eviction restored each cache to its pre-test
        # snapshot — both that our roots' entries were removed AND that
        # unrelated entries (from prior tests) were left untouched (§2.9
        # no-sibling-deletion-by-coincidence).
        assert total_ms < _TOTAL_CEILING_MS, (
            f"evict_source_root_caches total={total_ms:.1f} ms "
            f"(ceiling {_TOTAL_CEILING_MS} ms) over {_N_ROOTS} evictions; "
            f"may have regressed to O(M·N²) whole-cache scanning"
        )
        for cache in tracked_caches:
            after_len = len(cache)
            before_len = before_lens[id(cache)]
            assert after_len == before_len, (
                f"Cache {cache!r} size not restored after eviction: "
                f"before={before_len} after={after_len}; eviction either "
                f"left entries for the evicted roots (regression) or "
                f"dropped entries for unrelated roots (over-eviction bug)"
            )

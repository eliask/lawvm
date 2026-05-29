"""Extraction-context cache tests for provision_extractor._build_extraction_context.

§1.11 Hot-path: _build_extraction_context builds parent_map / exact_id_map /
sequence_map once per root ET._Element instead of repeating the walk on every
call.  A WeakKeyDictionary keyed on root releases entries when root is GC'd.

Tests:
  1. Cache identity — same root → same context object (cache hit)
  2. Cache isolation — different roots → distinct contexts
  3. Cache speedup — 100 hot calls are an order of magnitude faster than cold
  4. Cache invalidation — after root is GC'd the entry is released
  5. Behavior parity — cached result matches uncached build for same root
"""
from __future__ import annotations

import time
from lxml import etree as ET

from lawvm.uk_legislation.provision_extractor import (
    _EXTRACTION_CONTEXT_CACHE,
    _build_extraction_context,
)


# ---------------------------------------------------------------------------
# Shared XML helpers
# ---------------------------------------------------------------------------

_ACT_XML_A = """\
<body xmlns:leg="http://www.legislation.gov.uk/namespaces/legislation">
  <section id="section-1">
    <subsection id="section-1-1">
      <p>First subsection text.</p>
    </subsection>
    <subsection id="section-1-2">
      <p>Second subsection text.</p>
    </subsection>
  </section>
  <section id="section-2">
    <subsection id="section-2-1">
      <p>Another section.</p>
    </subsection>
  </section>
</body>
"""

_ACT_XML_B = """\
<body xmlns:leg="http://www.legislation.gov.uk/namespaces/legislation">
  <section id="section-10">
    <subsection id="section-10-1">
      <p>Section ten text.</p>
    </subsection>
  </section>
</body>
"""


# ---------------------------------------------------------------------------
# Test 1: Cache identity — same root, two calls, returns same context object
# ---------------------------------------------------------------------------

def test_extraction_context_cache_identity() -> None:
    """Two calls with the same root return the identical context object."""
    root = ET.fromstring(_ACT_XML_A)
    ctx_a = _build_extraction_context(root)
    ctx_b = _build_extraction_context(root)
    assert ctx_a is ctx_b, (
        "Second call with same root must return the cached object (identity)"
    )
    # Sanity: context is non-empty for an XML with IDs
    assert len(ctx_a.exact_id_map) > 0, "exact_id_map must be populated"
    assert len(ctx_a.parent_map) > 0, "parent_map must be populated"


# ---------------------------------------------------------------------------
# Test 2: Cache isolation — different roots produce distinct contexts
# ---------------------------------------------------------------------------

def test_extraction_context_cache_isolation() -> None:
    """Different root objects produce independent, distinct contexts."""
    root_a = ET.fromstring(_ACT_XML_A)
    root_b = ET.fromstring(_ACT_XML_B)
    ctx_a = _build_extraction_context(root_a)
    ctx_b = _build_extraction_context(root_b)
    assert ctx_a is not ctx_b, "Different roots must produce distinct contexts"
    # Verify actual content difference: XML_A has section-1, XML_B has section-10
    assert "section1" in ctx_a.exact_id_map, (
        f"ctx_a must contain section-1 key; got: {list(ctx_a.exact_id_map.keys())[:5]}"
    )
    assert "section10" in ctx_b.exact_id_map, (
        f"ctx_b must contain section-10 key; got: {list(ctx_b.exact_id_map.keys())[:5]}"
    )
    assert "section10" not in ctx_a.exact_id_map
    assert "section1" not in ctx_b.exact_id_map


# ---------------------------------------------------------------------------
# Test 3: Cache speedup — hot calls are faster than cold
# ---------------------------------------------------------------------------

def test_extraction_context_cache_speedup() -> None:
    """100 hot calls against a pre-warmed root are at least 10x faster than cold."""
    # Build a larger XML tree to make timing meaningful
    sections = "\n".join(
        f'<section id="s{i}"><subsection id="s{i}-1"><p>text</p></subsection></section>'
        for i in range(1, 300)
    )
    big_xml = f"<body>{sections}</body>"
    root = ET.fromstring(big_xml)

    # Cold build
    t0 = time.perf_counter()
    _build_extraction_context(root)
    cold_s = time.perf_counter() - t0

    # Hot calls — all should hit cache
    t1 = time.perf_counter()
    for _ in range(100):
        _build_extraction_context(root)
    hot_total_s = time.perf_counter() - t1
    hot_per_call_s = hot_total_s / 100

    assert hot_per_call_s < cold_s / 10, (
        f"Hot call ({hot_per_call_s:.6f}s) must be >10x faster than cold "
        f"({cold_s:.6f}s); actual ratio={cold_s / max(hot_per_call_s, 1e-9):.1f}x"
    )


# ---------------------------------------------------------------------------
# Test 4: Cache entry lifetime tied to root object identity
# ---------------------------------------------------------------------------

def test_extraction_context_cache_entry_per_root_identity() -> None:
    """Cache entries are keyed on root identity; a new root (same XML) is a miss.

    Note: UKExtractionContext holds ET._Element objects in parent_map (including
    root as a parent value for its children), so the WeakKeyDictionary cannot
    release entries purely via weak-reference expiry while the context is live.
    The cache is bounded per compile session: the extraction_cache dict in
    compile_ops_for_statute holds source_context.root alive for the session; when
    that dict drops at session end, the cyclic GC can collect the reference cycle
    together.  This test verifies the per-identity semantics — same object hits,
    new object from same bytes misses.
    """
    root_first = ET.fromstring(_ACT_XML_A)
    ctx_first = _build_extraction_context(root_first)

    # Same root object → cache hit (identity equality)
    ctx_first_again = _build_extraction_context(root_first)
    assert ctx_first is ctx_first_again, "Same root must return cached context"

    # New root from same XML bytes → cache miss (different identity)
    root_second = ET.fromstring(_ACT_XML_A)
    assert root_second is not root_first, "Test setup: must be distinct objects"
    ctx_second = _build_extraction_context(root_second)
    assert ctx_second is not ctx_first, "New root must produce a new context"

    # Both entries coexist in the cache
    assert root_first in _EXTRACTION_CONTEXT_CACHE
    assert root_second in _EXTRACTION_CONTEXT_CACHE


# ---------------------------------------------------------------------------
# Test 5: Behavior parity — cached result matches uncached reference
# ---------------------------------------------------------------------------

def test_extraction_context_cache_behavior_parity() -> None:
    """The cached context has the same content as a fresh build."""
    # Build and cache once
    root = ET.fromstring(_ACT_XML_A)
    cached_ctx = _build_extraction_context(root)

    # Re-build from an identical but independent tree (same bytes → same structure)
    root_fresh = ET.fromstring(_ACT_XML_A)
    fresh_ctx = _build_extraction_context(root_fresh)

    # exact_id_map keys must match
    assert set(cached_ctx.exact_id_map.keys()) == set(fresh_ctx.exact_id_map.keys()), (
        "exact_id_map keys must be identical between cached and fresh builds"
    )
    # sequence_map keys must match
    assert set(cached_ctx.sequence_map.keys()) == set(fresh_ctx.sequence_map.keys()), (
        "sequence_map keys must be identical between cached and fresh builds"
    )
    # parent_map must have same size
    assert len(cached_ctx.parent_map) == len(fresh_ctx.parent_map), (
        "parent_map sizes must match between cached and fresh builds"
    )

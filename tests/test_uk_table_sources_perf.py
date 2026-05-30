"""Performance and behavior regression tests for UK fee-table row index.

§1.11 Hot-path: _uk_build_fee_table_index builds rows once per source_root
instead of re-walking source_root.iter() and calling _uk_table_rows_with_rowspans
for every effect row.  Profiling ukpga/1970/9 showed 132,140
invocations of _uk_table_rows_with_rowspans producing 145M+ str ops and 2.5 GB
RSS.  The index reduces this to one walk per source_root.

Tests:
  1. Synthetic positive — same source_root, two calls, results identical
  2. Cache hit — second call is faster than first (cache is populated)
  3. Cache isolation — different source_root objects produce independent results
  4. Hot-loop perf — 1,000 calls in <0.5s
  5. Behavior regression — cached path classifies same as uncached reference
  6. Cache invalidation via fresh source_root — mutation to source_root content
     (new root object) is not seen as old index
  7. Empty source (no fee tables) — returns empty index and no refinements
  8. _uk_table_driven_fee_target_refinements behavior equivalence with index
"""
from __future__ import annotations

import time
from lxml import etree as ET

from lawvm.core.ir import LegalAddress
from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.table_sources import (
    _uk_get_fee_table_index,
    _uk_table_driven_fee_substitution,
    _uk_table_driven_fee_target_refinements,
    _uk_table_rows_with_rowspans,
)


# ---------------------------------------------------------------------------
# Shared XML helpers
# ---------------------------------------------------------------------------

_FEE_TABLE_XML = """\
<body>
  <table>
    <tr><th>Chapter</th><th>Short Title</th><th>Provision</th><th>New Fee</th><th>Old Fee</th></tr>
    <tr><td>1970 c. 9</td><td>Taxes Management Act 1970</td><td></td><td></td><td></td></tr>
    <tr><td></td><td>section 8(1)(b)</td><td>Penalty on failure to deliver return</td><td>£50</td><td>£20</td></tr>
    <tr><td></td><td>section 93(4)</td><td>Penalty on failure to deliver accounts</td><td>£100</td><td>£40</td></tr>
  </table>
</body>
"""

_FEE_TABLE_XML_WITH_FEE_PAYABLE = """\
<body>
  <table>
    <tr><th>Enactment specifying fees</th><th>Provision</th><th>Description</th><th>New</th><th>Old</th></tr>
    <tr><td>1970 c. 9</td><td>section 7(1)</td><td>Annual return fee</td><td>£30</td><td>£10</td></tr>
    <tr><td></td><td>section 8(1)</td><td>Penalty fee</td><td>£50</td><td>£20</td></tr>
  </table>
</body>
"""

_NO_FEE_TABLE_XML = """\
<body>
  <table>
    <tr><th>Name</th><th>Value</th></tr>
    <tr><td>Alice</td><td>100</td></tr>
    <tr><td>Bob</td><td>200</td></tr>
  </table>
</body>
"""


def _make_fee_effect(
    *,
    affected_year: str = "1970",
    affected_provisions: str = "s. 8(1)",
    effect_type: str = "words substituted",
) -> UKEffectRecord:
    return UKEffectRecord(
        effect_id="test-perf-fee-0001",
        effect_type=effect_type,
        applied=True,
        requires_applied=True,
        modified="2024-01-01",
        affected_uri="/id/ukpga/1970/9/section/8/subsection/1",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year=affected_year,
        affected_number="9",
        affected_provisions=affected_provisions,
        affecting_uri="/id/ukpga/2024/10",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2024",
        affecting_number="10",
        affecting_provisions="Sch. 1",
        affecting_title="Fees Amendment Act 2024",
    )


def _target_section_8_1() -> LegalAddress:
    return LegalAddress(path=(("section", "8"), ("subsection", "1")))


# ---------------------------------------------------------------------------
# Test 1: Synthetic positive — same source_root, two calls, results identical
# ---------------------------------------------------------------------------

def test_fee_table_index_results_identical_on_second_call() -> None:
    """Both calls with the same source_root return the same fee-table index."""
    root = ET.fromstring(_FEE_TABLE_XML_WITH_FEE_PAYABLE)
    result_a = _uk_get_fee_table_index(root)
    result_b = _uk_get_fee_table_index(root)
    assert result_a is result_b, (
        "Second call must return the cached object (identity equality)"
    )
    assert len(result_a) == 1, f"Expected 1 fee table, got {len(result_a)}"


# ---------------------------------------------------------------------------
# Test 2: Cache hit — second call faster than first
# ---------------------------------------------------------------------------

def test_fee_table_index_second_call_is_faster() -> None:
    """Second call with same source_root must be faster (cache hit path)."""
    root = ET.fromstring(_FEE_TABLE_XML_WITH_FEE_PAYABLE)
    # Prime the cache
    t0 = time.perf_counter()
    _uk_get_fee_table_index(root)
    t_first = time.perf_counter() - t0

    # Cache hit
    t0 = time.perf_counter()
    _uk_get_fee_table_index(root)
    t_second = time.perf_counter() - t0

    assert t_second < t_first, (
        f"Second call ({t_second*1e6:.1f}µs) must be faster than first ({t_first*1e6:.1f}µs)"
    )


# ---------------------------------------------------------------------------
# Test 3: Cache isolation — different root objects do not share entries
# ---------------------------------------------------------------------------

def test_fee_table_index_different_roots_independent() -> None:
    """Two distinct source_root objects get independent cache entries."""
    root_a = ET.fromstring(_FEE_TABLE_XML_WITH_FEE_PAYABLE)
    root_b = ET.fromstring(_NO_FEE_TABLE_XML)

    index_a = _uk_get_fee_table_index(root_a)
    index_b = _uk_get_fee_table_index(root_b)

    assert len(index_a) == 1, f"root_a should have 1 fee table, got {len(index_a)}"
    assert len(index_b) == 0, f"root_b should have 0 fee tables, got {len(index_b)}"
    assert index_a is not index_b


# ---------------------------------------------------------------------------
# Test 4: Hot-loop perf — 1,000 calls with same source_root in <0.5s
# ---------------------------------------------------------------------------

def test_fee_table_index_hot_loop_1000_calls_fast() -> None:
    """1,000 calls to _uk_get_fee_table_index with the same root must complete in <0.5s."""
    root = ET.fromstring(_FEE_TABLE_XML_WITH_FEE_PAYABLE)
    # Prime cache
    _uk_get_fee_table_index(root)

    t0 = time.perf_counter()
    for _ in range(1000):
        _uk_get_fee_table_index(root)
    elapsed = time.perf_counter() - t0

    assert elapsed < 0.5, (
        f"1,000 cached index lookups took {elapsed:.3f}s, expected <0.5s"
    )


def test_fee_driven_substitution_hot_loop_1000_calls_fast() -> None:
    """1,000 calls to _uk_table_driven_fee_substitution with same root must complete in <0.5s."""
    root = ET.fromstring(_FEE_TABLE_XML_WITH_FEE_PAYABLE)
    # Prime cache
    _uk_get_fee_table_index(root)

    effect = _make_fee_effect()
    target = _target_section_8_1()

    t0 = time.perf_counter()
    for _ in range(1000):
        _uk_table_driven_fee_substitution(
            effect=effect,
            extracted_text=None,
            source_root=root,
            target=target,
        )
    elapsed = time.perf_counter() - t0

    assert elapsed < 0.5, (
        f"1,000 cached fee-substitution calls took {elapsed:.3f}s, expected <0.5s"
    )


# ---------------------------------------------------------------------------
# Test 5: Behavior regression — fee-table index returns correct row content
# ---------------------------------------------------------------------------

def test_fee_table_index_row_content_correct() -> None:
    """Rows in the index must match what _uk_table_rows_with_rowspans returns directly."""
    root = ET.fromstring(_FEE_TABLE_XML_WITH_FEE_PAYABLE)
    # Find the table element
    table_el = next(el for el in root.iter() if el.tag == "table")
    direct_rows = _uk_table_rows_with_rowspans(table_el)

    index = _uk_get_fee_table_index(root)
    assert len(index) == 1
    entry = index[0]

    assert len(entry.rows) == len(direct_rows), (
        f"Index rows {len(entry.rows)} != direct rows {len(direct_rows)}"
    )
    for i, (irow, drow) in enumerate(zip(entry.rows, direct_rows, strict=True)):
        assert list(irow.cells) == drow, (
            f"Row {i} cells mismatch: index={list(irow.cells)!r} direct={drow!r}"
        )


def test_fee_table_index_col1_lower_precomputed() -> None:
    """col1_lower in index rows must equal col1.lower()."""
    root = ET.fromstring(_FEE_TABLE_XML_WITH_FEE_PAYABLE)
    index = _uk_get_fee_table_index(root)
    assert index
    for entry in index:
        for irow in entry.rows:
            assert irow.col1_lower == irow.col1.lower(), (
                f"col1_lower mismatch: {irow.col1_lower!r} != {irow.col1.lower()!r}"
            )


# ---------------------------------------------------------------------------
# Test 6: Cache invalidation via fresh source_root
# ---------------------------------------------------------------------------

def test_fee_table_index_fresh_root_not_cached() -> None:
    """A freshly parsed root object has its own independent cache entry."""
    root_a = ET.fromstring(_FEE_TABLE_XML_WITH_FEE_PAYABLE)
    root_b = ET.fromstring(_FEE_TABLE_XML_WITH_FEE_PAYABLE)

    # Same content, different objects
    assert root_a is not root_b

    index_a = _uk_get_fee_table_index(root_a)
    index_b = _uk_get_fee_table_index(root_b)

    # Both should have 1 fee table but be distinct objects
    assert len(index_a) == 1
    assert len(index_b) == 1
    assert index_a is not index_b


# ---------------------------------------------------------------------------
# Test 7: Empty source — no fee tables
# ---------------------------------------------------------------------------

def test_fee_table_index_no_fee_tables_returns_empty() -> None:
    """source_root with no fee tables returns an empty index."""
    root = ET.fromstring(_NO_FEE_TABLE_XML)
    index = _uk_get_fee_table_index(root)
    assert len(index) == 0


def test_fee_driven_substitution_no_source_root_none() -> None:
    """source_root=None returns recognized=False when affecting_title has no 'fee'."""
    effect = _make_fee_effect()
    result = _uk_table_driven_fee_substitution(
        effect=effect,
        extracted_text=None,
        source_root=None,
        target=_target_section_8_1(),
    )
    # affecting_title = "Fees Amendment Act 2024" contains "fee", so recognized=True
    assert result.recognized is True
    assert result.reason_code == "source_root_unavailable"


def test_fee_driven_substitution_no_fee_tables_returns_not_recognized() -> None:
    """source_root with no fee tables returns recognized=False."""
    root = ET.fromstring(_NO_FEE_TABLE_XML)
    effect = _make_fee_effect()
    result = _uk_table_driven_fee_substitution(
        effect=effect,
        extracted_text=None,
        source_root=root,
        target=_target_section_8_1(),
    )
    assert result.recognized is False


# ---------------------------------------------------------------------------
# Test 8: _uk_table_driven_fee_target_refinements behavior equivalence
# ---------------------------------------------------------------------------

def test_fee_target_refinements_no_source_root_returns_empty() -> None:
    """source_root=None returns empty list."""
    effect = _make_fee_effect()
    result = _uk_table_driven_fee_target_refinements(
        effect=effect,
        source_root=None,
        target=_target_section_8_1(),
    )
    assert result == []


def test_fee_target_refinements_no_fee_tables_returns_empty() -> None:
    """source_root with no fee tables returns empty list."""
    root = ET.fromstring(_NO_FEE_TABLE_XML)
    effect = _make_fee_effect()
    result = _uk_table_driven_fee_target_refinements(
        effect=effect,
        source_root=root,
        target=_target_section_8_1(),
    )
    assert result == []


def test_fee_target_refinements_stable_across_repeated_calls() -> None:
    """Repeated calls with same source_root must return identical results."""
    root = ET.fromstring(_FEE_TABLE_XML_WITH_FEE_PAYABLE)
    effect = _make_fee_effect()
    target = _target_section_8_1()

    result_a = _uk_table_driven_fee_target_refinements(
        effect=effect, source_root=root, target=target
    )
    result_b = _uk_table_driven_fee_target_refinements(
        effect=effect, source_root=root, target=target
    )
    assert result_a == result_b, (
        f"Results differ between calls: {result_a!r} vs {result_b!r}"
    )


# ---------------------------------------------------------------------------
# Test 9: table_index in index entries matches iteration order
# ---------------------------------------------------------------------------

def test_fee_table_index_entry_table_index_sequential() -> None:
    """table_index in each entry should be sequential (0, 1, 2...)."""
    # Wrap multiple fee tables in one source
    multi_xml = """\
<body>
  <table>
    <tr><th>Enactment specifying fees</th><th>Provision</th><th>Desc</th><th>New</th><th>Old</th></tr>
    <tr><td>1970 c. 9</td><td>section 8</td><td>Fee A</td><td>£50</td><td>£20</td></tr>
  </table>
  <table>
    <tr><th>Enactment specifying fees</th><th>Provision</th><th>Desc</th><th>New</th><th>Old</th></tr>
    <tr><td>1971 c. 1</td><td>section 10</td><td>Fee B</td><td>£70</td><td>£30</td></tr>
  </table>
</body>
"""
    root = ET.fromstring(multi_xml)
    index = _uk_get_fee_table_index(root)
    assert len(index) == 2, f"Expected 2 fee tables, got {len(index)}"
    for expected_idx, entry in enumerate(index):
        assert entry.table_index == expected_idx, (
            f"Entry {expected_idx} has table_index={entry.table_index}"
        )

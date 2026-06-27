"""Tests for the unified cross-jurisdiction benchmark contract."""

from __future__ import annotations

import pytest

from lawvm.core.bench_aggregate import (
    BenchDistribution,
    aggregate_residue_buckets,
    append_history,
    check_all_reconcile,
    compute_distribution,
    find_regressions,
    format_error_pct,
    load_history,
    partition_by_status,
    render_summary,
)
from lawvm.core.bench_comparator_registry import (
    get_bench_comparator,
    has_bench_comparator,
    register_bench_comparator,
    registered_jurisdictions,
    run_bench_comparator,
)
from lawvm.core.bench_contract import (
    BenchContractError,
    BenchStatus,
    BenchUnitResult,
    NON_SCORED_STATUSES,
    check_residue_reconciliation,
    headline_error,
    residue_reconciliation_violation,
)


def _scored(unit_id, structural_err=None, text_err=None, residue=None):
    return BenchUnitResult(
        unit_id=unit_id,
        bench_unit_status=BenchStatus.SCORED,
        structural_err=structural_err,
        text_err=text_err,
        residue_buckets=residue or {},
    )


# ---------------------------------------------------------------------------
# Axis validation + status invariants
# ---------------------------------------------------------------------------


def test_axis_must_be_in_unit_interval() -> None:
    with pytest.raises(BenchContractError):
        _scored("x", structural_err=1.5, residue={"k": 1})
    with pytest.raises(BenchContractError):
        _scored("x", structural_err=-0.1)


def test_none_axis_is_allowed_not_attempted() -> None:
    r = _scored("x", structural_err=None, text_err=0.0)
    assert r.structural_err is None
    assert r.attempted_axes == (0.0,)


def test_non_scored_unit_may_not_carry_axis_errors() -> None:
    for status in NON_SCORED_STATUSES | {BenchStatus.CRASH}:
        with pytest.raises(BenchContractError):
            BenchUnitResult(unit_id="x", bench_unit_status=status, structural_err=0.0)


def test_non_scored_unit_with_no_axes_is_valid() -> None:
    r = BenchUnitResult(unit_id="x", bench_unit_status=BenchStatus.NO_TRUTH)
    assert not r.is_scored
    assert not r.is_failure
    assert r.headline_error() is None
    assert r.headline_accuracy() is None


def test_crash_is_failure_other_nonscored_are_not() -> None:
    assert BenchUnitResult("x", BenchStatus.CRASH).is_failure
    assert not BenchUnitResult("x", BenchStatus.NO_TRUTH).is_failure
    assert not BenchUnitResult("x", BenchStatus.ORACLE_STALE).is_failure


# ---------------------------------------------------------------------------
# Worst-of headline
# ---------------------------------------------------------------------------


def test_headline_is_worst_of_axes() -> None:
    # structurally perfect but textually wrong is NOT "half right".
    r = _scored("x", structural_err=0.0, text_err=0.4)
    assert r.headline_error() == 0.4
    assert headline_error(r) == 0.4
    assert r.headline_accuracy() == pytest.approx(0.6)


def test_headline_uses_max_of_both_axes() -> None:
    r = _scored("x", structural_err=0.3, text_err=0.1, residue={"k": 1})
    assert r.headline_error() == 0.3


def test_headline_ignores_unattempted_axis() -> None:
    r = _scored("x", structural_err=0.2, text_err=None, residue={"k": 1})
    assert r.headline_error() == 0.2


def test_headline_none_when_no_axis_attempted() -> None:
    r = _scored("x")
    assert r.headline_error() is None


# ---------------------------------------------------------------------------
# Residue reconciliation invariant
# ---------------------------------------------------------------------------


def test_positive_error_requires_residue() -> None:
    bad = _scored("x", structural_err=0.5, residue={})
    with pytest.raises(BenchContractError):
        check_residue_reconciliation(bad)
    assert residue_reconciliation_violation(bad) is not None


def test_zero_error_forbids_phantom_residue() -> None:
    bad = _scored("x", structural_err=0.0, residue={"unit_missing_left": 2})
    with pytest.raises(BenchContractError):
        check_residue_reconciliation(bad)


def test_reconciled_scored_unit_passes() -> None:
    ok = _scored("x", structural_err=0.5, residue={"unit_missing_left": 3})
    check_residue_reconciliation(ok)  # no raise
    assert residue_reconciliation_violation(ok) is None
    perfect = _scored("y", structural_err=0.0, residue={})
    check_residue_reconciliation(perfect)


def test_text_axis_not_required_to_reconcile() -> None:
    # text_err > 0 with no residue is fine — text axis is continuous, not typed.
    r = _scored("x", structural_err=0.0, text_err=0.3, residue={})
    check_residue_reconciliation(r)


def test_reconciliation_skips_nonscored() -> None:
    check_residue_reconciliation(BenchUnitResult("x", BenchStatus.NO_TRUTH))


def test_reconciliation_skips_when_no_structural_axis() -> None:
    # EE-style: structural axis present but US-style count-only could be None.
    check_residue_reconciliation(_scored("x", structural_err=None, text_err=0.2))


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def test_distribution_matches_fi_bucket_semantics() -> None:
    # Accuracies 1.0, 0.995, 0.96, 0.8 over 4 scored units; one crash.
    results = [
        _scored("a", structural_err=0.0),  # acc 1.0
        _scored("b", structural_err=0.005, residue={"k": 1}),  # acc 0.995
        _scored("c", structural_err=0.04, residue={"k": 1}),  # acc 0.96
        _scored("d", structural_err=0.2, residue={"k": 1}),  # acc 0.80
        BenchUnitResult("e", BenchStatus.CRASH),
    ]
    dist = compute_distribution(results)
    assert dist.n == 4
    assert dist.perfect == 1
    assert dist.above_99 == 2  # 1.0, 0.995
    assert dist.above_95 == 3  # 1.0, 0.995, 0.96
    assert dist.below_90 == 1  # 0.80
    assert dist.errors == 1  # the crash
    assert dist.mean == pytest.approx((1.0 + 0.995 + 0.96 + 0.80) / 4)


def test_distribution_empty_when_no_scored() -> None:
    dist = compute_distribution([BenchUnitResult("x", BenchStatus.NO_TRUTH)])
    assert dist == BenchDistribution(0.0, 0, 0, 0, 0, 0, 1)


def test_distribution_uses_worst_of_for_buckets() -> None:
    # structurally perfect but textually 0.5 wrong -> accuracy 0.5 -> below_90.
    results = [_scored("a", structural_err=0.0, text_err=0.5)]
    dist = compute_distribution(results)
    assert dist.below_90 == 1
    assert dist.perfect == 0


def test_partition_by_status() -> None:
    results = [
        _scored("a", structural_err=0.0),
        BenchUnitResult("b", BenchStatus.NO_TRUTH),
        BenchUnitResult("c", BenchStatus.SOURCE_UNAVAILABLE),
        BenchUnitResult("d", BenchStatus.CRASH),
    ]
    scored, non_scored, crashed = partition_by_status(results)
    assert [r.unit_id for r in scored] == ["a"]
    assert {r.unit_id for r in non_scored} == {"b", "c"}
    assert [r.unit_id for r in crashed] == ["d"]


def test_format_error_pct() -> None:
    assert format_error_pct(1.0) == "0.00%"
    assert format_error_pct(0.9) == "10.00%"
    assert format_error_pct(None) == "n/a"


def test_aggregate_residue_and_check_all_reconcile() -> None:
    results = [
        _scored("a", structural_err=0.5, residue={"unit_missing_left": 2, "x": 1}),
        _scored("b", structural_err=0.0, residue={}),
    ]
    totals = aggregate_residue_buckets(results)
    assert totals["unit_missing_left"] == 2
    assert totals["x"] == 1
    assert check_all_reconcile(results) == []


def test_check_all_reconcile_reports_violations() -> None:
    results = [_scored("bad", structural_err=0.5, residue={})]
    violations = check_all_reconcile(results)
    assert len(violations) == 1
    assert "bad" in violations[0]


# ---------------------------------------------------------------------------
# Shared summary renderer
# ---------------------------------------------------------------------------


def test_render_summary_reports_counts_and_reconciliation_ok() -> None:
    results = [
        _scored("a", structural_err=0.0),
        _scored("b", structural_err=0.2, residue={"k": 1}),
        BenchUnitResult("c", BenchStatus.NO_TRUTH),
        BenchUnitResult("d", BenchStatus.CRASH),
    ]
    lines = render_summary(results, "v1", jurisdiction="fi")
    text = "\n".join(lines)
    assert "jurisdiction=fi" in text
    assert "2 scored" in text
    assert "crashed: 1" in text
    assert "excluded(non-scored): 1" in text
    assert "worst-of axes" in text
    assert "Residue reconciliation: OK" in text


def test_render_summary_structural_only_axis_binds_headline_no_text_line() -> None:
    """Jurisdictions with no text axis (EE/US: text_err=None) render correctly.

    The worst-of headline binds on the structural axis alone, and render_summary
    emits no spurious text-axis line for an axis the jurisdiction never attempts.
    """
    results = [
        _scored("a", structural_err=0.0, text_err=None),
        _scored("b", structural_err=0.25, text_err=None, residue={"section_mismatch": 1}),
    ]
    lines = render_summary(results, "v1", jurisdiction="ee")
    text = "\n".join(lines)
    # Mean accuracy = (1.0 + 0.75) / 2 = 0.875 -> mean error 12.50%.
    assert "Mean error : 12.50%" in text
    assert "2 scored" in text
    assert "Residue reconciliation: OK" in text
    # No spurious per-axis text line for an unattempted axis.
    assert "text" not in text.lower()


def test_render_summary_flags_reconciliation_violation() -> None:
    # A unit that violates the invariant must surface, not hide.
    bad = BenchUnitResult(
        "bad", BenchStatus.SCORED, structural_err=0.5, residue_buckets={}
    )
    lines = render_summary([bad], "v1")
    text = "\n".join(lines)
    assert "VIOLATION" in text
    assert "bad" in text


# ---------------------------------------------------------------------------
# History + regression guard
# ---------------------------------------------------------------------------


def test_history_roundtrip(tmp_path) -> None:
    path = tmp_path / "history.csv"
    dist = compute_distribution([_scored("a", structural_err=0.0)])
    append_history(path, "2026-06-20T00:00:00Z", "v1", dist)
    append_history(path, "2026-06-20T01:00:00Z", "v2", dist)
    rows = load_history(path)
    assert len(rows) == 2
    assert rows[0]["label"] == "v1"
    assert rows[0]["mean_score"] == "1.0000"
    assert rows[0]["n_statutes"] == "1"


def test_find_regressions_sorted_worst_first() -> None:
    prev = {"a": 1.0, "b": 0.9, "c": 0.8}
    curr = {"a": 0.5, "b": 0.8995, "c": 0.95}  # a regressed 0.5, b within tol, c improved
    regs = find_regressions(prev, curr, tolerance=0.001)
    assert [r.unit_id for r in regs] == ["a"]
    assert regs[0].delta == pytest.approx(-0.5)


def test_find_regressions_requires_both_runs() -> None:
    regs = find_regressions({"a": 1.0}, {"b": 0.0})
    assert regs == []


# ---------------------------------------------------------------------------
# Comparator registry
# ---------------------------------------------------------------------------


def test_comparator_registry_dispatch() -> None:
    from lawvm.core import bench_comparator_registry

    def comparator(unit_id: str) -> BenchUnitResult:
        return _scored(unit_id, structural_err=0.0)

    try:
        register_bench_comparator("testjuris", comparator)
        assert has_bench_comparator("testjuris")
        assert "testjuris" in registered_jurisdictions()
        assert get_bench_comparator("testjuris") is comparator
        result = run_bench_comparator("testjuris", "u1")
        assert result.unit_id == "u1"
    finally:
        bench_comparator_registry._COMPARATORS.pop("testjuris", None)


def test_get_unregistered_comparator_fails_loud() -> None:
    with pytest.raises(KeyError):
        get_bench_comparator("nonexistent_juris_xyz")


def test_run_comparator_validates_return_type() -> None:
    from lawvm.core import bench_comparator_registry

    # Deliberately register a comparator with the wrong return type to exercise
    # run_bench_comparator's runtime type guard.
    try:
        register_bench_comparator("badjuris", lambda: "not a result")  # ty: ignore[invalid-argument-type]
        with pytest.raises(TypeError):
            run_bench_comparator("badjuris")
    finally:
        bench_comparator_registry._COMPARATORS.pop("badjuris", None)

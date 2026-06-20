"""UK / EE / NZ / US bench comparators → unified contract BenchUnitResult."""

from __future__ import annotations

import pytest

from lawvm.core.bench_comparator_registry import has_bench_comparator
from lawvm.core.bench_contract import (
    BenchStatus,
    check_residue_reconciliation,
)


# ---------------------------------------------------------------------------
# UK
# ---------------------------------------------------------------------------


def _uk_result(**kw):
    from lawvm.tools import uk_bench

    base = dict(
        statute_id="ukpga/2000/1",
        act_type="ukpga",
        year=2000,
        n_effects=5,
        n_enacted_eids=10,
        n_oracle_eids=12,
        n_common=8,
        score=8 / 12,
        status="OK",
    )
    base.update(kw)
    return uk_bench._BenchResult(**base)


def test_uk_registered() -> None:
    from lawvm.tools import uk_bench  # noqa: F401  (import triggers registration)

    assert has_bench_comparator("uk")


def test_uk_scored_axes_and_residue() -> None:
    from lawvm.tools import uk_bench

    r = uk_bench.uk_bench_unit_result(_uk_result(text_score=0.9))
    assert r.status is BenchStatus.SCORED
    assert r.structural_err == pytest.approx(1 - 8 / 12)
    assert r.text_err == pytest.approx(0.1)
    assert dict(r.residue_buckets) == {"eid_only_in_enacted": 2, "eid_only_in_oracle": 4}
    check_residue_reconciliation(r)


def test_uk_perfect_no_residue() -> None:
    from lawvm.tools import uk_bench

    r = uk_bench.uk_bench_unit_result(
        _uk_result(n_enacted_eids=5, n_oracle_eids=5, n_common=5, score=1.0, text_score=1.0)
    )
    assert r.structural_err == 0.0
    assert dict(r.residue_buckets) == {}
    check_residue_reconciliation(r)


def test_uk_no_text_score_leaves_axis_unattempted() -> None:
    from lawvm.tools import uk_bench

    r = uk_bench.uk_bench_unit_result(_uk_result(text_score=-1.0))
    assert r.text_err is None


def test_uk_no_oracle_is_non_scored() -> None:
    from lawvm.tools import uk_bench

    r = uk_bench.uk_bench_unit_result(_uk_result(status="NO_ORACLE"))
    assert r.status is BenchStatus.NO_TRUTH


def test_uk_err_is_crash() -> None:
    from lawvm.tools import uk_bench

    r = uk_bench.uk_bench_unit_result(_uk_result(status="ERR", error="boom"))
    assert r.status is BenchStatus.CRASH
    assert r.witnesses == ("boom",)


# ---------------------------------------------------------------------------
# EE
# ---------------------------------------------------------------------------


def _ee_result(**kw):
    from lawvm.tools import ee_bench

    base = dict(
        grupi_id="g",
        base_id="b",
        oracle_id="o",
        title="t",
        n_ops=2,
        n_divs=3,
        sec_match=0.8,
        r_secs=10,
        o_secs=10,
        status="OK",
    )
    base.update(kw)
    return ee_bench._BenchResult(**base)


def test_ee_registered() -> None:
    from lawvm.tools import ee_bench  # noqa: F401  (import triggers registration)

    assert has_bench_comparator("ee")


def test_ee_scored_no_text_axis() -> None:
    from lawvm.tools import ee_bench

    r = ee_bench.ee_bench_unit_result(_ee_result(sec_match=0.8, o_secs=10))
    assert r.status is BenchStatus.SCORED
    assert r.structural_err == pytest.approx(0.2)
    assert r.text_err is None
    assert dict(r.residue_buckets) == {"section_mismatch": 2}
    check_residue_reconciliation(r)


def test_ee_perfect_no_residue() -> None:
    from lawvm.tools import ee_bench

    r = ee_bench.ee_bench_unit_result(_ee_result(sec_match=1.0, o_secs=5))
    assert r.structural_err == 0.0
    assert dict(r.residue_buckets) == {}
    check_residue_reconciliation(r)


def test_ee_empty_oracle_is_non_scored() -> None:
    from lawvm.tools import ee_bench

    r = ee_bench.ee_bench_unit_result(_ee_result(status="EMPTY_ORACLE", o_secs=0, sec_match=0.0))
    assert r.status is BenchStatus.NO_TRUTH


def test_ee_exception_is_crash() -> None:
    from lawvm.tools import ee_bench

    r = ee_bench.ee_bench_unit_result(_ee_result(status="EXC:boom", o_secs=0, sec_match=0.0))
    assert r.status is BenchStatus.CRASH
    assert r.is_failure
    assert r.witnesses == ("EXC:boom",)


# ---------------------------------------------------------------------------
# NZ
# ---------------------------------------------------------------------------


def _nz_result(**kw):
    from lawvm.tools import nz_bench

    base = dict(
        work_id="w1",
        families=(),
        status="OK",
        transitions_replayed=1,
        transitions_refused=0,
        ops_replayed=1,
        slice_nodes=10,
        slice_agreements=8,
        all_slices_agree=False,
        refusals_verification_failed=0,
        refusals_refusal_blocked=0,
        families_not_attempted=0,
        would_replay_if_refusals_ignored=0,
        text_similarity=0.9,
        tree_similarity=0.7,
        tree_similarity_stable=0.7,
        residual_family_counts={"temporal_mismatch": 2},
    )
    base.update(kw)
    return nz_bench._WorkResult(**base)


def test_nz_registered() -> None:
    from lawvm.tools import nz_bench  # noqa: F401  (import triggers registration)

    assert has_bench_comparator("nz")


def test_nz_dual_axes_and_residue() -> None:
    from lawvm.tools import nz_bench

    r = nz_bench.nz_bench_unit_result(_nz_result())
    assert r.status is BenchStatus.SCORED
    assert r.structural_err == pytest.approx(0.2)
    assert r.text_err == pytest.approx(0.1)
    assert r.residue_buckets["slice_disagreement"] == 2
    assert r.residue_buckets["oracle_temporal_mismatch"] == 2
    check_residue_reconciliation(r)


def test_nz_perfect_excludes_oracle_families() -> None:
    from lawvm.tools import nz_bench

    r = nz_bench.nz_bench_unit_result(
        _nz_result(slice_agreements=10, text_similarity=1.0, residual_family_counts={"agreement": 10})
    )
    assert r.structural_err == 0.0
    assert dict(r.residue_buckets) == {}
    check_residue_reconciliation(r)


def test_nz_no_slice_nodes_is_non_scored() -> None:
    from lawvm.tools import nz_bench

    r = nz_bench.nz_bench_unit_result(_nz_result(slice_nodes=0))
    assert r.status is BenchStatus.NO_TRUTH


def test_nz_exception_is_crash() -> None:
    from lawvm.tools import nz_bench

    r = nz_bench.nz_bench_unit_result(_nz_result(status="EXC:boom"))
    assert r.status is BenchStatus.CRASH
    assert r.is_failure


# ---------------------------------------------------------------------------
# US
# ---------------------------------------------------------------------------


def _us_window():
    from lawvm.us_federal import bench as us_bench

    return us_bench.BenchWindow(
        title=11,
        before_year=2018,
        after_year=2020,
        include=True,
        window_law_count=1,
        prior_edition_years=(),
        note="",
    )


def _us_result(**kw):
    from lawvm.us_federal import bench as us_bench

    base = dict(
        window=_us_window(),
        status="evaluated",
        oracle_changed=10,
        agreements=7,
        lawvm_wrong=2,
        oracle_suspect=1,
        missing_source=0,
        sunset_reversion=0,
        refusals=0,
    )
    base.update(kw)
    return us_bench.WindowResult(**base)


def test_us_registered() -> None:
    from lawvm.us_federal import bench as us_bench  # noqa: F401  (import triggers registration)

    assert has_bench_comparator("us")


def test_us_scored_count_based_no_text_axis() -> None:
    from lawvm.us_federal import bench as us_bench

    r = us_bench.us_bench_unit_result(_us_result())
    assert r.status is BenchStatus.SCORED
    assert r.structural_err == pytest.approx(0.3)
    assert r.text_err is None
    assert dict(r.residue_buckets) == {"lawvm_wrong": 2, "oracle_suspect": 1}
    check_residue_reconciliation(r)


def test_us_perfect_no_residue() -> None:
    from lawvm.us_federal import bench as us_bench

    r = us_bench.us_bench_unit_result(
        _us_result(agreements=10, lawvm_wrong=0, oracle_suspect=0)
    )
    assert r.structural_err == 0.0
    assert dict(r.residue_buckets) == {}
    check_residue_reconciliation(r)


def test_us_unaccounted_non_agreement_is_typed() -> None:
    from lawvm.us_federal import bench as us_bench

    r = us_bench.us_bench_unit_result(
        _us_result(oracle_changed=10, agreements=5, lawvm_wrong=2, oracle_suspect=0)
    )
    assert r.structural_err == pytest.approx(0.5)
    assert r.residue_buckets["lawvm_wrong"] == 2
    assert r.residue_buckets["unclassified_non_agreement"] == 3
    check_residue_reconciliation(r)


def test_us_skipped_is_non_scored() -> None:
    from lawvm.us_federal import bench as us_bench

    r = us_bench.us_bench_unit_result(_us_result(status="skipped"))
    assert r.status is BenchStatus.NO_TRUTH
    assert not r.is_failure


def test_us_no_oracle_change_is_non_scored() -> None:
    from lawvm.us_federal import bench as us_bench

    r = us_bench.us_bench_unit_result(_us_result(oracle_changed=0, agreements=0))
    assert r.status is BenchStatus.NO_TRUTH

from __future__ import annotations

import os
from pathlib import Path

import pytest

from lawvm.new_zealand.dry_run_north_star import (
    NZDryRunNorthStarReport,
    NZWorkNorthStarCensus,
    build_nz_dry_run_north_star_report,
)


def _census(
    work_id: str,
    *,
    history: dict[str, int],
    agreeing: dict[str, tuple[str, ...]] | None = None,
) -> NZWorkNorthStarCensus:
    return NZWorkNorthStarCensus(
        work_id=work_id,
        history_family_counts=dict(history),
        agreeing_witness_row_ids={"repeal": (), "text_replace": (), **(agreeing or {})},
    )


def test_denominator_is_history_witness_count_partitioned_into_pinned_buckets() -> None:
    # A single work whose history notes span every bucket. The denominator is the
    # ground-truth witness count, NOT any candidate-derived count.
    work = _census(
        "act_public_2010_1",
        history={
            "repealed": 10,
            "amended": 20,
            "inserted": 5,
            "added": 3,
            "replaced": 4,
            "substituted": 2,
            "brought into force": 1,
            "editorial change": 6,
            "expired": 1,
            "__missing__": 2,
            "__unclassified__": 1,
        },
        agreeing={"repeal": ("nz-opw-1", "nz-opw-2"), "text_replace": ("nz-opw-3",)},
    )
    report = NZDryRunNorthStarReport(
        db_path="data/nz_legislation.farchive",
        work_censuses=(work,),
        selected_work_ids=("act_public_2010_1",),
    )
    summary = report.summary()

    # Total denominator universe = sum of all history-note witnesses.
    assert summary["total_amendment_operation_witnesses"] == 55
    # Supported = repealed + amended.
    assert summary["supported_family_witnesses"] == 30
    # Frontier = inserted + added + replaced + substituted.
    assert summary["remaining_frontier_witnesses"] == 14
    # Non-executable-by-design is a separate bucket, NOT a coverage miss.
    assert summary["non_executable_by_design_witnesses"] == 8
    # Unclassified is its own bucket too.
    assert summary["unclassified_witnesses"] == 3
    # The four buckets partition the universe exactly (exhaustive, disjoint).
    assert (
        summary["supported_family_witnesses"]
        + summary["remaining_frontier_witnesses"]
        + summary["non_executable_by_design_witnesses"]
        + summary["unclassified_witnesses"]
        == summary["total_amendment_operation_witnesses"]
    )


def test_per_family_coverage_is_agreeing_over_pinned_denominator() -> None:
    work = _census(
        "act_public_2010_1",
        history={"repealed": 10, "amended": 20},
        agreeing={"repeal": ("nz-opw-1", "nz-opw-2"), "text_replace": ("nz-opw-3",)},
    )
    report = NZDryRunNorthStarReport(
        db_path="data/nz_legislation.farchive",
        work_censuses=(work,),
        selected_work_ids=("act_public_2010_1",),
    )
    per_family = report.summary()["per_family"]

    # repeal: 2 agreeing of 10 repealed history witnesses.
    assert per_family["repeal"]["operation_witnesses"] == 10
    assert per_family["repeal"]["dry_run_agreeing"] == 2
    assert per_family["repeal"]["coverage_fraction"] == pytest.approx(2 / 10)
    assert per_family["repeal"]["history_families"] == ["repealed"]

    # text_replace: 1 agreeing of 20 amended history witnesses.
    assert per_family["text_replace"]["operation_witnesses"] == 20
    assert per_family["text_replace"]["dry_run_agreeing"] == 1
    assert per_family["text_replace"]["coverage_fraction"] == pytest.approx(1 / 20)
    assert per_family["text_replace"]["history_families"] == ["amended"]


def test_combined_north_star_is_supported_agreeing_over_supported_total() -> None:
    work = _census(
        "act_public_2010_1",
        history={"repealed": 10, "amended": 20, "inserted": 100},
        agreeing={"repeal": ("nz-opw-1", "nz-opw-2"), "text_replace": ("nz-opw-3",)},
    )
    report = NZDryRunNorthStarReport(
        db_path="data/nz_legislation.farchive",
        work_censuses=(work,),
        selected_work_ids=("act_public_2010_1",),
    )
    summary = report.summary()

    # The north-star numerator/denominator only count SUPPORTED families. The
    # frontier (inserted=100) does NOT dilute the supported denominator; it is
    # reported separately as the remaining frontier.
    assert summary["supported_family_dry_run_agreeing"] == 3
    assert summary["supported_family_witnesses"] == 30
    assert summary["combined_coverage_fraction"] == pytest.approx(3 / 30)
    assert summary["remaining_frontier_witnesses"] == 100


def test_denominator_is_stable_under_candidate_extraction_growth() -> None:
    # The integrity guarantee: when extraction improves so that MORE witnesses
    # agree (numerator rises) the denominator does NOT move, so the fraction
    # rises monotonically. This is the cross-cycle north-star property the
    # candidate-derived denominator violated (45->84 growth dropped 0.60->0.51).
    history = {"repealed": 100, "amended": 200}

    cycle_n = NZDryRunNorthStarReport(
        db_path="data/nz_legislation.farchive",
        work_censuses=(
            _census(
                "w",
                history=history,
                agreeing={
                    "repeal": tuple(f"nz-opw-{i}" for i in range(20)),
                    "text_replace": tuple(f"nz-opw-{1000 + i}" for i in range(27)),
                },
            ),
        ),
    )
    cycle_n_plus_1 = NZDryRunNorthStarReport(
        db_path="data/nz_legislation.farchive",
        work_censuses=(
            _census(
                "w",
                history=history,  # identical ground truth
                agreeing={
                    "repeal": tuple(f"nz-opw-{i}" for i in range(25)),
                    "text_replace": tuple(f"nz-opw-{1000 + i}" for i in range(43)),
                },
            ),
        ),
    )
    s_n = cycle_n.summary()
    s_n1 = cycle_n_plus_1.summary()

    # Denominator pinned: identical across cycles despite more agreeing.
    assert s_n["supported_family_witnesses"] == s_n1["supported_family_witnesses"] == 300
    assert s_n["per_family"]["text_replace"]["operation_witnesses"] == 200
    assert s_n1["per_family"]["text_replace"]["operation_witnesses"] == 200
    # Numerator rose, so the fraction rose (monotone progress).
    assert s_n1["combined_coverage_fraction"] > s_n["combined_coverage_fraction"]
    assert s_n1["per_family"]["text_replace"]["coverage_fraction"] == pytest.approx(43 / 200)


def test_non_executable_bucket_is_separated_not_a_coverage_miss() -> None:
    # A work whose history is entirely non-executable-by-design contributes ZERO
    # to the supported denominator (so coverage is undefined, not 0/N), and the
    # non-executable count is reported as its own bucket.
    work = _census(
        "act_public_2010_1",
        history={"brought into force": 3, "editorial change": 2, "expired": 1},
    )
    report = NZDryRunNorthStarReport(
        db_path="data/nz_legislation.farchive",
        work_censuses=(work,),
        selected_work_ids=("act_public_2010_1",),
    )
    summary = report.summary()
    assert summary["non_executable_by_design_witnesses"] == 6
    assert summary["non_executable_family_counts"] == {
        "brought into force": 3,
        "editorial change": 2,
        "expired": 1,
    }
    # No supported witnesses -> the combined fraction is unavailable, not 0.0
    # (so the non-executable bucket can never be confused with a coverage miss).
    assert summary["supported_family_witnesses"] == 0
    assert summary["combined_coverage_fraction"] is None


def test_remaining_frontier_breakdown_orders_next_family_to_build() -> None:
    work = _census(
        "act_public_2010_1",
        history={
            "repealed": 5,
            "inserted": 1400,
            "replaced": 350,
            "substituted": 190,
            "added": 60,
        },
    )
    report = NZDryRunNorthStarReport(
        db_path="data/nz_legislation.farchive",
        work_censuses=(work,),
        selected_work_ids=("act_public_2010_1",),
    )
    counts = report.summary()["remaining_frontier_family_counts"]
    # Sorted by key (deterministic); the largest witness count orders cycle 4.
    assert counts == {"added": 60, "inserted": 1400, "replaced": 350, "substituted": 190}
    assert max(counts, key=lambda family: counts[family]) == "inserted"


def test_aggregation_sums_across_works_and_dedupes_witness_rows() -> None:
    work_a = _census(
        "act_public_2010_1",
        history={"repealed": 4, "amended": 6},
        agreeing={"repeal": ("nz-opw-1", "nz-opw-1", "nz-opw-2")},  # duplicate id
    )
    work_b = _census(
        "act_public_2011_2",
        history={"repealed": 3, "amended": 9},
        agreeing={"text_replace": ("nz-opw-5",)},
    )
    report = NZDryRunNorthStarReport(
        db_path="data/nz_legislation.farchive",
        work_censuses=(work_a, work_b),
        selected_work_ids=("act_public_2010_1", "act_public_2011_2"),
    )
    summary = report.summary()
    # Denominators sum across works.
    assert summary["per_family"]["repeal"]["operation_witnesses"] == 7
    assert summary["per_family"]["text_replace"]["operation_witnesses"] == 15
    # Numerator dedupes witness rows: work_a repeal agreeing = 2 distinct, not 3.
    assert summary["per_family"]["repeal"]["dry_run_agreeing"] == 2
    assert summary["per_family"]["text_replace"]["dry_run_agreeing"] == 1
    assert summary["supported_family_dry_run_agreeing"] == 3


def test_measurement_only_never_claims_replay() -> None:
    report = NZDryRunNorthStarReport(
        db_path="data/nz_legislation.farchive",
        work_censuses=(_census("act_public_2010_1", history={"repealed": 1}),),
    )
    summary = report.summary()
    assert summary["replay_claims"] is False
    assert summary["actual_replay_agreements"] == 0
    assert summary["dry_run_claims"] is True
    jsonable = report.to_jsonable()
    assert jsonable["replay_claims"] is False
    assert jsonable["report_kind"] == "dry_run_north_star"


_REAL_DB = (
    Path(os.environ.get("LAWVM_CANONICAL_DATA_ROOT", "<DATA_ROOT>"))
    / "data"
    / "nz_legislation.farchive"
)


@pytest.mark.skipif(not _REAL_DB.exists(), reason="archived NZ farchive not present")
def test_north_star_over_real_work_matches_pinned_ground_truth() -> None:
    # The pinned denominator must equal the operation surface's ground-truth
    # family counts, and the agreeing numerator must be bounded by it.
    from lawvm.new_zealand.operation_surface import build_archived_work_operation_surface

    work_id = "act_public_2005_87"
    report = build_nz_dry_run_north_star_report(_REAL_DB, work_ids=(work_id,))
    summary = report.summary()

    surface = build_archived_work_operation_surface(_REAL_DB, work_id)
    repealed = sum(1 for row in surface.rows if row.operation_family == "repealed")
    amended = sum(1 for row in surface.rows if row.operation_family == "amended")

    assert summary["per_family"]["repeal"]["operation_witnesses"] == repealed
    assert summary["per_family"]["text_replace"]["operation_witnesses"] == amended
    # Numerator is bounded by the pinned denominator (no over-count).
    assert summary["per_family"]["repeal"]["dry_run_agreeing"] <= repealed
    assert summary["per_family"]["text_replace"]["dry_run_agreeing"] <= amended
    assert summary["replay_claims"] is False

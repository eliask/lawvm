"""Tests for the NZ all-families chain-replay corpus aggregator.

Pure-logic tests build synthetic per-work results so the distribution
arithmetic, histogram binning, skip-cap census tallying, per-family
aggregation, and error/no-final accounting are exercised without the archive.
A small ``_REAL_DB``-gated integration block proves the parallel runner
aggregates real multi-work output deterministically and discloses its slice.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from lawvm.new_zealand.chain_replay_corpus import (
    DEFAULT_WORKERS,
    NZChainReplayCorpusReport,
    NZChainReplayWorkResult,
    _histogram,
    _percentile_sorted,
    aggregate_per_family,
    build_nz_chain_replay_corpus_report,
    summarize_distribution,
    tally_skip_caps,
)


def _result(
    work_id: str,
    *,
    stable: float | None,
    raw: float | None = None,
    shared_mean: float | None = None,
    per_family: dict[str, tuple[int, int, int, int, int]] | None = None,
    skips: dict[str, int] | None = None,
    n_divergences: int = 0,
    error: str = "",
) -> NZChainReplayWorkResult:
    return NZChainReplayWorkResult(
        work_id=work_id,
        families_requested=("repeal", "text_replace", "replace", "insert"),
        n_archived_versions=3,
        n_transitions=2,
        total_ops=5,
        ops_applied=3,
        ops_skipped=2,
        final_combined_similarity_stable=stable,
        final_combined_similarity_raw=raw if raw is not None else stable,
        final_shared_mean_similarity=shared_mean if shared_mean is not None else stable,
        per_family=per_family or {},
        skip_bucket_counts=skips or {},
        n_divergences=n_divergences,
        error=error,
    )


# --- distribution arithmetic ---


def test_percentile_interpolates_between_sorted_values() -> None:
    values = [0.0, 0.5, 1.0]
    assert _percentile_sorted(values, 0.5) == pytest.approx(0.5)
    assert _percentile_sorted(values, 0.25) == pytest.approx(0.25)
    assert _percentile_sorted(values, 0.75) == pytest.approx(0.75)
    # Endpoints are exact; single element returns itself.
    assert _percentile_sorted(values, 0.0) == pytest.approx(0.0)
    assert _percentile_sorted(values, 1.0) == pytest.approx(1.0)
    assert _percentile_sorted([0.42], 0.5) == pytest.approx(0.42)


def test_summarize_distribution_computes_mean_median_quartiles() -> None:
    dist = summarize_distribution([0.0, 0.25, 0.5, 0.75, 1.0])
    assert dist.count == 5
    assert dist.mean == pytest.approx(0.5)
    assert dist.median == pytest.approx(0.5)
    assert dist.p25 == pytest.approx(0.25)
    assert dist.p75 == pytest.approx(0.75)
    assert dist.minimum == pytest.approx(0.0)
    assert dist.maximum == pytest.approx(1.0)


def test_summarize_distribution_is_order_independent() -> None:
    a = summarize_distribution([0.9, 0.1, 0.5])
    b = summarize_distribution([0.5, 0.9, 0.1])
    assert a == b


def test_summarize_empty_distribution_is_honest_none_not_one() -> None:
    dist = summarize_distribution([])
    assert dist.count == 0
    # An empty sample reports None statistics — never a flattering 1.0 or 0.0.
    assert dist.mean is None
    assert dist.median is None
    assert dist.p25 is None
    assert dist.p75 is None
    assert dist.histogram == tuple([0] * 10)


# --- histogram binning ---


def test_histogram_bins_span_zero_to_one_with_top_bin_closed() -> None:
    # One value per decile, plus a perfect 1.0 that must land in the final bin.
    values = [0.05, 0.15, 0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 0.95, 1.0]
    hist = _histogram(values)
    assert len(hist) == 10
    assert hist == (1, 1, 1, 1, 1, 1, 1, 1, 1, 2)  # the 0.95 and 1.0 share the top bin
    assert sum(hist) == len(values)


def test_histogram_clamps_out_of_range_values() -> None:
    hist = _histogram([-0.5, 1.5])
    assert hist[0] == 1  # clamped low
    assert hist[-1] == 1  # clamped high
    assert sum(hist) == 2


# --- skip-cap census tallying ---


def test_tally_skip_caps_sums_counts_and_collects_exemplars() -> None:
    results = (
        _result("act_public_2010_2", stable=0.8, skips={"cap_a": 3, "cap_b": 1}),
        _result("act_public_2010_1", stable=0.9, skips={"cap_a": 2}),
        _result("act_public_2011_5", stable=0.7, skips={"cap_b": 4}),
    )
    counts, exemplars = tally_skip_caps(results)
    assert counts == {"cap_a": 5, "cap_b": 5}
    # Exemplars are collected in deterministic work-id order.
    assert exemplars["cap_a"] == ["act_public_2010_1", "act_public_2010_2"]
    assert exemplars["cap_b"] == ["act_public_2010_2", "act_public_2011_5"]


def test_skip_cap_census_is_ranked_by_descending_count() -> None:
    report = NZChainReplayCorpusReport(
        db_path="data/nz_legislation.farchive",
        families_requested=("repeal",),
        results=(
            _result("w_a", stable=0.9, skips={"rare": 1, "dominant": 10}),
            _result("w_b", stable=0.8, skips={"middle": 5}),
        ),
    )
    census = report.summary()["skip_cap_census"]
    assert [entry["bucket"] for entry in census] == ["dominant", "middle", "rare"]
    assert census[0]["count"] == 10


# --- per-family aggregation ---


def test_aggregate_per_family_sums_family_counts_in_canonical_order() -> None:
    results = (
        _result(
            "w_a",
            stable=0.9,
            per_family={
                "repeal": (4, 3, 1, 3, 3),
                "insert": (2, 0, 2, 0, 0),
            },
        ),
        _result(
            "w_b",
            stable=0.8,
            per_family={
                "repeal": (1, 1, 0, 1, 1),
            },
        ),
    )
    totals = aggregate_per_family(results)
    # Canonical family order (repeal before insert).
    assert list(totals.keys()) == ["repeal", "insert"]
    assert totals["repeal"] == {
        "enumerated": 5,
        "applied": 4,
        "skipped": 1,
        "oracle_agreements": 4,
        "oracle_total": 4,
    }
    assert totals["insert"]["skipped"] == 2


# --- error + no-final accounting (no silent drop) ---


def test_errored_and_no_final_works_are_counted_but_excluded_from_distribution() -> None:
    report = NZChainReplayCorpusReport(
        db_path="data/nz_legislation.farchive",
        families_requested=("repeal",),
        results=(
            _result("w_scored", stable=0.9),
            _result("w_no_final", stable=None),  # no similarity curve
            _result("w_error", stable=None, error="RuntimeError: boom"),
        ),
    )
    summary = report.summary()
    assert summary["works_attempted"] == 3
    assert summary["works_scored"] == 1
    assert summary["works_errored"] == 1
    assert summary["works_no_final_version"] == 1
    assert summary["errored_work_ids"] == ["w_error"]
    # The distribution only counts the scored work — never the error/no-final.
    assert summary["final_stable_combined_similarity_distribution"]["count"] == 1


def test_selection_context_discloses_max_works_cap() -> None:
    report = NZChainReplayCorpusReport(
        db_path="data/nz_legislation.farchive",
        families_requested=("repeal",),
        results=(_result("w_a", stable=0.9),),
        selected_work_ids=("w_a",),
        available_work_count=2383,
        max_works=1,
    )
    selection = report.summary()["selection_context"]
    assert selection["selected_work_count"] == 1
    assert selection["available_work_count"] == 2383
    assert selection["max_works"] == 1
    # The cap actually bit (1 selected < 2383 available) and that is stated.
    assert selection["truncated_by_max_works"] is True


def test_summary_never_claims_replay() -> None:
    report = NZChainReplayCorpusReport(
        db_path="data/nz_legislation.farchive",
        families_requested=("repeal",),
        results=(_result("w_a", stable=0.9),),
    )
    summary = report.summary()
    assert summary["replay_claims"] is False
    payload = report.to_jsonable(summary_only=True)
    assert payload["replay_claims"] is False
    assert payload["report_kind"] == "experimental_dry_run_chain_replay_corpus"


# --- real-archive integration (gated on the corpus being present) ---


_REAL_DB = (
    Path(os.environ.get("LAWVM_CANONICAL_DATA_ROOT") or Path(__file__).resolve().parents[1])
    / "data"
    / "nz_legislation.farchive"
)


@pytest.mark.skipif(not _REAL_DB.exists(), reason="archived NZ farchive not present")
def test_corpus_runner_aggregates_real_multi_work_population() -> None:
    # Two real works run end to end through the parallel runner; the canary must
    # contribute a scored final similarity and the slice is disclosed.
    report = build_nz_chain_replay_corpus_report(
        _REAL_DB,
        work_ids=("act_public_2005_87", "act_public_2009_38"),
        workers=2,
    )
    summary = report.summary()
    assert summary["works_attempted"] == 2
    assert summary["works_scored"] >= 1
    assert summary["works_errored"] == 0
    assert summary["replay_claims"] is False
    dist = summary["final_stable_combined_similarity_distribution"]
    assert dist["count"] == summary["works_scored"]
    assert dist["median"] is not None
    # The selection context discloses what actually ran.
    assert summary["selection_context"]["selected_work_count"] == 2


@pytest.mark.skipif(not _REAL_DB.exists(), reason="archived NZ farchive not present")
def test_corpus_runner_is_deterministic_across_worker_counts() -> None:
    # The aggregate must be byte-identical regardless of worker count: serial
    # (workers=1) and parallel (workers=2) produce the same summary (modulo the
    # wall clock + worker-count fields, which are runtime metadata).
    work_ids = ("act_public_2005_87", "act_public_2009_38", "act_public_1993_110")
    serial = build_nz_chain_replay_corpus_report(_REAL_DB, work_ids=work_ids, workers=1)
    parallel = build_nz_chain_replay_corpus_report(_REAL_DB, work_ids=work_ids, workers=2)

    def _strip(summary: dict) -> dict:
        out = dict(summary)
        out.pop("wall_clock_seconds", None)
        out.pop("workers", None)
        return out

    assert _strip(serial.summary()) == _strip(parallel.summary())
    # The default worker count is the published constant.
    assert DEFAULT_WORKERS >= 1

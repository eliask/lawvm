"""Tests for `lawvm bench -j nz` — the NZ actual-replay multi-lane benchmark.

Two lanes of coverage:

* Pure lane-separation unit tests over synthetic actual-replay reports — no
  farchive needed. These pin the coverage-lane accounting, and in particular the
  REPORT-ONLY ``would-replay-if-refusals-ignored`` lane: a window blocked SOLELY
  by dry-run REFUSAL-blocked ops (the kernel declined to form a candidate) counts
  toward that lane, while a window carrying a verification-failed op never does.
* A real-archive canary: the curated canary work scores high text/tree
  similarity on its actually-replayed transitions, and the coverage lanes stay
  separate from the similarity headline.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from lawvm.new_zealand.actual_replay import (
    NZ_ACTUAL_REPLAY_REFUSED_OP_DRY_RUN_RESIDUAL_RULE_ID,
    NZ_ACTUAL_REPLAY_REFUSED_OP_NOT_DRY_RUN_VERIFIED_RULE_ID,
    NZActualReplayRefusal,
    NZActualReplayReport,
)
from lawvm.tools import nz_bench


# --- synthetic report helpers ----------------------------------------------


def _refusal_blocked(date: str, op_id: str) -> NZActualReplayRefusal:
    """A dry-run REFUSAL-blocked op: the kernel declined to form a candidate."""
    return NZActualReplayRefusal(
        rule_id=NZ_ACTUAL_REPLAY_REFUSED_OP_NOT_DRY_RUN_VERIFIED_RULE_ID,
        message="kernel declined to form a candidate",
        amendment_date_iso=date,
        op_ids=(op_id,),
        detail={
            "family": "insert",
            "dry_run_refusal_rule_id": "nz_dry_run_refused_structural_insert_payload_not_cleanly_extractable",
            "target_address": "section:9",
        },
    )


def _verification_failed(date: str, op_id: str) -> NZActualReplayRefusal:
    """A proof was formed but disagreed with the oracle (verification-failed)."""
    return NZActualReplayRefusal(
        rule_id=NZ_ACTUAL_REPLAY_REFUSED_OP_DRY_RUN_RESIDUAL_RULE_ID,
        message="dry-run proof did not agree with the on-or-after oracle",
        amendment_date_iso=date,
        op_ids=(op_id,),
        detail={"family": "repeal", "oracle_match": "absent"},
    )


def _report(refusals: tuple[NZActualReplayRefusal, ...]) -> NZActualReplayReport:
    return NZActualReplayReport(
        work_id="act_public_test_1",
        families=("repeal", "text_replace", "replace", "insert"),
        transitions=(),
        refusals=refusals,
    )


# --- lane-separation unit tests --------------------------------------------


def test_refusal_blocked_window_counts_toward_would_replay_lane() -> None:
    # A window blocked ONLY by refusal-blocked ops would materialize if those
    # were treated as not-declared — it is the conservatism the lane exposes.
    report = _report((_refusal_blocked("2020-01-01", "op-a"),))
    verification_failed, refusal_blocked, would_replay = nz_bench._classify_refusal_lanes(report)

    assert verification_failed == 0
    assert refusal_blocked == 1
    assert would_replay == 1


def test_verification_failed_window_never_counts_toward_would_replay_lane() -> None:
    # A window carrying a verification-failed op keeps blocking even in the lane.
    report = _report(
        (
            _refusal_blocked("2020-01-01", "op-a"),
            _verification_failed("2020-01-01", "op-b"),
        )
    )
    verification_failed, refusal_blocked, would_replay = nz_bench._classify_refusal_lanes(report)

    assert verification_failed == 1
    assert refusal_blocked == 1
    # Same window: one verification-failed op poisons the would-replay lane.
    assert would_replay == 0


def test_lanes_separate_per_window() -> None:
    # Two distinct windows: one refusal-only (would-replay), one with a
    # verification-failed op (not would-replay).
    report = _report(
        (
            _refusal_blocked("2020-01-01", "op-a"),
            _refusal_blocked("2021-06-06", "op-c"),
            _verification_failed("2021-06-06", "op-d"),
        )
    )
    verification_failed, refusal_blocked, would_replay = nz_bench._classify_refusal_lanes(report)

    assert verification_failed == 1
    assert refusal_blocked == 2
    assert would_replay == 1  # only the 2020-01-01 window


def test_aggregate_keeps_coverage_lanes_separate_from_similarity() -> None:
    # A high similarity over a tiny replayed fraction must NOT be flattened: the
    # aggregate reports the score AND the replayed fraction together, with the
    # would-replay-if-refusals-ignored lane kept distinct from the strict count.
    work = nz_bench._WorkResult(
        work_id="w1",
        families=("repeal",),
        status="OK",
        transitions_replayed=2,
        transitions_refused=8,
        ops_replayed=3,
        slice_nodes=3,
        slice_agreements=3,
        all_slices_agree=True,
        refusals_verification_failed=2,
        refusals_refusal_blocked=6,
        families_not_attempted=0,
        would_replay_if_refusals_ignored=4,
        text_similarity=0.95,
        tree_similarity=0.9,
        tree_similarity_stable=0.99,
        residual_family_counts={"agreement": 3, "temporal_mismatch": 5},
        transition_scores=[
            nz_bench._TransitionScore(
                amendment_date_iso="2020-01-01",
                text_similarity=0.95,
                tree_similarity=0.9,
                tree_similarity_stable=0.99,
                path_jaccard=0.9,
                slice_node_count=2,
                slice_agreements=2,
                ops=2,
            ),
            nz_bench._TransitionScore(
                amendment_date_iso="2021-01-01",
                text_similarity=0.95,
                tree_similarity=0.9,
                tree_similarity_stable=0.99,
                path_jaccard=0.9,
                slice_node_count=1,
                slice_agreements=1,
                ops=1,
            ),
        ],
    )
    agg = nz_bench._aggregate([work])

    # Strict replayed count and refused count are separate, and the replayed
    # fraction is reported alongside the similarity (declared = 2 + 8 = 10).
    assert agg["transitions_replayed"] == 2
    assert agg["transitions_refused"] == 8
    assert agg["declared_transitions"] == 10
    assert agg["replayed_fraction"] == pytest.approx(0.2)
    # Coverage lanes stay split.
    assert agg["refusals_verification_failed"] == 2
    assert agg["refusals_refusal_blocked"] == 6
    # REPORT-ONLY conservatism lane never changes the strict replayed count.
    assert agg["would_replay_if_refusals_ignored"] == 4
    assert agg["hypothetical_replayed_if_refusals_ignored"] == 6
    assert agg["would_replay_fraction"] == pytest.approx(0.6)
    # Similarity is computed over the actually-replayed transitions.
    assert agg["transitions_scored"] == 2
    assert agg["text_similarity"] == pytest.approx(0.95)
    assert agg["tree_similarity"] == pytest.approx(0.9)
    # Oracle agreement is reported BY typed residual family, not just a number.
    assert agg["oracle_agreement_residual_family_counts"] == {
        "agreement": 3,
        "temporal_mismatch": 5,
    }


# --- real-archive canary ----------------------------------------------------


_REAL_DB = (
    Path(os.environ.get("LAWVM_CANONICAL_DATA_ROOT") or Path(__file__).resolve().parents[1])
    / "data"
    / "nz_legislation.farchive"
)
_CANARY_WORK = "act_public_1992_122"


@pytest.mark.skipif(not _REAL_DB.exists(), reason="archived NZ farchive not present")
def test_nz_bench_canary_scores_high_similarity_on_replayed_transitions(capsys, tmp_path) -> None:
    corpus = tmp_path / "canary_corpus.csv"
    corpus.write_text(f"work_id\n{_CANARY_WORK}\n", encoding="utf-8")
    out_json = tmp_path / "out.json"

    args = SimpleNamespace(
        db=str(_REAL_DB),
        corpus=str(corpus),
        smoke=False,
        max_works=None,
        json=False,
        output_json=str(out_json),
    )
    nz_bench.main(args)

    import json

    payload = json.loads(out_json.read_text(encoding="utf-8"))
    summary = payload["summary"]

    # The canary actually replays transitions and every materialized slice agrees.
    assert summary["transitions_replayed"] >= 1
    assert summary["target_slice_agreements"] == summary["target_slice_nodes"]
    assert summary["target_slice_nodes"] >= 1

    # Coverage lanes are present and the replayed fraction is reported alongside
    # the similarity — a tiny replayed fraction cannot masquerade as broad success.
    assert summary["declared_transitions"] >= summary["transitions_replayed"]
    assert 0.0 < summary["replayed_fraction"] <= 1.0
    assert "refusals_verification_failed" in summary
    assert "refusals_refusal_blocked" in summary

    # REPORT-ONLY lane present and never reduces the strict replayed count.
    assert summary["would_replay_if_refusals_ignored"] >= 0
    assert (
        summary["hypothetical_replayed_if_refusals_ignored"]
        == summary["transitions_replayed"] + summary["would_replay_if_refusals_ignored"]
    )

    # Dual similarity over the ACTUALLY-REPLAYED transitions scores high.
    assert summary["transitions_scored"] == summary["transitions_replayed"]
    assert summary["text_similarity"] >= 0.85
    assert summary["tree_similarity"] >= 0.85

    # Oracle agreement is reported BY typed residual family: the canary's clean
    # replayed transitions contribute at least one "agreement" residual.
    family_counts = summary["oracle_agreement_residual_family_counts"]
    assert family_counts.get("agreement", 0) >= 1

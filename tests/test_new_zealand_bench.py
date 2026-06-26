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
        work_status="OK",
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


# --- progress-line + header formatting --------------------------------------


def _ok_work(work_id: str = "w1") -> nz_bench._WorkResult:
    return nz_bench._WorkResult(
        work_id=work_id,
        families=("repeal",),
        work_status="OK",
        transitions_replayed=3,
        transitions_refused=2,
        ops_replayed=4,
        slice_nodes=5,
        slice_agreements=5,
        all_slices_agree=True,
        refusals_verification_failed=1,
        refusals_refusal_blocked=1,
        families_not_attempted=0,
        would_replay_if_refusals_ignored=1,
        text_similarity=0.91,
        tree_similarity=0.88,
        tree_similarity_stable=0.95,
        residual_family_counts={"agreement": 5},
    )


def test_progress_line_is_informative_for_replayed_work() -> None:
    # The per-work progress line streams the work_id AND its key result (the
    # multi-lane signal), in the uk_bench house style, not a bare counter.
    line = nz_bench._format_progress_line(
        done=7, total=33, elapsed=12.0, result=_ok_work("act_public_1992_122")
    )
    assert line.startswith("  [7/33] act_public_1992_122")
    assert "repl=3" in line
    assert "refused=2" in line
    assert "slice=5/5" in line
    assert "text=91%" in line
    assert "tree=88%" in line
    assert "(12s)" in line
    assert "status=OK" in line


def test_progress_line_no_replay_surfaces_coverage_not_zero_similarity() -> None:
    # A work that materialized nothing shows WHY (coverage lanes), not a
    # misleading 0% similarity over an empty replay set.
    work = _ok_work()
    work.transitions_replayed = 0
    work.transitions_refused = 4
    work.would_replay_if_refusals_ignored = 2
    line = nz_bench._format_progress_line(done=1, total=10, elapsed=3.0, result=work)
    assert "repl=0" in line
    assert "refused=4" in line
    assert "would+=2" in line
    assert "text=" not in line  # no fabricated similarity number
    assert "status=OK" in line


def test_progress_line_typed_status_for_errors() -> None:
    work = _ok_work()
    work.work_status = "EXC:ValueError:boom"
    work.transitions_replayed = 0
    line = nz_bench._format_progress_line(done=2, total=10, elapsed=1.0, result=work)
    assert "ERROR" in line
    assert "status=EXC:ValueError:boom" in line


# --- unified bench contract headline ----------------------------------------


def test_unified_summary_renders_worst_of_dual_axes(capsys) -> None:
    """NZ renders the shared unified headline (worst-of structural/text axes).

    Asserts meaningful properties: the status partition counts, the worst-of
    mean-error headline, and the residue-reconciliation honesty line. A scored
    work whose structural axis disagrees (slice 4/5 -> 20% structural error,
    above its 9% text error) makes the worst-of headline the binding structural
    axis — exactly the Liebig framing the contract enforces.
    """
    scored = _ok_work("scored")
    scored.slice_nodes = 5
    scored.slice_agreements = 4  # 20% structural error
    scored.text_similarity = 0.91  # 9% text error -> worst-of is structural 20%
    scored.residual_family_counts = {"temporal_mismatch": 1}

    crashed = _ok_work("crashed")
    crashed.work_status = "EXC:boom"

    nz_bench._render_unified_summary([scored, crashed], "smoke")

    out = capsys.readouterr().out
    assert "=== UNIFIED BENCH SUMMARY" in out
    assert "jurisdiction=nz" in out
    assert "1 scored" in out
    assert "crashed: 1" in out
    # Worst-of headline = max(structural 20%, text 9%) = 20.00% error.
    assert "Mean error : 20.00%" in out
    assert "Residue reconciliation: OK" in out


# --- real-archive canary ----------------------------------------------------


_REAL_DB = (
    Path(os.environ.get("LAWVM_CANONICAL_DATA_ROOT") or Path(__file__).resolve().parents[1])
    / "data"
    / "nz_legislation.farchive"
)
_CANARY_WORK = "act_public_1992_122"
_REAL_CORPUS = (
    Path(os.environ.get("LAWVM_CANONICAL_DATA_ROOT") or Path(__file__).resolve().parents[1])
    / "data"
    / "nz"
    / "bench_corpus.csv"
)


# --- size proxy + execution ordering ----------------------------------------


def test_load_corpus_with_size_reads_proxy_and_respects_max_works(tmp_path) -> None:
    # The cheap size proxy comes straight from the CSV; --max-works is applied to
    # the CSV (file) order BEFORE any reordering, so the selected set is stable.
    corpus = tmp_path / "c.csv"
    corpus.write_text(
        "work_id,n_amendment_operations,n_history_witnesses\n"
        "a,5,5\n"
        "b,40,40\n"
        "c,1,1\n"
        "d,99,99\n",
        encoding="utf-8",
    )
    pairs = nz_bench._load_corpus_with_size(corpus, max_works=3)
    # max_works selects the first 3 CSV rows, NOT the 3 largest.
    assert [wid for wid, _ in pairs] == ["a", "b", "c"]
    assert [sz for _, sz in pairs] == [5, 40, 1]


def test_execution_order_is_largest_work_first_then_stable() -> None:
    pairs = [("a", 5), ("b", 40), ("c", 1), ("d", 40)]
    # Descending by size proxy; ties (b, d) break on original CSV index.
    assert nz_bench._execution_order(pairs) == ["b", "d", "a", "c"]


def test_execution_order_falls_back_to_csv_order_without_size_column(tmp_path) -> None:
    corpus = tmp_path / "c.csv"
    corpus.write_text("work_id\na\nb\nc\n", encoding="utf-8")
    pairs = nz_bench._load_corpus_with_size(corpus, max_works=None)
    # No size column -> all proxies 0 -> stable CSV order preserved.
    assert [sz for _, sz in pairs] == [0, 0, 0]
    assert nz_bench._execution_order(pairs) == ["a", "b", "c"]


def test_resolve_workers_semantics() -> None:
    # None / 0 -> auto default (>=1, capped); 1 -> serial; large -> capped.
    assert nz_bench._resolve_workers(1) == 1
    assert nz_bench._resolve_workers(None) >= 1
    assert nz_bench._resolve_workers(0) >= 1
    assert nz_bench._resolve_workers(10_000) == nz_bench._MAX_WORKERS


@pytest.mark.skipif(not _REAL_DB.exists(), reason="archived NZ farchive not present")
@pytest.mark.slow
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

    # The run streams a header naming the corpus + work count, and an
    # informative per-work progress line carrying the work_id and its key
    # result — not a bare counter.
    err = capsys.readouterr().err
    assert "NZ actual-replay bench: scoring 1 works" in err
    assert str(corpus) in err
    assert f"[1/1] {_CANARY_WORK}" in err

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


def _run_bench_json(*, db, corpus, parallel, out_json) -> dict:
    args = SimpleNamespace(
        db=str(db),
        corpus=str(corpus),
        smoke=False,
        max_works=None,
        json=False,
        output_json=str(out_json),
        parallel=parallel,
    )
    nz_bench.main(args)
    import json

    return json.loads(Path(out_json).read_text(encoding="utf-8"))


@pytest.mark.skipif(
    not (_REAL_DB.exists() and _REAL_CORPUS.exists()),
    reason="archived NZ farchive / corpus not present",
)
@pytest.mark.slow
def test_parallel_aggregate_is_byte_identical_to_serial(tmp_path) -> None:
    # Parallelism + largest-work-first ordering must NOT change any score: the
    # aggregate summary and the per-work payloads (sorted to CSV order) are
    # byte-identical between the serial and the parallel run over the same slice.
    import csv as _csv
    import json as _json

    with open(_REAL_CORPUS, newline="") as f:
        rows = [r for r in _csv.DictReader(f)][:6]
    assert rows, "expected a non-empty real NZ corpus"
    slice_csv = tmp_path / "slice.csv"
    with open(slice_csv, "w", newline="") as f:
        writer = _csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    serial = _run_bench_json(
        db=_REAL_DB, corpus=slice_csv, parallel=1, out_json=tmp_path / "serial.json"
    )
    parallel = _run_bench_json(
        db=_REAL_DB, corpus=slice_csv, parallel=4, out_json=tmp_path / "parallel.json"
    )

    # Aggregate numbers are byte-identical.
    assert serial["summary"] == parallel["summary"]
    # Per-work payloads are emitted in CSV order in both runs and identical.
    assert [w["work_id"] for w in serial["works"]] == [
        w["work_id"] for w in parallel["works"]
    ]
    assert _json.dumps(serial["works"], sort_keys=True) == _json.dumps(
        parallel["works"], sort_keys=True
    )


@pytest.mark.skipif(
    not (_REAL_DB.exists() and _REAL_CORPUS.exists()),
    reason="archived NZ farchive / corpus not present",
)
@pytest.mark.slow
def test_run_scoped_parse_cache_is_byte_identical_to_uncached(
    tmp_path, monkeypatch
) -> None:
    # The run-scoped parse/archive cache (corpus_run_cache) the bench activates is
    # a PURE performance layer: it must produce a byte-identical report to a run
    # with the cache forcibly disabled. This pins the safe-win invariant — the
    # speedup never changes any score.
    import contextlib
    import csv as _csv
    import json as _json

    with open(_REAL_CORPUS, newline="") as f:
        rows = [r for r in _csv.DictReader(f)][:6]
    assert rows, "expected a non-empty real NZ corpus"
    slice_csv = tmp_path / "slice.csv"
    with open(slice_csv, "w", newline="") as f:
        writer = _csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    # Cache ON: the bench's normal (now cached) serial path.
    cached = _run_bench_json(
        db=_REAL_DB, corpus=slice_csv, parallel=1, out_json=tmp_path / "cached.json"
    )

    # Cache OFF: replace corpus_run_cache with a no-op context that never
    # activates a cache, so every parse/archive call falls through to the
    # uncached path. _run_bench_json uses the serial path (parallel=1), so only
    # the serial activation needs neutralizing.
    @contextlib.contextmanager
    def _no_cache():
        yield None

    monkeypatch.setattr(nz_bench, "corpus_run_cache", _no_cache)
    monkeypatch.setattr(nz_bench, "active_corpus_run_cache", lambda: None)
    uncached = _run_bench_json(
        db=_REAL_DB, corpus=slice_csv, parallel=1, out_json=tmp_path / "uncached.json"
    )

    assert cached["summary"] == uncached["summary"]
    assert _json.dumps(cached["works"], sort_keys=True) == _json.dumps(
        uncached["works"], sort_keys=True
    )

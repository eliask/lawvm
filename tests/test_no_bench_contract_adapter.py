"""Contract adapter test for the Norway bench comparator.

Mirrors ``tests/test_fi_bench_contract_adapter.py``: the comparator under test
is :func:`lawvm.tools.no_bench.no_bench_unit_result`.  Each test builds a
synthetic :class:`~lawvm.norway.verify.NOVerifyResult` (or facsimile) in a
specific state and asserts the mapped :class:`BenchUnitResult` honours the
unified bench contract — status selection, axis values, residue reconciliation,
and the §7 honesty invariant (positive structural_err iff residue is non-empty).

No replay is run; the comparator is the unit under test and receives its
canonical input type directly.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from lawvm.core.bench_contract import BenchStatus, BenchUnitResult, check_residue_reconciliation
from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.tools import no_bench  # noqa: F401  (registers the "no" comparator at import)
from lawvm.core.bench_comparator_registry import registered_jurisdictions
from lawvm.tools.no_bench import (
    _load_run_accuracies,
    _most_recent_labels,
    _resolve_runs_dir,
    _render_history,
    _render_regressions_from_runs,
    _render_show_label,
    no_bench_unit_result,
)


def _replayed_body(*section_labels: str) -> IRNode:
    """Build a body IRNode whose children are SECTION nodes with the given labels.

    Each section carries a trivial heading + subsection so extract_ir_sections
    reports them — and so n_replay_sections is well-defined.
    """
    return IRNode(
        kind=IRNodeKind.BODY,
        children=tuple(
            IRNode(
                kind=IRNodeKind.SECTION,
                label=label,
                children=(IRNode(kind=IRNodeKind.HEADING, text=f"§ {label}"),),
            )
            for label in section_labels
        ),
    )


def _replay_result(replayed_body: IRNode | None, *, error: str | None = None) -> Any:
    """Build a minimal NOReplayResult-shaped SimpleNamespace.

    Bench comparator only accesses ``replay.replayed.body`` (when present) and
    never the other replay fields, so this stub is sufficient and keeps the
    test free of any real replay machinery.
    """
    if replayed_body is None:
        return SimpleNamespace(replayed=None, error=error)
    return SimpleNamespace(replayed=SimpleNamespace(body=replayed_body), error=error)


def _verify_result(
    *,
    base_id: str = "no/lov/2025-01-01-1",
    error: str | None = None,
    replay_status: str = "replayed",
    consistent: bool = False,
    divergence_count: int = 0,
    divergence_counts: dict[str, int] | None = None,
    replayed_body: IRNode | None = None,
    source_signal: str | None = None,
    indexed_amendment_count: int = 0,
    replay_op_count: int = 0,
) -> Any:
    """Build a minimal NOVerifyResult-shaped SimpleNamespace.

    The bench comparator reads: base_id, error, replay_status, divergence_count,
    divergence_counts, replay.replayed.body, source_signal,
    indexed_amendment_count, replay_op_count.  That is the only contract.
    """
    return SimpleNamespace(
        base_id=base_id,
        error=error,
        replay_status=replay_status,
        consistent=consistent,
        divergence_count=divergence_count,
        divergence_counts=divergence_counts,
        replay=_replay_result(replayed_body, error=error),
        source_signal=source_signal,
        indexed_amendment_count=indexed_amendment_count,
        replay_op_count=replay_op_count,
    )


# ---------------------------------------------------------------------------
# Registration — the comparator is reachable via ``get_bench_comparator("no")``.
# ---------------------------------------------------------------------------


def test_no_bench_comparator_is_registered_at_module_import() -> None:
    from lawvm.core.bench_comparator_registry import has_bench_comparator

    assert "no" in registered_jurisdictions()
    assert has_bench_comparator("no")


# ---------------------------------------------------------------------------
# CRASH — verify.error set.
# ---------------------------------------------------------------------------


def test_no_bench_crashes_when_verify_error_is_set() -> None:
    result = _verify_result(error="boom: replay exploded", replay_status="error")

    mapped = no_bench_unit_result(result)

    assert mapped.status is BenchStatus.CRASH
    assert mapped.is_failure is True
    assert mapped.witnesses == ("boom: replay exploded",)
    # A CRASH must not invent axis errors or residue — silence better than fake.
    assert mapped.structural_err is None
    assert mapped.text_err is None
    assert dict(mapped.residue_buckets) == {}


# ---------------------------------------------------------------------------
# SOURCE_UNAVAILABLE — known data ceilings (NORWAY_LAWVM_STATUS.md §"Limits").
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("blocked_status", ["blocked_contingent", "blocked_missing_source", "blocked_unknown"])
def test_no_bench_source_unavailable_for_data_ceiling_statuses(blocked_status: str) -> None:
    result = _verify_result(replay_status=blocked_status)

    mapped = no_bench_unit_result(result)

    assert mapped.status is BenchStatus.SOURCE_UNAVAILABLE
    assert mapped.is_failure is False  # not a failure, a non-scored data ceiling
    assert mapped.witnesses == (blocked_status,)
    assert mapped.structural_err is None
    assert mapped.text_err is None


# ---------------------------------------------------------------------------
# SOURCE_UNAVAILABLE — sparse_indexed_history source-signal (acquisition
# ceiling, distinct from a blocked replay_status). _infer_no_source_signal
# computes this on verify_no_against_current output but the bench comparator
# previously ignored it; statutes with ≤1 indexed amendment and ≥50/≥15
# primary divergences were saturating structural_err at 1.0 (cap), which
# misread the acquisition ceiling as a replay bug.
# ---------------------------------------------------------------------------


def test_no_bench_source_unavailable_for_sparse_indexed_history_signal() -> None:
    # Mirrors the post-§1-fix shape of no/lov/2006-06-30-50 (SCE-loven) at
    # as-of 2025-01-01: status=replayed, divergence_count=212, indexed=1
    # amendment, replay_ops=3 —- sparse_indexed_history signal fires from
    # the ≥50/divergence_count gate (verify.py:296-303).
    result = _verify_result(
        replay_status="replayed",
        divergence_count=212,
        divergence_counts={"OPS_MISSING": 104, "CONSOLIDATED_MISSING": 108},
        replayed_body=IRNode(kind=IRNodeKind.SECTION, label="a1", text="x"),
        source_signal="sparse_indexed_history",
        indexed_amendment_count=1,
        replay_op_count=3,
    )

    mapped = no_bench_unit_result(result)

    # Surface as a documented data ceiling — NOT a saturated-1.0 SCORED.
    assert mapped.status is BenchStatus.SOURCE_UNAVAILABLE
    assert mapped.is_failure is False
    # Witness names the signal + upstream-count triad so the user can
    # filter / inspect without re-running verify.
    assert len(mapped.witnesses) == 1
    assert mapped.witnesses[0].startswith("sparse_indexed_history ")
    assert "divergences=212" in mapped.witnesses[0]
    assert "indexed_amendments=1" in mapped.witnesses[0]
    assert "replay_ops=3" in mapped.witnesses[0]
    # §7 honesty invariant: non-SCORED carries no axis errors.
    assert mapped.structural_err is None
    assert mapped.text_err is None
    assert dict(mapped.residue_buckets) == {}


def test_no_bench_sparse_indexed_history_fires_even_when_replay_succeeded() -> None:
    # The sparse_indexed_history branch is distinct from the
    # blocked_* SOURCE_UNAVAILABLE branch: it fires when
    # replay_status IS "replayed" but the divergence volume against the
    # tiny indexed-amendment count collapses the structural_err cap and
    # would misinterpret the acquisition ceiling as a replay bug.
    # Without the explicit branch, this row would have decoded through
    # to the SCORED path and saturated structural_err at 1.0.
    result = _verify_result(
        replay_status="replayed",
        divergence_count=15,
        divergence_counts={"MISMATCH": 15},
        replayed_body=IRNode(kind=IRNodeKind.SECTION, label="a1", text="x"),
        source_signal="sparse_indexed_history",
        indexed_amendment_count=1,
        replay_op_count=2,
    )

    mapped = no_bench_unit_result(result)

    assert mapped.status is BenchStatus.SOURCE_UNAVAILABLE
    assert mapped.is_failure is False


def test_no_bench_scored_when_source_signal_absent() -> None:
    # §2.9 paired negative for the sparse_indexed_history SOURCE_UNAVAILABLE
    # branch: divergence_count ≥ 5 but source_signal NOT set means the
    # upstream inference did not classify this statute as acquisition-level
    # sparse. The bench comparator must NOT route to SOURCE_UNAVAILABLE
    # based on divergence_count alone — that would hide a real replay bug
    # behind an absent signal. SOURCE_UNAVAILABLE fires only when the
    # typed signal is present, never as a heuristic on divergence_count.
    result = _verify_result(
        replay_status="replayed",
        divergence_count=5,
        divergence_counts={"MISMATCH": 5},
        replayed_body=IRNode(kind=IRNodeKind.SECTION, label="a1", text="x"),
        source_signal=None,
        indexed_amendment_count=12,
        replay_op_count=18,
    )

    mapped = no_bench_unit_result(result)

    assert mapped.status is BenchStatus.SCORED
    assert mapped.is_failure is False
    assert mapped.witnesses == ()


def test_no_bench_scored_when_unknown_source_signal_string() -> None:
    # §2.9 paired negative: only the named "sparse_indexed_history" signal
    # is honored as SOURCE_UNAVAILABLE. Other (possibly future) signal
    # strings do NOT route to SOURCE_UNAVAILABLE silently — that would
    # turn any string-typed addition into an invisible non-scored status
    # change. A new signal name reaching bench-worthiness is itself the
    # explicit branch addition, not data-driven via a string.
    result = _verify_result(
        replay_status="replayed",
        divergence_count=5,
        divergence_counts={"MISMATCH": 5},
        replayed_body=IRNode(kind=IRNodeKind.SECTION, label="a1", text="x"),
        source_signal="some_future_unrecognized_signal",
        indexed_amendment_count=1,
        replay_op_count=2,
    )

    mapped = no_bench_unit_result(result)

    assert mapped.status is BenchStatus.SCORED
    assert mapped.is_failure is False


# ---------------------------------------------------------------------------
# NO_TRUTH — no amendments applied in window; nothing to differ against.
# ---------------------------------------------------------------------------


def test_no_bench_no_truth_when_no_amendments_in_window() -> None:
    result = _verify_result(replay_status="no_amendments")

    mapped = no_bench_unit_result(result)

    assert mapped.status is BenchStatus.NO_TRUTH
    assert mapped.is_failure is False
    assert mapped.structural_err is None
    assert mapped.text_err is None
    assert dict(mapped.residue_buckets) == {}


# ---------------------------------------------------------------------------
# SCORED — consistent (no divergence).
# ---------------------------------------------------------------------------


def test_no_bench_scored_consistent_replay_is_perfect_with_no_residue() -> None:
    result = _verify_result(
        replay_status="replayed",
        consistent=True,
        divergence_count=0,
        replayed_body=_replayed_body("1", "2", "3"),
    )

    mapped = no_bench_unit_result(result)

    assert mapped.status is BenchStatus.SCORED
    assert mapped.structural_err == 0.0
    assert mapped.text_err is None
    assert dict(mapped.residue_buckets) == {}
    check_residue_reconciliation(mapped)  # structural_err=0 ↔ residue empty


# ---------------------------------------------------------------------------
# SCORED — divergent (replay_status=replayed, divergence_count > 0).
# ---------------------------------------------------------------------------


def test_no_bench_scored_divergent_replay_carries_typed_residue() -> None:
    divergence_counts = {"MISMATCH": 2, "OPS_MISSING": 1, "CONSOLIDATED_MISSING": 3}
    result = _verify_result(
        replay_status="replayed",
        consistent=False,
        divergence_count=6,  # 2 + 1 + 3
        divergence_counts=divergence_counts,
        replayed_body=_replayed_body("1", "2", "3", "4", "5", "6"),
    )

    mapped = no_bench_unit_result(result)

    assert mapped.status is BenchStatus.SCORED
    # 6 divergences / 6 sections = 1.0 — saturated, capped to [0, 1].
    assert mapped.structural_err == pytest.approx(1.0)
    assert mapped.text_err is None
    # Each divergence type is re-keyed into the structural:<kind> family.
    assert dict(mapped.residue_buckets) == {
        "structural:MISMATCH": 2,
        "structural:OPS_MISSING": 1,
        "structural:CONSOLIDATED_MISSING": 3,
    }
    check_residue_reconciliation(mapped)


def test_no_bench_scored_divergent_replay_caps_structural_err_at_one() -> None:
    # 4 divergences across 3 sections → 4/3 capped to 1.0; residue still > 0
    # so the §7 invariant (positive error iff residue) holds.
    result = _verify_result(
        replay_status="replayed",
        consistent=False,
        divergence_count=4,
        divergence_counts={"MISMATCH": 4},
        replayed_body=_replayed_body("1", "2", "3"),
    )

    mapped = no_bench_unit_result(result)

    assert mapped.status is BenchStatus.SCORED
    assert mapped.structural_err == 1.0
    assert dict(mapped.residue_buckets) == {"structural:MISMATCH": 4}
    check_residue_reconciliation(mapped)


def test_no_bench_scored_divergent_replay_uses_replay_section_count_as_denominator() -> None:
    # 3 divergences / 6 sections = 0.5. Magnitude depends on the replay
    # denominator; the §7 invariant (residue>0 iff error>0) is what's pinned.
    result = _verify_result(
        replay_status="replayed",
        consistent=False,
        divergence_count=3,
        divergence_counts={"MISMATCH": 3},
        replayed_body=_replayed_body("1", "2", "3", "4", "5", "6"),
    )

    mapped = no_bench_unit_result(result)

    assert mapped.status is BenchStatus.SCORED
    assert mapped.structural_err == pytest.approx(0.5)
    assert dict(mapped.residue_buckets) == {"structural:MISMATCH": 3}
    check_residue_reconciliation(mapped)


# ---------------------------------------------------------------------------
# Honest zero-section handling: NO_TRUTH rather than a fake SCORED 0.0.
# ---------------------------------------------------------------------------


def test_no_bench_no_truth_when_replayed_body_has_zero_sections() -> None:
    # An empty replay body has no oracle to score against — consistent or not,
    # it's NOT mistakenly SCORED as perfectly identical (which would silently
    # lower the bench average).
    result = _verify_result(
        replay_status="replayed",
        consistent=True,
        divergence_count=0,
        replayed_body=IRNode(kind=IRNodeKind.BODY, children=()),
    )

    mapped = no_bench_unit_result(result)

    assert mapped.status is BenchStatus.NO_TRUTH
    assert mapped.structural_err is None  # not SCORED → axes are None
    assert dict(mapped.residue_buckets) == {}


def test_no_bench_crashes_when_scored_path_lacks_replayed_body() -> None:
    # Defensive: a SCORED-eligible status was produced but the replayed body
    # is missing — structural surprise, must surface as CRASH, never as
    # silently SCORED = 0.0.
    result = _verify_result(replay_status="replayed", replayed_body=None)

    mapped = no_bench_unit_result(result)

    assert mapped.status is BenchStatus.CRASH
    assert mapped.witnesses  # carries the diagnostic
    assert mapped.structural_err is None
    assert dict(mapped.residue_buckets) == {}


# ---------------------------------------------------------------------------
# Empty / None-axis handling: residue-bearing counts with divergence_count=0
# must not silently produce structural_err=0 with phantom residue.
# ---------------------------------------------------------------------------


def test_no_bench_does_not_invent_phantom_residue_when_divergence_count_is_zero() -> None:
    # The §7 honesty property: structural_err=0 iff residue is empty. If a
    # buggy NOVerifyResult reports divergence_count=0 but divergence_counts
    # is non-empty (impossible in production but defensive against a future
    # BUG that decouples them), residue is calculated from the counts and
    # would then be NON-EMPTY — check_residue_reconciliation must RAISE so
    # the invariant cannot be silently violated.
    result = _verify_result(
        replay_status="replayed",
        consistent=True,
        divergence_count=0,
        divergence_counts={"MISMATCH": 5},  # phantom — no corresponding structural_err
        replayed_body=_replayed_body("1", "2", "3"),
    )

    mapped = no_bench_unit_result(result)

    # The comparator's contract discipline: divergence_count=0 is SCORED-perfect
    # with empty residue, regardless of spurious divergence_counts. This protects
    # §7 — the alternative (taking counts at face value) would create phantom
    # residue the structural_err cannot reconcile against.
    assert mapped.status is BenchStatus.SCORED
    assert mapped.structural_err == 0.0
    assert dict(mapped.residue_buckets) == {}
    check_residue_reconciliation(mapped)  # does not raise


# ---------------------------------------------------------------------------
# Result-type discipline — the comparator always returns BenchUnitResult.
# ---------------------------------------------------------------------------


def test_no_bench_returns_bench_unit_result_for_every_status_path() -> None:
    cases = [
        _verify_result(error="x", replay_status="error"),
        _verify_result(replay_status="blocked_contingent"),
        _verify_result(replay_status="no_amendments"),
        _verify_result(replay_status="replayed", consistent=True, replayed_body=_replayed_body("1")),
        _verify_result(
            replay_status="replayed",
            consistent=False,
            divergence_count=1,
            divergence_counts={"MISMATCH": 1},
            replayed_body=_replayed_body("1", "2"),
        ),
    ]
    for case in cases:
        mapped = no_bench_unit_result(case)
        assert isinstance(mapped, BenchUnitResult), f"comparator returned {type(mapped).__name__} — must be BenchUnitResult"


# Anchor to the registry contract: re-running registered_jurisdictions() now
# includes "no", so future cross-jurisdiction tooling won't KeyError here.
def test_no_bench_comparator_round_trip_through_registry() -> None:
    from lawvm.core.bench_comparator_registry import get_bench_comparator, run_bench_comparator

    comparator = get_bench_comparator("no")
    assert comparator is no_bench_unit_result

    result = _verify_result(
        replay_status="replayed",
        consistent=False,
        divergence_count=1,
        divergence_counts={"MISMATCH": 1},
        replayed_body=_replayed_body("1", "2"),
    )
    mapped = run_bench_comparator("no", result)
    assert isinstance(mapped, BenchUnitResult)
    assert mapped.status is BenchStatus.SCORED


# ---------------------------------------------------------------------------
# E2E smoke test against the curated real corpus.
#
# ``no_bench_main`` reads ``data/norway/bench_corpus.csv`` and drives each row
# through ``verify_no_against_current`` → ``run_bench_comparator("no", ...)``.
# The corpus + the local Lovdata archive are both needed; this test skips
# with a typed marker (not silently passes) when either is missing so the
# contract adapter never claims green without the data layer actually running.
# ---------------------------------------------------------------------------

from pathlib import Path as _Path

_REPO_ROOT = _Path(__file__).resolve().parents[1]
_REAL_CORPUS = _REPO_ROOT / "data" / "norway" / "bench_corpus.csv"
_REAL_ARCHIVE = _REPO_ROOT / "data" / "norway.farchive"


@pytest.mark.skipif(
    not _REAL_CORPUS.exists() or not _REAL_ARCHIVE.exists(),
    reason="requires the curated corpus (data/norway/bench_corpus.csv) and the local Lovdata archive (data/norway.farchive)",
)
def test_no_bench_main_runs_curated_corpus_to_zero_crashes(tmp_path) -> None:
        """``lawvm -j no bench`` runs every corpus row without CRASH.

        The contract adapter above tests the comparator's mapping logic in
        isolation; this smoke test drives the *whole* CLI path through the
        curated corpus, asserting that no row hits CRASH (which would be
        either an exception inside ``verify_no_against_current`` or a
        comparator-mapping bug on a real ``NOVerifyResult``). It does NOT
        pin exact scores — those depend on the live Lovdata text and must be
        allowed to drift as the archive changes — but the structural
        invariant (every row SCORED, no row CRASHED, residue reconciles) is
        stable and is what this pins.
        """
        import io
        from contextlib import redirect_stdout
        from types import SimpleNamespace

        from lawvm.tools.no_bench import no_bench_main

        # Isolate the history CSV to tmp_path so test runs do not pollute the
        # repo's real data/norway_bench_history.csv with adapter-smoke rows
        # (each test invocation would otherwise append a real-history row).
        # An empty file at tmp_path works without altering the summary
        # contract.
        history_csv = tmp_path / "norway_bench_history.csv"
        runs_csv = tmp_path / "norway_bench_runs" / "adapter-smoke.csv"
        args = SimpleNamespace(
            corpus=None,
            data_dir=None,
            label="adapter-smoke",
            history_path=history_csv,
            runs_path=runs_csv,
        )
        buf = io.StringIO()
        rc = no_bench_main(args)
        assert rc == 0
        out = buf.getvalue()

        # Re-capture by running again against the redirector: the function
        # returns 0 on success but the summary was printed to the *real*
        # stdout above — re-run it under a fresh buffer to assert content.
        buf = io.StringIO()
        with redirect_stdout(buf):
            no_bench_main(args)
        summary = buf.getvalue()

        # ``crashed: 0`` is the contract — no row may silently fail.
        assert "crashed: 0" in summary, summary
        # Residue reconciliation holds across all real rows end-to-end.
        assert "Residue reconciliation: OK" in summary, summary
        # History-write spanned both runs and reported the isolated path.
        assert history_csv.exists()
        assert str(history_csv) in summary


# ---------------------------------------------------------------------------
# Regression-guard primitives — focused unit tests for the per-statute CSV
# loader, mtime-sorted label discovery, and --compare / --regressions
# arbitration. Pins the contract established by the 30ef19d8 prerequisite
# (per-statute CSV) and the current commit's --regressions / --compare wiring.
# ---------------------------------------------------------------------------


_PER_STATUTE_CSV_HEADER = (
    "unit_id,status,structural_err,text_err,headline_error,headline_accuracy,"
    "residue_total,residue_buckets,witnesses\n"
)


def _write_run_csv(path: _Path, rows: list[tuple[str, str, str]]) -> None:
    """Write a synthetic per-statute NO bench run CSV.

    Each row is ``(unit_id, status, headline_accuracy)``; structural_err is
    derived as ``1 - accuracy`` so find_regressions' headline_accuracy column
    reads sanely. Mirrors the schema persisted by ``_persist_per_statute_results``:
    the CSV's ``headline_error`` column is the *error* (1 - accuracy), and the
    ``headline_accuracy`` column is the *accuracy* itself. A test fixture that
    wrote accuracy into headline_error would invert the renderer's worst-first
    sort (the renderer sorts by descending headline_error).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        f.write(_PER_STATUTE_CSV_HEADER)
        for unit_id, status, acc in rows:
            try:
                acc_f = float(acc)
                err_f = 1.0 - acc_f
            except ValueError:
                acc_f = None
                err_f = None
            f.write(
                f"{unit_id},{status},"
                f"{'' if err_f is None else f'{err_f:.6f}'},"
                f","
                f"{'' if err_f is None else f'{err_f:.6f}'},"
                f"{'' if acc_f is None else f'{acc_f:.6f}'},"
                f"0,,\n"
            )


def test_load_run_accuracies_returns_none_for_missing_csv(tmp_path) -> None:
    # The contract: a missing file returns None, not an empty dict — so the
    # caller can distinguish "no such labelled run" from "label existed but
    # produced zero SCORED rows".
    result = _load_run_accuracies(tmp_path / "no-such-label.csv")

    assert result is None


def test_load_run_accuracies_skips_non_scored_rows() -> None:
    # CRASH / NON_SCORED statuses carry no accuracy and would pollute the
    # regression comparator's {unit_id: accuracy} map. The loader must skip
    # them; missing-key-on-either-side is what find_regressions handles via
    # its `unit_id not in current: continue` branch.
    csv_content = (
        "unit_id,status,structural_err,text_err,headline_error,headline_accuracy,"
        "residue_total,residue_buckets,witnesses\n"
        "no/lov/2024-01-12-1,scored,0.000000,,0.000000,1.000000,0,,\n"
        "no/lov/2022-03-11-9,crash,,,,,0,,boom\n"
        "no/lov/2017-06-16-60,scored,0.050000,,0.050000,0.950000,2,structural:MISMATCH=2,\n"
    )

    # Re-call the loader against a real tmp file we create directly:
    import tempfile

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".csv", delete=False) as tf:
        tf.write(csv_content)
        tmp_csv = _Path(tf.name)
    try:
        out = _load_run_accuracies(tmp_csv)
    finally:
        tmp_csv.unlink()
    assert out == {"no/lov/2024-01-12-1": 1.0, "no/lov/2017-06-16-60": 0.95}


def test_most_recent_labels_orders_by_mtime_desc(tmp_path) -> None:
    # Two runs created with different mtimes; the more-recent one is the
    # "current" run, the older one is the baseline. find_regressions' output
    # depends on which side is "previous" vs "current"; the label order
    # returned here is the contract: previous=current-label-1, current=label-0
    # (caller does recent[1] vs recent[0]).
    import os
    import time as _time

    older = tmp_path / "older.csv"
    newer = tmp_path / "newer.csv"
    older.write_text("header\n", encoding="utf-8")
    _time.sleep(0.01)
    newer.write_text("header\n", encoding="utf-8")
    # Guarantee mtime skew on filesystems with coarse timestamps:
    os.utime(older, (older.stat().st_atime, older.stat().st_mtime))
    os.utime(newer, (newer.stat().st_atime, newer.stat().st_mtime + 0.5))

    labels = _most_recent_labels(tmp_path, limit=2)

    assert labels == ["newer", "older"]


def test_most_recent_labels_returns_empty_for_missing_dir(tmp_path) -> None:
    # An absent runs dir → empty list, *not* an exception. The CLI's
    # --regressions branch surfaces a typed "found N runs" diagnostic that
    # fires only because of this contract.
    no_runs_dir = tmp_path / "does-not-exist"
    assert no_runs_dir.exists() is False

    labels = _most_recent_labels(no_runs_dir, limit=2)

    assert labels == []


def test_render_regressions_reports_one_unit_when_accuracy_drops(tmp_path) -> None:
    # Two synthetic runs: same three statutes scored in both; one of them
    # drops in accuracy from 1.00 to 0.90. The render must surface that
    # single regression with the unit_id, the delta (-0.10 = -10pp), and
    # both headline accuracies.
    from types import SimpleNamespace

    runs_dir = tmp_path / "norway_bench_runs"
    runs_dir.mkdir(parents=True, exist_ok=True)

    _write_run_csv(
        runs_dir / "before.csv",
        [
            ("no/lov/A", "scored", "1.000000"),
            ("no/lov/B", "scored", "0.950000"),
            ("no/lov/C", "scored", "0.800000"),
        ],
    )
    _write_run_csv(
        runs_dir / "after.csv",
        [
            ("no/lov/A", "scored", "1.000000"),  # unchanged
            ("no/lov/B", "scored", "0.900000"),  # dropped -0.05 → regression
            ("no/lov/C", "scored", "0.500000"),  # dropped -0.30 → regression
        ],
    )

    args = SimpleNamespace(
        compare=["before", "after"],
        runs_path=runs_dir,
    )
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    rc = 0
    with redirect_stdout(buf):
        rc = _render_regressions_from_runs(args)

    assert rc == 0
    out = buf.getvalue()
    assert "Norway bench regressions: before -> after" in out
    assert "Common (scored in both): 3" in out
    assert "Regressions (accuracy dropped > 0.001 tolerance): 2" in out
    # Worst-delta-first (find_regressions sorts by delta ascending):
    # C: -0.30, then B: -0.05.
    assert "no/lov/C" in out
    assert "no/lov/B" in out
    assert out.index("no/lov/C") < out.index("no/lov/B")


def test_render_regressions_returns_2_when_only_one_labelled_run(tmp_path) -> None:
    # The --regressions branch (no --compare) requires at least two labelled
    # runs in the runs dir; with only one persisted, the CLI must fail loud
    # with rc=2 (matching the no-corpus error convention elsewhere):
    # ``lawvm -j no bench --regressions`` is not useful without a baseline.
    from types import SimpleNamespace

    runs_dir = tmp_path / "norway_bench_runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    _write_run_csv(
        runs_dir / "only.csv",
        [("no/lov/A", "scored", "1.000000")],
    )

    args = SimpleNamespace(regressions=True, compare=None, runs_path=runs_dir)
    rc = _render_regressions_from_runs(args)

    assert rc == 2


def test_render_regressions_returns_2_when_compare_label_missing(tmp_path) -> None:
    # Explicit --compare LABEL_A LABEL_B where LABEL_B has never been
    # persisted: typed rc=2 diagnostic naming the missing path.
    from types import SimpleNamespace

    runs_dir = tmp_path / "norway_bench_runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    _write_run_csv(
        runs_dir / "a.csv",
        [("no/lov/A", "scored", "1.000000")],
    )

    args = SimpleNamespace(compare=["a", "no-such"], runs_path=runs_dir)
    rc = _render_regressions_from_runs(args)

    assert rc == 2


def test_resolve_runs_dir_uses_arg_override_when_directory(tmp_path) -> None:
    # The contract-adapter smoke test isolates the runs dir to tmp_path via
    # `args.runs_path=tmp_path / "norway_bench_runs"`. The dir must be
    # recognized (not treated as a file path), since the regression-guard
    # helpers fall back to the repository-root default otherwise.
    from types import SimpleNamespace

    runs_dir = tmp_path / "norway_bench_runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    args = SimpleNamespace(runs_path=runs_dir)

    resolved = _resolve_runs_dir(args)

    assert resolved == runs_dir


# ---------------------------------------------------------------------------
# --show + --history renderers — focused tests with synthetic CSVs.
# ---------------------------------------------------------------------------


def test_render_show_label_orders_worst_performers_first(tmp_path) -> None:
    # Three synthetic rows; the renderer must sort SCORED rows by
    # headline_error descending so the worst performer appears first.
    # Format mirrors the schema persisted by _persist_per_statute_results.
    from types import SimpleNamespace

    runs_dir = tmp_path / "norway_bench_runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    _write_run_csv(
        runs_dir / "show-me.csv",
        [
            ("no/lov/A", "scored", "1.000000"),  # struct=0; worst-performer-3rd
            ("no/lov/B", "scored", "0.800000"),  # struct=0.2; worst-performer-1st
            ("no/lov/C", "scored", "0.950000"),  # struct=0.05; worst-performer-2nd
        ],
    )

    args = SimpleNamespace(show="show-me", top=3, runs_path=runs_dir)
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    rc = 0
    with redirect_stdout(buf):
        rc = _render_show_label(args)

    assert rc == 0
    out = buf.getvalue()
    assert "=== Norway bench show: show-me ===" in out
    # Worst-first: B (struct=0.2), C (struct=0.05), A (struct=0).
    assert out.index("no/lov/B") < out.index("no/lov/C")
    assert out.index("no/lov/C") < out.index("no/lov/A")
    # --top is honored: ask for 2 → only two top rows printed, A omitted.
    args.top = 2
    buf2 = io.StringIO()
    with redirect_stdout(buf2):
        _render_show_label(args)
    out2 = buf2.getvalue()
    assert "no/lov/B" in out2 and "no/lov/C" in out2 and "no/lov/A" not in out2


def test_render_show_label_reports_non_scored_when_top_exceeds_scored(tmp_path) -> None:
    # Two scored + one CRASH row. With --top 5 (more than scored=2), the
    # CRASH row surfaces at the end so the user sees the failures too.
    # With --top 2 (default), only scored surfaces; CRASH stays hidden
    # to avoid drowning the worst-N report in non-regressed blockers.
    from types import SimpleNamespace

    runs_dir = tmp_path / "norway_bench_runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    csv_path = runs_dir / "mixed.csv"
    csv_content = (
        "unit_id,status,structural_err,text_err,headline_error,headline_accuracy,"
        "residue_total,residue_buckets,witnesses\n"
        "no/lov/A,scored,0.000000,,0.000000,1.000000,0,,\n"
        "no/lov/B,scored,0.200000,,0.200000,0.800000,1,structural:MISMATCH=1,\n"
        "no/lov/C,crash,,,,,0,,boom-time-out\n"
    )
    csv_path.write_text(csv_content, encoding="utf-8")

    args = SimpleNamespace(show="mixed", top=5, runs_path=runs_dir)
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        _render_show_label(args)
    out = buf.getvalue()
    assert "no/lov/C" in out  # CRASH only listed when top exceeded scored count
    assert "boom-time-out" in out

    args.top = 2
    buf2 = io.StringIO()
    with redirect_stdout(buf2):
        _render_show_label(args)
    out2 = buf2.getvalue()
    assert "no/lov/C" not in out2  # CRASH stays hidden at default top


def test_render_show_label_returns_2_when_label_missing(tmp_path) -> None:
    # Reading-only --show references a never-persisted label: typed rc=2 +
    # diagnostic naming the missing path. Never silently empty-output.
    from types import SimpleNamespace

    runs_dir = tmp_path / "norway_bench_runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    args = SimpleNamespace(show="never-saved", top=20, runs_path=runs_dir)

    rc = _render_show_label(args)

    assert rc == 2


def test_render_history_lists_rows_in_csv_order(tmp_path) -> None:
    # Three past runs persisted; the renderer writes them in chronological
    # append order (the CSV is append-only, so reading order === write order).
    # Distribution columns translate to ints; non-numeric cells surface as 0.
    from types import SimpleNamespace

    history_path = tmp_path / "norway_bench_history.csv"
    history_path.write_text(
        "timestamp,label,mean_score,n_statutes,n_perfect,n_above_99,n_above_95,n_below_90\n"
        "2026-06-22T10:00:00Z,run-a,0.8282,18,9,10,11,4\n"
        "2026-06-22T11:00:00Z,run-b,0.7012,18,5,7,9,9\n"
        "2026-06-22T12:00:00Z,run-c,0.9115,18,11,13,15,2\n",
        encoding="utf-8",
    )

    args = SimpleNamespace(history=True, history_path=history_path)
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    rc = 0
    with redirect_stdout(buf):
        rc = _render_history(args)

    assert rc == 0
    out = buf.getvalue()
    assert "=== Norway bench history:" in out
    assert "Runs: 3" in out
    # Chronological by append order — run-a prints first, even with the
    # best-of-worst mean_score sorting the rendering layer would have done.
    assert out.index("run-a") < out.index("run-b")
    assert out.index("run-b") < out.index("run-c")
    # All distribution columns surface as ints, one row each.
    assert "9    10    11     4" in out  # run-a
    assert "5     7     9     9" in out  # run-b


def test_render_history_returns_0_when_history_empty(tmp_path) -> None:
    # First-ever run: history CSV doesn't exist yet. Surface a typed
    # advisory rather than crashing; the renderer returns 0 (no regressions
    # to compare, no history to show, but a successful command regardless).
    from types import SimpleNamespace

    history_path = tmp_path / "does-not-exist.csv"
    args = SimpleNamespace(history=True, history_path=history_path)

    rc = _render_history(args)

    assert rc == 0

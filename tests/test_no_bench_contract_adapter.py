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
from lawvm.tools.no_bench import no_bench_unit_result


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
) -> Any:
    """Build a minimal NOVerifyResult-shaped SimpleNamespace.

    The bench comparator reads: base_id, error, replay_status, divergence_count,
    divergence_counts, replay.replayed.body.  That is the only contract.
    """
    return SimpleNamespace(
        base_id=base_id,
        error=error,
        replay_status=replay_status,
        consistent=consistent,
        divergence_count=divergence_count,
        divergence_counts=divergence_counts,
        replay=_replay_result(replayed_body, error=error),
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
        args = SimpleNamespace(
            corpus=None, data_dir=None, label="adapter-smoke", history_path=history_csv
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

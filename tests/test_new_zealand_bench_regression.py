"""NZ bench smoke corpus regression guard (@slow).

Per AGENTS §3.4 expanded (2026-06-28): "For replay/lowering/temporal-resolution
changes with corpus-scale effect, the saved-run comparison is the full relevant
bench unless the scope is deliberately narrower and documented."

This module replaces the full-bench diff with a **structural invariant
regression guard** (AGENTS §2.9) that runs the NZ smoke bench and asserts the
key corpus-scale invariants hold against the documented post-merge state
(baseline saved at ``scripts/baselines/nz_smoke_2026-06-28.json``, commit
987714db). The structural invariants (similarity-freeze-free) are:

1. ALL 33 smoke-corpus works produce an OK bench report (no crash, no error).
2. At least 9 works have ``transitions_replayed > 0`` (matches the known
   actual-replay ground state under the strict-fail-closed promotion contract).
3. The work-level ``all_slices_agree`` is True on every transition-replaying
   work (the §1.12 slice-reconfirm defence-in-depth holds end-to-end).
4. The ``replay_bug`` residual family is bounded (<=120; documented 96).
5. The ``temporal_mismatch`` residual family is bounded (<=500; documented 260).
6. Baseline cross-check: aggregate transitions_replayed did NOT decrease.

@slow because it runs the full NZ bench on the smoke corpus (~21s after the
_localname EAFP speedup landed 2026-06-27).
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[1]
_REAL_DB = (
    Path(__import__("os").environ.get("LAWVM_CANONICAL_DATA_ROOT") or _REPO_ROOT)
    / "data"
    / "nz_legislation.farchive"
)

_BASELINE_PATH = _REPO_ROOT / "scripts" / "baselines" / "nz_smoke_2026-06-28.json"

_REPLAY_BUG_CEILING = 120
_TEMPORAL_MISMATCH_CEILING = 500
_MIN_REPLAYED_WORKS = 9


def _run_nz_smoke_bench() -> dict:
    """Run ``bench -j nz --smoke --json`` and return parsed JSON."""
    result = subprocess.run(
        ["uv", "run", "lawvm", "bench", "-j", "nz", "--smoke", "--json"],
        capture_output=True,
        text=True,
        cwd=str(_REPO_ROOT),
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"bench -j nz --smoke --json failed (exit={result.returncode}); "
            f"stderr[:400]={result.stderr[:400]}"
        )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"bench output is not valid JSON: {exc}; stdout[:400]={result.stdout[:400]}"
        )


@pytest.mark.skipif(not _REAL_DB.exists(), reason="NZ farchive not present")
@pytest.mark.skipif(not _BASELINE_PATH.exists(), reason="NZ baseline JSON not present")
@pytest.mark.slow
def test_nz_bench_smoke_structural_invariants_held_post_merge() -> None:
    """§3.4 saved-run comparison for the prior session's corpus-scale replay/
    lowering changes (part@xml_id widening, Family-D/F skip receipts, op-local
    divergence widening, effect_blocking_rule_id per-row derivation,
    target_provision_label alignment, _localname EAFP). The bounds are generous
    (96 documented → 120 ceiling; 260 documented → 500 ceiling) to catch ONLY
    real regressions, not measurement noise (AGENTS §2.9 structural-invariant
    form, not a fragile exact count).
    """
    bench = _run_nz_smoke_bench()

    works = bench.get("works", ())
    assert works, "bench output had no works"

    # Invariant 1: all works OK.
    assert all(w.get("work_status") == "OK" for w in works), (
        f"smoke bench: at least one work failed. Statuses: "
        f"{set(w.get('work_status', '?') for w in works)}"
    )

    # Invariant 2: at least 9 works cleanly replay.
    n_replay = sum(1 for w in works if w.get("transitions_replayed", 0) > 0)
    assert n_replay >= _MIN_REPLAYED_WORKS, (
        f"smoke bench: only {n_replay} works replayed (expected >= {_MIN_REPLAYED_WORKS})"
    )

    # Invariant 3: all replayed works agree on their target slice.
    disagree = [
        (w["work_id"], w.get("transitions_replayed", 0))
        for w in works
        if w.get("transitions_replayed", 0) > 0 and not w.get("all_slices_agree", True)
    ]
    assert not disagree, (
        f"smoke bench: {len(disagree)} transition-replaying work(s) had "
        f"all_slices_agree=False (§1.12 slice-reconfirm invariant). "
        f"offenders: {disagree[:8]}"
    )

    # Invariant 4: replay_bug bounded.
    residual_family = bench.get("summary", {}).get(
        "oracle_agreement_residual_family_counts", {}
    ) or {}
    n_bug = int(residual_family.get("replay_bug", 0))
    assert n_bug <= _REPLAY_BUG_CEILING, (
        f"smoke bench: {n_bug} replay_bug residuals (expected "
        f"<={_REPLAY_BUG_CEILING}; documented 96 post-Family-D/F closures)"
    )

    # Invariant 5: temporal_mismatch bounded.
    n_temporal = int(residual_family.get("temporal_mismatch", 0))
    assert n_temporal <= _TEMPORAL_MISMATCH_CEILING, (
        f"smoke bench: {n_temporal} temporal_mismatch residuals (expected "
        f"<={_TEMPORAL_MISMATCH_CEILING}; documented 260)"
    )

    # Invariant 6: baseline cross-check.
    baseline = json.loads(_BASELINE_PATH.read_text())
    baseline_summary = baseline.get("summary", {})
    assert baseline_summary.get("transitions_replayed", 0) <= bench.get(
        "summary", {}
    ).get("transitions_replayed", 0), (
        f"smoke bench: aggregate transitions_replayed decreased from "
        f"{baseline_summary.get('transitions_replayed', '?')} to "
        f"{bench.get('summary', {}).get('transitions_replayed', '?')}"
    )

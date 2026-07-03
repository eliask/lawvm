"""Tests for CTSF Phase 3 (task #186): the residual-set-diff GATE.

Covers the gate as a PARALLEL / REPORT-mode surface:

* the diff FAILs on a synthetic NEW ``replay_bug``/``unknown`` residual (the
  ``has_replay_bug_or_unknown`` predicate over the diff);
* the diff WARNs (never FAILs) on a NEW typed oracle/editorial/state-index/temporal
  residual — a non-billable move is telemetry, not a red gate;
* PASS iff the current typed-residual set equals the baseline exactly;
* determinism: same corpus ⇒ same verdict, byte-stable residual set;
* the frozen baseline round-trips (write → load → equal), and the COMMITTED
  baseline matches the frozen corpus (the on-disk artifact is not stale);
* report mode: the CLI ``main`` returns 0 regardless of verdict (never flips CI).
"""

from __future__ import annotations

import json

from lawvm.core.ctsf_gate import (
    FAIL_FAMILIES,
    GATE_BASELINE_PATH,
    GateResult,
    format_report,
    frozen_gate_corpus,
    load_baseline,
    main as ctsf_gate_main,
    residual_set,
    residual_set_diff_gate,
    run_gate_report,
    score_corpus,
    write_baseline,
)
from lawvm.core.ctsf_residual_report import RESIDUAL_VERDICT_FAMILIES


# ---------------------------------------------------------------------------
# The frozen corpus + baseline it produces
# ---------------------------------------------------------------------------


def test_frozen_corpus_scores_clean_baseline():
    """The frozen baseline corpus has NO billable (replay_bug/unknown) residual —
    it is the clean set the gate diffs against."""
    rs = score_corpus()
    billable = [
        f"{sid}:{fam}"
        for sid, families in rs.items()
        for fam in families
        if fam in FAIL_FAMILIES
    ]
    assert billable == [], f"frozen baseline corpus is not clean: {billable}"
    # It DOES exercise the typed non-billable lanes (state_index + cnf_unsupported),
    # so the WARN path is over real fixtures, not empty.
    all_fams = {fam for families in rs.values() for fam in families}
    assert "state_index" in all_fams
    assert "cnf_unsupported" in all_fams


def test_score_corpus_is_deterministic():
    assert score_corpus() == score_corpus()
    # Byte-stable serialization (dict order is sorted by sid).
    a = json.dumps(score_corpus(), sort_keys=False)
    b = json.dumps(score_corpus(), sort_keys=False)
    assert a == b


def test_residual_set_only_retains_nonzero_families():
    rs = score_corpus()
    for families in rs.values():
        for fam, count in families.items():
            assert fam in RESIDUAL_VERDICT_FAMILIES
            assert count > 0, "zero-count families must be dropped from the set"


# ---------------------------------------------------------------------------
# The diff gate — FAIL / WARN / PASS
# ---------------------------------------------------------------------------


def test_gate_passes_on_identical_set():
    rs = score_corpus()
    result = residual_set_diff_gate(rs, dict(rs))
    assert result.verdict == "PASS"
    assert result.failed is False
    assert result.new_billable == ()
    assert result.typed_moves == ()


def test_gate_fails_on_new_replay_bug_residual():
    """A NEW replay_bug residual vs baseline ⇒ FAIL (the billable lane)."""
    baseline = score_corpus()
    current = {**baseline, "synthetic/regressed": {"replay_bug": 1}}
    result = residual_set_diff_gate(current, baseline)
    assert result.verdict == "FAIL"
    assert result.failed is True
    assert any("replay_bug" in line for line in result.new_billable)


def test_gate_fails_on_new_unknown_residual():
    """A NEW unknown residual vs baseline ⇒ FAIL (the other billable lane)."""
    baseline = score_corpus()
    current = {**baseline, "synthetic/untyped": {"unknown": 1}}
    result = residual_set_diff_gate(current, baseline)
    assert result.verdict == "FAIL"
    assert any("unknown" in line for line in result.new_billable)


def test_gate_fails_when_existing_sid_grows_a_billable_residual():
    """A billable count RISING on an existing sid (not just a new sid) ⇒ FAIL."""
    baseline = {"s1": {"state_index": 1}}
    current = {"s1": {"state_index": 1, "unknown": 2}}
    result = residual_set_diff_gate(current, baseline)
    assert result.verdict == "FAIL"


def test_gate_warns_on_new_typed_nonbillable_residual():
    """A NEW typed oracle/editorial/state-index/temporal residual ⇒ WARN, not FAIL.
    The scalar moved but not in a billable family."""
    baseline = score_corpus()
    for family in (
        "oracle_editorial_pathology",
        "temporal_mismatch",
        "state_index",
        "cnf_unsupported",
    ):
        current = {**baseline, "synthetic/typed": {family: 1}}
        result = residual_set_diff_gate(current, baseline)
        assert result.verdict == "WARN", family
        assert result.failed is False
        assert any(family in line for line in result.typed_moves)


def test_gate_warns_on_resolved_residual():
    """A residual that FELL vs baseline is a WARN (reported, never silently eaten)."""
    baseline = {"s1": {"cnf_unsupported": 2}}
    current = {"s1": {"cnf_unsupported": 1}}
    result = residual_set_diff_gate(current, baseline)
    assert result.verdict == "WARN"
    assert any("cnf_unsupported" in line for line in result.resolved)


def test_gate_fail_beats_warn_when_both_move():
    """If a billable AND a typed move both appear, the verdict is FAIL (the strong
    signal dominates)."""
    baseline = score_corpus()
    current = {
        **baseline,
        "synthetic/billable": {"replay_bug": 1},
        "synthetic/typed": {"temporal_mismatch": 1},
    }
    result = residual_set_diff_gate(current, baseline)
    assert result.verdict == "FAIL"
    assert result.new_billable
    assert result.typed_moves


def test_gate_is_deterministic():
    baseline = score_corpus()
    current = {**baseline, "x": {"unknown": 1}}
    r1 = residual_set_diff_gate(current, baseline)
    r2 = residual_set_diff_gate(current, baseline)
    assert r1.to_dict() == r2.to_dict()


# ---------------------------------------------------------------------------
# Frozen baseline round-trip + committed-artifact freshness
# ---------------------------------------------------------------------------


def test_baseline_round_trips(tmp_path):
    path = tmp_path / "baseline.json"
    write_baseline(path=path)
    loaded = load_baseline(path)
    assert loaded == score_corpus()


def test_committed_baseline_matches_corpus():
    """The on-disk committed baseline is not stale: it equals the frozen corpus's
    current residual set. If this fails, regenerate:
        uv run python -m lawvm.core.ctsf_gate --update-baseline"""
    committed = load_baseline()
    assert committed == score_corpus(), (
        "Committed CTSF gate baseline is stale vs the frozen corpus. Regenerate "
        "with `uv run python -m lawvm.core.ctsf_gate --update-baseline`."
    )


def test_committed_baseline_gate_passes():
    """Running the gate against the committed baseline PASSES — the clean state."""
    result = run_gate_report()
    assert isinstance(result, GateResult)
    assert result.verdict == "PASS"


def test_baseline_path_is_under_tests_data():
    assert GATE_BASELINE_PATH.parts[:2] == ("tests", "data")


# ---------------------------------------------------------------------------
# Report mode — never flips CI red
# ---------------------------------------------------------------------------


def test_cli_report_mode_always_returns_zero(capsys):
    rc = ctsf_gate_main([])
    assert rc == 0
    out = capsys.readouterr().out
    assert "PARALLEL / REPORT MODE" in out
    assert "verdict:" in out


def test_cli_json_mode_returns_zero(capsys):
    rc = ctsf_gate_main(["--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["verdict"] in ("PASS", "WARN", "FAIL")
    assert "current" in payload and "baseline" in payload


def test_format_report_labels_parallel_mode():
    result = run_gate_report()
    text = format_report(result)
    assert "PARALLEL / REPORT MODE" in text
    assert "legacy scalar bench gate UNCHANGED" in text


def test_residual_set_over_reports_matches_score_corpus():
    """residual_set(corpus reports) == score_corpus() — the two entrypoints agree."""
    reports = [a.report() for a in frozen_gate_corpus()]
    assert residual_set(reports) == score_corpus()


# ---------------------------------------------------------------------------
# Guard: the FAIL families are exactly the billable ones
# ---------------------------------------------------------------------------


def test_fail_families_are_replay_bug_and_unknown():
    assert set(FAIL_FAMILIES) == {"replay_bug", "unknown"}
    for fam in FAIL_FAMILIES:
        assert fam in RESIDUAL_VERDICT_FAMILIES


def test_no_billable_family_silently_downgraded():
    """A billable family appearing anywhere in the diff is never routed to
    typed_moves (the WARN bucket) — the FAIL lane owns them exclusively."""
    baseline = {"s": {}}
    current = {"s": {"replay_bug": 1, "unknown": 1}}
    result = residual_set_diff_gate(current, baseline)
    assert result.verdict == "FAIL"
    assert not any(
        fam in line for line in result.typed_moves for fam in FAIL_FAMILIES
    )

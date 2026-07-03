"""Tests for the ESTONIA CTSF residual-set-diff gate corpus (task #205).

The EE analogue of ``test_ctsf_gate.py``: extends the now-PRIMARY CTSF gate to a
second jurisdiction. Covers:

* the frozen EE corpus membership is content-pinned (explicit, sorted, unique);
* the committed EE baseline self-describes (jurisdiction + corpus sids) and carries
  ZERO billable (replay_bug/unknown) residuals — the honest 0-billable steady state;
* the EE diff FAILs on a synthetic NEW billable residual and WARNs on a typed
  non-billable move (the same diff logic as FI, over the EE baseline);
* the baseline round-trips (write → load → equal);
* FAIL-RED wiring: ``run_ee_gate_report`` PASSes against the committed baseline
  (data-present) and the multi-jurisdiction ``run_gate`` / CLI fold the EE verdict
  into the exit code; the EE lane SKIPS clean when the RT archive is absent.

The data-present tests score the EE corpus via replay over the Riigi Teataja
Farchive; they SKIP cleanly when it is absent (a corpus-free CI checkout). The
unit surface (diff logic over the committed baseline, round-trip) is corpus-free.
"""

from __future__ import annotations

import json

import pytest

from lawvm.core.ctsf_gate import (
    FAIL_FAMILIES,
    GATE_EE_BASELINE_PATH,
    REAL_ANCHOR_EE_CORPUS_SIDS,
    REAL_ANCHOR_EE_JURISDICTION,
    GateResult,
    _repo_root,
    ee_anchor_corpus_available,
    load_ee_baseline,
    residual_set_diff_gate,
    run_ee_gate_report,
    run_gate,
    score_ee_real_corpus,
    write_ee_baseline,
)
from lawvm.core.ctsf_residual_report import RESIDUAL_VERDICT_FAMILIES

# The EE corpus is scored via replay over the RT Farchive; skip the real-corpus
# tests cleanly when it is absent. The unit surface is corpus-free and always runs.
requires_ee_corpus = pytest.mark.skipif(
    not ee_anchor_corpus_available(),
    reason="Riigi Teataja Farchive absent; EE #205 anchor corpus cannot be scored",
)


# ---------------------------------------------------------------------------
# Frozen corpus membership + committed baseline (corpus-free)
# ---------------------------------------------------------------------------


def test_ee_corpus_sids_are_frozen_and_sorted():
    """The EE corpus membership is content-pinned: an explicit, sorted, unique,
    non-empty tuple (no live enumeration)."""
    assert isinstance(REAL_ANCHOR_EE_CORPUS_SIDS, tuple)
    assert REAL_ANCHOR_EE_CORPUS_SIDS
    assert list(REAL_ANCHOR_EE_CORPUS_SIDS) == sorted(REAL_ANCHOR_EE_CORPUS_SIDS)
    assert len(set(REAL_ANCHOR_EE_CORPUS_SIDS)) == len(REAL_ANCHOR_EE_CORPUS_SIDS)


def test_committed_ee_baseline_declares_corpus():
    """The committed EE baseline records the jurisdiction + frozen corpus sids."""
    data = json.loads(
        (_repo_root() / GATE_EE_BASELINE_PATH).read_text(encoding="utf-8")
    )
    assert data["jurisdiction"] == REAL_ANCHOR_EE_JURISDICTION
    assert tuple(data["corpus_sids"]) == REAL_ANCHOR_EE_CORPUS_SIDS


def test_committed_ee_baseline_is_zero_billable():
    """The committed EE baseline carries ZERO billable (replay_bug/unknown) residuals
    — the honest 0-billable steady state.

    Provenance: the EE corpus is curated to clean statute families. Deep multi-
    amendment EE chains that surfaced GENUINE replay text-preservation bugs
    (``1022254`` §2 dropped a COFOG clause; ``1048615``) are DELIBERATELY EXCLUDED
    — they are defects to fix, not to freeze. So the EE gate's FAIL lane is proven by
    synthetic injection (``test_ee_new_billable_fails``), not a standing residual.
    """
    committed = load_ee_baseline()
    committed_billable = sum(
        families.get(fam, 0)
        for families in committed.values()
        for fam in FAIL_FAMILIES
    )
    assert committed_billable == 0, (
        "The committed EE baseline carries a standing billable residual. If a deep EE "
        "chain legitimately surfaced a NEW real replay bug this is a preregistered "
        "event — investigate + attribute it (bug fix or evidenced oracle_suspect "
        "re-typing), do not freeze an un-triaged billable."
    )


def test_committed_ee_baseline_exercises_typed_nonbillable_lane():
    """The EE baseline is not vacuous: it exercises a typed non-billable family
    (``oracle_editorial_pathology`` on ``1055878``) so the WARN lane is over a real
    fixture, not empty."""
    committed = load_ee_baseline()
    all_fams = {fam for families in committed.values() for fam in families}
    assert "oracle_editorial_pathology" in all_fams
    for families in committed.values():
        for fam, count in families.items():
            assert fam in RESIDUAL_VERDICT_FAMILIES
            assert count > 0, "zero-count families must be dropped from the set"


# ---------------------------------------------------------------------------
# The diff gate over the EE baseline — FAIL / WARN / PASS (corpus-free)
# ---------------------------------------------------------------------------


def test_ee_gate_passes_on_identical_set():
    base = load_ee_baseline()
    result = residual_set_diff_gate(base, dict(base))
    assert result.verdict == "PASS"
    assert result.failed is False


def test_ee_new_billable_fails():
    """A synthetic NEW replay_bug/unknown injected on top of the committed EE baseline
    FAILs the gate — the two lanes the honest metric exists to guard."""
    base = load_ee_baseline()
    for fam in FAIL_FAMILIES:
        current = {**base, "synthetic/regressed": {fam: 1}}
        result = residual_set_diff_gate(current, base)
        assert result.verdict == "FAIL", fam
        assert any(fam in line for line in result.new_billable)


def test_ee_typed_move_warns():
    """A NEW typed non-billable residual over the EE baseline WARNs, never FAILs."""
    base = load_ee_baseline()
    for fam in ("oracle_editorial_pathology", "temporal_mismatch", "state_index"):
        current = {**base, "synthetic/typed": {fam: 1}}
        result = residual_set_diff_gate(current, base)
        assert result.verdict == "WARN", fam
        assert result.failed is False


def test_ee_baseline_round_trips(tmp_path):
    """write → load round-trips an EE residual set byte-for-byte (corpus-free: uses an
    explicit synthetic set so the round-trip needs no RT archive)."""
    residuals = {
        "g/1": {"replay_bug": 1, "temporal_mismatch": 2},
        "g/2": {},
        "g/3": {"oracle_editorial_pathology": 3},
    }
    path = tmp_path / "ee_baseline.json"
    write_ee_baseline(residuals=residuals, path=path)
    assert load_ee_baseline(path) == residuals


def test_ee_baseline_path_is_under_tests_data():
    assert GATE_EE_BASELINE_PATH.parts[:2] == ("tests", "data")


# ---------------------------------------------------------------------------
# Data-present: the EE real corpus scores 0-billable + PASSes; fail-red wiring.
# ---------------------------------------------------------------------------


@requires_ee_corpus
def test_ee_real_corpus_scores_deterministically():
    """Same frozen corpus bytes ⇒ same typed-residual set (byte-stable)."""
    a = score_ee_real_corpus()
    b = score_ee_real_corpus()
    assert a == b
    assert json.dumps(a, sort_keys=False) == json.dumps(b, sort_keys=False)


@requires_ee_corpus
def test_committed_ee_baseline_matches_real_corpus():
    """The on-disk EE baseline is not stale: it equals the real EE corpus's current
    residual set. A move is a preregistered predict-then-compare event. Regenerate:
        uv run python -m lawvm.core.ctsf_gate --update-ee-baseline"""
    assert load_ee_baseline() == score_ee_real_corpus(), (
        "Committed EE CTSF baseline is stale vs the real #205 corpus. Confirm the move "
        "is legitimate, then regenerate with "
        "`uv run python -m lawvm.core.ctsf_gate --update-ee-baseline`."
    )


@requires_ee_corpus
def test_ee_real_corpus_is_zero_billable():
    """The real EE corpus scored live carries no billable residual — the honest
    steady state (not just the committed snapshot)."""
    live = score_ee_real_corpus()
    billable = sum(
        families.get(fam, 0)
        for families in live.values()
        for fam in FAIL_FAMILIES
    )
    assert billable == 0


@requires_ee_corpus
def test_ee_real_corpus_gate_passes():
    """The EE corpus scored against its committed baseline PASSes (empty diff)."""
    result = run_ee_gate_report()
    assert isinstance(result, GateResult)
    assert result.verdict == "PASS"


@requires_ee_corpus
def test_run_gate_passes_clean_ee_returns_zero():
    """The multi-jurisdiction PRIMARY callable gate returns 0 on the clean EE (and FI,
    if present) baseline."""
    assert run_gate() == 0

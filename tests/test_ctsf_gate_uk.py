"""Tests for the UNITED KINGDOM CTSF residual-set-diff gate corpus (task #205).

The UK analogue of ``test_ctsf_gate_ee.py``: extends the now-PRIMARY CTSF gate to a
third jurisdiction. Covers:

* the frozen UK corpus membership is content-pinned (explicit, sorted, unique);
* the committed UK baseline self-describes (jurisdiction + corpus sids) and carries
  ZERO billable (replay_bug/unknown) residuals — the honest 0-billable steady state;
* the UK diff FAILs on a synthetic NEW billable residual and WARNs on a typed
  non-billable move (the same diff logic as FI/EE, over the UK baseline);
* the baseline round-trips (write → load → equal);
* FAIL-RED wiring: ``run_uk_gate_report`` PASSes against the committed baseline
  (data-present) and the multi-jurisdiction ``run_gate`` / CLI fold the UK verdict
  into the exit code; the UK lane SKIPS clean when the UK archive is absent.

The data-present tests score the UK corpus via enacted→current replay over the
legislation.gov.uk Farchive; they SKIP cleanly when it is absent (a corpus-free CI
checkout). The unit surface (diff logic over the committed baseline, round-trip) is
corpus-free.
"""

from __future__ import annotations

import json

import pytest

from lawvm.core.ctsf_gate import (
    FAIL_FAMILIES,
    GATE_UK_BASELINE_PATH,
    REAL_ANCHOR_UK_CORPUS_SIDS,
    REAL_ANCHOR_UK_JURISDICTION,
    GateResult,
    _repo_root,
    load_uk_baseline,
    residual_set_diff_gate,
    run_gate,
    run_uk_gate_report,
    score_uk_real_corpus,
    uk_anchor_corpus_available,
    write_uk_baseline,
)
from lawvm.core.ctsf_residual_report import RESIDUAL_VERDICT_FAMILIES

# The UK corpus is scored via replay over the UK Farchive; skip the real-corpus tests
# cleanly when it is absent. The unit surface is corpus-free and always runs.
requires_uk_corpus = pytest.mark.skipif(
    not uk_anchor_corpus_available(),
    reason="legislation.gov.uk Farchive absent; UK #205 anchor corpus cannot be scored",
)


# ---------------------------------------------------------------------------
# Frozen corpus membership + committed baseline (corpus-free)
# ---------------------------------------------------------------------------


def test_uk_corpus_sids_are_frozen_and_sorted():
    """The UK corpus membership is content-pinned: an explicit, sorted, unique,
    non-empty tuple (no live enumeration)."""
    assert isinstance(REAL_ANCHOR_UK_CORPUS_SIDS, tuple)
    assert REAL_ANCHOR_UK_CORPUS_SIDS
    assert list(REAL_ANCHOR_UK_CORPUS_SIDS) == sorted(REAL_ANCHOR_UK_CORPUS_SIDS)
    assert len(set(REAL_ANCHOR_UK_CORPUS_SIDS)) == len(REAL_ANCHOR_UK_CORPUS_SIDS)


def test_committed_uk_baseline_declares_corpus():
    """The committed UK baseline records the jurisdiction + frozen corpus sids."""
    data = json.loads(
        (_repo_root() / GATE_UK_BASELINE_PATH).read_text(encoding="utf-8")
    )
    assert data["jurisdiction"] == REAL_ANCHOR_UK_JURISDICTION
    assert tuple(data["corpus_sids"]) == REAL_ANCHOR_UK_CORPUS_SIDS


def test_committed_uk_baseline_is_zero_billable():
    """The committed UK baseline carries ZERO billable (replay_bug/unknown) residuals
    — the honest 0-billable steady state.

    Provenance: the UK corpus is curated to acts whose enacted→current replay
    reproduces every replay-touched oracle eId. UK acts whose replay surfaces GENUINE
    billable residuals (a replay-touched eId the oracle carries that replay drops) are
    DELIBERATELY EXCLUDED — they are defects to fix, not to freeze. So the UK gate's
    FAIL lane is proven by synthetic injection (``test_uk_new_billable_fails``), not a
    standing residual.
    """
    committed = load_uk_baseline()
    committed_billable = sum(
        families.get(fam, 0)
        for families in committed.values()
        for fam in FAIL_FAMILIES
    )
    assert committed_billable == 0, (
        "The committed UK baseline carries a standing billable residual. If a UK act "
        "legitimately surfaced a NEW real replay bug this is a preregistered event — "
        "investigate + attribute it (bug fix or evidenced oracle_suspect re-typing), "
        "do not freeze an un-triaged billable."
    )


def test_committed_uk_baseline_families_are_typed():
    """Every family in the UK baseline is a recognized residual family with a
    positive count (zero-count families must be dropped from the set)."""
    committed = load_uk_baseline()
    for families in committed.values():
        for fam, count in families.items():
            assert fam in RESIDUAL_VERDICT_FAMILIES
            assert count > 0, "zero-count families must be dropped from the set"


# ---------------------------------------------------------------------------
# The diff gate over the UK baseline — FAIL / WARN / PASS (corpus-free)
# ---------------------------------------------------------------------------


def test_uk_gate_passes_on_identical_set():
    base = load_uk_baseline()
    result = residual_set_diff_gate(base, dict(base))
    assert result.verdict == "PASS"
    assert result.failed is False


def test_uk_new_billable_fails():
    """A synthetic NEW replay_bug/unknown injected on top of the committed UK baseline
    FAILs the gate — the two lanes the honest metric exists to guard."""
    base = load_uk_baseline()
    for fam in FAIL_FAMILIES:
        current = {**base, "synthetic/regressed": {fam: 1}}
        result = residual_set_diff_gate(current, base)
        assert result.verdict == "FAIL", fam
        assert any(fam in line for line in result.new_billable)


def test_uk_typed_move_warns():
    """A NEW typed non-billable residual over the UK baseline WARNs, never FAILs."""
    base = load_uk_baseline()
    for fam in ("oracle_editorial_pathology", "temporal_mismatch", "state_index"):
        current = {**base, "synthetic/typed": {fam: 1}}
        result = residual_set_diff_gate(current, base)
        assert result.verdict == "WARN", fam
        assert result.failed is False


def test_uk_baseline_round_trips(tmp_path):
    """write → load round-trips a UK residual set byte-for-byte (corpus-free: uses an
    explicit synthetic set so the round-trip needs no UK archive)."""
    residuals = {
        "ukpga/1/1": {"replay_bug": 1, "temporal_mismatch": 2},
        "ukpga/2/2": {},
        "ukpga/3/3": {"oracle_editorial_pathology": 3},
    }
    path = tmp_path / "uk_baseline.json"
    write_uk_baseline(residuals=residuals, path=path)
    assert load_uk_baseline(path) == residuals


def test_uk_baseline_path_is_under_tests_data():
    assert GATE_UK_BASELINE_PATH.parts[:2] == ("tests", "data")


# ---------------------------------------------------------------------------
# Data-present: the UK real corpus scores 0-billable + PASSes; fail-red wiring.
# ---------------------------------------------------------------------------


@requires_uk_corpus
def test_uk_real_corpus_scores_deterministically():
    """Same frozen corpus bytes ⇒ same typed-residual set (byte-stable)."""
    a = score_uk_real_corpus()
    b = score_uk_real_corpus()
    assert a == b
    assert json.dumps(a, sort_keys=False) == json.dumps(b, sort_keys=False)


@requires_uk_corpus
def test_committed_uk_baseline_matches_real_corpus():
    """The on-disk UK baseline is not stale: it equals the real UK corpus's current
    residual set. A move is a preregistered predict-then-compare event. Regenerate:
        uv run python -m lawvm.core.ctsf_gate --update-uk-baseline"""
    assert load_uk_baseline() == score_uk_real_corpus(), (
        "Committed UK CTSF baseline is stale vs the real #205 corpus. Confirm the move "
        "is legitimate, then regenerate with "
        "`uv run python -m lawvm.core.ctsf_gate --update-uk-baseline`."
    )


@requires_uk_corpus
def test_uk_real_corpus_is_zero_billable():
    """The real UK corpus scored live carries no billable residual — the honest
    steady state (not just the committed snapshot)."""
    live = score_uk_real_corpus()
    billable = sum(
        families.get(fam, 0)
        for families in live.values()
        for fam in FAIL_FAMILIES
    )
    assert billable == 0


@requires_uk_corpus
def test_uk_real_corpus_gate_passes():
    """The UK corpus scored against its committed baseline PASSes (empty diff)."""
    result = run_uk_gate_report()
    assert isinstance(result, GateResult)
    assert result.verdict == "PASS"


@requires_uk_corpus
def test_run_gate_passes_clean_uk_returns_zero():
    """The multi-jurisdiction PRIMARY callable gate returns 0 on the clean UK (and
    FI/EE, if present) baseline."""
    assert run_gate() == 0

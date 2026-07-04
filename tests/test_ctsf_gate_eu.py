"""Tests for the EUROPEAN UNION CTSF residual-set-diff gate corpus (task #204).

The EU analogue of ``test_ctsf_gate.py`` / ``test_ctsf_gate_ee.py`` /
``test_ctsf_gate_uk.py``: extends the now-PRIMARY CTSF gate to a FOURTH jurisdiction —
the last un-gated one. Covers:

* the frozen EU corpus membership is content-pinned (explicit ``(amender, base)``
  pairs, sorted, unique);
* the committed EU baseline self-describes (jurisdiction + corpus chains) and carries
  ZERO billable (replay_bug/unknown) residuals — the honest 0-billable steady state;
* the EU diff FAILs on a synthetic NEW billable residual and WARNs on a typed
  non-billable move (the same diff logic as FI/EE/UK, over the EU baseline);
* the baseline round-trips (write → load → equal);
* FAIL-RED wiring: ``run_eu_gate_report`` PASSes against the committed baseline
  (data-present) and the multi-jurisdiction ``run_gate`` folds the EU verdict into the
  exit code; the EU lane SKIPS clean when the EU Cellar Farchive is absent.

WHY EU IS DIFFERENT (documented, load-bearing). Unlike FI/EE/UK, the EU Cellar corpus
stores NO consolidation oracle and NO dated amendment DAG, so there is no published
oracle to score the native replay against. The EU gate therefore scores a genuinely
available, deterministic, offline property instead — the conserved apply fold's
invariant (``|applied| + |skipped| == |ops|``) over an (amender→base) replay window —
whose violation (or an apply RAISE) is a real ``replay_bug`` / ``unknown``. See
``lawvm.tools.eu_anchor_manifest`` and the EU section of ``lawvm.core.ctsf_gate``.

The data-present tests score the EU corpus via offline replay over the EU Cellar
Farchive; they SKIP cleanly when it is absent (a corpus-free CI checkout). The unit
surface (diff logic over the committed baseline, round-trip) is corpus-free.
"""

from __future__ import annotations

import json

import pytest

from lawvm.core.ctsf_gate import (
    FAIL_FAMILIES,
    GATE_EU_BASELINE_PATH,
    REAL_ANCHOR_EU_CORPUS_CHAINS,
    REAL_ANCHOR_EU_JURISDICTION,
    GateResult,
    _repo_root,
    eu_anchor_corpus_available,
    load_eu_baseline,
    residual_set_diff_gate,
    run_eu_gate_report,
    run_gate,
    score_eu_real_corpus,
    write_eu_baseline,
)
from lawvm.core.ctsf_residual_report import RESIDUAL_VERDICT_FAMILIES

# The EU corpus is scored via offline replay over the EU Cellar Farchive; skip the
# real-corpus tests cleanly when it is absent. The unit surface is corpus-free and
# always runs.
requires_eu_corpus = pytest.mark.skipif(
    not eu_anchor_corpus_available(),
    reason="EU Cellar Farchive absent; EU #204 anchor corpus cannot be scored",
)


# ---------------------------------------------------------------------------
# Frozen corpus membership + committed baseline (corpus-free)
# ---------------------------------------------------------------------------


def test_eu_corpus_chains_are_frozen_and_sorted():
    """The EU corpus membership is content-pinned: an explicit, sorted, unique,
    non-empty tuple of ``(amender, base)`` CELEX pairs (no live enumeration)."""
    assert isinstance(REAL_ANCHOR_EU_CORPUS_CHAINS, tuple)
    assert REAL_ANCHOR_EU_CORPUS_CHAINS
    assert list(REAL_ANCHOR_EU_CORPUS_CHAINS) == sorted(REAL_ANCHOR_EU_CORPUS_CHAINS)
    assert len(set(REAL_ANCHOR_EU_CORPUS_CHAINS)) == len(REAL_ANCHOR_EU_CORPUS_CHAINS)
    for chain in REAL_ANCHOR_EU_CORPUS_CHAINS:
        assert isinstance(chain, tuple) and len(chain) == 2
        amender, base = chain
        assert amender and base and amender != base


def test_committed_eu_baseline_declares_corpus():
    """The committed EU baseline records the jurisdiction + frozen corpus chains."""
    data = json.loads(
        (_repo_root() / GATE_EU_BASELINE_PATH).read_text(encoding="utf-8")
    )
    assert data["jurisdiction"] == REAL_ANCHOR_EU_JURISDICTION
    assert tuple(tuple(c) for c in data["corpus_chains"]) == REAL_ANCHOR_EU_CORPUS_CHAINS


def test_committed_eu_baseline_is_zero_billable():
    """The committed EU baseline carries ZERO billable (replay_bug/unknown) residuals
    — the honest 0-billable steady state.

    Provenance: a full sweep of the #204 ZIP-recovered acts found 49 offline-replayable
    base+amender chains and ZERO apply-raises / conservation violations (0 real billable
    EU replay bugs). So the EU gate's FAIL lane is proven by synthetic injection
    (``test_eu_new_billable_fails``), not a standing residual. If a future corpus/replay
    change surfaces a NEW real billable, that is a preregistered event to attribute (bug
    fix), never to freeze green.
    """
    committed = load_eu_baseline()
    committed_billable = sum(
        families.get(fam, 0)
        for families in committed.values()
        for fam in FAIL_FAMILIES
    )
    assert committed_billable == 0, (
        "The committed EU baseline carries a standing billable residual. If a replay "
        "window legitimately surfaced a NEW real replay bug this is a preregistered "
        "event — investigate + attribute it, do not freeze an un-triaged billable."
    )


def test_committed_eu_baseline_exercises_typed_nonbillable_lane():
    """The EU baseline is not vacuous: it exercises a typed non-billable family
    (``cnf_unsupported`` on a typed-op-skip window) so the WARN lane is over a real
    fixture, not empty."""
    committed = load_eu_baseline()
    all_fams = {fam for families in committed.values() for fam in families}
    assert "cnf_unsupported" in all_fams
    for families in committed.values():
        for fam, count in families.items():
            assert fam in RESIDUAL_VERDICT_FAMILIES
            assert count > 0, "zero-count families must be dropped from the set"


# ---------------------------------------------------------------------------
# The diff gate over the EU baseline — FAIL / WARN / PASS (corpus-free)
# ---------------------------------------------------------------------------


def test_eu_gate_passes_on_identical_set():
    base = load_eu_baseline()
    result = residual_set_diff_gate(base, dict(base))
    assert result.verdict == "PASS"
    assert result.failed is False


def test_eu_new_billable_fails():
    """A synthetic NEW replay_bug/unknown injected on top of the committed EU baseline
    FAILs the gate — the two lanes the honest metric exists to guard (an EU apply RAISE
    projects to replay_bug, a conservation violation to unknown)."""
    base = load_eu_baseline()
    for fam in FAIL_FAMILIES:
        current = {**base, "synthetic->regressed": {fam: 1}}
        result = residual_set_diff_gate(current, base)
        assert result.verdict == "FAIL", fam
        assert any(fam in line for line in result.new_billable)


def test_eu_typed_move_warns():
    """A NEW typed non-billable residual over the EU baseline WARNs, never FAILs."""
    base = load_eu_baseline()
    for fam in ("cnf_unsupported", "oracle_editorial_pathology", "temporal_mismatch"):
        current = {**base, "synthetic->typed": {fam: 1}}
        result = residual_set_diff_gate(current, base)
        assert result.verdict == "WARN", fam
        assert result.failed is False


def test_eu_baseline_round_trips(tmp_path):
    """write → load round-trips an EU residual set byte-for-byte (corpus-free: uses an
    explicit synthetic set so the round-trip needs no EU Cellar Farchive)."""
    residuals = {
        "a->b": {"replay_bug": 1, "cnf_unsupported": 2},
        "c->d": {},
        "e->f": {"unknown": 3},
    }
    path = tmp_path / "eu_baseline.json"
    write_eu_baseline(residuals=residuals, path=path)
    assert load_eu_baseline(path) == residuals


def test_eu_baseline_path_is_under_tests_data():
    assert GATE_EU_BASELINE_PATH.parts[:2] == ("tests", "data")


# ---------------------------------------------------------------------------
# Data-present: the EU real corpus scores 0-billable + PASSes; fail-red wiring.
# ---------------------------------------------------------------------------


@requires_eu_corpus
def test_eu_real_corpus_scores_deterministically():
    """Same frozen corpus bytes ⇒ same typed-residual set (byte-stable)."""
    a = score_eu_real_corpus()
    b = score_eu_real_corpus()
    assert a == b
    assert json.dumps(a, sort_keys=False) == json.dumps(b, sort_keys=False)


@requires_eu_corpus
def test_committed_eu_baseline_matches_real_corpus():
    """The on-disk EU baseline is not stale: it equals the real EU corpus's current
    residual set. A move is a preregistered predict-then-compare event. Regenerate:
        uv run python -m lawvm.core.ctsf_gate --update-eu-baseline"""
    assert load_eu_baseline() == score_eu_real_corpus(), (
        "Committed EU CTSF baseline is stale vs the real #204 corpus. Confirm the move "
        "is legitimate, then regenerate with "
        "`uv run python -m lawvm.core.ctsf_gate --update-eu-baseline`."
    )


@requires_eu_corpus
def test_eu_real_corpus_is_zero_billable():
    """The real EU corpus scored live carries no billable residual — the honest steady
    state (not just the committed snapshot)."""
    live = score_eu_real_corpus()
    billable = sum(
        families.get(fam, 0)
        for families in live.values()
        for fam in FAIL_FAMILIES
    )
    assert billable == 0


@requires_eu_corpus
def test_eu_real_corpus_gate_passes():
    """The EU corpus scored against its committed baseline PASSes (empty diff)."""
    result = run_eu_gate_report()
    assert isinstance(result, GateResult)
    assert result.verdict == "PASS"


@requires_eu_corpus
def test_run_gate_passes_clean_eu_returns_zero():
    """The multi-jurisdiction PRIMARY callable gate returns 0 on the clean EU (and FI/
    EE/UK, if present) baseline."""
    assert run_gate() == 0

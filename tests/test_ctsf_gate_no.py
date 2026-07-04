"""Tests for the NORWAY CTSF residual-set-diff gate corpus (#183/#205).

The Norway analogue of ``test_ctsf_gate_uk.py`` / ``test_ctsf_gate_ee.py``: extends
the now-PRIMARY CTSF gate to a fourth jurisdiction. The NO engine lives in
``lawvm.tools.no_anchor_manifest`` (it exposes ``score_no_real_corpus()`` + the frozen
baseline for the parent to integrate into ``core.ctsf_gate``; this test binds the
engine directly, not through ``core.ctsf_gate``). Covers:

* the frozen NO corpus membership is content-pinned (explicit, sorted, unique);
* the committed NO baseline self-describes (jurisdiction + corpus sids) and carries
  ZERO billable (replay_bug/unknown) residuals — the honest 0-billable steady state;
* the NO diff FAILs on a synthetic NEW billable residual and WARNs on a typed
  non-billable move (the shared ``residual_set_diff_gate`` diff logic);
* the baseline round-trips (write → load → equal);
* data-present: the real NO corpus scores deterministically, 0-billable, and PASSes
  its committed baseline; the NO lane SKIPS clean when the Norway archive is absent.

The data-present tests score the NO corpus via base→current replay over the Norway
Farchive; they SKIP cleanly when it is absent. The unit surface (diff logic over the
committed baseline, round-trip) is corpus-free.
"""

from __future__ import annotations

import json

import pytest

from lawvm.core.ctsf_gate import (
    FAIL_FAMILIES,
    GateResult,
    residual_set_diff_gate,
)
from lawvm.core.ctsf_residual_report import RESIDUAL_VERDICT_FAMILIES
from lawvm.tools.no_anchor_manifest import (
    GATE_NO_BASELINE_PATH,
    REAL_ANCHOR_NO_CORPUS,
    REAL_ANCHOR_NO_CORPUS_SIDS,
    REAL_ANCHOR_NO_JURISDICTION,
    _repo_root,
    load_no_baseline,
    no_anchor_corpus_available,
    run_no_gate_report,
    score_no_real_corpus,
    write_no_baseline,
)

# The NO corpus is scored via replay over the Norway Farchive; skip the real-corpus
# tests cleanly when it is absent. The unit surface is corpus-free and always runs.
requires_no_corpus = pytest.mark.skipif(
    not no_anchor_corpus_available(),
    reason="Norway Farchive absent; NO #205 anchor corpus cannot be scored",
)


# ---------------------------------------------------------------------------
# Frozen corpus membership + committed baseline (corpus-free)
# ---------------------------------------------------------------------------


def test_no_corpus_is_frozen_sorted_unique():
    """The NO corpus is content-pinned: explicit ``(base_id, as_of)`` pairs, unique
    base_ids, and a sorted/unique sid tuple (no live enumeration)."""
    assert isinstance(REAL_ANCHOR_NO_CORPUS, tuple)
    assert REAL_ANCHOR_NO_CORPUS
    base_ids = [base_id for base_id, _as_of in REAL_ANCHOR_NO_CORPUS]
    assert len(set(base_ids)) == len(base_ids)
    assert isinstance(REAL_ANCHOR_NO_CORPUS_SIDS, tuple)
    assert list(REAL_ANCHOR_NO_CORPUS_SIDS) == sorted(REAL_ANCHOR_NO_CORPUS_SIDS)
    assert len(set(REAL_ANCHOR_NO_CORPUS_SIDS)) == len(REAL_ANCHOR_NO_CORPUS_SIDS)
    assert set(REAL_ANCHOR_NO_CORPUS_SIDS) == set(base_ids)


def test_committed_no_baseline_declares_corpus():
    """The committed NO baseline records the jurisdiction + frozen corpus sids."""
    data = json.loads(
        (_repo_root() / GATE_NO_BASELINE_PATH).read_text(encoding="utf-8")
    )
    assert data["jurisdiction"] == REAL_ANCHOR_NO_JURISDICTION
    assert tuple(data["corpus_sids"]) == REAL_ANCHOR_NO_CORPUS_SIDS


def test_committed_no_baseline_is_zero_billable():
    """The committed NO baseline carries ZERO billable (replay_bug/unknown) residuals
    — the honest 0-billable steady state.

    Provenance: the NO corpus is curated to lov acts whose base→current replay
    reproduces every replay-touched oracle section. NO acts whose replay surfaces
    GENUINE billable residuals (e.g. no/lov/2020-05-07-38 § 10-64 sunset-date, and
    no/lov/2020-12-18-156 § 5) are DELIBERATELY EXCLUDED — they are defects to fix,
    not to freeze. So the NO gate's FAIL lane is proven by synthetic injection
    (``test_no_new_billable_fails``), not a standing residual.
    """
    committed = load_no_baseline()
    committed_billable = sum(
        families.get(fam, 0)
        for families in committed.values()
        for fam in FAIL_FAMILIES
    )
    assert committed_billable == 0, (
        "The committed NO baseline carries a standing billable residual. If a NO act "
        "legitimately surfaced a NEW real replay bug this is a preregistered event — "
        "investigate + attribute it (bug fix or evidenced oracle_suspect re-typing), "
        "do not freeze an un-triaged billable."
    )


def test_committed_no_baseline_families_are_typed():
    """Every family in the NO baseline is a recognized residual family with a
    positive count (zero-count families must be dropped from the set)."""
    committed = load_no_baseline()
    for families in committed.values():
        for fam, count in families.items():
            assert fam in RESIDUAL_VERDICT_FAMILIES
            assert count > 0, "zero-count families must be dropped from the set"


# ---------------------------------------------------------------------------
# The diff gate over the NO baseline — FAIL / WARN / PASS (corpus-free)
# ---------------------------------------------------------------------------


def test_no_gate_passes_on_identical_set():
    base = load_no_baseline()
    result = residual_set_diff_gate(base, dict(base))
    assert result.verdict == "PASS"
    assert result.failed is False


def test_no_new_billable_fails():
    """A synthetic NEW replay_bug/unknown injected on top of the committed NO baseline
    FAILs the gate — the two lanes the honest metric exists to guard."""
    base = load_no_baseline()
    for fam in FAIL_FAMILIES:
        current = {**base, "no/lov/9999-99-99-9": {fam: 1}}
        result = residual_set_diff_gate(current, base)
        assert result.verdict == "FAIL", fam
        assert any(fam in line for line in result.new_billable)


def test_no_typed_move_warns():
    """A NEW typed non-billable residual over the NO baseline WARNs, never FAILs."""
    base = load_no_baseline()
    for fam in ("oracle_editorial_pathology", "temporal_mismatch", "state_index"):
        current = {**base, "no/lov/9999-99-99-9": {fam: 1}}
        result = residual_set_diff_gate(current, base)
        assert result.verdict == "WARN", fam
        assert result.failed is False


def test_no_baseline_round_trips(tmp_path):
    """write → load round-trips a NO residual set byte-for-byte (corpus-free: uses an
    explicit synthetic set so the round-trip needs no NO archive)."""
    residuals = {
        "no/lov/1/1": {"replay_bug": 1, "temporal_mismatch": 2},
        "no/lov/2/2": {},
        "no/lov/3/3": {"oracle_editorial_pathology": 3},
    }
    path = tmp_path / "no_baseline.json"
    write_no_baseline(residuals=residuals, path=path)
    assert load_no_baseline(path) == residuals


def test_no_baseline_path_is_under_tests_data():
    assert GATE_NO_BASELINE_PATH.parts[:2] == ("tests", "data")


# ---------------------------------------------------------------------------
# Data-present: the NO real corpus scores 0-billable + PASSes; fail-red wiring.
# ---------------------------------------------------------------------------


@requires_no_corpus
def test_no_real_corpus_scores_deterministically():
    """Same frozen corpus bytes ⇒ same typed-residual set (byte-stable)."""
    a = score_no_real_corpus()
    b = score_no_real_corpus()
    assert a == b
    assert json.dumps(a, sort_keys=False) == json.dumps(b, sort_keys=False)


@requires_no_corpus
def test_committed_no_baseline_matches_real_corpus():
    """The on-disk NO baseline is not stale: it equals the real NO corpus's current
    residual set. A move is a preregistered predict-then-compare event. Regenerate:
        uv run python -m lawvm.tools.no_anchor_manifest --update-baseline"""
    assert load_no_baseline() == score_no_real_corpus(), (
        "Committed NO CTSF baseline is stale vs the real #205 corpus. Confirm the move "
        "is legitimate, then regenerate with "
        "`uv run python -m lawvm.tools.no_anchor_manifest --update-baseline`."
    )


@requires_no_corpus
def test_no_real_corpus_is_zero_billable():
    """The real NO corpus scored live carries no billable residual — the honest
    steady state (not just the committed snapshot)."""
    live = score_no_real_corpus()
    billable = sum(
        families.get(fam, 0)
        for families in live.values()
        for fam in FAIL_FAMILIES
    )
    assert billable == 0


@requires_no_corpus
def test_no_real_corpus_gate_passes():
    """The NO corpus scored against its committed baseline PASSes (empty diff)."""
    result = run_no_gate_report()
    assert isinstance(result, GateResult)
    assert result.verdict == "PASS"


@requires_no_corpus
def test_no_synthetic_injection_fails_red_over_real_corpus():
    """Injecting a synthetic billable residual on top of the LIVE real-corpus score
    flips the gate red — the fail-red wiring binds the actual scorer, not just the
    committed snapshot."""
    live = score_no_real_corpus()
    poisoned = {**live, "no/lov/9999-99-99-9": {"replay_bug": 1}}
    result = residual_set_diff_gate(poisoned, load_no_baseline())
    assert result.verdict == "FAIL"
    assert result.failed is True

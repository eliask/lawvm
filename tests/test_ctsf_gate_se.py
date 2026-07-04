"""Tests for the SWEDEN CTSF residual-set-diff gate corpus (#183/#205).

The SE analogue of ``test_ctsf_gate_ee.py`` / ``test_ctsf_gate_uk.py``: extends the
now-PRIMARY CTSF gate to Sweden. The SE corpus surface + baseline helpers live in
:mod:`lawvm.tools.se_anchor_manifest` (``score_se_real_corpus`` / ``load_se_baseline`` /
``REAL_ANCHOR_SE_CORPUS_SIDS``) — exposed for the parent to add the gate section to
``core/ctsf_gate.py``; the diff LOGIC (``residual_set_diff_gate`` / ``FAIL_FAMILIES`` /
``GateResult``) is reused from the neutral gate core. Covers:

* the frozen SE corpus membership is content-pinned (explicit, sorted, unique);
* the committed SE baseline self-describes (jurisdiction + corpus sids) and carries
  ZERO billable (replay_bug/unknown) residuals — the honest 0-billable steady state;
* the SE diff FAILs on a synthetic NEW billable residual and WARNs on a typed
  non-billable move (the same diff logic as FI/EE/UK, over the SE baseline);
* the baseline round-trips (write → load → equal);
* data-present: the real SE corpus scores deterministically, 0-billable, PASSes its
  baseline; the SE lane SKIPS clean when the ``sweden.farchive`` is absent.

The data-present tests score the SE corpus via per-amending-act pre→post replay over the
``sweden.farchive``; they SKIP cleanly when it is absent (a corpus-free CI checkout). The
unit surface (diff logic over the committed baseline, round-trip) is corpus-free.
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
from lawvm.tools.se_anchor_manifest import (
    GATE_SE_BASELINE_PATH,
    REAL_ANCHOR_SE_CORPUS_SIDS,
    REAL_ANCHOR_SE_JURISDICTION,
    _repo_root,
    load_se_baseline,
    score_se_real_corpus,
    se_anchor_corpus_available,
    write_se_baseline,
)

# The SE corpus is scored via replay over the sweden.farchive; skip the real-corpus
# tests cleanly when it is absent. The unit surface is corpus-free and always runs.
requires_se_corpus = pytest.mark.skipif(
    not se_anchor_corpus_available(),
    reason="sweden.farchive absent; SE #183 anchor corpus cannot be scored",
)


# ---------------------------------------------------------------------------
# Frozen corpus membership + committed baseline (corpus-free)
# ---------------------------------------------------------------------------


def test_se_corpus_sids_are_frozen_and_sorted():
    """The SE corpus membership is content-pinned: an explicit, sorted, unique,
    non-empty tuple (no live enumeration)."""
    assert isinstance(REAL_ANCHOR_SE_CORPUS_SIDS, tuple)
    assert REAL_ANCHOR_SE_CORPUS_SIDS
    assert list(REAL_ANCHOR_SE_CORPUS_SIDS) == sorted(REAL_ANCHOR_SE_CORPUS_SIDS)
    assert len(set(REAL_ANCHOR_SE_CORPUS_SIDS)) == len(REAL_ANCHOR_SE_CORPUS_SIDS)


def test_committed_se_baseline_declares_corpus():
    """The committed SE baseline records the jurisdiction + frozen corpus sids."""
    data = json.loads(
        (_repo_root() / GATE_SE_BASELINE_PATH).read_text(encoding="utf-8")
    )
    assert data["jurisdiction"] == REAL_ANCHOR_SE_JURISDICTION
    assert tuple(data["corpus_sids"]) == REAL_ANCHOR_SE_CORPUS_SIDS


def test_committed_se_baseline_is_zero_billable():
    """The committed SE baseline carries ZERO billable (replay_bug/unknown) residuals
    — the honest 0-billable steady state.

    Provenance: the SE corpus is curated to amending acts whose pre→post replay
    reproduces every replay-touched op-target against the contemporaneous oracle. SE
    amending acts whose replay surfaces a GENUINE three-bucket ``genuine_mismatch``
    (a replay-touched op target the contemporaneous oracle carries that replay
    drops/mis-segments) are DELIBERATELY EXCLUDED — they are defects to fix, not to
    freeze. So the SE gate's FAIL lane is proven by synthetic injection
    (``test_se_new_billable_fails``), not a standing residual.
    """
    committed = load_se_baseline()
    committed_billable = sum(
        families.get(fam, 0)
        for families in committed.values()
        for fam in FAIL_FAMILIES
    )
    assert committed_billable == 0, (
        "The committed SE baseline carries a standing billable residual. If a SE act "
        "legitimately surfaced a NEW real replay bug this is a preregistered event — "
        "investigate + attribute it (bug fix or evidenced oracle_suspect re-typing), "
        "do not freeze an un-triaged billable."
    )


def test_committed_se_baseline_families_are_typed():
    """Every family in the SE baseline is a recognized residual family with a
    positive count (zero-count families must be dropped from the set)."""
    committed = load_se_baseline()
    for families in committed.values():
        for fam, count in families.items():
            assert fam in RESIDUAL_VERDICT_FAMILIES
            assert count > 0, "zero-count families must be dropped from the set"


# ---------------------------------------------------------------------------
# The diff gate over the SE baseline — FAIL / WARN / PASS (corpus-free)
# ---------------------------------------------------------------------------


def test_se_gate_passes_on_identical_set():
    base = load_se_baseline()
    result = residual_set_diff_gate(base, dict(base))
    assert result.verdict == "PASS"
    assert result.failed is False


def test_se_new_billable_fails():
    """A synthetic NEW replay_bug/unknown injected on top of the committed SE baseline
    FAILs the gate — the two lanes the honest metric exists to guard."""
    base = load_se_baseline()
    for fam in FAIL_FAMILIES:
        current = {**base, "synthetic/regressed": {fam: 1}}
        result = residual_set_diff_gate(current, base)
        assert result.verdict == "FAIL", fam
        assert any(fam in line for line in result.new_billable)


def test_se_typed_move_warns():
    """A NEW typed non-billable residual over the SE baseline WARNs, never FAILs."""
    base = load_se_baseline()
    for fam in ("oracle_editorial_pathology", "temporal_mismatch", "state_index"):
        current = {**base, "synthetic/typed": {fam: 1}}
        result = residual_set_diff_gate(current, base)
        assert result.verdict == "WARN", fam
        assert result.failed is False


def test_se_baseline_round_trips(tmp_path):
    """write → load round-trips a SE residual set byte-for-byte (corpus-free: uses an
    explicit synthetic set so the round-trip needs no SE archive)."""
    residuals = {
        "2000:1": {"replay_bug": 1, "temporal_mismatch": 2},
        "2001:2": {},
        "2002:3": {"oracle_editorial_pathology": 3},
    }
    path = tmp_path / "se_baseline.json"
    write_se_baseline(residuals=residuals, path=path)
    assert load_se_baseline(path) == residuals


def test_se_baseline_path_is_under_tests_data():
    assert GATE_SE_BASELINE_PATH.parts[:2] == ("tests", "data")


# ---------------------------------------------------------------------------
# Data-present: the SE real corpus scores 0-billable + PASSes its baseline.
# ---------------------------------------------------------------------------


@requires_se_corpus
def test_se_real_corpus_scores_deterministically():
    """Same frozen corpus bytes ⇒ same typed-residual set (byte-stable)."""
    a = score_se_real_corpus()
    b = score_se_real_corpus()
    assert a == b
    assert json.dumps(a, sort_keys=False) == json.dumps(b, sort_keys=False)


@requires_se_corpus
def test_committed_se_baseline_matches_real_corpus():
    """The on-disk SE baseline is not stale: it equals the real SE corpus's current
    residual set. A move is a preregistered predict-then-compare event. Regenerate:
        uv run python -m lawvm.tools.se_anchor_manifest --update-baseline"""
    assert load_se_baseline() == score_se_real_corpus(), (
        "Committed SE CTSF baseline is stale vs the real #183 corpus. Confirm the move "
        "is legitimate, then regenerate with "
        "`uv run python -m lawvm.tools.se_anchor_manifest --update-baseline`."
    )


@requires_se_corpus
def test_se_real_corpus_is_zero_billable():
    """The real SE corpus scored live carries no billable residual — the honest
    steady state (not just the committed snapshot)."""
    live = score_se_real_corpus()
    billable = sum(
        families.get(fam, 0)
        for families in live.values()
        for fam in FAIL_FAMILIES
    )
    assert billable == 0


@requires_se_corpus
def test_se_real_corpus_gate_passes():
    """The SE corpus scored against its committed baseline PASSes (empty diff)."""
    current = score_se_real_corpus()
    baseline = load_se_baseline()
    result = residual_set_diff_gate(current, baseline)
    assert isinstance(result, GateResult)
    assert result.verdict == "PASS"


@requires_se_corpus
def test_se_promoted_218_acts_are_gated_clean():
    """The four SE amending acts convicted by the new SE CTSF gate (#218) were genuine
    replay defects — PDF footnote-interleaving and wrapped-cross-reference
    mis-segmentation in ``parse_se_official_act_text`` — that have been fixed at the
    parser root and PROMOTED into ``REAL_ANCHOR_SE_CORPUS_SIDS``. They must now attribute
    GATED-CLEAN (no candidate replay bug), proving the fix (not a metric dodge):

      * 2009:538 §9a truncated the inserted clause at the wrapped ``4 a §``
        cross-reference; §4b folded a mid-body ``Prop.``/``Ändringen innebär`` footnote.
      * 2021:1035 §9 truncated at the wrapped ``2 kap.``\\n``7 §`` chapter-section
        cross-reference and spawned a ghost §7.
      * 2026:249 §10a truncated at ``3 kap.``\\n``11 §`` and spawned a 2-level ghost
        §9a/heading; a trailing numbered ``Prop.`` footnote leaked in.
      * 2015:1037 §2 folded a multi-line ``Jfr Europa...`` EU-directive footnote; §4
        folded a mid-body ``Senaste lydelse`` footnote.

    All four are members of the frozen 0-billable corpus now, so
    ``test_committed_se_baseline_matches_real_corpus`` also guards them; this test adds
    the explicit predict-then-compare assertion that the promotion is real (the acts
    genuinely gate clean, they were not merely dropped from the convicting set).
    """
    from lawvm.sweden.fetch import open_se_archive
    from lawvm.tools.se_anchor_manifest import _default_db, attribute_statute

    archive = open_se_archive(_default_db(), readonly=True)
    try:
        for sid in ("2009:538", "2015:1037", "2021:1035", "2026:249"):
            assert sid in REAL_ANCHOR_SE_CORPUS_SIDS, sid
            attr = attribute_statute(sid, archive=archive)
            assert attr.status == "OK", sid
            assert not attr.candidate_bug_observations, (
                f"promoted SE act {sid} regressed — it convicts again; the #218 parser "
                f"fix (grafter.parse_se_official_act_text footnote/cross-reference "
                f"folding) must keep it 0-billable."
            )
            assert attr.is_gated_clean, sid
    finally:
        archive.close()

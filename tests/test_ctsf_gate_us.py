"""Tests for the US FEDERAL CTSF residual-set-diff gate corpus (#205, the EIGHTH
jurisdiction).

The US analogue of ``test_ctsf_gate_ee.py`` / ``test_ctsf_gate_eu.py`` /
``test_ctsf_gate_se.py``: extends the now-PRIMARY CTSF gate to the US federal frontend.
The US corpus surface + baseline helpers live in :mod:`lawvm.tools.us_anchor_manifest`
(``score_us_real_corpus`` / ``load_us_baseline`` / ``REAL_ANCHOR_US_CORPUS_WINDOWS``) —
exposed for the parent to add the gate section to ``core/ctsf_gate.py``; the diff LOGIC
(``residual_set_diff_gate`` / ``FAIL_FAMILIES`` / ``GateResult``) is reused from the
neutral gate core. Covers:

* the frozen US corpus membership is content-pinned (explicit, sorted, unique);
* the committed US baseline self-describes (jurisdiction + corpus windows) and carries
  ZERO billable (replay_bug/unknown) residuals — the honest 0-billable steady state;
* the US diff FAILs on a synthetic NEW billable residual and WARNs on a typed
  non-billable move (the same diff logic as FI/EE/UK, over the US baseline);
* the baseline round-trips (write → load → equal);
* data-present: the real US corpus scores deterministically, 0-billable, PASSes its
  baseline; the US lane SKIPS clean when the ``us_federal.farchive`` is absent;
* the DELIBERATELY-EXCLUDED billable windows convict (the fail-red mechanism fires on
  real bugs, not only synthetic injection).

WHY US IS DIFFERENT (documented, load-bearing). The US federal frontend is a TEXT/SPAN
MATERIALIZER (``us_federal.dry_run`` does string surgery on located char spans), NOT a
label-ordered tree grafter like FI/EE/UK/NO. But it carries the two things the anchor
gate needs — a dated OLRC-published replay window with an oracle to diff (the adjacent
USC annual editions), and a commensurable same-dimension touch surface (the
changed-section set). Its per-window disposition partition projects directly onto the
CTSF families (``lawvm_wrong`` → replay_bug, unclassified non-agreement → unknown; the
editorial / temporal / capability-gap dispositions to the WARN-lane families), exactly
as EU projects its conserved-apply partition. See ``lawvm.tools.us_anchor_manifest``.

The data-present tests score the US corpus via offline dry-run replay over the
``us_federal.farchive``; they SKIP cleanly when it is absent (a corpus-free CI
checkout). The unit surface (diff logic over the committed baseline, round-trip) is
corpus-free.
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
from lawvm.tools.us_anchor_manifest import (
    GATE_US_BASELINE_PATH,
    REAL_ANCHOR_US_CORPUS_WINDOWS,
    REAL_ANCHOR_US_JURISDICTION,
    _repo_root,
    load_us_baseline,
    score_us_real_corpus,
    us_anchor_corpus_available,
    write_us_baseline,
)

# The US corpus is scored via offline dry-run replay over the us_federal.farchive; skip
# the real-corpus tests cleanly when it is absent. The unit surface is corpus-free and
# always runs.
requires_us_corpus = pytest.mark.skipif(
    not us_anchor_corpus_available(),
    reason="us_federal.farchive absent; US #205 anchor corpus cannot be scored",
)


# ---------------------------------------------------------------------------
# Frozen corpus membership + committed baseline (corpus-free)
# ---------------------------------------------------------------------------


def test_us_corpus_windows_are_frozen_and_sorted():
    """The US corpus membership is content-pinned: an explicit, sorted, unique,
    non-empty tuple of adjacent-edition window keys (no live enumeration)."""
    assert isinstance(REAL_ANCHOR_US_CORPUS_WINDOWS, tuple)
    assert REAL_ANCHOR_US_CORPUS_WINDOWS
    assert list(REAL_ANCHOR_US_CORPUS_WINDOWS) == sorted(REAL_ANCHOR_US_CORPUS_WINDOWS)
    assert len(set(REAL_ANCHOR_US_CORPUS_WINDOWS)) == len(REAL_ANCHOR_US_CORPUS_WINDOWS)
    for key in REAL_ANCHOR_US_CORPUS_WINDOWS:
        # Each key is a real ``title{N}:{before}->{after}`` window address.
        assert key.startswith("title") and ":" in key and "->" in key


def test_committed_us_baseline_declares_corpus():
    """The committed US baseline records the jurisdiction + frozen corpus windows."""
    data = json.loads(
        (_repo_root() / GATE_US_BASELINE_PATH).read_text(encoding="utf-8")
    )
    assert data["jurisdiction"] == REAL_ANCHOR_US_JURISDICTION
    assert tuple(data["corpus_windows"]) == REAL_ANCHOR_US_CORPUS_WINDOWS


def test_committed_us_baseline_is_zero_billable():
    """The committed US baseline carries ZERO billable (replay_bug/unknown) residuals
    — the honest 0-billable steady state.

    Provenance: the US corpus is curated to adjacent-edition windows whose offline
    dry-run replay materializes every oracle-changed section it lowers in agreement, with
    no ``lawvm_wrong`` and no unclassified non-agreement. US windows whose replay
    surfaces a GENUINE billable (a section LawVM materializes wrong vs the contemporaneous
    oracle, or an unclassifiable non-agreement) are DELIBERATELY EXCLUDED — they are
    defects to fix, not to freeze. So the US gate's FAIL lane is proven both by synthetic
    injection (``test_us_new_billable_fails``) and by the excluded real windows
    (``test_us_excluded_billable_windows_convict``), never by a standing residual.
    """
    committed = load_us_baseline()
    committed_billable = sum(
        families.get(fam, 0)
        for families in committed.values()
        for fam in FAIL_FAMILIES
    )
    assert committed_billable == 0, (
        "The committed US baseline carries a standing billable residual. If a US window "
        "legitimately surfaced a NEW real replay bug this is a preregistered event — "
        "investigate + attribute it (bug fix or evidenced oracle_suspect re-typing), do "
        "not freeze an un-triaged billable."
    )


def test_committed_us_baseline_exercises_typed_nonbillable_lanes():
    """The US baseline is not vacuous: it exercises the typed non-billable families
    (``cnf_unsupported`` capability-gap, ``oracle_editorial_pathology``,
    ``temporal_mismatch``) so the WARN lane is over real fixtures, not empty."""
    committed = load_us_baseline()
    all_fams = {fam for families in committed.values() for fam in families}
    assert "cnf_unsupported" in all_fams
    assert "oracle_editorial_pathology" in all_fams
    assert "temporal_mismatch" in all_fams
    for families in committed.values():
        for fam, count in families.items():
            assert fam in RESIDUAL_VERDICT_FAMILIES
            assert count > 0, "zero-count families must be dropped from the set"


# ---------------------------------------------------------------------------
# The diff gate over the US baseline — FAIL / WARN / PASS (corpus-free)
# ---------------------------------------------------------------------------


def test_us_gate_passes_on_identical_set():
    base = load_us_baseline()
    result = residual_set_diff_gate(base, dict(base))
    assert result.verdict == "PASS"
    assert result.failed is False


def test_us_new_billable_fails():
    """A synthetic NEW replay_bug/unknown injected on top of the committed US baseline
    FAILs the gate — the two lanes the honest metric exists to guard (a US lawvm_wrong
    projects to replay_bug, an unclassified non-agreement to unknown)."""
    base = load_us_baseline()
    for fam in FAIL_FAMILIES:
        current = {**base, "title99:2000->2001": {fam: 1}}
        result = residual_set_diff_gate(current, base)
        assert result.verdict == "FAIL", fam
        assert any(fam in line for line in result.new_billable)


def test_us_typed_move_warns():
    """A NEW typed non-billable residual over the US baseline WARNs, never FAILs."""
    base = load_us_baseline()
    for fam in ("cnf_unsupported", "oracle_editorial_pathology", "temporal_mismatch"):
        current = {**base, "title99:2000->2001": {fam: 1}}
        result = residual_set_diff_gate(current, base)
        assert result.verdict == "WARN", fam
        assert result.failed is False


def test_us_baseline_round_trips(tmp_path):
    """write → load round-trips a US residual set byte-for-byte (corpus-free: uses an
    explicit synthetic set so the round-trip needs no US archive)."""
    residuals = {
        "title1:2000->2001": {"replay_bug": 1, "temporal_mismatch": 2},
        "title2:2001->2002": {},
        "title3:2002->2003": {"cnf_unsupported": 3},
    }
    path = tmp_path / "us_baseline.json"
    write_us_baseline(residuals=residuals, path=path)
    assert load_us_baseline(path) == residuals


def test_us_baseline_path_is_under_tests_data():
    assert GATE_US_BASELINE_PATH.parts[:2] == ("tests", "data")


# ---------------------------------------------------------------------------
# Data-present: the US real corpus scores 0-billable + PASSes its baseline.
# ---------------------------------------------------------------------------


@requires_us_corpus
def test_us_real_corpus_scores_deterministically():
    """Same frozen corpus bytes ⇒ same typed-residual set (byte-stable)."""
    a = score_us_real_corpus()
    b = score_us_real_corpus()
    assert a == b
    assert json.dumps(a, sort_keys=False) == json.dumps(b, sort_keys=False)


@requires_us_corpus
def test_committed_us_baseline_matches_real_corpus():
    """The on-disk US baseline is not stale: it equals the real US corpus's current
    residual set. A move is a preregistered predict-then-compare event. Regenerate:
        uv run python -m lawvm.tools.us_anchor_manifest --update-baseline"""
    assert load_us_baseline() == score_us_real_corpus(), (
        "Committed US CTSF baseline is stale vs the real #205 corpus. Confirm the move "
        "is legitimate, then regenerate with "
        "`uv run python -m lawvm.tools.us_anchor_manifest --update-baseline`."
    )


@requires_us_corpus
def test_us_real_corpus_is_zero_billable():
    """The real US corpus scored live carries no billable residual — the honest steady
    state (not just the committed snapshot)."""
    live = score_us_real_corpus()
    billable = sum(
        families.get(fam, 0)
        for families in live.values()
        for fam in FAIL_FAMILIES
    )
    assert billable == 0


@requires_us_corpus
def test_us_real_corpus_gate_passes():
    """The US corpus scored against its committed baseline PASSes (empty diff)."""
    current = score_us_real_corpus()
    baseline = load_us_baseline()
    result = residual_set_diff_gate(current, baseline)
    assert isinstance(result, GateResult)
    assert result.verdict == "PASS"


@requires_us_corpus
def test_us_excluded_billable_windows_convict():
    """The DELIBERATELY-EXCLUDED US windows (genuine dry-run ``lawvm_wrong`` divergences)
    convict as billable — proof the fail-red mechanism fires on real bugs, and that they
    are rightly kept OUT of the 0-billable corpus (not frozen green).

    These are ``us_dry_run_lawvm_wrong`` (replay_bug) windows the #205 US metric
    surfaces: title23:2020->2022 (8, down from 13 after §176's ``non- Federal``
    hyphen-wrap ``oracle_suspect``, then 12, then 8 after the shared resolver diverted
    §133/§148/§515's compound-chain empty-materializations to source-truncated
    oracle_suspect / missing_source), title50:2022->2023 (8 after §3919's
    terminal-period-strike courtesy space was materialized and later F3 cleanup),
    and the currently-demoted zero-corpus anchors title28:2014->2016 and
    title40:2018->2020. The residual counts are the DEFERRED_ROADMAP F2-F3 backlog
    plus demoted corpus-frontier windows; they still convict (billable > 0), which
    is what this test pins. If a window's replay was fully fixed (0 billable),
    promote it into REAL_ANCHOR_US_CORPUS_WINDOWS (a preregistered move)."""
    from lawvm.tools.us_anchor_manifest import attribute_window

    for key in (
        "title23:2020->2022",
        "title28:2014->2016",
        "title40:2018->2020",
        "title50:2022->2023",
    ):
        attr = attribute_window(key)
        assert attr.status == "OK", key
        assert attr.billable_observations, (
            f"excluded US window {key} no longer convicts — if its replay was fixed, "
            f"promote it into REAL_ANCHOR_US_CORPUS_WINDOWS (a preregistered move)."
        )

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

import pytest

from lawvm.core.ctsf_gate import (
    FAIL_FAMILIES,
    GATE_BASELINE_PATH,
    REAL_ANCHOR_CORPUS_SIDS,
    REAL_ANCHOR_JURISDICTION,
    GateResult,
    _repo_root,
    format_report,
    frozen_gate_corpus,
    load_baseline,
    main as ctsf_gate_main,
    real_anchor_corpus_available,
    residual_set,
    residual_set_diff_gate,
    run_gate_report,
    score_corpus,
    score_real_corpus,
    write_baseline,
)
from lawvm.core.ctsf_residual_report import RESIDUAL_VERDICT_FAMILIES

# The real #183 corpus is scored via replay over the Finlex archive; skip the
# real-corpus tests cleanly when the corpus is absent (a corpus-free CI checkout).
# The gate's UNIT surface (diff logic, synthetic corpus, baseline round-trip) is
# corpus-free and always runs.
requires_corpus = pytest.mark.skipif(
    not real_anchor_corpus_available(),
    reason="Finlex corpus archive absent; real #183 anchor corpus cannot be scored",
)


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
    """write → load round-trips a residual set byte-for-byte (corpus-free: uses an
    explicit synthetic residual set so the round-trip needs no Finlex archive)."""
    residuals = {
        "s/1": {"replay_bug": 1, "temporal_mismatch": 3},
        "s/2": {},
        "s/3": {"oracle_editorial_pathology": 2},
    }
    path = tmp_path / "baseline.json"
    write_baseline(residuals=residuals, path=path)
    loaded = load_baseline(path)
    assert loaded == residuals


def test_baseline_path_is_under_tests_data():
    assert GATE_BASELINE_PATH.parts[:2] == ("tests", "data")


# ---------------------------------------------------------------------------
# The REAL #183 touch-relation corpus — the gate's production corpus
# ---------------------------------------------------------------------------


def test_real_corpus_sids_are_frozen_and_sorted():
    """The corpus membership is content-pinned: an explicit, sorted, non-empty
    tuple (no live enumeration)."""
    assert isinstance(REAL_ANCHOR_CORPUS_SIDS, tuple)
    assert REAL_ANCHOR_CORPUS_SIDS
    assert list(REAL_ANCHOR_CORPUS_SIDS) == sorted(REAL_ANCHOR_CORPUS_SIDS)
    assert len(set(REAL_ANCHOR_CORPUS_SIDS)) == len(REAL_ANCHOR_CORPUS_SIDS)


def test_committed_baseline_declares_real_corpus():
    """The committed baseline records the jurisdiction + frozen corpus sids, so the
    on-disk artifact self-describes what it is a snapshot of."""
    data = json.loads(
        (_repo_root() / GATE_BASELINE_PATH).read_text(encoding="utf-8")
    )
    assert data["jurisdiction"] == REAL_ANCHOR_JURISDICTION
    assert tuple(data["corpus_sids"]) == REAL_ANCHOR_CORPUS_SIDS


def test_committed_baseline_acknowledges_standing_billable_residuals():
    """The honest baseline does NOT hide the real replay_bug/unknown residuals the
    touch relation exposes — they are the acknowledged current state (the gate FAILs
    on NEW ones vs them, it does not zero these out)."""
    committed = load_baseline()
    billable_total = sum(
        families.get(fam, 0)
        for families in committed.values()
        for fam in FAIL_FAMILIES
    )
    # The real corpus DOES surface standing billable residuals (1969/10): the whole
    # point of the honest metric. If the corpus is re-curated these can change, but
    # the baseline must always faithfully carry whatever is real, never suppress it.
    assert billable_total >= 1, (
        "The committed baseline reports zero billable residuals — the real corpus "
        "is expected to surface at least the standing 1969/10 replay_bug/unknown. "
        "If the corpus changed legitimately, this is a preregistered event; confirm "
        "the drop is real, not a suppression."
    )


@requires_corpus
def test_real_corpus_scores_deterministically():
    """Same frozen corpus bytes ⇒ same typed-residual set (byte-stable)."""
    a = score_real_corpus()
    b = score_real_corpus()
    assert a == b
    assert json.dumps(a, sort_keys=False) == json.dumps(b, sort_keys=False)


@requires_corpus
def test_committed_baseline_matches_real_corpus():
    """The on-disk committed baseline is not stale: it equals the REAL corpus's
    current residual set. If this fails, the corpus/projection moved — a
    preregistered predict-then-compare event. Regenerate (needs the Finlex corpus):
        uv run python -m lawvm.core.ctsf_gate --update-baseline"""
    committed = load_baseline()
    assert committed == score_real_corpus(), (
        "Committed CTSF gate baseline is stale vs the real #183 corpus. This is a "
        "preregistered event — confirm the move is legitimate, then regenerate with "
        "`uv run python -m lawvm.core.ctsf_gate --update-baseline`."
    )


@requires_corpus
def test_real_corpus_gate_passes_against_committed_baseline():
    """The real corpus scored against its own committed baseline PASSES (the diff is
    empty) — the honest steady state in report mode."""
    result = run_gate_report()
    assert isinstance(result, GateResult)
    assert result.verdict == "PASS"


@requires_corpus
def test_new_billable_on_real_baseline_fails():
    """A synthetic NEW replay_bug/unknown injected on top of the REAL corpus's set
    FAILs the gate — the honest metric catches a new billable regression over real
    data (the two lanes it exists to guard)."""
    baseline = score_real_corpus()
    for fam in FAIL_FAMILIES:
        current = {**baseline, "synthetic/regressed": {fam: 1}}
        result = residual_set_diff_gate(current, baseline)
        assert result.verdict == "FAIL", fam
        assert any(fam in line for line in result.new_billable)


@requires_corpus
def test_typed_move_on_real_baseline_warns():
    """A NEW typed non-billable residual over the real corpus WARNs, never FAILs."""
    baseline = score_real_corpus()
    for fam in ("oracle_editorial_pathology", "temporal_mismatch", "state_index"):
        current = {**baseline, "synthetic/typed": {fam: 1}}
        result = residual_set_diff_gate(current, baseline)
        assert result.verdict == "WARN", fam


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
    # Corpus-free: build a GateResult directly so the label check needs no archive.
    result = residual_set_diff_gate({"s": {}}, {"s": {}})
    text = format_report(result)
    assert "PARALLEL / REPORT MODE" in text
    assert "legacy scalar bench gate UNCHANGED" in text
    assert "REAL #183" in text


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

"""Tests for CTSF Phase 3 (task #186/#198): the residual-set-diff GATE — now PRIMARY.

Covers the gate as the PRIMARY / load-bearing correctness surface:

* the diff FAILs on a synthetic NEW ``replay_bug``/``unknown`` residual (the
  ``has_replay_bug_or_unknown`` predicate over the diff);
* the diff WARNs (never FAILs) on a NEW typed oracle/editorial/state-index/temporal
  residual — a non-billable move is telemetry, not a red gate;
* PASS iff the current typed-residual set equals the baseline exactly;
* determinism: same corpus ⇒ same verdict, byte-stable residual set;
* the frozen baseline round-trips (write → load → equal), and the COMMITTED
  baseline matches the frozen corpus (the on-disk artifact is not stale);
* FAIL-RED wiring: the callable gate (``run_gate``) and the CLI (``main``) return
  NONZERO on a new billable residual (data-present) and SKIP clean (return 0) when
  the corpus is absent — data-less CI is never failed by this gate.
"""

from __future__ import annotations

import json

import pytest

from lawvm.core.ctsf_gate import (
    FAIL_FAMILIES,
    GATE_BASELINE_PATH,
    REAL_ANCHOR_CORPUS_SIDS,
    REAL_ANCHOR_JURISDICTION,
    GATE_MODE,
    SCALAR_GATE_STATUS,
    GateResult,
    _repo_root,
    format_report,
    frozen_gate_corpus,
    load_baseline,
    main as ctsf_gate_main,
    real_anchor_corpus_available,
    residual_set,
    residual_set_diff_gate,
    run_gate,
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


def test_committed_baseline_carries_only_honest_billables():
    """The committed baseline's billable (replay_bug/unknown) count is exactly what
    the touch relation honestly localizes — no more, no fewer.

    Provenance: at freeze time the corpus surfaced 3 standing billables on 1969/10
    (1 replay_bug + 2 unknown). Investigating them via the touch relation (the
    ``FI_1969_10_CTSF`` burn-in) proved all three are the SAME oracle structural
    pathology: Finlex flattened § 3's two moments (``Rekisteröimishakemukseen on
    liitettävä:`` / ``Luettelokortissa tulee olla:``) into one 9-item list at
    version 20151688, absorbing the second moment's intro as a spurious item 5,
    while replay preserves the correct two-subsection shape unchanged. That is
    oracle-side re-rendering of a shape-stable replay unit, so the structure-aware
    touch relation now types it ``oracle_suspect_spontaneous_appearance``
    (→ oracle_editorial_pathology), NOT a replay bug. The honest steady state can
    therefore carry ZERO standing billables — the gate's FAIL lane is guaranteed by
    ``test_new_billable_on_real_baseline_fails`` (synthetic injection), not by a
    permanent standing residual. This test pins that the committed billable count
    equals the live one, so a NEW real billable (or a resurrected suppressed one)
    can never silently enter or leave the baseline.
    """
    committed = load_baseline()
    committed_billable = sum(
        families.get(fam, 0)
        for families in committed.values()
        for fam in FAIL_FAMILIES
    )
    # Post burn-in the honest count is zero; assert the committed baseline is not
    # hiding a real billable and not fabricating a fake one.
    assert committed_billable == 0, (
        "The committed baseline carries a standing billable (replay_bug/unknown) "
        "residual. If the corpus legitimately surfaced a NEW real replay bug this is "
        "a preregistered event — investigate and attribute it (bug fix or evidenced "
        "oracle_suspect re-typing), do not freeze an un-triaged billable."
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
# PRIMARY gate — fail-red wiring + data-aware skip
# ---------------------------------------------------------------------------


def test_gate_is_primary_and_scalar_is_demoted():
    """The flip is recorded in-code: the gate MODE is PRIMARY and the legacy scalar
    is demoted to telemetry (retirement deferred, documented)."""
    assert GATE_MODE == "PRIMARY"
    assert SCALAR_GATE_STATUS == "telemetry_retirement_deferred"


def test_run_gate_data_absent_skips_clean(monkeypatch):
    """When the corpus is absent the PRIMARY callable gate SKIPS clean (returns 0) —
    data-less CI is never failed by this gate."""
    import lawvm.core.ctsf_gate as gate

    monkeypatch.setattr(gate, "real_anchor_corpus_available", lambda: False)
    assert gate.run_gate() == 0


def test_cli_data_absent_skips_clean(monkeypatch, capsys):
    """The CLI mirrors the callable: corpus absent → exit 0, reporting the frozen
    baseline as the pinned state (never flips CI red data-less)."""
    import lawvm.core.ctsf_gate as gate

    monkeypatch.setattr(gate, "real_anchor_corpus_available", lambda: False)
    rc = ctsf_gate_main([])
    assert rc == 0
    out = capsys.readouterr().out
    assert "PRIMARY GATE" in out
    assert "SKIPPED clean" in out


def test_cli_json_mode_data_absent_returns_zero(monkeypatch, capsys):
    import lawvm.core.ctsf_gate as gate

    monkeypatch.setattr(gate, "real_anchor_corpus_available", lambda: False)
    rc = ctsf_gate_main(["--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["verdict"] in ("PASS", "WARN", "FAIL")
    assert "current" in payload and "baseline" in payload
    assert "corpus_absent" in payload.get("note", "")


def test_format_report_labels_primary_mode():
    # Corpus-free: build a GateResult directly so the label check needs no archive.
    result = residual_set_diff_gate({"s": {}}, {"s": {}})
    text = format_report(result)
    assert "PRIMARY GATE" in text
    assert "DEMOTED to telemetry" in text
    assert "REAL #183" in text


@requires_corpus
def test_run_gate_passes_clean_baseline_returns_zero():
    """Data-present + clean 0-billable baseline ⇒ the PRIMARY callable gate returns
    0 (the honest steady state passes)."""
    assert run_gate() == 0


@requires_corpus
def test_cli_primary_gate_passes_clean_returns_zero(capsys):
    """Data-present clean run: the CLI PRIMARY gate exits 0 on the current 0-billable
    baseline."""
    rc = ctsf_gate_main([])
    assert rc == 0
    out = capsys.readouterr().out
    assert "PRIMARY GATE" in out
    assert "verdict: PASS" in out


@requires_corpus
def test_cli_primary_gate_fails_red_on_injected_billable(monkeypatch, capsys):
    """Data-present: an INJECTED new billable residual on top of the real corpus flips
    the CLI exit code red (return 1) — the honest metric is load-bearing."""
    import lawvm.core.ctsf_gate as gate

    real = score_real_corpus()

    def _regressed():
        return {**real, "synthetic/regressed": {"replay_bug": 1}}

    monkeypatch.setattr(gate, "score_real_corpus", _regressed)
    rc = ctsf_gate_main([])
    assert rc == 1
    out = capsys.readouterr().out
    assert "verdict: FAIL" in out


@requires_corpus
def test_run_gate_fails_red_on_injected_billable(monkeypatch):
    """Data-present: the callable gate returns NONZERO on an injected new billable."""
    import lawvm.core.ctsf_gate as gate

    real = score_real_corpus()
    for fam in FAIL_FAMILIES:
        monkeypatch.setattr(
            gate,
            "score_real_corpus",
            lambda fam=fam: {**real, "synthetic/regressed": {fam: 1}},
        )
        assert gate.run_gate() == 1, fam


@requires_corpus
def test_run_gate_warn_does_not_fail_red(monkeypatch):
    """Data-present: a typed non-billable move WARNs — the gate stays green (0)."""
    import lawvm.core.ctsf_gate as gate

    real = score_real_corpus()
    monkeypatch.setattr(
        gate,
        "score_real_corpus",
        lambda: {**real, "synthetic/typed": {"temporal_mismatch": 1}},
    )
    assert gate.run_gate() == 0


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

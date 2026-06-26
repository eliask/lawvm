"""Tests for the UK spec-discovery ledger adapter.

Mirrors ``test_spec_ledger.py`` (the FI adapter) for the UK adapter:

  * the per-EID ``UKDivergenceRow`` shape;
  * the ``_UK_DIAGNOSIS_DISPOSITION`` mapping, including the loud "unknown"
    fallback for an uncataloged diagnosis;
  * UK dispatch through ``run_ledger``;
  * one small fixed UK statute end-to-end producing a non-empty ledger (skipped
    when the UK farchive corpus is not available — bounded CI has no archive).

Unit assertions use synthetic fixtures, never corpus literals, so they stay
green without the archive. The existing FI ledger tests are unaffected.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from lawvm.tools.spec_ledger import (
    DivergenceRow,
    StatuteLedgerInput,
    build_ledger,
    run_ledger,
)
from lawvm.uk_legislation.spec_ledger_adapter import (
    _UK_DIAGNOSIS_DISPOSITION,
    uk_ledger_inputs,
)
from lawvm.tools.uk_oracle_check import (
    UKDivergenceRow,
    UKDivergenceState,
    _covering_diagnostic_row,
    uk_divergence_rows_for_statute,
)


# ---------------------------------------------------------------------------
# Per-EID row shape
# ---------------------------------------------------------------------------

def test_uk_divergence_row_shape_fields():
    row = UKDivergenceRow(
        eid="section-22-1-a-3",
        diagnosis="deterministic_gap",
        blame_source="ukpga/2001/2",
        phase_owner="typed_elaboration",
        authority_layer="EFFECT_FEED_INDEX",
        rule_id="uk_effect_some_rule",
    )
    assert row.eid == "section-22-1-a-3"
    assert row.diagnosis == "deterministic_gap"
    assert row.blame_source == "ukpga/2001/2"
    assert row.phase_owner == "typed_elaboration"
    assert row.authority_layer == "EFFECT_FEED_INDEX"
    assert row.rule_id == "uk_effect_some_rule"


def test_uk_divergence_row_optional_facets_default_empty():
    row = UKDivergenceRow(eid="section-1", diagnosis="text_diff")
    assert row.blame_source == ""
    assert row.phase_owner == ""
    assert row.authority_layer == ""
    assert row.rule_id == ""


def test_covering_diagnostic_row_loose_substring_match():
    rows = [
        {"affected_provisions": "section-22", "rule_id": "r.one", "owner_phase": "typed_elaboration"},
        {"affected_provisions": "schedule-2", "rule_id": "r.two"},
        {"affected_provisions": "", "rule_id": "r.empty"},
    ]
    cover = _covering_diagnostic_row("section-22-1-a-3", rows)
    assert cover is not None
    assert cover["rule_id"] == "r.one"
    assert _covering_diagnostic_row("part-7", rows) is None


# ---------------------------------------------------------------------------
# Disposition mapping (incl. loud unknown fallback)
# ---------------------------------------------------------------------------

def test_uk_diagnosis_disposition_bucket_names():
    # §2.1 oracle-check bucket names — the primary per-EID diagnosis vocab.
    assert _UK_DIAGNOSIS_DISPOSITION["deterministic_gap"] == "lawvm_wrong"
    assert _UK_DIAGNOSIS_DISPOSITION["manual_frontier"] == "missing_source"
    assert _UK_DIAGNOSIS_DISPOSITION["oracle_suspect"] == "oracle_suspect"
    assert _UK_DIAGNOSIS_DISPOSITION["text_diff"] == "unknown"


def test_uk_diagnosis_disposition_seeds_pathology_and_compare_shape():
    # A source-pathology class maps to missing_source (source under-specified).
    assert _UK_DIAGNOSIS_DISPOSITION["missing_extracted_source"] == "missing_source"
    assert _UK_DIAGNOSIS_DISPOSITION["appropriate_place_insert_unsupported"] == "missing_source"
    # A compare-shape class maps to oracle_suspect (replay coherent; editorial shape).
    assert _UK_DIAGNOSIS_DISPOSITION["collapsed_subtree_oracle_shape"] == "oracle_suspect"
    assert _UK_DIAGNOSIS_DISPOSITION["retained_repeal_oracle_branch"] == "oracle_suspect"
    # An owning-phase constant resolves too.
    assert _UK_DIAGNOSIS_DISPOSITION["compare_oracle_classification"] == "oracle_suspect"


def test_uk_unknown_diagnosis_falls_back_to_unknown_not_a_pass():
    # An uncataloged diagnosis must render loud "unknown" via .get fallback —
    # never be silently treated as a pass (a non-divergence).
    assert _UK_DIAGNOSIS_DISPOSITION.get("totally_new_diagnosis", "unknown") == "unknown"
    assert "totally_new_diagnosis" not in _UK_DIAGNOSIS_DISPOSITION


# ---------------------------------------------------------------------------
# uk_ledger_inputs maps UKDivergenceRow facets onto neutral DivergenceRow
# ---------------------------------------------------------------------------

def test_uk_ledger_inputs_maps_rows(monkeypatch):
    import lawvm.tools.uk_oracle_check as uk

    fake_state = UKDivergenceState(
        buckets={"deterministic_gap": ["section-1"], "oracle_suspect": ["section-2"]},
        lowering_rejections=[{"rule_id": "uk_effect_alpha", "affected_provisions": "section-1"}],
        effect_diagnostics=[{"rule_id": "uk_effect_beta", "affected_provisions": "section-2"}],
        n_ops=3,
    )
    fake_rows = [
        UKDivergenceRow(
            eid="section-1",
            diagnosis="deterministic_gap",
            blame_source="ukpga/2001/2",
            phase_owner="typed_elaboration",
            authority_layer="EFFECT_FEED_INDEX",
            rule_id="uk_effect_alpha",
        ),
        UKDivergenceRow(
            eid="section-2",
            diagnosis="oracle_suspect",
            rule_id="uk_effect_beta",
        ),
        # An unmapped diagnosis must surface as "unknown", not a pass.
        UKDivergenceRow(eid="section-9", diagnosis="brand_new_label"),
    ]

    monkeypatch.setattr(uk, "_compute_uk_divergence_state", lambda sid, db_path=None: fake_state)
    monkeypatch.setattr(uk, "uk_divergence_rows_for_statute", lambda sid, db_path=None: fake_rows)

    inputs = list(uk_ledger_inputs(["ukpga/2000/1"], "official_consolidation"))
    assert len(inputs) == 1
    inp = inputs[0]
    # firings tallied from the diagnostic rule_ids
    assert inp.rule_firings == {"uk_effect_alpha": 1, "uk_effect_beta": 1}
    by_eid = {d.section_key: d for d in inp.divergences}
    assert by_eid["section-1"].disposition == "lawvm_wrong"
    assert by_eid["section-1"].phase_owner == "typed_elaboration"
    assert by_eid["section-1"].authority_layer == "EFFECT_FEED_INDEX"
    assert by_eid["section-1"].rule_id == "uk_effect_alpha"
    assert by_eid["section-2"].disposition == "oracle_suspect"
    # loud unknown fallback
    assert by_eid["section-9"].disposition == "unknown"


def test_uk_ledger_inputs_skips_errored_statute(monkeypatch):
    import lawvm.tools.uk_oracle_check as uk

    monkeypatch.setattr(
        uk, "_compute_uk_divergence_state",
        lambda sid, db_path=None: UKDivergenceState(error="Archive not found"),
    )
    monkeypatch.setattr(uk, "uk_divergence_rows_for_statute", lambda sid, db_path=None: [])
    inputs = list(uk_ledger_inputs(["ukpga/2000/1"], "official_consolidation"))
    assert inputs == []


# ---------------------------------------------------------------------------
# run_ledger dispatch
# ---------------------------------------------------------------------------

def test_run_ledger_dispatches_uk():
    import dataclasses

    from lawvm.tools.spec_ledger import get_ledger_adapter, register_ledger_adapter

    synthetic = [
        StatuteLedgerInput(
            "ukpga/2000/1",
            {"uk_effect_alpha": 2},
            [
                DivergenceRow(
                    sid="ukpga/2000/1",
                    section_key="section-1",
                    diagnosis="deterministic_gap",
                    disposition="lawvm_wrong",
                    rule_id="uk_effect_alpha",
                    phase_owner="typed_elaboration",
                ),
            ],
        )
    ]
    # Re-register a UK adapter whose ledger_inputs is the synthetic stream; restore the
    # real adapter after the test so registry state does not leak.
    original = get_ledger_adapter("uk")
    register_ledger_adapter(
        dataclasses.replace(original, ledger_inputs=lambda sids, mode: iter(synthetic))
    )
    try:
        led = run_ledger("uk", ["ukpga/2000/1"], "official_consolidation")
    finally:
        register_ledger_adapter(original)
    assert led.jurisdiction == "uk"
    assert led.statutes == 1
    assert led.statute_errors == 0
    assert led.rules["uk_effect_alpha"].firings == 2
    assert led.rules["uk_effect_alpha"].contradicted == 1


def test_run_ledger_unknown_jurisdiction_raises():
    with pytest.raises(NotImplementedError):
        run_ledger("zz", ["x/1"], "official_consolidation")


def test_uk_phase_owner_survives_neutral_exemplar():
    # phase_owner/authority_layer must travel through the neutral DivergenceRow
    # exemplar so blind-spots can be bucketed by owning phase / source purity.
    row = DivergenceRow(
        sid="ukpga/2000/1",
        section_key="section-1",
        diagnosis="deterministic_gap",
        disposition="lawvm_wrong",
        rule_id=None,
        phase_owner="typed_elaboration",
        authority_layer="EFFECT_FEED_INDEX",
    )
    ex = row.exemplar()
    assert ex["phase_owner"] == "typed_elaboration"
    assert ex["authority_layer"] == "EFFECT_FEED_INDEX"
    # FI rows that don't set the facets stay clean (no spurious keys).
    fi_row = DivergenceRow(
        sid="1958/370",
        section_key="section:5",
        diagnosis="REPLAY_EXTRA",
        disposition="lawvm_wrong",
        rule_id="fi.section_ref",
    )
    assert "phase_owner" not in fi_row.exemplar()
    assert "authority_layer" not in fi_row.exemplar()


# ---------------------------------------------------------------------------
# End-to-end: one small fixed UK statute → non-empty ledger
# (skipped when the UK farchive corpus is unavailable, e.g. bounded CI)
# ---------------------------------------------------------------------------

def _uk_archive_path() -> Path | None:
    root = os.environ.get("LAWVM_CANONICAL_DATA_ROOT")
    if not root:
        return None
    candidates = [
        Path(root) / "data" / "uk_legislation.farchive",
        Path(root) / "uk_legislation.farchive",
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def test_uk_statute_end_to_end_non_empty_ledger():
    # Opt-in only: a full UK corpus compile is heavy and shares global UK compile
    # internals that sibling contract tests monkeypatch, so it is unsafe to run
    # inside the bounded parallel gate. Mirrors the FI ledger test's stance of
    # not exercising the corpus-backed path by default. Run explicitly with
    #   LAWVM_SPEC_LEDGER_UK_E2E=1 LAWVM_CANONICAL_DATA_ROOT=<checkout> pytest ...
    if os.environ.get("LAWVM_SPEC_LEDGER_UK_E2E") != "1":
        pytest.skip("set LAWVM_SPEC_LEDGER_UK_E2E=1 to run the corpus-backed UK end-to-end")
    archive = _uk_archive_path()
    if archive is None:
        pytest.skip("UK farchive corpus not available (LAWVM_CANONICAL_DATA_ROOT unset/missing)")

    rows = uk_divergence_rows_for_statute("asp/2000/1", db_path=archive)
    # The fixed statute has known divergences; the per-EID surface is non-empty.
    assert rows, "expected a non-empty UK per-EID divergence surface for asp/2000/1"
    assert all(isinstance(r, UKDivergenceRow) for r in rows)
    assert all(r.diagnosis in _UK_DIAGNOSIS_DISPOSITION or r.diagnosis for r in rows)

    # Build the ledger through uk_ledger_inputs with the explicit archive db.
    import lawvm.tools.uk_oracle_check as uk

    inputs = []
    for sid in ["asp/2000/1"]:
        state = uk._compute_uk_divergence_state(sid, db_path=archive)
        assert not state.error
        firings = {}
        for r in (
            state.lowering_rejections
            + state.effect_feed_parse_rejections
            + state.authority_rejections
            + state.effect_diagnostics
        ):
            rid = str(r.get("rule_id") or "")
            if rid:
                firings[rid] = firings.get(rid, 0) + 1
        divs = [
            DivergenceRow(
                sid=sid,
                section_key=r.eid,
                diagnosis=r.diagnosis,
                disposition=_UK_DIAGNOSIS_DISPOSITION.get(r.diagnosis, "unknown"),
                rule_id=r.rule_id or None,
                blame_source=r.blame_source,
                phase_owner=r.phase_owner,
                authority_layer=r.authority_layer,
            )
            for r in uk_divergence_rows_for_statute(sid, db_path=archive)
        ]
        inputs.append(StatuteLedgerInput(sid=sid, rule_firings=firings, divergences=divs))

    led = build_ledger(inputs, jurisdiction="uk", mode="official_consolidation", catalog={})
    assert led.statutes == 1
    assert led.rules, "expected non-empty per-rule ledger from compiled UK ops"


def _gap_row(sid: str, i: int) -> DivergenceRow:
    return DivergenceRow(
        sid=sid,
        section_key=f"part-{i}",
        diagnosis="deterministic_gap",
        disposition="lawvm_wrong",
        rule_id=None,
        blame_source="",
    )


def test_whole_statute_noncommensurable_wall_is_demoted():
    """A statute that is overwhelmingly unattributed deterministic-gaps (one
    EID-scheme mismatch, e.g. ukpga/1907/51) is demoted out of the falsifying
    bucket so it cannot masquerade as thousands of real bugs."""
    from lawvm.uk_legislation.spec_ledger_adapter import (
        _NONCOMMENSURABLE_DIAGNOSIS,
        _demote_whole_statute_noncommensurable,
    )

    rows = [_gap_row("ukpga/1907/51", i) for i in range(60)]
    out = _demote_whole_statute_noncommensurable(rows)
    assert all(r.diagnosis == _NONCOMMENSURABLE_DIAGNOSIS for r in out)
    assert all(r.disposition == "unknown" for r in out)
    # none counts as falsifying -> statute does not dominate the real-bug ranking
    led = build_ledger(
        [StatuteLedgerInput(sid="ukpga/1907/51", rule_firings={}, divergences=out)],
        jurisdiction="uk",
        mode="official_consolidation",
        catalog={},
    )
    assert led.statute_real_bugs.get("ukpga/1907/51", 0) == 0


def _attr_gap_row(sid: str, i: int) -> DivergenceRow:
    return DivergenceRow(
        sid=sid,
        section_key=f"part-{i}",
        diagnosis="deterministic_gap",
        disposition="lawvm_wrong",
        rule_id="uk_some_rule",
        blame_source="",
    )


def test_noncommensurable_demotion_is_conservative():
    """Small statutes and statutes below the wall fraction are never demoted."""
    from lawvm.uk_legislation.spec_ledger_adapter import (
        _demote_whole_statute_noncommensurable,
    )

    # below the row floor -> untouched even if all are unattributed gaps
    small = [_gap_row("x/1", i) for i in range(10)]
    assert _demote_whole_statute_noncommensurable(small) == small

    # 52 unattributed gaps + 8 attributed (wall = 52/60 = 0.87 < 0.9) -> unchanged
    mixed = [_gap_row("x/2", i) for i in range(52)] + [
        _attr_gap_row("x/2", 52 + j) for j in range(8)
    ]
    out = _demote_whole_statute_noncommensurable(mixed)
    assert any(
        r.rule_id is None and r.diagnosis == "deterministic_gap" for r in out
    ), "wall fraction not met -> rows unchanged"

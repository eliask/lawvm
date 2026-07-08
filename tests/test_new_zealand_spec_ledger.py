"""Tests for the NZ witness-attribution spec-ledger adapter.

The adapter (``lawvm.new_zealand.spec_ledger_adapter``) turns NZ dry-run oracle
outcomes into a per-rule discovered-spec ledger by reusing the jurisdiction-neutral
spec-ledger core read-only. These tests pin:

- rule-catalog completeness: every cataloged rule_id is a real dry-run constant,
  and every oracle rule_id a kernel can emit is cataloged (no silent blind spot);
- the agrees -> corroborated / residual -> contradicted mapping;
- disposition-mapping HONESTY: a genuine content/position mismatch stays
  ``lawvm_wrong`` (falsifying), never silently ``oracle_suspect``;
- ledger arithmetic (firings, corroborated_est, contradicted) end to end;
- a loud ``legacy_unknown`` for a fired oracle rule_id with no catalog entry.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import List

import pytest

import lawvm.new_zealand.dry_run as dry_run
from lawvm.new_zealand.dry_run import (
    NZ_DRY_RUN_INSERT_AGREES_RULE_ID,
    NZ_DRY_RUN_INSERT_RESIDUAL_CONTENT_MISMATCH_RULE_ID,
    NZ_DRY_RUN_INSERT_RESIDUAL_NOT_PRESENT_RULE_ID,
    NZ_DRY_RUN_INSERT_RESIDUAL_POSITION_MISMATCH_RULE_ID,
    NZ_DRY_RUN_REPEAL_REMOVED_AGREES_RULE_ID,
    NZ_DRY_RUN_REPEAL_TOMBSTONE_AGREES_RULE_ID,
    NZ_DRY_RUN_REPLACE_AGREES_RULE_ID,
    NZ_DRY_RUN_REPLACE_RESIDUAL_MISMATCH_RULE_ID,
    NZ_DRY_RUN_REPLACE_RESIDUAL_TARGET_MISSING_RULE_ID,
    NZ_DRY_RUN_RESIDUAL_TARGET_MISSING_IN_ORACLE_RULE_ID,
    NZ_DRY_RUN_RESIDUAL_TARGET_NOT_REMOVED_IN_ORACLE_RULE_ID,
    NZ_DRY_RUN_RESIDUAL_TARGET_NOT_TOMBSTONE_IN_ORACLE_RULE_ID,
    NZ_DRY_RUN_TEXT_REPLACE_AGREES_RULE_ID,
    NZ_DRY_RUN_TEXT_RESIDUAL_NEW_TEXT_ABSENT_RULE_ID,
    NZ_DRY_RUN_TEXT_RESIDUAL_OLD_TEXT_REMAINS_RULE_ID,
    NZ_DRY_RUN_TEXT_RESIDUAL_TARGET_MISSING_RULE_ID,
    NZDryRunReport,
    NZMutationBoundaryProof,
)
from lawvm.new_zealand.spec_ledger_adapter import (
    NZ_LEGACY_UNKNOWN,
    NZ_RULE_CONFIDENCE,
    NZ_RULE_SPECS,
    _disposition_for,
    build_nz_spec_ledger,
    ledger_to_dict,
    nz_ledger_inputs_from_reports,
    render_text,
)
from lawvm.tools.spec_ledger import build_ledger


# Every oracle rule_id a dry-run kernel can emit (agree + residual). If a new
# kernel outcome appears, this list must grow and the catalog-coverage test below
# turns the gap into a loud failure rather than a silent blind spot.
_ALL_ORACLE_RULE_IDS = (
    NZ_DRY_RUN_REPEAL_TOMBSTONE_AGREES_RULE_ID,
    NZ_DRY_RUN_REPEAL_REMOVED_AGREES_RULE_ID,
    NZ_DRY_RUN_RESIDUAL_TARGET_NOT_TOMBSTONE_IN_ORACLE_RULE_ID,
    NZ_DRY_RUN_RESIDUAL_TARGET_MISSING_IN_ORACLE_RULE_ID,
    NZ_DRY_RUN_RESIDUAL_TARGET_NOT_REMOVED_IN_ORACLE_RULE_ID,
    NZ_DRY_RUN_TEXT_REPLACE_AGREES_RULE_ID,
    NZ_DRY_RUN_TEXT_RESIDUAL_OLD_TEXT_REMAINS_RULE_ID,
    NZ_DRY_RUN_TEXT_RESIDUAL_NEW_TEXT_ABSENT_RULE_ID,
    NZ_DRY_RUN_TEXT_RESIDUAL_TARGET_MISSING_RULE_ID,
    NZ_DRY_RUN_REPLACE_AGREES_RULE_ID,
    NZ_DRY_RUN_REPLACE_RESIDUAL_MISMATCH_RULE_ID,
    NZ_DRY_RUN_REPLACE_RESIDUAL_TARGET_MISSING_RULE_ID,
    NZ_DRY_RUN_INSERT_AGREES_RULE_ID,
    NZ_DRY_RUN_INSERT_RESIDUAL_NOT_PRESENT_RULE_ID,
    NZ_DRY_RUN_INSERT_RESIDUAL_CONTENT_MISMATCH_RULE_ID,
    NZ_DRY_RUN_INSERT_RESIDUAL_POSITION_MISMATCH_RULE_ID,
)

# The residual ``oracle_match`` family strings each kernel partition can return,
# paired with the disposition the adapter must assign. ``target_missing`` is the
# only present/absent (structural) family; everything else is a genuine content/
# position mismatch and MUST stay falsifying (``lawvm_wrong``).
_RESIDUAL_MATCH_FAMILIES = {
    "target_not_tombstone": "lawvm_wrong",
    "target_not_removed": "lawvm_wrong",
    "target_missing": "structural",
    "residual_old_text_remains": "lawvm_wrong",
    "residual_new_text_absent": "lawvm_wrong",
    "residual_replacement_mismatch": "lawvm_wrong",
    "residual_insert_not_present": "lawvm_wrong",
    "residual_insert_content_mismatch": "lawvm_wrong",
    "residual_insert_position_mismatch": "lawvm_wrong",
}


def _proof(
    *,
    op_id: str,
    oracle_match: str,
    oracle_rule_id: str,
    target_address: str = "section:108",
) -> NZMutationBoundaryProof:
    return NZMutationBoundaryProof(
        op_id=op_id,
        action="REPEAL",
        target_address=target_address,
        selected_source_path=("prov:108",),
        target_xml_id="DLM1",
        target_digest_before="before",
        target_digest_after="after",
        operation_payload="payload",
        occupancy_before="substantive",
        occupancy_after="tombstone",
        parent_source_path=(),
        parent_digest_before="",
        parent_digest_after="",
        unaffected_neighbor_paths=(),
        unaffected_neighbor_digests_before=(),
        unaffected_neighbor_digests_after=(),
        neighbors_unchanged=True,
        oracle_version_id="v1",
        oracle_target_present=oracle_match != "target_missing",
        oracle_target_occupancy="tombstone",
        oracle_match=oracle_match,
        oracle_match_rule_id=oracle_rule_id,
    )


def _report(work_id: str, proofs: List[NZMutationBoundaryProof], family: str = "repeal") -> NZDryRunReport:
    return NZDryRunReport(
        work_id=work_id,
        operation_family=family,
        proofs=tuple(proofs),
        refusals=(),
        preflight_status="ready_for_dry_run_replay",
    )


# ---------------------------------------------------------------------------
# Catalog completeness
# ---------------------------------------------------------------------------

def test_every_cataloged_rule_id_is_a_real_dry_run_constant() -> None:
    real_ids = {value for name, value in vars(dry_run).items()
                if name.endswith("_RULE_ID") and isinstance(value, str)}
    for rule_id in NZ_RULE_SPECS:
        assert rule_id in real_ids, f"cataloged rule_id {rule_id!r} is not a dry_run constant"
        assert NZ_RULE_SPECS[rule_id].strip(), f"{rule_id} has an empty believed_spec"


def test_every_oracle_rule_a_kernel_can_emit_is_cataloged() -> None:
    # No fired oracle rule_id may be an uncataloged blind spot.
    for rule_id in _ALL_ORACLE_RULE_IDS:
        assert rule_id in NZ_RULE_SPECS, f"oracle rule_id {rule_id!r} has no catalog entry"
        assert rule_id in NZ_RULE_CONFIDENCE


def test_catalog_has_no_extra_unknown_entries() -> None:
    assert set(NZ_RULE_SPECS) == set(_ALL_ORACLE_RULE_IDS)


# ---------------------------------------------------------------------------
# Disposition mapping honesty
# ---------------------------------------------------------------------------

def test_disposition_mapping_is_honest_no_oracle_suspect_for_mismatch() -> None:
    for family, expected in _RESIDUAL_MATCH_FAMILIES.items():
        assert _disposition_for(family) == expected
    # HONESTY: no NZ residual family is dispositioned ``oracle_suspect`` today.
    dispositions = {_disposition_for(f) for f in _RESIDUAL_MATCH_FAMILIES}
    assert "oracle_suspect" not in dispositions


def test_unknown_oracle_match_is_loud_not_silent() -> None:
    # An unmapped residual family is ``unknown`` (loud), never swallowed as a pass.
    assert _disposition_for("some_future_residual_family") == "unknown"


# ---------------------------------------------------------------------------
# agrees -> corroborated, residual -> contradicted mapping + arithmetic
# ---------------------------------------------------------------------------

def test_agrees_proof_is_a_corroborating_firing_with_no_divergence() -> None:
    report = _report("act_public_2001_1", [
        _proof(op_id="op1", oracle_match="agrees", oracle_rule_id=NZ_DRY_RUN_REPEAL_TOMBSTONE_AGREES_RULE_ID),
        _proof(op_id="op2", oracle_match="agrees", oracle_rule_id=NZ_DRY_RUN_REPEAL_TOMBSTONE_AGREES_RULE_ID),
    ])
    inputs = nz_ledger_inputs_from_reports([report])
    assert len(inputs) == 1
    inp = inputs[0]
    assert inp.rule_firings == {NZ_DRY_RUN_REPEAL_TOMBSTONE_AGREES_RULE_ID: 2}
    assert inp.divergences == []

    ledger = build_ledger(inputs, jurisdiction="nz", mode="dry_run", catalog=NZ_RULE_SPECS)
    entry = ledger.rules[NZ_DRY_RUN_REPEAL_TOMBSTONE_AGREES_RULE_ID]
    assert entry.firings == 2
    assert entry.corroborated_est == 2
    assert entry.contradicted == 0


def test_residual_proof_is_a_contradicting_divergence() -> None:
    report = _report("act_public_2001_2", [
        _proof(
            op_id="op1",
            oracle_match="target_not_tombstone",
            oracle_rule_id=NZ_DRY_RUN_RESIDUAL_TARGET_NOT_TOMBSTONE_IN_ORACLE_RULE_ID,
        ),
    ])
    inputs = nz_ledger_inputs_from_reports([report])
    inp = inputs[0]
    # The residual rule's firing is tallied so its arithmetic is well-formed.
    assert inp.rule_firings == {NZ_DRY_RUN_RESIDUAL_TARGET_NOT_TOMBSTONE_IN_ORACLE_RULE_ID: 1}
    assert len(inp.divergences) == 1
    div = inp.divergences[0]
    assert div.disposition == "lawvm_wrong"
    assert div.rule_id == NZ_DRY_RUN_RESIDUAL_TARGET_NOT_TOMBSTONE_IN_ORACLE_RULE_ID

    ledger = build_ledger(inputs, jurisdiction="nz", mode="dry_run", catalog=NZ_RULE_SPECS)
    entry = ledger.rules[NZ_DRY_RUN_RESIDUAL_TARGET_NOT_TOMBSTONE_IN_ORACLE_RULE_ID]
    assert entry.firings == 1
    assert entry.contradicted == 1
    assert entry.corroborated_est == 0


def test_target_missing_residual_is_structural_falsifying() -> None:
    report = _report("act_public_2001_3", [
        _proof(
            op_id="op1",
            oracle_match="target_missing",
            oracle_rule_id=NZ_DRY_RUN_RESIDUAL_TARGET_MISSING_IN_ORACLE_RULE_ID,
        ),
    ])
    inputs = nz_ledger_inputs_from_reports([report])
    div = inputs[0].divergences[0]
    assert div.disposition == "structural"
    ledger = build_ledger(inputs, jurisdiction="nz", mode="dry_run", catalog=NZ_RULE_SPECS)
    # structural counts as falsifying evidence in the neutral core.
    assert ledger.rules[NZ_DRY_RUN_RESIDUAL_TARGET_MISSING_IN_ORACLE_RULE_ID].contradicted == 1
    assert ledger.statute_real_bugs["act_public_2001_3"] == 1


def test_mixed_corpus_arithmetic_is_consistent() -> None:
    reports = [
        _report("w1", [
            _proof(op_id="a", oracle_match="agrees", oracle_rule_id=NZ_DRY_RUN_TEXT_REPLACE_AGREES_RULE_ID),
            _proof(op_id="b", oracle_match="agrees", oracle_rule_id=NZ_DRY_RUN_TEXT_REPLACE_AGREES_RULE_ID),
            _proof(
                op_id="c",
                oracle_match="residual_old_text_remains",
                oracle_rule_id=NZ_DRY_RUN_TEXT_RESIDUAL_OLD_TEXT_REMAINS_RULE_ID,
            ),
        ], family="text_replace"),
        _report("w2", [
            _proof(op_id="d", oracle_match="agrees", oracle_rule_id=NZ_DRY_RUN_TEXT_REPLACE_AGREES_RULE_ID),
        ], family="text_replace"),
    ]
    ledger = build_ledger(
        nz_ledger_inputs_from_reports(reports),
        jurisdiction="nz",
        mode="dry_run",
        catalog=NZ_RULE_SPECS,
    )
    agree = ledger.rules[NZ_DRY_RUN_TEXT_REPLACE_AGREES_RULE_ID]
    assert agree.firings == 3
    assert agree.corroborated_est == 3
    assert agree.contradicted == 0
    contra = ledger.rules[NZ_DRY_RUN_TEXT_RESIDUAL_OLD_TEXT_REMAINS_RULE_ID]
    assert contra.firings == 1
    assert contra.contradicted == 1


# ---------------------------------------------------------------------------
# legacy_unknown blind spot
# ---------------------------------------------------------------------------

def test_uncataloged_fired_rule_is_flagged_legacy_unknown() -> None:
    report = _report("w_blind", [
        _proof(op_id="x", oracle_match="agrees", oracle_rule_id="nz_dry_run_some_future_uncataloged_rule"),
    ])
    ledger = build_ledger(
        nz_ledger_inputs_from_reports([report]),
        jurisdiction="nz",
        mode="dry_run",
        catalog=NZ_RULE_SPECS,
    )
    art = ledger_to_dict(ledger)
    assert "nz_dry_run_some_future_uncataloged_rule" in art["legacy_unknown_rules"]
    blind = next(r for r in art["rules"] if r["rule_id"] == "nz_dry_run_some_future_uncataloged_rule")
    assert blind["cataloged"] is False
    assert blind["confidence"] == NZ_LEGACY_UNKNOWN


def test_render_text_marks_uncataloged_rule() -> None:
    report = _report("w_blind", [
        _proof(op_id="x", oracle_match="agrees", oracle_rule_id="nz_dry_run_some_future_uncataloged_rule"),
    ])
    ledger = build_ledger(
        nz_ledger_inputs_from_reports([report]),
        jurisdiction="nz",
        mode="dry_run",
        catalog=NZ_RULE_SPECS,
    )
    text = render_text(ledger)
    assert "[UNCATALOGED!]" in text
    assert "LEGACY_UNKNOWN" in text


def test_nz_spec_ledger_main_persists_shared_diffable_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import lawvm.new_zealand.spec_ledger_adapter as adapter

    ledger = build_ledger(
        nz_ledger_inputs_from_reports(
            [
                _report(
                    "act_public_2001_1",
                    [
                        _proof(
                            op_id="op1",
                            oracle_match="agrees",
                            oracle_rule_id=NZ_DRY_RUN_REPEAL_TOMBSTONE_AGREES_RULE_ID,
                        )
                    ],
                )
            ]
        ),
        jurisdiction="nz",
        mode="dry_run_after_tree_vs_archived_on_or_after_xml",
        catalog=NZ_RULE_SPECS,
    )
    out_dir = tmp_path / "ledger-out"
    monkeypatch.setattr(adapter, "build_nz_spec_ledger", lambda *args, **kwargs: ledger)

    adapter.main(
        SimpleNamespace(
            db="missing-but-monkeypatched.farchive",
            work_id=(),
            corpus=None,
            max_works=None,
            json=False,
            json_out="",
            out_dir=str(out_dir),
        )
    )

    assert (out_dir / "spec_ledger.json").exists()
    assert (out_dir / "spec_ledger.md").exists()
    assert "wrote" in capsys.readouterr().err


def test_empty_reports_yield_no_inputs() -> None:
    assert nz_ledger_inputs_from_reports([_report("w_empty", [])]) == []


# ---------------------------------------------------------------------------
# End-to-end smoke over the smoke corpus (gated on the farchive being present).
# ---------------------------------------------------------------------------

@pytest.mark.slow
def test_spec_ledger_smoke_corpus_end_to_end() -> None:
    from pathlib import Path

    db_path = Path("data/nz_legislation.farchive")
    corpus_path = Path("data/nz/bench_corpus_smoke.csv")
    if not db_path.exists() or not corpus_path.exists():
        pytest.skip("NZ farchive / smoke corpus not present in this checkout")

    ledger = build_nz_spec_ledger(db_path, corpus_path=corpus_path)
    art = ledger_to_dict(ledger)

    # The discovered spec must materialize at least the corroborated repeal rule,
    # and every fired rule must be cataloged (no legacy_unknown blind spots).
    assert art["n_rules"] >= 1
    fired_ids = {r["rule_id"] for r in art["rules"]}
    assert NZ_DRY_RUN_REPEAL_TOMBSTONE_AGREES_RULE_ID in fired_ids
    assert art["legacy_unknown_rules"] == []

    # Arithmetic invariant per rule: corroborated_est + divergences == firings,
    # and contradicted <= divergences.
    for rule in art["rules"]:
        assert rule["corroborated_est"] + rule["divergences"] == rule["firings"]
        assert rule["contradicted"] <= rule["divergences"]
        # No residual was flattered into oracle_suspect.
        assert "oracle_suspect" not in rule["by_disposition"]

"""Integration: contingent-commencement claim gates the PIT compile filter.

Proves the opt-in gating through ``UKReplayPipeline.compile_ops_for_statute``:

  - a VALIDATED owned claim APPLIES the conditional repeal at a post-deadline PIT
    and WITHHOLDS it at a pre-deadline PIT;
  - with NO claim authored the gate never fires (replay-neutral by default);
  - an unvalidated/mismatched claim never gates replay (it is recorded but does
    not change the effect set).

The effects/archive surfaces are monkeypatched so the test is deterministic and
does not depend on a specific corpus statute carrying this rare shape.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import lawvm.uk_legislation.uk_amendment_replay as replay_mod
from lawvm.uk_legislation.contingent_commencement_claim import (
    REPEAL_FIRES_ON_DID_NOT_COMMENCE,
    RESOLUTION_DID_NOT_COMMENCE,
    ContingentCommencementClaim,
)
from lawvm.uk_legislation.effects import UKEffectRecord

_SNIPPET = (
    "Section 12 is repealed at the end of 2026 if it has not been brought "
    "into force before the end of 2026."
)
_STATUTE = "ukpga/2020/1"
_EFFECT_ID = "e-77"


def _conditional_repeal_effect() -> UKEffectRecord:
    return UKEffectRecord(
        effect_id=_EFFECT_ID,
        effect_type="repealed",
        applied=True,
        requires_applied=False,
        modified="2026-01-01",
        affected_uri="/id/ukpga/2020/1/section/12",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2020",
        affected_number="1",
        affected_provisions="s. 12",
        affecting_uri="/id/ukpga/2020/1",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2020",
        affecting_number="1",
        affecting_provisions="s. 1",
        affecting_title="Test Act",
        comments=_SNIPPET,
        in_force_dates=[{"date": "2026-12-31", "prospective": "false"}],
    )


@pytest.fixture
def patched_pipeline(monkeypatch) -> replay_mod.UKReplayPipeline:
    monkeypatch.setattr(
        replay_mod,
        "load_effects_for_statute_from_archive",
        lambda *a, **k: [_conditional_repeal_effect()],
    )
    monkeypatch.setattr(
        replay_mod,
        "resolve_uk_effective_date_overrides_for_replay",
        lambda *a, **k: {},
    )
    monkeypatch.setattr(
        replay_mod,
        "get_affecting_act_xml_from_archive",
        lambda *a, **k: None,
    )
    return replay_mod.UKReplayPipeline(Path("."))


def _did_not_commence_claim() -> ContingentCommencementClaim:
    return ContingentCommencementClaim(
        claim_id="c1",
        claim_kind="contingent_commencement",
        statute_id=_STATUTE,
        effect_id=_EFFECT_ID,
        trigger_id="ukpga/2020/1/section/12",
        deadline_date="2026-12-31",
        source_snippet=_SNIPPET,
        resolution=RESOLUTION_DID_NOT_COMMENCE,
        repeal_fires_on=REPEAL_FIRES_ON_DID_NOT_COMMENCE,
    )


def _gate_rules(diags: list[dict]) -> list[str]:
    return [
        str(d.get("rule_id"))
        for d in diags
        if "contingent_commencement" in str(d.get("rule_id", ""))
    ]


def test_validated_claim_applies_repeal_at_post_deadline_pit(patched_pipeline):
    diags: list[dict] = []
    patched_pipeline.compile_ops_for_statute(
        _STATUTE,
        pit_date="2027-01-01",
        archive=object(),
        effect_diagnostics_out=diags,
        contingent_commencement_claims=[_did_not_commence_claim()],
    )
    rules = _gate_rules(diags)
    assert "uk_contingent_commencement_claim_validated" in rules
    assert "uk_contingent_commencement_repeal_applied_at_pit" in rules


def test_validated_claim_withholds_repeal_at_pre_deadline_pit(patched_pipeline):
    diags: list[dict] = []
    patched_pipeline.compile_ops_for_statute(
        _STATUTE,
        pit_date="2025-01-01",
        archive=object(),
        effect_diagnostics_out=diags,
        contingent_commencement_claims=[_did_not_commence_claim()],
    )
    rules = _gate_rules(diags)
    assert "uk_contingent_commencement_claim_validated" in rules
    assert "uk_contingent_commencement_repeal_withheld_pre_deadline" in rules


def test_absent_claim_does_not_fire_gate(patched_pipeline):
    # Replay-neutral by default: no claim => no contingent gate diagnostics at all.
    diags: list[dict] = []
    patched_pipeline.compile_ops_for_statute(
        _STATUTE,
        pit_date="2027-01-01",
        archive=object(),
        effect_diagnostics_out=diags,
    )
    assert _gate_rules(diags) == []


def test_mismatched_claim_is_recorded_but_does_not_gate(patched_pipeline):
    # A claim whose source_snippet is not a real conditional-temporal-repeal
    # shape is rejected by the validator and never reaches the gate, so it
    # cannot change the effect set (no free-form override).
    bad = ContingentCommencementClaim(
        claim_id="c-bad",
        claim_kind="contingent_commencement",
        statute_id=_STATUTE,
        effect_id=_EFFECT_ID,
        trigger_id="ukpga/2020/1/section/12",
        deadline_date="2026-12-31",
        source_snippet="Section 12 is repealed.",
        resolution=RESOLUTION_DID_NOT_COMMENCE,
        repeal_fires_on=REPEAL_FIRES_ON_DID_NOT_COMMENCE,
    )
    diags: list[dict] = []
    patched_pipeline.compile_ops_for_statute(
        _STATUTE,
        pit_date="2027-01-01",
        archive=object(),
        effect_diagnostics_out=diags,
        contingent_commencement_claims=[bad],
    )
    rules = _gate_rules(diags)
    # validator rejected it; no gate (applied/withheld) ran.
    assert any("rejected" in r for r in rules)
    assert not any("repeal_applied" in r or "repeal_withheld" in r for r in rules)

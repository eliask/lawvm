"""Integration: appropriate-place claim emits the insert through compile_ops.

Proves the opt-in emission through ``UKReplayPipeline.compile_ops_for_statute``:

  - a VALIDATED owned claim EMITS an INSERT op at the claimed position for an
    effect that lowering otherwise rejects to the manual frontier;
  - with NO claim authored no insert op is produced (replay-neutral by default);
  - an unvalidated/mismatched claim is recorded but never emits an op.

The effects/archive surfaces are monkeypatched so the test is deterministic and
does not depend on a specific corpus statute carrying this shape.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import lawvm.uk_legislation.uk_amendment_replay as replay_mod
from lawvm.core.semantic_types import StructuralAction
from lawvm.uk_legislation.appropriate_place_claim import (
    APPROPRIATE_PLACE_INSERT_CLAIM_KIND,
    POSITION_ALPHABETICAL_INDEX,
    AppropriatePlaceInsertClaim,
)
from lawvm.uk_legislation.effects import UKEffectRecord

_SNIPPET = "At the appropriate place insert the following entry."
_STATUTE = "ukpga/2008/17"
_EFFECT_ID = "e-ap"


def _appropriate_place_effect() -> UKEffectRecord:
    return UKEffectRecord(
        effect_id=_EFFECT_ID,
        effect_type="words inserted",
        applied=True,
        requires_applied=False,
        modified="2018-01-01",
        affected_uri="/id/ukpga/2008/17/section/31",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2008",
        affected_number="17",
        affected_provisions="s. 31(12)",
        affecting_uri="/id/uksi/2018/1040",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2018",
        affecting_number="1040",
        affecting_provisions="reg. 2",
        affecting_title="Test Regulations",
        comments=_SNIPPET,
        in_force_dates=[{"date": "2018-10-01", "prospective": "false"}],
    )


@pytest.fixture
def patched_pipeline(monkeypatch) -> replay_mod.UKReplayPipeline:
    monkeypatch.setattr(
        replay_mod,
        "load_effects_for_statute_from_archive",
        lambda *a, **k: [_appropriate_place_effect()],
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


def _valid_claim() -> AppropriatePlaceInsertClaim:
    return AppropriatePlaceInsertClaim(
        claim_id="c1",
        claim_kind=APPROPRIATE_PLACE_INSERT_CLAIM_KIND,
        statute_id=_STATUTE,
        effect_id=_EFFECT_ID,
        target_list_eid="s31-list",
        entry_label="new-entry",
        entry_text="the inserted entry text",
        source_snippet=_SNIPPET,
        position_kind=POSITION_ALPHABETICAL_INDEX,
        alphabetical_index=0,
    )


def _ap_rules(diags: list[dict]) -> list[str]:
    return [
        str(d.get("rule_id"))
        for d in diags
        if "appropriate_place" in str(d.get("rule_id", ""))
    ]


def test_validated_claim_emits_insert_op(patched_pipeline):
    diags: list[dict] = []
    ops = patched_pipeline.compile_ops_for_statute(
        _STATUTE,
        pit_date="2019-01-01",
        archive=object(),
        effect_diagnostics_out=diags,
        appropriate_place_claims=[_valid_claim()],
    )
    rules = _ap_rules(diags)
    assert "uk_appropriate_place_claim_validated" in rules
    assert "uk_appropriate_place_insert_emitted_at_claimed_position" in rules
    inserts = [op for op in ops if op.action is StructuralAction.INSERT]
    assert any(op.target.leaf_label() == "new-entry" for op in inserts)


def test_absent_claim_emits_no_insert(patched_pipeline):
    # Replay-neutral by default: no claim => no appropriate-place op at all.
    diags: list[dict] = []
    ops = patched_pipeline.compile_ops_for_statute(
        _STATUTE,
        pit_date="2019-01-01",
        archive=object(),
        effect_diagnostics_out=diags,
    )
    assert _ap_rules(diags) == []
    assert not any(op.target.leaf_label() == "new-entry" for op in ops)


def test_mismatched_claim_recorded_but_emits_no_insert(patched_pipeline):
    # A claim whose source_snippet is not a real appropriate-place insert is
    # rejected by the validator and never reaches the gate, so it cannot emit.
    bad = AppropriatePlaceInsertClaim(
        claim_id="c-bad",
        claim_kind=APPROPRIATE_PLACE_INSERT_CLAIM_KIND,
        statute_id=_STATUTE,
        effect_id=_EFFECT_ID,
        target_list_eid="s31-list",
        entry_label="new-entry",
        entry_text="the inserted entry text",
        source_snippet="Section 31 is repealed.",
        position_kind=POSITION_ALPHABETICAL_INDEX,
        alphabetical_index=0,
    )
    diags: list[dict] = []
    ops = patched_pipeline.compile_ops_for_statute(
        _STATUTE,
        pit_date="2019-01-01",
        archive=object(),
        effect_diagnostics_out=diags,
        appropriate_place_claims=[bad],
    )
    rules = _ap_rules(diags)
    assert any("rejected" in r for r in rules)
    assert not any("insert_emitted" in r for r in rules)
    assert not any(op.target.leaf_label() == "new-entry" for op in ops)

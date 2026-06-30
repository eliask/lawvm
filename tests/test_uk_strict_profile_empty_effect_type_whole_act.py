"""§2.9 disposition-3 per-site test for the ``empty_effect_type_whole_act``
strict-profile consume site (Tier C PR2, site 6).

The site lives at ``reject_external_or_partial_whole_act_scope`` in
``effect_target_prelude.py``: an effect whose metadata points at the whole
Act but carries NO effect type, with source text that does not explicitly
say "the whole Act ... is repealed", is rejected by default
(``uk_effect_empty_type_whole_act_action_rejected``) — lowering must not
infer a destructive whole-Act action from incidental source text. When the
active strict-profile carries ``allows_uk_empty_effect_type_whole_act=True``
the default-block is LIFTED and an audited
``uk_strict_profile_lifted_empty_effect_type_whole_act`` observation fires.

Trigger: SYNTHETIC. ``uk_effect_empty_type_whole_act_action_rejected`` does
not appear in ``scripts/baselines/uk_broad_2026-05-31.json`` (count 0), so no
readily-available real corpus witness exists; a synthetic effect with an
empty effect type and incidental (non-repeal) source text reproduces the
trigger. Grounding-neutral by construction (test-only).
"""
from __future__ import annotations

from lawvm.core.ir import LegalAddress
from lawvm.core.semantic_types import FacetKind
import lawvm.uk_legislation.effect_target_prelude as effect_target_prelude
from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.strict_profile import UK_INGESTION_V1, UkStrictProfile

_LIFT_RULE_ID = "uk_strict_profile_lifted_empty_effect_type_whole_act"
_BLOCK_RULE_ID = "uk_effect_empty_type_whole_act_action_rejected"
# Source text that does NOT match the explicit "whole Act ... is repealed"
# guard, so the empty-type whole-Act rejection branch is entered.
_INCIDENTAL_TEXT = "These provisions relate to the operation of the whole Act in practice."


def _empty_type_whole_act_effect() -> UKEffectRecord:
    return UKEffectRecord(
        effect_id="key-empty-type-whole-act",
        effect_type="",
        applied=True,
        requires_applied=False,
        modified="2012-01-01",
        affected_uri="http://www.legislation.gov.uk/id/ukpga/2000/1",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="1",
        affected_provisions="Act",
        affecting_uri="http://www.legislation.gov.uk/id/uksi/2012/1",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2012",
        affecting_number="1",
        affecting_provisions="reg. 3",
        affecting_title="Test Regulations",
        in_force_dates=[{"date": "2012-01-01", "prospective": "false"}],
    )


def _reject(rejections: list[dict]) -> bool:
    return effect_target_prelude.reject_external_or_partial_whole_act_scope(
        effect=_empty_type_whole_act_effect(),
        action="",
        effect_type="",
        t_str="Act",
        target=LegalAddress(path=(), special=FacetKind.WHOLE_ACT),
        extracted_el=None,
        extracted_text=_INCIDENTAL_TEXT,
        lowering_rejections_out=rejections,
    )


def test_default_profile_preserves_empty_type_whole_act_block(monkeypatch) -> None:
    """§2.9 disposition 2 (negative): no strict-profile loaded — block
    preserved, no lift."""
    monkeypatch.delenv("LAWVM_UK_STRICT_PROFILE", raising=False)
    rejections: list[dict] = []
    rejected = _reject(rejections)
    assert rejected is True
    rule_ids = {r.get("rule_id") for r in rejections}
    assert _BLOCK_RULE_ID in rule_ids
    assert _LIFT_RULE_ID not in rule_ids


def test_strict_profile_loaded_but_not_allowed_still_blocks(monkeypatch) -> None:
    """§2.9 disposition 2: strict-profile loaded (default preset) but
    ``allows_uk_empty_effect_type_whole_act=False`` — block preserved."""
    monkeypatch.setenv("LAWVM_UK_STRICT_PROFILE", "uk_ingestion_v1")
    rejections: list[dict] = []
    rejected = _reject(rejections)
    assert rejected is True
    rule_ids = {r.get("rule_id") for r in rejections}
    assert _BLOCK_RULE_ID in rule_ids
    assert _LIFT_RULE_ID not in rule_ids


def test_strict_profile_allowed_lifts_empty_type_whole_act_block_with_audit(monkeypatch) -> None:
    """§2.9 disposition 3: strict-profile loaded AND
    ``allows_uk_empty_effect_type_whole_act=True`` — block LIFTED with
    audited observation."""
    allowed_profile = UkStrictProfile(
        core_profile=UK_INGESTION_V1,
        allows_uk_empty_effect_type_whole_act=True,
    )
    monkeypatch.setattr(
        effect_target_prelude, "active_uk_strict_profile", lambda: allowed_profile
    )
    rejections: list[dict] = []
    rejected = _reject(rejections)
    assert rejected is False, "block must be LIFTED when strict-allows"
    lifts = [r for r in rejections if r.get("rule_id") == _LIFT_RULE_ID]
    assert lifts, "lift audit observation MUST be emitted"
    lift = lifts[0]
    assert lift["family"] == "unsupported_target_scope"
    assert lift["reason_code"] == "strict_profile_authorized_empty_type_whole_act"
    assert lift["strict_disposition"] == "proceed"
    assert lift["strict_profile_name"] == UK_INGESTION_V1.name
    assert lift["lifted_rejection_rule_id"] == _BLOCK_RULE_ID
    assert _BLOCK_RULE_ID not in {r.get("rule_id") for r in rejections}, (
        "block-rejection receipt must NOT fire when the lift is active"
    )

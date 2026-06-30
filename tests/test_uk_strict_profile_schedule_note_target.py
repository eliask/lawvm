"""§2.9 disposition-3 per-site test for the ``schedule_note_target`` strict-
profile consume site (Tier C PR2, site 4).

The site lives at ``reject_unsupported_target_facet`` in
``effect_target_prelude.py``: a target naming a schedule note (``Sch. 5
para. 3 note``) is rejected by default
(``uk_effect_schedule_note_target_rejected``) — lowering must not coerce a
note into paragraph/subparagraph structure. When the active strict-profile
carries ``allows_uk_schedule_note_target=True`` the default-block is LIFTED
and an audited ``uk_strict_profile_lifted_schedule_note_target`` observation
fires.

Trigger: CORPUS-WITNESSED. ``uk_effect_schedule_note_target_rejected`` is a
live corpus rejection — e.g. ``ukpga/1990/8`` (Town and Country Planning
Act 1990) shows 16/18 occurrences in
``scripts/baselines/uk_broad_2026-05-31.json``. The witness shape (a schedule
note target) is reproduced here as a synthetic effect so the test is
self-contained and does not require a full corpus statute compile (the same
direct-reject-function pattern as the devolved per-site test). Grounding-
neutral by construction (test-only).
"""
from __future__ import annotations

import lawvm.uk_legislation.effect_target_prelude as effect_target_prelude
from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.strict_profile import UK_INGESTION_V1, UkStrictProfile

_LIFT_RULE_ID = "uk_strict_profile_lifted_schedule_note_target"
_BLOCK_RULE_ID = "uk_effect_schedule_note_target_rejected"
_TARGET_REF = "Sch. 5 para. 3 note"


def _schedule_note_effect() -> UKEffectRecord:
    return UKEffectRecord(
        effect_id="key-schedule-note-target",
        effect_type="inserted",
        applied=True,
        requires_applied=False,
        modified="2012-01-01",
        affected_uri="http://www.legislation.gov.uk/id/ukpga/1990/8",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="1990",
        affected_number="8",
        affected_provisions=_TARGET_REF,
        affecting_uri="http://www.legislation.gov.uk/id/uksi/2012/1",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2012",
        affecting_number="1",
        affecting_provisions="reg. 3",
        affecting_title="Test Regulations",
        in_force_dates=[{"date": "2012-01-01", "prospective": "false"}],
    )


def _reject(rejections: list[dict]) -> bool:
    return effect_target_prelude.reject_unsupported_target_facet(
        effect=_schedule_note_effect(),
        action="insert",
        t_str=_TARGET_REF,
        target_candidate_count=0,
        structured_crossheading_op_built=False,
        extracted_el=None,
        extracted_text=None,
        source_root=None,
        lowering_rejections_out=rejections,
    )


def test_default_profile_preserves_schedule_note_block(monkeypatch) -> None:
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
    ``allows_uk_schedule_note_target=False`` — block preserved, no lift."""
    monkeypatch.setenv("LAWVM_UK_STRICT_PROFILE", "uk_ingestion_v1")
    rejections: list[dict] = []
    rejected = _reject(rejections)
    assert rejected is True
    rule_ids = {r.get("rule_id") for r in rejections}
    assert _BLOCK_RULE_ID in rule_ids
    assert _LIFT_RULE_ID not in rule_ids


def test_strict_profile_allowed_lifts_schedule_note_block_with_audit(monkeypatch) -> None:
    """§2.9 disposition 3: strict-profile loaded AND
    ``allows_uk_schedule_note_target=True`` — block LIFTED with audited
    observation."""
    allowed_profile = UkStrictProfile(
        core_profile=UK_INGESTION_V1,
        allows_uk_schedule_note_target=True,
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
    assert lift["family"] == "unsupported_target_facet"
    assert lift["reason_code"] == "strict_profile_authorized_schedule_note_target"
    assert lift["strict_disposition"] == "proceed"
    assert lift["strict_profile_name"] == UK_INGESTION_V1.name
    assert lift["lifted_rejection_rule_id"] == _BLOCK_RULE_ID
    assert _BLOCK_RULE_ID not in {r.get("rule_id") for r in rejections}, (
        "block-rejection receipt must NOT fire when the lift is active"
    )

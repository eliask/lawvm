"""§2.9 disposition-3 per-site test for the ``crossheading_insert`` strict-
profile consume site (Tier C PR2, site 3).

The site lives at ``reject_unsupported_target_facet`` in
``effect_target_prelude.py``: a target that names *only* a cross-heading
facet (``s. 221 cross-heading``) with no standalone Pblock payload is
rejected by default (``uk_effect_crossheading_insert_rejected``) — lowering
cannot coerce the heading instruction into a body provision insert without
corrupting structure. When the active strict-profile carries
``allows_uk_crossheading_insert=True`` the default-block is LIFTED and an
audited ``uk_strict_profile_lifted_crossheading_insert`` observation fires.

Trigger: SYNTHETIC. The ``uk_effect_crossheading_insert_rejected`` rule does
not appear in the ``scripts/baselines/uk_broad_2026-05-31.json`` ratchet
(count 0), so no readily-available real corpus witness exists; this mirrors
the synthetic-effect approach already used by the savings/devolved per-site
tests (``test_uk_effect_savings_references.py`` /
``test_uk_devolved_whole_act_repeal_extent.py``). Grounding-neutral by
construction (test-only; drives the already-wired reject function directly).
"""
from __future__ import annotations

import lawvm.uk_legislation.effect_target_prelude as effect_target_prelude
from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.strict_profile import UK_INGESTION_V1, UkStrictProfile

_LIFT_RULE_ID = "uk_strict_profile_lifted_crossheading_insert"
_BLOCK_RULE_ID = "uk_effect_crossheading_insert_rejected"


def _crossheading_insert_effect() -> UKEffectRecord:
    return UKEffectRecord(
        effect_id="key-crossheading-insert",
        effect_type="inserted",
        applied=True,
        requires_applied=False,
        modified="2012-01-01",
        affected_uri="http://www.legislation.gov.uk/id/ukpga/2000/1",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="1",
        affected_provisions="s. 221 cross-heading",
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
        effect=_crossheading_insert_effect(),
        action="insert",
        t_str="s. 221 cross-heading",
        target_candidate_count=0,
        structured_crossheading_op_built=False,
        extracted_el=None,
        extracted_text=None,
        source_root=None,
        lowering_rejections_out=rejections,
    )


def test_default_profile_preserves_crossheading_block(monkeypatch) -> None:
    """§2.9 disposition 2 (negative): no strict-profile loaded — the default
    block is preserved and no lift observation fires."""
    monkeypatch.delenv("LAWVM_UK_STRICT_PROFILE", raising=False)
    rejections: list[dict] = []
    rejected = _reject(rejections)
    assert rejected is True, "default must preserve the crossheading block"
    rule_ids = {r.get("rule_id") for r in rejections}
    assert _BLOCK_RULE_ID in rule_ids, "block-rejection receipt MUST be emitted"
    assert _LIFT_RULE_ID not in rule_ids, "lift must NOT fire under default profile"


def test_strict_profile_loaded_but_not_allowed_still_blocks(monkeypatch) -> None:
    """§2.9 disposition 2: strict-profile loaded (default preset) but
    ``allows_uk_crossheading_insert=False`` — block preserved, no lift."""
    monkeypatch.setenv("LAWVM_UK_STRICT_PROFILE", "uk_ingestion_v1")
    rejections: list[dict] = []
    rejected = _reject(rejections)
    assert rejected is True
    rule_ids = {r.get("rule_id") for r in rejections}
    assert _BLOCK_RULE_ID in rule_ids
    assert _LIFT_RULE_ID not in rule_ids


def test_strict_profile_allowed_lifts_crossheading_block_with_audit(monkeypatch) -> None:
    """§2.9 disposition 3: strict-profile loaded AND
    ``allows_uk_crossheading_insert=True`` — block LIFTED with audited
    observation. The §0 evidence ledger records WHO authorized + why."""
    allowed_profile = UkStrictProfile(
        core_profile=UK_INGESTION_V1,
        allows_uk_crossheading_insert=True,
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
    assert lift["reason_code"] == "strict_profile_authorized_crossheading_insert"
    assert lift["strict_disposition"] == "proceed"
    assert lift["strict_profile_name"] == UK_INGESTION_V1.name
    assert lift["lifted_rejection_rule_id"] == _BLOCK_RULE_ID
    assert _BLOCK_RULE_ID not in {r.get("rule_id") for r in rejections}, (
        "block-rejection receipt must NOT fire when the lift is active"
    )

"""§2.9 disposition-3 per-site test for the ``heading_only_facet`` strict-
profile consume site (Tier C PR2, site 5).

The site lives at ``reject_unsupported_target_facet`` in
``effect_target_prelude.py``: a target naming only a heading/sidenote facet
(``s. 5 heading``) with an effect type that has no supported word-patch
lowering is rejected by default
(``uk_effect_heading_only_ref_rejected``) — lowering cannot safely mutate
the host provision body. When the active strict-profile carries
``allows_uk_heading_only_facet=True`` the default-block is LIFTED and an
audited ``uk_strict_profile_lifted_heading_only_facet`` observation fires.

Trigger: CORPUS-WITNESSED. ``uk_effect_heading_only_ref_rejected`` is a live
corpus rejection — e.g. ``ukpga/1968/20`` and ``ukpga/1990/8`` show several
occurrences each in ``scripts/baselines/uk_broad_2026-05-31.json``. The
witness shape (a heading-only facet target with a non-word-patch effect
type) is reproduced here as a synthetic effect so the test is self-contained
(the same direct-reject-function pattern as the devolved per-site test).
``effect_type="inserted"`` is used so
``_is_heading_facet_word_patch_supported`` returns False (the rejection
prerequisite). Grounding-neutral by construction (test-only).
"""
from __future__ import annotations

import lawvm.uk_legislation.effect_target_prelude as effect_target_prelude
from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.strict_profile import UK_INGESTION_V1, UkStrictProfile

_LIFT_RULE_ID = "uk_strict_profile_lifted_heading_only_facet"
_BLOCK_RULE_ID = "uk_effect_heading_only_ref_rejected"
_TARGET_REF = "s. 5 heading"


def _heading_only_effect() -> UKEffectRecord:
    return UKEffectRecord(
        effect_id="key-heading-only-facet",
        # "inserted" has no heading-facet word-patch support unless the source
        # carries a full replacement fragment (we pass none) — so the rejection
        # prerequisite holds.
        effect_type="inserted",
        applied=True,
        requires_applied=False,
        modified="2012-01-01",
        affected_uri="http://www.legislation.gov.uk/id/ukpga/1968/20",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="1968",
        affected_number="20",
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
        effect=_heading_only_effect(),
        action="insert",
        t_str=_TARGET_REF,
        target_candidate_count=0,
        structured_crossheading_op_built=False,
        extracted_el=None,
        extracted_text=None,
        source_root=None,
        lowering_rejections_out=rejections,
    )


def test_default_profile_preserves_heading_only_block(monkeypatch) -> None:
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
    ``allows_uk_heading_only_facet=False`` — block preserved, no lift."""
    monkeypatch.setenv("LAWVM_UK_STRICT_PROFILE", "uk_ingestion_v1")
    rejections: list[dict] = []
    rejected = _reject(rejections)
    assert rejected is True
    rule_ids = {r.get("rule_id") for r in rejections}
    assert _BLOCK_RULE_ID in rule_ids
    assert _LIFT_RULE_ID not in rule_ids


def test_strict_profile_allowed_lifts_heading_only_block_with_audit(monkeypatch) -> None:
    """§2.9 disposition 3: strict-profile loaded AND
    ``allows_uk_heading_only_facet=True`` — block LIFTED with audited
    observation."""
    allowed_profile = UkStrictProfile(
        core_profile=UK_INGESTION_V1,
        allows_uk_heading_only_facet=True,
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
    assert lift["reason_code"] == "strict_profile_authorized_heading_only_facet"
    assert lift["strict_disposition"] == "proceed"
    assert lift["strict_profile_name"] == UK_INGESTION_V1.name
    assert lift["lifted_rejection_rule_id"] == _BLOCK_RULE_ID
    assert _BLOCK_RULE_ID not in {r.get("rule_id") for r in rejections}, (
        "block-rejection receipt must NOT fire when the lift is active"
    )

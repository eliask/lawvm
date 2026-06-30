"""§2.9 disposition-3 per-site test for the ``partial_whole_act_repeal``
strict-profile consume site (Tier C PR2, site 7).

The site lives at ``reject_external_or_partial_whole_act_scope`` in
``effect_target_prelude.py``: a ``repealed in part`` effect against the whole
Act whose source text explicitly says "the whole Act (other than ...) is
repealed" is rejected by default
(``uk_effect_partial_whole_act_repeal_rejected``) — lowering cannot safely
expand that broad negative scope. When the active strict-profile carries
``allows_uk_partial_whole_act_repeal=True`` the default-block is LIFTED and
an audited ``uk_strict_profile_lifted_partial_whole_act_repeal`` observation
fires, carrying the exception provisions in the audit payload.

Trigger: SYNTHETIC. ``uk_effect_partial_whole_act_repeal_rejected`` does not
appear in ``scripts/baselines/uk_broad_2026-05-31.json`` (count 0), so no
readily-available real corpus witness exists; a synthetic effect with the
explicit "whole Act (other than ...) is repealed" source text reproduces the
trigger (matching ``_PARTIAL_WHOLE_ACT_REPEAL_RE``). Grounding-neutral by
construction (test-only).
"""
from __future__ import annotations

from lawvm.core.ir import LegalAddress
from lawvm.core.semantic_types import FacetKind
import lawvm.uk_legislation.effect_target_prelude as effect_target_prelude
from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.strict_profile import UK_INGESTION_V1, UkStrictProfile

_LIFT_RULE_ID = "uk_strict_profile_lifted_partial_whole_act_repeal"
_BLOCK_RULE_ID = "uk_effect_partial_whole_act_repeal_rejected"
_SOURCE_TEXT = "The whole Act (other than sections 1 and 2) is repealed."


def _partial_whole_act_repeal_effect() -> UKEffectRecord:
    return UKEffectRecord(
        effect_id="key-partial-whole-act-repeal",
        effect_type="repealed in part",
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
        effect=_partial_whole_act_repeal_effect(),
        action="repeal",
        effect_type="repealed in part",
        t_str="Act",
        target=LegalAddress(path=(), special=FacetKind.WHOLE_ACT),
        extracted_el=None,
        extracted_text=_SOURCE_TEXT,
        lowering_rejections_out=rejections,
    )


def test_default_profile_preserves_partial_whole_act_repeal_block(monkeypatch) -> None:
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
    ``allows_uk_partial_whole_act_repeal=False`` — block preserved."""
    monkeypatch.setenv("LAWVM_UK_STRICT_PROFILE", "uk_ingestion_v1")
    rejections: list[dict] = []
    rejected = _reject(rejections)
    assert rejected is True
    rule_ids = {r.get("rule_id") for r in rejections}
    assert _BLOCK_RULE_ID in rule_ids
    assert _LIFT_RULE_ID not in rule_ids


def test_strict_profile_allowed_lifts_partial_whole_act_repeal_block_with_audit(monkeypatch) -> None:
    """§2.9 disposition 3: strict-profile loaded AND
    ``allows_uk_partial_whole_act_repeal=True`` — block LIFTED with audited
    observation that carries the exception provisions."""
    allowed_profile = UkStrictProfile(
        core_profile=UK_INGESTION_V1,
        allows_uk_partial_whole_act_repeal=True,
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
    assert lift["reason_code"] == "strict_profile_authorized_partial_whole_act_repeal"
    assert lift["strict_disposition"] == "proceed"
    assert lift["strict_profile_name"] == UK_INGESTION_V1.name
    assert lift["lifted_rejection_rule_id"] == _BLOCK_RULE_ID
    # The exception provisions are carried in the audit payload so a triager
    # can answer which provisions the broad negative scope excluded.
    assert lift["exception_provisions"] == "sections 1 and 2"
    assert _BLOCK_RULE_ID not in {r.get("rule_id") for r in rejections}, (
        "block-rejection receipt must NOT fire when the lift is active"
    )

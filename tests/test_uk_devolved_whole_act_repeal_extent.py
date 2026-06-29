"""Family guard: a devolved instrument cannot repeal the whole UK extent of a
UK-wide Act.

A "repealed" effect targeting the *whole* of a UK Public General Act under the
authority of a Scottish/Welsh/Northern Ireland instrument repeals that Act only
*as it extends to* the devolved territory. Lowering it as a UK-wide whole-Act
repeal silently deletes the surviving (e.g. England-&-Wales) text the current
consolidation retains — the forbidden over-application direction (§2.1). The
guard rejects exactly that case while leaving genuinely-UK-wide whole-Act
repeals (and devolved partial/section repeals) untouched.
"""
from __future__ import annotations

from lawvm.core.ir import LegalAddress
from lawvm.core.semantic_types import FacetKind
from lawvm.uk_legislation.effect_target_prelude import (
    reject_external_or_partial_whole_act_scope,
)
from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.source_adjudication import classify_uk_manual_compile_frontier


def _whole_act_repeal_effect(
    *,
    affecting_class: str,
    affecting_uri: str = "",
    affected_class: str = "UnitedKingdomPublicGeneralAct",
    effect_type: str = "repealed",
) -> UKEffectRecord:
    return UKEffectRecord(
        effect_id="key-test-whole-act-repeal",
        effect_type=effect_type,
        applied=True,
        requires_applied=False,
        modified="2013-07-30",
        affected_uri="http://www.legislation.gov.uk/id/ukpga/1967/24",
        affected_class=affected_class,
        affected_year="1967",
        affected_number="24",
        affected_provisions="Act",
        affecting_uri=affecting_uri,
        affecting_class=affecting_class,
        affecting_year="2012",
        affecting_number="321",
        affecting_provisions="Sch. 5 Pt. 1",
        affecting_title="The Welfare of Animals at the Time of Killing (Scotland) Regulations 2012",
        in_force_dates=[{"date": "2013-01-01", "prospective": "false"}],
    )


def _reject(effect: UKEffectRecord) -> tuple[bool, list[dict]]:
    rejections: list[dict] = []
    rejected = reject_external_or_partial_whole_act_scope(
        effect=effect,
        action="repeal",
        effect_type=effect.effect_type,
        t_str="Act",
        target=LegalAddress(path=(), special=FacetKind.WHOLE_ACT),
        extracted_el=None,
        extracted_text=None,
        lowering_rejections_out=rejections,
    )
    return rejected, rejections


def test_scottish_si_whole_act_repeal_of_uk_act_is_rejected() -> None:
    effect = _whole_act_repeal_effect(affecting_class="ScottishStatutoryInstrument")
    assert effect.affecting_is_devolved is True
    assert effect.affected_has_uk_wide_extent_class is True
    rejected, rejections = _reject(effect)
    assert rejected is True
    assert any(
        r.get("rule_id")
        == "uk_effect_devolved_whole_act_repeal_extent_limited_rejected"
        for r in rejections
    )


def test_scottish_act_whole_act_repeal_of_uk_act_is_rejected() -> None:
    effect = _whole_act_repeal_effect(affecting_class="ScottishAct")
    rejected, rejections = _reject(effect)
    assert rejected is True
    assert rejections[0]["rule_id"] == (
        "uk_effect_devolved_whole_act_repeal_extent_limited_rejected"
    )


def test_devolved_recognized_from_uri_slug_when_class_blank() -> None:
    effect = _whole_act_repeal_effect(
        affecting_class="",
        affecting_uri="http://www.legislation.gov.uk/id/ssi/2012/321",
    )
    assert effect.affecting_is_devolved is True
    rejected, _ = _reject(effect)
    assert rejected is True


def test_uk_wide_si_whole_act_repeal_is_not_rejected() -> None:
    # A UK Statutory Instrument has UK-wide competence; its whole-Act repeal is
    # genuine and must lower normally.
    effect = _whole_act_repeal_effect(
        affecting_class="UnitedKingdomStatutoryInstrument"
    )
    assert effect.affecting_is_devolved is False
    rejected, rejections = _reject(effect)
    assert rejected is False
    assert rejections == []


def test_uk_pga_whole_act_repeal_is_not_rejected() -> None:
    effect = _whole_act_repeal_effect(
        affecting_class="UnitedKingdomPublicGeneralAct"
    )
    rejected, rejections = _reject(effect)
    assert rejected is False
    assert rejections == []


def test_devolved_repeal_of_devolved_act_is_not_rejected() -> None:
    # When the affected Act is itself a devolved-territory enactment, a devolved
    # whole-Act repeal is within competence and must not be blocked.
    effect = _whole_act_repeal_effect(
        affecting_class="ScottishStatutoryInstrument",
        affected_class="ScottishAct",
    )
    assert effect.affected_has_uk_wide_extent_class is False
    rejected, rejections = _reject(effect)
    assert rejected is False
    assert rejections == []


def test_devolved_section_repeal_is_not_rejected_by_this_guard() -> None:
    # The guard only fires for whole-Act repeals; a devolved section-level repeal
    # is not in scope here (it is handled by the ordinary target lowering path).
    effect = _whole_act_repeal_effect(affecting_class="ScottishStatutoryInstrument")
    rejections: list[dict] = []
    rejected = reject_external_or_partial_whole_act_scope(
        effect=effect,
        action="repeal",
        effect_type="repealed",
        t_str="s. 3",
        target=LegalAddress(path=(("section", "3"),)),
        extracted_el=None,
        extracted_text=None,
        lowering_rejections_out=rejections,
    )
    assert rejected is False
    assert rejections == []


def test_devolved_partial_whole_act_repeal_not_caught_by_full_repeal_branch() -> None:
    # "repealed in part" is handled by the existing partial-scope branch, not the
    # new full-repeal devolved guard; the new branch keys on the exact
    # "repealed" type so it does not double-classify partial repeals.
    effect = _whole_act_repeal_effect(
        affecting_class="ScottishStatutoryInstrument",
        effect_type="repealed in part",
    )
    rejected, rejections = _reject(effect)
    assert not any(
        r.get("rule_id")
        == "uk_effect_devolved_whole_act_repeal_extent_limited_rejected"
        for r in rejections
    )
    # The branch did not fire; whatever the partial-scope path decides, the new
    # devolved-full-repeal rule must not appear.
    del rejected


def test_devolved_whole_act_repeal_frontier_status_is_out_of_scope() -> None:
    effect = _whole_act_repeal_effect(affecting_class="ScottishStatutoryInstrument")
    rejected, rejections = _reject(effect)
    assert rejected is True
    rejected_row = next(
        r
        for r in rejections
        if r["rule_id"] == "uk_effect_devolved_whole_act_repeal_extent_limited_rejected"
    )
    classification = classify_uk_manual_compile_frontier(
        effect_type="repealed",
        source_pathology="",
        extracted_tag="",
        extracted_text="",
        lowering_rejections=[rejected_row],
        compiled_op_count=0,
        replay_applicable=False,
        structural_for_replay=False,
    )
    assert classification["manual_frontier_status"] == "non_textual_or_out_of_scope"
    assert (
        classification["rule_id"]
        == "uk_manual_frontier_devolved_extent_limited_repeal_out_of_scope"
    )


def test_uk_wide_si_whole_act_repeal_is_not_devolved_frontier() -> None:
    effect = _whole_act_repeal_effect(
        affecting_class="UnitedKingdomStatutoryInstrument"
    )
    rejected, rejections = _reject(effect)
    assert rejected is False
    classification = classify_uk_manual_compile_frontier(
        effect_type="repealed",
        source_pathology="",
        extracted_tag="",
        extracted_text="",
        lowering_rejections=rejections,
        compiled_op_count=0,
        replay_applicable=False,
        structural_for_replay=False,
    )
    assert (
        classification["rule_id"]
        != "uk_manual_frontier_devolved_extent_limited_repeal_out_of_scope"
    )


def test_strict_profile_not_allowed_still_blocks(monkeypatch) -> None:
    """§2.9 disposition 2: strict-profile loaded (default preset) but
    ``allows_uk_devolved_extent_repeal=False`` — block preserved."""
    monkeypatch.setenv("LAWVM_UK_STRICT_PROFILE", "uk_ingestion_v1")
    effect = _whole_act_repeal_effect(affecting_class="ScottishStatutoryInstrument")
    rejected, rejections = _reject(effect)
    assert rejected is True
    assert any(
        r.get("rule_id")
        == "uk_effect_devolved_whole_act_repeal_extent_limited_rejected"
        for r in rejections
    ), "block MUST fire when strict-not-allowed"
    assert not any(
        r.get("rule_id") == "uk_strict_profile_lifted_devolved_extent_repeal"
        for r in rejections
    ), "lift observation must NOT fire when not allowed"


def test_strict_profile_allowed_lifts_block_with_audit(monkeypatch) -> None:
    """§2.9 disposition 3: strict-profile loaded with
    ``allows_uk_devolved_extent_repeal=True`` — block LIFTED with audited
    observation. The most dangerous lift in the suite because it directly
    risks §0-forbidden over-repeal (destroying surviving E&W text).

    The §0 evidence ledger records WHO authorized + why."""
    import lawvm.uk_legislation.effect_target_prelude as mod
    from lawvm.uk_legislation.strict_profile import UK_INGESTION_V1, UkStrictProfile

    allowed_profile = UkStrictProfile(
        core_profile=UK_INGESTION_V1,
        allows_uk_devolved_extent_repeal=True,
    )
    monkeypatch.setattr(
        mod, "active_uk_strict_profile", lambda: allowed_profile
    )
    effect = _whole_act_repeal_effect(affecting_class="ScottishStatutoryInstrument")
    rejected, rejections = _reject(effect)
    assert rejected is False, "block must be LIFTED when strict-allows"
    lift = [
        r for r in rejections
        if r.get("rule_id") == "uk_strict_profile_lifted_devolved_extent_repeal"
    ]
    assert lift, "lift audit observation MUST be emitted"
    assert lift[0]["reason_code"] == (
        "strict_profile_authorized_devolved_whole_act_repeal"
    )
    assert lift[0]["strict_disposition"] == "proceed"
    assert "OVER-REPEAL" in lift[0].get("reason", "")
    assert (
        "uk_effect_devolved_whole_act_repeal_extent_limited_rejected"
        not in {r.get("rule_id") for r in rejections}
    ), "block-rejection receipt must NOT fire when lift is active"

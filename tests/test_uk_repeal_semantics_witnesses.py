from __future__ import annotations

from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.repeal_semantics_witnesses import (
    _duplicate_repeal_target_witnesses,
    is_repeal_semantics_effect,
    source_text_repeal_semantics_family,
)


def _effect(
    *,
    effect_id: str,
    effect_type: str,
    affected_provisions: str = "s. 1",
    affecting_provisions: str = "s. 2",
) -> UKEffectRecord:
    return UKEffectRecord(
        effect_id=effect_id,
        effect_type=effect_type,
        applied=True,
        requires_applied=True,
        modified="2026-01-01",
        affected_uri="https://www.legislation.gov.uk/id/ukpga/2000/1/section/1",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="1",
        affected_provisions=affected_provisions,
        affecting_uri="https://www.legislation.gov.uk/id/ukpga/2026/1",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2026",
        affecting_number="1",
        affecting_provisions=affecting_provisions,
        affecting_title="Test Act 2026",
    )


def test_repeal_semantics_effect_family_includes_repeals_revocations_and_omissions() -> None:
    assert is_repeal_semantics_effect(_effect(effect_id="e1", effect_type="repealed"))
    assert is_repeal_semantics_effect(_effect(effect_id="e2", effect_type="entry omitted"))
    assert is_repeal_semantics_effect(_effect(effect_id="e3", effect_type="revoked"))
    assert not is_repeal_semantics_effect(_effect(effect_id="e4", effect_type="inserted"))


def test_source_text_repeal_semantics_family_detects_no_revive_phrases() -> None:
    assert (
        source_text_repeal_semantics_family("The repeal of a repeal does not revive the enactment.")
        == "repeal_of_repeal_no_revive_phrase"
    )
    assert (
        source_text_repeal_semantics_family("This provision concerns repeal of the repeal made by section 2.")
        == "repeal_of_repeal_phrase"
    )
    assert (
        source_text_repeal_semantics_family("The repeal shall not revive any earlier enactment.")
        == "repeal_of_repeal_no_revive_phrase"
    )
    assert source_text_repeal_semantics_family("Section 1 is repealed.") == ""


def test_duplicate_repeal_target_witness_requires_multiple_affecting_provisions() -> None:
    witnesses = _duplicate_repeal_target_witnesses(
        "ukpga/2000/1",
        (
            _effect(effect_id="e1", effect_type="repealed", affecting_provisions="s. 2"),
            _effect(effect_id="e2", effect_type="repealed", affecting_provisions="Sch. 1"),
        ),
    )

    assert len(witnesses) == 1
    row = witnesses[0].to_dict()
    assert row["family"] == "duplicate_repeal_target_candidate"
    assert row["rule_id"] == "uk_repeal_semantics_duplicate_target_candidate"
    assert row["duplicate_count"] == 2
    assert row["related_effect_ids"] == ("e1", "e2")
    assert row["related_affecting_provisions"] == ("Sch. 1", "s. 2")


def test_duplicate_repeal_target_witness_classifies_body_schedule_double_entry() -> None:
    witnesses = _duplicate_repeal_target_witnesses(
        "ukpga/2000/1",
        (
            _effect(effect_id="e1", effect_type="repealed", affecting_provisions="s. 192(6) Sch. 13"),
            _effect(effect_id="e2", effect_type="repealed", affecting_provisions="Sch. 13"),
        ),
    )

    assert len(witnesses) == 1
    row = witnesses[0].to_dict()
    assert row["family"] == "body_schedule_repeal_double_entry_candidate"
    assert row["rule_id"] == "uk_repeal_semantics_body_schedule_double_entry_candidate"


def test_duplicate_repeal_target_witness_ignores_same_source_duplicate() -> None:
    witnesses = _duplicate_repeal_target_witnesses(
        "ukpga/2000/1",
        (
            _effect(effect_id="e1", effect_type="repealed", affecting_provisions="s. 2"),
            _effect(effect_id="e2", effect_type="repealed", affecting_provisions="s. 2"),
        ),
    )

    assert witnesses == ()

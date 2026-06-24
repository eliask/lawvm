"""UK effect-feed savings references must block whole-target repeals.

An effect that carries ``ukm:Savings`` references is legally qualified; a
whole-target repeal (``repealed``, ``omitted``, ``entry repealed``,
``entry omitted`` ...) must not be applied as an unconditional deletion when the
savings provision is an explicit schedule of the affecting instrument.  Partial
substitutions and insertions with savings references are left to their own,
more specific lowering paths.
"""
from __future__ import annotations

from typing import Any

from lawvm.uk_legislation.effect_compiler import (
    UK_EFFECT_SAVINGS_REFERENCES_QUALIFIED_REPEAL_BLOCKED_RULE_ID,
    compile_effect_to_ir_ops,
)
from lawvm.uk_legislation.effects import UKEffectRecord


def _effect(*, effect_type: str, savings_references: list[dict[str, str]]) -> UKEffectRecord:
    return UKEffectRecord(
        effect_id="e1",
        effect_type=effect_type,
        applied=True,
        requires_applied=False,
        modified="2012-08-14",
        affected_uri="/id/ukpga/1968/67/schedule/2",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="1968",
        affected_number="67",
        affected_provisions="Sch. 2",
        affecting_uri="/id/uksi/2012/1916",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2012",
        affecting_number="1916",
        affecting_provisions="Sch. 35",
        affecting_title="The Human Medicines Regulations 2012",
        in_force_dates=[{"date": "2012-08-14", "prospective": "false"}],
        savings_references=savings_references,
    )


def _rule_ids(records: list[dict[str, Any]]) -> set[str]:
    return {str(r.get("rule_id") or "") for r in records}


def test_schedule_savings_qualified_repeal_is_blocked() -> None:
    observations: list[dict[str, Any]] = []
    ops = compile_effect_to_ir_ops(
        _effect(
            effect_type="repealed",
            savings_references=[
                {
                    "ref": "schedule-32",
                    "uri": "http://www.legislation.gov.uk/id/uksi/2012/1916/schedule/32",
                    "text": "Sch. 32",
                }
            ],
        ),
        None,
        sequence=0,
        lowering_rejections_out=observations,
    )
    assert ops == []
    assert UK_EFFECT_SAVINGS_REFERENCES_QUALIFIED_REPEAL_BLOCKED_RULE_ID in _rule_ids(observations)


def test_schedule_savings_qualified_omission_is_blocked() -> None:
    """Whole-node ``omitted`` with schedule savings references lowers to a repeal action."""
    observations: list[dict[str, Any]] = []
    ops = compile_effect_to_ir_ops(
        _effect(
            effect_type="omitted",
            savings_references=[
                {
                    "ref": "schedule-32",
                    "uri": "http://www.legislation.gov.uk/id/uksi/2012/1916/schedule/32",
                    "text": "Sch. 32",
                }
            ],
        ),
        None,
        sequence=0,
        lowering_rejections_out=observations,
    )
    assert ops == []
    assert UK_EFFECT_SAVINGS_REFERENCES_QUALIFIED_REPEAL_BLOCKED_RULE_ID in _rule_ids(observations)


def test_partial_substitution_with_schedule_savings_is_not_blocked() -> None:
    """Word-level substitutions remain in text-patch lowering even with schedule savings."""
    observations: list[dict[str, Any]] = []
    compile_effect_to_ir_ops(
        _effect(
            effect_type="words substituted",
            savings_references=[
                {
                    "ref": "schedule-32",
                    "uri": "http://www.legislation.gov.uk/id/uksi/2012/1916/schedule/32",
                    "text": "Sch. 32",
                }
            ],
        ),
        None,
        sequence=0,
        lowering_rejections_out=observations,
    )
    assert UK_EFFECT_SAVINGS_REFERENCES_QUALIFIED_REPEAL_BLOCKED_RULE_ID not in _rule_ids(observations)


def test_repeal_with_non_schedule_savings_is_not_blocked() -> None:
    """Savings references to sections or regulations are ordinary savings clauses."""
    observations: list[dict[str, Any]] = []
    ops = compile_effect_to_ir_ops(
        _effect(
            effect_type="repealed",
            savings_references=[
                {
                    "ref": "section-226",
                    "uri": "http://www.legislation.gov.uk/id/ukpga/2008/29/section/226",
                    "text": "s. 226",
                }
            ],
        ),
        None,
        sequence=0,
        lowering_rejections_out=observations,
    )
    assert len(ops) == 1
    assert UK_EFFECT_SAVINGS_REFERENCES_QUALIFIED_REPEAL_BLOCKED_RULE_ID not in _rule_ids(observations)


def test_repeal_without_savings_is_not_blocked() -> None:
    observations: list[dict[str, Any]] = []
    ops = compile_effect_to_ir_ops(
        _effect(effect_type="repealed", savings_references=[]),
        None,
        sequence=0,
        lowering_rejections_out=observations,
    )
    assert len(ops) == 1
    assert UK_EFFECT_SAVINGS_REFERENCES_QUALIFIED_REPEAL_BLOCKED_RULE_ID not in _rule_ids(observations)


def test_insertion_with_schedule_savings_is_not_blocked() -> None:
    observations: list[dict[str, Any]] = []
    compile_effect_to_ir_ops(
        _effect(
            effect_type="inserted",
            savings_references=[
                {
                    "ref": "schedule-32",
                    "uri": "http://www.legislation.gov.uk/id/uksi/2012/1916/schedule/32",
                    "text": "Sch. 32",
                }
            ],
        ),
        None,
        sequence=0,
        lowering_rejections_out=observations,
    )
    assert UK_EFFECT_SAVINGS_REFERENCES_QUALIFIED_REPEAL_BLOCKED_RULE_ID not in _rule_ids(observations)

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

from lxml import etree as ET

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


def _schedule_element_with_text(text: str) -> ET._Element:
    """Build a minimal Schedule XML element with the given text payload."""
    el = ET.Element("Schedule")
    text_el = ET.SubElement(el, "Text")
    text_el.text = text
    return el


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


def test_applied_by_overlay_with_schedule_savings_does_not_reach_savings_guard() -> None:
    """§1.11 production-lane liveness pinning the §1.11+Rule B interaction.

    An ``applied by ...`` non-textual modification effect carrying schedule-
    savings references MUST NOT reach the savings-qualified-repeal block.
    The §1.11 fix reroutes non-textual-modification effect types that would
    otherwise have had ``action='repeal'`` inferred via the substring
    predicate at ``source_action_inference`` to
    ``uk_effect_applied_by_action_inference_blocked``; ``compile_effect_to_
    ir_ops`` then exits at the ``action_inference.blocked`` shortcut before
    the savings guard runs.

    Without this pin, a future regression that re-enables substring-predicate
    action inference for ``applied by ...`` types could silently route savings-
    qualified overlays back through the savings-repeal block (the §0-forbidden
    over-repeal direction by way of an unowned structural op). The savings
    rule's schedule-savings prerequisite is necessary-but-not-sufficient: the
    rule also requires ``action == 'repeal'``, which the §1.11 fix pre-empts.
    """
    schedule_text = (
        "SCHEDULE 7A TRANSITIONAL PROVISIONS Application of amendments "
        "1 In section 75 (interpretation), in subsection (2), repeal the "
        'definition of "constituent authority"; insert— "constituent '
        'authority" means the body referred to in section 4A(2)'
    )
    extracted_el = _schedule_element_with_text(schedule_text)

    observations: list[dict[str, Any]] = []
    ops = compile_effect_to_ir_ops(
        _effect(
            effect_type="applied by 2010 c. 8, Sch 7A para. 36(6) (as inserted)",
            savings_references=[
                {
                    "ref": "schedule-32",
                    "uri": "http://www.legislation.gov.uk/id/uksi/2012/1916/schedule/32",
                    "text": "Sch. 32",
                }
            ],
        ),
        extracted_el,
        sequence=0,
        lowering_rejections_out=observations,
    )
    rule_ids = _rule_ids(observations)
    assert ops == [], (
        "an §1.11-blocked action inference must not produce any lowering ops; "
        "got: {}".format(ops)
    )
    assert "uk_effect_applied_by_action_inference_blocked" in rule_ids, (
        "§1.11 fix should reroute applied-by + repeal-text effects through the "
        "action-inference block (rule_id=uk_effect_applied_by_action_inference_"
        "blocked); got rule_ids: {}".format(sorted(rule_ids))
    )
    assert (
        UK_EFFECT_SAVINGS_REFERENCES_QUALIFIED_REPEAL_BLOCKED_RULE_ID
        not in rule_ids
    ), (
        "savings-qualified-repeal block must NOT fire when action inference "
        "is blocked (compile_effect_to_ir_ops early-returns at the blocked "
        "shortcut); got rule_ids: {}".format(sorted(rule_ids))
    )

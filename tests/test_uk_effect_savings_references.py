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


def test_savings_qualified_repeal_carries_strict_block_quirks_skip_disposition() -> None:
    """§2.9 production-lane liveness: the savings-qualified repeal block MUST
    carry ``strict_disposition="block"`` / ``quirks_disposition="skip"`` plus
    the ``savings_qualification`` family tag, so a future regression that
    silently downgrades the block (e.g. ``strict_disposition="record"`` letting
    the repeal run in strict mode, or ``quirks_disposition="apply"`` letting
    it run in quirks mode) cannot re-introduce the §0-forbidden over-repeal
    direction (destroying saved state) without breaking this test.

    Drives a known-violating input (whole-target ``repealed`` carrying a
    schedule savings reference) through the production lowering lane at
    ``compile_effect_to_ir_ops`` and asserts the strict/quirks disposition
    tuple is locked alongside the ``savings_qualification`` family and the
    carried savings_references evidence.
    """
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
    # The block is the load-bearing invariant: no lowering ops are produced.
    assert ops == []
    savings_rejections = [
        r
        for r in observations
        if r.get("rule_id")
        == UK_EFFECT_SAVINGS_REFERENCES_QUALIFIED_REPEAL_BLOCKED_RULE_ID
    ]
    assert len(savings_rejections) == 1, (
        "the savings-qualified-repeal block MUST emit exactly one rejection; "
        f"got {len(savings_rejections)}"
    )
    record = savings_rejections[0]
    # Disposition tuple — the §0 over-retention-safe direction is preserved
    # iff strict blocks AND quirks skips the lowering. Either flip silently
    # re-enables over-repeal. The detail-dict keys merge into the record
    # top-level by ``_append_uk_effect_lowering_rejection``'s
    # ``payload.update(detail or {})`` — so strict_disposition lives at the
    # record root, not under a ``detail`` sub-key.
    assert record.get("strict_disposition") == "block", (
        "strict profile MUST block the savings-qualified repeal; "
        f"a downgrade to {record.get('strict_disposition')!r} would let the "
        "saved target be destroyed under strict mode (§0 forbidden direction)"
    )
    assert record.get("quirks_disposition") == "skip", (
        "quirks profile MUST skip the savings-qualified repeal (no replay "
        f"mutation); a downgrade to {record.get('quirks_disposition')!r} "
        "would let the saved target be destroyed under quirks mode"
    )
    # Family tag — specializes the prior generic ``applicability`` tag so
    # audit/projection consumers can route this residue distinctly.
    assert record.get("family") == "savings_qualification", (
        "savings-qualified repeal MUST carry the ``savings_qualification`` "
        "family tag (specialising the prior generic ``applicability`` "
        f"label); got {record.get('family')!r}"
    )
    # Reason code grounded in the §2.1 rule family's mechanism.
    assert record.get("reason_code") == "savings_references_qualify_structural_mutation"
    # Savings references are carried as evidence so a triager can answer
    # §3.2's evidence path (which schedule savings the source asserted)
    # without re-running extraction.
    carried_refs = record.get("savings_references") or []
    assert any(
        str(ref.get("ref") or "").startswith("schedule-") for ref in carried_refs
    ), "the blocking schedule savings reference MUST be carried as evidence"
    # Reaffirms the action carrier: this is a whole-target REPEAL qualified
    # by savings, not a partial substitution lowering.
    assert record.get("lowering_action") == "repeal"

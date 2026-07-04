"""UK heading-facet container-scoped full-replacement lowering.

These tests pin the explicit-heading lowerable subset:

    For the heading to Part 1 substitute "X".

The effect-feed row names a heading-only facet target (``Pt. 1 heading``);
the affecting source instruction explicitly quotes the replacement and names
the heading container. Lowering must emit a single TEXT_ALL heading-facet
text patch addressed at ``FacetKind.HEADING`` — never a body mutation.

Negative tests pin the must-stay-manual neighbours: deictic sidenote inserts
with no quoted heading anchor, and parenthetical "the title of which becomes"
notes carried inside a body-amendment instruction.
"""
from __future__ import annotations

from lxml import etree as ET

from lawvm.core.ir import LegalAddress
from lawvm.core.semantic_types import FacetKind
from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.heading_facets import (
    _heading_facet_full_replacement_fragment,
    _is_heading_facet_word_patch_supported,
)
from lawvm.uk_legislation.uk_amendment_replay import compile_effect_to_ir_ops

_LEG_NS = "http://www.legislation.gov.uk/namespaces/legislation"


def _heading_substitute_effect(
    text: str, *, provisions: str, effect_type: str = "substituted"
) -> tuple[UKEffectRecord, ET._Element]:
    extracted_el = ET.fromstring(
        f"""
        <P3 xmlns="{_LEG_NS}" id="schedule-8-paragraph-2">
          <Pnumber>2</Pnumber>
          <Text>{text}</Text>
        </P3>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_heading_to_container_substitute",
        effect_type=effect_type,
        applied=True,
        requires_applied=False,
        modified="2009-10-31",
        affected_uri="/id/ukpga/1968/20",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="1968",
        affected_number="20",
        affected_provisions=provisions,
        affecting_uri="/id/ukpga/2006/52",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2006",
        affecting_number="52",
        affecting_provisions="Sch. 8 para. 2",
        affecting_title="Armed Forces Act 2006",
        in_force_dates=[{"date": "2009-10-31", "prospective": "false"}],
    )
    return effect, extracted_el


# --------------------------------------------------------------------------
# Fragment-level recognizer assertions (witness + synthetic positives)
# --------------------------------------------------------------------------


def test_heading_to_part_substitute_fragment_recognized() -> None:
    """Witness ukpga/1968/20 (affected by ukpga/2006/52 Sch. 8)."""
    for raw, expected in [
        (
            '2 For the heading to Part 1 substitute " THE COURT MARTIAL APPEAL COURT " .',
            "THE COURT MARTIAL APPEAL COURT",
        ),
        (
            '6 For the heading to Part 2 substitute " APPEALS FROM THE COURT MARTIAL " .',
            "APPEALS FROM THE COURT MARTIAL",
        ),
        (
            "41 For the heading to Part 3 substitute "
            '" APPEAL FROM COURT MARTIAL APPEAL COURT TO SUPREME COURT " .',
            "APPEAL FROM COURT MARTIAL APPEAL COURT TO SUPREME COURT",
        ),
    ]:
        fragment = _heading_facet_full_replacement_fragment(raw)
        assert fragment is not None, raw
        assert fragment["original"] == "TEXT_ALL"
        assert fragment["replacement"] == expected
        assert fragment["rule_id"] == "uk_effect_heading_facet_full_replacement_text_patch"


def test_heading_to_chapter_and_schedule_substitute_fragment_recognized() -> None:
    """Synthetic positives: the container-scoped form generalizes past Part."""
    for raw, expected in [
        ('For the heading to Chapter 5 substitute " New Chapter Title ".', "New Chapter Title"),
        ('For the heading to the Schedule 2 substitute " New Schedule Title ".', "New Schedule Title"),
        ('For the title to section 7 substitute " New Section Title ".', "New Section Title"),
    ]:
        fragment = _heading_facet_full_replacement_fragment(raw)
        assert fragment is not None, raw
        assert fragment["replacement"] == expected


# --------------------------------------------------------------------------
# Op-level lowering: heading-only TEXT_ALL patch, no body mutation
# --------------------------------------------------------------------------


def test_compile_heading_to_part_substitute_lowers_to_heading_facet_replacement() -> None:
    effect, extracted_el = _heading_substitute_effect(
        '2 For the heading to Part 1 substitute " THE COURT MARTIAL APPEAL COURT " .',
        provisions="Pt. 1 heading",
    )
    lowering_rejections: list[dict[str, object]] = []
    ops = compile_effect_to_ir_ops(
        effect, extracted_el, sequence=0, lowering_rejections_out=lowering_rejections
    )

    assert len(ops) == 1
    op = ops[0]
    assert op.target == LegalAddress(path=(("part", "1"),), special=FacetKind.HEADING)
    assert op.text_patch is not None
    assert op.text_patch.selector.match_text == "TEXT_ALL"
    assert op.text_patch.replacement == "THE COURT MARTIAL APPEAL COURT"
    assert any(
        record["rule_id"] == "uk_effect_heading_facet_full_replacement_lowered"
        for record in lowering_rejections
    )
    # The heading-only-ref rejection must NOT fire: the source proves the facet.
    assert not any(
        record["rule_id"] == "uk_effect_heading_only_ref_rejected"
        for record in lowering_rejections
    )


# --------------------------------------------------------------------------
# Negative tests — must stay manual
# --------------------------------------------------------------------------


def test_deictic_sidenote_insert_stays_manual() -> None:
    """ukpga/1968/20: 'In the sidenote, at the end add "..."' has no quoted
    heading-substitution anchor and a deictic ('the sidenote') reference; it is
    not a full-replacement and must keep its heading-only-ref rejection."""
    text = '2 In the sidenote, at the end add " otherwise than after guilty plea " .'
    assert _heading_facet_full_replacement_fragment(text) is None

    effect, extracted_el = _heading_substitute_effect(
        text, provisions="s. 14 sidenote", effect_type="words added"
    )
    lowering_rejections: list[dict[str, object]] = []
    compile_effect_to_ir_ops(
        effect, extracted_el, sequence=0, lowering_rejections_out=lowering_rejections
    )
    # Not lowered as a full heading replacement.
    assert not any(
        record["rule_id"] == "uk_effect_heading_facet_full_replacement_lowered"
        for record in lowering_rejections
    )


def test_parenthetical_title_of_which_becomes_stays_manual() -> None:
    """ukpga/1990/8: '(the title of which becomes "X") is amended as follows'
    is a body-amendment instruction carrying a parenthetical title note; the
    heading change is inferred from a body-only source, not an explicit
    heading-facet substitution. It must stay manual."""
    text = (
        "1 Section 217 of TCPA 1990 (the title of which becomes "
        '"Appeal against a section 215 notice") is amended as follows.'
    )
    assert _heading_facet_full_replacement_fragment(text) is None
    assert _is_heading_facet_word_patch_supported("substituted", text) is False


def test_for_the_heading_without_substitute_stays_unmatched() -> None:
    """A heading reference with no 'substitute' action must not be coerced into
    a full replacement."""
    assert (
        _heading_facet_full_replacement_fragment(
            "For the heading to Part 1 of this Act, see the note below."
        )
        is None
    )


# --------------------------------------------------------------------------
# Cross-heading inserts: heading-only target must not become a body insert
# --------------------------------------------------------------------------


def test_crossheading_only_insert_without_pblock_payload_stays_residue() -> None:
    """ukpga/2003/1 + Finance Act 2013 Sch. 23 para. 4(b):
    'before section 221 insert the heading "Payments".'

    The effect feed names a cross-heading facet ('s. 221 cross-heading') and the
    source carries only inline heading text — no standalone cross-heading Pblock
    carrier. Lowering must NOT coerce the heading instruction into a body
    paragraph insert (which mis-resolves to an unrelated section and corrupts
    the tree); it must stay typed residue under a distinct rejection rule.
    """
    extracted_el = ET.fromstring(
        f"""
        <P3 xmlns="{_LEG_NS}" id="schedule-23-paragraph-4-b">
          <Pnumber>b</Pnumber>
          <Text>before section 221 insert the heading "Payments".</Text>
        </P3>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_crossheading_only_insert",
        effect_type="inserted",
        applied=True,
        requires_applied=True,
        modified="2013-07-17",
        affected_uri="/id/ukpga/2003/1",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2003",
        affected_number="1",
        affected_provisions="s. 221 cross-heading",
        affecting_uri="/id/ukpga/2013/29",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2013",
        affecting_number="29",
        affecting_provisions="Sch. 23 para. 4",
        affecting_title="Finance Act 2013",
        comments="",
        in_force_dates=[{"date": "2013-07-17", "prospective": "false"}],
    )
    lowering_rejections: list[dict[str, object]] = []
    ops = compile_effect_to_ir_ops(
        effect, extracted_el, sequence=0, lowering_rejections_out=lowering_rejections
    )

    assert ops == []
    assert any(
        record["rule_id"] == "uk_effect_crossheading_insert_rejected"
        for record in lowering_rejections
    )


def test_structural_insert_with_crossheading_carrier_still_lowers() -> None:
    """A combined 'structural provision AND its cross-heading' insert
    ('Sch. 6 para. 43A and cross-heading') carries a real Pblock/P1group payload
    and must still lower to a structural op — the cross-heading-only rejection
    must not fire on the combined form.
    """
    extracted_el = ET.fromstring(
        f"""
        <P1 xmlns="{_LEG_NS}">
          <Pnumber>1</Pnumber>
          <Text>After paragraph 43 insert-</Text>
          <BlockAmendment>
            <P1group>
              <Title>Electronic monitoring: general</Title>
              <P1 eId="schedule-6-paragraph-43A">
                <Pnumber>43A</Pnumber>
                <Text>Where a youth rehabilitation order imposes an electronic monitoring requirement.</Text>
              </P1>
            </P1group>
          </BlockAmendment>
        </P1>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_structural_plus_crossheading_insert",
        effect_type="inserted",
        applied=True,
        requires_applied=True,
        modified="2022-06-28",
        affected_uri="/id/ukpga/2020/17/schedule/6",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2020",
        affected_number="17",
        affected_provisions="Sch. 6 para. 43A and cross-heading",
        affecting_uri="/id/ukpga/2022/32",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2022",
        affecting_number="32",
        affecting_provisions="Sch. 17 para. 1",
        affecting_title="Police, Crime, Sentencing and Courts Act 2022",
        comments="",
        in_force_dates=[{"date": "2022-06-28", "prospective": "false"}],
    )
    lowering_rejections: list[dict[str, object]] = []
    ops = compile_effect_to_ir_ops(
        effect, extracted_el, sequence=0, lowering_rejections_out=lowering_rejections
    )

    assert len(ops) == 1
    assert ops[0].target == LegalAddress((("schedule", "6"), ("paragraph", "43a")), root="supplements")
    assert not any(
        record["rule_id"] == "uk_effect_crossheading_insert_rejected"
        for record in lowering_rejections
    )

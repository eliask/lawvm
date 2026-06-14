"""Regression: an SI Order's "shall be incorporated in this Order" enactments
uptake article must not be lowered to a structural mutation of the host
provisions.

ukpga/1845/20 (Railways Clauses Consolidation Act 1845) is incorporated by
reference into many railway/transport works Orders. Those Orders contain an
article reading

    "The following provisions of the [RCCA 1845] shall be incorporated in this
    Order:— section 24 (...); section 58 (...), except for the words from 'and
    if any question' to the end; section 68 (...); ..."

The effect feed surfaces these as empty-effect-type rows whose extracted source
is that incorporation list. Before this fix, the "from '...' to the end"
exception caveat false-matched the ``\\bfrom\\b.*\\bto\\b`` range-substitution
heuristic in source-action inference, so the whole incorporation prose was
lowered as a REPLACE of the host section — silently overwriting the operative
text of RCCA 1845 sections 24, 58, 68, 71, 75, 81-85, 103-105, 145 and others
(79 spurious ops across uksi/2000/2585, uksi/2001/2870, uksi/2002/1065).

Such a clause incorporates host provisions into the Order's *own* scheme; it
does not amend the host Act. It must be blocked as out-of-scope, not lowered.
"""

from __future__ import annotations

from lxml import etree as ET

from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.source_action_inference import (
    infer_uk_effect_action_from_source,
)
from lawvm.uk_legislation.source_text_reclassifications import (
    _empty_effect_type_incorporation_of_enactments_source,
)


_INCORPORATION_TEXT = (
    "The following provisions of the Railways Clauses Consolidation Act 1845 "
    "shall be incorporated in this Order— section 24 (obstructing construction "
    "of railway); section 58 (company to repair roads used by them), except for "
    "the words from “and if any question” to the end; section 68 "
    "(accommodation works by company); section 71 (additional accommodation "
    "works by owners), except for the words “or directed by such justices "
    "to be paid”."
)


def _incorporation_effect() -> UKEffectRecord:
    return UKEffectRecord(
        effect_id="uk_test_incorporation_of_enactments",
        effect_type="",
        applied=True,
        requires_applied=False,
        modified="",
        affected_uri="/id/ukpga/1845/20",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="1845",
        affected_number="20",
        affected_provisions="s. 24",
        affecting_uri="/id/uksi/2002/1065",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2002",
        affecting_number="1065",
        affecting_provisions="art. 3(1)",
        affecting_title="The Piccadilly Line (Heathrow T5 Extension) Order 2002",
        in_force_dates=[{"date": "2002-04-22", "prospective": "false"}],
    )


def _block_amendment(text: str) -> ET._Element:
    el = ET.Element("BlockAmendment")
    p = ET.SubElement(el, "Text")
    p.text = text
    return el


def test_incorporation_of_enactments_detector_matches_order_uptake_clause() -> None:
    assert _empty_effect_type_incorporation_of_enactments_source(_INCORPORATION_TEXT)
    # "construed as one with" / ordinary amendment prose must not match.
    assert not _empty_effect_type_incorporation_of_enactments_source(
        "In section 24 for the words “Five Pounds” substitute “Level 1”."
    )
    assert not _empty_effect_type_incorporation_of_enactments_source("")


def test_incorporation_of_enactments_is_blocked_not_lowered_to_replace() -> None:
    rejections: list[dict[str, object]] = []
    inference = infer_uk_effect_action_from_source(
        effect=_incorporation_effect(),
        effect_type="",
        initial_action=None,
        extracted_el=_block_amendment(_INCORPORATION_TEXT),
        extracted_text=_INCORPORATION_TEXT,
        source_root=None,
        lowering_rejections_out=rejections,
    )
    assert inference.blocked is True
    assert inference.action is None
    rule_ids = {str(r.get("rule_id")) for r in rejections}
    assert "uk_effect_incorporation_of_enactments_source_rejected" in rule_ids


def test_genuine_from_to_range_substitution_still_infers_replace() -> None:
    """Negative control: a real 'from X to Y' substitution must remain a replace."""
    text = (
        "In subsection (2) for the words from “the Minister” to "
        "“whichever is the later” substitute “the Secretary of State”."
    )
    assert not _empty_effect_type_incorporation_of_enactments_source(text)
    rejections: list[dict[str, object]] = []
    inference = infer_uk_effect_action_from_source(
        effect=_incorporation_effect(),
        effect_type="",
        initial_action=None,
        extracted_el=_block_amendment(text),
        extracted_text=text,
        source_root=None,
        lowering_rejections_out=rejections,
    )
    assert inference.blocked is False
    assert inference.action == "replace"

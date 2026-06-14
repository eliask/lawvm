"""Regression: an ``extended (<external territory>) (with modifications)`` effect
must not be lowered to a structural repeal/replace of the affected provision in
the principal UK consolidation.

``ukpga/2006/46`` (Companies Act 2006) Part 28 Ch. 1 is the subject of

    extended (Isle of Man) (with modifications)   by uksi/2008/3122 art. 2 Sch.

whose extracted Schedule reads "Modifications with which Chapter 1 of Part 28 of
the Companies Act 2006 extends to the Isle of Man ... 1 In section 948 ... a omit
subsections (4) and (5); ...". Such an effect declares a territorially-scoped
VARIANT text (the Part as it extends to the Isle of Man), not an amendment of the
principal (UK) text.

Before this fix, the effect type carried no canonical action (it is an
``extended`` overlay verb), so empty-effect-type source-action inference sniffed
the modifying Schedule body's "omit" verb and lowered the row to a structural
REPEAL of ``part:28/chapter:1`` — the forbidden §2.1 over-repeal direction,
destroying the Part in the principal consolidation.

LawVM has no extent-variant model, so the source-faithful outcome is to BLOCK the
effect to the manual-compile frontier (the M4 extent-variant axis). A PLAIN extent
extension ("extended (Isle of Man)", no "with modifications") carries no variant
body and must still lower normally.
"""

from __future__ import annotations

from lxml import etree as ET

from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.lowering_actions import (
    is_uk_territorial_extent_with_modifications_effect_type,
)
from lawvm.uk_legislation.source_action_inference import (
    infer_uk_effect_action_from_source,
)


_EXTENT_SCHEDULE_TEXT = (
    "SCHEDULE Modifications with which Chapter 1 of Part 28 of the Companies "
    "Act 2006 extends to the Isle of Man Article 2 1 In section 948 "
    "(restrictions on disclosure)— a omit subsections (4) and (5); b in "
    "subsection (7) after paragraph (a) insert— aa the Financial Supervision "
    "Commission of the Isle of Man;"
)


def _extent_effect(effect_type: str) -> UKEffectRecord:
    return UKEffectRecord(
        effect_id="uk_test_territorial_extent_with_modifications",
        effect_type=effect_type,
        applied=True,
        requires_applied=True,
        modified="",
        affected_uri="/id/ukpga/2006/46",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2006",
        affected_number="46",
        affected_provisions="Pt. 28 Ch. 1",
        affecting_uri="/id/uksi/2008/3122",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2008",
        affecting_number="3122",
        affecting_provisions="art. 2 Sch.",
        affecting_title=(
            "The Companies Act 2006 (Extension to Isle of Man) Order 2008"
        ),
        in_force_dates=[{"date": "2009-03-01", "prospective": "false"}],
    )


def _schedule(text: str) -> ET._Element:
    el = ET.Element("Schedule")
    p = ET.SubElement(el, "Text")
    p.text = text
    return el


def test_detector_matches_extent_with_modifications_only() -> None:
    assert is_uk_territorial_extent_with_modifications_effect_type(
        "extended (Isle of Man) (with modifications)"
    )
    assert is_uk_territorial_extent_with_modifications_effect_type(
        "extended (Channel Islands) (with modifications)"
    )
    assert is_uk_territorial_extent_with_modifications_effect_type(
        "extended in part (Jersey) (with modifications)"
    )
    # Plain extent extension (no modifications) must NOT match.
    assert not is_uk_territorial_extent_with_modifications_effect_type(
        "extended (Isle of Man)"
    )
    # "applied (with modifications)" is an application overlay, not an extent.
    assert not is_uk_territorial_extent_with_modifications_effect_type(
        "applied (with modifications)"
    )
    # GB-internal jurisdiction suffixes are not external-territory extents.
    assert not is_uk_territorial_extent_with_modifications_effect_type(
        "extended (s) (with modifications)"
    )
    assert not is_uk_territorial_extent_with_modifications_effect_type("")


def test_extent_with_modifications_is_blocked_not_lowered_to_repeal() -> None:
    rejections: list[dict[str, object]] = []
    inference = infer_uk_effect_action_from_source(
        effect=_extent_effect("extended (Isle of Man) (with modifications)"),
        effect_type="extended (isle of man) (with modifications)",
        initial_action=None,
        extracted_el=_schedule(_EXTENT_SCHEDULE_TEXT),
        extracted_text=_EXTENT_SCHEDULE_TEXT,
        source_root=None,
        lowering_rejections_out=rejections,
    )
    # The Part must NOT be repealed: no action, blocked to the frontier.
    assert inference.blocked is True
    assert inference.action is None
    rule_ids = {str(r.get("rule_id")) for r in rejections}
    assert (
        "uk_effect_territorial_extent_with_modifications_rejected" in rule_ids
    )
    rejection = next(
        r
        for r in rejections
        if r.get("rule_id")
        == "uk_effect_territorial_extent_with_modifications_rejected"
    )
    assert rejection.get("family") == "applicability_scope"
    assert rejection.get("blocking") is True
    assert rejection.get("extent_variant_axis") == "M4"


def test_plain_extent_extension_not_blocked_by_extent_detector() -> None:
    """Negative control: a plain "extends to the Isle of Man" with no
    modifications and a real "substituted" instruction still lowers normally;
    the extent-with-modifications detector must not fire."""
    plain_text = (
        "2 Schedule 2 to the Companies Act 2006 as substituted by the Companies "
        "Act 2006 (Amendment of Schedule 2) (No. 2) Order 2009 shall extend to "
        "the Isle of Man."
    )
    assert not is_uk_territorial_extent_with_modifications_effect_type(
        "extended (Isle of Man)"
    )
    rejections: list[dict[str, object]] = []
    inference = infer_uk_effect_action_from_source(
        effect=_extent_effect("extended (Isle of Man)"),
        effect_type="extended (isle of man)",
        initial_action=None,
        extracted_el=_schedule(plain_text),
        extracted_text=plain_text,
        source_root=None,
        lowering_rejections_out=rejections,
    )
    # Not blocked by the extent-variant detector; ordinary inference proceeds.
    assert inference.blocked is False
    assert "uk_effect_territorial_extent_with_modifications_rejected" not in {
        str(r.get("rule_id")) for r in rejections
    }

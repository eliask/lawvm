"""Payload normalization observations for UK effect lowering."""

from __future__ import annotations

from dataclasses import dataclass
import xml.etree.ElementTree as ET
from typing import Any, Callable, Optional

from lawvm.core.ir import LegalAddress
from lawvm.uk_legislation.addressing import _addr_leaf_kind
from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.lowering_records import _append_uk_effect_lowering_observation
from lawvm.uk_legislation.source_payload_helpers import (
    UK_FLAT_P1PARA_SCHEDULE_PARAGRAPH_INSERT_RULE_ID,
    _flat_p1para_schedule_paragraph_insert_payload,
    _inserted_section_p1group_heading_text,
    _prepend_inserted_section_heading_carrier,
)


@dataclass(frozen=True)
class UKFlatP1paraScheduleParagraphInsertLowering:
    content_ir: Optional[dict[str, Any]]
    lowered: bool = False


def lower_flat_p1para_schedule_paragraph_insert_payload(
    *,
    effect: UKEffectRecord,
    action: str,
    target_ref: str,
    payload_match_target: LegalAddress,
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    fallback_target_eid: Callable[[LegalAddress], str],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> UKFlatP1paraScheduleParagraphInsertLowering:
    if action != "insert" or extracted_el is None:
        return UKFlatP1paraScheduleParagraphInsertLowering(content_ir=None)
    flat_p1para_payload = _flat_p1para_schedule_paragraph_insert_payload(
        extracted_el,
        payload_match_target,
        fallback_target_eid=fallback_target_eid,
    )
    if flat_p1para_payload is None:
        return UKFlatP1paraScheduleParagraphInsertLowering(content_ir=None)
    flat_p1para_payload_detail = dict(
        flat_p1para_payload.pop("_lawvm_detail", {}) or {}
    )
    _append_uk_effect_lowering_observation(
        lowering_rejections_out,
        rule_id=UK_FLAT_P1PARA_SCHEDULE_PARAGRAPH_INSERT_RULE_ID,
        family="payload_normalization",
        reason_code="flat_blockamendment_p1para_labelled_schedule_paragraph",
        reason=(
            "UK inserted schedule paragraph source payload is a flat "
            "BlockAmendment/P1para with a direct text run beginning with "
            "the target paragraph label; lowering uses that labelled text "
            "as the paragraph payload and records sibling heading text as "
            "unresolved rather than replaying the whole instruction."
        ),
        effect=effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        detail={
            "target_ref": target_ref,
            "target": str(payload_match_target),
            **flat_p1para_payload_detail,
        },
    )
    return UKFlatP1paraScheduleParagraphInsertLowering(
        content_ir=flat_p1para_payload,
        lowered=True,
    )


def prepend_inserted_p1group_heading_carrier(
    *,
    effect: UKEffectRecord,
    target_ref: str,
    target: LegalAddress,
    content_ir: dict[str, Any],
    actual_el: ET.Element,
    extracted_el: ET.Element,
    extracted_text: Optional[str],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> bool:
    inserted_heading_text = _inserted_section_p1group_heading_text(
        actual_el,
        extracted_el,
        target,
    )
    target_leaf_kind = _addr_leaf_kind(target) or ""
    heading_source_rule_id = (
        "uk_inserted_section_p1group_heading_carrier"
        if target_leaf_kind == "section"
        else "uk_inserted_p1group_heading_carrier"
    )
    heading_observation_rule_id = (
        "uk_effect_inserted_section_p1group_heading_carrier_lowered"
        if target_leaf_kind == "section"
        else "uk_effect_inserted_p1group_heading_carrier_lowered"
    )
    if not inserted_heading_text or not _prepend_inserted_section_heading_carrier(
        content_ir,
        heading_text=inserted_heading_text,
        source_rule_id=heading_source_rule_id,
    ):
        return False
    _append_uk_effect_lowering_observation(
        lowering_rejections_out,
        rule_id=heading_observation_rule_id,
        family="payload_normalization",
        reason_code=f"inserted_{target_leaf_kind}_wrapped_by_p1group_title",
        reason=(
            "UK inserted provision payload is wrapped by a P1group "
            "Title; lowering preserves that title as a target-owned "
            "heading carrier instead of relying on a shared live parent group"
        ),
        effect=effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        detail={
            "target_ref": target_ref,
            "target": str(target),
            "source_tag": "P1group",
            "heading_text_preview": inserted_heading_text[:200],
        },
    )
    return True

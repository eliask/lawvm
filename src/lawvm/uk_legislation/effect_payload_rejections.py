"""Payload-gate rejection helpers for UK effect lowering."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any, Optional

from lawvm.core.ir import LegalAddress
from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.lowering_records import _append_uk_effect_lowering_rejection
from lawvm.uk_legislation.source_payload_elaboration import (
    _is_broad_schedule_flat_replace_payload,
    _is_non_substantive_structural_payload,
)


def reject_mixed_heading_structural_insert_missing_payload(
    *,
    effect: UKEffectRecord,
    t_str: str,
    mixed_heading_source_ref_by_target: dict[str, str],
    content_ir: Optional[dict[str, Any]],
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> bool:
    if content_ir is not None or t_str not in mixed_heading_source_ref_by_target:
        return False
    _append_uk_effect_lowering_rejection(
        lowering_rejections_out,
        rule_id="uk_effect_mixed_heading_structural_insert_payload_unresolved",
        family="source_shape_filter",
        reason_code="mixed_heading_structural_insert_payload_missing",
        reason=(
            "UK mixed structural-plus-heading insert target was "
            "normalized to its structural component, but no matching "
            "source-owned structural payload was found; lowering must "
            "not synthesize inserted body text from the heading-qualified "
            "metadata string."
        ),
        effect=effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        detail={
            "original_target_ref": mixed_heading_source_ref_by_target[t_str],
            "structural_target_ref": t_str,
        },
    )
    return True


def reject_missing_structural_payload(
    *,
    effect: UKEffectRecord,
    action: str,
    t_str: str,
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    use_metadata_fallback: bool,
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> bool:
    if not (
        extracted_el is None
        and action in ("replace", "insert")
        and not extracted_text
        and not use_metadata_fallback
    ):
        return False
    _append_uk_effect_lowering_rejection(
        lowering_rejections_out,
        rule_id="uk_effect_missing_structural_payload_rejected",
        family="source_pathology_filter",
        reason_code="missing_extracted_payload",
        reason=(
            "UK structural effect has no extracted source payload; "
            "lowering cannot emit an empty replace or insert without "
            "risking destructive replay"
        ),
        effect=effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        detail={"target_ref": t_str, "action": action},
    )
    return True


def reject_non_substantive_structural_payload(
    *,
    effect: UKEffectRecord,
    curr_action: Optional[str],
    t_str: str,
    payload_node_mut: Any,
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> bool:
    if not (curr_action in ("insert", "replace") and _is_non_substantive_structural_payload(payload_node_mut)):
        return False
    _append_uk_effect_lowering_rejection(
        lowering_rejections_out,
        rule_id="uk_effect_non_substantive_payload_rejected",
        family="source_pathology_filter",
        reason_code="non_substantive_structural_payload",
        reason=(
            "UK structural effect payload contains only numbering "
            "or dot leaders, so replaying it would create a bogus "
            "legal unit"
        ),
        effect=effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        detail={
            "target_ref": t_str,
            "action": curr_action,
            "payload_kind": str(payload_node_mut.kind) if payload_node_mut is not None else "",
        },
    )
    return True


def reject_broad_schedule_flat_replace_payload(
    *,
    effect: UKEffectRecord,
    curr_action: Optional[str],
    t_str: str,
    target: LegalAddress,
    payload_node_mut: Any,
    actual_el: Optional[ET.Element],
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> bool:
    if not (
        curr_action == "replace"
        and _is_broad_schedule_flat_replace_payload(
            target=target,
            payload_node=payload_node_mut,
            actual_source_el=actual_el,
        )
    ):
        return False
    _append_uk_effect_lowering_rejection(
        lowering_rejections_out,
        rule_id="uk_effect_broad_schedule_flat_payload_rejected",
        family="payload_coverage_filter",
        reason_code="broad_schedule_or_part_replace_payload_undercovered",
        reason=(
            "UK structural replace targets a whole schedule or schedule part, "
            "but the extracted source payload is only flat text and does not "
            "claim the target's descendant structure."
        ),
        effect=effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        detail={
            "target_ref": t_str,
            "target": str(target),
            "payload_kind": str(payload_node_mut.kind),
            "payload_label": str(payload_node_mut.label or ""),
            "payload_text_preview": " ".join((payload_node_mut.text or "").split())[:240],
        },
    )
    return True

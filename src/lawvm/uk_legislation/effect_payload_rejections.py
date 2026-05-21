"""Payload-gate rejection helpers for UK effect lowering."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any, Optional

from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.lowering_records import _append_uk_effect_lowering_rejection


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

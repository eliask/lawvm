"""Substitution-series normalization for UK effect lowering."""

from __future__ import annotations

from dataclasses import dataclass
import xml.etree.ElementTree as ET
from typing import Any, Optional

from lawvm.core.ir import LegalAddress
from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.lowering_records import _append_uk_effect_lowering_observation
from lawvm.uk_legislation.source_payload_elaboration import (
    _source_payload_matches_target_leaf,
    _substituted_series_new_sibling_insert_detail,
)


@dataclass(frozen=True)
class UKSubstitutedPayloadInsertNormalization:
    curr_action: str


def lower_substituted_payload_insert_normalization(
    *,
    effect: UKEffectRecord,
    curr_action: str,
    original_target_refs: list[str],
    target_index: int,
    target_ref: str,
    target: LegalAddress,
    content_ir: Optional[dict[str, Any]],
    source_replaced_sibling_count: Optional[int],
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> UKSubstitutedPayloadInsertNormalization:
    substituted_series_insert_detail = _substituted_series_new_sibling_insert_detail(
        effect_type=effect.effect_type,
        original_target_refs=original_target_refs,
        target_index=target_index,
        target_ref=target_ref,
        target=target,
        content_ir=content_ir,
    )
    if substituted_series_insert_detail is not None:
        _append_uk_effect_lowering_observation(
            lowering_rejections_out,
            rule_id="uk_effect_substituted_series_new_sibling_insert_lowered",
            family="lowering_normalization",
            reason_code="substituted_for_single_old_target_with_new_sibling_payload",
            reason=(
                "UK substituted-for row names one replaced target but the "
                "source-backed replacement series contains an additional "
                "sibling payload; lowering preserves the first target as "
                "replace and lowers later source-owned siblings as inserts"
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail=substituted_series_insert_detail,
        )
        return UKSubstitutedPayloadInsertNormalization(curr_action="insert")

    if (
        source_replaced_sibling_count is not None
        and target_index >= source_replaced_sibling_count
        and _source_payload_matches_target_leaf(content_ir, target)
    ):
        _append_uk_effect_lowering_observation(
            lowering_rejections_out,
            rule_id="uk_effect_substituted_range_extra_payload_sibling_insert_lowered",
            family="lowering_normalization",
            reason_code="source_substitution_payload_contains_extra_sibling",
            reason=(
                "UK source substitutes a bounded sibling range but the "
                "BlockAmendment contains additional source-owned sibling "
                "payloads beyond the replaced range; lowering keeps the "
                "range members as replacements and lowers the extra "
                "siblings as inserts."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                "target_ref": target_ref,
                "target": str(target),
                "replaced_sibling_count": source_replaced_sibling_count,
                "source_payload_kind": str(content_ir.get("kind") or "") if content_ir else "",
                "source_payload_label": str(content_ir.get("label") or "") if content_ir else "",
            },
        )
        return UKSubstitutedPayloadInsertNormalization(curr_action="insert")

    return UKSubstitutedPayloadInsertNormalization(curr_action=curr_action)

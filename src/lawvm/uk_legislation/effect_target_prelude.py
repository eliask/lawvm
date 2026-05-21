"""Target-list preprocessing for UK effect lowering."""

from __future__ import annotations

from dataclasses import dataclass
import re
import xml.etree.ElementTree as ET
from typing import Any, Optional

from lawvm.core.ir import LegalAddress
from lawvm.uk_legislation.addressing import (
    _addr_container,
    _addr_field,
    _addr_leaf_kind,
    _addr_leaf_label,
)
from lawvm.uk_legislation.canonicalize import canonicalize_uk_address
from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.heading_facets import (
    _is_heading_facet_word_patch_supported,
    _is_heading_only_ref,
    _is_schedule_note_ref,
    _mixed_heading_structural_insert_ref,
)
from lawvm.uk_legislation.lowering_records import (
    _append_uk_effect_lowering_observation,
    _append_uk_effect_lowering_rejection,
)
from lawvm.uk_legislation.source_context import _first_amendment_container
from lawvm.uk_legislation.source_payload_elaboration import _expand_sibling_targets_from_extracted
from lawvm.uk_legislation.substitution_metadata import (
    _expand_sibling_targets_from_text,
    _source_text_schedule_paragraph_target_override,
)
from lawvm.uk_legislation.xml_helpers import _tag


_UK_ENACTED_SCHEDULE_TABLE_ROW_PART_TARGET_RULE_ID = (
    "uk_effect_enacted_schedule_table_row_part_target_refined"
)
_UK_SOURCE_TEXT_SCHEDULE_PARAGRAPH_TARGET_OVERRIDE_RULE_ID = (
    "uk_effect_source_text_schedule_paragraph_target_overrides_metadata"
)


@dataclass(frozen=True)
class UKTargetPrelude:
    targets_str: list[str]
    mixed_heading_source_ref_by_target: dict[str, str]


def expand_single_target_prelude(
    *,
    effect: UKEffectRecord,
    action: str,
    targets_str: list[str],
    original_targets_str: list[str],
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> UKTargetPrelude:
    targets = list(targets_str)
    mixed_heading_source_ref_by_target: dict[str, str] = {}
    if len(targets) != 1:
        return UKTargetPrelude(
            targets_str=targets,
            mixed_heading_source_ref_by_target=mixed_heading_source_ref_by_target,
        )

    mixed_heading_structural_ref = _mixed_heading_structural_insert_ref(
        targets[0],
        action=action,
    )
    expansion_source_el = extracted_el
    expansion_ref = targets[0]
    if mixed_heading_structural_ref:
        expansion_ref = mixed_heading_structural_ref
        amendment_container = _first_amendment_container(extracted_el)
        expansion_source_el = amendment_container if amendment_container is not None else extracted_el
    else:
        amendment_container = _first_amendment_container(extracted_el)
        if amendment_container is not None:
            expansion_source_el = amendment_container

    expanded_targets = _expand_sibling_targets_from_extracted(expansion_ref, expansion_source_el)
    if not expanded_targets:
        expanded_targets = _expand_sibling_targets_from_text(expansion_ref, extracted_text)
    if expanded_targets:
        targets = expanded_targets
        if mixed_heading_structural_ref:
            mixed_heading_source_ref_by_target = {
                target_ref: original_targets_str[0] for target_ref in expanded_targets
            }
        else:
            _append_uk_effect_lowering_observation(
                lowering_rejections_out,
                rule_id="uk_effect_source_payload_sibling_range_expanded",
                family="target_shape_normalization",
                reason_code="source_payload_children_expand_compressed_sibling_range",
                reason=(
                    "UK effect metadata compressed a sibling target range, "
                    "while the extracted BlockAmendment contains one direct "
                    "payload child for each sibling; lowering expands the "
                    "targets to those source-owned children."
                ),
                effect=effect,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                detail={
                    "original_target_ref": original_targets_str[0],
                    "expanded_targets": list(expanded_targets),
                    "source_container": _tag(expansion_source_el) if expansion_source_el is not None else "",
                },
            )
    elif mixed_heading_structural_ref and len(re.findall(r"\([0-9A-Z]+\)", mixed_heading_structural_ref, re.I)) == 1:
        targets = [mixed_heading_structural_ref]
        mixed_heading_source_ref_by_target = {
            mixed_heading_structural_ref: original_targets_str[0],
        }

    if mixed_heading_structural_ref and mixed_heading_source_ref_by_target:
        _append_uk_effect_lowering_observation(
            lowering_rejections_out,
            rule_id="uk_effect_mixed_heading_structural_insert_target_normalized",
            family="target_shape_normalization",
            reason_code="mixed_heading_structural_insert_target_split",
            reason=(
                "UK effect target combines inserted structural provisions "
                "with a heading facet; lowering removes the heading suffix "
                "only for source-owned structural insert targets and keeps "
                "the heading facet unresolved."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                "original_target_ref": original_targets_str[0],
                "structural_targets": list(targets),
                "heading_facet_status": "unresolved",
            },
        )

    return UKTargetPrelude(
        targets_str=targets,
        mixed_heading_source_ref_by_target=mixed_heading_source_ref_by_target,
    )


def reject_unsupported_target_facet(
    *,
    effect: UKEffectRecord,
    t_str: str,
    target_candidate_count: int,
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> bool:
    if _is_schedule_note_ref(t_str):
        _append_uk_effect_lowering_rejection(
            lowering_rejections_out,
            rule_id="uk_effect_schedule_note_target_rejected",
            family="unsupported_target_facet",
            reason_code="schedule_note_target_unsupported",
            reason=(
                "UK effect target names a schedule note; lowering must "
                "not coerce that note into paragraph/subparagraph structure."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={"target_ref": t_str, "target_candidate_count": target_candidate_count},
        )
        return True

    if _is_heading_only_ref(t_str) and not _is_heading_facet_word_patch_supported(
        effect.effect_type,
        extracted_text,
    ):
        _append_uk_effect_lowering_rejection(
            lowering_rejections_out,
            rule_id="uk_effect_heading_only_ref_rejected",
            family="unsupported_target_facet",
            reason_code="heading_only_ref_unsupported",
            reason=(
                "UK effect target names only a heading or sidenote facet; "
                "lowering cannot safely mutate the host provision body"
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={"target_ref": t_str, "target_candidate_count": target_candidate_count},
        )
        return True

    return False


def refine_enacted_schedule_table_row_part_target(
    *,
    effect: UKEffectRecord,
    action: str,
    t_str: str,
    target: LegalAddress,
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> LegalAddress:
    source_schedule_table_row_part_label = (
        str(extracted_el.get("source_part_label") or "")
        if extracted_el is not None
        and str(extracted_el.get("source_rule_id") or "")
        == "uk_affecting_act_enacted_schedule_table_row_source_extracted"
        else ""
    )
    if not (
        action == "insert"
        and source_schedule_table_row_part_label
        and _addr_container(target) == "schedule"
        and _addr_field(target, "part") is None
        and _addr_leaf_kind(target) == "paragraph"
    ):
        return target

    schedule_label = _addr_field(target, "schedule") or ""
    paragraph_label = _addr_leaf_label(target) or ""
    refined_target = canonicalize_uk_address(
        LegalAddress(
            path=(
                ("schedule", schedule_label),
                ("part", source_schedule_table_row_part_label),
                ("paragraph", paragraph_label),
            )
        )
    )
    _append_uk_effect_lowering_observation(
        lowering_rejections_out,
        rule_id=_UK_ENACTED_SCHEDULE_TABLE_ROW_PART_TARGET_RULE_ID,
        family="target_resolution_recovery",
        reason_code="source_enacted_schedule_table_row_part_context",
        reason=(
            "UK enacted affecting source exposed the added schedule "
            "paragraph as a unique row under a schedule Part; lowering "
            "refines the metadata paragraph target to that source-owned "
            "Part instead of inserting under the schedule root."
        ),
        effect=effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        detail={
            "target_ref": t_str,
            "metadata_target": str(target),
            "refined_target": str(refined_target),
            "source_part_label": source_schedule_table_row_part_label,
            "source_rule_id": str(extracted_el.get("source_rule_id") or "") if extracted_el is not None else "",
            "source_row_text": str(extracted_el.get("source_row_text") or "") if extracted_el is not None else "",
        },
    )
    return refined_target


def refine_source_text_schedule_paragraph_target(
    *,
    effect: UKEffectRecord,
    action: str,
    is_word_level: bool,
    t_str: str,
    target: LegalAddress,
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> LegalAddress:
    source_text_target_override = (
        _source_text_schedule_paragraph_target_override(
            extracted_text=extracted_text,
            target=target,
        )
        if is_word_level and action == "replace"
        else None
    )
    if source_text_target_override is None:
        return target

    refined_target = canonicalize_uk_address(source_text_target_override)
    _append_uk_effect_lowering_observation(
        lowering_rejections_out,
        rule_id=_UK_SOURCE_TEXT_SCHEDULE_PARAGRAPH_TARGET_OVERRIDE_RULE_ID,
        family="target_resolution_recovery",
        reason_code="explicit_source_schedule_paragraph_overrides_metadata",
        reason=(
            "UK source text explicitly names a different paragraph in "
            "the same schedule than the effect metadata; lowering uses "
            "the source-named target and records the metadata target as "
            "overridden evidence."
        ),
        effect=effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        detail={
            "target_ref": t_str,
            "metadata_target": str(target),
            "source_target": str(refined_target),
        },
    )
    return refined_target

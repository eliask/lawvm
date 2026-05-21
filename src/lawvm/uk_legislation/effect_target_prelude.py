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
    _is_direct_section_paragraph_ref,
    _is_schedule_part_abbreviation_ref,
    _is_schedule_note_ref,
    _mixed_heading_structural_insert_ref,
)
from lawvm.uk_legislation.lowering_records import (
    _append_uk_effect_lowering_observation,
    _append_uk_effect_lowering_rejection,
)
from lawvm.uk_legislation.source_context import _first_amendment_container
from lawvm.uk_legislation.source_payload_helpers import (
    UK_NONADDRESSABLE_SCHEDULE_PART_INSERT_TARGET_RULE_ID as _UK_NONADDRESSABLE_SCHEDULE_PART_INSERT_TARGET_RULE_ID,
    _flat_p1para_schedule_paragraph_insert_payload,
)
from lawvm.uk_legislation.schedule_list_selectors import _uk_numbered_schedule_entry_repeal_target
from lawvm.uk_legislation.source_payload_elaboration import _expand_sibling_targets_from_extracted
from lawvm.uk_legislation.substitution_metadata import (
    UKSourceLabelChangingSubstitution,
    _expand_sibling_targets_from_text,
    _source_text_schedule_paragraph_target_override,
)
from lawvm.uk_legislation.target_anchors import _fallback_target_eid
from lawvm.uk_legislation.target_parser import _parse_affected_target, _schedule_part_context_removed_target
from lawvm.uk_legislation.xml_helpers import _tag


_UK_ENACTED_SCHEDULE_TABLE_ROW_PART_TARGET_RULE_ID = (
    "uk_effect_enacted_schedule_table_row_part_target_refined"
)
_UK_SOURCE_TEXT_SCHEDULE_PARAGRAPH_TARGET_OVERRIDE_RULE_ID = (
    "uk_effect_source_text_schedule_paragraph_target_overrides_metadata"
)
_UK_NUMBERED_SCHEDULE_ENTRY_REPEAL_TARGET_REFINED_RULE_ID = (
    "uk_effect_numbered_schedule_entry_repeal_target_refined"
)


@dataclass(frozen=True)
class UKTargetPrelude:
    targets_str: list[str]
    mixed_heading_source_ref_by_target: dict[str, str]


@dataclass(frozen=True)
class UKPerTargetContext:
    heading_facet_target: bool
    target: LegalAddress
    payload_match_target: LegalAddress
    label_changing_substitution: Optional[UKSourceLabelChangingSubstitution]
    target_replacement_leaf_override: Optional[str]
    target_replacement_leaf_kind: Optional[str]


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


def reject_schedule_entry_missing_source(
    *,
    effect: UKEffectRecord,
    effect_type: str,
    action: str,
    t_str: str,
    target: LegalAddress,
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> bool:
    if not (
        extracted_el is None
        and effect_type in {"entry inserted", "entry repealed", "entry omitted"}
    ):
        return False
    _append_uk_effect_lowering_rejection(
        lowering_rejections_out,
        rule_id="uk_effect_schedule_entry_missing_source_rejected",
        family="source_schedule_list_entry_elaboration",
        reason_code="entry_effect_requires_source_text",
        reason=(
            "UK schedule-entry effect row requires affecting source text; "
            "metadata alone does not identify the entry payload or entry "
            "anchor safely enough for replay."
        ),
        effect=effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        detail={"target_ref": t_str, "target": str(target), "action": action},
    )
    return True


def resolve_effect_target_context(
    *,
    effect: UKEffectRecord,
    action: str,
    is_word_level: bool,
    t_str: str,
    target_index: int,
    label_changing_substitutions: tuple[UKSourceLabelChangingSubstitution, ...],
    replacement_leaf_override: Optional[str],
    replacement_leaf_kind: Optional[str],
    source_parent_substitution_range_payload: Optional[dict[str, Any]],
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> UKPerTargetContext:
    heading_facet_target = _is_heading_only_ref(t_str)
    parsed_target = _parse_affected_target(t_str)
    target = parsed_target if _is_direct_section_paragraph_ref(t_str) else canonicalize_uk_address(parsed_target)
    target = refine_enacted_schedule_table_row_part_target(
        effect=effect,
        action=action,
        t_str=t_str,
        target=target,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        lowering_rejections_out=lowering_rejections_out,
    )
    label_changing_substitution = next(
        (
            substitution
            for substitution in label_changing_substitutions
            if tuple(target.path) == tuple(substitution.source_target.path)
        ),
        None,
    )
    target_replacement_leaf_override = replacement_leaf_override
    target_replacement_leaf_kind = replacement_leaf_kind
    if label_changing_substitution is not None:
        target_replacement_leaf_override = _addr_leaf_label(label_changing_substitution.replacement_target)
        target_replacement_leaf_kind = _addr_leaf_kind(label_changing_substitution.replacement_target)
    target = refine_source_text_schedule_paragraph_target(
        effect=effect,
        action=action,
        is_word_level=is_word_level,
        t_str=t_str,
        target=target,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        lowering_rejections_out=lowering_rejections_out,
    )
    target = refine_flat_p1para_schedule_insert_target(
        effect=effect,
        action=action,
        t_str=t_str,
        target=target,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        lowering_rejections_out=lowering_rejections_out,
    )
    payload_match_target = target
    if label_changing_substitution is not None:
        payload_match_target = label_changing_substitution.replacement_target
    elif source_parent_substitution_range_payload is not None and target_index == 0:
        payload_match_target = LegalAddress(
            path=(
                *target.path[:-1],
                ("item", str(source_parent_substitution_range_payload["payload_label"])),
            )
        )
    return UKPerTargetContext(
        heading_facet_target=heading_facet_target,
        target=target,
        payload_match_target=payload_match_target,
        label_changing_substitution=label_changing_substitution,
        target_replacement_leaf_override=target_replacement_leaf_override,
        target_replacement_leaf_kind=target_replacement_leaf_kind,
    )


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


def refine_flat_p1para_schedule_insert_target(
    *,
    effect: UKEffectRecord,
    action: str,
    t_str: str,
    target: LegalAddress,
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> LegalAddress:
    if action != "insert":
        return target
    flat_p1para_probe = _flat_p1para_schedule_paragraph_insert_payload(
        extracted_el,
        target,
        fallback_target_eid=_fallback_target_eid,
    )
    if flat_p1para_probe is None or _addr_field(target, "part") is None:
        return target
    stripped_target = _schedule_part_context_removed_target(target)
    if stripped_target is None:
        return target

    refined_target = canonicalize_uk_address(stripped_target)
    _append_uk_effect_lowering_observation(
        lowering_rejections_out,
        rule_id=_UK_NONADDRESSABLE_SCHEDULE_PART_INSERT_TARGET_RULE_ID,
        family="target_resolution_recovery",
        reason_code="flat_insert_payload_uses_nonaddressable_schedule_part_context",
        reason=(
            "UK source names a schedule Part as insertion context, "
            "but the source-owned BlockAmendment payload is a direct "
            "labelled schedule paragraph with no Part wrapper; lowering "
            "records the Part as context and targets the replay-addressable "
            "schedule paragraph."
        ),
        effect=effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        detail={
            "target_ref": t_str,
            "metadata_target": str(target),
            "normalized_target": str(refined_target),
            "removed_part_label": _addr_field(target, "part") or "",
        },
    )
    return refined_target


def append_target_shape_observations(
    *,
    effect: UKEffectRecord,
    t_str: str,
    target: LegalAddress,
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> None:
    if _is_direct_section_paragraph_ref(t_str):
        _append_uk_effect_lowering_observation(
            lowering_rejections_out,
            rule_id="uk_effect_direct_section_paragraph_target_normalized",
            family="target_shape_normalization",
            reason_code="explicit_section_paragraph_ref",
            reason=(
                "UK affected-provision reference uses section-number plus "
                "an alphabetic bracket, which denotes a direct section "
                "paragraph rather than an alphabetic subsection."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={"target_ref": t_str, "target": str(target)},
        )
    if _is_schedule_part_abbreviation_ref(t_str) and any(kind == "part" for kind, _label in target.path):
        _append_uk_effect_lowering_observation(
            lowering_rejections_out,
            rule_id="uk_effect_schedule_part_abbreviation_target_normalized",
            family="target_shape_normalization",
            reason_code="explicit_schedule_part_abbreviation_ref",
            reason=(
                "UK affected-provision reference uses a schedule Part abbreviation; "
                "lowering preserves it as an explicit schedule part target rather "
                "than treating the abbreviation as a paragraph label."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={"target_ref": t_str, "target": str(target)},
        )


def refine_numbered_schedule_entry_repeal_target(
    *,
    effect: UKEffectRecord,
    action: str,
    t_str: str,
    target: LegalAddress,
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> LegalAddress:
    if action != "repeal":
        return target
    refined_target = _uk_numbered_schedule_entry_repeal_target(
        target=target,
        extracted_text=extracted_text,
    )
    if refined_target is None:
        return target

    _append_uk_effect_lowering_observation(
        lowering_rejections_out,
        rule_id=_UK_NUMBERED_SCHEDULE_ENTRY_REPEAL_TARGET_REFINED_RULE_ID,
        family="source_schedule_list_entry_elaboration",
        reason_code="explicit_numbered_entry_child",
        reason=(
            "UK source text claims omission/repeal of a numbered "
            "entry under a schedule partition; lowering refines "
            "the partition carrier target to the explicit numbered "
            "paragraph instead of deleting the carrier."
        ),
        effect=effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        detail={
            "target_ref": t_str,
            "original_target": str(target),
            "refined_target": str(refined_target),
        },
    )
    return refined_target

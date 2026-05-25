"""Single-target lowering orchestration for UK effect rows."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Optional, Protocol, Sequence

from lawvm.core.ir import LegalAddress, LegalOperation
from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.effect_crossheading_prelude import (
    append_crossheading_group_repeal_observation,
    build_crossheading_compound_heading_op,
    build_crossheading_context,
    refine_crossheading_or_heading_facet_target,
    reject_unsupported_crossheading_replace,
)
from lawvm.uk_legislation.effect_payload_normalization import (
    extract_uk_structural_payload_ir,
)
from lawvm.uk_legislation.effect_payload_rejections import (
    reject_missing_structural_payload,
    reject_mixed_heading_structural_insert_missing_payload,
)
from lawvm.uk_legislation.effect_schedule_lowering import (
    lower_source_range_definition_list_end_schedule_entries,
    try_lower_schedule_list_entry_mutation,
    try_lower_schedule_table_end_rows_insert,
)
from lawvm.uk_legislation.effect_substitution_normalization import (
    lower_substituted_payload_insert_normalization,
)
from lawvm.uk_legislation.effect_operation_finalization import (
    UKFinalizeTargetOperationInput,
    finalize_uk_target_operation,
)
from lawvm.uk_legislation.effect_table_lowering import (
    prepare_table_cell_text_patch_context,
    try_lower_repeal_table_effect,
    try_lower_table_column_insert,
    try_lower_table_row_insert,
)
from lawvm.uk_legislation.effect_target_prelude import (
    append_target_shape_observations,
    refine_numbered_schedule_entry_repeal_target,
    reject_external_or_partial_whole_act_scope,
    reject_schedule_entry_missing_source,
    reject_structural_pseudo_definition_target,
    reject_unsupported_target_facet,
    resolve_effect_target_context,
)
from lawvm.uk_legislation.effect_text_fragment_lowering import lower_uk_text_fragment_rewrite
from lawvm.uk_legislation.heading_facets import (
    _CROSSHEADING_BEFORE_ANCHOR_REPLACEMENT_RULE,
    _is_heading_only_ref,
)
from lawvm.uk_legislation.lowering_records import _append_uk_effect_lowering_observation
from lawvm.uk_legislation.source_amendment_program_fragments import (
    _fragment_substitution_amendment_program_inserted_parent_child_insert,
    reject_amendment_program_inserted_parent_structural_insert,
)
from lawvm.uk_legislation.source_definition_fragments import (
    lower_metadata_pseudo_definition_entry_range_insertions,
    lower_metadata_pseudo_definition_child_substitution,
    lower_source_carried_definition_child_at_end_insert,
    lower_source_carried_definition_child_text_omission,
)
from lawvm.uk_legislation.source_payload_helpers import infer_source_payload_from_target
from lawvm.uk_legislation.source_structural_sibling import lower_source_structural_sibling_insert
from lawvm.uk_legislation.source_text_reclassifications import (
    reclassify_word_level_structural_subsection_omission,
)
from lawvm.uk_legislation.substitution_metadata import UKSourceLabelChangingSubstitution
from lawvm.uk_legislation.target_anchors import _fallback_target_eid
from lawvm.uk_legislation.witnesses import UKEffectWitness, UKProvisionExtractionWitness


class _SingleLoweringResult(Protocol):
    handled: bool
    op: Optional[LegalOperation]


class _BatchLoweringResult(Protocol):
    handled: bool
    ops: Sequence[LegalOperation]


def _append_handled_lowering_op(
    ops: list[LegalOperation],
    result: _SingleLoweringResult,
) -> bool:
    if not result.handled:
        return False
    if result.op is not None:
        ops.append(result.op)
    return True


def _extend_handled_lowering_ops(
    ops: list[LegalOperation],
    result: _BatchLoweringResult,
) -> bool:
    if not result.handled:
        return False
    ops.extend(result.ops)
    return True


@dataclass(frozen=True)
class _ChainedInsertAnchorState:
    preceding_eid: Optional[str] = None
    preceding_eid_source: str = "effect_comments_after_clause"


@dataclass(frozen=True)
class _EffectTargetLoweringInput:
    effect: UKEffectRecord
    effect_type: str
    action: str
    is_word_level: bool
    target_index: int
    target_ref: str
    targets_str: list[str]
    original_targets_str: list[str]
    label_changing_substitutions: tuple[UKSourceLabelChangingSubstitution, ...]
    replacement_leaf_override: Optional[str]
    replacement_leaf_kind: Optional[str]
    source_parent_substitution_range_payload: Optional[dict[str, Any]]
    source_parent_at_end_added_payload: Optional[dict[str, Any]]
    source_replaced_sibling_count: Optional[int]
    mixed_heading_source_ref_by_target: dict[str, str]
    use_metadata_fallback: bool
    allow_payload_identity_synthesis: bool
    sequence: int
    existing_ops_count: int
    effect_witness: UKEffectWitness
    extraction_witness: UKProvisionExtractionWitness
    extracted_el: Optional[ET.Element]
    extracted_text: Optional[str]
    source_root: Optional[ET.Element]
    chained_insert_anchor: _ChainedInsertAnchorState
    lowering_rejections_out: Optional[list[dict[str, Any]]]


@dataclass(frozen=True)
class _EffectTargetLoweringResult:
    ops: list[LegalOperation]
    chained_insert_anchor: _ChainedInsertAnchorState
    unlowered_overlap_target: str = ""
    unlowered_overlap_reason: str = ""


def _lower_effect_target(ctx: _EffectTargetLoweringInput) -> _EffectTargetLoweringResult:
    effect = ctx.effect
    effect_type = ctx.effect_type
    action = ctx.action
    t_str = ctx.target_ref
    extracted_el = ctx.extracted_el
    extracted_text = ctx.extracted_text
    lowering_rejections_out = ctx.lowering_rejections_out
    target_ops: list[LegalOperation] = []
    unchanged = _EffectTargetLoweringResult(
        ops=target_ops,
        chained_insert_anchor=ctx.chained_insert_anchor,
    )

    if reject_unsupported_target_facet(
        effect=effect,
        t_str=t_str,
        target_candidate_count=len(ctx.targets_str),
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        lowering_rejections_out=lowering_rejections_out,
    ):
        return unchanged
    target_context = resolve_effect_target_context(
        effect=effect,
        action=action,
        is_word_level=ctx.is_word_level,
        t_str=t_str,
        target_index=ctx.target_index,
        label_changing_substitutions=ctx.label_changing_substitutions,
        replacement_leaf_override=ctx.replacement_leaf_override,
        replacement_leaf_kind=ctx.replacement_leaf_kind,
        source_parent_substitution_range_payload=ctx.source_parent_substitution_range_payload,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        lowering_rejections_out=lowering_rejections_out,
    )
    heading_facet_target = target_context.heading_facet_target
    target = target_context.target
    payload_match_target = target_context.payload_match_target
    label_changing_substitution = target_context.label_changing_substitution
    target_replacement_leaf_override = target_context.target_replacement_leaf_override
    target_replacement_leaf_kind = target_context.target_replacement_leaf_kind
    flat_p1para_schedule_insert_lowered = False
    append_target_shape_observations(
        effect=effect,
        t_str=t_str,
        target=target,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        lowering_rejections_out=lowering_rejections_out,
    )
    target = refine_numbered_schedule_entry_repeal_target(
        effect=effect,
        action=action,
        t_str=t_str,
        target=target,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        lowering_rejections_out=lowering_rejections_out,
    )
    metadata_pseudo_definition_child = lower_metadata_pseudo_definition_child_substitution(
        effect=effect,
        action=action,
        target=target,
        target_ref=t_str,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        lowering_rejections_out=lowering_rejections_out,
    )
    if metadata_pseudo_definition_child is not None:
        target = metadata_pseudo_definition_child.target
        payload_match_target = target
    metadata_pseudo_definition_range = lower_metadata_pseudo_definition_entry_range_insertions(
        effect=effect,
        action=action,
        target=target,
        target_ref=t_str,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        lowering_rejections_out=lowering_rejections_out,
    )
    if metadata_pseudo_definition_range is not None:
        target = metadata_pseudo_definition_range.target
        payload_match_target = target
        source_range_definition_list_end = lower_source_range_definition_list_end_schedule_entries(
            effect=effect,
            metadata_pseudo_definition_range=metadata_pseudo_definition_range,
            sequence=ctx.sequence,
            effect_witness=ctx.effect_witness,
            extraction_witness=ctx.extraction_witness,
            original_targets_str=ctx.original_targets_str,
            t_str=t_str,
        )
        _extend_handled_lowering_ops(target_ops, source_range_definition_list_end)
    crossheading_context = build_crossheading_context(
        effect=effect,
        action=action,
        t_str=t_str,
        target=target,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
    )
    crossheading_replacement_text = crossheading_context.replacement_text
    crossheading_replacement_rule_id = crossheading_context.replacement_rule_id
    crossheading_text_patch_fragment = crossheading_context.text_patch_fragment
    crossheading_compound_heading_text = crossheading_context.compound_heading_text
    crossheading_group_repeal_selector = crossheading_context.group_repeal_selector
    if reject_unsupported_crossheading_replace(
        effect=effect,
        action=action,
        t_str=t_str,
        context=crossheading_context,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        lowering_rejections_out=lowering_rejections_out,
    ):
        return unchanged
    if reject_schedule_entry_missing_source(
        effect=effect,
        effect_type=effect_type,
        action=action,
        t_str=t_str,
        target=target,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        lowering_rejections_out=lowering_rejections_out,
    ):
        return unchanged
    if metadata_pseudo_definition_range is None and reject_structural_pseudo_definition_target(
        effect=effect,
        action=action,
        t_str=t_str,
        target=target,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        lowering_rejections_out=lowering_rejections_out,
    ):
        return unchanged
    if crossheading_compound_heading_text is not None:
        target_ops.append(
            build_crossheading_compound_heading_op(
                effect=effect,
                t_str=t_str,
                target=target,
                replacement_text=crossheading_compound_heading_text,
                sequence=ctx.sequence,
                effect_witness=ctx.effect_witness,
                extraction_witness=ctx.extraction_witness,
                original_targets_str=ctx.original_targets_str,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                lowering_rejections_out=lowering_rejections_out,
            )
        )
    schedule_table_end_rows = try_lower_schedule_table_end_rows_insert(
        effect=effect,
        action=action,
        t_str=t_str,
        target=target,
        heading_facet_target=heading_facet_target,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        sequence=ctx.sequence,
        effect_witness=ctx.effect_witness,
        extraction_witness=ctx.extraction_witness,
        original_targets_str=ctx.original_targets_str,
        lowering_rejections_out=lowering_rejections_out,
    )
    if _append_handled_lowering_op(target_ops, schedule_table_end_rows):
        return unchanged
    schedule_list_entry = try_lower_schedule_list_entry_mutation(
        effect=effect,
        action=action,
        effect_type=effect_type,
        t_str=t_str,
        target=target,
        heading_facet_target=heading_facet_target,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        source_root=ctx.source_root,
        sequence=ctx.sequence,
        effect_witness=ctx.effect_witness,
        extraction_witness=ctx.extraction_witness,
        original_targets_str=ctx.original_targets_str,
        lowering_rejections_out=lowering_rejections_out,
    )
    if _append_handled_lowering_op(target_ops, schedule_list_entry):
        return unchanged
    table_column_insert = try_lower_table_column_insert(
        effect=effect,
        action=action,
        t_str=t_str,
        target=target,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        sequence=ctx.sequence,
        effect_witness=ctx.effect_witness,
        extraction_witness=ctx.extraction_witness,
        original_targets_str=ctx.original_targets_str,
        lowering_rejections_out=lowering_rejections_out,
    )
    if _append_handled_lowering_op(target_ops, table_column_insert):
        return unchanged
    table_row_insert = try_lower_table_row_insert(
        effect=effect,
        action=action,
        t_str=t_str,
        target=target,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        source_root=ctx.source_root,
        sequence=ctx.sequence,
        effect_witness=ctx.effect_witness,
        extraction_witness=ctx.extraction_witness,
        original_targets_str=ctx.original_targets_str,
        lowering_rejections_out=lowering_rejections_out,
    )
    if _append_handled_lowering_op(target_ops, table_row_insert):
        return unchanged
    repeal_table_effect = try_lower_repeal_table_effect(
        effect=effect,
        t_str=t_str,
        target=target,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        source_root=ctx.source_root,
        sequence=ctx.sequence,
        effect_witness=ctx.effect_witness,
        extraction_witness=ctx.extraction_witness,
        original_targets_str=ctx.original_targets_str,
        lowering_rejections_out=lowering_rejections_out,
    )
    if _extend_handled_lowering_ops(target_ops, repeal_table_effect):
        return unchanged
    table_cell_context = prepare_table_cell_text_patch_context(
        effect=effect,
        t_str=t_str,
        target=target,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        source_root=ctx.source_root,
        lowering_rejections_out=lowering_rejections_out,
    )
    table_cell_selector = table_cell_context.table_cell_selector
    selector_rule_id = table_cell_context.selector_rule_id
    source_carried_table_entry_paragraph_substitution = (
        table_cell_context.source_carried_table_entry_paragraph_substitution
    )
    target = table_cell_context.target
    if table_cell_context.handled:
        return unchanged
    target = refine_crossheading_or_heading_facet_target(
        effect=effect,
        t_str=t_str,
        target=target,
        heading_facet_target=heading_facet_target,
        crossheading_replacement_text=crossheading_replacement_text,
        crossheading_replacement_observation_rule_id=(
            crossheading_context.replacement_observation_rule_id
        ),
        crossheading_replacement_reason_code=crossheading_context.replacement_reason_code,
        crossheading_replacement_reason=crossheading_context.replacement_reason,
        crossheading_text_patch_fragment=crossheading_text_patch_fragment,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        lowering_rejections_out=lowering_rejections_out,
    )
    if reject_external_or_partial_whole_act_scope(
        effect=effect,
        action=action,
        effect_type=effect_type,
        t_str=t_str,
        target=target,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        lowering_rejections_out=lowering_rejections_out,
    ):
        return unchanged
    structural_payload = extract_uk_structural_payload_ir(
        effect=effect,
        action=action,
        target_ref=t_str,
        target=target,
        payload_match_target=payload_match_target,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        fallback_target_eid=_fallback_target_eid,
        lowering_rejections_out=lowering_rejections_out,
    )
    content_ir = structural_payload.content_ir
    actual_el = structural_payload.actual_el
    flat_p1para_schedule_insert_lowered = (
        structural_payload.flat_p1para_schedule_insert_lowered
    )
    source_structural_payload_matches_target = (
        structural_payload.source_structural_payload_matches_target
    )
    if (
        action == "insert"
        and content_ir is not None
        and str(content_ir.get("kind") or "").lower() in {"schedule", "irnodekind.schedule"}
        and len(target.path) > 1
        and str(target.path[0][0] or "").lower() == "schedule"
    ):
        original_target = target
        target = LegalAddress(path=target.path[:1], special=None)
        payload_match_target = target
        _append_uk_effect_lowering_observation(
            lowering_rejections_out,
            rule_id="uk_effect_source_schedule_parent_payload_retargeted",
            family="payload_normalization",
            reason_code="inserted_schedule_parent_payload_claims_feed_descendant",
            reason=(
                "UK source payload contains an explicit Schedule wrapper while "
                "the effect-feed target points at a descendant; lowering targets "
                "the source-claimed schedule shell instead of replaying the "
                "descendant at an unsafe fallback location."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                "target_ref": t_str,
                "original_target": str(original_target),
                "target": str(target),
            },
        )

    if reject_mixed_heading_structural_insert_missing_payload(
        effect=effect,
        t_str=t_str,
        mixed_heading_source_ref_by_target=ctx.mixed_heading_source_ref_by_target,
        content_ir=content_ir,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        lowering_rejections_out=lowering_rejections_out,
    ):
        return unchanged

    if content_ir is None:
        content_ir = infer_source_payload_from_target(
            target=target,
            extracted_text=extracted_text,
            effect_id=effect.effect_id,
            use_metadata_fallback=(
                ctx.use_metadata_fallback and not _is_heading_only_ref(t_str)
            ),
        )

    if reject_missing_structural_payload(
        effect=effect,
        action=action,
        t_str=t_str,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        use_metadata_fallback=ctx.use_metadata_fallback,
        lowering_rejections_out=lowering_rejections_out,
    ):
        return unchanged

    curr_action = action
    fragment_subs: Optional[list] = None
    # Text-level fields (populated for text_replace / text_repeal ops)
    op_text_match: Optional[str] = None
    op_text_replacement: Optional[str] = None
    op_text_occurrence: int = 0
    op_text_end_occurrence: int = 0
    if crossheading_group_repeal_selector is not None:
        curr_action = "repeal"
        content_ir = None
        append_crossheading_group_repeal_observation(
            effect=effect,
            crossheading_group_repeal_selector=crossheading_group_repeal_selector,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            lowering_rejections_out=lowering_rejections_out,
        )
    elif crossheading_replacement_text is not None:
        curr_action = "text_replace"
        content_ir = None
        op_text_match = "TEXT_ALL"
        op_text_replacement = crossheading_replacement_text
        fragment_subs = [
            {
                "original": "TEXT_ALL",
                "replacement": crossheading_replacement_text,
                "rule_id": (
                    crossheading_replacement_rule_id
                    or _CROSSHEADING_BEFORE_ANCHOR_REPLACEMENT_RULE
                ),
            }
        ]
    elif crossheading_text_patch_fragment is not None:
        curr_action = "text_replace"
        content_ir = None
        fragment_subs = [crossheading_text_patch_fragment]
        op_text_match = crossheading_text_patch_fragment["original"]
        op_text_replacement = crossheading_text_patch_fragment["replacement"]
    elif metadata_pseudo_definition_child is not None:
        curr_action = "text_replace"
        content_ir = None
        fragment_subs = [metadata_pseudo_definition_child.fragment]
        op_text_match = metadata_pseudo_definition_child.op_text_match
        op_text_replacement = metadata_pseudo_definition_child.op_text_replacement
    elif metadata_pseudo_definition_range is not None:
        if metadata_pseudo_definition_range.fragments:
            curr_action = "text_replace"
            content_ir = None
            fragment_subs = list(metadata_pseudo_definition_range.fragments)
            op_text_match = fragment_subs[0]["original"]
            op_text_replacement = fragment_subs[0]["replacement"]
        else:
            curr_action = ""
            content_ir = None
    substitution_insert_normalization = lower_substituted_payload_insert_normalization(
        effect=effect,
        curr_action=curr_action,
        original_target_refs=ctx.original_targets_str,
        target_index=ctx.target_index,
        target_ref=t_str,
        target=target,
        content_ir=content_ir,
        source_replaced_sibling_count=ctx.source_replaced_sibling_count,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        lowering_rejections_out=lowering_rejections_out,
    )
    curr_action = substitution_insert_normalization.curr_action

    structural_sibling_insert = lower_source_structural_sibling_insert(
        effect=effect,
        effect_type=effect_type,
        curr_action=curr_action,
        target=target,
        content_ir=content_ir,
        target_ref=t_str,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        lowering_rejections_out=lowering_rejections_out,
    )
    target = structural_sibling_insert.target
    content_ir = structural_sibling_insert.content_ir
    structural_sibling_insert_detail = structural_sibling_insert.detail

    amendment_program_inserted_parent_child_insert = (
        _fragment_substitution_amendment_program_inserted_parent_child_insert(
            extracted_text=extracted_text,
            target=target,
        )
    )
    if amendment_program_inserted_parent_child_insert is None and reject_amendment_program_inserted_parent_structural_insert(
        effect=effect,
        curr_action=curr_action,
        target=target,
        target_ref=t_str,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        lowering_rejections_out=lowering_rejections_out,
    ):
        return unchanged

    structural_omission_reclassification = reclassify_word_level_structural_subsection_omission(
        effect=effect,
        curr_action=curr_action,
        content_ir=content_ir,
        target=target,
        target_ref=t_str,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        lowering_rejections_out=lowering_rejections_out,
    )
    curr_action = structural_omission_reclassification.curr_action
    content_ir = structural_omission_reclassification.content_ir

    definition_child_text_omission_lowering = lower_source_carried_definition_child_text_omission(
        effect=effect,
        curr_action=curr_action,
        content_ir=content_ir,
        fragment_subs=fragment_subs,
        op_text_match=op_text_match,
        op_text_replacement=op_text_replacement,
        target=target,
        target_ref=t_str,
        extracted_el=extracted_el,
        source_root=ctx.source_root,
        extracted_text=extracted_text,
        lowering_rejections_out=lowering_rejections_out,
    )
    target = definition_child_text_omission_lowering.target
    curr_action = definition_child_text_omission_lowering.curr_action
    content_ir = definition_child_text_omission_lowering.content_ir
    fragment_subs = definition_child_text_omission_lowering.fragment_subs
    op_text_match = definition_child_text_omission_lowering.op_text_match
    op_text_replacement = definition_child_text_omission_lowering.op_text_replacement

    definition_child_at_end_insert_lowering = lower_source_carried_definition_child_at_end_insert(
        effect=effect,
        curr_action=curr_action,
        content_ir=content_ir,
        fragment_subs=fragment_subs,
        op_text_match=op_text_match,
        op_text_replacement=op_text_replacement,
        target=target,
        target_ref=t_str,
        extracted_el=extracted_el,
        source_root=ctx.source_root,
        extracted_text=extracted_text,
        lowering_rejections_out=lowering_rejections_out,
    )
    target = definition_child_at_end_insert_lowering.target
    curr_action = definition_child_at_end_insert_lowering.curr_action
    content_ir = definition_child_at_end_insert_lowering.content_ir
    fragment_subs = definition_child_at_end_insert_lowering.fragment_subs
    op_text_match = definition_child_at_end_insert_lowering.op_text_match
    op_text_replacement = definition_child_at_end_insert_lowering.op_text_replacement

    text_fragment_lowering = lower_uk_text_fragment_rewrite(
        effect=effect,
        effect_type=effect_type,
        curr_action=curr_action,
        content_ir=content_ir,
        fragment_subs=fragment_subs,
        op_text_match=op_text_match,
        op_text_replacement=op_text_replacement,
        op_text_occurrence=op_text_occurrence,
        op_text_end_occurrence=op_text_end_occurrence,
        target=target,
        target_ref=t_str,
        targets_str=ctx.targets_str,
        is_word_level=ctx.is_word_level,
        heading_facet_target=heading_facet_target,
        source_structural_payload_matches_target=source_structural_payload_matches_target,
        source_carried_table_entry_paragraph_substitution=(
            source_carried_table_entry_paragraph_substitution
        ),
        table_cell_selector=table_cell_selector,
        selector_rule_id=selector_rule_id,
        structural_sibling_insert_detail=structural_sibling_insert_detail,
        extracted_el=extracted_el,
        source_root=ctx.source_root,
        extracted_text=extracted_text,
        lowering_rejections_out=lowering_rejections_out,
    )
    if text_fragment_lowering.skip_effect:
        return unchanged
    target = text_fragment_lowering.target
    curr_action = text_fragment_lowering.curr_action
    content_ir = text_fragment_lowering.content_ir
    fragment_subs = text_fragment_lowering.fragment_subs
    op_text_match = text_fragment_lowering.op_text_match
    op_text_replacement = text_fragment_lowering.op_text_replacement
    op_text_occurrence = text_fragment_lowering.op_text_occurrence
    op_text_end_occurrence = text_fragment_lowering.op_text_end_occurrence
    unlowered_overlap_reason = text_fragment_lowering.unlowered_overlap_reason

    if not curr_action:
        return _EffectTargetLoweringResult(
            ops=target_ops,
            chained_insert_anchor=ctx.chained_insert_anchor,
            unlowered_overlap_target=t_str if unlowered_overlap_reason else "",
            unlowered_overlap_reason=unlowered_overlap_reason,
        )
    finalization = finalize_uk_target_operation(
        UKFinalizeTargetOperationInput(
            effect=effect,
            existing_ops_count=ctx.existing_ops_count + len(target_ops),
            sequence=ctx.sequence,
            curr_action=curr_action,
            content_ir=content_ir,
            target=target,
            payload_match_target=payload_match_target,
            target_replacement_leaf_override=target_replacement_leaf_override,
            target_replacement_leaf_kind=target_replacement_leaf_kind,
            actual_el=actual_el,
            target_ref=t_str,
            original_targets_str=ctx.original_targets_str,
            targets_str=ctx.targets_str,
            fragment_subs=fragment_subs,
            op_text_match=op_text_match,
            op_text_replacement=op_text_replacement,
            op_text_occurrence=op_text_occurrence,
            op_text_end_occurrence=op_text_end_occurrence,
            chained_insert_preceding_eid=ctx.chained_insert_anchor.preceding_eid,
            chained_insert_preceding_eid_source=(
                ctx.chained_insert_anchor.preceding_eid_source
            ),
            effect_witness=ctx.effect_witness,
            extraction_witness=ctx.extraction_witness,
            table_cell_selector=table_cell_selector,
            crossheading_group_repeal_selector=crossheading_group_repeal_selector,
            label_changing_substitution=label_changing_substitution,
            flat_p1para_schedule_insert_lowered=flat_p1para_schedule_insert_lowered,
            source_parent_substitution_range_payload=ctx.source_parent_substitution_range_payload,
            source_parent_at_end_added_payload=ctx.source_parent_at_end_added_payload,
            target_index=ctx.target_index,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            allow_payload_identity_synthesis=ctx.allow_payload_identity_synthesis,
            lowering_rejections_out=lowering_rejections_out,
        )
    )
    if finalization.skip_effect:
        return _EffectTargetLoweringResult(
            ops=target_ops,
            chained_insert_anchor=ctx.chained_insert_anchor,
            unlowered_overlap_target=t_str if unlowered_overlap_reason else "",
            unlowered_overlap_reason=unlowered_overlap_reason,
        )
    target_ops.extend(finalization.ops)
    chained_insert_anchor = _ChainedInsertAnchorState(
        preceding_eid=finalization.chained_insert_preceding_eid,
        preceding_eid_source=finalization.chained_insert_preceding_eid_source,
    )
    return _EffectTargetLoweringResult(
        ops=target_ops,
        chained_insert_anchor=chained_insert_anchor,
        unlowered_overlap_target=t_str if unlowered_overlap_reason else "",
        unlowered_overlap_reason=unlowered_overlap_reason,
    )

"""Final operation assembly for a lowered UK effect target."""

from __future__ import annotations

from lxml import etree as ET
from dataclasses import dataclass
from typing import Any, Optional

from lawvm.core.ir import LegalAddress, LegalOperation
from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.effect_lowering_tail import (
    append_chained_insertion_anchor_observation,
    resolve_uk_insertion_anchor_context,
)
from lawvm.uk_legislation.effect_operation_builder import (
    build_lowered_operations_for_text_patches,
)
from lawvm.uk_legislation.effect_payload_normalization import (
    prepare_uk_operation_payload_node,
)
from lawvm.uk_legislation.substitution_metadata import UKSourceLabelChangingSubstitution
from lawvm.uk_legislation.target_anchors import _target_anchor_eid
from lawvm.uk_legislation.text_patch_lowering import build_uk_text_patch_items
from lawvm.uk_legislation.witnesses import UKEffectWitness, UKProvisionExtractionWitness


@dataclass(frozen=True)
class UKFinalizedTargetOperation:
    ops: list[LegalOperation]
    chained_insert_preceding_eid: Optional[str]
    chained_insert_preceding_eid_source: str
    skip_effect: bool = False


@dataclass(frozen=True)
class UKFinalizeTargetOperationInput:
    effect: UKEffectRecord
    existing_ops_count: int
    sequence: int
    curr_action: str
    content_ir: Optional[dict[str, Any]]
    target: LegalAddress
    payload_match_target: LegalAddress
    target_replacement_leaf_override: Optional[str]
    target_replacement_leaf_kind: Optional[str]
    actual_el: Optional[ET._Element]
    target_ref: str
    original_targets_str: list[str]
    targets_str: list[str]
    fragment_subs: Optional[list[dict[str, Any]]]
    op_text_match: Optional[str]
    op_text_replacement: Optional[str]
    op_text_occurrence: int
    op_text_end_occurrence: int
    chained_insert_preceding_eid: Optional[str]
    chained_insert_preceding_eid_source: str
    effect_witness: UKEffectWitness
    extraction_witness: UKProvisionExtractionWitness
    table_cell_selector: Optional[dict[str, Any]]
    crossheading_group_repeal_selector: Optional[dict[str, Any]]
    label_changing_substitution: Optional[UKSourceLabelChangingSubstitution]
    flat_p1para_schedule_insert_lowered: bool
    source_parent_substitution_range_payload: Optional[dict[str, Any]]
    source_parent_at_end_added_payload: Optional[dict[str, Any]]
    substituted_payload_insert_rule_id: Optional[str]
    target_index: int
    extracted_el: Optional[ET._Element]
    extracted_text: Optional[str]
    allow_payload_identity_synthesis: bool
    lowering_rejections_out: Optional[list[dict[str, Any]]]


def finalize_uk_target_operation(
    ctx: UKFinalizeTargetOperationInput,
) -> UKFinalizedTargetOperation:
    anchor_context = resolve_uk_insertion_anchor_context(
        effect=ctx.effect,
        curr_action=ctx.curr_action,
        target=ctx.target,
        chained_insert_preceding_eid=ctx.chained_insert_preceding_eid,
        chained_insert_preceding_eid_source=ctx.chained_insert_preceding_eid_source,
        extracted_el=ctx.extracted_el,
        extracted_text=ctx.extracted_text,
    )
    preceding_eid = anchor_context.preceding_eid
    preceding_eid_source = anchor_context.preceding_eid_source
    following_eid = anchor_context.following_eid
    following_eid_source = anchor_context.following_eid_source

    payload_preparation = prepare_uk_operation_payload_node(
        effect=ctx.effect,
        curr_action=ctx.curr_action,
        content_ir=ctx.content_ir,
        target_ref=ctx.target_ref,
        target=ctx.target,
        payload_match_target=ctx.payload_match_target,
        target_replacement_leaf_override=ctx.target_replacement_leaf_override,
        target_replacement_leaf_kind=ctx.target_replacement_leaf_kind,
        actual_el=ctx.actual_el,
        extracted_el=ctx.extracted_el,
        extracted_text=ctx.extracted_text,
        allow_payload_identity_synthesis=ctx.allow_payload_identity_synthesis,
        lowering_rejections_out=ctx.lowering_rejections_out,
    )
    if payload_preparation.skip_effect:
        return UKFinalizedTargetOperation(
            ops=[],
            chained_insert_preceding_eid=ctx.chained_insert_preceding_eid,
            chained_insert_preceding_eid_source=ctx.chained_insert_preceding_eid_source,
            skip_effect=True,
        )
    payload_node = payload_preparation.payload_node
    text_patch_items = build_uk_text_patch_items(
        curr_action=ctx.curr_action,
        fragment_subs=ctx.fragment_subs,
        op_text_match=ctx.op_text_match,
        op_text_replacement=ctx.op_text_replacement,
        op_text_occurrence=ctx.op_text_occurrence,
        op_text_end_occurrence=ctx.op_text_end_occurrence,
    )

    append_chained_insertion_anchor_observation(
        ctx.lowering_rejections_out,
        effect=ctx.effect,
        target_ref=ctx.target_ref,
        target=ctx.target,
        preceding_eid=preceding_eid,
        preceding_eid_source=preceding_eid_source,
        used_chained_insert_anchor=anchor_context.used_chained_insert_anchor,
        extracted_el=ctx.extracted_el,
        extracted_text=ctx.extracted_text,
    )
    ops = build_lowered_operations_for_text_patches(
        effect=ctx.effect,
        existing_ops_count=ctx.existing_ops_count,
        sequence=ctx.sequence,
        curr_action=ctx.curr_action,
        target=ctx.target,
        payload_node=payload_node,
        target_ref=ctx.target_ref,
        original_targets_str=ctx.original_targets_str,
        targets_str=ctx.targets_str,
        text_patch_items=text_patch_items,
        op_text_match=ctx.op_text_match,
        op_text_replacement=ctx.op_text_replacement,
        op_text_occurrence=ctx.op_text_occurrence,
        op_text_end_occurrence=ctx.op_text_end_occurrence,
        preceding_eid=preceding_eid,
        preceding_eid_source=preceding_eid_source,
        following_eid=following_eid,
        following_eid_source=following_eid_source,
        effect_witness=ctx.effect_witness,
        extraction_witness=ctx.extraction_witness,
        table_cell_selector=ctx.table_cell_selector,
        crossheading_group_repeal_selector=ctx.crossheading_group_repeal_selector,
        label_changing_substitution=ctx.label_changing_substitution,
        flat_p1para_schedule_insert_lowered=ctx.flat_p1para_schedule_insert_lowered,
        source_parent_substitution_range_payload=ctx.source_parent_substitution_range_payload,
        source_parent_at_end_added_payload=ctx.source_parent_at_end_added_payload,
        substituted_payload_insert_rule_id=ctx.substituted_payload_insert_rule_id,
        target_index=ctx.target_index,
    )
    if ctx.curr_action == "insert" and preceding_eid:
        target_anchor_eid = _target_anchor_eid(ctx.target)
        if target_anchor_eid:
            return UKFinalizedTargetOperation(
                ops=ops,
                chained_insert_preceding_eid=target_anchor_eid,
                chained_insert_preceding_eid_source="prior_insert_in_same_effect",
            )
    return UKFinalizedTargetOperation(
        ops=ops,
        chained_insert_preceding_eid=None,
        chained_insert_preceding_eid_source="effect_comments_after_clause",
    )

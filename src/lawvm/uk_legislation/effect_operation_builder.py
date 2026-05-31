"""Shared lowered-operation assembly helpers for UK effects."""

from __future__ import annotations

import json
from typing import Any, NamedTuple, Optional

from lawvm.core.ir import IRNode, LegalAddress, LegalOperation, OperationSource
from lawvm.uk_legislation.heading_facets import _CROSSHEADING_AND_STRUCTURAL_REPEAL_RULE
from lawvm.uk_legislation.lowering_actions import _to_structural_action
from lawvm.uk_legislation.provenance_notes import (
    NOTE_CROSSHEADING_GROUP_REPEAL_SELECTOR as _NOTE_CROSSHEADING_GROUP_REPEAL_SELECTOR,
    NOTE_SOURCE_LABEL_CHANGE_SUBSTITUTION as _NOTE_SOURCE_LABEL_CHANGE_SUBSTITUTION,
    NOTE_TABLE_CELL_SELECTOR as _NOTE_TABLE_CELL_SELECTOR,
)
from lawvm.uk_legislation.source_parent_payloads import (
    UK_SOURCE_PARENT_AT_END_ADDED_PAYLOAD_RULE_ID as _UK_SOURCE_PARENT_AT_END_ADDED_PAYLOAD_RULE_ID,
    UK_SOURCE_PARENT_SUBSTITUTION_RANGE_PAYLOAD_RULE_ID as _UK_SOURCE_PARENT_SUBSTITUTION_RANGE_PAYLOAD_RULE_ID,
)
from lawvm.uk_legislation.source_payload_helpers import (
    UK_FLAT_P1PARA_SCHEDULE_PARAGRAPH_INSERT_RULE_ID as _UK_FLAT_P1PARA_SCHEDULE_PARAGRAPH_INSERT_RULE_ID,
)
from lawvm.uk_legislation.substitution_metadata import (
    UK_SOURCE_LABEL_CHANGING_SUBSTITUTION_RULE_ID as _UK_SOURCE_LABEL_CHANGING_SUBSTITUTION_RULE_ID,
    UKSourceLabelChangingSubstitution,
)
from lawvm.uk_legislation.witness_builders import (
    _uk_insertion_anchor_witness,
    _uk_target_expansion_witness,
    _uk_temporal_group_id,
    _uk_text_rewrite_spec,
)
from lawvm.uk_legislation.witness_sidecars import _uk_lowered_op_provenance_tags
from lawvm.uk_legislation.witness_sidecars import _payload_with_rewrite_witness
from lawvm.uk_legislation.witnesses import (
    UKEffectWitness,
    UKLoweredOperationWitness,
    UKProvisionExtractionWitness,
)
from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.text_patch_lowering import UKTextPatchItem
from lawvm.uk_legislation.whole_act_text_patch import (
    UK_SIMPLE_WHOLE_ACT_ALL_OCCURRENCES_SUBSTITUTION_RULE_ID,
)


class UKLoweredOperationProvenance(NamedTuple):
    provenance_tags: tuple[str, ...]
    witness_rule_id: Optional[str]


def build_lowered_operation_provenance(
    *,
    lowered_witness: UKLoweredOperationWitness,
    table_cell_selector: Optional[dict[str, Any]],
    crossheading_group_repeal_selector: Optional[dict[str, Any]],
    curr_action: str,
    target: LegalAddress,
    label_changing_substitution: Optional[UKSourceLabelChangingSubstitution],
    flat_p1para_schedule_insert_lowered: bool,
    source_parent_substitution_range_payload: Optional[dict[str, Any]],
    source_parent_at_end_added_payload: Optional[dict[str, Any]],
    substituted_payload_insert_rule_id: Optional[str],
    target_index: int,
) -> UKLoweredOperationProvenance:
    provenance_tags = _uk_lowered_op_provenance_tags(lowered_witness)
    if table_cell_selector is not None:
        provenance_tags = (
            *provenance_tags,
            f"{_NOTE_TABLE_CELL_SELECTOR}{json.dumps(table_cell_selector, ensure_ascii=False)}",
        )
    op_witness_rule_id = None
    if crossheading_group_repeal_selector is not None and curr_action == "repeal":
        op_witness_rule_id = _CROSSHEADING_AND_STRUCTURAL_REPEAL_RULE
        provenance_tags = (
            *provenance_tags,
            (
                f"{_NOTE_CROSSHEADING_GROUP_REPEAL_SELECTOR}"
                f"{json.dumps(crossheading_group_repeal_selector, ensure_ascii=False)}"
            ),
        )
    if (
        label_changing_substitution is not None
        and curr_action == "replace"
        and tuple(target.path) == tuple(label_changing_substitution.source_target.path)
    ):
        op_witness_rule_id = _UK_SOURCE_LABEL_CHANGING_SUBSTITUTION_RULE_ID
        label_change_note = {
            "rule_id": _UK_SOURCE_LABEL_CHANGING_SUBSTITUTION_RULE_ID,
            "source_target": str(label_changing_substitution.source_target),
            "replacement_target": str(label_changing_substitution.replacement_target),
            "source_ref": label_changing_substitution.source_ref,
            "replacement_ref": label_changing_substitution.replacement_ref,
        }
        provenance_tags = (
            *provenance_tags,
            (
                f"{_NOTE_SOURCE_LABEL_CHANGE_SUBSTITUTION}"
                f"{json.dumps(label_change_note, ensure_ascii=False)}"
            ),
        )
    if flat_p1para_schedule_insert_lowered and curr_action == "insert":
        op_witness_rule_id = _UK_FLAT_P1PARA_SCHEDULE_PARAGRAPH_INSERT_RULE_ID
    if (
        source_parent_substitution_range_payload is not None
        and curr_action == "replace"
        and target_index < len(source_parent_substitution_range_payload["payload_labels"])
    ):
        op_witness_rule_id = _UK_SOURCE_PARENT_SUBSTITUTION_RANGE_PAYLOAD_RULE_ID
    if source_parent_at_end_added_payload is not None and curr_action == "insert":
        op_witness_rule_id = _UK_SOURCE_PARENT_AT_END_ADDED_PAYLOAD_RULE_ID
    if substituted_payload_insert_rule_id is not None and curr_action == "insert":
        op_witness_rule_id = substituted_payload_insert_rule_id
    text_rewrite_witness = lowered_witness.text_rewrite_witness
    if (
        str(target.special or "") == "whole_act"
        and curr_action == "text_replace"
        and text_rewrite_witness is not None
        and text_rewrite_witness.rewrite_source
        in {
            "uk_effect_all_occurrences_substitution_text_patch",
            "uk_effect_wherever_they_occur_substitution_text_patch",
        }
    ):
        op_witness_rule_id = UK_SIMPLE_WHOLE_ACT_ALL_OCCURRENCES_SUBSTITUTION_RULE_ID
    return UKLoweredOperationProvenance(
        provenance_tags=provenance_tags,
        witness_rule_id=op_witness_rule_id,
    )


def build_lowered_operations_for_text_patches(
    *,
    effect: UKEffectRecord,
    existing_ops_count: int,
    sequence: int,
    curr_action: str,
    target: LegalAddress,
    payload_node: Optional[IRNode],
    target_ref: str,
    original_targets_str: list[str],
    targets_str: list[str],
    text_patch_items: list[UKTextPatchItem],
    op_text_match: Optional[str],
    op_text_replacement: Optional[str],
    op_text_occurrence: int,
    op_text_end_occurrence: int,
    preceding_eid: Optional[str],
    preceding_eid_source: str,
    following_eid: Optional[str],
    following_eid_source: Optional[str],
    effect_witness: UKEffectWitness,
    extraction_witness: UKProvisionExtractionWitness,
    table_cell_selector: Optional[dict[str, Any]],
    crossheading_group_repeal_selector: Optional[dict[str, Any]],
    label_changing_substitution: Optional[UKSourceLabelChangingSubstitution],
    flat_p1para_schedule_insert_lowered: bool,
    source_parent_substitution_range_payload: Optional[dict[str, Any]],
    source_parent_at_end_added_payload: Optional[dict[str, Any]],
    substituted_payload_insert_rule_id: Optional[str],
    target_index: int,
) -> list[LegalOperation]:
    src = OperationSource(
        statute_id=effect.affecting_act_id,
        title=effect.affecting_title,
        effective=effect_witness.applicability.effective_date or "",
        raw_text=extraction_witness.extracted_text,
    )
    target_expansion_witness = _uk_target_expansion_witness(
        target_ref,
        [target_ref],
        original_targets_str=original_targets_str,
    )
    insertion_anchor_witness = _uk_insertion_anchor_witness(
        preceding_eid,
        following_eid=following_eid,
        anchor_source=following_eid_source or preceding_eid_source,
    )

    lowered_ops: list[LegalOperation] = []
    for text_patch_item in text_patch_items:
        text_rewrite_witness = _uk_text_rewrite_spec(
            fragment_subs=text_patch_item.witness_fragments,
            text_patch=text_patch_item.text_patch,
            op_text_match=op_text_match,
            op_text_replacement=op_text_replacement,
            op_text_occurrence=op_text_occurrence,
            op_text_end_occurrence=op_text_end_occurrence,
        )
        op_count = existing_ops_count + len(lowered_ops)
        lowered_witness = UKLoweredOperationWitness(
            op_id=(
                f"{effect.effect_id}_{op_count}"
                if len(targets_str) > 1 or len(text_patch_items) > 1
                else effect.effect_id
            ),
            sequence=sequence,
            action=_to_structural_action(curr_action),
            target=target,
            payload=payload_node,
            source=src,
            effect_witness=effect_witness,
            extraction_witness=extraction_witness,
            target_expansion_witness=target_expansion_witness,
            text_rewrite_witness=text_rewrite_witness,
            insertion_anchor_witness=insertion_anchor_witness,
        )
        operation_provenance = build_lowered_operation_provenance(
            lowered_witness=lowered_witness,
            table_cell_selector=table_cell_selector,
            crossheading_group_repeal_selector=crossheading_group_repeal_selector,
            curr_action=curr_action,
            target=target,
            label_changing_substitution=label_changing_substitution,
            flat_p1para_schedule_insert_lowered=flat_p1para_schedule_insert_lowered,
            source_parent_substitution_range_payload=source_parent_substitution_range_payload,
            source_parent_at_end_added_payload=source_parent_at_end_added_payload,
            substituted_payload_insert_rule_id=substituted_payload_insert_rule_id,
            target_index=target_index,
        )
        lowered_ops.append(
            LegalOperation(
                op_id=lowered_witness.op_id,
                sequence=lowered_witness.sequence,
                action=lowered_witness.action,
                target=lowered_witness.target,
                payload=_payload_with_rewrite_witness(
                    lowered_witness.payload,
                    lowered_witness,
                ),
                source=lowered_witness.source,
                group_id=_uk_temporal_group_id(effect),
                provenance_tags=operation_provenance.provenance_tags,
                text_patch=text_patch_item.text_patch,
                witness_rule_id=operation_provenance.witness_rule_id,
            )
        )
    return lowered_ops

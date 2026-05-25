"""Table-specific special lowering for UK effects."""

from __future__ import annotations

from dataclasses import dataclass, replace as dc_replace
import json
import xml.etree.ElementTree as ET
from typing import Any, Optional

from lawvm.core.ir import IRNode, LegalAddress, LegalOperation, OperationSource, TextPatchSpec, TextSelector
from lawvm.core.semantic_types import IRNodeKind, StructuralAction, TextPatchKindEnum
from lawvm.uk_legislation.addressing import _addr_leaf_kind
from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.lowering_records import (
    _append_uk_effect_lowering_observation,
    _append_uk_effect_lowering_rejection,
)
from lawvm.uk_legislation.provenance_notes import (
    NOTE_TABLE_COLUMN_INSERT_SELECTOR as _NOTE_TABLE_COLUMN_INSERT_SELECTOR,
    NOTE_TABLE_ROW_INSERT_SELECTOR as _NOTE_TABLE_ROW_INSERT_SELECTOR,
)
from lawvm.uk_legislation.source_table_entry_paragraph import (
    _source_carried_table_entry_paragraph_substitution,
)
from lawvm.uk_legislation.table_selectors import (
    UK_EMBEDDED_TABLE_STRUCTURAL_INSERTION_RULE_ID as _UK_EMBEDDED_TABLE_STRUCTURAL_INSERTION_RULE_ID,
    UK_EMBEDDED_TABLE_STRUCTURAL_SUBSTITUTION_RULE_ID as _UK_EMBEDDED_TABLE_STRUCTURAL_SUBSTITUTION_RULE_ID,
    UK_TABLE_COLUMN_INSERT_RULE_ID as _UK_TABLE_COLUMN_INSERT_RULE_ID,
    UK_TABLE_ENTRY_INLINE_TEXT_RULE_ID as _UK_TABLE_ENTRY_INLINE_TEXT_RULE_ID,
    UK_TABLE_ENTRY_INSTRUCTION_REJECTED_RULE_ID as _UK_TABLE_ENTRY_INSTRUCTION_REJECTED_RULE_ID,
    UK_TABLE_ENTRY_ROW_INSERT_RULE_ID as _UK_TABLE_ENTRY_ROW_INSERT_RULE_ID,
    _uk_broad_table_entry_instruction,
    _uk_embedded_table_payload_structural_insertion,
    _uk_embedded_table_payload_structural_substitution,
    _uk_parent_target_before_table_marker,
    _uk_schedule_list_entry_table_payload,
    _uk_single_logical_table_entry_group_payload,
    _uk_single_table_column_payload,
    _uk_single_table_row_payload,
    _uk_table_column_insert_selector,
    _uk_table_column_text_patch_selector,
    _uk_table_entry_inline_text_selector,
    _uk_table_entry_row_insert_selector,
)
from lawvm.uk_legislation.table_sources import (
    _UK_REPEAL_TABLE_DEFINITION_CHILD_TEXT_REPEAL_RULE_ID,
    _UK_REPEAL_TABLE_DEFINITION_ENTRY_TEXT_REPEAL_RULE_ID,
    _UK_REPEAL_TABLE_QUOTED_WORDS_TEXT_REPEAL_RULE_ID,
    _UK_REPEAL_TABLE_SENTENCE_TEXT_REPEAL_RULE_ID,
    _UK_REPEAL_TABLE_STRUCTURAL_REPEAL_RULE_ID,
    _uk_table_driven_repeal_table_quoted_words_text_repeal,
    _uk_table_driven_repeal_table_structural_repeal,
)
from lawvm.uk_legislation.witness_builders import (
    _uk_target_expansion_witness,
    _uk_temporal_group_id,
    _uk_text_rewrite_spec,
)
from lawvm.uk_legislation.witness_sidecars import (
    _payload_with_rewrite_witness,
    _uk_lowered_op_provenance_tags,
)
from lawvm.uk_legislation.witnesses import (
    UKEffectWitness,
    UKLoweredOperationWitness,
    UKProvisionExtractionWitness,
)


@dataclass(frozen=True)
class UKTableLoweringResult:
    handled: bool
    op: Optional[LegalOperation] = None


@dataclass(frozen=True)
class UKTableBatchLoweringResult:
    handled: bool
    ops: tuple[LegalOperation, ...] = ()


@dataclass(frozen=True)
class UKTableCellContext:
    handled: bool
    target: LegalAddress
    table_cell_selector: Optional[dict[str, Any]] = None
    selector_rule_id: str = ""
    source_carried_table_entry_paragraph_substitution: Optional[dict[str, Any]] = None


def _uk_definition_pseudo_parent_target(target: LegalAddress) -> Optional[LegalAddress]:
    """Return the owning provision for feed pseudo-targets like s.167(1) defn."""
    for index, (kind, label) in enumerate(target.path):
        if kind.lower() == "paragraph" and label.strip().lower() == "defn":
            parent_path = target.path[:index]
            if parent_path and any(parent_kind == "section" for parent_kind, _ in parent_path):
                return LegalAddress(path=parent_path, special=target.special)
            return None
    return None


def try_lower_table_column_insert(
    *,
    effect: UKEffectRecord,
    action: str,
    t_str: str,
    target: LegalAddress,
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    sequence: int,
    effect_witness: UKEffectWitness,
    extraction_witness: UKProvisionExtractionWitness,
    original_targets_str: list[str],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> UKTableLoweringResult:
    table_column_insert_selector = (
        _uk_table_column_insert_selector(
            target_ref=t_str,
            target=target,
            extracted_text=extracted_text,
            extracted_el=extracted_el,
        )
        if action == "insert"
        else None
    )
    if table_column_insert_selector is None:
        return UKTableLoweringResult(handled=False)

    table_marker_parent = _uk_parent_target_before_table_marker(target)
    parent_target = table_marker_parent
    if (
        parent_target is not None
        and len(parent_target.path) >= 2
        and parent_target.path[-1] == ("subsection", "1")
        and parent_target.path[-2][0] == "section"
    ):
        table_column_insert_selector = {
            **table_column_insert_selector,
            "allow_implicit_subsection_one_table": True,
            "table_marker_parent_target": str(parent_target),
        }
        parent_target = LegalAddress(path=parent_target.path[:-1], special=parent_target.special)

    source_column_payload = _uk_single_table_column_payload(extracted_el)
    if parent_target is None or source_column_payload is None:
        _append_uk_effect_lowering_rejection(
            lowering_rejections_out,
            rule_id=_UK_TABLE_COLUMN_INSERT_RULE_ID,
            family="source_table_elaboration",
            reason_code=(
                "table_marker_parent_missing"
                if parent_target is None
                else "between_columns_without_single_column_payload"
            ),
            reason=(
                "UK table-column insertion needs both a containing "
                "table target and an exactly one-column BlockAmendment "
                "table payload; lowering blocks instead of inventing "
                "column cells from flattened text."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={"target_ref": t_str, "target": str(target), **table_column_insert_selector},
        )
        return UKTableLoweringResult(handled=True)

    _append_uk_effect_lowering_observation(
        lowering_rejections_out,
        rule_id=_UK_TABLE_COLUMN_INSERT_RULE_ID,
        family="source_table_elaboration",
        reason_code="explicit_between_columns_table_column_insert_selector",
        reason=(
            "UK table-column insertion lowered as a typed column "
            "insert; replay must prove the visual column boundary, "
            "row alignment, and span adjustments before mutating the table."
        ),
        effect=effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        detail={
            "target_ref": t_str,
            "original_target": str(target),
            "containing_target": str(parent_target),
            **table_column_insert_selector,
        },
    )
    return UKTableLoweringResult(
        handled=True,
        op=_build_table_payload_op(
            effect=effect,
            sequence=sequence,
            target=parent_target,
            payload=source_column_payload,
            effect_witness=effect_witness,
            extraction_witness=extraction_witness,
            original_targets_str=original_targets_str,
            t_str=t_str,
            provenance_note=(
                f"{_NOTE_TABLE_COLUMN_INSERT_SELECTOR}"
                f"{json.dumps(table_column_insert_selector, ensure_ascii=False)}"
            ),
            witness_rule_id=_UK_TABLE_COLUMN_INSERT_RULE_ID,
        ),
    )


def try_lower_table_row_insert(
    *,
    effect: UKEffectRecord,
    action: str,
    t_str: str,
    target: LegalAddress,
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    source_root: Optional[ET.Element],
    sequence: int,
    effect_witness: UKEffectWitness,
    extraction_witness: UKProvisionExtractionWitness,
    original_targets_str: list[str],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> UKTableLoweringResult:
    table_row_insert_selector = (
        _uk_table_entry_row_insert_selector(
            target_ref=t_str,
            target=target,
            extracted_text=extracted_text,
            extracted_el=extracted_el,
            source_root=source_root,
        )
        if action == "insert"
        else None
    )
    if table_row_insert_selector is None:
        return UKTableLoweringResult(handled=False)

    table_marker_parent = _uk_parent_target_before_table_marker(target)
    parent_target = table_marker_parent
    if (
        parent_target is None
        and table_row_insert_selector.get("source_names_table")
        and _addr_leaf_kind(target)
        in {"section", "subsection", "paragraph", "schedule", "part", "chapter"}
    ):
        parent_target = target
    if (
        parent_target is None
        and str(table_row_insert_selector.get("source_payload_mode") or "") == "table_rows"
        and _addr_leaf_kind(target) == "subsection"
    ):
        parent_target = target
    if (
        parent_target is not None
        and len(parent_target.path) >= 2
        and parent_target.path[-1] == ("subsection", "1")
        and parent_target.path[-2][0] == "section"
    ):
        table_row_insert_selector = {
            **table_row_insert_selector,
            "allow_implicit_subsection_one_table": True,
            "table_marker_parent_target": str(parent_target),
        }
        parent_target = LegalAddress(path=parent_target.path[:-1], special=parent_target.special)
    if parent_target is None:
        _append_uk_effect_lowering_rejection(
            lowering_rejections_out,
            rule_id="uk_effect_table_entry_row_insert_target_unresolved",
            family="source_table_elaboration",
            reason_code="table_marker_parent_missing",
            reason=(
                "UK table-row insertion source names a table entry, "
                "but the affected target could not be reduced to a "
                "containing provision for table-row replay."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={"target_ref": t_str, "target": str(target), **table_row_insert_selector},
        )
        return UKTableLoweringResult(handled=True)

    entry_label_table_rows = (
        str(table_row_insert_selector.get("selector_mode") or "") == "entry_label"
        and str(table_row_insert_selector.get("source_payload_mode") or "") == "table_rows"
    )
    logical_entry_group_payload = (
        str(table_row_insert_selector.get("source_payload_mode") or "")
        == "logical_table_entry_group"
    )
    needs_single_source_row_payload = (
        str(table_row_insert_selector.get("source_payload_mode") or "") == "single_table_row"
        or (
            str(table_row_insert_selector.get("selector_mode") or "") == "entry_label"
            and not entry_label_table_rows
        )
    )
    source_row_payload = (
        _uk_single_table_row_payload(extracted_el)
        if needs_single_source_row_payload
        else None
    )
    source_table_payload = (
        _uk_schedule_list_entry_table_payload(extracted_el)
        if str(table_row_insert_selector.get("source_payload_mode") or "") == "table_rows"
        else None
    )
    source_logical_entry_group_payload = (
        _uk_single_logical_table_entry_group_payload(extracted_el)
        if logical_entry_group_payload
        else None
    )
    if needs_single_source_row_payload and source_row_payload is None:
        _append_uk_effect_lowering_rejection(
            lowering_rejections_out,
            rule_id=_UK_TABLE_ENTRY_ROW_INSERT_RULE_ID,
            family="source_table_elaboration",
            reason_code=(
                "explicit_table_entry_label_insert_without_single_row_payload"
                if str(table_row_insert_selector.get("selector_mode") or "") == "entry_label"
                else "deictic_table_entry_insert_without_single_row_payload"
            ),
            reason=(
                "UK table-row insertion resolves a table-entry anchor, but "
                "the source does not carry exactly one BlockAmendment "
                "table row payload; lowering blocks instead of "
                "inventing a row from flattened text."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                "target_ref": t_str,
                "original_target": str(target),
                "containing_target": str(parent_target),
                "entry_shape": (
                    "deictic_table_entry"
                    if str(table_row_insert_selector.get("source_payload_mode") or "")
                    == "single_table_row"
                    else "numbered_entry"
                ),
                **table_row_insert_selector,
            },
        )
        return UKTableLoweringResult(handled=True)
    if logical_entry_group_payload and source_logical_entry_group_payload is None:
        _append_uk_effect_lowering_rejection(
            lowering_rejections_out,
            rule_id=_UK_TABLE_ENTRY_ROW_INSERT_RULE_ID,
            family="source_table_elaboration",
            reason_code="deictic_table_entry_insert_without_single_logical_entry_payload",
            reason=(
                "UK table-row insertion resolves a deictic table-entry "
                "anchor, but the source table payload is not exactly one "
                "logical entry group owned by a rowspanning first cell."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                "target_ref": t_str,
                "original_target": str(target),
                "containing_target": str(parent_target),
                "entry_shape": "deictic_logical_table_entry_group",
                **table_row_insert_selector,
            },
        )
        return UKTableLoweringResult(handled=True)
    if (
        str(table_row_insert_selector.get("source_payload_mode") or "") == "table_rows"
        and source_table_payload is None
    ):
        _append_uk_effect_lowering_rejection(
            lowering_rejections_out,
            rule_id=_UK_TABLE_ENTRY_ROW_INSERT_RULE_ID,
            family="source_table_elaboration",
            reason_code="explicit_table_entry_group_insert_without_table_payload",
            reason=(
                "UK table-entry group insertion names an entry anchor, "
                "but the source does not carry a BlockAmendment table "
                "payload; lowering blocks instead of inventing table "
                "rows from flattened text."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                "target_ref": t_str,
                "original_target": str(target),
                "containing_target": str(parent_target),
                **table_row_insert_selector,
            },
        )
        return UKTableLoweringResult(handled=True)

    _append_uk_effect_lowering_observation(
        lowering_rejections_out,
        rule_id=_UK_TABLE_ENTRY_ROW_INSERT_RULE_ID,
        family="source_table_elaboration",
        reason_code="explicit_table_entry_row_insert_selector",
        reason=(
            "UK table-row insertion lowered as a typed row insert; "
            "replay must resolve the source-owned table row before "
            "mutating table structure."
        ),
        effect=effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        detail={
            "target_ref": t_str,
            "original_target": str(target),
            "containing_target": str(parent_target),
            **table_row_insert_selector,
        },
    )
    payload_node = _table_row_insert_payload(
        selector=table_row_insert_selector,
        source_row_payload=source_row_payload,
        source_table_payload=source_table_payload,
        source_logical_entry_group_payload=source_logical_entry_group_payload,
        logical_entry_group_payload=logical_entry_group_payload,
    )
    return UKTableLoweringResult(
        handled=True,
        op=_build_table_payload_op(
            effect=effect,
            sequence=sequence,
            target=parent_target,
            payload=payload_node,
            effect_witness=effect_witness,
            extraction_witness=extraction_witness,
            original_targets_str=original_targets_str,
            t_str=t_str,
            provenance_note=(
                f"{_NOTE_TABLE_ROW_INSERT_SELECTOR}"
                f"{json.dumps(table_row_insert_selector, ensure_ascii=False)}"
            ),
            witness_rule_id=_UK_TABLE_ENTRY_ROW_INSERT_RULE_ID,
        ),
    )


def try_lower_repeal_table_effect(
    *,
    effect: UKEffectRecord,
    t_str: str,
    target: LegalAddress,
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    source_root: Optional[ET.Element],
    sequence: int,
    effect_witness: UKEffectWitness,
    extraction_witness: UKProvisionExtractionWitness,
    original_targets_str: list[str],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> UKTableBatchLoweringResult:
    pseudo_definition_parent_target = _uk_definition_pseudo_parent_target(target)
    if pseudo_definition_parent_target is not None:
        pseudo_definition_text_repeal = _uk_table_driven_repeal_table_quoted_words_text_repeal(
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            source_root=source_root,
            target=pseudo_definition_parent_target,
            allow_structural_definition_entry=True,
        )
        if (
            pseudo_definition_text_repeal.recognized
            and pseudo_definition_text_repeal.original
            and pseudo_definition_text_repeal.rule_id
            == _UK_REPEAL_TABLE_DEFINITION_ENTRY_TEXT_REPEAL_RULE_ID
        ):
            pseudo_definition_originals = (
                pseudo_definition_text_repeal.original,
                *pseudo_definition_text_repeal.additional_originals,
            )
            _append_uk_effect_lowering_observation(
                lowering_rejections_out,
                rule_id=_UK_REPEAL_TABLE_DEFINITION_ENTRY_TEXT_REPEAL_RULE_ID,
                family="source_repeal_table_elaboration",
                reason_code="unique_repeal_table_extent_row_definition_entry_from_pseudo_target",
                reason=(
                    "UK repeal-table source row matched the affected Act and "
                    "owning provision exactly, the feed target used a pseudo "
                    "definition child path, and the extent cell explicitly "
                    "names the definition entry; lowering emits a bounded "
                    "definition-entry text delete instead of structurally "
                    "repealing the pseudo path."
                ),
                effect=effect,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                detail={
                    "target_ref": t_str,
                    "original_target": str(target),
                    "target": str(pseudo_definition_parent_target),
                    "table_index": pseudo_definition_text_repeal.table_index,
                    "row_text": pseudo_definition_text_repeal.row_text,
                    "enactment_cell": pseudo_definition_text_repeal.enactment_cell,
                    "extent_cell": pseudo_definition_text_repeal.extent_cell,
                    "enactment_match_basis": (
                        pseudo_definition_text_repeal.enactment_match_basis
                    ),
                    "original": pseudo_definition_text_repeal.original,
                    "originals": pseudo_definition_originals,
                    "occurrence": pseudo_definition_text_repeal.occurrence,
                    "end_occurrence": pseudo_definition_text_repeal.end_occurrence,
                },
            )
            return UKTableBatchLoweringResult(
                handled=True,
                ops=_build_repeal_table_text_ops(
                    effect=effect,
                    sequence=sequence,
                    target=pseudo_definition_parent_target,
                    originals=pseudo_definition_originals,
                    occurrence=pseudo_definition_text_repeal.occurrence,
                    end_occurrence=pseudo_definition_text_repeal.end_occurrence,
                    rule_id=_UK_REPEAL_TABLE_DEFINITION_ENTRY_TEXT_REPEAL_RULE_ID,
                    effect_witness=effect_witness,
                    extraction_witness=extraction_witness,
                    original_targets_str=original_targets_str,
                    t_str=t_str,
                ),
            )
        if pseudo_definition_text_repeal.recognized:
            _append_uk_effect_lowering_rejection(
                lowering_rejections_out,
                rule_id=f"{_UK_REPEAL_TABLE_DEFINITION_ENTRY_TEXT_REPEAL_RULE_ID}_unresolved",
                family="source_repeal_table_elaboration",
                reason_code=pseudo_definition_text_repeal.reason_code,
                reason=(
                    "UK repeal-table source exposed a pseudo definition "
                    "target, but the source table did not uniquely name a "
                    "definition entry in the owning provision."
                ),
                effect=effect,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                detail={
                    "target_ref": t_str,
                    "original_target": str(target),
                    "target": str(pseudo_definition_parent_target),
                    "match_count": pseudo_definition_text_repeal.match_count,
                },
            )
            return UKTableBatchLoweringResult(handled=True)

    repeal_table_structural_repeal = _uk_table_driven_repeal_table_structural_repeal(
        effect=effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        source_root=source_root,
        target=target,
    )
    if repeal_table_structural_repeal.recognized and repeal_table_structural_repeal.match_count == 1:
        reason_code = (
            repeal_table_structural_repeal.reason_code
            or "unique_repeal_table_extent_row_structural_repeal"
        )
        if reason_code == "mixed_structural_and_word_repeal_split":
            parent_target = LegalAddress(path=target.path[:-1], special=None)
            _append_uk_effect_lowering_observation(
                lowering_rejections_out,
                rule_id="uk_effect_repeal_table_mixed_structural_word_repeal_split",
                family="source_repeal_table_elaboration",
                reason_code=reason_code,
                reason=(
                    "UK repeal-table source row explicitly names both a "
                    "structural target and an adjacent word deletion; lowering "
                    "emits separate typed operations so each mutation boundary "
                    "remains owned."
                ),
                effect=effect,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                detail={
                    "target_ref": t_str,
                    "target": str(target),
                    "text_target": str(parent_target),
                    "text_selector": repeal_table_structural_repeal.mixed_word_selector,
                    "table_index": repeal_table_structural_repeal.table_index,
                    "row_text": repeal_table_structural_repeal.row_text,
                    "enactment_cell": repeal_table_structural_repeal.enactment_cell,
                    "extent_cell": repeal_table_structural_repeal.extent_cell,
                    "enactment_match_basis": repeal_table_structural_repeal.enactment_match_basis,
                },
            )
            return UKTableBatchLoweringResult(
                handled=True,
                ops=(
                    *_build_repeal_table_text_ops(
                        effect=effect,
                        sequence=sequence,
                        target=parent_target,
                        originals=(repeal_table_structural_repeal.mixed_word_selector,),
                        occurrence=0,
                        end_occurrence=0,
                        rule_id="uk_effect_repeal_table_mixed_structural_word_repeal_split",
                        effect_witness=effect_witness,
                        extraction_witness=extraction_witness,
                        original_targets_str=original_targets_str,
                        t_str=t_str,
                        op_id_suffix="_text_repeal",
                    ),
                    _build_table_structural_repeal_op(
                        effect=effect,
                        sequence=sequence,
                        target=target,
                        effect_witness=effect_witness,
                        extraction_witness=extraction_witness,
                        original_targets_str=original_targets_str,
                        t_str=t_str,
                        witness_rule_id=(
                            "uk_effect_repeal_table_mixed_structural_word_repeal_split"
                        ),
                        op_id_suffix="_structural_repeal",
                    ),
                ),
            )
        _append_uk_effect_lowering_observation(
            lowering_rejections_out,
            rule_id=_UK_REPEAL_TABLE_STRUCTURAL_REPEAL_RULE_ID,
            family="source_repeal_table_elaboration",
            reason_code=reason_code,
            reason=(
                "UK repeal-table source row matched the affected Act and "
                "provision exactly, and its extent cell names a whole "
                "provision repeal; lowering emits a typed exact-target "
                "repeal instead of replaying the broad repeal schedule."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                "target_ref": t_str,
                "target": str(target),
                "table_index": repeal_table_structural_repeal.table_index,
                "row_text": repeal_table_structural_repeal.row_text,
                "enactment_cell": repeal_table_structural_repeal.enactment_cell,
                "extent_cell": repeal_table_structural_repeal.extent_cell,
                "enactment_match_basis": repeal_table_structural_repeal.enactment_match_basis,
                "split_from_mixed_extent_row": (
                    reason_code == "mixed_structural_and_word_repeal_split_structural_target"
                ),
            },
        )
        return UKTableBatchLoweringResult(
            handled=True,
            ops=(
                _build_table_structural_repeal_op(
                    effect=effect,
                    sequence=sequence,
                    target=target,
                    effect_witness=effect_witness,
                    extraction_witness=extraction_witness,
                    original_targets_str=original_targets_str,
                    t_str=t_str,
                    witness_rule_id=_UK_REPEAL_TABLE_STRUCTURAL_REPEAL_RULE_ID,
                ),
            ),
        )
    if repeal_table_structural_repeal.recognized:
        _append_uk_effect_lowering_rejection(
            lowering_rejections_out,
            rule_id=f"{_UK_REPEAL_TABLE_STRUCTURAL_REPEAL_RULE_ID}_unresolved",
            family="source_repeal_table_elaboration",
            reason_code=repeal_table_structural_repeal.reason_code,
            reason=(
                "UK repeal-table source could not be resolved to one "
                "exact structural extent row for the affected target."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                "target_ref": t_str,
                "target": str(target),
                "match_count": repeal_table_structural_repeal.match_count,
                "table_index": repeal_table_structural_repeal.table_index,
                "row_text": repeal_table_structural_repeal.row_text,
                "enactment_cell": repeal_table_structural_repeal.enactment_cell,
                "extent_cell": repeal_table_structural_repeal.extent_cell,
                "enactment_match_basis": repeal_table_structural_repeal.enactment_match_basis,
                "broad_container_target": repeal_table_structural_repeal.broad_container_target,
            },
        )
        return UKTableBatchLoweringResult(handled=True)

    repeal_table_text_repeal = _uk_table_driven_repeal_table_quoted_words_text_repeal(
        effect=effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        source_root=source_root,
        target=target,
    )
    if repeal_table_text_repeal.recognized and repeal_table_text_repeal.original:
        repeal_table_rule_id = repeal_table_text_repeal.rule_id
        if repeal_table_rule_id == _UK_REPEAL_TABLE_DEFINITION_ENTRY_TEXT_REPEAL_RULE_ID:
            reason_code = "unique_repeal_table_extent_row_definition_entry"
            reason = (
                "UK repeal-table source row matched the affected Act and "
                "provision exactly, and its extent cell names a definition "
                "entry repeal; lowering emits definition-entry text deletes "
                "instead of replaying the broad repeal schedule."
            )
        elif repeal_table_rule_id == _UK_REPEAL_TABLE_DEFINITION_CHILD_TEXT_REPEAL_RULE_ID:
            reason_code = "unique_repeal_table_extent_row_definition_child"
            reason = (
                "UK repeal-table source row matched the affected Act and "
                "provision exactly, and its extent cell names definition "
                "child repeals; lowering emits definition-child text deletes "
                "instead of replaying the broad repeal schedule."
            )
        elif repeal_table_rule_id == _UK_REPEAL_TABLE_SENTENCE_TEXT_REPEAL_RULE_ID:
            reason_code = "unique_repeal_table_extent_row_sentence"
            reason = (
                "UK repeal-table source row matched the affected Act and "
                "provision exactly, and its extent cell names an ordinal "
                "sentence repeal; lowering emits a sentence text delete "
                "instead of replaying the broad repeal schedule."
            )
        else:
            reason_code = "unique_repeal_table_extent_row_quoted_words"
            reason = (
                "UK repeal-table source row matched the affected Act and "
                "provision exactly, and its extent cell names a quoted "
                "word-level repeal; lowering emits a text delete instead "
                "of replaying the broad repeal schedule."
            )
        repeal_table_originals = (
            repeal_table_text_repeal.original,
            *repeal_table_text_repeal.additional_originals,
        )
        _append_uk_effect_lowering_observation(
            lowering_rejections_out,
            rule_id=repeal_table_rule_id,
            family="source_repeal_table_elaboration",
            reason_code=reason_code,
            reason=reason,
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                "target_ref": t_str,
                "target": str(target),
                "table_index": repeal_table_text_repeal.table_index,
                "row_text": repeal_table_text_repeal.row_text,
                "enactment_cell": repeal_table_text_repeal.enactment_cell,
                "extent_cell": repeal_table_text_repeal.extent_cell,
                "enactment_match_basis": repeal_table_text_repeal.enactment_match_basis,
                "original": repeal_table_text_repeal.original,
                "originals": repeal_table_originals,
                "occurrence": repeal_table_text_repeal.occurrence,
                "end_occurrence": repeal_table_text_repeal.end_occurrence,
            },
        )
        return UKTableBatchLoweringResult(
            handled=True,
            ops=_build_repeal_table_text_ops(
                effect=effect,
                sequence=sequence,
                target=target,
                originals=repeal_table_originals,
                occurrence=repeal_table_text_repeal.occurrence,
                end_occurrence=repeal_table_text_repeal.end_occurrence,
                rule_id=repeal_table_rule_id,
                effect_witness=effect_witness,
                extraction_witness=extraction_witness,
                original_targets_str=original_targets_str,
                t_str=t_str,
            ),
        )
    if repeal_table_text_repeal.recognized:
        reason = (
            "UK repeal-table source names a sentence deletion for the affected target, "
            "but UK replay does not yet have a typed sentence-delete selector for this lane."
            if repeal_table_text_repeal.reason_code == "sentence_repeal_requires_sentence_selector"
            else (
                "UK repeal-table source could not be resolved to one "
                "bounded quoted-words extent row for the affected target."
            )
        )
        _append_uk_effect_lowering_rejection(
            lowering_rejections_out,
            rule_id=f"{_UK_REPEAL_TABLE_QUOTED_WORDS_TEXT_REPEAL_RULE_ID}_unresolved",
            family="source_repeal_table_elaboration",
            reason_code=repeal_table_text_repeal.reason_code,
            reason=reason,
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                "target_ref": t_str,
                "target": str(target),
                "match_count": repeal_table_text_repeal.match_count,
                "table_index": repeal_table_text_repeal.table_index,
                "row_text": repeal_table_text_repeal.row_text,
                "enactment_cell": repeal_table_text_repeal.enactment_cell,
                "extent_cell": repeal_table_text_repeal.extent_cell,
                "enactment_match_basis": repeal_table_text_repeal.enactment_match_basis,
            },
        )
        return UKTableBatchLoweringResult(handled=True)
    return UKTableBatchLoweringResult(handled=False)


def prepare_table_cell_text_patch_context(
    *,
    effect: UKEffectRecord,
    t_str: str,
    target: LegalAddress,
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    source_root: Optional[ET.Element],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> UKTableCellContext:
    table_cell_selector = _uk_table_entry_inline_text_selector(
        target_ref=t_str,
        target=target,
        extracted_text=extracted_text,
        extracted_el=extracted_el,
        source_root=source_root,
    )
    if table_cell_selector is None:
        table_cell_selector = _uk_table_column_text_patch_selector(
            target_ref=t_str,
            target=target,
            extracted_text=extracted_text,
        )
    source_carried_table_entry_paragraph_substitution = (
        _source_carried_table_entry_paragraph_substitution(
            extracted_el=extracted_el,
            source_root=source_root,
            extracted_text=extracted_text,
            target_ref=t_str,
            target=target,
        )
        if table_cell_selector is None
        else None
    )
    if source_carried_table_entry_paragraph_substitution is not None:
        table_cell_selector = dict(
            source_carried_table_entry_paragraph_substitution["table_cell_selector"]
        )
    if table_cell_selector is None:
        embedded_table_structural_substitution = _uk_embedded_table_payload_structural_substitution(
            target_ref=t_str,
            target=target,
            extracted_text=extracted_text,
        )
        if embedded_table_structural_substitution is not None:
            _append_uk_effect_lowering_observation(
                lowering_rejections_out,
                rule_id=_UK_EMBEDDED_TABLE_STRUCTURAL_SUBSTITUTION_RULE_ID,
                family="source_table_elaboration",
                reason_code="embedded_table_belongs_to_structural_substitution_payload",
                reason=(
                    "UK source text contains table words inside a paragraph-level "
                    "substitution payload; lowering preserves the structural "
                    "paragraph substitution path instead of treating the embedded "
                    "payload table as a standalone table-entry instruction."
                ),
                effect=effect,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                detail=embedded_table_structural_substitution,
            )
            return UKTableCellContext(handled=False, target=target)
        embedded_table_structural_insertion = _uk_embedded_table_payload_structural_insertion(
            target_ref=t_str,
            target=target,
            extracted_text=extracted_text,
        )
        if embedded_table_structural_insertion is not None:
            _append_uk_effect_lowering_observation(
                lowering_rejections_out,
                rule_id=_UK_EMBEDDED_TABLE_STRUCTURAL_INSERTION_RULE_ID,
                family="source_table_elaboration",
                reason_code="embedded_table_belongs_to_structural_insertion_payload",
                reason=(
                    "UK source text contains table words inside a structural "
                    "insertion payload; lowering preserves the structural "
                    "insertion path instead of treating embedded payload text as "
                    "a standalone table-entry instruction."
                ),
                effect=effect,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                detail=embedded_table_structural_insertion,
            )
            return UKTableCellContext(handled=False, target=target)
        table_entry_instruction = _uk_broad_table_entry_instruction(
            target_ref=t_str,
            target=target,
            extracted_text=extracted_text,
            effect_type=effect.effect_type,
        )
        if table_entry_instruction:
            _append_uk_effect_lowering_rejection(
                lowering_rejections_out,
                rule_id=_UK_TABLE_ENTRY_INSTRUCTION_REJECTED_RULE_ID,
                family="source_table_elaboration",
                reason_code="table_entry_instruction_without_cell_target",
                reason=(
                    "UK source instruction targets a table entry or column, "
                    "but effect metadata names only a broader provision; "
                    "lowering must not replay it as a host repeal/replace."
                ),
                effect=effect,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                detail=table_entry_instruction,
            )
            return UKTableCellContext(handled=True, target=target)
        return UKTableCellContext(handled=False, target=target)

    selector_rule_id = str(table_cell_selector.get("rule_id") or _UK_TABLE_ENTRY_INLINE_TEXT_RULE_ID)
    selector_mode = str(table_cell_selector.get("selector_mode") or "")
    table_marker_parent = _uk_parent_target_before_table_marker(target)
    parent_target = (
        target
        if (
            selector_mode in {"unique_column_text", "unique_entry_cell"}
            or (
                selector_mode == "unique_relating_cell"
                and bool(table_cell_selector.get("source_names_containing_target"))
            )
        )
        and table_marker_parent is None
        else table_marker_parent
    )
    if (
        parent_target is not None
        and len(parent_target.path) >= 2
        and parent_target.path[-1] == ("subsection", "1")
        and parent_target.path[-2][0] == "section"
    ):
        table_cell_selector = {
            **table_cell_selector,
            "allow_implicit_subsection_one_table": True,
            "table_marker_parent_target": str(parent_target),
        }
        parent_target = LegalAddress(path=parent_target.path[:-1], special=parent_target.special)
    if parent_target is None:
        _append_uk_effect_lowering_rejection(
            lowering_rejections_out,
            rule_id="uk_effect_table_entry_inline_text_target_unresolved",
            family="source_table_elaboration",
            reason_code="table_marker_parent_missing",
            reason=(
                "UK table-entry word effect named a table cell, but "
                "the affected target could not be reduced to a containing "
                "provision for table-cell replay."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={"target_ref": t_str, "target": str(target), **table_cell_selector},
        )
        return UKTableCellContext(handled=True, target=target)

    _append_uk_effect_lowering_observation(
        lowering_rejections_out,
        rule_id=selector_rule_id,
        family="source_table_elaboration",
        reason_code=(
            "explicit_table_column_preimage_selector"
            if selector_mode == "unique_column_text"
            else "source_parent_table_entry_paragraph_selector"
            if selector_mode == "unique_entry_cell"
            else "explicit_table_entry_column_selector"
        ),
        reason=(
            "UK table word effect lowered as a typed table-cell text "
            "patch; replay must resolve the source-owned table cell "
            "before mutating text."
        ),
        effect=effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        detail={
            "target_ref": t_str,
            "original_target": str(target),
            "containing_target": str(parent_target),
            **table_cell_selector,
        },
    )
    return UKTableCellContext(
        handled=False,
        target=parent_target,
        table_cell_selector=table_cell_selector,
        selector_rule_id=selector_rule_id,
        source_carried_table_entry_paragraph_substitution=(
            source_carried_table_entry_paragraph_substitution
        ),
    )


def _table_row_insert_payload(
    *,
    selector: dict[str, Any],
    source_row_payload: Optional[IRNode],
    source_table_payload: Optional[IRNode],
    source_logical_entry_group_payload: Optional[IRNode],
    logical_entry_group_payload: bool,
) -> IRNode:
    if str(selector.get("source_payload_mode") or "") == "table_rows":
        assert source_table_payload is not None
        return dc_replace(
            source_table_payload,
            attrs={
                **dict(source_table_payload.attrs or {}),
                "source_rule_id": "uk_table_entry_group_insert_payload"
                if str(selector.get("selector_mode") or "") == "entry_group_heading"
                else "uk_table_entry_label_insert_payload",
                "anchor_direction": str(selector["direction"]),
                **(
                    {
                        "relating_text": str(selector["relating_text"]),
                    }
                    if str(selector.get("selector_mode") or "") == "entry_group_heading"
                    else {
                        "anchor_entry_label": str(selector["anchor_entry_label"]),
                    }
                ),
            },
        )
    if logical_entry_group_payload:
        assert source_logical_entry_group_payload is not None
        return dc_replace(
            source_logical_entry_group_payload,
            attrs={
                **dict(source_logical_entry_group_payload.attrs or {}),
                "source_rule_id": "uk_table_entry_logical_group_insert_payload",
                "relating_text": str(selector["relating_text"]),
                "source_context": str(selector.get("source_context") or ""),
            },
        )
    if (
        str(selector.get("selector_mode") or "") == "entry_label"
        or str(selector.get("source_payload_mode") or "") == "single_table_row"
    ):
        assert source_row_payload is not None
        return dc_replace(
            source_row_payload,
            attrs={
                **dict(source_row_payload.attrs or {}),
                "source_rule_id": "uk_table_entry_row_insert_payload",
                **(
                    {
                        "anchor_entry_label": str(selector["anchor_entry_label"]),
                    }
                    if str(selector.get("selector_mode") or "") == "entry_label"
                    else {
                        "relating_text": str(selector["relating_text"]),
                        "source_context": str(selector.get("source_context") or ""),
                    }
                ),
            },
        )

    column_index = int(selector["column_index"])
    return IRNode(
        kind=IRNodeKind.ROW,
        label=None,
        attrs={
            "source_rule_id": "uk_table_entry_row_insert_payload",
            "target_column_index": str(column_index),
            "relating_text": str(selector["relating_text"]),
        },
        children=tuple(
            IRNode(
                kind=IRNodeKind.CELL,
                label=None,
                text=(str(selector["inserted_text"]) if cell_index == column_index else ""),
                attrs={
                    "source_rule_id": "uk_table_entry_row_insert_cell",
                    "column_index": str(cell_index),
                },
            )
            for cell_index in range(1, column_index + 1)
        ),
    )


def _build_table_structural_repeal_op(
    *,
    effect: UKEffectRecord,
    sequence: int,
    target: LegalAddress,
    effect_witness: UKEffectWitness,
    extraction_witness: UKProvisionExtractionWitness,
    original_targets_str: list[str],
    t_str: str,
    witness_rule_id: str,
    op_id_suffix: str = "",
) -> LegalOperation:
    src = OperationSource(
        statute_id=effect.affecting_act_id,
        title=effect.affecting_title,
        effective=effect_witness.applicability.effective_date or "",
        raw_text=extraction_witness.extracted_text,
    )
    target_expansion_witness = _uk_target_expansion_witness(
        t_str,
        [t_str],
        original_targets_str=original_targets_str,
    )
    lowered_witness = UKLoweredOperationWitness(
        op_id=f"{effect.effect_id}{op_id_suffix}",
        sequence=sequence,
        action=StructuralAction.REPEAL,
        target=target,
        payload=None,
        source=src,
        effect_witness=effect_witness,
        extraction_witness=extraction_witness,
        target_expansion_witness=target_expansion_witness,
        text_rewrite_witness=None,
        insertion_anchor_witness=None,
    )
    return LegalOperation(
        op_id=lowered_witness.op_id,
        sequence=lowered_witness.sequence,
        action=StructuralAction.REPEAL,
        target=target,
        payload=None,
        source=src,
        group_id=_uk_temporal_group_id(effect),
        provenance_tags=_uk_lowered_op_provenance_tags(lowered_witness),
        witness_rule_id=witness_rule_id,
    )


def _build_repeal_table_text_ops(
    *,
    effect: UKEffectRecord,
    sequence: int,
    target: LegalAddress,
    originals: tuple[str, ...],
    occurrence: int,
    end_occurrence: int,
    rule_id: str,
    effect_witness: UKEffectWitness,
    extraction_witness: UKProvisionExtractionWitness,
    original_targets_str: list[str],
    t_str: str,
    op_id_suffix: str = "",
) -> tuple[LegalOperation, ...]:
    src = OperationSource(
        statute_id=effect.affecting_act_id,
        title=effect.affecting_title,
        effective=effect_witness.applicability.effective_date or "",
        raw_text=extraction_witness.extracted_text,
    )
    target_expansion_witness = _uk_target_expansion_witness(
        t_str,
        [t_str],
        original_targets_str=original_targets_str,
    )
    ops: list[LegalOperation] = []
    for original_index, original in enumerate(originals):
        fragment_subs = [
            {
                "original": original,
                "replacement": "",
                "rule_id": rule_id,
                "occurrence": str(occurrence),
                "end_occurrence": str(end_occurrence),
            }
        ]
        text_patch = TextPatchSpec(
            kind=TextPatchKindEnum.DELETE,
            selector=TextSelector(
                match_text=original,
                occurrence=occurrence,
                end_occurrence=end_occurrence,
            ),
        )
        text_rewrite_witness = _uk_text_rewrite_spec(
            fragment_subs=fragment_subs,
            text_patch=text_patch,
            op_text_match=original,
            op_text_replacement="",
            op_text_occurrence=occurrence,
            op_text_end_occurrence=end_occurrence,
        )
        lowered_witness = UKLoweredOperationWitness(
            op_id=(
                f"{effect.effect_id}{op_id_suffix}"
                if len(originals) == 1
                else f"{effect.effect_id}{op_id_suffix}_{original_index}"
            ),
            sequence=sequence,
            action=StructuralAction.TEXT_REPEAL,
            target=target,
            payload=None,
            source=src,
            effect_witness=effect_witness,
            extraction_witness=extraction_witness,
            target_expansion_witness=target_expansion_witness,
            text_rewrite_witness=text_rewrite_witness,
            insertion_anchor_witness=None,
        )
        ops.append(
            LegalOperation(
                op_id=lowered_witness.op_id,
                sequence=lowered_witness.sequence,
                action=StructuralAction.TEXT_REPEAL,
                target=target,
                payload=None,
                source=src,
                group_id=_uk_temporal_group_id(effect),
                provenance_tags=_uk_lowered_op_provenance_tags(lowered_witness),
                text_patch=text_patch,
                witness_rule_id=rule_id,
            )
        )
    return tuple(ops)


def _build_table_payload_op(
    *,
    effect: UKEffectRecord,
    sequence: int,
    target: LegalAddress,
    payload: IRNode,
    effect_witness: UKEffectWitness,
    extraction_witness: UKProvisionExtractionWitness,
    original_targets_str: list[str],
    t_str: str,
    provenance_note: str,
    witness_rule_id: str,
) -> LegalOperation:
    src = OperationSource(
        statute_id=effect.affecting_act_id,
        title=effect.affecting_title,
        effective=effect_witness.applicability.effective_date or "",
        raw_text=extraction_witness.extracted_text,
    )
    target_expansion_witness = _uk_target_expansion_witness(
        t_str,
        [t_str],
        original_targets_str=original_targets_str,
    )
    lowered_witness = UKLoweredOperationWitness(
        op_id=effect.effect_id,
        sequence=sequence,
        action=StructuralAction.INSERT,
        target=target,
        payload=payload,
        source=src,
        effect_witness=effect_witness,
        extraction_witness=extraction_witness,
        target_expansion_witness=target_expansion_witness,
        text_rewrite_witness=None,
        insertion_anchor_witness=None,
    )
    return LegalOperation(
        op_id=lowered_witness.op_id,
        sequence=lowered_witness.sequence,
        action=StructuralAction.INSERT,
        target=target,
        payload=_payload_with_rewrite_witness(payload, lowered_witness),
        source=src,
        group_id=_uk_temporal_group_id(effect),
        provenance_tags=(
            *_uk_lowered_op_provenance_tags(lowered_witness),
            provenance_note,
        ),
        witness_rule_id=witness_rule_id,
    )

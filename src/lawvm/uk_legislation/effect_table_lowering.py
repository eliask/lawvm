"""Table-specific special lowering for UK effects."""

from __future__ import annotations

from dataclasses import dataclass, replace as dc_replace
import json
import xml.etree.ElementTree as ET
from typing import Any, Optional

from lawvm.core.ir import IRNode, LegalAddress, LegalOperation, OperationSource
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
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
from lawvm.uk_legislation.table_selectors import (
    UK_TABLE_COLUMN_INSERT_RULE_ID as _UK_TABLE_COLUMN_INSERT_RULE_ID,
    UK_TABLE_ENTRY_ROW_INSERT_RULE_ID as _UK_TABLE_ENTRY_ROW_INSERT_RULE_ID,
    _uk_parent_target_before_table_marker,
    _uk_schedule_list_entry_table_payload,
    _uk_single_logical_table_entry_group_payload,
    _uk_single_table_column_payload,
    _uk_single_table_row_payload,
    _uk_table_column_insert_selector,
    _uk_table_entry_row_insert_selector,
)
from lawvm.uk_legislation.witness_builders import (
    _uk_target_expansion_witness,
    _uk_temporal_group_id,
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

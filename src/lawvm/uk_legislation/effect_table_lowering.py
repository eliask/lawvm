"""Table-specific special lowering for UK effects."""

from __future__ import annotations

from dataclasses import dataclass
import json
import xml.etree.ElementTree as ET
from typing import Any, Optional

from lawvm.core.ir import IRNode, LegalAddress, LegalOperation, OperationSource
from lawvm.core.semantic_types import StructuralAction
from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.lowering_records import (
    _append_uk_effect_lowering_observation,
    _append_uk_effect_lowering_rejection,
)
from lawvm.uk_legislation.provenance_notes import (
    NOTE_TABLE_COLUMN_INSERT_SELECTOR as _NOTE_TABLE_COLUMN_INSERT_SELECTOR,
)
from lawvm.uk_legislation.table_selectors import (
    UK_TABLE_COLUMN_INSERT_RULE_ID as _UK_TABLE_COLUMN_INSERT_RULE_ID,
    _uk_parent_target_before_table_marker,
    _uk_single_table_column_payload,
    _uk_table_column_insert_selector,
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

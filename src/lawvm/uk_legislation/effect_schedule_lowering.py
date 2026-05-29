"""Schedule-entry and schedule-table special lowering for UK effects."""

from __future__ import annotations

from dataclasses import dataclass, replace as dc_replace
import json
from lxml import etree as ET
from typing import Any, Optional

from lawvm.core.ir import IRNode, LegalAddress, LegalOperation, OperationSource
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.lowering_records import (
    _append_uk_effect_lowering_observation,
    _append_uk_effect_lowering_rejection,
)
from lawvm.uk_legislation.provenance_notes import (
    NOTE_SCHEDULE_LIST_ENTRY_REPEAL_SELECTOR as _NOTE_SCHEDULE_LIST_ENTRY_REPEAL_SELECTOR,
    NOTE_SCHEDULE_LIST_ENTRY_REPLACE_SELECTOR as _NOTE_SCHEDULE_LIST_ENTRY_REPLACE_SELECTOR,
    NOTE_SCHEDULE_LIST_ENTRY_SELECTOR as _NOTE_SCHEDULE_LIST_ENTRY_SELECTOR,
    NOTE_SCHEDULE_LIST_ENTRY_TABLE_ROWS_SELECTOR as _NOTE_SCHEDULE_LIST_ENTRY_TABLE_ROWS_SELECTOR,
    NOTE_SCHEDULE_TABLE_END_ROWS_SELECTOR as _NOTE_SCHEDULE_TABLE_END_ROWS_SELECTOR,
)
from lawvm.uk_legislation.schedule_list_selectors import (
    UK_SCHEDULE_LIST_ENTRY_INSERT_RULE_ID as _UK_SCHEDULE_LIST_ENTRY_INSERT_RULE_ID,
    UK_SCHEDULE_LIST_ENTRY_REPEAL_RULE_ID as _UK_SCHEDULE_LIST_ENTRY_REPEAL_RULE_ID,
    UK_SCHEDULE_LIST_ENTRY_REPLACE_RULE_ID as _UK_SCHEDULE_LIST_ENTRY_REPLACE_RULE_ID,
    _uk_schedule_list_entry_insert_selector,
    _uk_schedule_list_entry_repeal_selector,
    _uk_schedule_list_entry_replace_selector,
    split_schedule_entry_insert_payload,
)
from lawvm.uk_legislation.source_definition_fragments import (
    UK_SOURCE_RANGE_DEFINITION_ENTRY_LIST_END_INSERT_RULE_ID as _UK_SOURCE_RANGE_DEFINITION_ENTRY_LIST_END_INSERT_RULE_ID,
    UKPseudoDefinitionEntryRangeTextPatches,
)
from lawvm.uk_legislation.source_parent_payloads import (
    SOURCE_PARENT_SCHEDULE_ENTRY_INSERT_RE as _SOURCE_PARENT_SCHEDULE_ENTRY_INSERT_RE,
    _source_parent_instruction_with_payload,
)
from lawvm.uk_legislation.table_selectors import (
    UK_SCHEDULE_TABLE_END_ROWS_RULE_ID as _UK_SCHEDULE_TABLE_END_ROWS_RULE_ID,
    _uk_schedule_list_entry_table_payload,
    _uk_schedule_table_end_rows_selector,
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


_UK_SCHEDULE_LIST_ENTRY_TABLE_ROWS_RULE_ID = "uk_effect_schedule_list_entry_table_rows_lowered"


@dataclass(frozen=True)
class UKScheduleLoweringResult:
    handled: bool
    op: Optional[LegalOperation] = None


@dataclass(frozen=True)
class UKScheduleBatchLoweringResult:
    handled: bool
    ops: tuple[LegalOperation, ...] = ()


def lower_source_range_definition_list_end_schedule_entries(
    *,
    effect: UKEffectRecord,
    metadata_pseudo_definition_range: Optional[UKPseudoDefinitionEntryRangeTextPatches],
    sequence: int,
    effect_witness: UKEffectWitness,
    extraction_witness: UKProvisionExtractionWitness,
    original_targets_str: list[str],
    t_str: str,
) -> UKScheduleBatchLoweringResult:
    if metadata_pseudo_definition_range is None:
        return UKScheduleBatchLoweringResult(handled=False)
    target = metadata_pseudo_definition_range.target
    ops: list[LegalOperation] = []
    for entry in metadata_pseudo_definition_range.at_end_entries:
        inserted_text = str(entry.get("inserted_text") or "").strip()
        if not inserted_text:
            continue
        selector = {
            "rule_id": _UK_SOURCE_RANGE_DEFINITION_ENTRY_LIST_END_INSERT_RULE_ID,
            "direction": "end",
            "anchor_text": "",
            "inserted_text": inserted_text,
            "target_ref": t_str,
            "target": str(target),
            "placement_family": "definition_list_end_from_source_range",
            "source_row_id": str(entry.get("source_row_id") or ""),
            "source_inserted_definition_terms": tuple(
                term
                for term in str(entry.get("source_inserted_definition_terms") or "").split("\x1f")
                if term
            ),
            "source_payload_additional_definition_terms": tuple(
                term
                for term in str(entry.get("source_payload_additional_definition_terms") or "").split("\x1f")
                if term
            ),
        }
        payload_node = IRNode(
            kind=IRNodeKind.SCHEDULE_ENTRY,
            label=None,
            text=inserted_text,
            attrs={
                "source_rule_id": "uk_source_range_definition_list_end_insert_payload",
                "anchor_direction": "end",
                "placement_family": "definition_list_end_from_source_range",
                "source_row_id": str(entry.get("source_row_id") or ""),
            },
        )
        ops.append(
            _build_schedule_payload_op(
                effect=effect,
                sequence=sequence,
                action=StructuralAction.INSERT,
                target=target,
                payload=payload_node,
                effect_witness=effect_witness,
                extraction_witness=extraction_witness,
                original_targets_str=original_targets_str,
                t_str=t_str,
                provenance_note=(
                    f"{_NOTE_SCHEDULE_LIST_ENTRY_SELECTOR}"
                    f"{json.dumps(selector, ensure_ascii=False)}"
                ),
                witness_rule_id=_UK_SOURCE_RANGE_DEFINITION_ENTRY_LIST_END_INSERT_RULE_ID,
            )
        )
    return UKScheduleBatchLoweringResult(handled=bool(ops), ops=tuple(ops))


def try_lower_schedule_table_end_rows_insert(
    *,
    effect: UKEffectRecord,
    action: str,
    t_str: str,
    target: LegalAddress,
    heading_facet_target: bool,
    extracted_el: Optional[ET._Element],
    extracted_text: Optional[str],
    sequence: int,
    effect_witness: UKEffectWitness,
    extraction_witness: UKProvisionExtractionWitness,
    original_targets_str: list[str],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> UKScheduleLoweringResult:
    schedule_table_end_rows_selector = (
        _uk_schedule_table_end_rows_selector(
            target_ref=t_str,
            target=target,
            extracted_text=extracted_text,
        )
        if action == "insert" and not heading_facet_target
        else None
    )
    if schedule_table_end_rows_selector is None:
        return UKScheduleLoweringResult(handled=False)

    table_payload_node = _uk_schedule_list_entry_table_payload(extracted_el)
    if table_payload_node is None:
        _append_uk_effect_lowering_rejection(
            lowering_rejections_out,
            rule_id=_UK_SCHEDULE_TABLE_END_ROWS_RULE_ID,
            family="source_table_elaboration",
            reason_code="explicit_schedule_end_insert_without_table_payload",
            reason=(
                "UK source text explicitly inserts at the end of a "
                "schedule, but no single BlockAmendment table payload "
                "was available; lowering blocks instead of inventing "
                "flattened text or schedule entries."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail=dict(schedule_table_end_rows_selector),
        )
        return UKScheduleLoweringResult(handled=True)

    _append_uk_effect_lowering_observation(
        lowering_rejections_out,
        rule_id=_UK_SCHEDULE_TABLE_END_ROWS_RULE_ID,
        family="source_table_elaboration",
        reason_code="explicit_schedule_end_insert_table_payload",
        reason=(
            "UK source text explicitly inserts source-owned tabular "
            "rows at the end of a schedule table; lowering preserves "
            "the BlockAmendment table rows and replay must resolve a "
            "unique table-backed schedule carrier."
        ),
        effect=effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        detail=dict(schedule_table_end_rows_selector),
    )
    payload_node = dc_replace(
        table_payload_node,
        attrs={
            **dict(table_payload_node.attrs or {}),
            "source_rule_id": "uk_schedule_table_end_rows_payload",
            "anchor_direction": "end",
        },
    )
    return UKScheduleLoweringResult(
        handled=True,
        op=_build_schedule_payload_op(
            effect=effect,
            sequence=sequence,
            action=StructuralAction.INSERT,
            target=target,
            payload=payload_node,
            effect_witness=effect_witness,
            extraction_witness=extraction_witness,
            original_targets_str=original_targets_str,
            t_str=t_str,
            provenance_note=(
                f"{_NOTE_SCHEDULE_TABLE_END_ROWS_SELECTOR}"
                f"{json.dumps(schedule_table_end_rows_selector, ensure_ascii=False)}"
            ),
            witness_rule_id=_UK_SCHEDULE_TABLE_END_ROWS_RULE_ID,
        ),
    )


def try_lower_schedule_list_entry_mutation(
    *,
    effect: UKEffectRecord,
    action: str,
    effect_type: str,
    t_str: str,
    target: LegalAddress,
    heading_facet_target: bool,
    extracted_el: Optional[ET._Element],
    extracted_text: Optional[str],
    source_root: Optional[ET._Element],
    sequence: int,
    effect_witness: UKEffectWitness,
    extraction_witness: UKProvisionExtractionWitness,
    original_targets_str: list[str],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> UKScheduleBatchLoweringResult:
    schedule_list_entry_selector = (
        _uk_schedule_list_entry_insert_selector(
            target_ref=t_str,
            target=target,
            extracted_text=extracted_text,
        )
        if action == "insert" and not heading_facet_target
        else None
    )
    source_parent_schedule_entry_insert = (
        _source_parent_instruction_with_payload(
            extracted_el=extracted_el,
            source_root=source_root,
            extracted_text=extracted_text,
            instruction_pattern=_SOURCE_PARENT_SCHEDULE_ENTRY_INSERT_RE,
        )
        if schedule_list_entry_selector is None and action == "insert" and not heading_facet_target
        else None
    )
    if source_parent_schedule_entry_insert is not None:
        schedule_list_entry_selector = _uk_schedule_list_entry_insert_selector(
            target_ref=t_str,
            target=target,
            extracted_text=source_parent_schedule_entry_insert["combined_text"],
        )
        if schedule_list_entry_selector is not None:
            schedule_list_entry_selector = {
                **schedule_list_entry_selector,
                "source_parent_id": source_parent_schedule_entry_insert["source_parent_id"],
                "source_parent_instruction": source_parent_schedule_entry_insert[
                    "source_parent_instruction"
                ],
            }

    if schedule_list_entry_selector is not None:
        selector_rule_id = str(
            schedule_list_entry_selector.get("rule_id") or _UK_SCHEDULE_LIST_ENTRY_INSERT_RULE_ID
        )
        entry_carrier_family = str(
            schedule_list_entry_selector.get("entry_carrier_family") or "schedule_list"
        )
        table_payload_node = _uk_schedule_list_entry_table_payload(extracted_el)
        if table_payload_node is not None:
            _append_uk_effect_lowering_observation(
                lowering_rejections_out,
                rule_id=_UK_SCHEDULE_LIST_ENTRY_TABLE_ROWS_RULE_ID,
                family="source_table_elaboration",
                reason_code="explicit_schedule_entry_insert_table_payload",
                reason=(
                    "UK schedule-list-entry insertion carried a tabular "
                    "source payload; lowering preserves source rows and "
                    "replay must resolve the entry anchor in the target "
                    "schedule table before inserting rows."
                ),
                effect=effect,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                detail={
                    "selector_rule_id": str(schedule_list_entry_selector.get("rule_id") or ""),
                    **{
                        key: value
                        for key, value in schedule_list_entry_selector.items()
                        if key != "rule_id"
                    },
                },
            )
            payload_node = dc_replace(
                table_payload_node,
                attrs={
                    **dict(table_payload_node.attrs or {}),
                    "source_rule_id": "uk_schedule_list_entry_table_rows_payload",
                    "anchor_text": str(schedule_list_entry_selector["anchor_text"]),
                    "anchor_direction": str(schedule_list_entry_selector["direction"]),
                },
            )
            return UKScheduleBatchLoweringResult(
                handled=True,
                ops=(
                    _build_schedule_payload_op(
                        effect=effect,
                        sequence=sequence,
                        action=StructuralAction.INSERT,
                        target=target,
                        payload=payload_node,
                        effect_witness=effect_witness,
                        extraction_witness=extraction_witness,
                        original_targets_str=original_targets_str,
                        t_str=t_str,
                        provenance_note=(
                            f"{_NOTE_SCHEDULE_LIST_ENTRY_TABLE_ROWS_SELECTOR}"
                            f"{json.dumps(schedule_list_entry_selector, ensure_ascii=False)}"
                        ),
                        witness_rule_id=_UK_SCHEDULE_LIST_ENTRY_TABLE_ROWS_RULE_ID,
                    ),
                ),
            )

        _append_uk_effect_lowering_observation(
            lowering_rejections_out,
            rule_id=selector_rule_id,
            family="source_schedule_list_entry_elaboration",
            reason_code="explicit_schedule_list_entry_anchor",
            reason=(
                "UK list-entry insertion lowered as a typed schedule-entry "
                "sibling insert; replay must resolve exactly one anchor entry "
                "before mutating direct list children."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                **dict(schedule_list_entry_selector),
                "inserted_entry_count": len(
                    split_schedule_entry_insert_payload(
                        str(schedule_list_entry_selector["inserted_text"])
                    )
                ),
            },
        )
        inserted_entries = split_schedule_entry_insert_payload(
            str(schedule_list_entry_selector["inserted_text"])
        )
        if not inserted_entries:
            return UKScheduleBatchLoweringResult(handled=True)
        insert_ops: list[LegalOperation] = []
        anchor_text = str(schedule_list_entry_selector["anchor_text"])
        direction = str(schedule_list_entry_selector["direction"])
        for entry_index, inserted_text in enumerate(inserted_entries):
            entry_selector = {
                **dict(schedule_list_entry_selector),
                "anchor_text": anchor_text,
                "inserted_text": inserted_text,
                "source_inserted_text": str(schedule_list_entry_selector["inserted_text"]),
                "inserted_entry_index": entry_index,
                "inserted_entry_count": len(inserted_entries),
            }
            payload_node = IRNode(
                kind=IRNodeKind.SCHEDULE_ENTRY,
                label=None,
                text=inserted_text,
                attrs={
                    "source_rule_id": (
                        "uk_schedule_list_entry_insert_payload"
                        if entry_carrier_family == "schedule_list"
                        else "uk_non_schedule_list_entry_insert_payload"
                    ),
                    "anchor_text": anchor_text,
                    "anchor_direction": direction,
                    "source_inserted_entry_index": str(entry_index),
                    "source_inserted_entry_count": str(len(inserted_entries)),
                },
            )
            insert_ops.append(
                _build_schedule_payload_op(
                    effect=effect,
                    sequence=sequence,
                    action=StructuralAction.INSERT,
                    target=target,
                    payload=payload_node,
                    effect_witness=effect_witness,
                    extraction_witness=extraction_witness,
                    original_targets_str=original_targets_str,
                    t_str=t_str,
                    provenance_note=(
                        f"{_NOTE_SCHEDULE_LIST_ENTRY_SELECTOR}"
                        f"{json.dumps(entry_selector, ensure_ascii=False)}"
                    ),
                    witness_rule_id=selector_rule_id,
                )
            )
            if direction == "after":
                anchor_text = inserted_text
        return UKScheduleBatchLoweringResult(handled=True, ops=tuple(insert_ops))

    schedule_list_entry_repeal_selector = (
        _uk_schedule_list_entry_repeal_selector(
            target_ref=t_str,
            target=target,
            extracted_text=extracted_text,
        )
        if action == "repeal"
        or effect_type in {"words omitted", "word omitted", "words repealed", "word repealed"}
        else None
    )
    if schedule_list_entry_repeal_selector is not None:
        _append_uk_effect_lowering_observation(
            lowering_rejections_out,
            rule_id=_UK_SCHEDULE_LIST_ENTRY_REPEAL_RULE_ID,
            family="source_schedule_list_entry_elaboration",
            reason_code="explicit_schedule_list_entry_repeal_anchor",
            reason=(
                "UK schedule-list-entry repeal lowered as a typed "
                "entry-level schedule mutation; replay must resolve every "
                "claimed entry anchor before deleting any schedule child."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail=dict(schedule_list_entry_repeal_selector),
        )
        return UKScheduleBatchLoweringResult(
            handled=True,
            ops=(
                _build_schedule_payload_op(
                    effect=effect,
                    sequence=sequence,
                    action=StructuralAction.REPEAL,
                    target=target,
                    payload=None,
                    effect_witness=effect_witness,
                    extraction_witness=extraction_witness,
                    original_targets_str=original_targets_str,
                    t_str=t_str,
                    provenance_note=(
                        f"{_NOTE_SCHEDULE_LIST_ENTRY_REPEAL_SELECTOR}"
                        f"{json.dumps(schedule_list_entry_repeal_selector, ensure_ascii=False)}"
                    ),
                    witness_rule_id=_UK_SCHEDULE_LIST_ENTRY_REPEAL_RULE_ID,
                ),
            ),
        )

    schedule_list_entry_replace_selector = (
        _uk_schedule_list_entry_replace_selector(
            target_ref=t_str,
            target=target,
            extracted_text=extracted_text,
        )
        if action == "replace" or effect_type in {"words substituted", "word substituted"}
        else None
    )
    if schedule_list_entry_replace_selector is None:
        return UKScheduleBatchLoweringResult(handled=False)

    _append_uk_effect_lowering_observation(
        lowering_rejections_out,
        rule_id=_UK_SCHEDULE_LIST_ENTRY_REPLACE_RULE_ID,
        family="source_schedule_list_entry_elaboration",
        reason_code="explicit_schedule_list_entry_replace_anchor",
        reason=(
            "UK schedule-list-entry replacement lowered as a typed "
            "entry-level schedule mutation; replay must resolve the "
            "claimed entry anchor before replacing a schedule child."
        ),
        effect=effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        detail=dict(schedule_list_entry_replace_selector),
    )
    payload_node = IRNode(
        kind=IRNodeKind.SCHEDULE_ENTRY,
        label=None,
        text=str(schedule_list_entry_replace_selector["replacement_text"]),
        attrs={
            "source_rule_id": "uk_schedule_list_entry_replace_payload",
            "anchor_text": str(schedule_list_entry_replace_selector["anchor"]),
        },
    )
    return UKScheduleBatchLoweringResult(
        handled=True,
        ops=(
            _build_schedule_payload_op(
                effect=effect,
                sequence=sequence,
                action=StructuralAction.REPLACE,
                target=target,
                payload=payload_node,
                effect_witness=effect_witness,
                extraction_witness=extraction_witness,
                original_targets_str=original_targets_str,
                t_str=t_str,
                provenance_note=(
                    f"{_NOTE_SCHEDULE_LIST_ENTRY_REPLACE_SELECTOR}"
                    f"{json.dumps(schedule_list_entry_replace_selector, ensure_ascii=False)}"
                ),
                witness_rule_id=_UK_SCHEDULE_LIST_ENTRY_REPLACE_RULE_ID,
            ),
        ),
    )


def _build_schedule_payload_op(
    *,
    effect: UKEffectRecord,
    sequence: int,
    action: StructuralAction,
    target: LegalAddress,
    payload: Optional[IRNode],
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
        action=action,
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
        action=action,
        target=target,
        payload=(
            _payload_with_rewrite_witness(payload, lowered_witness)
            if payload is not None
            else None
        ),
        source=src,
        group_id=_uk_temporal_group_id(effect),
        provenance_tags=(
            *_uk_lowered_op_provenance_tags(lowered_witness),
            provenance_note,
        ),
        witness_rule_id=witness_rule_id,
    )

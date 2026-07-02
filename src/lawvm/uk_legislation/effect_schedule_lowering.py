"""Schedule-entry and schedule-table special lowering for UK effects."""

from __future__ import annotations

from dataclasses import dataclass, replace as dc_replace
import json
from lxml import etree as ET
from typing import Any, Optional

from lawvm.core.ir import IRNode, LegalAddress, LegalOperation, OperationSource, TextPatchSpec, TextSelector
from lawvm.core.semantic_types import IRNodeKind, StructuralAction, TextPatchKindEnum
from lawvm.uk_legislation.addressing import _addr_container, _addr_field, _addr_leaf_kind, _addr_leaf_label
from lawvm.uk_legislation.effects import (
    UKEffectRecord,
    UK_SCHEDULE_WORDS_BEFORE_TABLE_SUBSTITUTION_RULE_ID as _UK_SCHEDULE_WORDS_BEFORE_TABLE_SUBSTITUTION_RULE_ID,
)
from lawvm.uk_legislation.lowering_records import (
    _append_uk_effect_lowering_observation,
    _append_uk_effect_lowering_rejection,
)
from lawvm.uk_legislation.payload_identity import _synthesize_payload_descendant_eids

from lawvm.uk_legislation.provenance_notes import (
    NOTE_SCHEDULE_LIST_ENTRY_REPEAL_SELECTOR as _NOTE_SCHEDULE_LIST_ENTRY_REPEAL_SELECTOR,
    NOTE_SCHEDULE_LIST_ENTRY_REPLACE_SELECTOR as _NOTE_SCHEDULE_LIST_ENTRY_REPLACE_SELECTOR,
    NOTE_SCHEDULE_LIST_ENTRY_SELECTOR as _NOTE_SCHEDULE_LIST_ENTRY_SELECTOR,
    NOTE_SCHEDULE_LIST_ENTRY_TABLE_ROWS_SELECTOR as _NOTE_SCHEDULE_LIST_ENTRY_TABLE_ROWS_SELECTOR,
    NOTE_SCHEDULE_TABLE_END_ROWS_SELECTOR as _NOTE_SCHEDULE_TABLE_END_ROWS_SELECTOR,
)
from lawvm.uk_legislation.schedule_list_selectors import (
    UK_CONNECTOR_PRECEDING_CHILD_LIST_ENTRY_SUBSTITUTION_RULE_ID as _UK_CONNECTOR_PRECEDING_CHILD_LIST_ENTRY_SUBSTITUTION_RULE_ID,
    UK_SCHEDULE_LIST_ENTRY_INSERT_RULE_ID as _UK_SCHEDULE_LIST_ENTRY_INSERT_RULE_ID,
    UK_SCHEDULE_LIST_ENTRY_REPEAL_RULE_ID as _UK_SCHEDULE_LIST_ENTRY_REPEAL_RULE_ID,
    UK_SCHEDULE_LIST_ENTRY_REPLACE_RULE_ID as _UK_SCHEDULE_LIST_ENTRY_REPLACE_RULE_ID,
    _uk_connector_preceding_child_list_entry_substitution_selector,
    _uk_schedule_list_entry_insert_selector,
    _uk_schedule_list_entry_repeal_selector,
    _uk_schedule_list_entry_replace_selector,
    split_schedule_entry_insert_payload,
)
from lawvm.uk_legislation.source_definition_fragments import (
    UK_DIRECT_DEFINITION_ENTRY_LIST_END_INSERT_RULE_ID as _UK_DIRECT_DEFINITION_ENTRY_LIST_END_INSERT_RULE_ID,
    UK_SOURCE_RANGE_DEFINITION_ENTRY_LIST_END_INSERT_RULE_ID as _UK_SOURCE_RANGE_DEFINITION_ENTRY_LIST_END_INSERT_RULE_ID,
    UKPseudoDefinitionEntryRangeTextPatches,
    direct_definition_entry_list_end_fragment,
)
from lawvm.uk_legislation.source_parent_payloads import (
    SOURCE_PARENT_SCHEDULE_ENTRY_INSERT_RE as _SOURCE_PARENT_SCHEDULE_ENTRY_INSERT_RE,
    _source_previous_that_entry_insert_context,
    _source_parent_instruction_with_payload,
)
from lawvm.uk_legislation.table_selectors import (
    UK_SCHEDULE_TABLE_END_ROWS_RULE_ID as _UK_SCHEDULE_TABLE_END_ROWS_RULE_ID,
    _uk_schedule_list_entry_table_payload,
    _uk_schedule_table_end_rows_selector,
)
from lawvm.uk_legislation.uk_grafter import _clean_num, _LEG_NS, _parse_section
from lawvm.uk_legislation.witness_builders import (
    _uk_insertion_anchor_witness,
    _uk_text_rewrite_spec,
    _uk_target_expansion_witness,
    _uk_temporal_group_id,
)
from lawvm.uk_legislation.witness_sidecars import (
    _payload_with_rewrite_witness,
    _uk_lowered_op_provenance_tags,
)
from lawvm.uk_legislation.xml_helpers import _tag

from lawvm.uk_legislation.witnesses import (
    UKEffectWitness,
    UKLoweredOperationWitness,
    UKProvisionExtractionWitness,
)
from lawvm.core.quirks_disposition import QuirksDisposition


_UK_SCHEDULE_LIST_ENTRY_TABLE_ROWS_RULE_ID = "uk_effect_schedule_list_entry_table_rows_lowered"
_UK_SCHEDULE_WORDS_BEFORE_TABLE_SIBLING_DEFERRED_RULE_ID = (
    "uk_effect_schedule_words_before_table_substitution_sibling_deferred_to_base"
)
_UK_DIRECT_DEFINITION_LIST_END_PLACEMENT_FAMILY = (
    "definition_list_end_from_direct_source_row"
)


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


def try_lower_direct_definition_list_end_schedule_entry(
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
) -> UKScheduleBatchLoweringResult:
    if action != "insert" or heading_facet_target:
        return UKScheduleBatchLoweringResult(handled=False)
    if len(target.path) != 1 or str(target.path[0][0]).lower() != "schedule":
        return UKScheduleBatchLoweringResult(handled=False)
    source_row_id = (
        str(extracted_el.get("id") or extracted_el.get("Id") or "")
        if extracted_el is not None
        else ""
    )
    entry = direct_definition_entry_list_end_fragment(
        row_text=extracted_text or "",
        source_row_id=source_row_id,
    )
    if entry is None:
        return UKScheduleBatchLoweringResult(handled=False)
    inserted_text = str(entry.get("inserted_text") or "").strip()
    if not inserted_text:
        return UKScheduleBatchLoweringResult(handled=True)
    selector = {
        "rule_id": _UK_DIRECT_DEFINITION_ENTRY_LIST_END_INSERT_RULE_ID,
        "direction": "end",
        "anchor_text": "",
        "inserted_text": inserted_text,
        "target_ref": t_str,
        "target": str(target),
        "placement_family": _UK_DIRECT_DEFINITION_LIST_END_PLACEMENT_FAMILY,
        "source_row_id": source_row_id,
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
    _append_uk_effect_lowering_observation(
        lowering_rejections_out,
        rule_id=_UK_DIRECT_DEFINITION_ENTRY_LIST_END_INSERT_RULE_ID,
        family="definition_entry_elaboration",
        reason_code="direct_definition_list_end_insert_structural_list_end",
        reason=(
            "UK affecting source row directly inserts definition entries at "
            "the end of a schedule definition-list surface. Lowering emits a "
            "typed schedule-entry insert, and replay must prove direct "
            "schedule-entry children before mutating the target."
        ),
        effect=effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        detail={
            **selector,
            "source_row_text": str(entry.get("source_row_text") or ""),
        },
    )
    payload_node = IRNode(
        kind=IRNodeKind.SCHEDULE_ENTRY,
        label=None,
        text=inserted_text,
        attrs={
            "source_rule_id": "uk_direct_definition_list_end_insert_payload",
            "anchor_direction": "end",
            "placement_family": _UK_DIRECT_DEFINITION_LIST_END_PLACEMENT_FAMILY,
            "source_row_id": source_row_id,
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
                    f"{_NOTE_SCHEDULE_LIST_ENTRY_SELECTOR}"
                    f"{json.dumps(selector, ensure_ascii=False)}"
                ),
                witness_rule_id=_UK_DIRECT_DEFINITION_ENTRY_LIST_END_INSERT_RULE_ID,
            ),
        ),
    )


def _try_lower_schedule_words_before_table_substitution(
    *,
    effect: UKEffectRecord,
    action: str,
    effect_type: str,
    t_str: str,
    target: LegalAddress,
    extracted_el: Optional[ET._Element],
    extracted_text: Optional[str],
    sequence: int,
    effect_witness: UKEffectWitness,
    extraction_witness: UKProvisionExtractionWitness,
    original_targets_str: list[str],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> UKScheduleBatchLoweringResult:
    """Lower a schedule paragraph "words before the table substitute" formula.

    UK affecting source sometimes replaces the lead-in text of a schedule
    paragraph that carries a table, and simultaneously inserts lettered sibling
    paragraphs that themselves contain table-relative instructions.  The
    whole BlockAmendment payload is a sequence of P1 paragraphs (target,
    target+A, target+B, ...).  This rule lowers that sequence as a replace on
    the original paragraph plus bounded inserts after it, instead of letting
    inner "omit" / "insert" text be misread as schedule-list-entry repeals.
    """
    if extracted_el is None:
        return UKScheduleBatchLoweringResult(handled=False)
    if _addr_container(target) != "schedule" or _addr_leaf_kind(target) != "paragraph":
        return UKScheduleBatchLoweringResult(handled=False)
    text = " ".join((extracted_text or "").split()).lower()
    if "for the words before the table substitute" not in text:
        return UKScheduleBatchLoweringResult(handled=False)

    block_amendment: Optional[ET._Element] = None
    for el in extracted_el.iter():
        if _tag(el) == "BlockAmendment":
            block_amendment = el
            break
    if block_amendment is None:
        return UKScheduleBatchLoweringResult(handled=False)

    schedule_label = _clean_num(_addr_field(target, "schedule") or "")
    target_label = _clean_num(_addr_leaf_label(target) or "")
    if not schedule_label or not target_label:
        return UKScheduleBatchLoweringResult(handled=False)

    ordered_labels: list[str] = []
    found: dict[str, ET._Element] = {}
    for child in list(block_amendment):
        if _tag(child) != "P1":
            continue
        pnumber = child.find(f"./{{{_LEG_NS}}}Pnumber")
        if pnumber is None:
            continue
        label = _clean_num((pnumber.text or "").strip())
        if not label:
            continue
        if label not in found:
            ordered_labels.append(label)
        found[label] = child

    if not ordered_labels or target_label != ordered_labels[0]:
        # The expanded sibling targets (e.g. 161A, 161B) are inserted by the
        # base-target effect lowering (which lowers ordered_labels[0] into a
        # replace plus the chained sibling inserts). A separate per-target call
        # for one of those siblings must NOT lower it again; we consume it here.
        # Record a typed deferral observation so the consumed sibling effect is
        # visible in the lowering census rather than dropped silently, and so the
        # deferral can be audited against the base-target effect's ops.
        if not ordered_labels:
            # The block carries no labelled P1 paragraphs at all: the
            # words-before-table shape is unconfirmed, so there is no base-target
            # effect to defer to. Surface this as a finding rather than silently
            # consuming the row.
            _append_uk_effect_lowering_observation(
                lowering_rejections_out,
                rule_id=_UK_SCHEDULE_WORDS_BEFORE_TABLE_SIBLING_DEFERRED_RULE_ID,
                family="schedule_words_before_table",
                reason_code="words_before_table_block_without_labelled_paragraphs",
                reason=(
                    "A schedule 'words before the table substitute' formula was "
                    "detected but its BlockAmendment carries no labelled P1 "
                    "paragraphs; the row is consumed without lowering and there is "
                    "no base-target effect to defer to, so it is surfaced for "
                    "review rather than dropped silently."
                ),
                effect=effect,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                detail={
                    "target_ref": t_str,
                    "target": str(target),
                    "schedule_label": schedule_label,
                    "target_label": target_label,
                    "ordered_labels": list(ordered_labels),
                    "deferred_to_base_target": False,
                    "strict_disposition": "record",
                    "quirks_disposition": QuirksDisposition.SKIP,
                },
            )
            return UKScheduleBatchLoweringResult(handled=True)
        base_label = ordered_labels[0]
        _append_uk_effect_lowering_observation(
            lowering_rejections_out,
            rule_id=_UK_SCHEDULE_WORDS_BEFORE_TABLE_SIBLING_DEFERRED_RULE_ID,
            family="schedule_words_before_table",
            reason_code="sibling_insert_deferred_to_base_target_effect",
            reason=(
                "A sibling schedule paragraph (e.g. 161A/161B) of a "
                "'words before the table substitute' block is consumed without "
                "separate lowering because the base-target effect lowers the "
                "whole P1 series (replace on the base paragraph plus chained "
                "sibling inserts). The deferral is recorded so it is visible in "
                "the census and can be cross-checked against the base-target "
                "effect's emitted inserts rather than dropped silently."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                "target_ref": t_str,
                "target": str(target),
                "schedule_label": schedule_label,
                "deferred_sibling_label": target_label,
                "base_target_label": base_label,
                "ordered_labels": list(ordered_labels),
                "deferred_to_base_target": True,
                "strict_disposition": "record",
                "quirks_disposition": QuirksDisposition.SKIP,
            },
        )
        return UKScheduleBatchLoweringResult(handled=True)

    schedule_root = f"schedule-{schedule_label}"

    def _build_payload_node(p1_el: ET._Element, para_label: str) -> Optional[IRNode]:
        para_addr = LegalAddress(
            path=(*target.path[:-1], ("paragraph", para_label)),
            special=None,
        )
        node = _parse_section(p1_el, "schedule", force_active=True, pit_date=None, is_eur=False)
        if node is None:
            return None
        # PR2 (audit XJUR-02 / AGENTS.md §2.3): no in-place mutation of the
        # IRNode returned by ``_parse_section`` (post-N3d-Sub-PR-A: that helper
        # already builds ``IRNode`` directly); build a new attrs dict and return
        # a fresh node via ``dataclasses.replace``.
        node = dc_replace(
            node,
            attrs={
                **dict(node.attrs),
                "eId": f"{schedule_root}-paragraph-{_clean_num(para_label)}",
                "source_rule_id": _UK_SCHEDULE_WORDS_BEFORE_TABLE_SUBSTITUTION_RULE_ID,
            },
        )
        node = _synthesize_payload_descendant_eids(
            node,
            target=para_addr,
            effect=effect,
            lowering_records_out=lowering_rejections_out,
            allow_payload_identity_synthesis=True,
        )
        # Sub-PR F (mutable_ir Wave N3d): ``_synthesize_payload_descendant_eids``
        # is now typed ``IRNode -> IRNode`` (the ``UKMutableNode`` shadow was
        # deleted) and the ``_parse_section`` upstream has built ``IRNode``
        # directly since Sub-PR A, so the returned node is already a frozen
        # ``IRNode`` and no boundary converter is required.
        return node

    lowered_ops: list[LegalOperation] = []
    replace_payload = _build_payload_node(found[target_label], target_label)
    if replace_payload is None:
        return UKScheduleBatchLoweringResult(handled=False)
    lowered_ops.append(
        _build_schedule_payload_op(
            effect=effect,
            sequence=sequence,
            action=StructuralAction.REPLACE,
            target=target,
            payload=replace_payload,
            effect_witness=effect_witness,
            extraction_witness=extraction_witness,
            original_targets_str=original_targets_str,
            t_str=t_str,
            provenance_note=json.dumps(
                {
                    "rule_id": _UK_SCHEDULE_WORDS_BEFORE_TABLE_SUBSTITUTION_RULE_ID,
                    "target_ref": t_str,
                    "target": str(target),
                    "schedule_root": schedule_root,
                    "replaced_paragraph": target_label,
                },
                ensure_ascii=False,
            ),
            witness_rule_id=_UK_SCHEDULE_WORDS_BEFORE_TABLE_SUBSTITUTION_RULE_ID,
        )
    )

    # Insert target+A and target+B after the preceding sibling, chaining anchors.
    prev_eid = f"{schedule_root}-paragraph-{_clean_num(target_label)}"
    for insert_label in ordered_labels[1:]:
        if insert_label not in found:
            continue
        insert_payload = _build_payload_node(found[insert_label], insert_label)
        if insert_payload is None:
            continue
        insert_target = LegalAddress(
            path=(*target.path[:-1], ("paragraph", insert_label)),
            special=None,
        )
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
        insertion_anchor_witness = _uk_insertion_anchor_witness(
            preceding_eid=prev_eid,
            following_eid=None,
        )
        lowered_witness = UKLoweredOperationWitness(
            op_id=effect.effect_id,
            sequence=sequence,
            action=StructuralAction.INSERT,
            target=insert_target,
            payload=insert_payload,
            source=src,
            effect_witness=effect_witness,
            extraction_witness=extraction_witness,
            target_expansion_witness=target_expansion_witness,
            text_rewrite_witness=None,
            insertion_anchor_witness=insertion_anchor_witness,
        )
        lowered_ops.append(
            LegalOperation(
                op_id=lowered_witness.op_id,
                sequence=lowered_witness.sequence,
                action=lowered_witness.action,
                target=lowered_witness.target,
                payload=_payload_with_rewrite_witness(
                    insert_payload,
                    lowered_witness,
                ),
                source=src,
                group_id=_uk_temporal_group_id(effect),
                provenance_tags=(
                    *_uk_lowered_op_provenance_tags(lowered_witness),
                    json.dumps(
                        {
                            "rule_id": _UK_SCHEDULE_WORDS_BEFORE_TABLE_SUBSTITUTION_RULE_ID,
                            "target_ref": t_str,
                            "inserted_label": insert_label,
                            "preceding_eid": prev_eid,
                        },
                        ensure_ascii=False,
                    ),
                ),
                witness_rule_id=_UK_SCHEDULE_WORDS_BEFORE_TABLE_SUBSTITUTION_RULE_ID,
            )
        )
        prev_eid = f"{schedule_root}-paragraph-{_clean_num(insert_label)}"

    _append_uk_effect_lowering_observation(
        lowering_rejections_out,
        rule_id=_UK_SCHEDULE_WORDS_BEFORE_TABLE_SUBSTITUTION_RULE_ID,
        family="source_schedule_paragraph_elaboration",
        reason_code="schedule_paragraph_words_before_table_substitution_lowered",
        reason=(
            "UK schedule paragraph substitution formula replaces the lead-in text "
            "before a table and inserts lettered sibling paragraphs. Lowering emits "
            "a source-backed replace on the original paragraph and chained inserts "
            "for the new siblings, rather than treating inner 'omit'/'insert' text "
            "as schedule-list-entry repeals."
        ),
        effect=effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        detail={
            "target_ref": t_str,
            "target": str(target),
            "schedule_root": schedule_root,
            "replaced_paragraph": target_label,
            "inserted_labels": list(ordered_labels[1:]),
            "emitted_op_count": len(lowered_ops),
        },
    )
    return UKScheduleBatchLoweringResult(handled=True, ops=tuple(lowered_ops))


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
    words_before_table_result = _try_lower_schedule_words_before_table_substitution(
        effect=effect,
        action=action,
        effect_type=effect_type,
        t_str=t_str,
        target=target,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        sequence=sequence,
        effect_witness=effect_witness,
        extraction_witness=extraction_witness,
        original_targets_str=original_targets_str,
        lowering_rejections_out=lowering_rejections_out,
    )
    if words_before_table_result.handled:
        return words_before_table_result

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

    source_previous_that_entry_insert = (
        _source_previous_that_entry_insert_context(extracted_el=extracted_el)
        if schedule_list_entry_selector is None and action == "insert" and not heading_facet_target
        else None
    )
    if source_previous_that_entry_insert is not None:
        schedule_list_entry_selector = _uk_schedule_list_entry_insert_selector(
            target_ref=t_str,
            target=target,
            extracted_text=source_previous_that_entry_insert["combined_text"],
            allow_local_paragraph_carrier=True,
        )
        if schedule_list_entry_selector is not None:
            schedule_list_entry_selector = {
                **schedule_list_entry_selector,
                "source_anchor_form": source_previous_that_entry_insert[
                    "source_anchor_form"
                ],
                "source_antecedent_id": source_previous_that_entry_insert[
                    "source_antecedent_id"
                ],
                "source_antecedent_text": source_previous_that_entry_insert[
                    "source_antecedent_text"
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
            reason_code=(
                "explicit_schedule_list_entry_beginning"
                if str(schedule_list_entry_selector.get("direction") or "") == "beginning"
                else "explicit_schedule_list_entry_anchor"
            ),
            reason=(
                "UK list-entry insertion lowered as a typed schedule-entry "
                "sibling insert; replay must resolve the source-owned placement "
                "boundary before mutating direct list children."
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
            if direction == "beginning":
                entry_selector["beginning_insert_index"] = entry_index
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
                    **(
                        {"beginning_insert_index": str(entry_index)}
                        if direction == "beginning"
                        else {}
                    ),
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

    connector_substitution_selector = (
        _uk_connector_preceding_child_list_entry_substitution_selector(
            target_ref=t_str,
            target=target,
            extracted_text=extracted_text,
        )
        if action == "replace" or effect_type in {"words substituted", "word substituted"}
        else None
    )
    if connector_substitution_selector is not None:
        connector_text = str(connector_substitution_selector["connector_text"])
        anchor_child_kind = str(connector_substitution_selector["anchor_child_kind"])
        anchor_child_label = str(connector_substitution_selector["anchor_child_label"])
        connector_selector = (
            f"TEXT_WORD_{connector_text}_IMMEDIATELY_PRECEDING_"
            f"{anchor_child_kind}_{anchor_child_label}"
        )
        connector_patch = TextPatchSpec(
            kind=TextPatchKindEnum.DELETE,
            selector=TextSelector(match_text=connector_selector, occurrence=0),
        )
        connector_rewrite = _uk_text_rewrite_spec(
            fragment_subs=[
                {
                    "original": connector_selector,
                    "replacement": "",
                    "rule_id": _UK_CONNECTOR_PRECEDING_CHILD_LIST_ENTRY_SUBSTITUTION_RULE_ID,
                    "source_anchor_form": "connector_preceding_child",
                }
            ],
            text_patch=connector_patch,
            op_text_match=connector_selector,
            op_text_replacement="",
            op_text_occurrence=0,
        )
        src = OperationSource(
            statute_id=effect.affecting_act_id,
            title=effect.affecting_title,
            effective=effect_witness.applicability.effective_date or "",
            raw_text=extraction_witness.extracted_text,
        )
        connector_witness = UKLoweredOperationWitness(
            op_id=f"{effect.effect_id}_connector_preceding_child",
            sequence=sequence,
            action=StructuralAction.TEXT_PATCH,
            target=target,
            payload=None,
            source=src,
            effect_witness=effect_witness,
            extraction_witness=extraction_witness,
            target_expansion_witness=_uk_target_expansion_witness(
                t_str,
                [t_str],
                original_targets_str=original_targets_str,
            ),
            text_rewrite_witness=connector_rewrite,
            insertion_anchor_witness=None,
        )
        connector_op = LegalOperation(
            op_id=connector_witness.op_id,
            sequence=connector_witness.sequence,
            action=connector_witness.action,
            target=connector_witness.target,
            payload=None,
            source=src,
            group_id=_uk_temporal_group_id(effect),
            provenance_tags=_uk_lowered_op_provenance_tags(connector_witness),
            text_patch=connector_patch,
            witness_rule_id=_UK_CONNECTOR_PRECEDING_CHILD_LIST_ENTRY_SUBSTITUTION_RULE_ID,
        )
        _append_uk_effect_lowering_observation(
            lowering_rejections_out,
            rule_id=_UK_CONNECTOR_PRECEDING_CHILD_LIST_ENTRY_SUBSTITUTION_RULE_ID,
            family="source_schedule_list_entry_elaboration",
            reason_code="connector_preceding_child_substitution_split",
            reason=(
                "UK source substituted a connector immediately preceding a "
                "child with a labelled list entry. Lowering splits the source "
                "claim into an explicit contextual connector repeal and a "
                "bounded child-boundary insert; replay must prove both before "
                "mutating the target."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail=dict(connector_substitution_selector),
        )
        payload_kind = {
            "paragraph": IRNodeKind.PARAGRAPH,
            "subparagraph": IRNodeKind.SUBPARAGRAPH,
            "subsection": IRNodeKind.SUBSECTION,
        }.get(anchor_child_kind, IRNodeKind.SCHEDULE_ENTRY)
        payload_node = IRNode(
            kind=payload_kind,
            label=str(connector_substitution_selector["inserted_label"]),
            text=str(connector_substitution_selector["inserted_text"]),
            attrs={
                "source_rule_id": "uk_connector_preceding_child_list_entry_payload",
                "anchor_direction": "before",
                "source_anchor_form": "connector_preceding_child",
                "source_anchor_child_kind": anchor_child_kind,
                "source_anchor_child_label": anchor_child_label,
                "source_heading_text": str(connector_substitution_selector["heading_text"]),
            },
        )
        insert_op = _build_schedule_payload_op(
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
                f"{json.dumps(connector_substitution_selector, ensure_ascii=False)}"
            ),
            witness_rule_id=_UK_CONNECTOR_PRECEDING_CHILD_LIST_ENTRY_SUBSTITUTION_RULE_ID,
        )
        return UKScheduleBatchLoweringResult(
            handled=True,
            ops=(connector_op, insert_op),
        )

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
            "replacement_texts": tuple(
                str(text)
                for text in schedule_list_entry_replace_selector.get("replacement_texts", ())
            ),
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

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import replace
from typing import Any, Optional

from lawvm.core.ir import (
    IRNode,
    LegalAddress,
    LegalOperation,
    OperationSource,
    TextPatchSpec,
    TextSelector,
)
from lawvm.core.ir_helpers import irnode_from_dict
from lawvm.core.semantic_types import IRNodeKind, StructuralAction, TextPatchKindEnum
from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.lowering_records import _append_uk_effect_lowering_observation
from lawvm.uk_legislation.metadata_rewrites import UKMetadataRenumberTargets
from lawvm.uk_legislation.source_definition_fragments import (
    UK_DEFINITION_CHILD_RANGE_SUBSTITUTION_RULE_ID,
)
from lawvm.uk_legislation.source_parent_payloads import (
    UK_AFTER_SECTION_SUBSECTION_RANGE_INSERT_BLOCK_AMENDMENT_RULE_ID,
    UK_AT_END_SECTION_SUBSECTION_INSERT_BLOCK_AMENDMENT_RULE_ID,
    UK_AFTER_PARAGRAPH_INSERT_LABELLED_SERIES_RULE_ID,
    UK_AFTER_PARAGRAPH_INSERT_SINGLE_LABEL_RULE_ID,
    UK_AFTER_PARAGRAPH_INSERT_CONNECTOR_SIBLING_RULE_ID,
    UK_SOURCE_CARRIED_STRUCTURED_TAIL_SUBSTITUTION_RULE_ID,
)
from lawvm.uk_legislation.target_anchors import _target_anchor_eid
from lawvm.uk_legislation.target_parser import _parse_affected_target
from lawvm.uk_legislation.uk_grafter import _parse_p2
from lawvm.uk_legislation.witness_builders import (
    _uk_insertion_anchor_witness,
    _uk_target_expansion_witness,
    _uk_temporal_group_id,
    _uk_text_rewrite_spec,
)
from lawvm.uk_legislation.xml_helpers import _direct_structural_num, _tag


def _strip_payload_leading_label(node: IRNode) -> IRNode:
    label = str(node.label or "").strip()
    text = str(node.text or "")
    if label and text.lower().startswith(label.lower()) and len(text) > len(label):
        tail = text[len(label) :]
        if tail[:1].isspace():
            return replace(node, text=tail.strip())
    return node
from lawvm.uk_legislation.witness_sidecars import (
    _payload_with_rewrite_witness,
    _uk_lowered_op_provenance_tags,
)
from lawvm.uk_legislation.witnesses import (
    UKEffectWitness,
    UKLoweredOperationWitness,
    UKProvisionExtractionWitness,
)


def lower_uk_metadata_renumber_effect(  # noqa: PLR0913
    *,
    effect: UKEffectRecord,
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    sequence: int,
    metadata_renumber_targets: UKMetadataRenumberTargets,
    effect_witness: UKEffectWitness,
    extraction_witness: UKProvisionExtractionWitness,
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> list[LegalOperation]:
    """Lower a source-backed UK metadata renumber into a canonical op."""
    source_target = metadata_renumber_targets.source_target
    destination = metadata_renumber_targets.destination
    _append_uk_effect_lowering_observation(
        lowering_rejections_out,
        rule_id=metadata_renumber_targets.rule_id,
        family="lineage_normalization",
        reason_code=metadata_renumber_targets.reason_code,
        reason=metadata_renumber_targets.reason,
        effect=effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        detail={
            "source_target": str(source_target),
            "destination": str(destination),
            "metadata_destination": (
                str(metadata_renumber_targets.metadata_destination)
                if metadata_renumber_targets.metadata_destination is not None
                else ""
            ),
            "affected_provisions": effect.affected_provisions,
        },
    )
    src = OperationSource(
        statute_id=effect.affecting_act_id,
        title=effect.affecting_title,
        effective=effect_witness.applicability.effective_date or "",
        raw_text=extraction_witness.extracted_text,
    )
    target_expansion_witness = _uk_target_expansion_witness(
        effect.affected_provisions,
        [effect.affected_provisions],
        original_targets_str=[effect.affected_provisions],
    )
    lowered_witness = UKLoweredOperationWitness(
        op_id=effect.effect_id,
        sequence=sequence,
        action=StructuralAction.RENUMBER,
        target=source_target,
        payload=None,
        source=src,
        effect_witness=effect_witness,
        extraction_witness=extraction_witness,
        target_expansion_witness=target_expansion_witness,
        text_rewrite_witness=None,
        insertion_anchor_witness=None,
    )
    return [
        LegalOperation(
            op_id=lowered_witness.op_id,
            sequence=lowered_witness.sequence,
            action=StructuralAction.RENUMBER,
            target=source_target,
            destination=destination,
            source=src,
            group_id=_uk_temporal_group_id(effect),
            provenance_tags=_uk_lowered_op_provenance_tags(lowered_witness),
            witness_rule_id=metadata_renumber_targets.rule_id,
        )
    ]


def lower_uk_after_paragraph_insert_labelled_series(  # noqa: PLR0913
    *,
    effect: UKEffectRecord,
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    sequence: int,
    after_paragraph_series: dict[str, Any],
    effect_witness: UKEffectWitness,
    extraction_witness: UKProvisionExtractionWitness,
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> list[LegalOperation]:
    """Lower a source-owned semicolon patch plus labelled paragraph inserts."""
    _append_uk_effect_lowering_observation(
        lowering_rejections_out,
        rule_id=UK_AFTER_PARAGRAPH_INSERT_LABELLED_SERIES_RULE_ID,
        family="source_context_elaboration",
        reason_code="after_paragraph_insert_semicolon_and_labelled_series",
        reason=(
            "UK source row inserts a semicolon after an existing paragraph "
            "and then a contiguous labelled paragraph series; lowering "
            "separates the punctuation patch from the inserted legal "
            "siblings instead of treating the instruction text as one "
            "payload."
        ),
        effect=effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        detail={
            key: value
            for key, value in after_paragraph_series.items()
            if key != "rule_id"
        },
    )
    src = OperationSource(
        statute_id=effect.affecting_act_id,
        title=effect.affecting_title,
        effective=effect_witness.applicability.effective_date or "",
        raw_text=extraction_witness.extracted_text,
    )
    semicolon_target = LegalAddress(
        path=(
            ("section", str(after_paragraph_series["section"])),
            ("subsection", str(after_paragraph_series["subsection"])),
            ("paragraph", str(after_paragraph_series["anchor_label"])),
        )
    )
    semicolon_patch = TextPatchSpec(
        kind=TextPatchKindEnum.APPEND,
        selector=TextSelector(match_text="TEXT_END", occurrence=0),
        replacement=";",
    )
    semicolon_rewrite = _uk_text_rewrite_spec(
        fragment_subs=[
            {
                "original": "TEXT_END",
                "replacement": ";",
                "rule_id": UK_AFTER_PARAGRAPH_INSERT_LABELLED_SERIES_RULE_ID,
            }
        ],
        text_patch=semicolon_patch,
        op_text_match="TEXT_END",
        op_text_replacement=";",
        op_text_occurrence=0,
    )
    semicolon_witness = UKLoweredOperationWitness(
        op_id=f"{effect.effect_id}_semicolon",
        sequence=sequence,
        action=StructuralAction.TEXT_REPLACE,
        target=semicolon_target,
        payload=None,
        source=src,
        effect_witness=effect_witness,
        extraction_witness=extraction_witness,
        target_expansion_witness=_uk_target_expansion_witness(
            effect.affected_provisions,
            [str(after_paragraph_series["semicolon_target"])],
            original_targets_str=[effect.affected_provisions],
        ),
        text_rewrite_witness=semicolon_rewrite,
        insertion_anchor_witness=None,
    )
    custom_ops = [
        LegalOperation(
            op_id=semicolon_witness.op_id,
            sequence=semicolon_witness.sequence,
            action=semicolon_witness.action,
            target=semicolon_target,
            payload=None,
            source=src,
            group_id=_uk_temporal_group_id(effect),
            provenance_tags=_uk_lowered_op_provenance_tags(semicolon_witness),
            text_patch=semicolon_patch,
            witness_rule_id=UK_AFTER_PARAGRAPH_INSERT_LABELLED_SERIES_RULE_ID,
        )
    ]
    preceding_target = semicolon_target
    for payload_index, payload in enumerate(after_paragraph_series["payloads"]):
        payload_target = _parse_affected_target(str(payload["target_ref"]))
        payload_node = IRNode(
            kind=IRNodeKind.PARAGRAPH,
            label=str(payload["label"]),
            text=str(payload["text"]),
        )
        insert_witness = UKLoweredOperationWitness(
            op_id=f"{effect.effect_id}_insert_{payload_index}",
            sequence=sequence,
            action=StructuralAction.INSERT,
            target=payload_target,
            payload=payload_node,
            source=src,
            effect_witness=effect_witness,
            extraction_witness=extraction_witness,
            target_expansion_witness=_uk_target_expansion_witness(
                effect.affected_provisions,
                [str(payload["target_ref"])],
                original_targets_str=[effect.affected_provisions],
            ),
            text_rewrite_witness=None,
            insertion_anchor_witness=_uk_insertion_anchor_witness(
                _target_anchor_eid(preceding_target),
                anchor_source=UK_AFTER_PARAGRAPH_INSERT_LABELLED_SERIES_RULE_ID,
            ),
        )
        custom_ops.append(
            LegalOperation(
                op_id=insert_witness.op_id,
                sequence=insert_witness.sequence,
                action=insert_witness.action,
                target=payload_target,
                payload=_payload_with_rewrite_witness(payload_node, insert_witness),
                source=src,
                group_id=_uk_temporal_group_id(effect),
                provenance_tags=_uk_lowered_op_provenance_tags(insert_witness),
                witness_rule_id=UK_AFTER_PARAGRAPH_INSERT_LABELLED_SERIES_RULE_ID,
            )
        )
        preceding_target = payload_target
    return custom_ops


def lower_uk_after_paragraph_insert_connector_sibling(  # noqa: PLR0913
    *,
    effect: UKEffectRecord,
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    sequence: int,
    after_paragraph_connector: dict[str, Any],
    effect_witness: UKEffectWitness,
    extraction_witness: UKProvisionExtractionWitness,
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> list[LegalOperation]:
    """Lower a connector-tail append plus one labelled paragraph insert."""
    rule_id = UK_AFTER_PARAGRAPH_INSERT_CONNECTOR_SIBLING_RULE_ID
    _append_uk_effect_lowering_observation(
        lowering_rejections_out,
        rule_id=rule_id,
        family="source_context_elaboration",
        reason_code="after_paragraph_insert_connector_sibling",
        reason=(
            "UK source row inserts connector text at an existing paragraph "
            "tail and a new labelled sibling; lowering separates the tail "
            "text patch from the structural insert."
        ),
        effect=effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        detail={
            key: value
            for key, value in after_paragraph_connector.items()
            if key != "rule_id"
        },
    )
    src = OperationSource(
        statute_id=effect.affecting_act_id,
        title=effect.affecting_title,
        effective=effect_witness.applicability.effective_date or "",
        raw_text=extraction_witness.extracted_text,
    )
    anchor_target = LegalAddress(
        path=(
            ("section", str(after_paragraph_connector["section"])),
            ("subsection", str(after_paragraph_connector["subsection"])),
            ("paragraph", str(after_paragraph_connector["anchor_label"])),
        )
    )
    anchor_patch_text = str(after_paragraph_connector["anchor_patch"])
    anchor_patch = TextPatchSpec(
        kind=TextPatchKindEnum.APPEND,
        selector=TextSelector(match_text="TEXT_END", occurrence=0),
        replacement=anchor_patch_text,
    )
    anchor_rewrite = _uk_text_rewrite_spec(
        fragment_subs=[
            {
                "original": "TEXT_END",
                "replacement": anchor_patch_text,
                "rule_id": rule_id,
            }
        ],
        text_patch=anchor_patch,
        op_text_match="TEXT_END",
        op_text_replacement=anchor_patch_text,
        op_text_occurrence=0,
    )
    anchor_witness = UKLoweredOperationWitness(
        op_id=f"{effect.effect_id}_anchor_tail",
        sequence=sequence,
        action=StructuralAction.TEXT_REPLACE,
        target=anchor_target,
        payload=None,
        source=src,
        effect_witness=effect_witness,
        extraction_witness=extraction_witness,
        target_expansion_witness=_uk_target_expansion_witness(
            effect.affected_provisions,
            [str(after_paragraph_connector["anchor_target"])],
            original_targets_str=[effect.affected_provisions],
        ),
        text_rewrite_witness=anchor_rewrite,
        insertion_anchor_witness=None,
    )
    payload = after_paragraph_connector["payload"]
    payload_target = _parse_affected_target(str(payload["target_ref"]))
    payload_node = IRNode(
        kind=IRNodeKind.PARAGRAPH,
        label=str(payload["label"]),
        text=str(payload["text"]),
    )
    insert_witness = UKLoweredOperationWitness(
        op_id=f"{effect.effect_id}_insert",
        sequence=sequence,
        action=StructuralAction.INSERT,
        target=payload_target,
        payload=payload_node,
        source=src,
        effect_witness=effect_witness,
        extraction_witness=extraction_witness,
        target_expansion_witness=_uk_target_expansion_witness(
            effect.affected_provisions,
            [str(payload["target_ref"])],
            original_targets_str=[effect.affected_provisions],
        ),
        text_rewrite_witness=None,
        insertion_anchor_witness=_uk_insertion_anchor_witness(
            _target_anchor_eid(anchor_target),
            anchor_source=rule_id,
        ),
    )
    return [
        LegalOperation(
            op_id=anchor_witness.op_id,
            sequence=anchor_witness.sequence,
            action=anchor_witness.action,
            target=anchor_target,
            payload=None,
            source=src,
            group_id=_uk_temporal_group_id(effect),
            provenance_tags=_uk_lowered_op_provenance_tags(anchor_witness),
            text_patch=anchor_patch,
            witness_rule_id=rule_id,
        ),
        LegalOperation(
            op_id=insert_witness.op_id,
            sequence=insert_witness.sequence,
            action=insert_witness.action,
            target=payload_target,
            payload=_payload_with_rewrite_witness(payload_node, insert_witness),
            source=src,
            group_id=_uk_temporal_group_id(effect),
            provenance_tags=_uk_lowered_op_provenance_tags(insert_witness),
            witness_rule_id=rule_id,
        ),
    ]


def lower_uk_definition_child_range_substitution(  # noqa: PLR0913
    *,
    effect: UKEffectRecord,
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    sequence: int,
    definition_child_range: dict[str, Any],
    effect_witness: UKEffectWitness,
    extraction_witness: UKProvisionExtractionWitness,
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> list[LegalOperation]:
    """Lower same-label definition-child range substitutions into child rewrites."""
    rule_id = UK_DEFINITION_CHILD_RANGE_SUBSTITUTION_RULE_ID
    fragments = tuple(definition_child_range["fragments"])
    _append_uk_effect_lowering_observation(
        lowering_rejections_out,
        rule_id=rule_id,
        family="definition_entry_elaboration",
        reason_code="definition_child_range_same_label_payload",
        reason=(
            "UK source substitutes a range of definition child paragraphs "
            "with replacement children carrying the same labels; lowering "
            "emits bounded definition-child text replacements for each child "
            "instead of rewriting the parent provision."
        ),
        effect=effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        detail={
            key: value
            for key, value in definition_child_range.items()
            if key != "rule_id"
        },
    )
    src = OperationSource(
        statute_id=effect.affecting_act_id,
        title=effect.affecting_title,
        effective=effect_witness.applicability.effective_date or "",
        raw_text=extraction_witness.extracted_text,
    )
    target = _parse_affected_target(str(definition_child_range["target_ref"]))
    ops: list[LegalOperation] = []
    for index, fragment in enumerate(fragments):
        text_patch = TextPatchSpec(
            kind=TextPatchKindEnum.REPLACE,
            selector=TextSelector(match_text=str(fragment["original"]), occurrence=0),
            replacement=str(fragment["replacement"]),
        )
        rewrite = _uk_text_rewrite_spec(
            fragment_subs=[dict(fragment)],
            text_patch=text_patch,
            op_text_match=str(fragment["original"]),
            op_text_replacement=str(fragment["replacement"]),
            op_text_occurrence=0,
        )
        witness = UKLoweredOperationWitness(
            op_id=f"{effect.effect_id}_definition_child_{fragment['source_child_label']}_{index}",
            sequence=sequence,
            action=StructuralAction.TEXT_REPLACE,
            target=target,
            payload=None,
            source=src,
            effect_witness=effect_witness,
            extraction_witness=extraction_witness,
            target_expansion_witness=_uk_target_expansion_witness(
                effect.affected_provisions,
                [str(definition_child_range["target_ref"])],
                original_targets_str=[effect.affected_provisions],
            ),
            text_rewrite_witness=rewrite,
            insertion_anchor_witness=None,
        )
        ops.append(
            LegalOperation(
                op_id=witness.op_id,
                sequence=witness.sequence,
                action=witness.action,
                target=target,
                payload=None,
                source=src,
                group_id=_uk_temporal_group_id(effect),
                provenance_tags=_uk_lowered_op_provenance_tags(witness),
                text_patch=text_patch,
                witness_rule_id=rule_id,
            )
        )
    return ops


def lower_uk_after_paragraph_insert_single_label(  # noqa: PLR0913
    *,
    effect: UKEffectRecord,
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    sequence: int,
    after_paragraph_insert: dict[str, Any],
    effect_witness: UKEffectWitness,
    extraction_witness: UKProvisionExtractionWitness,
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> list[LegalOperation]:
    """Lower a source-owned single labelled paragraph insert."""
    rule_id = str(
        after_paragraph_insert.get("rule_id")
        or UK_AFTER_PARAGRAPH_INSERT_SINGLE_LABEL_RULE_ID
    )
    _append_uk_effect_lowering_observation(
        lowering_rejections_out,
        rule_id=rule_id,
        family="source_context_elaboration",
        reason_code="after_paragraph_insert_single_label",
        reason=(
            "UK source row inserts one labelled paragraph after an existing "
            "paragraph; lowering emits a typed sibling insert instead of "
            "treating the instruction prose as payload."
        ),
        effect=effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        detail={
            key: value
            for key, value in after_paragraph_insert.items()
            if key != "rule_id"
        },
    )
    src = OperationSource(
        statute_id=effect.affecting_act_id,
        title=effect.affecting_title,
        effective=effect_witness.applicability.effective_date or "",
        raw_text=extraction_witness.extracted_text,
    )
    payload = dict(after_paragraph_insert["payload"])
    payload_target = _parse_affected_target(str(payload["target_ref"]))
    if "kind" in payload:
        payload_node = irnode_from_dict(payload)
    else:
        payload_node = IRNode(
            kind=IRNodeKind.PARAGRAPH,
            label=str(payload["label"]),
            text=str(payload["text"]),
        )
    anchor_target = _parse_affected_target(
        f"s. {after_paragraph_insert['section']}({after_paragraph_insert['subsection']})"
        f"({after_paragraph_insert['anchor_label']})"
    )
    insert_witness = UKLoweredOperationWitness(
        op_id=effect.effect_id,
        sequence=sequence,
        action=StructuralAction.INSERT,
        target=payload_target,
        payload=payload_node,
        source=src,
        effect_witness=effect_witness,
        extraction_witness=extraction_witness,
        target_expansion_witness=_uk_target_expansion_witness(
            effect.affected_provisions,
            [str(payload["target_ref"])],
            original_targets_str=[effect.affected_provisions],
        ),
        text_rewrite_witness=None,
        insertion_anchor_witness=_uk_insertion_anchor_witness(
            _target_anchor_eid(anchor_target),
            anchor_source=rule_id,
        ),
    )
    return [
        LegalOperation(
            op_id=insert_witness.op_id,
            sequence=insert_witness.sequence,
            action=insert_witness.action,
            target=payload_target,
            payload=_payload_with_rewrite_witness(payload_node, insert_witness),
            source=src,
            group_id=_uk_temporal_group_id(effect),
            provenance_tags=_uk_lowered_op_provenance_tags(insert_witness),
            witness_rule_id=rule_id,
        )
    ]


def lower_uk_after_section_subsection_range_insert_block_amendment(  # noqa: PLR0913
    *,
    effect: UKEffectRecord,
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    sequence: int,
    after_section_subsection_range_insert: dict[str, Any],
    effect_witness: UKEffectWitness,
    extraction_witness: UKProvisionExtractionWitness,
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> list[LegalOperation]:
    """Lower a source-owned contiguous subsection range insert."""
    rule_id = str(
        after_section_subsection_range_insert.get("rule_id")
        or UK_AFTER_SECTION_SUBSECTION_RANGE_INSERT_BLOCK_AMENDMENT_RULE_ID
    )
    at_end_single = rule_id == UK_AT_END_SECTION_SUBSECTION_INSERT_BLOCK_AMENDMENT_RULE_ID
    _append_uk_effect_lowering_observation(
        lowering_rejections_out,
        rule_id=rule_id,
        family="source_context_elaboration",
        reason_code=(
            "at_end_section_subsection_insert_block_amendment"
            if at_end_single
            else "after_section_subsection_range_insert_block_amendment"
        ),
        reason=(
            "UK source row inserts labelled subsection payloads after an "
            "existing subsection; lowering emits typed sibling inserts "
            "instead of treating the instruction prose as the target payload."
        ),
        effect=effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        detail={
            key: value
            for key, value in after_section_subsection_range_insert.items()
            if key != "rule_id"
        },
    )
    if extracted_el is None:
        return []
    amendment = next(
        (
            candidate
            for candidate in extracted_el.iter()
            if _tag(candidate) == "BlockAmendment"
        ),
        None,
    )
    if amendment is None:
        return []
    payload_children = tuple(child for child in list(amendment) if _tag(child) == "P2")
    expected_labels = tuple(
        str(label) for label in after_section_subsection_range_insert["payload_labels"]
    )
    payload_by_label = {
        str(_direct_structural_num(child) or "").strip().strip("()").lower(): child
        for child in payload_children
    }
    if tuple(payload_by_label) != expected_labels:
        return []
    src = OperationSource(
        statute_id=effect.affecting_act_id,
        title=effect.affecting_title,
        effective=effect_witness.applicability.effective_date or "",
        raw_text=extraction_witness.extracted_text,
    )
    custom_ops: list[LegalOperation] = []
    preceding_target = _parse_affected_target(
        f"s. {after_section_subsection_range_insert['section']}"
        f"({after_section_subsection_range_insert['anchor_label']})"
    )
    for index, label in enumerate(expected_labels):
        payload_el = payload_by_label[label]
        payload_node = _strip_payload_leading_label(
            irnode_from_dict(
                _parse_p2(
                    payload_el,
                    "body",
                    force_active=True,
                    pit_date=None,
                ).to_dict()
            )
        )
        target_ref = f"s. {after_section_subsection_range_insert['section']}({label})"
        payload_target = _parse_affected_target(target_ref)
        insert_witness = UKLoweredOperationWitness(
            op_id=f"{effect.effect_id}_insert_{index}",
            sequence=sequence,
            action=StructuralAction.INSERT,
            target=payload_target,
            payload=payload_node,
            source=src,
            effect_witness=effect_witness,
            extraction_witness=extraction_witness,
            target_expansion_witness=_uk_target_expansion_witness(
                effect.affected_provisions,
                [target_ref],
                original_targets_str=[effect.affected_provisions],
            ),
            text_rewrite_witness=None,
            insertion_anchor_witness=_uk_insertion_anchor_witness(
                _target_anchor_eid(preceding_target),
                anchor_source=rule_id,
            ),
        )
        custom_ops.append(
            LegalOperation(
                op_id=insert_witness.op_id,
                sequence=insert_witness.sequence,
                action=insert_witness.action,
                target=payload_target,
                payload=_payload_with_rewrite_witness(payload_node, insert_witness),
                source=src,
                group_id=_uk_temporal_group_id(effect),
                provenance_tags=_uk_lowered_op_provenance_tags(insert_witness),
                witness_rule_id=rule_id,
            )
        )
        preceding_target = payload_target
    return custom_ops


def lower_uk_source_carried_structured_tail_substitution(  # noqa: PLR0913
    *,
    effect: UKEffectRecord,
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    sequence: int,
    structured_tail_substitution: dict[str, Any],
    effect_witness: UKEffectWitness,
    extraction_witness: UKProvisionExtractionWitness,
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> list[LegalOperation]:
    """Lower `from X to the end` substitutions carrying labelled children."""
    _append_uk_effect_lowering_observation(
        lowering_rejections_out,
        rule_id=UK_SOURCE_CARRIED_STRUCTURED_TAIL_SUBSTITUTION_RULE_ID,
        family="source_context_elaboration",
        reason_code="source_carried_structured_tail_substitution",
        reason=(
            "UK source substitutes a parent text tail with visibly labelled "
            "child paragraphs; lowering emits child replace operations and "
            "records the parent trim selector instead of flattening the child "
            "payload into host text."
        ),
        effect=effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        detail={
            key: value
            for key, value in structured_tail_substitution.items()
            if key != "rule_id"
        },
    )
    src = OperationSource(
        statute_id=effect.affecting_act_id,
        title=effect.affecting_title,
        effective=effect_witness.applicability.effective_date or "",
        raw_text=extraction_witness.extracted_text,
    )
    ops: list[LegalOperation] = []
    for payload_index, payload in enumerate(structured_tail_substitution["payloads"]):
        payload_target = _parse_affected_target(str(payload["target_ref"]))
        payload_kind = (
            IRNodeKind.ITEM
            if str(structured_tail_substitution.get("payload_kind") or "") == "item"
            else IRNodeKind.PARAGRAPH
        )
        payload_node = IRNode(
            kind=payload_kind,
            label=str(payload["label"]),
            text=str(payload["text"]),
        )
        lowered_witness = UKLoweredOperationWitness(
            op_id=f"{effect.effect_id}_structured_tail_{payload_index}",
            sequence=sequence,
            action=StructuralAction.REPLACE,
            target=payload_target,
            payload=payload_node,
            source=src,
            effect_witness=effect_witness,
            extraction_witness=extraction_witness,
            target_expansion_witness=_uk_target_expansion_witness(
                effect.affected_provisions,
                [str(payload["target_ref"])],
                original_targets_str=[effect.affected_provisions],
            ),
            text_rewrite_witness=None,
            insertion_anchor_witness=None,
        )
        ops.append(
            LegalOperation(
                op_id=lowered_witness.op_id,
                sequence=lowered_witness.sequence,
                action=lowered_witness.action,
                target=payload_target,
                payload=_payload_with_rewrite_witness(payload_node, lowered_witness),
                source=src,
                group_id=_uk_temporal_group_id(effect),
                provenance_tags=_uk_lowered_op_provenance_tags(lowered_witness),
                witness_rule_id=UK_SOURCE_CARRIED_STRUCTURED_TAIL_SUBSTITUTION_RULE_ID,
            )
        )
    return ops

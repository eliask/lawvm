"""Post-loop helpers for UK effect lowering."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any, Optional

from lawvm.core.ir import LegalAddress, LegalOperation, OperationSource
from lawvm.core.semantic_types import StructuralAction
from lawvm.uk_legislation.canonicalize import canonicalize_uk_address
from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.lowering_records import _append_uk_effect_lowering_rejection
from lawvm.uk_legislation.source_definition_fragments import (
    _looks_like_appropriate_place_definition_entry_insert_text,
)
from lawvm.uk_legislation.source_payload_elaboration import (
    _extract_crossheading_payload_from_extracted,
)
from lawvm.uk_legislation.source_parent_payloads import (
    UK_SOURCE_PARENT_SUBSTITUTION_RANGE_PAYLOAD_RULE_ID as _UK_SOURCE_PARENT_SUBSTITUTION_RANGE_PAYLOAD_RULE_ID,
)
from lawvm.uk_legislation.target_parser import _parse_affected_target
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


def append_unlowered_overlap_substitution_rejection(
    lowering_rejections_out: Optional[list[dict[str, Any]]],
    *,
    effect: UKEffectRecord,
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    effect_type: str,
    original_targets_str: list[str],
    target_candidate_count: int,
    unlowered_overlap_substitution_targets: list[str],
    unlowered_overlap_substitution_reason: str,
) -> None:
    appropriate_place_definition_entry = _looks_like_appropriate_place_definition_entry_insert_text(
        extracted_text or ""
    )
    lowering_rule_id = (
        "uk_effect_appropriate_place_definition_entry_insert_rejected"
        if appropriate_place_definition_entry
        else "uk_effect_overlap_substitution_unlowered"
    )
    reason_code = (
        "appropriate_place_definition_entry_requires_anchor_claim"
        if appropriate_place_definition_entry
        else unlowered_overlap_substitution_reason
    )
    reason = (
        "UK source inserts a definition entry at an appropriate place without "
        "naming an anchor; lowering requires a validated placement claim and "
        "must not infer an insertion point from live text or oracle order."
        if appropriate_place_definition_entry
        else (
            "UK word-level overlap substitution lowered to no replay operations "
            "because the source instruction could not be parsed into a safe text patch"
        )
    )
    _append_uk_effect_lowering_rejection(
        lowering_rejections_out,
        rule_id=lowering_rule_id,
        family="lowering_filter",
        reason_code=reason_code,
        reason=reason,
        effect=effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        detail={
            "effect_type_normalized": effect_type,
            "original_affected_provisions": effect.affected_provisions,
            "original_target_candidates": original_targets_str,
            "unlowered_target_candidates": unlowered_overlap_substitution_targets,
            "target_candidate_count": target_candidate_count,
            "parser": "parse_fragment_substitution",
            "placement_family": (
                "appropriate_place_definition_entry_requires_anchor_claim"
                if appropriate_place_definition_entry
                else ""
            ),
        },
    )


def build_crossheading_insert_ops(
    *,
    effect: UKEffectRecord,
    extracted_el: Optional[ET.Element],
    sequence: int,
    effect_witness: UKEffectWitness,
    extraction_witness: UKProvisionExtractionWitness,
) -> list[LegalOperation]:
    crossheading_payload = _extract_crossheading_payload_from_extracted(
        effect.affected_provisions,
        extracted_el,
    )
    if crossheading_payload is None:
        return []

    crossheading_target = canonicalize_uk_address(LegalAddress(path=(("crossheading", ""),)))
    crossheading_target_witness = _uk_target_expansion_witness(
        "cross-heading",
        ["cross-heading"],
    )
    crossheading_lowered_witness = UKLoweredOperationWitness(
        op_id=f"{effect.effect_id}_crossheading",
        sequence=sequence,
        action=StructuralAction.INSERT,
        target=crossheading_target,
        payload=crossheading_payload,
        source=OperationSource(
            statute_id=effect.affecting_act_id,
            title=effect.affecting_title,
            effective=effect_witness.applicability.effective_date or "",
            raw_text=extraction_witness.extracted_text,
        ),
        effect_witness=effect_witness,
        extraction_witness=extraction_witness,
        target_expansion_witness=crossheading_target_witness,
        text_rewrite_witness=None,
        insertion_anchor_witness=None,
    )
    return [
        LegalOperation(
            op_id=crossheading_lowered_witness.op_id,
            sequence=sequence,
            action=StructuralAction.INSERT,
            target=crossheading_target,
            payload=_payload_with_rewrite_witness(
                crossheading_payload,
                crossheading_lowered_witness,
            ),
            source=crossheading_lowered_witness.source,
            group_id=_uk_temporal_group_id(effect),
            provenance_tags=_uk_lowered_op_provenance_tags(crossheading_lowered_witness),
        )
    ]


def build_trailing_repeal_ops(
    *,
    effect: UKEffectRecord,
    sequence: int,
    trailing_repeal_refs: list[str],
    effect_witness: UKEffectWitness,
    extraction_witness: UKProvisionExtractionWitness,
    original_targets_str: list[str],
    source_parent_substitution_range_payload: Optional[dict[str, Any]],
) -> list[LegalOperation]:
    src = OperationSource(
        statute_id=effect.affecting_act_id,
        title=effect.affecting_title,
        effective=effect_witness.applicability.effective_date or "",
        raw_text=extraction_witness.extracted_text,
    )
    ops: list[LegalOperation] = []
    for repeal_idx, repeal_ref in enumerate(trailing_repeal_refs):
        repeal_target = _parse_affected_target(repeal_ref)
        target_expansion_witness = _uk_target_expansion_witness(
            repeal_ref,
            [repeal_ref],
            original_targets_str=original_targets_str,
        )
        lowered_witness = UKLoweredOperationWitness(
            op_id=f"{effect.effect_id}_repeal_{repeal_idx}",
            sequence=sequence,
            action=StructuralAction.REPEAL,
            target=repeal_target,
            payload=None,
            source=src,
            effect_witness=effect_witness,
            extraction_witness=extraction_witness,
            target_expansion_witness=target_expansion_witness,
            text_rewrite_witness=None,
            insertion_anchor_witness=None,
        )
        ops.append(
            LegalOperation(
                op_id=lowered_witness.op_id,
                sequence=lowered_witness.sequence,
                action=lowered_witness.action,
                target=lowered_witness.target,
                payload=None,
                source=lowered_witness.source,
                group_id=_uk_temporal_group_id(effect),
                provenance_tags=_uk_lowered_op_provenance_tags(lowered_witness),
                witness_rule_id=(
                    _UK_SOURCE_PARENT_SUBSTITUTION_RANGE_PAYLOAD_RULE_ID
                    if source_parent_substitution_range_payload is not None
                    else None
                ),
            )
        )
    return ops

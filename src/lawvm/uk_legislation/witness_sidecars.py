"""Serialization helpers for UK lowered-operation witnesses."""

from __future__ import annotations

import json
from dataclasses import replace as dc_replace
from typing import Any, Optional

from lawvm.core.ir import IRNode, LegalAddress, LegalOperation, OperationSource
from lawvm.core.semantic_types import FacetKind, StructuralAction
from lawvm.uk_legislation.provenance_notes import (
    NOTE_EFFECT_TYPE,
    NOTE_FRAGMENT_SUB,
    NOTE_METADATA_SOURCE_FALLBACK,
    NOTE_ORIGINAL_REF,
    NOTE_PRECEDING_EID,
    NOTE_RAW_TEXT,
    NOTE_REWRITE_WITNESS,
    NOTE_TEXT_REWRITE_RULE,
)
from lawvm.uk_legislation.witnesses import (
    UKApplicabilityWitness,
    UKEffectWitness,
    UKInsertionAnchorWitness,
    UKLoweredOperationWitness,
    UKProvisionExtractionWitness,
    UKTargetExpansionWitness,
    UKTextRewriteSpec,
)


def _lowered_witness_to_payload_data(witness: UKLoweredOperationWitness) -> dict[str, Any]:
    """Project a lowered witness into a JSON-safe payload sidecar."""
    target = witness.target
    effect_witness = witness.effect_witness
    applicability = effect_witness.applicability
    extraction_witness = witness.extraction_witness
    target_expansion_witness = witness.target_expansion_witness
    text_rewrite_witness = witness.text_rewrite_witness
    insertion_anchor_witness = witness.insertion_anchor_witness
    return {
        "op_id": witness.op_id,
        "sequence": witness.sequence,
        "action": witness.action.value,
        "target": {
            "path": [[kind, label] for kind, label in target.path],
            "special": target.special.value if target.special is not None else None,
        },
        "source": {
            "statute_id": witness.source.statute_id,
            "title": witness.source.title,
            "effective": witness.source.effective,
            "raw_text": witness.source.raw_text,
        },
        "effect_witness": {
            "effect_id": effect_witness.effect_id,
            "affected_provisions_raw": effect_witness.affected_provisions_raw,
            "affecting_provisions_raw": effect_witness.affecting_provisions_raw,
            "effect_type_raw": effect_witness.effect_type_raw,
            "comments_raw": effect_witness.comments_raw,
            "authority_layer": effect_witness.authority_layer,
            "applicability": {
                "effective_date": applicability.effective_date,
                "in_force_dates": list(applicability.in_force_dates),
                "requires_applied": applicability.requires_applied,
                "applied": applicability.applied,
                "effect_type_raw": applicability.effect_type_raw,
            },
        },
        "extraction_witness": {
            "effect_id": extraction_witness.effect_id,
            "authority_layer": extraction_witness.authority_layer,
            "extracted_tag": extraction_witness.extracted_tag,
            "extracted_text": extraction_witness.extracted_text,
            "extracted_source_present": extraction_witness.extracted_source_present,
            "metadata_fallback_used": extraction_witness.metadata_fallback_used,
            "extraction_failure_kind": extraction_witness.extraction_failure_kind,
        },
        "target_expansion_witness": {
            "original_ref": target_expansion_witness.original_ref,
            "expanded_refs": list(target_expansion_witness.expanded_refs),
            "expansion_source": target_expansion_witness.expansion_source,
        },
        "text_rewrite_witness": None
        if text_rewrite_witness is None
        else {
            "primary_match": text_rewrite_witness.primary_match,
            "primary_replacement": text_rewrite_witness.primary_replacement,
            "alternatives": [[original, replacement] for original, replacement in text_rewrite_witness.alternatives],
            "occurrence": text_rewrite_witness.occurrence,
            "end_occurrence": text_rewrite_witness.end_occurrence,
            "rewrite_source": text_rewrite_witness.rewrite_source,
        },
        "insertion_anchor_witness": None
        if insertion_anchor_witness is None
        else {
            "preceding_eid": insertion_anchor_witness.preceding_eid,
            "following_eid": insertion_anchor_witness.following_eid,
            "anchor_source": insertion_anchor_witness.anchor_source,
        },
    }


def _lowered_witness_from_payload_data(data: dict[str, Any]) -> UKLoweredOperationWitness:
    """Rehydrate a lowered witness from the JSON-safe payload sidecar."""
    target_data = dict(data.get("target", {}) or {})
    source_data = dict(data.get("source", {}) or {})
    effect_data = dict(data.get("effect_witness", {}) or {})
    applicability_data = dict(effect_data.get("applicability", {}) or {})
    extraction_data = dict(data.get("extraction_witness", {}) or {})
    expansion_data = dict(data.get("target_expansion_witness", {}) or {})
    text_rewrite_data = data.get("text_rewrite_witness")
    anchor_data = data.get("insertion_anchor_witness")
    target_path = tuple(
        (str(kind), str(label))
        for kind, label in (target_data.get("path", []) or [])
        if str(kind)
    )
    special = target_data.get("special")
    return UKLoweredOperationWitness(
        op_id=str(data.get("op_id", "") or ""),
        sequence=int(data.get("sequence", 0) or 0),
        action=StructuralAction(str(data.get("action", StructuralAction.REPLACE.value) or StructuralAction.REPLACE.value)),
        target=LegalAddress(
            path=target_path,
            special=FacetKind(special) if special else None,
        ),
        payload=None,
        source=OperationSource(
            statute_id=str(source_data.get("statute_id", "") or ""),
            title=str(source_data.get("title", "") or ""),
            effective=str(source_data.get("effective", "") or ""),
            raw_text=str(source_data.get("raw_text", "") or ""),
        ),
        effect_witness=UKEffectWitness(
            effect_id=str(effect_data.get("effect_id", "") or ""),
            affected_provisions_raw=str(effect_data.get("affected_provisions_raw", "") or ""),
            affecting_provisions_raw=str(effect_data.get("affecting_provisions_raw", "") or ""),
            effect_type_raw=str(effect_data.get("effect_type_raw", "") or ""),
            comments_raw=str(effect_data.get("comments_raw", "") or ""),
            authority_layer=str(effect_data.get("authority_layer", "") or ""),
            applicability=UKApplicabilityWitness(
                effective_date=applicability_data.get("effective_date"),
                in_force_dates=tuple(str(item) for item in (applicability_data.get("in_force_dates", []) or [])),
                requires_applied=bool(applicability_data.get("requires_applied", False)),
                applied=bool(applicability_data.get("applied", False)),
                effect_type_raw=str(applicability_data.get("effect_type_raw", "") or ""),
            ),
        ),
        extraction_witness=UKProvisionExtractionWitness(
            effect_id=str(extraction_data.get("effect_id", "") or ""),
            authority_layer=str(extraction_data.get("authority_layer", "") or ""),
            extracted_tag=extraction_data.get("extracted_tag"),
            extracted_text=str(extraction_data.get("extracted_text", "") or ""),
            extracted_source_present=bool(extraction_data.get("extracted_source_present", False)),
            metadata_fallback_used=bool(extraction_data.get("metadata_fallback_used", False)),
            extraction_failure_kind=extraction_data.get("extraction_failure_kind"),
        ),
        target_expansion_witness=UKTargetExpansionWitness(
            original_ref=str(expansion_data.get("original_ref", "") or ""),
            expanded_refs=tuple(str(item) for item in (expansion_data.get("expanded_refs", []) or [])),
            expansion_source=str(expansion_data.get("expansion_source", "") or ""),
        ),
        text_rewrite_witness=None
        if text_rewrite_data is None
        else UKTextRewriteSpec(
            primary_match=text_rewrite_data.get("primary_match"),
            primary_replacement=text_rewrite_data.get("primary_replacement"),
            alternatives=tuple(
                (str(original), str(replacement))
                for original, replacement in (text_rewrite_data.get("alternatives", []) or [])
            ),
            occurrence=int(text_rewrite_data.get("occurrence", 0) or 0),
            end_occurrence=int(text_rewrite_data.get("end_occurrence", 0) or 0),
            rewrite_source=str(text_rewrite_data.get("rewrite_source", "") or ""),
        ),
        insertion_anchor_witness=None
        if anchor_data is None
        else UKInsertionAnchorWitness(
            preceding_eid=anchor_data.get("preceding_eid"),
            following_eid=anchor_data.get("following_eid"),
            anchor_source=str(anchor_data.get("anchor_source", "") or ""),
        ),
    )


def _witness_for_op(op: LegalOperation) -> object | None:
    """Return the preferred witness payload for UK replay helpers.

    Prefer the typed payload-sidecar witness when present so sidecar-backed
    lanes can migrate away from the shared source witness carrier. Payload-
    less text ops use a provenance-tag sidecar so authority filtering can still
    inspect their source witness.
    """
    payload = getattr(op, "payload", None)
    payload_attrs = getattr(payload, "attrs", None)
    if isinstance(payload_attrs, dict):
        witness = payload_attrs.get("rewrite_witness")
        if isinstance(witness, dict) and {"effect_witness", "extraction_witness", "target_expansion_witness"} <= set(witness):
            return _lowered_witness_from_payload_data(witness)
        if witness is not None:
            return witness
    for note in getattr(op, "provenance_tags", ()) or ():
        if not str(note).startswith(NOTE_REWRITE_WITNESS):
            continue
        try:
            witness_payload = json.loads(str(note)[len(NOTE_REWRITE_WITNESS) :])
        except json.JSONDecodeError:
            return None
        if (
            isinstance(witness_payload, dict)
            and {"effect_witness", "extraction_witness", "target_expansion_witness"} <= set(witness_payload)
        ):
            return _lowered_witness_from_payload_data(witness_payload)
    return None


def _payload_with_rewrite_witness(
    payload: Optional[IRNode],
    witness: UKLoweredOperationWitness,
) -> Optional[IRNode]:
    """Attach a sidecar witness to a payload node without creating a cycle."""
    if payload is None:
        return None
    payload_witness = _lowered_witness_to_payload_data(dc_replace(witness, payload=None))
    return dc_replace(payload, attrs={**dict(payload.attrs), "rewrite_witness": payload_witness})


def _uk_lowered_op_provenance_tags(witness: UKLoweredOperationWitness) -> tuple[str, ...]:
    provenance_tags: list[str] = [
        f"{NOTE_EFFECT_TYPE}{witness.effect_witness.effect_type_raw}",
        f"{NOTE_ORIGINAL_REF}{witness.target_expansion_witness.original_ref}",
    ]
    if witness.extraction_witness.extracted_text:
        provenance_tags.append(f"{NOTE_RAW_TEXT}{witness.extraction_witness.extracted_text}")
    if witness.text_rewrite_witness is not None and witness.text_rewrite_witness.alternatives:
        witness_payload = _lowered_witness_to_payload_data(dc_replace(witness, payload=None))
        provenance_tags.append(f"{NOTE_REWRITE_WITNESS}{json.dumps(witness_payload, ensure_ascii=False)}")
        fragment_sub_payload = []
        for original, replacement in witness.text_rewrite_witness.alternatives:
            fragment: dict[str, str] = {"original": original, "replacement": replacement}
            if witness.text_rewrite_witness.occurrence:
                fragment["occurrence"] = str(witness.text_rewrite_witness.occurrence)
            if witness.text_rewrite_witness.end_occurrence:
                fragment["end_occurrence"] = str(witness.text_rewrite_witness.end_occurrence)
            fragment_sub_payload.append(fragment)
        provenance_tags.append(f"{NOTE_FRAGMENT_SUB}{json.dumps(fragment_sub_payload, ensure_ascii=False)}")
        provenance_tags.append(f"{NOTE_TEXT_REWRITE_RULE}{witness.text_rewrite_witness.rewrite_source}")
    if witness.insertion_anchor_witness is not None and witness.insertion_anchor_witness.preceding_eid:
        provenance_tags.append(f"{NOTE_PRECEDING_EID}{witness.insertion_anchor_witness.preceding_eid}")
    if (
        witness.payload is None
        and witness.text_rewrite_witness is None
        and witness.action in {StructuralAction.RENUMBER, StructuralAction.REPEAL}
    ):
        witness_payload = _lowered_witness_to_payload_data(dc_replace(witness, payload=None))
        provenance_tags.append(f"{NOTE_REWRITE_WITNESS}{json.dumps(witness_payload, ensure_ascii=False)}")
    if witness.extraction_witness.metadata_fallback_used:
        provenance_tags.append(f"{NOTE_METADATA_SOURCE_FALLBACK}{witness.effect_witness.effect_id}")
    return tuple(provenance_tags)

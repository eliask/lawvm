from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from lawvm.core.ir import IRNode, LegalAddress, OperationSource, StructuralAction


@dataclass(frozen=True)
class UKApplicabilityWitness:
    effective_date: Optional[str]
    in_force_dates: tuple[str, ...]
    requires_applied: bool
    applied: bool
    effect_type_raw: str


@dataclass(frozen=True)
class UKEffectWitness:
    effect_id: str
    affected_provisions_raw: str
    affecting_provisions_raw: str
    effect_type_raw: str
    comments_raw: str
    authority_layer: str
    applicability: UKApplicabilityWitness


@dataclass(frozen=True)
class UKProvisionExtractionWitness:
    effect_id: str
    authority_layer: str
    extracted_tag: Optional[str]
    extracted_text: str
    extracted_source_present: bool
    metadata_fallback_used: bool
    extraction_failure_kind: Optional[str]


@dataclass(frozen=True)
class UKTargetExpansionWitness:
    original_ref: str
    expanded_refs: tuple[str, ...]
    expansion_source: str


@dataclass(frozen=True)
class UKTextRewriteSpec:
    primary_match: Optional[str]
    primary_replacement: Optional[str]
    alternatives: tuple[tuple[str, str], ...]
    occurrence: int
    rewrite_source: str


@dataclass(frozen=True)
class UKInsertionAnchorWitness:
    preceding_eid: Optional[str]
    anchor_source: str


@dataclass(frozen=True)
class UKLoweredOperationWitness:
    op_id: str
    sequence: int
    action: StructuralAction
    target: LegalAddress
    payload: Optional[IRNode]
    source: OperationSource
    effect_witness: UKEffectWitness
    extraction_witness: UKProvisionExtractionWitness
    target_expansion_witness: UKTargetExpansionWitness
    text_rewrite_witness: Optional[UKTextRewriteSpec]
    insertion_anchor_witness: Optional[UKInsertionAnchorWitness]

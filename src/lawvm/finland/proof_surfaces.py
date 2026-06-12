"""Finland proof-surface projections.

These adapters expose existing Finland compiler facts through shared
proof-surface contracts. They are report/read-model projections only; they do
not authorize replay and do not change Finnish lowering or apply semantics.
"""

from __future__ import annotations

import hashlib
import importlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping, cast

from lawvm.core.agreement_residual import (
    AgreementResidual,
    agreement_surface_evidence_report,
    agreement_surface_from_residuals,
)
from lawvm.core.authority import (
    UNKNOWN_STATUS,
    BranchEdgeKind,
    WOULD_AMEND_EDGE,
    WOULD_INSERT_EDGE,
    WOULD_REPEAL_EDGE,
    WOULD_REPLACE_EDGE,
    BranchGraphEdge,
    LegalBranch,
    PROPOSAL_AUTHORITY,
)
from lawvm.core.branch_projection import BranchImpactProjection, branch_impact_projection_from_edges
from lawvm.core.candidate_set_certificate import (
    CANDIDATE_SET_COMPLETE,
    CANDIDATE_SET_PARTIAL,
    CANDIDATE_SET_UNAVAILABLE,
    CandidateSetCertificate,
)
from lawvm.core.compile_result import SourcePathology
from lawvm.core.evidence_surface_report import EvidenceSurfaceReport
from lawvm.core.execution_authorization import ExecutionAuthorization
from lawvm.core.frontier_work_item import (
    FrontierWorkItem,
    frontier_work_item_with_claim_template,
)
from lawvm.core.mutation_accounting import MutationAccountingResult, MutationInvariantReport
from lawvm.core.mutation_boundary_proof import MutationBoundaryProof
from lawvm.core.source_completeness import (
    SourceCompletenessStatus,
    source_completeness_status_from_mapping,
)
from lawvm.core.ownership_closure import (
    OwnershipClosureCertificate,
    ownership_closure_evidence_report,
)
from lawvm.core.potential_operation import (
    POTENTIAL_OPERATION_COMPILED,
    POTENTIAL_OPERATION_FAILED,
    PotentialOperation,
    potential_operation_evidence_report,
)
from lawvm.core.source_acquisition import (
    SourceAcquisitionAssertion,
    SourceBundlePolicy,
    source_bundle_evidence_report,
)
from lawvm.core.source_witness import DigestWitness, SourceWitness
from lawvm.core.source_witness import (
    nested_source_witness_digest_coverage_counts,
    source_witness_digest_coverage_counts,
)
from lawvm.core.source_unit_coverage import (
    SOURCE_UNIT_FRONTIER_WITNESSED,
    SOURCE_UNIT_LINEAGE_WITNESSED,
    SourceUnitCoverage,
    SourceUnitCoverageStatus,
    source_unit_coverage_evidence_report,
)
from lawvm.core.source_pathology import (
    SourcePathologyProjection,
    source_pathology_evidence_report,
    source_pathology_projection,
)
from lawvm.core.temporal_resolution import (
    TEMPORAL_RECOVERY_FAMILY,
    TEMPORAL_UNRESOLVED_CONTINGENT,
    TEMPORAL_UNKNOWN_EFFECTIVE_DATE,
    TemporalResolutionStatus,
    TemporalResolutionEvidence,
)
from lawvm.finland.consolidated_artifacts import artifact_record

if TYPE_CHECKING:
    from lawvm.finland.he_branch_parser import HEParsedBranch


_DEFAULT_REQUIRED_PROOFS: tuple[str, ...] = (
    "source_identity_proof",
    "target_identity_proof",
    "payload_identity_or_manual_resolution_proof",
    "mutation_boundary_proof_before_replay_promotion",
)
_DEFAULT_FORBIDDEN_SHORTCUTS: tuple[str, ...] = (
    "treat_source_pathology_as_replay_authorization",
    "silently_widen_or_retarget_source_scope",
    "delete_or_overwrite_live_structure_to_match_oracle",
)
_DEFAULT_SAFE_DEFAULT = "preserve_uncertainty_and_do_not_promote_pathology_to_replay_authority"
_SPARSE_SLOT_PROMOTION_PROOFS: tuple[str, ...] = (
    "full_sparse_slot_candidate_enumeration",
    "slot_uniqueness_proof",
    "payload_identity_proof",
    "mutation_boundary_proof_before_replay_promotion",
)
_FINLEX_RESIDUAL_FORBIDDEN_SHORTCUTS: tuple[str, ...] = (
    "finlex_oracle_as_source_truth",
    "editorial_witness_as_replay_authorization",
    "agreement_residual_as_mutation_instruction",
)
_MUTATION_BOUNDARY_FORBIDDEN_SHORTCUTS: tuple[str, ...] = (
    "mutation_boundary_report_as_replay_authorization",
    "ignore_unexplained_changed_paths",
    "treat_allowed_recovery_as_universal_target_widening",
)
_RECOVERY_AUTHORIZATION_REQUIRED_PROOFS: tuple[str, ...] = (
    "source_identity_proof",
    "target_identity_proof",
    "recovery_rule_ownership_proof",
    "mutation_boundary_proof_before_replay_promotion",
)
_RECOVERY_AUTHORIZATION_FORBIDDEN_SHORTCUTS: tuple[str, ...] = (
    "recovery_projection_as_replay_authorization",
    "strict_recovery_row_as_quirks_permission",
    "recovery_finding_as_mutation_boundary_proof",
    "recovery_projection_as_target_widening",
)
_FAILED_OPERATION_REQUIRED_PROOFS: tuple[str, ...] = (
    "source_identity_proof",
    "target_identity_proof",
    "failed_operation_reason_classification",
    "payload_identity_or_manual_resolution_proof",
    "mutation_boundary_proof_before_replay_promotion",
)
_FAILED_OPERATION_FORBIDDEN_SHORTCUTS: tuple[str, ...] = (
    "failed_operation_as_replay_authorization",
    "failed_operation_reason_as_manual_claim",
    "failed_operation_target_as_target_resolution_proof",
    "failed_operation_absence_as_source_cue_exhaustiveness_proof",
)
_FAILED_OPERATION_SAFE_DEFAULT = (
    "treat_failed_operation_as_non_executable_frontier_until_source_target_payload_and_boundary_are_proven"
)
_HE_BRANCH_FORBIDDEN_SHORTCUTS: tuple[str, ...] = (
    "he_branch_op_as_enacted_operation",
    "he_branch_parse_success_as_replay_authorization",
    "he_branch_target_resolution_as_target_hijack",
    "he_branch_projection_as_current_law",
)
_HE_BRANCH_REQUIRED_PROOFS: tuple[str, ...] = (
    "enacted_source_identity_proof",
    "proposal_enactment_proof",
    "target_identity_proof_against_enacted_state",
    "mutation_boundary_proof_before_replay_promotion",
)


@dataclass(frozen=True, slots=True)
class FinlandSourcePathologyProofRule:
    """Declarative projection metadata for a Finland source-pathology code."""

    code: str
    lane: str
    owner_phase: str
    strict_disposition: str
    quirks_disposition: str
    frontier_family: str
    frontier_status: str
    required_claim_kind: str
    required_proofs: tuple[str, ...] = _DEFAULT_REQUIRED_PROOFS
    forbidden_shortcuts: tuple[str, ...] = _DEFAULT_FORBIDDEN_SHORTCUTS
    safe_default: str = _DEFAULT_SAFE_DEFAULT
    candidate_operation_family: str = "source_pathology_resolution"
    validator_status: str = "not_validated_for_replay_promotion"
    required_validator_checks: tuple[str, ...] = (
        "validate_source_pathology_resolution_claim",
        "validate_mutation_boundary_before_replay_promotion",
    )


@dataclass(frozen=True, slots=True)
class FinlandRecoveryAuthorizationRule:
    """Declarative projection metadata for Finland recovery/strictness findings."""

    kind: str
    owner_phase: str
    family: str
    strict_disposition: str = "record"
    quirks_disposition: str = "record"
    validator_status: str = "not_validated_for_replay_promotion"
    required_proofs: tuple[str, ...] = _RECOVERY_AUTHORIZATION_REQUIRED_PROOFS
    forbidden_shortcuts: tuple[str, ...] = _RECOVERY_AUTHORIZATION_FORBIDDEN_SHORTCUTS
    safe_default: str = "treat_recovery_projection_as_diagnostic_not_replay_authorization"


_SOURCE_PATHOLOGY_RULES: dict[str, FinlandSourcePathologyProofRule] = {
    "PARTIAL_WHOLE_SECTION_PAYLOAD": FinlandSourcePathologyProofRule(
        code="PARTIAL_WHOLE_SECTION_PAYLOAD",
        lane="source_pathology",
        owner_phase="payload_normalization",
        strict_disposition="block",
        quirks_disposition="record",
        frontier_family="fi_partial_whole_section_payload",
        frontier_status="source_pathology_frontier",
        required_claim_kind="fi.v1.PAYLOAD_COMPLETENESS_RESOLUTION",
    ),
    "MALFORMED_BROAD_REPLACE_BODY": FinlandSourcePathologyProofRule(
        code="MALFORMED_BROAD_REPLACE_BODY",
        lane="source_pathology",
        owner_phase="payload_normalization",
        strict_disposition="block",
        quirks_disposition="record",
        frontier_family="fi_malformed_broad_replace_body",
        frontier_status="source_pathology_frontier",
        required_claim_kind="fi.v1.PAYLOAD_COMPLETENESS_RESOLUTION",
    ),
    "CONTAINER_MEMBERSHIP_MISMATCH": FinlandSourcePathologyProofRule(
        code="CONTAINER_MEMBERSHIP_MISMATCH",
        lane="source_pathology",
        owner_phase="typed_elaboration",
        strict_disposition="block",
        quirks_disposition="record",
        frontier_family="fi_container_membership_mismatch",
        frontier_status="source_pathology_frontier",
        required_claim_kind="fi.v1.CONTAINER_MEMBERSHIP_RESOLUTION",
    ),
    "SPARSE_ITEM_BODY_MISSING": FinlandSourcePathologyProofRule(
        code="SPARSE_ITEM_BODY_MISSING",
        lane="source_pathology",
        owner_phase="typed_elaboration",
        strict_disposition="block",
        quirks_disposition="record",
        frontier_family="fi_sparse_item_body_missing",
        frontier_status="source_pathology_frontier",
        required_claim_kind="fi.v1.SPARSE_SLOT_PAYLOAD_RESOLUTION",
    ),
    "BASE_MISSING_CHAPTER_SPAN": FinlandSourcePathologyProofRule(
        code="BASE_MISSING_CHAPTER_SPAN",
        lane="source_pathology",
        owner_phase="source_chain_elaboration",
        strict_disposition="block",
        quirks_disposition="record",
        frontier_family="fi_missing_base_chapter_span",
        frontier_status="source_chain_frontier",
        required_claim_kind="fi.v1.SOURCE_CHAIN_RESOLUTION",
    ),
    "RECODIFICATION_SOURCE_CHAIN_GAP": FinlandSourcePathologyProofRule(
        code="RECODIFICATION_SOURCE_CHAIN_GAP",
        lane="source_pathology",
        owner_phase="source_chain_elaboration",
        strict_disposition="block",
        quirks_disposition="record",
        frontier_family="fi_recodification_source_chain_gap",
        frontier_status="source_chain_frontier",
        required_claim_kind="fi.v1.SOURCE_CHAIN_RESOLUTION",
    ),
    "TEMPORARY_SECTION_REBASE": FinlandSourcePathologyProofRule(
        code="TEMPORARY_SECTION_REBASE",
        lane="temporal_recovery",
        owner_phase="temporal_elaboration",
        strict_disposition="block",
        quirks_disposition="record",
        frontier_family="fi_temporary_section_rebase",
        frontier_status="temporal_frontier",
        required_claim_kind="fi.v1.TEMPORAL_BASE_SELECTION_RESOLUTION",
        required_proofs=(
            "source_identity_proof",
            "target_identity_proof",
            "temporal_applicability_proof",
            "mutation_boundary_proof_before_replay_promotion",
        ),
    ),
    "DESTRUCTIVE_SHAPE_LOSS_RISK": FinlandSourcePathologyProofRule(
        code="DESTRUCTIVE_SHAPE_LOSS_RISK",
        lane="replay_recovery_risk",
        owner_phase="replay_apply",
        strict_disposition="block",
        quirks_disposition="record",
        frontier_family="fi_destructive_shape_loss_risk",
        frontier_status="mutation_boundary_frontier",
        required_claim_kind="fi.v1.MUTATION_BOUNDARY_RESOLUTION",
    ),
}

_RECOVERY_AUTHORIZATION_RULES: dict[str, FinlandRecoveryAuthorizationRule] = {
    "PARSE.EXTRACTION_FALLBACK": FinlandRecoveryAuthorizationRule(
        kind="PARSE.EXTRACTION_FALLBACK",
        owner_phase="surface_parse",
        family="formula_extraction_recovery",
    ),
    "PARSE.SEMANTIC_COLLAPSE_MOVE_RENUMBER": FinlandRecoveryAuthorizationRule(
        kind="PARSE.SEMANTIC_COLLAPSE_MOVE_RENUMBER",
        owner_phase="surface_parse",
        family="semantic_collapse_recovery",
    ),
    "PARSE.TARGET_GUESSING": FinlandRecoveryAuthorizationRule(
        kind="PARSE.TARGET_GUESSING",
        owner_phase="surface_parse",
        family="target_resolution_recovery",
    ),
    "LOWER.CONTEXT_DEPENDENT_ANCHOR_RESOLUTION": FinlandRecoveryAuthorizationRule(
        kind="LOWER.CONTEXT_DEPENDENT_ANCHOR_RESOLUTION",
        owner_phase="canonical_op_compilation",
        family="context_dependent_anchor_resolution",
    ),
    "ELAB.ALIGN_SPARSE_OMISSION_TO_LIVE": FinlandRecoveryAuthorizationRule(
        kind="ELAB.ALIGN_SPARSE_OMISSION_TO_LIVE",
        owner_phase="typed_elaboration",
        family="sparse_payload_elaboration_recovery",
    ),
    "ELAB.SPLIT_SPARSE_OMISSION_CONSECUTIVE": FinlandRecoveryAuthorizationRule(
        kind="ELAB.SPLIT_SPARSE_OMISSION_CONSECUTIVE",
        owner_phase="typed_elaboration",
        family="sparse_payload_elaboration_recovery",
    ),
    "ELAB.CONTAINER_PRUNED_SHADOWED": FinlandRecoveryAuthorizationRule(
        kind="ELAB.CONTAINER_PRUNED_SHADOWED",
        owner_phase="typed_elaboration",
        family="payload_ownership_recovery",
    ),
    "ELAB.PAYLOAD_COMPLETENESS": FinlandRecoveryAuthorizationRule(
        kind="ELAB.PAYLOAD_COMPLETENESS",
        owner_phase="payload_elaboration",
        family="payload_completeness_recovery",
    ),
    "ELAB.SPARSE_PAYLOAD_LEFTOVER": FinlandRecoveryAuthorizationRule(
        kind="ELAB.SPARSE_PAYLOAD_LEFTOVER",
        owner_phase="typed_elaboration",
        family="sparse_payload_frontier",
        strict_disposition="block",
    ),
    "ELAB.STRICT_REJECTED_OPERATION": FinlandRecoveryAuthorizationRule(
        kind="ELAB.STRICT_REJECTED_OPERATION",
        owner_phase="typed_elaboration",
        family="strict_operation_barrier",
        strict_disposition="block",
    ),
    "APPLY.UNCOVERED_BODY_RECOVERY": FinlandRecoveryAuthorizationRule(
        kind="APPLY.UNCOVERED_BODY_RECOVERY",
        owner_phase="replay_apply",
        family="uncovered_body_recovery",
    ),
    "APPLY.STRICT_REJECTED_UNCOVERED_BODY": FinlandRecoveryAuthorizationRule(
        kind="APPLY.STRICT_REJECTED_UNCOVERED_BODY",
        owner_phase="replay_apply",
        family="uncovered_body_recovery",
        strict_disposition="block",
    ),
    "APPLY.FALLBACK_WHOLE_SECTION_REPLACE": FinlandRecoveryAuthorizationRule(
        kind="APPLY.FALLBACK_WHOLE_SECTION_REPLACE",
        owner_phase="replay_apply",
        family="whole_section_replace_fallback",
    ),
    "APPLY.WORD_SUBSTITUTION": FinlandRecoveryAuthorizationRule(
        kind="APPLY.WORD_SUBSTITUTION",
        owner_phase="replay_apply",
        family="text_substitution_recovery",
    ),
    "APPLY.SOURCE_CORRECTED_BY_PATCH": FinlandRecoveryAuthorizationRule(
        kind="APPLY.SOURCE_CORRECTED_BY_PATCH",
        owner_phase="replay_apply",
        family="source_corrected_by_patch",
    ),
    "APPLY.LEGACY_DISPATCH_FALLBACK": FinlandRecoveryAuthorizationRule(
        kind="APPLY.LEGACY_DISPATCH_FALLBACK",
        owner_phase="replay_apply",
        family="legacy_dispatch_fallback",
    ),
    "COVERAGE.HIGH_UNCOVERED_BODY_DEGRADED": FinlandRecoveryAuthorizationRule(
        kind="COVERAGE.HIGH_UNCOVERED_BODY_DEGRADED",
        owner_phase="payload_elaboration",
        family="uncovered_body_coverage_barrier",
        strict_disposition="block",
    ),
}


def source_pathology_proof_rule(code: str) -> FinlandSourcePathologyProofRule:
    """Return declarative projection metadata for a pathology code."""

    code_text = str(code or "")
    return _SOURCE_PATHOLOGY_RULES.get(
        code_text,
        FinlandSourcePathologyProofRule(
            code=code_text or "UNKNOWN_SOURCE_PATHOLOGY",
            lane="source_pathology",
            owner_phase="typed_elaboration",
            strict_disposition="block",
            quirks_disposition="record",
            frontier_family="fi_unclassified_source_pathology",
            frontier_status="source_pathology_frontier",
            required_claim_kind="source_pathology_resolution",
        ),
    )


def _with_finland_claim_template(item: FrontierWorkItem) -> FrontierWorkItem:
    importlib.import_module("lawvm.finland.claim_kinds")
    return frontier_work_item_with_claim_template(item)


def source_pathology_execution_authorization(
    pathology: SourcePathology | Mapping[str, Any],
) -> ExecutionAuthorization:
    """Project a Finland source pathology into shared authorization metadata."""

    row = _pathology_row(pathology)
    rule = source_pathology_proof_rule(str(row.get("code") or ""))
    return ExecutionAuthorization(
        executable=False,
        replay_authorized=False,
        authorization_status="source_pathology_not_replay_authority",
        authorization_rule_id=f"fi_source_pathology_{rule.code.lower()}",
        owner_phase=rule.owner_phase,
        strict_disposition=rule.strict_disposition,
        quirks_disposition=rule.quirks_disposition,
        validator_status=rule.validator_status,
        required_proofs=rule.required_proofs,
        safe_default=rule.safe_default,
        forbidden_shortcuts=rule.forbidden_shortcuts,
        detail={
            "jurisdiction": "fi",
            "lane": rule.lane,
            "source_pathology_code": row.get("code", ""),
            "source_statute": row.get("source_statute", ""),
            "target_unit_kind": row.get("target_unit_kind", ""),
            "target_label": row.get("target_label", ""),
        },
    )


def source_pathology_frontier_work_item(
    pathology: SourcePathology | Mapping[str, Any],
    *,
    statute_id: str = "",
) -> FrontierWorkItem:
    """Project a Finland source pathology into a non-executable frontier item."""

    row = _pathology_row(pathology)
    rule = source_pathology_proof_rule(str(row.get("code") or ""))
    authorization = source_pathology_execution_authorization(row)
    source_statute = str(row.get("source_statute") or statute_id or "unknown")
    target_label = str(row.get("target_label") or "")
    source_unit_id = target_label or str(row.get("code") or "source_pathology")
    bounded_preview = str(row.get("message") or "")
    source_witness = SourceWitness(
        source_role="finland_source_pathology",
        artifact_id=source_statute,
        source_unit_id=source_unit_id,
        bounded_preview=bounded_preview,
        preview_digest=_preview_digest_witness(bounded_preview),
        source_lane=rule.lane,
        metadata={
            "source_pathology_code": row.get("code", ""),
            "source_statute": source_statute,
            "target_unit_kind": row.get("target_unit_kind", ""),
            "target_label": target_label,
            "detail": dict(row.get("detail") or {}),
        },
    )
    return _with_finland_claim_template(FrontierWorkItem(
        work_item_id=_pathology_work_item_id(row, statute_id=statute_id),
        jurisdiction="fi",
        source_artifact_id=source_statute,
        source_unit_id=source_unit_id,
        source_witness=source_witness.to_dict(),
        target_witness={
            "target_unit_kind": row.get("target_unit_kind", ""),
            "target_label": target_label,
            "detail": dict(row.get("detail") or {}),
        },
        owner_phase=rule.owner_phase,
        frontier_family=rule.frontier_family,
        frontier_status=rule.frontier_status,
        candidate_operation_family=rule.candidate_operation_family,
        candidate_targets=tuple(target for target in (target_label,) if target),
        required_claim_kind=rule.required_claim_kind,
        required_validator_checks=rule.required_validator_checks,
        required_proofs=rule.required_proofs,
        safe_default=rule.safe_default,
        forbidden_shortcuts=rule.forbidden_shortcuts,
        executable=False,
        replay_authorized=False,
        authorization_status=authorization.authorization_status,
        detail={
            "execution_authorization": authorization.to_dict(),
            "source_pathology": row,
            "proof_surface_projection_only": True,
        },
    ))


def source_pathology_proof_surface_rows(
    pathologies: tuple[SourcePathology | Mapping[str, Any], ...],
    *,
    statute_id: str = "",
) -> dict[str, list[dict[str, Any]]]:
    """Return shared proof-surface rows for a sequence of pathologies."""

    authorizations: list[dict[str, Any]] = []
    frontier_items: list[dict[str, Any]] = []
    for pathology in pathologies:
        authorizations.append(source_pathology_execution_authorization(pathology).to_dict())
        frontier_items.append(
            source_pathology_frontier_work_item(
                pathology,
                statute_id=statute_id,
            ).to_dict()
        )
    return {
        "source_pathology_execution_authorizations": authorizations,
        "source_pathology_frontier_work_items": frontier_items,
    }


def failed_operation_execution_authorization(
    failed_op: Mapping[str, Any],
) -> ExecutionAuthorization:
    """Project a visible failed operation as blocked, non-executable work."""

    row = _failed_operation_row(failed_op)
    reason_code = str(row.get("reason_code") or "unknown")
    return ExecutionAuthorization(
        executable=False,
        replay_authorized=False,
        authorization_status="failed_operation_not_replay_authority",
        authorization_rule_id=f"fi_failed_operation_{_kind_slug(reason_code)}",
        owner_phase="replay_apply",
        strict_disposition="block",
        quirks_disposition="record",
        validator_status="failed_operation_requires_manual_or_deterministic_resolution",
        required_proofs=_FAILED_OPERATION_REQUIRED_PROOFS,
        safe_default=_FAILED_OPERATION_SAFE_DEFAULT,
        forbidden_shortcuts=_FAILED_OPERATION_FORBIDDEN_SHORTCUTS,
        detail={
            "jurisdiction": "fi",
            "source_statute": row.get("source_statute", ""),
            "failure_reason_code": reason_code,
            "target_unit_kind": row.get("target_unit_kind", ""),
            "target_label": row.get("target_label", ""),
        },
    )


def failed_operation_frontier_work_item(
    failed_op: Mapping[str, Any],
    *,
    index: int = 0,
    statute_id: str = "",
) -> FrontierWorkItem:
    """Project one failed operation row as non-executable frontier work."""

    row = _failed_operation_row(failed_op)
    authorization = failed_operation_execution_authorization(row)
    source_statute = str(row.get("source_statute") or statute_id or "unknown")
    target_label = str(row.get("target_label") or "")
    reason_code = str(row.get("reason_code") or "unknown")
    source_unit_id = target_label or f"failed-operation:{reason_code}:{index}"
    bounded_preview = str(row.get("description") or row.get("reason") or reason_code)
    source_witness = SourceWitness(
        source_role="finland_failed_operation",
        artifact_id=source_statute,
        source_unit_id=source_unit_id,
        bounded_preview=bounded_preview,
        preview_digest=_preview_digest_witness(bounded_preview),
        source_lane="failed_operation",
        metadata={
            "source_statute": source_statute,
            "failure_reason_code": reason_code,
            "target_unit_kind": row.get("target_unit_kind", ""),
            "target_label": target_label,
            "reason": row.get("reason", ""),
        },
    )
    return _with_finland_claim_template(FrontierWorkItem(
        work_item_id=_failed_operation_work_item_id(row, statute_id=statute_id, index=index),
        jurisdiction="fi",
        source_artifact_id=source_statute,
        source_unit_id=source_unit_id,
        source_witness=source_witness.to_dict(),
        target_witness={
            "target_unit_kind": row.get("target_unit_kind", ""),
            "target_label": target_label,
            "target_section": row.get("target_section", ""),
            "target_chapter": row.get("target_chapter"),
            "target_part": row.get("target_part"),
            "failure_reason_code": reason_code,
        },
        owner_phase="replay_apply",
        frontier_family="fi_failed_operation_resolution",
        frontier_status="failed_operation_frontier",
        candidate_operation_family="failed_operation_resolution",
        candidate_targets=tuple(target for target in (target_label,) if target),
        guidance_refs=("lawvm_failed_operation_resolution",),
        required_claim_kind="fi.v1.FAILED_OPERATION_RESOLUTION",
        required_validator_checks=(
            "validate_failed_operation_resolution_claim",
            "validate_target_identity_before_replay_promotion",
            "validate_mutation_boundary_before_replay_promotion",
        ),
        required_proofs=_FAILED_OPERATION_REQUIRED_PROOFS,
        safe_default=_FAILED_OPERATION_SAFE_DEFAULT,
        forbidden_shortcuts=_FAILED_OPERATION_FORBIDDEN_SHORTCUTS,
        executable=False,
        replay_authorized=False,
        authorization_status=authorization.authorization_status,
        detail={
            "execution_authorization": authorization.to_dict(),
            "failed_operation": row,
            "proof_surface_projection_only": True,
        },
    ))


def failed_operation_proof_surface_rows(
    failed_ops: tuple[Mapping[str, Any], ...],
    *,
    statute_id: str = "",
) -> dict[str, list[dict[str, Any]]]:
    """Return shared proof-surface rows for visible failed operations."""

    authorizations: list[dict[str, Any]] = []
    frontier_items: list[dict[str, Any]] = []
    for index, failed_op in enumerate(failed_ops):
        authorizations.append(failed_operation_execution_authorization(failed_op).to_dict())
        frontier_items.append(
            failed_operation_frontier_work_item(
                failed_op,
                index=index,
                statute_id=statute_id,
            ).to_dict()
        )
    return {
        "failed_operation_execution_authorizations": authorizations,
        "failed_operation_frontier_work_items": frontier_items,
    }


def _source_pathology_projections(
    pathologies: tuple[Mapping[str, Any], ...],
) -> tuple[SourcePathologyProjection, ...]:
    projections: list[SourcePathologyProjection] = []
    for pathology in pathologies:
        rule = source_pathology_proof_rule(str(pathology.get("code") or ""))
        projections.append(
            source_pathology_projection(
                pathology,
                jurisdiction="fi",
                affected_phase=rule.owner_phase,
                suggested_lane=rule.lane,
                blocks_execution=rule.strict_disposition == "block",
            )
        )
    return tuple(projections)


def consolidated_artifact_source_witness(
    *,
    locator: str,
    xml_bytes: bytes,
    source_role: str = "finlex_consolidated_oracle",
) -> SourceWitness:
    """Build a shared source witness for a cached Finland consolidated XML artifact."""

    record = artifact_record(locator, xml_bytes)
    preview = _bounded_bytes_preview(xml_bytes)
    return SourceWitness(
        source_role=source_role,
        artifact_id=record.sid,
        locator=locator,
        version_id=record.embedded_version_tag or record.path_version,
        source_path=locator,
        digest=DigestWitness(
            digest_algorithm="sha256",
            digest=hashlib.sha256(xml_bytes).hexdigest(),
        ),
        bounded_preview=preview,
        preview_digest=_preview_digest_witness(preview),
        source_lane=record.namespace or "finlex_consolidated",
        metadata={
            "namespace": record.namespace,
            "sid": record.sid,
            "lang": record.lang,
            "locator": locator,
            "path_version": record.path_version,
            "embedded_version_tag": record.embedded_version_tag,
            "date_consolidated": (record.date_consolidated.isoformat() if record.date_consolidated is not None else ""),
            "xml_size_bytes": len(xml_bytes),
        },
    )


def corrigendum_source_witness(
    row: Mapping[str, Any],
    *,
    source_role: str = "finland_corrigendum_pdf",
) -> SourceWitness:
    """Build a shared source witness for a Finland corrigendum PDF record."""

    source_pdf = str(row.get("source_pdf") or row.get("pdf_name") or "")
    amendment_id = str(row.get("amendment_id") or "")
    preview_parts = [
        part
        for part in (
            str(row.get("pdf_name") or ""),
            amendment_id,
            str(row.get("date_published") or ""),
            str(row.get("correction_item_count") or ""),
        )
        if part
    ]
    preview = " | ".join(preview_parts)
    digest_text = str(row.get("sha256") or "")
    return SourceWitness(
        source_role=source_role,
        artifact_id=source_pdf,
        source_unit_id=amendment_id,
        locator=source_pdf,
        version_id=str(row.get("date_published") or ""),
        source_path=source_pdf,
        digest=(DigestWitness(digest_algorithm="sha256", digest=digest_text) if digest_text else None),
        bounded_preview=preview,
        preview_digest=_preview_digest_witness(preview),
        source_lane="corrigendum_pdf",
        metadata={
            "statute_id": str(row.get("statute_id") or ""),
            "amendment_id": amendment_id,
            "pdf_name": str(row.get("pdf_name") or ""),
            "source_pdf": source_pdf,
            "lang": str(row.get("lang") or ""),
            "date_published": str(row.get("date_published") or ""),
            "date_status": str(row.get("date_status") or ""),
            "correction_item_count": int(row.get("correction_item_count") or 0),
            "size_bytes": int(row.get("size_bytes") or 0),
        },
    )


def finlex_html_topology_source_witness(
    row: Mapping[str, Any],
    *,
    statute_id: str,
    source_role: str = "finlex_html_topology_audit",
) -> SourceWitness:
    """Build a shared source witness for a Finland HTML/XML topology audit."""

    html_url = str(row.get("html_url") or "")
    missing_from_xml = _string_sequence(row.get("missing_from_xml"))
    extra_in_xml = _string_sequence(row.get("extra_in_xml"))
    noncommensurable_reason = str(row.get("noncommensurable_reason") or "")
    html_error = str(row.get("html_error") or "")
    preview = " | ".join(
        part
        for part in (
            statute_id,
            html_url,
            f"missing_from_xml={','.join(missing_from_xml[:10])}" if missing_from_xml else "",
            f"extra_in_xml={','.join(extra_in_xml[:10])}" if extra_in_xml else "",
            f"noncommensurable_reason={noncommensurable_reason}" if noncommensurable_reason else "",
            f"html_error={html_error}" if html_error else "",
        )
        if part
    )
    return SourceWitness(
        source_role=source_role,
        artifact_id=statute_id,
        source_unit_id=statute_id,
        locator=html_url,
        version_id="live",
        source_path=html_url,
        bounded_preview=preview,
        preview_digest=_preview_digest_witness(preview),
        source_lane="finlex_html_live_audit",
        metadata={
            "statute_id": statute_id,
            "html_url": html_url,
            "mismatch": bool(row.get("mismatch")),
            "missing_from_xml": missing_from_xml,
            "extra_in_xml": extra_in_xml,
            "missing_from_xml_count": len(missing_from_xml),
            "extra_in_xml_count": len(extra_in_xml),
            "html_error": html_error,
            "noncommensurable_reason": noncommensurable_reason,
        },
    )


def source_adjudication_lineage_source_witness_rows(
    source_adjudication: Any,
    *,
    statute_id: str = "",
) -> list[dict[str, Any]]:
    """Project Finland source-adjudication lineage rows into source witnesses."""

    if source_adjudication is None:
        return []
    adjudication_statute = str(_field(source_adjudication, "statute_id", "") or statute_id or "")
    lineage = _mapping_sequence(_field(source_adjudication, "lineage", ()))
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(lineage, start=1):
        amendment_id = str(row.get("statute_id") or "")
        if not amendment_id:
            continue
        rows.append(
            source_adjudication_lineage_source_witness(
                row,
                statute_id=adjudication_statute,
                index=index,
            ).to_dict()
        )
    return rows


def source_adjudication_lineage_source_witness(
    row: Mapping[str, Any],
    *,
    statute_id: str,
    index: int,
    source_role: str = "finland_source_lineage_amendment",
) -> SourceWitness:
    """Build source footing for one Finland replay source-chain lineage row."""

    amendment_id = str(row.get("statute_id") or "")
    effective_date = str(row.get("effective_date") or "")
    issue_date = str(row.get("issue_date") or "")
    title = str(row.get("title") or "")
    included = bool(row.get("included"))
    selection_basis = str(row.get("selection_basis") or "")
    preview = " | ".join(
        part
        for part in (
            f"parent={statute_id}",
            f"sequence={index}",
            amendment_id,
            effective_date,
            issue_date,
            "included" if included else "not_included",
            selection_basis,
            title,
        )
        if part
    )
    return SourceWitness(
        source_role=source_role,
        artifact_id=amendment_id,
        source_unit_id=amendment_id,
        locator=amendment_id,
        version_id=effective_date or issue_date,
        source_path=amendment_id,
        bounded_preview=preview,
        preview_digest=_preview_digest_witness(preview),
        source_lane="finland_source_adjudication_lineage",
        metadata={
            "statute_id": statute_id,
            "sequence": index,
            "amendment_id": amendment_id,
            "title": title,
            "effective_date": effective_date,
            "issue_date": issue_date,
            "sort_mode": str(row.get("sort_mode") or ""),
            "included": included,
            "selection_basis": selection_basis,
        },
    )


def mutation_boundary_proof_rows(
    reports: tuple[MutationInvariantReport | Mapping[str, Any], ...],
    *,
    statute_id: str,
    materialization_surface: str = "finland_strict_report",
) -> list[dict[str, Any]]:
    """Project Finland apply mutation-invariant reports into shared proof rows."""

    rows: list[dict[str, Any]] = []
    for index, report_like in enumerate(reports, start=1):
        report = _mutation_invariant_report(report_like)
        proof = MutationBoundaryProof.from_mutation_invariant_report(
            report,
            proof_id=_mutation_boundary_proof_id(
                statute_id=statute_id,
                index=index,
                op_id=report.op_id,
            ),
            jurisdiction="fi",
            materialization_surface=materialization_surface,
            owner_phase="replay_apply",
            safe_default="preserve_report_as_passive_boundary_evidence_not_replay_authorization",
            forbidden_shortcuts=_MUTATION_BOUNDARY_FORBIDDEN_SHORTCUTS,
        )
        row = proof.to_dict()
        source_statute = ""
        if isinstance(report_like, Mapping):
            report_mapping = cast("Mapping[str, Any]", report_like)
            source_statute = str(report_mapping.get("source_statute") or "")
        if source_statute:
            row["source_artifact_id"] = source_statute
        rows.append(row)
    return rows


def sparse_slot_candidate_set_certificate_rows(
    projection_rows: tuple[Mapping[str, Any], ...],
    *,
    statute_id: str = "",
) -> list[dict[str, Any]]:
    """Project Finland sparse-slot report rows into candidate certificates."""

    certificates: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, tuple[str, ...]]] = set()
    for row in projection_rows:
        certificate = _sparse_slot_candidate_certificate(row, statute_id=statute_id)
        if certificate is None:
            continue
        payload = certificate.to_dict()
        key = (
            str(payload.get("scope_id") or ""),
            str(payload.get("rule_id") or ""),
            str(payload.get("reason") or ""),
            tuple(str(candidate) for candidate in payload.get("candidate_ids", []) or []),
        )
        if key in seen:
            continue
        seen.add(key)
        certificates.append(payload)
    return certificates


def finlex_editorial_witness_agreement_residual(
    row: Mapping[str, Any],
    *,
    statute_id: str = "",
) -> AgreementResidual:
    """Project a Finlex editorial witness row into an agreement residual."""

    kind = str(row.get("kind") or "")
    slot_address = str(row.get("slot_address") or "")
    amendment_id = str(row.get("amendment_id") or "")
    timeline_terminator = str(row.get("timeline_terminator") or "")
    residual_id = _stable_residual_id(
        "fi-finlex-editorial-witness",
        statute_id,
        kind,
        slot_address,
        amendment_id,
        timeline_terminator,
    )
    if kind == "editorial_witness_confirmed":
        family = "agreement"
        status = "agrees"
        rule_id = "fi_finlex_inline_repeal_stub_confirmed"
        missing_proofs: tuple[str, ...] = ()
    elif kind == "editorial_witness_disagrees":
        family = "unknown"
        status = "residual"
        rule_id = "fi_finlex_inline_repeal_stub_disagrees"
        missing_proofs = (
            "manual_editorial_witness_triage",
            "timeline_terminator_source_review",
        )
    else:
        family = "source_footing_gap"
        status = "residual"
        rule_id = "fi_finlex_inline_repeal_stub_unresolved"
        missing_proofs = (
            "timeline_terminator_proof",
            "source_lineage_review",
        )
    return AgreementResidual(
        residual_id=residual_id,
        jurisdiction="fi",
        agreement_surface="finlex_inline_repeal_stub",
        family=family,
        status=status,
        owner_phase="oracle_adjudication",
        rule_id=rule_id,
        source_artifact_id=amendment_id or statute_id,
        replay_count=1 if kind == "editorial_witness_confirmed" or timeline_terminator else 0,
        oracle_count=1,
        missing_proofs=missing_proofs,
        safe_default="classify_finlex_editorial_witness_without_authorizing_replay",
        forbidden_shortcuts=_FINLEX_RESIDUAL_FORBIDDEN_SHORTCUTS,
        detail={
            "statute_id": statute_id,
            "witness_kind": kind,
            "slot_address": slot_address,
            "amendment_id": amendment_id,
            "timeline_terminator": timeline_terminator,
            "severity": str(row.get("severity") or ""),
        },
    )


def finlex_editorial_witness_agreement_residual_rows(
    rows: tuple[Mapping[str, Any], ...],
    *,
    statute_id: str = "",
) -> list[dict[str, Any]]:
    """Return shared residual rows for Finlex editorial witness records."""

    return [
        finlex_editorial_witness_agreement_residual(row, statute_id=statute_id).to_dict()
        for row in rows
        if str(row.get("kind") or "").startswith("editorial_witness_")
    ]


def source_adjudication_agreement_residual_rows(
    source_adjudication: Any,
    *,
    statute_id: str = "",
) -> list[dict[str, Any]]:
    """Project Finland source/oracle adjudication into agreement residual rows."""

    if source_adjudication is None:
        return []
    adjudication_statute = str(_field(source_adjudication, "statute_id", "") or statute_id or "")
    replay_mode = str(_field(source_adjudication, "replay_mode", "") or "")
    reason = str(_field(source_adjudication, "html_noncommensurable_reason", "") or "").strip()
    if not reason:
        return []
    residual = AgreementResidual(
        residual_id=_stable_residual_id(
            "fi-finlex-source-adjudication",
            adjudication_statute,
            replay_mode,
            reason,
        ),
        jurisdiction="fi",
        agreement_surface="finlex_html_oracle_compare",
        family="non_commensurable_surface",
        status="residual",
        owner_phase="oracle_adjudication",
        rule_id="fi_finlex_html_non_commensurable_surface",
        source_artifact_id=adjudication_statute,
        replay_count=0,
        oracle_count=0,
        missing_proofs=("compare_projection_review",),
        safe_default="classify_non_commensurable_finlex_surface_without_rewriting_replay",
        forbidden_shortcuts=_FINLEX_RESIDUAL_FORBIDDEN_SHORTCUTS,
        detail={
            "statute_id": adjudication_statute,
            "replay_mode": replay_mode,
            "html_noncommensurable_reason": reason,
            "cutoff_date": str(_field(source_adjudication, "cutoff_date", "") or ""),
            "oracle_version_amendment_id": str(_field(source_adjudication, "oracle_version_amendment_id", "") or ""),
            "oracle_suspect": str(_field(source_adjudication, "oracle_suspect", "") or ""),
        },
    )
    return [residual.to_dict()]


def finland_strict_report_evidence_surface(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Wrap Finland strict-report JSON in the shared evidence envelope."""

    source_pathologies = _mapping_sequence(payload.get("source_pathologies"))
    source_pathology_projections = _source_pathology_projections(source_pathologies)
    source_pathology_report = source_pathology_evidence_report(
        source_pathology_projections,
        jurisdiction="fi",
        report_kind="finland_source_pathology",
    ).to_dict()
    source_pathology_rows = _mapping_sequence(source_pathology_report.get("rows"))
    source_pathology_authorizations = _mapping_sequence(payload.get("source_pathology_execution_authorizations"))
    source_pathology_frontier_items = _mapping_sequence(payload.get("source_pathology_frontier_work_items"))
    failed_operation_authorizations = _mapping_sequence(payload.get("failed_operation_execution_authorizations"))
    failed_operation_frontier_items = _mapping_sequence(payload.get("failed_operation_frontier_work_items"))
    frontier_items = (*source_pathology_frontier_items, *failed_operation_frontier_items)
    potential_operations = _mapping_sequence(payload.get("potential_operations"))
    potential_operation_report = potential_operation_evidence_report(
        potential_operations,
        jurisdiction="fi",
        report_kind="finland_strict_report_potential_operations",
    ).to_dict()
    potential_operation_rows = _mapping_sequence(potential_operation_report.get("rows"))
    potential_operation_summary = dict(potential_operation_report.get("summary") or {})
    sparse_certificates = _mapping_sequence(payload.get("sparse_slot_candidate_set_certificates"))
    source_lineage_witnesses = _mapping_sequence(payload.get("source_lineage_source_witnesses"))
    source_unit_coverages = _mapping_sequence(payload.get("source_unit_coverages"))
    source_unit_coverage_report = source_unit_coverage_evidence_report(
        source_unit_coverages,
        jurisdiction="fi",
        report_kind="finland_strict_report_source_unit_coverage",
    ).to_dict()
    source_unit_coverage_rows = _mapping_sequence(source_unit_coverage_report.get("rows"))
    source_unit_coverage_summary = dict(source_unit_coverage_report.get("summary") or {})
    agreement_residuals = _mapping_sequence(payload.get("agreement_residuals"))
    agreement_report_rows, agreement_report_summary = _strict_report_agreement_surface_rows(
        agreement_residuals,
        payload=payload,
    )
    mutation_boundary_proofs = _mapping_sequence(payload.get("mutation_boundary_proofs"))
    projection_rows = _mapping_sequence(payload.get("projection_rows"))
    failed_ops = _mapping_sequence(payload.get("failed_ops"))
    strict_fail_reasons = _string_sequence(payload.get("strict_fail_reasons"))
    source_completeness_row = source_completeness_status_row(payload)
    source_completeness_issues = _mapping_sequence(payload.get("source_completeness_issues"))
    temporal_resolution_rows = temporal_resolution_evidence_rows_from_projection_rows(
        projection_rows,
        strict_fail_reasons=strict_fail_reasons,
    )
    recovery_authorization_rows = recovery_execution_authorization_rows_from_projection_rows(
        projection_rows,
        strict_fail_reasons=strict_fail_reasons,
        statute_id=str(payload.get("statute_id") or ""),
    )
    strict_report_candidate_sets = _mapping_sequence(payload.get("strict_report_candidate_set_certificates"))
    strict_report_candidate_set_authorizations = _mapping_sequence(
        payload.get("strict_report_candidate_set_execution_authorizations")
    )
    strict_report_candidate_set_frontier_items = _mapping_sequence(
        payload.get("strict_report_candidate_set_frontier_work_items")
    )
    ownership_closure = _mapping_or_empty(payload.get("ownership_closure_certificate"))
    ownership_closure_report = _mapping_or_empty(payload.get("ownership_closure_report"))
    ownership_closure_rows = _mapping_sequence(ownership_closure_report.get("rows"))
    ownership_closure_summary = dict(ownership_closure_report.get("summary") or {})
    ops = payload.get("ops")
    ops_summary = dict(ops) if isinstance(ops, Mapping) else {}
    rows = tuple(
        (
            *(
                {"surface": "source_pathology", **dict(row)}
                for row in source_pathology_rows
            ),
            *(
                {"surface": "source_pathology_execution_authorization", **dict(row)}
                for row in source_pathology_authorizations
            ),
            *(
                {"surface": "source_pathology_frontier_work_item", **dict(row)}
                for row in source_pathology_frontier_items
            ),
            *(
                {"surface": "failed_operation_execution_authorization", **dict(row)}
                for row in failed_operation_authorizations
            ),
            *(
                {"surface": "failed_operation_frontier_work_item", **dict(row)}
                for row in failed_operation_frontier_items
            ),
            *(
                {"surface": "potential_operation", **dict(row)}
                for row in potential_operation_rows
            ),
            *({"surface": "sparse_slot_candidate_set_certificate", **dict(row)} for row in sparse_certificates),
            *({"surface": "source_lineage_source_witness", **dict(row)} for row in source_lineage_witnesses),
            *({"surface": "source_unit_coverage", **dict(row)} for row in source_unit_coverage_rows),
            *({"surface": "agreement_residual", **dict(row)} for row in agreement_report_rows),
            *({"surface": "mutation_boundary_proof", **dict(row)} for row in mutation_boundary_proofs),
            *(({"surface": "source_completeness_status", **source_completeness_row},) if source_completeness_row else ()),
            *({"surface": "source_completeness_issue", **dict(row)} for row in source_completeness_issues),
            *({"surface": "temporal_resolution_evidence", **dict(row)} for row in temporal_resolution_rows),
            *({"surface": "recovery_execution_authorization", **dict(row)} for row in recovery_authorization_rows),
            *({"surface": "strict_report_candidate_set_certificate", **dict(row)} for row in strict_report_candidate_sets),
            *(
                {"surface": "strict_report_candidate_set_execution_authorization", **dict(row)}
                for row in strict_report_candidate_set_authorizations
            ),
            *(
                {"surface": "strict_report_candidate_set_frontier_work_item", **dict(row)}
                for row in strict_report_candidate_set_frontier_items
            ),
            *({"surface": "ownership_closure_certificate", **dict(row)} for row in ownership_closure_rows),
        )
    )
    summary = {
        "canonical_op_count": int(ops_summary.get("canonical") or 0),
        "failed_op_count": int(ops_summary.get("failed") or 0),
        "total_op_count": int(ops_summary.get("total") or 0),
        "source_pathology_count": len(source_pathology_rows),
        "source_pathology_kind_counts": dict(
            source_pathology_report.get("summary", {}).get("pathology_kind_counts", {})
        ),
        "source_pathology_affected_phase_counts": dict(
            source_pathology_report.get("summary", {}).get("affected_phase_counts", {})
        ),
        "source_pathology_execution_authorization_count": len(source_pathology_authorizations),
        "source_pathology_frontier_work_item_count": len(source_pathology_frontier_items),
        "failed_operation_execution_authorization_count": len(failed_operation_authorizations),
        "failed_operation_frontier_work_item_count": len(failed_operation_frontier_items),
        "potential_operation_count": len(potential_operation_rows),
        "potential_operation_classification_counts": dict(
            potential_operation_summary.get("classification_counts") or {}
        ),
        "potential_operation_family_counts": dict(
            potential_operation_summary.get("operation_family_counts") or {}
        ),
        "frontier_claim_template_status_counts": _frontier_claim_template_status_counts(frontier_items),
        "frontier_claim_template_kind_counts": _frontier_claim_template_kind_counts(frontier_items),
        "sparse_slot_candidate_set_certificate_count": len(sparse_certificates),
        "source_lineage_source_witness_count": len(source_lineage_witnesses),
        "source_unit_coverage_count": len(source_unit_coverage_rows),
        "source_unit_coverage_status_counts": dict(
            source_unit_coverage_summary.get("coverage_status_counts") or {}
        ),
        "source_unit_coverage_family_counts": dict(
            source_unit_coverage_summary.get("unit_family_counts") or {}
        ),
        "agreement_residual_count": len(agreement_report_rows),
        "agreement_residual_family_counts": dict(
            agreement_report_summary.get("residual_family_counts", {})
        ),
        "agreement_residual_status_counts": dict(
            agreement_report_summary.get("residual_status_counts", {})
        ),
        "agreement_materialization_kind": str(
            agreement_report_summary.get("materialization_kind") or ""
        ),
        "agreement_comparison_materialization_kind": str(
            agreement_report_summary.get("comparison_materialization_kind") or ""
        ),
        "mutation_boundary_proof_count": len(mutation_boundary_proofs),
        "source_completeness_status_count": 1 if source_completeness_row else 0,
        "source_completeness": source_completeness_row.get("counts", {}) if source_completeness_row else {},
        "source_completeness_issue_count": len(source_completeness_issues),
        "source_completeness_issue_kind_counts": _count_by_field(
            source_completeness_issues,
            "kind",
        ),
        "source_completeness_issue_family_counts": _count_by_field(
            source_completeness_issues,
            "issue_family",
        ),
        "temporal_resolution_evidence_count": len(temporal_resolution_rows),
        "recovery_execution_authorization_count": len(recovery_authorization_rows),
        "strict_report_candidate_set_certificate_count": len(strict_report_candidate_sets),
        "strict_report_candidate_set_status_counts": _count_by_field(
            strict_report_candidate_sets,
            "completeness_status",
        ),
        "strict_report_candidate_set_kind_counts": _count_by_field(
            strict_report_candidate_sets,
            "candidate_set_kind",
        ),
        "strict_report_candidate_set_execution_authorization_count": len(
            strict_report_candidate_set_authorizations
        ),
        "strict_report_candidate_set_execution_authorization_status_counts": _count_by_field(
            strict_report_candidate_set_authorizations,
            "authorization_status",
        ),
        "strict_report_candidate_set_frontier_work_item_count": len(
            strict_report_candidate_set_frontier_items
        ),
        "strict_report_candidate_set_frontier_status_counts": _count_by_field(
            strict_report_candidate_set_frontier_items,
            "frontier_status",
        ),
        "ownership_closure_certificate_count": len(ownership_closure_rows),
        "ownership_closure_status": str(ownership_closure.get("closure_status") or ""),
        "ownership_closure_failed_gate_counts": dict(
            ownership_closure_summary.get("failed_gate_counts") or {}
        ),
        "ownership_closure_unowned_counts": dict(
            ownership_closure_summary.get("unowned_counts") or {}
        ),
        "source_pathology_frontier_source_witness_digest_coverage_counts": (
            nested_source_witness_digest_coverage_counts(source_pathology_frontier_items)
        ),
        "failed_operation_frontier_source_witness_digest_coverage_counts": (
            nested_source_witness_digest_coverage_counts(failed_operation_frontier_items)
        ),
        "source_lineage_source_witness_digest_coverage_counts": (
            source_witness_digest_coverage_counts(source_lineage_witnesses)
        ),
        "projection_row_count": len(projection_rows),
        "failed_op_row_count": len(failed_ops),
        "strict_fail_reason_count": len(strict_fail_reasons),
    }
    return EvidenceSurfaceReport(
        jurisdiction="fi",
        report_kind="finland_strict_report",
        schema="lawvm.finland_strict_report.v1",
        truth_claim="finland_strict_compile_and_proof_surface_diagnostics",
        replay_claims=False,
        canonical_effect_claims=True,
        candidate_effect_claims=False,
        dry_run_claims=False,
        agreement_claims=False,
        summary=summary,
        filters={
            "statute_id": str(payload.get("statute_id") or ""),
            "profile": str(payload.get("profile") or ""),
        },
        filtered_summary=summary,
        rows=rows,
        rows_truncated=False,
        detail={
            "safe_default": "report_compile_diagnostics_without_authorizing_unproved_replay",
            "forbidden_shortcuts": (
                "strict_report_row_as_replay_authorization",
                "frontier_item_as_canonical_operation",
                "candidate_certificate_as_slot_uniqueness_proof",
                "projection_row_as_oracle_agreement",
                "mutation_boundary_proof_as_replay_authorization",
                "source_completeness_status_as_replay_authorization",
                "temporal_resolution_evidence_as_unconditional_commencement_proof",
                "recovery_projection_as_replay_authorization",
                "candidate_set_certificate_as_source_cue_exhaustiveness_proof",
                "ownership_closure_certificate_as_full_corpus_omniscience",
            ),
        },
    ).to_dict()


def finland_strict_report_candidate_set_certificates(
    payload: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Candidate-set certificates for visible strict-report accounting slices."""

    statute_id = str(payload.get("statute_id") or "unknown")
    canonical_count = _ops_count(payload, "canonical")
    canonical_op_ids = _string_sequence(payload.get("canonical_op_ids"))
    failed_ops = _mapping_sequence(payload.get("failed_ops"))
    potential_operations = _mapping_sequence(payload.get("potential_operations"))
    source_unit_coverages = _mapping_sequence(payload.get("source_unit_coverages"))
    source_lineage_witnesses = _mapping_sequence(payload.get("source_lineage_source_witnesses"))
    visible_operation_ids = (
        _potential_operation_candidate_ids(potential_operations)
        or _visible_operation_candidate_ids(
            canonical_op_ids=canonical_op_ids,
            canonical_count=canonical_count,
            failed_ops=failed_ops,
        )
    )
    source_unit_ids = (
        _source_unit_coverage_candidate_ids(source_unit_coverages)
        or _source_lineage_candidate_ids(source_lineage_witnesses)
    )
    return [
        CandidateSetCertificate(
            scope_id=f"fi:{statute_id}:strict-report-visible-operation-rows",
            candidate_set_kind="fi_strict_report_visible_operation_rows",
            phase="strict_report_projection",
            rule_id="fi_strict_report_visible_operation_candidate_projection",
            reason=(
                "Visible canonical and failed operation rows are accounted for, "
                "but this does not prove all source-text operation cues were enumerated."
            ),
            completeness_status=CANDIDATE_SET_PARTIAL,
            candidate_count=len(visible_operation_ids),
            candidate_ids=visible_operation_ids,
            missing_candidate_count=1,
            blocker_counts={"source_text_cue_exhaustiveness_unproved": 1},
            blocker_families=("source_text_cue_exhaustiveness_unproved",),
            next_promotion_allowed=False,
            next_promotion_requires=(
                "source_unit_enumeration_certificate",
                "operation_cue_exhaustiveness_certificate",
                "execution_authorization",
            ),
            detail={
                "visible_scope": "strict_report_canonical_and_failed_operation_rows",
                "potential_operation_row_count": len(potential_operations),
                "canonical_count": canonical_count,
                "failed_count": len(failed_ops),
                "safe_default": "do_not_treat_visible_operation_rows_as_complete_source_cue_coverage",
            },
        ).to_dict(),
        CandidateSetCertificate(
            scope_id=f"fi:{statute_id}:strict-report-source-lineage-units",
            candidate_set_kind="fi_strict_report_source_lineage_units",
            phase="source_chain_elaboration",
            rule_id="fi_strict_report_source_lineage_candidate_projection",
            reason=(
                "Source-lineage witnesses are accounted for when available, but "
                "they are amendment-chain witnesses rather than a full source-unit enumeration."
            ),
            completeness_status=(
                CANDIDATE_SET_PARTIAL if source_unit_ids else CANDIDATE_SET_UNAVAILABLE
            ),
            candidate_count=len(source_unit_ids),
            candidate_ids=source_unit_ids,
            missing_candidate_count=1,
            blocker_counts={"source_unit_enumeration_exhaustiveness_unproved": 1},
            blocker_families=("source_unit_enumeration_exhaustiveness_unproved",),
            next_promotion_allowed=False,
            next_promotion_requires=(
                "source_unit_enumeration_certificate",
                "source_unit_digest_coverage",
            ),
            detail={
                "visible_scope": "strict_report_source_lineage_witnesses",
                "safe_default": "do_not_treat_lineage_witnesses_as_full_source_unit_enumeration",
            },
        ).to_dict(),
        CandidateSetCertificate(
            scope_id=f"fi:{statute_id}:strict-report-source-unit-enumeration",
            candidate_set_kind="fi_strict_report_source_unit_enumeration",
            phase="source_unit_enumeration",
            rule_id="fi_strict_report_source_unit_enumeration_gap_certificate",
            reason=(
                "Strict-report evidence does not yet include an independent "
                "enumeration of all amendment-bearing source units."
            ),
            completeness_status=(
                CANDIDATE_SET_PARTIAL if source_unit_ids else CANDIDATE_SET_UNAVAILABLE
            ),
            candidate_count=len(source_unit_ids),
            candidate_ids=source_unit_ids,
            missing_candidate_count=1,
            blocker_counts={"source_unit_enumeration_not_complete": 1},
            blocker_families=("source_unit_enumeration_gap",),
            next_promotion_allowed=False,
            next_promotion_requires=(
                "source_artifact_unit_inventory",
                "source_unit_digest_coverage",
                "unclassified_source_unit_count_zero",
            ),
            detail={
                "visible_scope": "strict_report_negative_space_source_units",
                "source_unit_coverage_count": len(source_unit_coverages),
                "source_lineage_witness_count": len(source_lineage_witnesses),
                "safe_default": "do_not_treat_this_as_source_unit_closure_until_complete",
            },
        ).to_dict(),
        CandidateSetCertificate(
            scope_id=f"fi:{statute_id}:strict-report-operation-cue-coverage",
            candidate_set_kind="fi_strict_report_operation_cue_coverage",
            phase="operation_cue_detection",
            rule_id="fi_strict_report_operation_cue_coverage_gap_certificate",
            reason=(
                "Strict-report visible operation rows do not prove that all "
                "source-text operation cues were detected or classified."
            ),
            completeness_status=(
                CANDIDATE_SET_PARTIAL if visible_operation_ids else CANDIDATE_SET_UNAVAILABLE
            ),
            candidate_count=len(visible_operation_ids),
            candidate_ids=visible_operation_ids,
            missing_candidate_count=1,
            blocker_counts={"operation_cue_exhaustiveness_unproved": 1},
            blocker_families=("operation_cue_coverage_gap",),
            next_promotion_allowed=False,
            next_promotion_requires=(
                "independent_source_text_cue_detector",
                "operation_cue_classification_report",
                "parser_gap_frontier_items_for_unclassified_cues",
            ),
            detail={
                "visible_scope": "strict_report_potential_operation_rows",
                "visible_operation_row_count": len(visible_operation_ids),
                "potential_operation_row_count": len(potential_operations),
                "safe_default": "do_not_treat_visible_operation_rows_as_operation_cue_closure",
            },
        ).to_dict(),
    ]


def finland_strict_report_potential_operation_rows(
    payload: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Potential-operation coverage rows for visible strict-report operations.

    These rows are a typed census of visible canonical/failed operation rows.
    They intentionally do not prove that all source-text operation cues were
    independently detected.
    """

    statute_id = str(payload.get("statute_id") or "unknown")
    canonical_count = _ops_count(payload, "canonical")
    canonical_op_ids = _string_sequence(payload.get("canonical_op_ids"))
    failed_ops = _mapping_sequence(payload.get("failed_ops"))
    failed_operation_frontiers = _failed_operation_frontier_by_candidate_id(
        _mapping_sequence(payload.get("failed_operation_frontier_work_items"))
    )
    rows: list[dict[str, Any]] = []
    for index, op_id in enumerate(canonical_op_ids[:canonical_count], start=1):
        visible_id = op_id or f"canonical-op:{index}"
        rows.append(_strict_report_canonical_potential_operation(
            statute_id=statute_id,
            visible_id=visible_id,
            visible_index=index,
            synthesized=False,
        ))
    for index in range(len(rows), canonical_count):
        visible_id = f"canonical-op:{index + 1}"
        rows.append(_strict_report_canonical_potential_operation(
            statute_id=statute_id,
            visible_id=visible_id,
            visible_index=index + 1,
            synthesized=True,
        ))
    for index, row in enumerate(failed_ops):
        candidate_id = _failed_operation_candidate_id(index=index, row=row)
        failed_row = _failed_operation_row(row)
        source_anchor = _failed_operation_potential_source_anchor(
            failed_operation_frontiers.get(candidate_id)
        )
        rows.append(
            PotentialOperation(
                potential_operation_id=candidate_id,
                jurisdiction="fi",
                source_artifact_id=str(
                    failed_row.get("source_statute")
                    or failed_row.get("amendment_id")
                    or statute_id
                ),
                source_unit_id=str(
                    failed_row.get("target_label")
                    or failed_row.get("description")
                    or f"failed-operation:{index + 1}"
                ),
                owner_phase="replay_apply",
                classification=POTENTIAL_OPERATION_FAILED,
                operation_family="fi_failed_operation",
                action=str(row.get("action") or ""),
                target=str(failed_row.get("target_label") or ""),
                source_anchor=source_anchor,
                refs=(),
                required_proofs=_FAILED_OPERATION_REQUIRED_PROOFS,
                safe_default=_FAILED_OPERATION_SAFE_DEFAULT,
                detail={
                    "statute_id": statute_id,
                    "visible_index": index + 1,
                    "failed_operation": failed_row,
                    "projection_only": True,
                },
            ).to_dict()
        )
    return rows


def _failed_operation_frontier_by_candidate_id(
    frontier_items: tuple[Mapping[str, Any], ...],
) -> dict[str, Mapping[str, Any]]:
    rows: dict[str, Mapping[str, Any]] = {}
    for index, item in enumerate(frontier_items):
        detail = _mapping_or_empty(item.get("detail"))
        failed_operation = _mapping_or_empty(detail.get("failed_operation"))
        candidate_id = _failed_operation_candidate_id(index=index, row=failed_operation)
        rows[candidate_id] = item
    return rows


def _failed_operation_potential_source_anchor(
    frontier: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if frontier is None:
        return {}
    source_witness = _mapping_or_empty(frontier.get("source_witness"))
    return {
        "basis": "failed_operation_frontier_source_witness",
        "frontier_work_item_id": str(frontier.get("work_item_id") or ""),
        "frontier_status": str(frontier.get("frontier_status") or ""),
        "authorization_status": str(frontier.get("authorization_status") or ""),
        "required_claim_kind": str(frontier.get("required_claim_kind") or ""),
        "source_artifact_id": str(frontier.get("source_artifact_id") or ""),
        "source_unit_id": str(frontier.get("source_unit_id") or ""),
        "source_role": str(source_witness.get("source_role") or ""),
        "source_lane": str(source_witness.get("source_lane") or ""),
        "preview_digest_algorithm": str(source_witness.get("preview_digest_algorithm") or ""),
        "preview_digest": str(source_witness.get("preview_digest") or ""),
        "projection_only": True,
        "does_not_claim": [
            "operation_cue_exhaustiveness",
            "source_unit_enumeration_closure",
            "replay_authorization",
        ],
    }


def finland_strict_report_source_unit_coverage_rows(
    payload: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Source-unit coverage rows visible to the Finland strict report.

    This projects source-lineage and frontier source witnesses. It is useful
    accounting, but it is not a full inventory of amendment-bearing XML units.
    """

    statute_id = str(payload.get("statute_id") or "unknown")
    source_lineage_witnesses = _mapping_sequence(payload.get("source_lineage_source_witnesses"))
    source_pathology_frontiers = _mapping_sequence(payload.get("source_pathology_frontier_work_items"))
    failed_operation_frontiers = _mapping_sequence(payload.get("failed_operation_frontier_work_items"))
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for index, witness in enumerate(source_lineage_witnesses, start=1):
        artifact_id = str(witness.get("artifact_id") or witness.get("amendment_id") or statute_id)
        source_unit_id = str(witness.get("source_unit_id") or artifact_id or f"lineage:{index}")
        _append_source_unit_coverage_row(
            rows,
            seen=seen,
            statute_id=statute_id,
            artifact_id=artifact_id,
            source_unit_id=source_unit_id,
            owner_phase="source_chain_elaboration",
            coverage_status=SOURCE_UNIT_LINEAGE_WITNESSED,
            unit_family="finland_source_lineage_amendment",
            source_role=str(witness.get("source_role") or "finland_source_lineage_amendment"),
            source_lane=str(witness.get("source_lane") or "finland_source_adjudication_lineage"),
            witness=witness,
            index=index,
            safe_default="treat_lineage_source_unit_coverage_as_witnessed_only_not_full_enumeration",
        )
    frontier_index = len(rows)
    for frontier in (*source_pathology_frontiers, *failed_operation_frontiers):
        witness = frontier.get("source_witness")
        if not isinstance(witness, Mapping):
            continue
        frontier_index += 1
        artifact_id = str(
            witness.get("artifact_id")
            or witness.get("source_statute")
            or frontier.get("source_artifact_id")
            or statute_id
        )
        source_unit_id = str(
            witness.get("source_unit_id")
            or frontier.get("source_unit_id")
            or frontier.get("work_item_id")
            or f"frontier:{frontier_index}"
        )
        _append_source_unit_coverage_row(
            rows,
            seen=seen,
            statute_id=statute_id,
            artifact_id=artifact_id,
            source_unit_id=source_unit_id,
            owner_phase=str(frontier.get("owner_phase") or "frontier_projection"),
            coverage_status=SOURCE_UNIT_FRONTIER_WITNESSED,
            unit_family=str(frontier.get("frontier_family") or "finland_frontier_source_unit"),
            source_role=str(witness.get("source_role") or "finland_frontier_source_unit"),
            source_lane=str(witness.get("source_lane") or frontier.get("frontier_status") or "frontier"),
            witness=witness,
            index=frontier_index,
            safe_default="treat_frontier_source_unit_coverage_as_witnessed_only_not_full_enumeration",
            frontier=frontier,
        )
    return rows


def finland_strict_report_candidate_set_execution_authorizations(
    payload: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Replay-authorization boundary rows for strict-report candidate sets."""

    rows = _mapping_sequence(payload.get("strict_report_candidate_set_certificates"))
    authorizations: list[dict[str, Any]] = []
    for row in rows:
        candidate_set_kind = str(row.get("candidate_set_kind") or "unknown")
        completeness_status = str(row.get("completeness_status") or "unknown")
        complete = completeness_status == CANDIDATE_SET_COMPLETE
        required_proofs = _string_sequence(row.get("next_promotion_requires")) or (
            "candidate_set_completion_proof",
            "execution_authorization",
        )
        authorization = ExecutionAuthorization(
            executable=False,
            replay_authorized=False,
            authorization_status=(
                "candidate_set_complete_not_replay_authority"
                if complete
                else "candidate_set_incomplete_not_replay_authority"
            ),
            authorization_rule_id=f"fi_strict_report_candidate_set_{_kind_slug(candidate_set_kind)}",
            owner_phase=str(row.get("phase") or "strict_report_projection"),
            strict_disposition="record" if complete else "block",
            quirks_disposition="record",
            validator_status=(
                "candidate_set_complete_requires_separate_execution_authorization"
                if complete
                else "candidate_set_incomplete_requires_missing_coverage_proofs"
            ),
            required_proofs=required_proofs,
            safe_default="do_not_treat_candidate_set_certificate_as_replay_authorization",
            forbidden_shortcuts=(
                "candidate_set_certificate_as_replay_authorization",
                "visible_candidate_set_as_source_cue_exhaustiveness_proof",
                "partial_candidate_set_as_target_uniqueness_proof",
            ),
            detail={
                "jurisdiction": "fi",
                "candidate_set_kind": candidate_set_kind,
                "completeness_status": completeness_status,
                "scope_id": str(row.get("scope_id") or ""),
                "candidate_count": int(row.get("candidate_count") or 0),
                "missing_candidate_count": int(row.get("missing_candidate_count") or 0),
                "blocker_counts": dict(row.get("blocker_counts") or {}),
                "projection_only": True,
            },
        ).to_dict()
        authorization["row_id"] = _strict_report_candidate_set_authorization_row_id(
            candidate_set_kind=candidate_set_kind,
            scope_id=str(row.get("scope_id") or ""),
            completeness_status=completeness_status,
        )
        authorization["candidate_set_kind"] = candidate_set_kind
        authorization["completeness_status"] = completeness_status
        authorization["scope_id"] = str(row.get("scope_id") or "")
        authorizations.append(authorization)
    return authorizations


def _append_source_unit_coverage_row(
    rows: list[dict[str, Any]],
    *,
    seen: set[tuple[str, str, str, str]],
    statute_id: str,
    artifact_id: str,
    source_unit_id: str,
    owner_phase: str,
    coverage_status: SourceUnitCoverageStatus,
    unit_family: str,
    source_role: str,
    source_lane: str,
    witness: Mapping[str, Any],
    index: int,
    safe_default: str,
    frontier: Mapping[str, Any] | None = None,
) -> None:
    key = (artifact_id, source_unit_id, coverage_status, source_lane)
    if key in seen:
        return
    seen.add(key)
    detail: dict[str, Any] = {
        "statute_id": statute_id,
        "visible_index": index,
        "source_witness": dict(witness),
        "projection_only": True,
        "does_not_claim": [
            "complete_source_unit_enumeration",
            "operation_cue_exhaustiveness",
            "replay_authorization",
        ],
    }
    if frontier is not None:
        detail["frontier_work_item_id"] = str(frontier.get("work_item_id") or "")
        detail["frontier_status"] = str(frontier.get("frontier_status") or "")
    rows.append(
        SourceUnitCoverage(
            coverage_id=_strict_report_source_unit_coverage_id(
                statute_id=statute_id,
                artifact_id=artifact_id,
                source_unit_id=source_unit_id,
                index=index,
            ),
            jurisdiction="fi",
            source_artifact_id=artifact_id,
            source_unit_id=source_unit_id,
            owner_phase=owner_phase,
            coverage_status=coverage_status,
            unit_family=unit_family,
            source_role=source_role,
            source_lane=source_lane,
            refs=(artifact_id,),
            required_proofs=(
                "source_artifact_unit_inventory",
                "source_unit_digest_coverage",
                "unclassified_source_unit_count_zero",
            ),
            safe_default=safe_default,
            detail=detail,
        ).to_dict()
    )


def finland_strict_report_candidate_set_frontier_work_items(
    payload: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Project incomplete strict-report candidate sets as actionable work.

    These rows are not manual-claim templates and not replay authority. They
    simply make the missing coverage-certificate work queue machine-visible.
    """

    statute_id = str(payload.get("statute_id") or "unknown")
    rows = _mapping_sequence(payload.get("strict_report_candidate_set_certificates"))
    frontier_items: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        completeness_status = str(row.get("completeness_status") or "")
        if completeness_status == CANDIDATE_SET_COMPLETE:
            continue
        candidate_set_kind = str(row.get("candidate_set_kind") or "unknown")
        scope_id = str(row.get("scope_id") or candidate_set_kind)
        required_proofs = _string_sequence(row.get("next_promotion_requires")) or (
            "candidate_set_completion_certificate",
        )
        item = FrontierWorkItem(
            work_item_id=_strict_report_candidate_set_frontier_work_item_id(
                statute_id=statute_id,
                candidate_set_kind=candidate_set_kind,
                scope_id=scope_id,
                completeness_status=completeness_status,
                index=index,
            ),
            jurisdiction="fi",
            source_artifact_id=f"fi:{statute_id}:strict-report-candidate-sets",
            source_unit_id=scope_id,
            source_witness={
                "source_role": "finland_strict_report_candidate_set",
                "artifact_id": f"fi:{statute_id}:strict-report-candidate-sets",
                "source_unit_id": scope_id,
                "source_lane": "strict_report_candidate_set",
            },
            target_witness={
                "candidate_set_kind": candidate_set_kind,
                "scope_id": scope_id,
                "completeness_status": completeness_status,
                "candidate_count": int(row.get("candidate_count") or 0),
                "missing_candidate_count": int(row.get("missing_candidate_count") or 0),
                "blocker_counts": dict(row.get("blocker_counts") or {}),
            },
            owner_phase=str(row.get("phase") or "strict_report_projection"),
            frontier_family=f"fi_{_kind_slug(candidate_set_kind)}_coverage_gap",
            frontier_status=f"{completeness_status or 'unknown'}_candidate_set_frontier",
            candidate_operation_family="candidate_set_coverage_completion",
            candidate_targets=(scope_id,),
            guidance_refs=("lawvm_candidate_set_completion_review",),
            required_claim_kind=_candidate_set_frontier_required_claim_kind(candidate_set_kind),
            required_validator_checks=(
                "validate_candidate_set_completion_certificate",
                "validate_no_replay_promotion_from_partial_candidate_set",
            ),
            required_proofs=required_proofs,
            safe_default="keep_candidate_set_gap_open_until_completion_certificate_exists",
            forbidden_shortcuts=(
                "candidate_set_frontier_as_replay_authorization",
                "candidate_set_frontier_as_source_cue_exhaustiveness_proof",
                "partial_candidate_set_as_complete_candidate_set",
            ),
            executable=False,
            replay_authorized=False,
            authorization_status=(
                "candidate_set_incomplete_not_replay_authority"
                if completeness_status != CANDIDATE_SET_COMPLETE
                else "candidate_set_complete_not_replay_authority"
            ),
            detail={
                "candidate_set": dict(row),
                "projection_only": True,
                "does_not_claim": [
                    "candidate_set_completion",
                    "operation_cue_exhaustiveness",
                    "source_unit_enumeration_closure",
                    "replay_authorization",
                ],
            },
        )
        frontier_items.append(item.to_dict())
    return frontier_items


def finland_strict_report_ownership_closure_certificate(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Build a passive ownership-closure certificate for strict-report facts.

    This certificate is deliberately scoped to the strict-report proof surfaces.
    It stays open until source-unit enumeration and operation-candidate coverage
    are backed by real certificates; those missing proof boundaries are not
    papered over by a green strict-report row.
    """

    statute_id = str(payload.get("statute_id") or "unknown")
    profile_id = str(payload.get("profile") or "unknown_profile")
    projection_rows = _mapping_sequence(payload.get("projection_rows"))
    failed_ops = _mapping_sequence(payload.get("failed_ops"))
    strict_fail_reasons = _string_sequence(payload.get("strict_fail_reasons"))
    source_pathologies = _mapping_sequence(payload.get("source_pathologies"))
    source_pathology_frontier_items = _mapping_sequence(payload.get("source_pathology_frontier_work_items"))
    failed_operation_frontier_items = _mapping_sequence(payload.get("failed_operation_frontier_work_items"))
    potential_operations = _mapping_sequence(payload.get("potential_operations"))
    source_unit_coverages = _mapping_sequence(payload.get("source_unit_coverages"))
    sparse_certificates = _mapping_sequence(payload.get("sparse_slot_candidate_set_certificates"))
    source_lineage_witnesses = _mapping_sequence(payload.get("source_lineage_source_witnesses"))
    agreement_residuals = _mapping_sequence(payload.get("agreement_residuals"))
    mutation_boundary_proofs = _mapping_sequence(payload.get("mutation_boundary_proofs"))
    candidate_set_certificates = _mapping_sequence(payload.get("strict_report_candidate_set_certificates"))
    candidate_set_authorizations = _mapping_sequence(payload.get("strict_report_candidate_set_execution_authorizations"))
    candidate_set_frontier_items = _mapping_sequence(payload.get("strict_report_candidate_set_frontier_work_items"))
    source_completeness = _mapping_or_empty(payload.get("source_completeness"))
    temporal_rows = temporal_resolution_evidence_rows_from_projection_rows(
        projection_rows,
        strict_fail_reasons=strict_fail_reasons,
    )
    recovery_authorizations = recovery_execution_authorization_rows_from_projection_rows(
        projection_rows,
        strict_fail_reasons=strict_fail_reasons,
        statute_id=statute_id,
    )
    unproved_mutation_boundaries = tuple(
        row for row in mutation_boundary_proofs if str(row.get("status") or "") != "proved"
    )
    incomplete_candidate_sets = tuple(
        row
        for row in candidate_set_certificates
        if str(row.get("completeness_status") or "") != "complete"
    )
    candidate_set_authorization_keys = {
        _strict_report_candidate_set_authorization_key(row)
        for row in candidate_set_authorizations
        if _strict_report_candidate_set_authorization_key(row) is not None
    }
    candidate_sets_without_authorization = tuple(
        row
        for row in candidate_set_certificates
        if _strict_report_candidate_set_authorization_key(row)
        not in candidate_set_authorization_keys
    )
    candidate_set_frontier_keys = {
        _strict_report_candidate_set_frontier_key(row)
        for row in candidate_set_frontier_items
        if _strict_report_candidate_set_frontier_key(row) is not None
    }
    incomplete_candidate_sets_without_frontier = tuple(
        row
        for row in incomplete_candidate_sets
        if _strict_report_candidate_set_frontier_key(row) not in candidate_set_frontier_keys
    )
    failed_gates = (
        *(
            (
                "source_unit_enumeration_closure_unverified",
                "operation_candidate_coverage_unverified",
            )
            if not candidate_set_certificates
            else ()
        ),
        *(
            f"candidate_set_{str(row.get('candidate_set_kind') or 'unknown')}_{str(row.get('completeness_status') or 'unknown')}"
            for row in incomplete_candidate_sets
        ),
        *(
            "candidate_set_"
            f"{str(row.get('candidate_set_kind') or 'unknown')}_"
            f"{str(row.get('scope_id') or 'unknown')}_"
            "execution_authorization_missing"
            for row in candidate_sets_without_authorization
        ),
        *(() if not failed_ops else ("failed_ops_present",)),
        *(() if not strict_fail_reasons else ("strict_fail_reasons_present",)),
        *(() if not unproved_mutation_boundaries else ("unproved_mutation_boundary_present",)),
    )
    unowned_counts = {
        "source_units_without_enumeration_certificate": 1
        if not _has_candidate_set(candidate_set_certificates, "fi_strict_report_source_unit_enumeration")
        else 0,
        "operation_cues_without_candidate_coverage_certificate": 1
        if not _has_candidate_set(candidate_set_certificates, "fi_strict_report_operation_cue_coverage")
        else 0,
        "incomplete_candidate_set_certificates": len(incomplete_candidate_sets),
        "candidate_set_certificates_without_execution_authorization": len(
            candidate_sets_without_authorization
        ),
        "incomplete_candidate_set_certificates_without_frontier_work_item": len(
            incomplete_candidate_sets_without_frontier
        ),
        "failed_ops_without_frontier_work_item": max(
            len(failed_ops) - len(failed_operation_frontier_items),
            0,
        ),
        "strict_fail_reasons_without_closure": len(strict_fail_reasons),
        "unproved_mutation_boundary_proofs": len(unproved_mutation_boundaries),
    }
    closed = not failed_gates and all(count == 0 for count in unowned_counts.values())
    missing_required_certificates = (
        *_closure_certificate_requirements(
            candidate_set_certificates,
            candidate_set_kind="fi_strict_report_source_unit_enumeration",
            missing_certificate="source_unit_enumeration_certificate",
            incomplete_certificate="complete_source_unit_enumeration_certificate",
        ),
        *_closure_certificate_requirements(
            candidate_set_certificates,
            candidate_set_kind="fi_strict_report_operation_cue_coverage",
            missing_certificate="operation_candidate_coverage_certificate",
            incomplete_certificate="complete_operation_cue_exhaustiveness_certificate",
        ),
        *(
            "candidate_set_execution_authorization:"
            f"{str(row.get('candidate_set_kind') or 'unknown')}:"
            f"{str(row.get('scope_id') or 'unknown')}"
            for row in candidate_sets_without_authorization
        ),
        *(
            "candidate_set_frontier_work_item:"
            f"{str(row.get('candidate_set_kind') or 'unknown')}:"
            f"{str(row.get('scope_id') or 'unknown')}"
            for row in incomplete_candidate_sets_without_frontier
        ),
    )
    closure_dimensions = (
        "visible_operation_rows",
        "source_lineage_units",
        "source_unit_enumeration",
        "operation_cue_coverage",
        "failed_operations",
        "strict_fail_reasons",
        "mutation_boundary_proofs",
    )
    does_not_claim = (
        "full_finland_corpus_closure",
        *(
            ()
            if _candidate_set_complete(
                candidate_set_certificates,
                "fi_strict_report_source_unit_enumeration",
            )
            else ("source_unit_enumeration_closure",)
        ),
        *(
            ()
            if _candidate_set_complete(
                candidate_set_certificates,
                "fi_strict_report_operation_cue_coverage",
            )
            else ("operation_candidate_coverage_closure",)
        ),
        "replay_authorization",
    )
    owned_counts = {
        "canonical_ops": _ops_count(payload, "canonical"),
        "failed_ops_visible": len(failed_ops),
        "failed_operation_frontier_items": len(failed_operation_frontier_items),
        "potential_operations": len(potential_operations),
        "projection_rows_visible": len(projection_rows),
        "source_pathology_frontier_items": len(source_pathology_frontier_items),
        "source_pathologies_visible": len(source_pathologies),
        "sparse_slot_candidate_certificates": len(sparse_certificates),
        "source_lineage_witnesses": len(source_lineage_witnesses),
        "source_unit_coverages": len(source_unit_coverages),
        "agreement_residuals": len(agreement_residuals),
        "mutation_boundary_proofs": len(mutation_boundary_proofs),
        "temporal_resolution_evidence_rows": len(temporal_rows),
        "recovery_authorization_rows": len(recovery_authorizations),
        "strict_report_candidate_set_certificates": len(candidate_set_certificates),
        "strict_report_candidate_set_authorizations": len(candidate_set_authorizations),
        "strict_report_candidate_set_frontier_items": len(candidate_set_frontier_items),
    }
    certificate = OwnershipClosureCertificate(
        certificate_id=_strict_report_id("ownership-closure", statute_id, payload),
        corpus_slice_id=f"fi:{statute_id}:strict-report-visible-surfaces",
        source_bundle_hash=_strict_report_digest(
            "source-bundle",
            {
                "source_completeness": source_completeness,
                "source_lineage_source_witnesses": source_lineage_witnesses,
                "source_unit_coverages": source_unit_coverages,
            },
        ),
        profile_id=profile_id,
        interpretation_policy_id="fi.strict_report.visible_surfaces.v1",
        graph_snapshot_hash=_strict_report_digest(
            "strict-report-graph",
            _strict_report_closure_graph_payload(payload),
        ),
        phase_report_ids={
            "strict_report": _strict_report_id("strict-report", statute_id, payload),
            "source_lineage_witnesses": _strict_report_id(
                "source-lineage",
                statute_id,
                source_lineage_witnesses,
            ),
            "source_pathology_frontiers": _strict_report_id(
                "source-pathology-frontiers",
                statute_id,
                source_pathology_frontier_items,
            ),
            "failed_operation_frontiers": _strict_report_id(
                "failed-operation-frontiers",
                statute_id,
                failed_operation_frontier_items,
            ),
            "potential_operations": _strict_report_id(
                "potential-operations",
                statute_id,
                potential_operations,
            ),
            "source_unit_coverages": _strict_report_id(
                "source-unit-coverages",
                statute_id,
                source_unit_coverages,
            ),
            "mutation_boundary_proofs": _strict_report_id(
                "mutation-boundary",
                statute_id,
                mutation_boundary_proofs,
            ),
            "strict_report_candidate_sets": _strict_report_id(
                "strict-report-candidate-sets",
                statute_id,
                candidate_set_certificates,
            ),
            "strict_report_candidate_set_authorizations": _strict_report_id(
                "strict-report-candidate-set-authorizations",
                statute_id,
                candidate_set_authorizations,
            ),
            "strict_report_candidate_set_frontiers": _strict_report_id(
                "strict-report-candidate-set-frontiers",
                statute_id,
                candidate_set_frontier_items,
            ),
        },
        closed=closed,
        failed_gates=tuple(failed_gates),
        unowned_counts=unowned_counts,
        owned_counts=owned_counts,
        detail={
            "scope": "strict_report_visible_surfaces_only",
            "closure_dimensions": closure_dimensions,
            "does_not_claim": does_not_claim,
            "safe_default": "treat_open_certificate_as_accounting_gap_not_replay_failure",
            "missing_required_certificates": missing_required_certificates,
        },
    )
    return certificate.to_dict()


def finland_strict_report_ownership_closure_report(
    certificate: Mapping[str, Any],
) -> dict[str, Any]:
    """Wrap one strict-report closure certificate in a passive evidence report."""

    cert = OwnershipClosureCertificate(
        certificate_id=str(certificate.get("certificate_id") or ""),
        corpus_slice_id=str(certificate.get("corpus_slice_id") or ""),
        source_bundle_hash=str(certificate.get("source_bundle_hash") or ""),
        profile_id=str(certificate.get("profile_id") or ""),
        interpretation_policy_id=str(certificate.get("interpretation_policy_id") or ""),
        graph_snapshot_hash=str(certificate.get("graph_snapshot_hash") or ""),
        phase_report_ids=_mapping_str_str(certificate.get("phase_report_ids")),
        closed=bool(certificate.get("closed")),
        failed_gates=tuple(str(item) for item in certificate.get("failed_gates", ()) or ()),
        unowned_counts=_mapping_str_int(certificate.get("unowned_counts")),
        owned_counts=_mapping_str_int(certificate.get("owned_counts")),
        detail=_mapping_or_empty(certificate.get("detail")),
    )
    return ownership_closure_evidence_report(
        cert,
        jurisdiction="fi",
        report_kind="finland_strict_report_ownership_closure",
    ).to_dict()


def _strict_report_agreement_surface_rows(
    agreement_residuals: tuple[Mapping[str, Any], ...],
    *,
    payload: Mapping[str, Any],
) -> tuple[tuple[Mapping[str, Any], ...], Mapping[str, Any]]:
    if not agreement_residuals:
        return (), {}
    statute_id = str(payload.get("statute_id") or "unknown")
    surface = agreement_surface_from_residuals(
        agreement_residuals,
        jurisdiction="fi",
        agreement_surface="finlex_oracle_compare",
        materialization_id=f"fi:{statute_id}:materialization",
        comparison_target_id=f"finlex:{statute_id}",
        comparison_kind="residual_classification",
        materialization_kind="legal_text_state",
        comparison_materialization_kind="official_consolidation_view",
        profile_id=str(payload.get("profile") or ""),
    )
    report = agreement_surface_evidence_report(
        surface,
        report_kind="finland_agreement_surface",
    ).to_dict()
    return _mapping_sequence(report.get("rows")), dict(report.get("summary") or {})


def source_completeness_status_row(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Project strict-report source-completeness counts into a passive row."""
    status = source_completeness_status_from_mapping(payload, jurisdiction="fi")
    if status is None:
        return {}
    return status.to_dict()


def temporal_resolution_evidence_rows_from_projection_rows(
    projection_rows: tuple[Mapping[str, Any], ...],
    *,
    strict_fail_reasons: tuple[str, ...] = (),
) -> tuple[dict[str, Any], ...]:
    """Project existing Finland TIME.* findings into shared temporal evidence rows."""
    fail_reason_set = {str(reason) for reason in strict_fail_reasons}
    rows: list[dict[str, Any]] = []
    for row in projection_rows:
        kind = str(row.get("kind") or "")
        if kind not in {
            "TIME.ESTIMATED_EFFECTIVE_DATE",
            "TIME.CONTINGENT_EFFECTIVE_DATE",
        }:
            continue
        detail_raw = row.get("detail")
        detail: Mapping[str, Any] = detail_raw if isinstance(detail_raw, Mapping) else {}
        rows.append(
            TemporalResolutionEvidence(
                rule_id=_temporal_resolution_rule_id(kind),
                phase="temporal_elaboration",
                reason=str(row.get("message") or _temporal_resolution_reason(kind)),
                status=_temporal_resolution_status(kind),
                family=TEMPORAL_RECOVERY_FAMILY,
                blocking=kind in fail_reason_set,
                source_locator=str(row.get("source") or ""),
                strict_disposition="block" if kind in fail_reason_set else "record",
                quirks_disposition="record",
                detail={
                    "finding_kind": kind,
                    "step": str(detail.get("step") or ""),
                    "source_statute": str(row.get("source") or ""),
                },
            ).to_diagnostic_detail()
        )
    return tuple(rows)


def recovery_execution_authorization_rows_from_projection_rows(
    projection_rows: tuple[Mapping[str, Any], ...],
    *,
    strict_fail_reasons: tuple[str, ...] = (),
    statute_id: str = "",
) -> tuple[dict[str, Any], ...]:
    """Project Finland recovery/strictness findings into non-executable authorizations."""

    fail_reason_set = {str(reason) for reason in strict_fail_reasons}
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for index, row in enumerate(projection_rows, start=1):
        kind = str(row.get("kind") or "")
        rule = _RECOVERY_AUTHORIZATION_RULES.get(kind)
        if rule is None:
            continue
        detail_raw = row.get("detail")
        detail: Mapping[str, Any] = detail_raw if isinstance(detail_raw, Mapping) else {}
        source_statute = str(row.get("source") or detail.get("source_statute") or detail.get("amendment_id") or "")
        message = str(row.get("message") or "")
        key = (kind, source_statute, message)
        if key in seen:
            continue
        seen.add(key)
        blocking = kind in fail_reason_set or rule.strict_disposition == "block"
        authorization = ExecutionAuthorization(
            executable=False,
            replay_authorized=False,
            authorization_status="strict_recovery_blocked" if blocking else "recovery_projection_not_replay_authority",
            authorization_rule_id=f"fi_recovery_{_kind_slug(kind)}",
            owner_phase=rule.owner_phase,
            strict_disposition="block" if blocking else rule.strict_disposition,
            quirks_disposition=rule.quirks_disposition,
            validator_status=rule.validator_status,
            required_proofs=rule.required_proofs,
            safe_default=rule.safe_default,
            forbidden_shortcuts=rule.forbidden_shortcuts,
            detail={
                "jurisdiction": "fi",
                "statute_id": statute_id,
                "finding_kind": kind,
                "family": rule.family,
                "source_statute": source_statute,
                "message": message,
                "strict_fail_reason_present": kind in fail_reason_set,
                "projection_only": True,
                "projection_detail": dict(detail),
            },
        ).to_dict()
        authorization["row_id"] = _recovery_authorization_row_id(
            statute_id=statute_id,
            index=index,
            kind=kind,
            source_statute=source_statute,
            message=message,
        )
        authorization["finding_kind"] = kind
        authorization["family"] = rule.family
        if source_statute:
            authorization["source_artifact_id"] = source_statute
        rows.append(authorization)
    return tuple(rows)


def _temporal_resolution_rule_id(kind: str) -> str:
    if kind == "TIME.CONTINGENT_EFFECTIVE_DATE":
        return "fi_time_contingent_effective_date"
    return "fi_time_estimated_effective_date"


def _temporal_resolution_status(kind: str) -> TemporalResolutionStatus:
    if kind == "TIME.CONTINGENT_EFFECTIVE_DATE":
        return TEMPORAL_UNRESOLVED_CONTINGENT
    return TEMPORAL_UNKNOWN_EFFECTIVE_DATE


def _temporal_resolution_reason(kind: str) -> str:
    if kind == "TIME.CONTINGENT_EFFECTIVE_DATE":
        return "effective date is contingent or decree-set"
    return "effective date was estimated or substituted from publication metadata"


def _kind_slug(kind: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "_" for ch in str(kind or "")).strip("_") or "unknown"


def _recovery_authorization_row_id(
    *,
    statute_id: str,
    index: int,
    kind: str,
    source_statute: str,
    message: str,
) -> str:
    payload = {
        "statute_id": statute_id,
        "index": index,
        "kind": kind,
        "source_statute": source_statute,
        "message": message,
    }
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=True, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    return f"fi:{statute_id or 'unknown'}:recovery-auth:{_kind_slug(kind)}:{digest}"


def finland_bench_run_evidence_surface(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Wrap a saved Finland benchmark run in a passive evidence envelope."""

    stats_raw = payload.get("stats")
    stats = dict(stats_raw) if isinstance(stats_raw, Mapping) else {}
    diagnostic_counts = dict(payload.get("diagnostic_summary_counts") or {})
    status_counts = dict(payload.get("status_counts") or {})
    summary = {
        "statute_count": int(stats.get("n") or 0),
        "error_count": int(stats.get("errors") or 0),
        "mean_score": stats.get("mean"),
        "perfect_count": int(stats.get("perfect") or 0),
        "above_99_count": int(stats.get("above_99") or 0),
        "above_95_count": int(stats.get("above_95") or 0),
        "below_90_count": int(stats.get("below_90") or 0),
        "status_counts": status_counts,
        "diagnostic_summary_counts": diagnostic_counts,
        "diagnostic_summary_row_count": int(payload.get("diagnostic_summary_row_count") or 0),
        "section_score": bool(payload.get("section_score") or False),
        "levenshtein_score": bool(payload.get("levenshtein_score") or False),
    }
    oracle_adjusted = payload.get("oracle_stale_adjusted")
    if isinstance(oracle_adjusted, Mapping):
        summary["oracle_stale_adjusted_mean"] = oracle_adjusted.get("mean")
        summary["oracle_stale_adjusted_excluded_count"] = len(oracle_adjusted.get("excluded") or ())
    return EvidenceSurfaceReport(
        jurisdiction="fi",
        report_kind="finland_bench_run",
        schema="lawvm.finland_bench_run.v1",
        truth_claim="finland_benchmark_agreement_regression_evidence_not_source_truth",
        replay_claims=False,
        canonical_effect_claims=False,
        candidate_effect_claims=False,
        dry_run_claims=False,
        agreement_claims=True,
        summary=summary,
        filters={
            "label": str(payload.get("label") or ""),
            "mode": str(payload.get("mode") or ""),
            "corpus_path": str(payload.get("corpus_path") or ""),
        },
        filtered_summary=summary,
        rows=(),
        rows_truncated=False,
        written_paths=tuple(str(path) for path in (payload.get("run_path"), payload.get("history_path")) if path),
        detail={
            "safe_default": "treat_benchmark_scores_as_regression_evidence_not_replay_authorization",
            "forbidden_shortcuts": (
                "bench_score_as_source_truth",
                "bench_score_as_replay_authorization",
                "diagnostics_summary_as_mutation_instruction",
                "oracle_adjusted_headline_as_legal_state",
                "run_csv_as_manual_claim_authority",
            ),
            "included_surfaces": (
                "saved_run_csv",
                "benchmark_history_csv",
                "status_counts",
                "diagnostic_summary_counts",
            ),
            "timestamp": str(payload.get("timestamp") or ""),
            "worker_count": int(payload.get("worker_count") or 0),
            "fast_mode": bool(payload.get("fast_mode") or False),
            "diagnostic_replay": bool(payload.get("diagnostic_replay") or False),
        },
    ).to_dict()


def finland_evidence_bundle_evidence_surface(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Wrap Finland evidence-bundle JSON in the shared evidence envelope."""

    html_topology = payload.get("html_topology")
    html_witnesses: tuple[Mapping[str, Any], ...] = ()
    if isinstance(html_topology, Mapping) and isinstance(html_topology.get("source_witness"), Mapping):
        html_witnesses = (html_topology["source_witness"],)
    supporting_amendments = _mapping_sequence(payload.get("supporting_amendments"))
    corrigendum_witnesses = tuple(
        witness
        for amendment in supporting_amendments
        for witness in _mapping_sequence(amendment.get("source_witnesses"))
    )
    source_witnesses = (*html_witnesses, *corrigendum_witnesses)
    proof_claims = _mapping_sequence(payload.get("proof_claims"))
    section_claims = _mapping_sequence(payload.get("section_claims"))
    source_pathologies = _mapping_sequence(payload.get("source_pathologies"))
    source_pathology_report = source_pathology_evidence_report(
        _source_pathology_projections(source_pathologies),
        jurisdiction="fi",
        report_kind="finland_evidence_bundle_source_pathology",
    ).to_dict()
    source_pathology_rows = _mapping_sequence(source_pathology_report.get("rows"))
    context_diagnostics = _mapping_sequence(payload.get("evidence_context_diagnostics"))
    section_bisect = _mapping_sequence(payload.get("section_bisect"))
    compiler_observations_raw = payload.get("compiler_observations")
    compiler_observations = dict(compiler_observations_raw) if isinstance(compiler_observations_raw, Mapping) else {}
    proof_tiers = _string_sequence(payload.get("proof_tiers"))
    rows = tuple(
        (
            *({**dict(row), "surface": "source_witness"} for row in source_witnesses),
            *({**dict(row), "surface": "proof_claim"} for row in proof_claims),
            *({"surface": "source_pathology", **dict(row)} for row in source_pathology_rows),
            *({**dict(row), "surface": "evidence_context_diagnostic"} for row in context_diagnostics),
        )
    )
    summary = {
        "proof_claim_count": len(proof_claims),
        "section_claim_count": len(section_claims),
        "source_pathology_count": len(source_pathology_rows),
        "source_pathology_kind_counts": dict(
            source_pathology_report.get("summary", {}).get("pathology_kind_counts", {})
        ),
        "source_pathology_affected_phase_counts": dict(
            source_pathology_report.get("summary", {}).get("affected_phase_counts", {})
        ),
        "source_pathology_suggested_lane_counts": dict(
            source_pathology_report.get("summary", {}).get("suggested_lane_counts", {})
        ),
        "supporting_amendment_count": len(supporting_amendments),
        "source_witness_count": len(source_witnesses),
        "html_topology_source_witness_count": len(html_witnesses),
        "corrigendum_source_witness_count": len(corrigendum_witnesses),
        "source_witness_digest_coverage_counts": source_witness_digest_coverage_counts(source_witnesses),
        "evidence_context_diagnostic_count": len(context_diagnostics),
        "section_bisect_row_count": len(section_bisect),
        "proof_tiers": proof_tiers,
        "primary_proof_tier": str(payload.get("primary_proof_tier") or ""),
        "overall_score": payload.get("overall_score"),
        "section_score": payload.get("section_score"),
        "compiler_normalized_observation_count": int(
            compiler_observations.get("normalized_section_observation_count") or 0
        ),
    }
    return EvidenceSurfaceReport(
        jurisdiction="fi",
        report_kind="finland_evidence_bundle",
        schema="lawvm.finland_evidence_bundle.v1",
        truth_claim="finland_oracle_review_and_proof_claim_diagnostics",
        replay_claims=False,
        canonical_effect_claims=False,
        candidate_effect_claims=False,
        dry_run_claims=False,
        agreement_claims=True,
        summary=summary,
        filters={
            "statute_id": str(payload.get("statute_id") or ""),
            "mode": str(payload.get("mode") or ""),
        },
        filtered_summary=summary,
        rows=rows,
        rows_truncated=False,
        detail={
            "safe_default": "treat_evidence_bundle_as_passive_diagnostics_not_replay_authorization",
            "forbidden_shortcuts": (
                "evidence_bundle_as_replay_authorization",
                "oracle_score_as_source_truth",
                "proof_claim_as_mutation_instruction",
                "html_topology_witness_as_raw_html_digest",
                "corrigendum_witness_as_manual_patch_authority",
            ),
            "included_surfaces": (
                "source_witness",
                "proof_claim",
                "source_pathology",
                "evidence_context_diagnostic",
            ),
        },
    ).to_dict()


def finland_frontier_proof_evidence_surface(
    *,
    rows: tuple[Mapping[str, Any], ...],
    summary: Mapping[str, Any],
    label: str,
    mode: str,
    top: int | None = None,
    bucket_filter: str = "",
) -> dict[str, Any]:
    """Wrap Finland frontier proof rows in the shared evidence envelope."""

    normalized_rows = tuple(dict(row) for row in rows)
    normalized_summary = dict(summary)
    row_surfaces = tuple({**row, "surface": "frontier_proof_row"} for row in normalized_rows)
    report_summary = {
        "frontier_proof_row_count": len(normalized_rows),
        "primary_tiers": dict(normalized_summary.get("primary_tiers") or {}),
        "proof_kinds": dict(normalized_summary.get("proof_kinds") or {}),
        "section_claim_kinds": dict(normalized_summary.get("section_claim_kinds") or {}),
        "statute_only_proof_kinds": dict(normalized_summary.get("statute_only_proof_kinds") or {}),
        "section_claim_rules": dict(normalized_summary.get("section_claim_rules") or {}),
        "defeated_section_claim_kinds": dict(normalized_summary.get("defeated_section_claim_kinds") or {}),
        "defeated_section_claim_rules": dict(normalized_summary.get("defeated_section_claim_rules") or {}),
        "alternative_replay_sections": dict(normalized_summary.get("alternative_replay_sections") or {}),
        "bucket_primary_tiers": dict(normalized_summary.get("bucket_primary_tiers") or {}),
    }
    return EvidenceSurfaceReport(
        jurisdiction="fi",
        report_kind="finland_frontier_proof_report",
        schema="lawvm.finland_frontier_proof_report.v1",
        truth_claim="finland_frontier_proof_diagnostics",
        replay_claims=False,
        canonical_effect_claims=False,
        candidate_effect_claims=False,
        dry_run_claims=False,
        agreement_claims=True,
        summary=report_summary,
        filters={
            "label": label,
            "mode": mode,
            "top": top if top is not None else "",
            "bucket_filter": bucket_filter,
        },
        filtered_summary=report_summary,
        rows=row_surfaces,
        rows_truncated=False,
        detail={
            "safe_default": "treat_frontier_proof_report_as_diagnostic_ranking_not_replay_authorization",
            "forbidden_shortcuts": (
                "frontier_rank_as_replay_authorization",
                "proof_tier_as_canonical_operation",
                "proof_row_as_mutation_instruction",
                "oracle_score_as_source_truth",
                "bucket_as_source_pathology_proof",
            ),
            "included_surfaces": ("frontier_proof_row",),
        },
    ).to_dict()


def finland_he_branch_evidence_surface(
    parsed: HEParsedBranch | Mapping[str, Any],
) -> dict[str, Any]:
    """Wrap a parsed Finland HE branch in a passive proof-surface envelope.

    HE branches are future-law/proposal diagnostics.  This surface makes typed
    proposal parsing visible without claiming enacted-law authority, canonical
    effects, dry-run authority, or agreement with an oracle.
    """

    proposed_ops = tuple(_field(parsed, "proposed_ops", ()) or ())
    parse_findings = tuple(_field(parsed, "parse_findings", ()) or ())
    target_statute_ids = tuple(str(item) for item in (_field(parsed, "target_statute_ids", ()) or ()) if str(item))
    branch_projection_row = _he_branch_impact_projection_row(
        parsed=parsed,
        proposed_ops=proposed_ops,
    )
    rows = (
        *(_he_branch_proposed_op_row(op) for op in proposed_ops),
        *(_he_branch_finding_row(finding) for finding in parse_findings),
        *((branch_projection_row,) if branch_projection_row is not None else ()),
    )
    parse_status = _enum_text(_field(parsed, "parse_status", ""))
    summary = {
        "proposed_op_count": len(proposed_ops),
        "target_statute_count": len(target_statute_ids),
        "branch_impact_projection_count": 1 if branch_projection_row is not None else 0,
        "branch_impact_row_count": (
            _nonnegative_int(branch_projection_row["branch_impact_row_count"])
            if branch_projection_row is not None
            else 0
        ),
        "parse_finding_count": len(parse_findings),
        "enactment_sections_found": _nonnegative_int(_field(parsed, "enactment_sections_found", 0)),
        "clauses_attempted": _nonnegative_int(_field(parsed, "clauses_attempted", 0)),
        "clauses_succeeded": _nonnegative_int(_field(parsed, "clauses_succeeded", 0)),
        "parse_status": parse_status,
        "proposal_relative_op_count": sum(
            1
            for op in proposed_ops
            if bool(_field(op, "is_proposal_relative", False))
            or _enum_text(_field(op, "target_resolution", "")) == "proposal_relative"
        ),
        "unresolved_target_finding_count": sum(
            1
            for finding in parse_findings
            if str(_field(finding, "rule_id", "")).startswith("HE_BRANCH.TARGET_")
        ),
    }
    voimaantulo = _field(parsed, "proposed_voimaantulo", None)
    return EvidenceSurfaceReport(
        jurisdiction="fi",
        report_kind="finland_he_branch",
        schema="lawvm.finland_he_branch.v1",
        truth_claim="finland_government_proposal_branch_parse_diagnostics",
        replay_claims=False,
        canonical_effect_claims=False,
        candidate_effect_claims=False,
        dry_run_claims=False,
        agreement_claims=False,
        summary=summary,
        filters={
            "branch_id": str(_field(parsed, "branch_id", "")),
            "he_id": str(_field(parsed, "he_id", "")),
            "parse_status": parse_status,
        },
        filtered_summary=summary,
        rows=rows,
        rows_truncated=False,
        detail={
            "safe_default": "treat_he_branch_rows_as_future_law_diagnostics_not_current_law_authority",
            "forbidden_shortcuts": _HE_BRANCH_FORBIDDEN_SHORTCUTS,
            "included_surfaces": (
                "he_branch_proposed_op",
                "he_branch_target_resolution_finding",
                "he_branch_parse_finding",
                "he_branch_impact_projection",
            ),
            "target_statute_ids": target_statute_ids,
            "he_year": _nonnegative_int(_field(parsed, "he_year", 0)),
            "he_number": _nonnegative_int(_field(parsed, "he_number", 0)),
            "proposed_voimaantulo": str(voimaantulo) if voimaantulo is not None else "",
        },
    ).to_dict()


def finland_corrigendum_review_evidence_surface(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Wrap Finland corrigendum review JSON in the shared evidence envelope."""

    amendments = _mapping_sequence(payload.get("amendments"))
    source_pathologies = _mapping_sequence(payload.get("source_pathologies"))
    source_pathology_report = source_pathology_evidence_report(
        _source_pathology_projections(source_pathologies),
        jurisdiction="fi",
        report_kind="finland_corrigendum_review_source_pathology",
    ).to_dict()
    source_pathology_rows = _mapping_sequence(source_pathology_report.get("rows"))
    unblamed_sections = _mapping_sequence(payload.get("unblamed_sections"))
    witnesses = tuple(
        witness
        for amendment in amendments
        for witness in _mapping_sequence(amendment.get("source_witnesses"))
    )
    rows = tuple(
        (
            *({**dict(row), "surface": "corrigendum_source_witness"} for row in witnesses),
            *({"surface": "source_pathology", **dict(row)} for row in source_pathology_rows),
            *({**dict(row), "surface": "corrigendum_review_amendment"} for row in amendments),
            *({**dict(row), "surface": "unblamed_section"} for row in unblamed_sections),
        )
    )
    summary = {
        "amendment_count": len(amendments),
        "source_pathology_count": len(source_pathology_rows),
        "source_pathology_kind_counts": dict(
            source_pathology_report.get("summary", {}).get("pathology_kind_counts", {})
        ),
        "source_pathology_affected_phase_counts": dict(
            source_pathology_report.get("summary", {}).get("affected_phase_counts", {})
        ),
        "source_pathology_suggested_lane_counts": dict(
            source_pathology_report.get("summary", {}).get("suggested_lane_counts", {})
        ),
        "unblamed_section_count": len(unblamed_sections),
        "contingent_effective_source_count": len(_string_sequence(payload.get("contingent_effective_sources"))),
        "corrigendum_source_witness_count": len(witnesses),
        "corrigendum_source_witness_digest_coverage_counts": source_witness_digest_coverage_counts(witnesses),
        "corrigendum_db_row_count": sum(int(amendment.get("corrigendum_db_rows") or 0) for amendment in amendments),
        "corrigendum_no_match_row_count": sum(
            int(amendment.get("corrigendum_no_match_rows") or 0) for amendment in amendments
        ),
        "corrigendum_verified_row_count": sum(
            int(amendment.get("corrigendum_verified_rows") or 0) for amendment in amendments
        ),
        "manual_override_count": sum(int(amendment.get("manual_override_count") or 0) for amendment in amendments),
        "manual_template_entry_count": sum(
            int(amendment.get("manual_template_entry_count") or 0) for amendment in amendments
        ),
    }
    return EvidenceSurfaceReport(
        jurisdiction="fi",
        report_kind="finland_corrigendum_review",
        schema="lawvm.finland_corrigendum_review.v1",
        truth_claim="finland_corrigendum_review_diagnostics",
        replay_claims=False,
        canonical_effect_claims=False,
        candidate_effect_claims=False,
        dry_run_claims=False,
        agreement_claims=True,
        summary=summary,
        filters={
            "statute_id": str(payload.get("statute_id") or ""),
            "mode": str(payload.get("mode") or ""),
        },
        filtered_summary=summary,
        rows=rows,
        rows_truncated=False,
        detail={
            "safe_default": "treat_corrigendum_review_as_source_diagnostics_not_replay_authorization",
            "forbidden_shortcuts": (
                "corrigendum_review_as_replay_authorization",
                "corrigendum_source_witness_as_patch_application",
                "source_pathology_as_corrigendum_proof",
                "manual_template_count_as_manual_claim",
                "oracle_score_as_source_truth",
            ),
            "included_surfaces": (
                "corrigendum_source_witness",
                "source_pathology",
                "corrigendum_review_amendment",
                "unblamed_section",
            ),
        },
    ).to_dict()


def finland_corrigendum_provenance_evidence_surface(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Wrap amendment-scoped Finland corrigendum provenance in the shared envelope."""

    provenance_rows = _mapping_sequence(payload.get("rows"))
    source_witnesses = _mapping_sequence(payload.get("source_witnesses"))
    row_witnesses = tuple(
        witness
        for row in provenance_rows
        for witness in (row.get("source_witness"),)
        if isinstance(witness, Mapping)
    )
    witnesses = source_witnesses or row_witnesses
    status_counts: dict[str, int] = {}
    for row in provenance_rows:
        status = str(row.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
    rows = tuple(
        (
            *({**dict(row), "surface": "corrigendum_source_witness"} for row in witnesses),
            *({**dict(row), "surface": "corrigendum_provenance_row"} for row in provenance_rows),
        )
    )
    summary = {
        "provenance_row_count": len(provenance_rows),
        "source_witness_count": len(witnesses),
        "source_witness_digest_coverage_counts": source_witness_digest_coverage_counts(witnesses),
        "status_counts": dict(sorted(status_counts.items())),
        "verified_count": int(payload.get("verified_count") or 0),
        "attachment_only_count": int(payload.get("attachment_only_count") or 0),
        "manual_exact_count": int(payload.get("manual_exact_count") or 0),
        "open_manual_candidate_count": int(payload.get("open_manual_candidate_count") or 0),
        "manual_entry_count": int(payload.get("manual_entry_count") or 0),
    }
    return EvidenceSurfaceReport(
        jurisdiction="fi",
        report_kind="finland_corrigendum_provenance",
        schema="lawvm.finland_corrigendum_provenance.v1",
        truth_claim="finland_corrigendum_provenance_diagnostics",
        replay_claims=False,
        canonical_effect_claims=False,
        candidate_effect_claims=False,
        dry_run_claims=False,
        agreement_claims=False,
        summary=summary,
        filters={
            "amendment_id": str(payload.get("amendment_id") or ""),
        },
        filtered_summary=summary,
        rows=rows,
        rows_truncated=False,
        detail={
            "safe_default": "treat_corrigendum_provenance_as_source_diagnostics_not_replay_authorization",
            "forbidden_shortcuts": (
                "corrigendum_provenance_as_replay_authorization",
                "corrigendum_source_witness_as_patch_application",
                "manual_override_status_as_manual_claim",
                "source_verified_status_as_mutation_boundary_proof",
                "attachment_only_status_as_source_text_repair",
            ),
            "included_surfaces": (
                "corrigendum_source_witness",
                "corrigendum_provenance_row",
            ),
        },
    ).to_dict()


def finland_corrigendum_overview_evidence_surface(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Wrap corpus-level Finland corrigendum overview in the shared envelope."""

    top_unresolved = _mapping_sequence(payload.get("top_unresolved_amendments"))
    top_open_manual = _mapping_sequence(payload.get("top_open_manual_amendments"))
    top_attachment_only = _mapping_sequence(payload.get("top_attachment_only_amendments"))
    source_pdf_count = int(payload.get("source_pdf_count") or 0)
    missing_date_published_count = int(payload.get("missing_date_published_count") or 0)
    source_date_status_counts = dict(payload.get("source_date_status_counts") or {})
    source_completeness_status = _corrigendum_sources_completeness_status(
        pdf_count=source_pdf_count,
        missing_date_count=missing_date_published_count,
        date_status_counts=source_date_status_counts,
        mode=str(payload.get("mode") or ""),
        manifest_kind="finland_corrigendum_overview_sources",
    )
    rows = tuple(
        (
            *({**dict(row), "surface": "corrigendum_overview_unresolved_amendment"} for row in top_unresolved),
            *({**dict(row), "surface": "corrigendum_overview_open_manual_amendment"} for row in top_open_manual),
            *(
                {**dict(row), "surface": "corrigendum_overview_attachment_only_amendment"}
                for row in top_attachment_only
            ),
            *(
                (
                    {
                        "surface": "source_completeness_status",
                        **source_completeness_status.to_dict(),
                    },
                )
                if source_completeness_status is not None
                else ()
            ),
        )
    )
    status_counts = dict(payload.get("status_counts") or {})
    summary = {
        "official_item_count": int(payload.get("official_item_count") or 0),
        "amendment_count": int(payload.get("amendment_count") or 0),
        "source_pdf_count": source_pdf_count,
        "missing_amendment_id_count": int(payload.get("missing_amendment_id_count") or 0),
        "missing_date_published_count": missing_date_published_count,
        "source_date_status_counts": source_date_status_counts,
        "type_counts": dict(payload.get("type_counts") or {}),
        "status_counts": status_counts,
        "top_unresolved_amendment_count": len(top_unresolved),
        "top_open_manual_amendment_count": len(top_open_manual),
        "top_attachment_only_amendment_count": len(top_attachment_only),
        "open_manual_candidate_count": int(status_counts.get("open_manual_candidate") or 0),
        "unresolved_unverified_count": int(status_counts.get("unresolved_unverified") or 0),
        "unresolved_unreviewed_count": int(status_counts.get("unresolved_unreviewed") or 0),
        "source_completeness_status_count": 1 if source_completeness_status is not None else 0,
        "source_completeness": (
            source_completeness_status.counts if source_completeness_status is not None else {}
        ),
    }
    return EvidenceSurfaceReport(
        jurisdiction="fi",
        report_kind="finland_corrigendum_overview",
        schema="lawvm.finland_corrigendum_overview.v1",
        truth_claim="finland_corrigendum_corpus_overview_diagnostics",
        replay_claims=False,
        canonical_effect_claims=False,
        candidate_effect_claims=False,
        dry_run_claims=False,
        agreement_claims=False,
        summary=summary,
        filters={
            "mode": str(payload.get("mode") or ""),
            "limit": int(payload.get("limit") or 0),
        },
        filtered_summary=summary,
        rows=rows,
        rows_truncated=False,
        detail={
            "safe_default": "treat_corrigendum_overview_as_corpus_diagnostics_not_replay_authorization",
            "forbidden_shortcuts": (
                "corrigendum_overview_as_replay_authorization",
                "status_count_as_manual_claim",
                "status_count_as_mutation_boundary_proof",
                "source_date_status_as_source_text_repair",
                "top_list_rank_as_execution_priority",
            ),
            "included_surfaces": (
                "corrigendum_overview_unresolved_amendment",
                "corrigendum_overview_open_manual_amendment",
                "corrigendum_overview_attachment_only_amendment",
                "source_completeness_status",
            ),
        },
    ).to_dict()


def finland_corrigendum_open_manual_evidence_surface(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Wrap the open manual-corrigendum candidate listing in the shared envelope."""

    candidates = _mapping_sequence(payload.get("rows"))
    frontier_items = tuple(
        _corrigendum_open_manual_frontier_work_item(index=index, candidate=candidate).to_dict()
        for index, candidate in enumerate(candidates)
    )
    rows = tuple(
        (
            *({**dict(row), "surface": "corrigendum_open_manual_candidate"} for row in candidates),
            *(
                {**dict(row), "surface": "corrigendum_open_manual_frontier_work_item"}
                for row in frontier_items
            ),
        )
    )
    summary = {
        "candidate_count": len(candidates),
        "frontier_work_item_count": len(frontier_items),
        "frontier_claim_template_status_counts": _frontier_claim_template_status_counts(frontier_items),
        "frontier_claim_template_kind_counts": _frontier_claim_template_kind_counts(frontier_items),
        "open_manual_row_count": sum(int(row.get("open_manual_rows") or 0) for row in candidates),
        "attachment_only_row_count": sum(int(row.get("attachment_only_rows") or 0) for row in candidates),
        "unverified_row_count": sum(int(row.get("db_no_match_rows") or 0) for row in candidates),
        "manual_entry_count": sum(int(row.get("manual_entry_count") or 0) for row in candidates),
        "db_row_count": sum(int(row.get("db_row_count") or 0) for row in candidates),
    }
    return EvidenceSurfaceReport(
        jurisdiction="fi",
        report_kind="finland_corrigendum_open_manual",
        schema="lawvm.finland_corrigendum_open_manual.v1",
        truth_claim="finland_corrigendum_open_manual_frontier_listing",
        replay_claims=False,
        canonical_effect_claims=False,
        candidate_effect_claims=False,
        dry_run_claims=False,
        agreement_claims=False,
        summary=summary,
        filters={
            "limit": int(payload.get("limit") or 0),
            "include_all": bool(payload.get("include_all")),
        },
        filtered_summary=summary,
        rows=rows,
        rows_truncated=False,
        detail={
            "safe_default": "treat_open_manual_listing_as_frontier_triage_not_manual_claim_or_replay_authority",
            "forbidden_shortcuts": (
                "open_manual_candidate_as_replay_authorization",
                "open_manual_candidate_as_manual_claim",
                "candidate_rank_as_execution_priority",
                "unverified_count_as_source_text_repair",
                "manual_entry_count_as_claim_validation",
            ),
            "included_surfaces": (
                "corrigendum_open_manual_candidate",
                "corrigendum_open_manual_frontier_work_item",
            ),
        },
    ).to_dict()


def _corrigendum_open_manual_frontier_work_item(
    *,
    index: int,
    candidate: Mapping[str, Any],
) -> FrontierWorkItem:
    amendment_id = str(candidate.get("amendment_id") or f"unknown-{index}")
    digest = hashlib.sha256(
        json.dumps(
            {
                "amendment_id": amendment_id,
                "db_row_count": int(candidate.get("db_row_count") or 0),
                "db_no_match_rows": int(candidate.get("db_no_match_rows") or 0),
                "open_manual_rows": int(candidate.get("open_manual_rows") or 0),
                "attachment_only_rows": int(candidate.get("attachment_only_rows") or 0),
                "manual_entry_count": int(candidate.get("manual_entry_count") or 0),
            },
            ensure_ascii=True,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:16]
    return _with_finland_claim_template(FrontierWorkItem(
        work_item_id=f"fi:{amendment_id}:corrigendum-open-manual:{digest}",
        jurisdiction="fi",
        source_artifact_id=amendment_id,
        source_unit_id=f"{amendment_id}#open-manual",
        owner_phase="manual_claim_frontier",
        frontier_family="fi_corrigendum_open_manual_candidate",
        frontier_status="manual_claim_needed",
        candidate_operation_family="corrigendum_source_repair",
        candidate_targets=(amendment_id,),
        guidance_refs=("lawvm_corrigendum_open_manual_review",),
        required_claim_kind="fi.v1.CORRIGENDUM_SOURCE_CORRECTION",
        required_validator_checks=(
            "manual_corrigendum_claim_review",
            "source_xml_non_verification_review",
            "mutation_boundary_check_before_replay",
        ),
        required_proofs=(
            "corrigendum_source_correction_claim",
            "source_corrigendum_witness_review",
            "targeted_source_xml_non_verification_review",
            "mutation_boundary_proof_before_replay_promotion",
        ),
        safe_default="do_not_apply_open_manual_candidate_without_manual_claim",
        forbidden_shortcuts=(
            "open_manual_candidate_as_replay_authorization",
            "open_manual_candidate_as_manual_claim",
            "candidate_rank_as_execution_priority",
            "unverified_count_as_source_text_repair",
            "manual_entry_count_as_claim_validation",
        ),
        executable=False,
        replay_authorized=False,
        authorization_status="blocked_manual_claim_required",
        detail={
            "amendment_id": amendment_id,
            "candidate_index": index,
            "db_row_count": int(candidate.get("db_row_count") or 0),
            "db_no_match_rows": int(candidate.get("db_no_match_rows") or 0),
            "open_manual_rows": int(candidate.get("open_manual_rows") or 0),
            "attachment_only_rows": int(candidate.get("attachment_only_rows") or 0),
            "manual_entry_count": int(candidate.get("manual_entry_count") or 0),
        },
    ))


def finland_corrigendum_unsupported_patch_frontier_item(
    *,
    patch: Mapping[str, Any] | object,
    source_witness: Mapping[str, Any] | None = None,
) -> FrontierWorkItem:
    """Project an unsupported corrigendum patch as non-executable frontier work."""

    row = _unsupported_corrigendum_patch_row(patch)
    amendment_id = str(row.get("amendment_id") or "unknown")
    sequence = _positive_int(row.get("sequence"))
    correction_kind = str(row.get("correction_kind") or "unsupported")
    reason = str(row.get("reason") or "FINLAND.CORRIGENDUM_UNSUPPORTED_PATCH")
    location = str(row.get("location") or "")
    target = str(row.get("target") or "")
    source_statute = str(row.get("source_statute") or f"corr/{amendment_id}")
    witness = dict(source_witness or {})
    source_artifact_id = str(witness.get("artifact_id") or witness.get("locator") or source_statute)
    source_unit_id = str(
        witness.get("source_unit_id")
        or f"{amendment_id}#unsupported-corrigendum-{sequence or 'unknown'}"
    )
    digest = hashlib.sha256(
        json.dumps(
            {
                "amendment_id": amendment_id,
                "sequence": sequence,
                "correction_kind": correction_kind,
                "reason": reason,
                "location": location,
                "target": target,
                "wrong_text": str(row.get("wrong_text") or ""),
                "correct_text": str(row.get("correct_text") or ""),
            },
            ensure_ascii=True,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:16]
    return _with_finland_claim_template(FrontierWorkItem(
        work_item_id=f"fi:{amendment_id}:corrigendum-unsupported-patch:{digest}",
        jurisdiction="fi",
        source_artifact_id=source_artifact_id,
        source_unit_id=source_unit_id,
        source_witness=witness,
        target_witness={
            "target": target,
            "location": location,
            "correction_kind": correction_kind,
        },
        owner_phase="corrigendum_payload_extraction",
        frontier_family=f"fi_corrigendum_{correction_kind.lower()}_unsupported",
        frontier_status="unsupported_corrigendum_patch_frontier",
        candidate_operation_family="corrigendum_patch_support",
        candidate_targets=(target or amendment_id,),
        guidance_refs=("lawvm_corrigendum_unsupported_patch_review",),
        required_claim_kind="fi.v1.CORRIGENDUM_UNSUPPORTED_PATCH_RESOLUTION",
        required_validator_checks=(
            "manual_corrigendum_claim_review",
            "unsupported_patch_parser_support_review",
            "source_xml_non_verification_review",
            "mutation_boundary_check_before_replay",
        ),
        required_proofs=(
            "corrigendum_source_correction_claim",
            "unsupported_patch_shape_resolution",
            "source_corrigendum_witness_review",
            "targeted_source_xml_non_verification_review",
            "mutation_boundary_proof_before_replay_promotion",
        ),
        safe_default="do_not_apply_unsupported_corrigendum_patch_without_manual_claim_or_parser_support",
        forbidden_shortcuts=(
            "unsupported_corrigendum_patch_as_replay_authorization",
            "unsupported_corrigendum_patch_as_manual_claim",
            "correct_text_as_insert_payload_without_boundary_proof",
            "source_witness_as_patch_application",
        ),
        executable=False,
        replay_authorized=False,
        authorization_status="blocked_unsupported_corrigendum_patch",
        detail={
            "amendment_id": amendment_id,
            "sequence": sequence,
            "correction_kind": correction_kind,
            "reason": reason,
            "location": location,
            "source_statute": source_statute,
            "wrong_text_preview": str(row.get("wrong_text") or "")[:160],
            "correct_text_preview": str(row.get("correct_text") or "")[:160],
        },
    ))


def finland_corrigendum_unsupported_patch_evidence_surface(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Wrap unsupported corrigendum patches in a passive evidence envelope."""

    patches = tuple(
        _unsupported_corrigendum_patch_row(patch)
        for patch in _object_sequence(payload.get("patches"))
    )
    source_witnesses = _mapping_sequence(payload.get("source_witnesses"))
    default_source_witness = source_witnesses[0] if source_witnesses else None
    frontier_items = tuple(
        finland_corrigendum_unsupported_patch_frontier_item(
            patch=patch,
            source_witness=default_source_witness,
        ).to_dict()
        for patch in patches
    )
    rows = tuple(
        (
            *({**dict(row), "surface": "corrigendum_source_witness"} for row in source_witnesses),
            *(
                {**dict(row), "surface": "corrigendum_unsupported_patch_frontier_work_item"}
                for row in frontier_items
            ),
            *({**dict(row), "surface": "corrigendum_unsupported_patch"} for row in patches),
        )
    )
    summary = {
        "unsupported_patch_count": len(patches),
        "frontier_work_item_count": len(frontier_items),
        "frontier_claim_template_status_counts": _frontier_claim_template_status_counts(frontier_items),
        "frontier_claim_template_kind_counts": _frontier_claim_template_kind_counts(frontier_items),
        "source_witness_count": len(source_witnesses),
        "source_witness_digest_coverage_counts": source_witness_digest_coverage_counts(source_witnesses),
        "reason_counts": _count_by_field(patches, "reason"),
        "correction_kind_counts": _count_by_field(patches, "correction_kind"),
    }
    return EvidenceSurfaceReport(
        jurisdiction="fi",
        report_kind="finland_corrigendum_unsupported_patch",
        schema="lawvm.finland_corrigendum_unsupported_patch.v1",
        truth_claim="finland_corrigendum_unsupported_patch_frontier_listing",
        replay_claims=False,
        canonical_effect_claims=False,
        candidate_effect_claims=False,
        dry_run_claims=False,
        agreement_claims=False,
        summary=summary,
        filters={
            "amendment_id": str(payload.get("amendment_id") or ""),
        },
        filtered_summary=summary,
        rows=rows,
        rows_truncated=False,
        detail={
            "safe_default": "treat_unsupported_corrigendum_patches_as_frontier_work_not_replay_authority",
            "forbidden_shortcuts": (
                "unsupported_corrigendum_patch_as_replay_authorization",
                "unsupported_corrigendum_patch_as_manual_claim",
                "corrigendum_source_witness_as_patch_application",
                "unsupported_patch_count_as_source_text_repair",
            ),
            "included_surfaces": (
                "corrigendum_source_witness",
                "corrigendum_unsupported_patch_frontier_work_item",
                "corrigendum_unsupported_patch",
            ),
        },
    ).to_dict()


def _unsupported_corrigendum_patch_row(patch: Mapping[str, Any] | object) -> Mapping[str, Any]:
    target = _field(patch, "target", None)
    return {
        "amendment_id": str(_field(patch, "amendment_id", "") or ""),
        "sequence": _positive_int(_field(patch, "sequence", 0)),
        "correction_kind": str(
            _field(patch, "correction_kind", "")
            or _field(patch, "correction_type", "")
            or "unsupported"
        ),
        "location": str(_field(patch, "location", "") or _field(patch, "location_desc", "")),
        "target": str(target) if target is not None else "",
        "correct_text": str(_field(patch, "correct_text", "") or ""),
        "wrong_text": str(_field(patch, "wrong_text", "") or ""),
        "reason": str(_field(patch, "reason", "") or "FINLAND.CORRIGENDUM_UNSUPPORTED_PATCH"),
        "source_statute": str(_field(patch, "source_statute", "") or ""),
    }


def _object_sequence(value: Any) -> tuple[Mapping[str, Any] | object, ...]:
    if value is None:
        return ()
    if isinstance(value, list | tuple):
        return tuple(value)
    return (value,)


def finland_corrigendum_manual_template_frontier_item(
    *,
    amendment_id: str,
    entry_index: int,
    entry: Mapping[str, Any],
    source_witness: Mapping[str, Any] | None = None,
) -> FrontierWorkItem:
    """Project a generated corrigendum manual-template entry as non-executable work."""

    witness = dict(source_witness or {})
    source_artifact_id = str(witness.get("artifact_id") or witness.get("locator") or amendment_id)
    source_unit_id = str(witness.get("source_unit_id") or f"{amendment_id}#{entry_index}")
    wrong_text = str(entry.get("wrong_text") or "")
    correct_text = str(entry.get("correct_text") or "")
    correction_type = str(entry.get("correction_type") or "")
    digest = hashlib.sha256(
        json.dumps(
            {
                "amendment_id": amendment_id,
                "entry_index": entry_index,
                "wrong_text": wrong_text,
                "correct_text": correct_text,
                "correction_type": correction_type,
            },
            ensure_ascii=True,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:16]
    return _with_finland_claim_template(FrontierWorkItem(
        work_item_id=f"fi:{amendment_id}:corrigendum-manual-template:{digest}",
        jurisdiction="fi",
        source_artifact_id=source_artifact_id,
        source_unit_id=source_unit_id,
        source_witness=witness,
        owner_phase="manual_claim_frontier",
        frontier_family="fi_corrigendum_manual_override",
        frontier_status="manual_claim_needed",
        candidate_operation_family="corrigendum_source_repair",
        candidate_targets=(amendment_id,),
        guidance_refs=("lawvm_corrigendum_manual_template",),
        required_claim_kind="fi.v1.CORRIGENDUM_SOURCE_CORRECTION",
        required_validator_checks=(
            "manual_corrigendum_claim_review",
            "source_xml_non_verification_review",
            "mutation_boundary_check_before_replay",
        ),
        required_proofs=(
            "corrigendum_source_correction_claim",
            "source_corrigendum_witness_review",
            "targeted_source_xml_non_verification_review",
            "mutation_boundary_proof_before_replay_promotion",
        ),
        safe_default="do_not_apply_corrigendum_template_without_manual_claim",
        forbidden_shortcuts=(
            "manual_template_entry_as_replay_authorization",
            "wrong_correct_text_pair_as_source_repair",
            "source_witness_as_patch_application",
            "manual_template_entry_as_manual_claim",
        ),
        executable=False,
        replay_authorized=False,
        authorization_status="blocked_manual_claim_required",
        detail={
            "amendment_id": amendment_id,
            "entry_index": entry_index,
            "correction_type": correction_type,
            "wrong_text_preview": wrong_text[:160],
            "correct_text_preview": correct_text[:160],
            "notes": str(entry.get("notes") or ""),
        },
    ))


def finland_corrigendum_manual_template_evidence_surface(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Wrap Finland corrigendum manual-template output in the shared envelope."""

    entries = _mapping_sequence(payload.get("entries"))
    frontier_items = _mapping_sequence(payload.get("frontier_work_items"))
    source_witnesses = _mapping_sequence(payload.get("source_witnesses"))
    rows = tuple(
        (
            *({**dict(row), "surface": "corrigendum_source_witness"} for row in source_witnesses),
            *({**dict(row), "surface": "corrigendum_manual_template_frontier_work_item"} for row in frontier_items),
            *({**dict(row), "surface": "corrigendum_manual_template_entry"} for row in entries),
        )
    )
    summary = {
        "entry_count": len(entries),
        "frontier_work_item_count": len(frontier_items),
        "frontier_claim_template_status_counts": _frontier_claim_template_status_counts(frontier_items),
        "frontier_claim_template_kind_counts": _frontier_claim_template_kind_counts(frontier_items),
        "source_witness_count": len(source_witnesses),
        "source_witness_digest_coverage_counts": source_witness_digest_coverage_counts(source_witnesses),
        "manual_entry_count": int(payload.get("manual_entry_count") or 0),
        "already_covered": bool(payload.get("already_covered")),
        "attachment_only_entry_count": int(payload.get("attachment_only_entry_count") or 0),
        "include_all": bool(payload.get("include_all")),
    }
    return EvidenceSurfaceReport(
        jurisdiction="fi",
        report_kind="finland_corrigendum_manual_template",
        schema="lawvm.finland_corrigendum_manual_template.v1",
        truth_claim="finland_corrigendum_manual_template_frontier_diagnostics",
        replay_claims=False,
        canonical_effect_claims=False,
        candidate_effect_claims=False,
        dry_run_claims=False,
        agreement_claims=False,
        summary=summary,
        filters={
            "amendment_id": str(payload.get("amendment_id") or ""),
            "include_all": bool(payload.get("include_all")),
        },
        filtered_summary=summary,
        rows=rows,
        rows_truncated=False,
        detail={
            "safe_default": "treat_manual_template_as_frontier_scaffold_not_manual_claim_or_replay_authority",
            "forbidden_shortcuts": (
                "manual_template_as_replay_authorization",
                "manual_template_entry_as_manual_claim",
                "frontier_work_item_as_canonical_operation",
                "source_witness_as_patch_application",
                "wrong_correct_text_pair_as_source_repair",
            ),
            "included_surfaces": (
                "corrigendum_source_witness",
                "corrigendum_manual_template_frontier_work_item",
                "corrigendum_manual_template_entry",
            ),
        },
    ).to_dict()


def finland_corrigendum_sources_evidence_surface(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Wrap Finland corrigendum PDF source manifest output in the shared envelope."""

    records = _mapping_sequence(payload.get("records"))
    source_witnesses = _mapping_sequence(payload.get("source_witnesses"))
    date_status_counts = dict(payload.get("date_status_counts") or {})
    pdf_count = int(payload.get("pdf_count") or 0)
    missing_date_count = sum(
        int(count or 0)
        for status, count in date_status_counts.items()
        if str(status) != "present"
    )
    source_completeness_status = _corrigendum_sources_completeness_status(
        pdf_count=pdf_count,
        missing_date_count=missing_date_count,
        date_status_counts=date_status_counts,
        mode=str(payload.get("mode") or ""),
        manifest_kind="finland_corrigendum_pdf_sources",
    )
    source_bundle_report = _corrigendum_source_bundle_report(
        records,
        mode=str(payload.get("mode") or ""),
        rows_truncated=bool(payload.get("records_truncated")),
    )
    source_bundle_summary = (
        dict(source_bundle_report.summary) if source_bundle_report is not None else {}
    )
    rows = tuple(
        (
            *({**dict(row), "surface": "corrigendum_source_witness"} for row in source_witnesses),
            *({**dict(row), "surface": "corrigendum_source_manifest_record"} for row in records),
            *(source_bundle_report.rows if source_bundle_report is not None else ()),
            *(
                (
                    {
                        "surface": "source_completeness_status",
                        **source_completeness_status.to_dict(),
                    },
                )
                if source_completeness_status is not None
                else ()
            ),
        )
    )
    summary = {
        "pdf_count": pdf_count,
        "amendment_count": int(payload.get("amendment_count") or 0),
        "total_item_count": int(payload.get("total_item_count") or 0),
        "shown_record_count": len(records),
        "source_witness_count": len(source_witnesses),
        "source_witness_digest_coverage_counts": source_witness_digest_coverage_counts(source_witnesses),
        "date_status_counts": date_status_counts,
        "missing_date_count": missing_date_count,
        "source_completeness_status_count": 1 if source_completeness_status is not None else 0,
        "source_completeness": (
            source_completeness_status.counts if source_completeness_status is not None else {}
        ),
        "source_bundle_assertion_count": int(source_bundle_summary.get("assertion_count") or 0),
        "source_bundle_admission_count": int(source_bundle_summary.get("admission_count") or 0),
        "source_bundle_admitted_count": int(source_bundle_summary.get("admitted_count") or 0),
        "source_bundle_status_counts": dict(source_bundle_summary.get("status_counts") or {}),
    }
    return EvidenceSurfaceReport(
        jurisdiction="fi",
        report_kind="finland_corrigendum_sources",
        schema="lawvm.finland_corrigendum_sources.v1",
        truth_claim="finland_corrigendum_pdf_source_manifest_diagnostics",
        replay_claims=False,
        canonical_effect_claims=False,
        candidate_effect_claims=False,
        dry_run_claims=False,
        agreement_claims=False,
        summary=summary,
        filters={
            "mode": str(payload.get("mode") or ""),
            "limit": int(payload.get("limit") or 0),
        },
        filtered_summary=summary,
        rows=rows,
        rows_truncated=bool(payload.get("records_truncated")),
        detail={
            "safe_default": "treat_corrigendum_source_manifest_as_source_footing_not_replay_authorization",
            "forbidden_shortcuts": (
                "source_manifest_as_replay_authorization",
                "source_witness_as_patch_application",
                "pdf_digest_as_manual_claim",
                "date_status_as_commencement_proof",
                "manifest_record_as_source_text_repair",
                "source_bundle_admission_as_replay_authorization",
            ),
            "included_surfaces": (
                "corrigendum_source_witness",
                "corrigendum_source_manifest_record",
                "source_acquisition_assertion",
                "source_bundle_admission",
                "source_completeness_status",
            ),
        },
    ).to_dict()


def _corrigendum_source_bundle_report(
    records: tuple[Mapping[str, Any], ...],
    *,
    mode: str,
    rows_truncated: bool,
) -> EvidenceSurfaceReport | None:
    if not records:
        return None
    assertions = tuple(_corrigendum_source_acquisition_assertion(record) for record in records)
    policy = SourceBundlePolicy(
        policy_id="fi.corrigendum_source_bundle.v1",
        jurisdiction="fi",
        admitted_source_lanes=("corrigendum_pdf",),
        safe_default="exclude_corrigendum_pdf_source_until_manifest_policy_is_satisfied",
        detail={
            "source_family": "finland_corrigendum_pdf_sources",
            "source_role": "finland_corrigendum_pdf",
        },
    )
    admissions = tuple(policy.evaluate(assertion) for assertion in assertions)
    return source_bundle_evidence_report(
        admissions,
        jurisdiction="fi",
        assertions=assertions,
        report_kind="finland_corrigendum_source_bundle",
        filters={"mode": mode},
        rows_truncated=rows_truncated,
    )


def _corrigendum_source_acquisition_assertion(
    record: Mapping[str, Any],
) -> SourceAcquisitionAssertion:
    witness = corrigendum_source_witness(record)
    digest = str(record.get("sha256") or "")
    artifact_id = (
        witness.artifact_id
        or witness.source_unit_id
        or str(record.get("pdf_name") or "")
        or (f"sha256:{digest}" if digest else "unknown_corrigendum_pdf_source")
    )
    assertion_key = json.dumps(
        {
            "amendment_id": str(record.get("amendment_id") or ""),
            "artifact_id": artifact_id,
            "digest": digest,
            "source_pdf": str(record.get("source_pdf") or ""),
        },
        ensure_ascii=True,
        sort_keys=True,
    )
    assertion_digest = hashlib.sha256(assertion_key.encode("utf-8")).hexdigest()[:16]
    return SourceAcquisitionAssertion(
        assertion_id=f"fi.corrigendum-source:{assertion_digest}",
        jurisdiction="fi",
        artifact_id=artifact_id,
        source_lane=witness.source_lane,
        assertion_kind="finland_corrigendum_pdf_manifest_record",
        status="source_manifest_recorded",
        witness=witness,
        detail={
            "statute_id": str(record.get("statute_id") or ""),
            "amendment_id": str(record.get("amendment_id") or ""),
            "pdf_name": str(record.get("pdf_name") or ""),
            "source_pdf": str(record.get("source_pdf") or ""),
            "date_status": str(record.get("date_status") or ""),
            "digest_available": bool(digest),
            "manifest_source": "finland_corrigendum_pdf_sources",
        },
    )


def _corrigendum_sources_completeness_status(
    *,
    pdf_count: int,
    missing_date_count: int,
    date_status_counts: Mapping[str, Any],
    mode: str,
    manifest_kind: str,
) -> SourceCompletenessStatus | None:
    if pdf_count <= 0:
        return None
    dates_available = max(pdf_count - missing_date_count, 0)
    return SourceCompletenessStatus(
        jurisdiction="fi",
        statute_id="corrigendum_source_manifest",
        chain_length=pdf_count,
        source_available=pdf_count,
        dates_available=dates_available,
        owner_phase="source_acquisition",
        detail={
            "manifest_kind": manifest_kind,
            "mode": mode,
            "date_status_counts": dict(date_status_counts),
        },
    )


def _pathology_row(pathology: SourcePathology | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(pathology, SourcePathology):
        return {
            "code": pathology.code,
            "message": pathology.message,
            "source_statute": pathology.source_statute,
            "target_unit_kind": str(pathology.target_unit_kind or ""),
            "target_label": pathology.target_label,
            "detail": dict(pathology.detail),
        }
    detail = pathology.get("detail", {})
    return {
        "code": str(pathology.get("code") or ""),
        "message": str(pathology.get("message") or ""),
        "source_statute": str(pathology.get("source_statute") or ""),
        "target_unit_kind": str(pathology.get("target_unit_kind") or ""),
        "target_label": str(pathology.get("target_label") or ""),
        "detail": dict(detail) if isinstance(detail, Mapping) else {},
    }


def _pathology_work_item_id(row: Mapping[str, Any], *, statute_id: str = "") -> str:
    payload = {
        "statute_id": statute_id,
        "code": str(row.get("code") or ""),
        "source_statute": str(row.get("source_statute") or ""),
        "target_unit_kind": str(row.get("target_unit_kind") or ""),
        "target_label": str(row.get("target_label") or ""),
        "detail": dict(row.get("detail") or {}),
    }
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=True, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    code = str(row.get("code") or "unknown").lower()
    source = str(row.get("source_statute") or statute_id or "unknown").replace("/", "_")
    return f"fi-source-pathology:{source}:{code}:{digest}"


def _failed_operation_row(failed_op: Mapping[str, Any]) -> dict[str, Any]:
    source_statute = str(failed_op.get("source") or failed_op.get("source_statute") or failed_op.get("amendment_id") or "")
    target_section = str(failed_op.get("target_section") or "")
    target_chapter = failed_op.get("target_chapter")
    target_part = failed_op.get("target_part")
    target_label = _failed_operation_target_label(
        target_unit_kind=str(failed_op.get("target_unit_kind") or ""),
        target_section=target_section,
        target_chapter=str(target_chapter or ""),
        target_part=str(target_part or ""),
    )
    return {
        "amendment_id": str(failed_op.get("amendment_id") or source_statute),
        "source_statute": source_statute,
        "description": str(failed_op.get("description") or ""),
        "reason": str(failed_op.get("reason") or ""),
        "reason_code": str(failed_op.get("reason_code") or ""),
        "target_unit_kind": str(failed_op.get("target_unit_kind") or ""),
        "target_section": target_section,
        "target_chapter": target_chapter,
        "target_part": target_part,
        "target_label": target_label,
        "target_kind": str(failed_op.get("target_kind") or ""),
    }


def _failed_operation_target_label(
    *,
    target_unit_kind: str,
    target_section: str,
    target_chapter: str,
    target_part: str,
) -> str:
    if target_unit_kind == "part" and target_part:
        return f"part:{target_part}"
    if target_unit_kind == "chapter" and target_chapter:
        return f"chapter:{target_chapter}"
    if target_unit_kind == "section" and target_section:
        prefix = f"chapter:{target_chapter}/" if target_chapter else ""
        return f"{prefix}section:{target_section}"
    return target_section or target_chapter or target_part


def _failed_operation_work_item_id(
    row: Mapping[str, Any],
    *,
    statute_id: str = "",
    index: int = 0,
) -> str:
    payload = {
        "statute_id": statute_id,
        "index": index,
        "amendment_id": str(row.get("amendment_id") or ""),
        "source_statute": str(row.get("source_statute") or ""),
        "reason_code": str(row.get("reason_code") or ""),
        "target_unit_kind": str(row.get("target_unit_kind") or ""),
        "target_label": str(row.get("target_label") or ""),
        "reason": str(row.get("reason") or ""),
    }
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=True, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    source = str(row.get("source_statute") or statute_id or "unknown").replace("/", "_")
    return f"fi-failed-operation:{source}:{_kind_slug(str(row.get('reason_code') or 'unknown'))}:{digest}"


def _sparse_slot_candidate_certificate(
    row: Mapping[str, Any],
    *,
    statute_id: str = "",
) -> CandidateSetCertificate | None:
    kind = str(row.get("kind") or "")
    detail_raw = row.get("detail")
    if not isinstance(detail_raw, Mapping):
        return None
    detail = dict(detail_raw)
    if kind == "ELAB.SPARSE_SLOT_BINDING":
        return _sparse_slot_binding_candidate_certificate(detail, statute_id=statute_id)
    if kind == "ELAB.SPARSE_PAYLOAD_LEFTOVER":
        return _sparse_leftover_candidate_certificate(detail, statute_id=statute_id)
    if kind == "ELAB.AMBIGUOUS_BINDING":
        return _sparse_ambiguous_binding_candidate_certificate(detail, statute_id=statute_id)
    if kind == "ELAB.UNASSIGNED_SPARSE_SLOTS":
        return _sparse_leftover_candidate_certificate(detail, statute_id=statute_id)
    return None


def _sparse_slot_binding_candidate_certificate(
    detail: Mapping[str, Any],
    *,
    statute_id: str,
) -> CandidateSetCertificate:
    source_statute = str(detail.get("source_statute") or statute_id or "unknown")
    target_unit_kind = str(detail.get("target_unit_kind") or "")
    target_norm = str(detail.get("target_norm") or "")
    target_chapter = str(detail.get("target_chapter") or "")
    slot_index = _positive_int(detail.get("payload_slot_index"))
    slot_label = str(detail.get("payload_slot_label") or "")
    candidate_id = _sparse_payload_slot_candidate_id(slot_index=slot_index, slot_label=slot_label)
    scope_id = _sparse_scope_id(
        source_statute=source_statute,
        target_unit_kind=target_unit_kind,
        target_norm=target_norm,
        target_chapter=target_chapter,
        suffix=f"binding:{candidate_id}",
    )
    return CandidateSetCertificate(
        scope_id=scope_id,
        candidate_set_kind="fi_sparse_payload_slot_assignment",
        phase="typed_elaboration",
        rule_id="fi_sparse_slot_binding_candidate_set",
        reason="selected_sparse_slot_binding_recorded_without_full_candidate_enumeration",
        completeness_status="partial",
        candidate_count=1,
        candidate_ids=(candidate_id,),
        selected_candidate_ids=(candidate_id,),
        blocker_counts={"candidate_set_not_enumerated": 1},
        blocker_families=("candidate_set_completeness",),
        next_promotion_allowed=False,
        next_promotion_requires=_SPARSE_SLOT_PROMOTION_PROOFS,
        detail={
            "jurisdiction": "fi",
            "source_statute": source_statute,
            "target_unit_kind_witness": target_unit_kind,
            "target_norm_witness": target_norm,
            "target_chapter_witness": target_chapter,
            "op_description": str(detail.get("op_description") or ""),
            "op_type": str(detail.get("op_type") or ""),
            "target_paragraph": str(detail.get("target_paragraph") or ""),
            "target_item": str(detail.get("target_item") or ""),
            "target_special": str(detail.get("target_special") or ""),
            "payload_slot_index": slot_index,
            "payload_slot_label": slot_label,
            "projection_only": True,
        },
    )


def _sparse_ambiguous_binding_candidate_certificate(
    detail: Mapping[str, Any],
    *,
    statute_id: str,
) -> CandidateSetCertificate:
    source_statute = str(detail.get("amendment_id") or detail.get("source_statute") or statute_id or "unknown")
    slot_id = _positive_int(detail.get("slot_id"))
    candidate_count = max(_positive_int(detail.get("candidate_count")), 1)
    candidate_id = f"payload-slot:{slot_id}" if slot_id else "payload-slot:unknown"
    return CandidateSetCertificate(
        scope_id=_sparse_scope_id(
            source_statute=source_statute,
            target_unit_kind="",
            target_norm="",
            target_chapter="",
            suffix=f"ambiguous:{candidate_id}",
        ),
        candidate_set_kind="fi_sparse_payload_slot_assignment",
        phase="typed_elaboration",
        rule_id="fi_sparse_slot_ambiguous_binding_candidate_set",
        reason="ambiguous_sparse_slot_binding",
        completeness_status="partial",
        candidate_count=candidate_count,
        candidate_ids=(candidate_id,),
        blocker_counts={"ambiguous_binding": 1},
        blocker_families=("sparse_slot_ambiguity",),
        next_promotion_allowed=False,
        next_promotion_requires=_SPARSE_SLOT_PROMOTION_PROOFS,
        detail={
            "jurisdiction": "fi",
            "source_statute": source_statute,
            "slot_id": slot_id,
            "admissibility": str(detail.get("admissibility") or ""),
            "projection_only": True,
        },
    )


def _sparse_leftover_candidate_certificate(
    detail: Mapping[str, Any],
    *,
    statute_id: str,
) -> CandidateSetCertificate | None:
    slots = _string_sequence(detail.get("unassigned_slots"))
    if not slots:
        return None
    source_statute = str(detail.get("source_statute") or statute_id or "unknown")
    target_unit_kind = str(detail.get("target_unit_kind") or "")
    target_norm = str(detail.get("target_norm") or "")
    target_chapter = str(detail.get("target_chapter") or "")
    candidate_ids = tuple(_sparse_payload_slot_candidate_id_from_text(slot) for slot in slots)
    return CandidateSetCertificate(
        scope_id=_sparse_scope_id(
            source_statute=source_statute,
            target_unit_kind=target_unit_kind,
            target_norm=target_norm,
            target_chapter=target_chapter,
            suffix="unassigned:" + hashlib.sha256("|".join(candidate_ids).encode("utf-8")).hexdigest()[:12],
        ),
        candidate_set_kind="fi_sparse_payload_slot_assignment",
        phase="typed_elaboration",
        rule_id="fi_sparse_unassigned_payload_slot_candidate_set",
        reason="unassigned_sparse_payload_slots",
        completeness_status="rejected",
        candidate_count=len(candidate_ids),
        candidate_ids=candidate_ids,
        blocker_counts={"unassigned_payload_slot": len(candidate_ids)},
        blocker_families=("sparse_payload_leftover",),
        next_promotion_allowed=False,
        next_promotion_requires=_SPARSE_SLOT_PROMOTION_PROOFS,
        detail={
            "jurisdiction": "fi",
            "source_statute": source_statute,
            "target_unit_kind_witness": target_unit_kind,
            "target_norm_witness": target_norm,
            "target_chapter_witness": target_chapter,
            "unassigned_slots": slots,
            "projection_only": True,
        },
    )


def _positive_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int) and value > 0:
        return value
    text = str(value or "").strip()
    return int(text) if text.isdigit() else 0


def _string_sequence(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value else ()
    if isinstance(value, list | tuple):
        return tuple(str(item) for item in value if str(item))
    return ()


def _mapping_or_empty(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    return {}


def _mapping_str_str(value: Any) -> Mapping[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): str(item) for key, item in value.items() if str(key) and str(item)}


def _mapping_str_int(value: Any) -> Mapping[str, int]:
    if not isinstance(value, Mapping):
        return {}
    out: dict[str, int] = {}
    for key, item in value.items():
        if isinstance(item, bool):
            continue
        if isinstance(item, int):
            out[str(key)] = item
            continue
        text = str(item or "").strip()
        if text.isdigit():
            out[str(key)] = int(text)
    return out


def _mapping_sequence(value: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, list | tuple):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _count_by_field(rows: tuple[Mapping[str, Any], ...], field_name: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        key = str(row.get(field_name) or "")
        if key:
            counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _count_values(values: tuple[str, ...]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = value or "__none__"
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _frontier_claim_template_status_counts(
    frontier_items: tuple[Mapping[str, Any], ...],
) -> dict[str, int]:
    return _count_values(
        tuple(
            str(item.get("suggested_claim_template_status") or "__none__")
            for item in frontier_items
        )
    )


def _frontier_claim_template_kind_counts(
    frontier_items: tuple[Mapping[str, Any], ...],
) -> dict[str, int]:
    kinds: list[str] = []
    for item in frontier_items:
        template = item.get("suggested_claim_template") or {}
        if isinstance(template, Mapping):
            kinds.append(str(template.get("claim_kind") or "__none__"))
        else:
            kinds.append("__none__")
    return _count_values(tuple(kinds))


def _has_candidate_set(
    rows: tuple[Mapping[str, Any], ...],
    candidate_set_kind: str,
) -> bool:
    return any(str(row.get("candidate_set_kind") or "") == candidate_set_kind for row in rows)


def _candidate_set_complete(
    rows: tuple[Mapping[str, Any], ...],
    candidate_set_kind: str,
) -> bool:
    return any(
        str(row.get("candidate_set_kind") or "") == candidate_set_kind
        and str(row.get("completeness_status") or "") == CANDIDATE_SET_COMPLETE
        for row in rows
    )


def _closure_certificate_requirements(
    rows: tuple[Mapping[str, Any], ...],
    *,
    candidate_set_kind: str,
    missing_certificate: str,
    incomplete_certificate: str,
) -> tuple[str, ...]:
    if not _has_candidate_set(rows, candidate_set_kind):
        return (missing_certificate,)
    if not _candidate_set_complete(rows, candidate_set_kind):
        return (incomplete_certificate,)
    return ()


def _strict_report_candidate_set_authorization_key(
    row: Mapping[str, Any],
) -> tuple[str, str, str] | None:
    candidate_set_kind = str(row.get("candidate_set_kind") or "")
    scope_id = str(row.get("scope_id") or "")
    completeness_status = str(row.get("completeness_status") or "")
    if not candidate_set_kind or not scope_id or not completeness_status:
        return None
    return (candidate_set_kind, scope_id, completeness_status)


def _strict_report_candidate_set_authorization_row_id(
    *,
    candidate_set_kind: str,
    scope_id: str,
    completeness_status: str,
) -> str:
    digest = _strict_report_digest(
        "candidate-set-authorization",
        {
            "candidate_set_kind": candidate_set_kind,
            "scope_id": scope_id,
            "completeness_status": completeness_status,
        },
    ).split(":", 1)[1][:16]
    return f"fi:strict-report-candidate-set-authorization:{digest}"


def _strict_report_candidate_set_frontier_key(
    row: Mapping[str, Any],
) -> tuple[str, str, str] | None:
    direct = _strict_report_candidate_set_authorization_key(row)
    if direct is not None:
        return direct
    target_witness = row.get("target_witness")
    if not isinstance(target_witness, Mapping):
        return None
    return _strict_report_candidate_set_authorization_key(target_witness)


def _strict_report_candidate_set_frontier_work_item_id(
    *,
    statute_id: str,
    candidate_set_kind: str,
    scope_id: str,
    completeness_status: str,
    index: int,
) -> str:
    digest = _strict_report_digest(
        "candidate-set-frontier",
        {
            "statute_id": statute_id,
            "candidate_set_kind": candidate_set_kind,
            "scope_id": scope_id,
            "completeness_status": completeness_status,
            "index": index,
        },
    ).split(":", 1)[1][:16]
    return f"fi:{statute_id or 'unknown'}:candidate-set-frontier:{digest}"


def _candidate_set_frontier_required_claim_kind(candidate_set_kind: str) -> str:
    if candidate_set_kind in (
        "fi_strict_report_source_lineage_units",
        "fi_strict_report_source_unit_enumeration",
    ):
        return "source_unit_enumeration_certificate"
    if candidate_set_kind in (
        "fi_strict_report_visible_operation_rows",
        "fi_strict_report_operation_cue_coverage",
    ):
        return "operation_cue_exhaustiveness_certificate"
    return "candidate_set_completion_certificate"


def _strict_report_canonical_potential_operation(
    *,
    statute_id: str,
    visible_id: str,
    visible_index: int,
    synthesized: bool,
) -> dict[str, Any]:
    detail: dict[str, Any] = {
        "statute_id": statute_id,
        "visible_index": visible_index,
        "projection_only": True,
    }
    if synthesized:
        detail["op_id_synthesized"] = True
    potential_operation_id = (
        visible_id if visible_id.startswith("canonical-op:") else f"canonical-op:{visible_id}"
    )
    return PotentialOperation(
        potential_operation_id=potential_operation_id,
        jurisdiction="fi",
        source_artifact_id=f"fi:{statute_id}:strict-report-canonical-ops",
        source_unit_id=visible_id,
        owner_phase="canonical_operation_lowering",
        classification=POTENTIAL_OPERATION_COMPILED,
        operation_family="fi_canonical_operation",
        refs=(visible_id,),
        required_proofs=(
            "source_text_operation_cue_detector",
            "source_unit_enumeration_certificate",
            "operation_cue_exhaustiveness_certificate",
        ),
        safe_default="do_not_treat_compiled_visible_ops_as_source_cue_exhaustiveness",
        detail=detail,
    ).to_dict()


def _potential_operation_candidate_ids(
    potential_operations: tuple[Mapping[str, Any], ...],
) -> tuple[str, ...]:
    return tuple(
        str(row.get("potential_operation_id") or "")
        for row in potential_operations
        if str(row.get("potential_operation_id") or "")
    )


def _visible_operation_candidate_ids(
    *,
    canonical_op_ids: tuple[str, ...],
    canonical_count: int,
    failed_ops: tuple[Mapping[str, Any], ...],
) -> tuple[str, ...]:
    ids = [
        op_id or f"canonical-op:{index + 1}"
        for index, op_id in enumerate(canonical_op_ids[:canonical_count])
    ]
    if len(ids) < canonical_count:
        ids.extend(f"canonical-op:{index + 1}" for index in range(len(ids), canonical_count))
    ids.extend(_failed_operation_candidate_id(index=index, row=row) for index, row in enumerate(failed_ops))
    return tuple(ids)


def _failed_operation_candidate_id(*, index: int, row: Mapping[str, Any]) -> str:
    op_id = str(row.get("op_id") or "")
    if op_id:
        return f"failed-op:{op_id}"
    digest_payload = {
        "index": index,
        "amendment_id": row.get("amendment_id") or row.get("source") or "",
        "description": row.get("description") or "",
        "reason": row.get("reason") or "",
        "reason_code": row.get("reason_code") or "",
        "target_unit_kind": row.get("target_unit_kind") or row.get("target_kind") or "",
        "target_section": row.get("target_section") or "",
        "target_chapter": row.get("target_chapter") or "",
        "target_part": row.get("target_part") or "",
    }
    digest = hashlib.sha256(
        json.dumps(digest_payload, ensure_ascii=True, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    return f"failed-op:{digest}"


def _source_lineage_candidate_ids(
    source_lineage_witnesses: tuple[Mapping[str, Any], ...],
) -> tuple[str, ...]:
    ids: list[str] = []
    for index, witness in enumerate(source_lineage_witnesses):
        artifact_id = str(witness.get("artifact_id") or "")
        source_unit_id = str(witness.get("source_unit_id") or "")
        if artifact_id or source_unit_id:
            ids.append(f"source-lineage:{artifact_id or 'unknown'}:{source_unit_id or index + 1}")
            continue
        digest = hashlib.sha256(
            json.dumps(witness, ensure_ascii=True, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()[:16]
        ids.append(f"source-lineage:{digest}")
    return tuple(ids)


def _source_unit_coverage_candidate_ids(
    source_unit_coverages: tuple[Mapping[str, Any], ...],
) -> tuple[str, ...]:
    return tuple(
        str(row.get("coverage_id") or "")
        for row in source_unit_coverages
        if str(row.get("coverage_id") or "")
    )


def _strict_report_source_unit_coverage_id(
    *,
    statute_id: str,
    artifact_id: str,
    source_unit_id: str,
    index: int,
) -> str:
    digest = _strict_report_digest(
        "source-unit-coverage",
        {
            "statute_id": statute_id,
            "artifact_id": artifact_id,
            "source_unit_id": source_unit_id,
            "index": index,
        },
    ).split(":", 1)[1][:16]
    return f"fi:{statute_id or 'unknown'}:source-unit-coverage:{digest}"


def _ops_count(payload: Mapping[str, Any], name: str) -> int:
    ops = payload.get("ops")
    if not isinstance(ops, Mapping):
        return 0
    value = ops.get(name)
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(value, 0)
    text = str(value or "").strip()
    return int(text) if text.isdigit() else 0


def _strict_report_digest(prefix: str, payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, default=str).encode("utf-8")
    return f"sha256:{hashlib.sha256(prefix.encode('utf-8') + b':' + encoded).hexdigest()}"


def _strict_report_id(prefix: str, statute_id: str, payload: Any) -> str:
    digest = _strict_report_digest(prefix, payload).split(":", 1)[1][:16]
    return f"fi:{statute_id or 'unknown'}:{prefix}:{digest}"


def _strict_report_closure_graph_payload(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    keys = (
        "statute_id",
        "profile",
        "ops",
        "canonical_op_ids",
        "source_completeness",
        "source_pathologies",
        "source_pathology_execution_authorizations",
        "source_pathology_frontier_work_items",
        "failed_operation_execution_authorizations",
        "failed_operation_frontier_work_items",
        "potential_operations",
        "source_unit_coverages",
        "sparse_slot_candidate_set_certificates",
        "source_lineage_source_witnesses",
        "agreement_residuals",
        "mutation_boundary_proofs",
        "strict_report_candidate_set_certificates",
        "strict_report_candidate_set_execution_authorizations",
        "strict_report_candidate_set_frontier_work_items",
        "strict_fail_reasons",
        "projection_rows",
        "failed_ops",
    )
    return {key: payload.get(key) for key in keys if key in payload}


def _preview_digest_witness(text: str) -> DigestWitness | None:
    if not text:
        return None
    return DigestWitness(
        digest_algorithm="sha256",
        digest=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


def _bounded_bytes_preview(data: bytes, *, limit: int = 512) -> str:
    return data[:limit].decode("utf-8", errors="replace")


def _mutation_invariant_report(
    report: MutationInvariantReport | Mapping[str, Any],
) -> MutationInvariantReport:
    if isinstance(report, MutationInvariantReport):
        return report
    return MutationInvariantReport(
        op_id=str(report.get("op_id") or ""),
        helper=str(report.get("helper") or ""),
        outcome=str(report.get("outcome") or ""),
        touched_paths=_path_tuple(report.get("touched_paths")),
        changed_paths=_path_tuple(report.get("changed_paths")),
        allowed_roots=_path_tuple(report.get("allowed_roots")),
        allowed_effect_region_paths=_path_tuple(report.get("allowed_effect_region_paths")),
        declared_allowance_paths=_path_tuple(report.get("declared_allowance_paths")),
        declared_recovery_paths=_path_tuple(report.get("declared_recovery_paths")),
        declared_recovery_rule_ids=_string_sequence(report.get("declared_recovery_rule_ids")),
        declared_migration_paths=_path_tuple(report.get("declared_migration_paths")),
        declared_migration_rule_ids=_string_sequence(report.get("declared_migration_rule_ids")),
        permitted_paths=_path_tuple(report.get("permitted_paths")),
        covered_changed_paths=_path_tuple(report.get("covered_changed_paths")),
        unexplained_changed_paths=_path_tuple(report.get("unexplained_changed_paths")),
        allowed_non_target_paths=_path_tuple(report.get("allowed_non_target_paths")),
        out_of_scope_paths=_path_tuple(report.get("out_of_scope_paths")),
        matched_allowance_rule_ids=_string_sequence(report.get("matched_allowance_rule_ids")),
        path_set_invariant_holds=_bool_field(
            report,
            "path_set_invariant_holds",
            default=True,
        ),
        results=tuple(_mutation_accounting_result(result) for result in _mapping_sequence(report.get("results"))),
    )


def _mutation_accounting_result(row: Mapping[str, Any]) -> MutationAccountingResult:
    return MutationAccountingResult(
        code=str(row.get("code") or ""),
        op_id=str(row.get("op_id") or ""),
        helper=str(row.get("helper") or ""),
        touched_count=int(row.get("touched_count") or 0),
        allowed_roots=_path_tuple(row.get("allowed_roots")),
        out_of_scope_paths=_path_tuple(row.get("out_of_scope_paths")),
        allowed_paths=_path_tuple(row.get("allowed_paths")),
        matched_allowance_rule_ids=_string_sequence(row.get("matched_allowance_rule_ids")),
    )


def _mutation_boundary_proof_id(
    *,
    statute_id: str,
    index: int,
    op_id: str,
) -> str:
    return f"fi:{statute_id}:mutation-boundary:{index}:{op_id or 'unknown-op'}"


def _path_tuple(value: Any) -> tuple[tuple[tuple[str, str], ...], ...]:
    if value in (None, ""):
        return ()
    if not isinstance(value, (list, tuple)):
        raise ValueError("mutation invariant path field must be a sequence")
    paths: list[tuple[tuple[str, str], ...]] = []
    for path_index, path in enumerate(value):
        if not isinstance(path, (list, tuple)):
            raise ValueError(f"mutation invariant path {path_index} must be a sequence")
        steps: list[tuple[str, str]] = []
        for step_index, step in enumerate(path):
            if not isinstance(step, (list, tuple)) or len(step) != 2:
                raise ValueError(f"mutation invariant path {path_index} step {step_index} must have kind and label")
            steps.append((str(step[0]), str(step[1])))
        paths.append(tuple(steps))
    return tuple(paths)


def _he_branch_impact_projection_row(
    *,
    parsed: Any,
    proposed_ops: tuple[Any, ...],
) -> dict[str, Any] | None:
    branch_id = str(_field(parsed, "branch_id", ""))
    if not branch_id:
        return None
    he_id = str(_field(parsed, "he_id", ""))
    projection = _he_branch_impact_projection(
        branch=LegalBranch(
            branch_id=branch_id,
            authority_layer=PROPOSAL_AUTHORITY,
            legal_status=UNKNOWN_STATUS,
            source_artifact_id=he_id,
            title=he_id,
        ),
        proposed_ops=proposed_ops,
    )
    return {
        "surface": "he_branch_impact_projection",
        "status": "branch_projection_not_enacted_authority",
        "owner_phase": "branch_projection",
        "branch_id": branch_id,
        "source_he_id": he_id,
        "branch_impact_row_count": len(projection.rows),
        "projection": projection.to_dict(),
        "executable": False,
        "replay_authorized": False,
        "forbidden_shortcuts": list(_HE_BRANCH_FORBIDDEN_SHORTCUTS),
    }


def _he_branch_impact_projection(
    *,
    branch: LegalBranch,
    proposed_ops: tuple[Any, ...],
) -> BranchImpactProjection:
    edges = tuple(
        edge
        for op in proposed_ops
        if (edge := _he_branch_graph_edge(branch=branch, op=op)) is not None
    )
    return branch_impact_projection_from_edges(
        branch,
        edges,
        status="diagnostic_only",
        message="Finland government-proposal branch impact projection is not enacted-law authority.",
    )


def _he_branch_graph_edge(*, branch: LegalBranch, op: Any) -> BranchGraphEdge | None:
    target_statute_id = str(_field(op, "target_statute_id", ""))
    if not target_statute_id:
        return None
    op_index = _nonnegative_int(_field(op, "op_index", 0))
    return BranchGraphEdge(
        branch_id=branch.branch_id,
        edge_kind=_he_branch_edge_kind(str(_field(op, "operation_kind", ""))),
        scenario_id=branch.scenario_id,
        source_artifact_id=str(_field(op, "source_he_id", branch.source_artifact_id)),
        source_statute_id=str(_field(op, "source_he_id", branch.source_artifact_id)),
        source_unit_id=f"proposed-op:{op_index}",
        target_statute_id=target_statute_id,
        target_address=str(_field(op, "target_provision_ref", "")),
        operation_id=f"{branch.branch_id}:proposed-op:{op_index}",
        authority_layer=branch.authority_layer,
        legal_status=branch.legal_status,
    )


def _he_branch_edge_kind(operation_kind: str) -> BranchEdgeKind:
    normalized = operation_kind.strip().lower()
    if normalized == "insert":
        return WOULD_INSERT_EDGE
    if normalized in {"replace", "amend", "change"}:
        return WOULD_REPLACE_EDGE
    if normalized in {"repeal", "omit", "delete"}:
        return WOULD_REPEAL_EDGE
    return WOULD_AMEND_EDGE


def _he_branch_proposed_op_row(op: Any) -> dict[str, Any]:
    authorization = ExecutionAuthorization(
        executable=False,
        replay_authorized=False,
        authorization_status="he_branch_proposal_not_replay_authority",
        authorization_rule_id="fi_he_branch_proposal_surface_only",
        owner_phase="surface_parse",
        strict_disposition="record",
        quirks_disposition="record",
        validator_status="not_validated_for_replay_promotion",
        required_proofs=_HE_BRANCH_REQUIRED_PROOFS,
        safe_default="record_as_future_law_diagnostic_without_replay_promotion",
        forbidden_shortcuts=_HE_BRANCH_FORBIDDEN_SHORTCUTS,
        detail={
            "branch_id": str(_field(op, "branch_id", "")),
            "source_he_id": str(_field(op, "source_he_id", "")),
            "op_index": _nonnegative_int(_field(op, "op_index", 0)),
        },
    )
    return {
        "surface": "he_branch_proposed_op",
        "status": "proposed_branch_op_not_enacted_authority",
        "owner_phase": "surface_parse",
        "op_index": _nonnegative_int(_field(op, "op_index", 0)),
        "operation_kind": str(_field(op, "operation_kind", "")),
        "target_provision_ref": str(_field(op, "target_provision_ref", "")),
        "target_statute_id": str(_field(op, "target_statute_id", "")),
        "target_resolution": _enum_text(_field(op, "target_resolution", "")),
        "is_proposal_relative": bool(_field(op, "is_proposal_relative", False)),
        "parse_confidence": float(_field(op, "parse_confidence", 0.0) or 0.0),
        "payload_summary": str(_field(op, "payload_summary", "")),
        "source_he_id": str(_field(op, "source_he_id", "")),
        "branch_id": str(_field(op, "branch_id", "")),
        "source_span_text": str(_field(op, "source_span_text", "")),
        "source_span_preamble": str(_field(op, "source_span_preamble", "")),
        "execution_authorization": authorization.to_dict(),
        "forbidden_shortcuts": list(_HE_BRANCH_FORBIDDEN_SHORTCUTS),
    }


def _he_branch_finding_row(finding: Any) -> dict[str, Any]:
    rule_id = str(_field(finding, "rule_id", ""))
    target_ref = str(_field(finding, "target_provision_ref", ""))
    is_target_finding = bool(target_ref) or rule_id.startswith("HE_BRANCH.TARGET_")
    owner_phase = "target_resolution" if is_target_finding else str(_field(finding, "phase", "surface_parse"))
    surface = "he_branch_target_resolution_finding" if is_target_finding else "he_branch_parse_finding"
    authorization = ExecutionAuthorization(
        executable=False,
        replay_authorized=False,
        authorization_status="he_branch_finding_not_replay_authority",
        authorization_rule_id="fi_he_branch_finding_surface_only",
        owner_phase=owner_phase,
        strict_disposition=str(_field(finding, "strict_disposition", "record") or "record"),
        quirks_disposition="record",
        validator_status="not_validated_for_replay_promotion",
        required_proofs=_HE_BRANCH_REQUIRED_PROOFS,
        safe_default="record_finding_and_preserve_uncertainty",
        forbidden_shortcuts=_HE_BRANCH_FORBIDDEN_SHORTCUTS,
        detail={
            "rule_id": rule_id,
            "op_index": _nonnegative_int(_field(finding, "op_index", 0)),
            "target_provision_ref": target_ref,
        },
    )
    return {
        "surface": surface,
        "status": "recorded",
        "owner_phase": owner_phase,
        "rule_id": rule_id,
        "op_index": _nonnegative_int(_field(finding, "op_index", 0)),
        "reason": str(_field(finding, "reason", "")),
        "detail": str(_field(finding, "detail", "")),
        "family": str(_field(finding, "family", "target_resolution" if is_target_finding else "source_pathology")),
        "strict_disposition": str(_field(finding, "strict_disposition", "record") or "record"),
        "target_provision_ref": target_ref,
        "target_statute_id": str(_field(finding, "target_statute_id", "")),
        "is_proposal_relative": bool(_field(finding, "is_proposal_relative", False)),
        "clause_text": str(_field(finding, "clause_text", "")),
        "execution_authorization": authorization.to_dict(),
        "forbidden_shortcuts": list(_HE_BRANCH_FORBIDDEN_SHORTCUTS),
    }


def _enum_text(value: Any) -> str:
    enum_value = getattr(value, "value", value)
    return str(enum_value or "")


def _bool_field(row: Mapping[str, Any], key: str, *, default: bool) -> bool:
    value = row.get(key, default)
    if isinstance(value, bool):
        return value
    raise ValueError(f"mutation invariant {key} must be a boolean")


def _nonnegative_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(value, 0)
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return 0


def _field(record: Any, name: str, default: Any = None) -> Any:
    if isinstance(record, Mapping):
        return record.get(name, default)
    return getattr(record, name, default)


def _stable_residual_id(*parts: str) -> str:
    normalized = tuple(str(part or "") for part in parts)
    digest = hashlib.sha256(json.dumps(normalized, ensure_ascii=True).encode("utf-8")).hexdigest()[:16]
    prefix = normalized[0] if normalized else "fi-residual"
    return f"{prefix}:{digest}"


def _sparse_payload_slot_candidate_id(*, slot_index: int, slot_label: str) -> str:
    label = slot_label.strip() or "unlabeled"
    index = str(slot_index) if slot_index else "unknown"
    return f"payload-slot:{index}:{label}"


def _sparse_payload_slot_candidate_id_from_text(slot: str) -> str:
    text = str(slot or "").strip()
    if ":" not in text:
        return f"payload-slot:unknown:{text or 'unlabeled'}"
    index, label = text.split(":", 1)
    return _sparse_payload_slot_candidate_id(
        slot_index=_positive_int(index),
        slot_label=label.strip("()") or "unlabeled",
    )


def _sparse_scope_id(
    *,
    source_statute: str,
    target_unit_kind: str,
    target_norm: str,
    target_chapter: str,
    suffix: str,
) -> str:
    scope_parts = (
        source_statute or "unknown",
        target_unit_kind or "unknown-target-kind",
        target_chapter or "no-chapter",
        target_norm or "unknown-target",
        suffix,
    )
    safe = ":".join(part.replace("/", "_").replace(" ", "_") for part in scope_parts)
    return f"fi-sparse-slot:{safe}"

"""Finland proof-surface projections.

These adapters expose existing Finland compiler facts through shared
proof-surface contracts. They are report/read-model projections only; they do
not authorize replay and do not change Finnish lowering or apply semantics.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from lawvm.core.compile_result import SourcePathology
from lawvm.core.execution_authorization import ExecutionAuthorization
from lawvm.core.frontier_work_item import FrontierWorkItem
from lawvm.core.source_witness import SourceWitness


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


_SOURCE_PATHOLOGY_RULES: dict[str, FinlandSourcePathologyProofRule] = {
    "PARTIAL_WHOLE_SECTION_PAYLOAD": FinlandSourcePathologyProofRule(
        code="PARTIAL_WHOLE_SECTION_PAYLOAD",
        lane="source_pathology",
        owner_phase="payload_normalization",
        strict_disposition="block",
        quirks_disposition="record",
        frontier_family="fi_partial_whole_section_payload",
        frontier_status="source_pathology_frontier",
        required_claim_kind="payload_completeness_resolution",
    ),
    "MALFORMED_BROAD_REPLACE_BODY": FinlandSourcePathologyProofRule(
        code="MALFORMED_BROAD_REPLACE_BODY",
        lane="source_pathology",
        owner_phase="payload_normalization",
        strict_disposition="block",
        quirks_disposition="record",
        frontier_family="fi_malformed_broad_replace_body",
        frontier_status="source_pathology_frontier",
        required_claim_kind="payload_completeness_resolution",
    ),
    "CONTAINER_MEMBERSHIP_MISMATCH": FinlandSourcePathologyProofRule(
        code="CONTAINER_MEMBERSHIP_MISMATCH",
        lane="source_pathology",
        owner_phase="typed_elaboration",
        strict_disposition="block",
        quirks_disposition="record",
        frontier_family="fi_container_membership_mismatch",
        frontier_status="source_pathology_frontier",
        required_claim_kind="container_membership_resolution",
    ),
    "SPARSE_ITEM_BODY_MISSING": FinlandSourcePathologyProofRule(
        code="SPARSE_ITEM_BODY_MISSING",
        lane="source_pathology",
        owner_phase="typed_elaboration",
        strict_disposition="block",
        quirks_disposition="record",
        frontier_family="fi_sparse_item_body_missing",
        frontier_status="source_pathology_frontier",
        required_claim_kind="sparse_slot_payload_resolution",
    ),
    "BASE_MISSING_CHAPTER_SPAN": FinlandSourcePathologyProofRule(
        code="BASE_MISSING_CHAPTER_SPAN",
        lane="source_pathology",
        owner_phase="source_chain_elaboration",
        strict_disposition="block",
        quirks_disposition="record",
        frontier_family="fi_missing_base_chapter_span",
        frontier_status="source_chain_frontier",
        required_claim_kind="base_source_chain_resolution",
    ),
    "RECODIFICATION_SOURCE_CHAIN_GAP": FinlandSourcePathologyProofRule(
        code="RECODIFICATION_SOURCE_CHAIN_GAP",
        lane="source_pathology",
        owner_phase="source_chain_elaboration",
        strict_disposition="block",
        quirks_disposition="record",
        frontier_family="fi_recodification_source_chain_gap",
        frontier_status="source_chain_frontier",
        required_claim_kind="recodification_source_chain_resolution",
    ),
    "TEMPORARY_SECTION_REBASE": FinlandSourcePathologyProofRule(
        code="TEMPORARY_SECTION_REBASE",
        lane="temporal_recovery",
        owner_phase="temporal_elaboration",
        strict_disposition="block",
        quirks_disposition="record",
        frontier_family="fi_temporary_section_rebase",
        frontier_status="temporal_frontier",
        required_claim_kind="temporal_base_selection_resolution",
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
        required_claim_kind="mutation_boundary_resolution",
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
    source_witness = SourceWitness(
        source_role="finland_source_pathology",
        artifact_id=source_statute,
        source_unit_id=source_unit_id,
        bounded_preview=str(row.get("message") or ""),
        source_lane=rule.lane,
        metadata={
            "source_pathology_code": row.get("code", ""),
            "source_statute": source_statute,
            "target_unit_kind": row.get("target_unit_kind", ""),
            "target_label": target_label,
            "detail": dict(row.get("detail") or {}),
        },
    )
    return FrontierWorkItem(
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
    )


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

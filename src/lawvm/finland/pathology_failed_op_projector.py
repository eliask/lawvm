"""Project Finland source-pathology and failed-operation rows into proof surfaces."""

from __future__ import annotations

import hashlib
import importlib
import json
from typing import Any, Mapping

from lawvm.core.compile_result import SourcePathology
from lawvm.core.execution_authorization import ExecutionAuthorization
from lawvm.core.frontier_work_item import FrontierWorkItem, frontier_work_item_with_claim_template
from lawvm.core.source_pathology import SourcePathologyProjection, source_pathology_projection
from lawvm.core.source_witness import SourceWitness
from lawvm.finland.proof_surface_row_helpers import kind_slug, preview_digest_witness
from lawvm.finland.source_pathology_proof_registry import source_pathology_proof_rule
from lawvm.core.quirks_disposition import QuirksDisposition

FAILED_OPERATION_REQUIRED_PROOFS: tuple[str, ...] = (
    "source_identity_proof",
    "target_identity_proof",
    "failed_operation_reason_classification",
    "payload_identity_or_manual_resolution_proof",
    "mutation_boundary_proof_before_replay_promotion",
)
FAILED_OPERATION_FORBIDDEN_SHORTCUTS: tuple[str, ...] = (
    "failed_operation_as_replay_authorization",
    "failed_operation_reason_as_manual_claim",
    "failed_operation_target_as_target_resolution_proof",
    "failed_operation_absence_as_source_cue_exhaustiveness_proof",
)
FAILED_OPERATION_SAFE_DEFAULT = (
    "treat_failed_operation_as_non_executable_frontier_until_source_target_payload_and_boundary_are_proven"
)


def pathology_row(pathology: SourcePathology | Mapping[str, Any]) -> dict[str, Any]:
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


def pathology_work_item_id(row: Mapping[str, Any], *, statute_id: str = "") -> str:
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


def failed_operation_row(failed_op: Mapping[str, Any]) -> dict[str, Any]:
    source_statute = str(failed_op.get("source") or failed_op.get("source_statute") or failed_op.get("amendment_id") or "")
    target_section = str(failed_op.get("target_section") or "")
    target_chapter = failed_op.get("target_chapter")
    target_part = failed_op.get("target_part")
    target_label = failed_operation_target_label(
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


def failed_operation_target_label(
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


def failed_operation_work_item_id(
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
    return f"fi-failed-operation:{source}:{kind_slug(str(row.get('reason_code') or 'unknown'))}:{digest}"
def _with_finland_claim_template(item: FrontierWorkItem) -> FrontierWorkItem:
    importlib.import_module("lawvm.finland.claim_kinds")
    return frontier_work_item_with_claim_template(item)


def source_pathology_execution_authorization(
    pathology: SourcePathology | Mapping[str, Any],
) -> ExecutionAuthorization:
    """Project a Finland source pathology into shared authorization metadata."""

    row = pathology_row(pathology)
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

    row = pathology_row(pathology)
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
        preview_digest=preview_digest_witness(bounded_preview),
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
        work_item_id=pathology_work_item_id(row, statute_id=statute_id),
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

    row = failed_operation_row(failed_op)
    reason_code = str(row.get("reason_code") or "unknown")
    return ExecutionAuthorization(
        executable=False,
        replay_authorized=False,
        authorization_status="failed_operation_not_replay_authority",
        authorization_rule_id=f"fi_failed_operation_{kind_slug(reason_code)}",
        owner_phase="replay_apply",
        strict_disposition="block",
        quirks_disposition=QuirksDisposition.RECORD,
        validator_status="failed_operation_requires_manual_or_deterministic_resolution",
        required_proofs=FAILED_OPERATION_REQUIRED_PROOFS,
        safe_default=FAILED_OPERATION_SAFE_DEFAULT,
        forbidden_shortcuts=FAILED_OPERATION_FORBIDDEN_SHORTCUTS,
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

    row = failed_operation_row(failed_op)
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
        preview_digest=preview_digest_witness(bounded_preview),
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
        work_item_id=failed_operation_work_item_id(row, statute_id=statute_id, index=index),
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
        required_proofs=FAILED_OPERATION_REQUIRED_PROOFS,
        safe_default=FAILED_OPERATION_SAFE_DEFAULT,
        forbidden_shortcuts=FAILED_OPERATION_FORBIDDEN_SHORTCUTS,
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


def source_pathology_projections(
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


__all__ = [
    "FAILED_OPERATION_FORBIDDEN_SHORTCUTS",
    "FAILED_OPERATION_REQUIRED_PROOFS",
    "FAILED_OPERATION_SAFE_DEFAULT",
    "failed_operation_execution_authorization",
    "failed_operation_frontier_work_item",
    "failed_operation_proof_surface_rows",
    "failed_operation_row",
    "failed_operation_work_item_id",
    "source_pathology_execution_authorization",
    "source_pathology_frontier_work_item",
    "source_pathology_proof_surface_rows",
    "source_pathology_projections",
]

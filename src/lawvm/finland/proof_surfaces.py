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

from lawvm.core.agreement_residual import AgreementResidual
from lawvm.core.candidate_set_certificate import CandidateSetCertificate
from lawvm.core.compile_result import SourcePathology
from lawvm.core.evidence_surface_report import EvidenceSurfaceReport
from lawvm.core.execution_authorization import ExecutionAuthorization
from lawvm.core.frontier_work_item import FrontierWorkItem
from lawvm.core.mutation_accounting import MutationAccountingResult, MutationInvariantReport
from lawvm.core.mutation_boundary_proof import MutationBoundaryProof
from lawvm.core.source_witness import DigestWitness, SourceWitness
from lawvm.core.source_witness import source_witness_digest_coverage
from lawvm.finland.consolidated_artifacts import artifact_record


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
            source_statute = str(report_like.get("source_statute") or "")
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

    source_pathology_authorizations = _mapping_sequence(payload.get("source_pathology_execution_authorizations"))
    source_pathology_frontier_items = _mapping_sequence(payload.get("source_pathology_frontier_work_items"))
    sparse_certificates = _mapping_sequence(payload.get("sparse_slot_candidate_set_certificates"))
    agreement_residuals = _mapping_sequence(payload.get("agreement_residuals"))
    mutation_boundary_proofs = _mapping_sequence(payload.get("mutation_boundary_proofs"))
    projection_rows = _mapping_sequence(payload.get("projection_rows"))
    failed_ops = _mapping_sequence(payload.get("failed_ops"))
    strict_fail_reasons = _string_sequence(payload.get("strict_fail_reasons"))
    ops = payload.get("ops")
    ops_summary = dict(ops) if isinstance(ops, Mapping) else {}
    rows = tuple(
        (
            *(
                {"surface": "source_pathology_execution_authorization", **dict(row)}
                for row in source_pathology_authorizations
            ),
            *(
                {"surface": "source_pathology_frontier_work_item", **dict(row)}
                for row in source_pathology_frontier_items
            ),
            *({"surface": "sparse_slot_candidate_set_certificate", **dict(row)} for row in sparse_certificates),
            *({"surface": "agreement_residual", **dict(row)} for row in agreement_residuals),
            *({"surface": "mutation_boundary_proof", **dict(row)} for row in mutation_boundary_proofs),
        )
    )
    summary = {
        "canonical_op_count": int(ops_summary.get("canonical") or 0),
        "failed_op_count": int(ops_summary.get("failed") or 0),
        "total_op_count": int(ops_summary.get("total") or 0),
        "source_pathology_execution_authorization_count": len(source_pathology_authorizations),
        "source_pathology_frontier_work_item_count": len(source_pathology_frontier_items),
        "sparse_slot_candidate_set_certificate_count": len(sparse_certificates),
        "agreement_residual_count": len(agreement_residuals),
        "mutation_boundary_proof_count": len(mutation_boundary_proofs),
        "source_pathology_frontier_source_witness_digest_coverage_counts": (
            _source_witness_digest_coverage_counts(source_pathology_frontier_items)
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
            ),
        },
    ).to_dict()


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


def _mapping_sequence(value: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, list | tuple):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


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


def _bool_field(row: Mapping[str, Any], key: str, *, default: bool) -> bool:
    value = row.get(key, default)
    if isinstance(value, bool):
        return value
    raise ValueError(f"mutation invariant {key} must be a boolean")


def _source_witness_digest_coverage_counts(
    frontier_items: tuple[Mapping[str, Any], ...],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in frontier_items:
        witness = item.get("source_witness")
        coverage = source_witness_digest_coverage(witness) if isinstance(witness, Mapping) else "missing_source_witness"
        counts[coverage] = counts.get(coverage, 0) + 1
    return dict(sorted(counts.items()))


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

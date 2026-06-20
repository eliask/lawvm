"""Replay-level evidence/report projection for the Finnish frontend."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, Optional

from lawvm.core.compile_result import SourcePathology, StrictProfile
from lawvm.core.mutation_accounting import MutationInvariantReport as ApplyMutationInvariantReport
from lawvm.core.observation_registry import get_finding_spec
from lawvm.core.observed_write_audit import ObservedWriteAudit
from lawvm.core.phase_result import Finding
from lawvm.core.regex_recognition_coverage import RegexRecognitionCoverage
from lawvm.finland.apply_events import (
    ApplyMutationEvent,
    build_apply_mutation_invariant_reports,
    check_apply_mutation_accounting,
    check_apply_mutation_invariant_reports,
)
from lawvm.finland.evidence_projector import (
    EvidenceProjectionRequest,
    MetaProjection,
    project_evidence,
)
from lawvm.finland.effect_lifecycle_signals import EffectLifecycleOverride
from lawvm.finland.mutation_boundary_proof_projector import mutation_boundary_proof_rows
from lawvm.finland.replay_findings import (
    _apply_mutation_boundary_violation_finding,
    _apply_mutation_invariant_report_finding,
    _base_observation_to_finding,
    _serialize_apply_mutation_event,
    _serialize_apply_mutation_invariant_report,
    _serialize_observed_write_audit,
    _strict_rejected_source_pathology_finding,
)
from lawvm.finland.restructure_plan import StructuralTransformPlan


@dataclass(frozen=True, slots=True)
class ReplayEvidenceProjectionRequest:
    """Evidence streams produced by replay before final product materialization."""

    parent_id: str
    replay_findings: list[Finding]
    source_pathologies: list[SourcePathology]
    elaboration_observations: list[Dict[str, object]]
    sparse_slot_bindings: list[Dict[str, object]]
    sparse_leftovers: list[Dict[str, object]]
    regex_recognition_coverages: list[RegexRecognitionCoverage]
    commencement_expiry_overrides: list[EffectLifecycleOverride]
    write_audits: list[ObservedWriteAudit]
    mutation_events: list[ApplyMutationEvent]
    restructure_plans: list[StructuralTransformPlan]
    source_pathologies_out: Optional[list[SourcePathology]]
    replay_meta_out: Optional[Dict[str, object]]
    strict_profile: Optional[StrictProfile]
    replay_print: Callable[[str], None]
    mutation_invariant_reports: list[ApplyMutationInvariantReport] | None = None


@dataclass(frozen=True, slots=True)
class ReplayEvidenceProjectionResult:
    """Derived replay evidence used by later product construction."""

    mutation_invariant_reports: tuple[ApplyMutationInvariantReport, ...]
    apply_mutation_boundary_violations: tuple[str, ...]


def project_replay_evidence(
    request: ReplayEvidenceProjectionRequest,
) -> ReplayEvidenceProjectionResult:
    """Project replay evidence into findings, compatibility sinks, and metadata."""

    _append_base_observation_findings(
        observations=request.elaboration_observations,
        replay_findings=request.replay_findings,
    )
    if request.source_pathologies_out is not None:
        request.source_pathologies_out.extend(request.source_pathologies)

    mutation_invariant_reports = _append_mutation_report_findings(request)
    boundary_violations = _append_mutation_boundary_findings(
        request,
        mutation_invariant_reports,
    )
    _emit_source_pathology_warnings(request)
    _append_strict_source_pathology_findings(request)

    project_evidence(
        EvidenceProjectionRequest(
            findings=tuple(request.replay_findings),
            meta_projections=_replay_evidence_meta_projections(
                request,
                mutation_invariant_reports=mutation_invariant_reports,
            ),
            proof_rows=tuple(
                mutation_boundary_proof_rows(
                    mutation_invariant_reports,
                    statute_id=request.parent_id,
                )
            )
            if mutation_invariant_reports
            else (),
            replay_meta_out=request.replay_meta_out,
        )
    )
    if request.replay_meta_out is not None and boundary_violations:
        request.replay_meta_out["apply_mutation_boundary_violations"] = list(
            boundary_violations
        )

    return ReplayEvidenceProjectionResult(
        mutation_invariant_reports=mutation_invariant_reports,
        apply_mutation_boundary_violations=boundary_violations,
    )


def _append_base_observation_findings(
    *,
    observations: list[Dict[str, object]],
    replay_findings: list[Finding],
) -> None:
    """Convert base-source observations into governed replay findings."""

    seen_base_observations: set[tuple[str, str, str, str, str]] = set()
    for obs_dict in observations:
        obs_kind = str(obs_dict.get("kind", "")).strip()
        if obs_kind != "LABEL_EID_DIVERGENCE" and not obs_kind.startswith("BASE_"):
            continue
        if get_finding_spec(obs_kind) is None:
            continue
        source_statute = str(obs_dict.get("source_statute", "")).strip()
        detail_dict = _detail_dict(obs_dict)
        section_address = str(detail_dict.get("section_address", "")).strip()
        if not section_address:
            raw_path = detail_dict.get("path")
            if isinstance(raw_path, list):
                section_address = "/".join(str(part) for part in raw_path)
            elif isinstance(raw_path, tuple):
                section_address = "/".join(str(part) for part in raw_path)
        label = str(detail_dict.get("label", "")).strip()
        eid = str(detail_dict.get("eId", "")).strip()
        key = (obs_kind, source_statute, section_address, label, eid)
        if key in seen_base_observations:
            continue
        seen_base_observations.add(key)
        finding = _base_observation_to_finding(obs_dict)
        if finding is not None:
            replay_findings.append(finding)


def _detail_dict(obs_dict: Dict[str, object]) -> dict[str, object]:
    raw_detail = obs_dict.get("detail")
    if not isinstance(raw_detail, dict):
        return {}
    return {str(k): v for k, v in raw_detail.items()}


def _replay_evidence_meta_projections(
    request: ReplayEvidenceProjectionRequest,
    *,
    mutation_invariant_reports: tuple[ApplyMutationInvariantReport, ...],
) -> tuple[MetaProjection, ...]:
    projections: list[MetaProjection] = []

    if request.source_pathologies:
        projections.append(
            MetaProjection(
                meta_key="source_pathologies",
                rows=tuple(
                    {
                        "source_statute": pathology.source_statute,
                        **pathology.as_detail(),
                    }
                    for pathology in request.source_pathologies
                ),
            )
        )
    if request.elaboration_observations:
        projections.append(
            MetaProjection(
                meta_key="elaboration_observations",
                rows=tuple(dict(observation) for observation in request.elaboration_observations),
            )
        )
    uncovered_candidate_audits = tuple(
        {
            "source_statute": str(observation.get("source_statute", "") or ""),
            **_detail_dict(observation),
        }
        for observation in request.elaboration_observations
        if str(observation.get("kind", "") or "") == "APPLY.UNCOVERED_BODY_CANDIDATE_AUDIT"
    )
    if uncovered_candidate_audits:
        projections.append(
            MetaProjection(
                meta_key="uncovered_body_candidate_audits",
                rows=uncovered_candidate_audits,
                dedup_keys=("source_statute", "op_id", "target_section", "disposition"),
            )
        )
    apply_resolved_op_audits = tuple(
        {
            "source_statute": str(observation.get("source_statute", "") or ""),
            **_detail_dict(observation),
        }
        for observation in request.elaboration_observations
        if str(observation.get("kind", "") or "") == "APPLY.RESOLVED_OP_AUDIT"
    )
    if apply_resolved_op_audits:
        projections.append(
            MetaProjection(
                meta_key="apply_resolved_op_audits",
                rows=apply_resolved_op_audits,
                dedup_keys=("source_statute", "op_id", "disposition"),
            )
        )
    if request.sparse_slot_bindings:
        projections.append(
            MetaProjection(
                meta_key="sparse_slot_bindings",
                rows=tuple(dict(row) for row in request.sparse_slot_bindings),
            )
        )
    if request.sparse_leftovers:
        projections.append(
            MetaProjection(
                meta_key="sparse_leftovers",
                rows=tuple(dict(row) for row in request.sparse_leftovers),
            )
        )
    if request.regex_recognition_coverages:
        projections.append(
            MetaProjection(
                meta_key="regex_recognition_coverage",
                rows=tuple(coverage.to_dict() for coverage in request.regex_recognition_coverages),
            )
        )
    if request.commencement_expiry_overrides:
        projections.append(
            MetaProjection(
                meta_key="commencement_expiry_overrides",
                rows=tuple(row.to_meta_row() for row in request.commencement_expiry_overrides),
            )
        )
    if request.write_audits:
        projections.append(
            MetaProjection(
                meta_key="apply_write_audits",
                rows=tuple(_serialize_observed_write_audit(audit) for audit in request.write_audits),
            )
        )

    occupancy_observations = tuple(
        {
            "source_statute": finding.source_statute or "",
            "detail": dict(finding.detail or {}),
        }
        for finding in request.replay_findings
        if finding.kind == "APPLY.OCCUPANCY_POLICY_VIOLATION"
    )
    if occupancy_observations:
        projections.append(
            MetaProjection(
                meta_key="occupancy_observations",
                rows=occupancy_observations,
                dedup_keys=("source_statute",),
            )
        )

    if request.mutation_events:
        projections.append(
            MetaProjection(
                meta_key="apply_mutation_events",
                rows=tuple(_serialize_apply_mutation_event(event) for event in request.mutation_events),
            )
        )
    if mutation_invariant_reports:
        projections.append(
            MetaProjection(
                meta_key="apply_mutation_invariant_reports",
                rows=tuple(
                    _serialize_apply_mutation_invariant_report(report)
                    for report in mutation_invariant_reports
                ),
            )
        )
    if request.restructure_plans:
        projections.append(
            MetaProjection(
                meta_key="restructure_plans",
                rows=tuple(plan.to_dict() for plan in request.restructure_plans),
            )
        )

    return tuple(projections)


def _append_mutation_report_findings(
    request: ReplayEvidenceProjectionRequest,
) -> tuple[ApplyMutationInvariantReport, ...]:
    if request.mutation_invariant_reports:
        mutation_invariant_reports = tuple(request.mutation_invariant_reports)
    elif request.mutation_events:
        mutation_invariant_reports = build_apply_mutation_invariant_reports(
            request.mutation_events
        )
    else:
        return ()
    seen_apply_mutation_findings: set[tuple[str, str, str, str]] = set()
    for report in mutation_invariant_reports:
        for accounting_result in report.results:
            finding = _apply_mutation_invariant_report_finding(
                report=report,
                result=accounting_result,
                source_statute=request.parent_id,
            )
            if finding is None:
                continue
            dedupe_key = (finding.kind, report.op_id, report.helper, request.parent_id)
            if dedupe_key in seen_apply_mutation_findings:
                continue
            request.replay_findings.append(finding)
            seen_apply_mutation_findings.add(dedupe_key)
    return mutation_invariant_reports


def _append_mutation_boundary_findings(
    request: ReplayEvidenceProjectionRequest,
    mutation_invariant_reports: tuple[ApplyMutationInvariantReport, ...],
) -> tuple[str, ...]:
    boundary_violations = (
        check_apply_mutation_invariant_reports(mutation_invariant_reports)
        if mutation_invariant_reports
        else check_apply_mutation_accounting(request.mutation_events)
    )
    if boundary_violations:
        if not mutation_invariant_reports:
            seen_apply_boundary_findings = {
                (
                    finding.kind,
                    str(finding.detail.get("violation") or ""),
                    request.parent_id,
                )
                for finding in request.replay_findings
            }
            for violation in boundary_violations:
                finding = _apply_mutation_boundary_violation_finding(
                    violation=violation,
                    source_statute=request.parent_id,
                )
                key = (
                    finding.kind,
                    str(finding.detail.get("violation") or ""),
                    request.parent_id,
                )
                if key in seen_apply_boundary_findings:
                    continue
                request.replay_findings.append(finding)
                seen_apply_boundary_findings.add(key)
        for violation in boundary_violations:
            request.replay_print(f"WARNING apply mutation boundary: {violation}")
    return tuple(boundary_violations)


def _emit_source_pathology_warnings(request: ReplayEvidenceProjectionRequest) -> None:
    for pathology in request.source_pathologies:
        request.replay_print(
            f"WARNING source pathology: {pathology.code} {pathology.source_statute} {pathology.target_label}"
        )


def _append_strict_source_pathology_findings(
    request: ReplayEvidenceProjectionRequest,
) -> None:
    if request.strict_profile is None or not request.source_pathologies:
        return

    existing_rejections = {
        (
            finding.kind,
            str(finding.detail.get("code") or ""),
            str(finding.detail.get("target_label") or ""),
        )
        for finding in request.replay_findings
        if finding.kind == "APPLY.SOURCE_PATHOLOGY_DETECTED"
    }
    for pathology in request.source_pathologies:
        finding = _strict_rejected_source_pathology_finding(
            pathology,
            stage="replay_xml",
        )
        key = (
            "APPLY.SOURCE_PATHOLOGY_DETECTED",
            pathology.code,
            pathology.target_label,
        )
        if key in existing_rejections:
            continue
        request.replay_findings.append(finding)
        existing_rejections.add(key)

"""Project Finland strict-report payloads into the shared evidence envelope."""

from __future__ import annotations

from typing import Any, Mapping

from lawvm.core.candidate_set_certificate import candidate_set_evidence_report
from lawvm.core.evidence_surface_report import EvidenceSurfaceReport
from lawvm.core.execution_authorization import execution_authorization_evidence_report
from lawvm.core.frontier_work_item import frontier_work_item_evidence_report
from lawvm.core.potential_operation import potential_operation_evidence_report
from lawvm.core.regex_recognition_coverage import regex_recognition_coverage_evidence_report
from lawvm.core.source_pathology import source_pathology_evidence_report
from lawvm.core.source_unit_coverage import source_unit_coverage_evidence_report
from lawvm.core.source_witness import (
    nested_source_witness_digest_coverage_counts,
    source_witness_digest_coverage_counts,
)
from lawvm.finland.agreement_residual_proof_projector import strict_report_agreement_surface_rows
from lawvm.finland.pathology_failed_op_projector import (
    source_pathology_projections as _source_pathology_projections,
)
from lawvm.finland.proof_surface_row_helpers import (
    authorization_rows_with_report,
    count_by_field,
    mapping_or_empty,
    mapping_sequence,
    string_sequence,
)
from lawvm.finland.recovery_temporal_proof_projector import (
    recovery_execution_authorization_rows_from_projection_rows,
    source_completeness_status_row,
    temporal_resolution_evidence_rows_from_projection_rows,
)

def finland_strict_report_evidence_surface(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Wrap Finland strict-report JSON in the shared evidence envelope."""

    source_pathologies = mapping_sequence(payload.get("source_pathologies"))
    source_pathology_projections = _source_pathology_projections(source_pathologies)
    source_pathology_report = source_pathology_evidence_report(
        source_pathology_projections,
        jurisdiction="fi",
        report_kind="finland_source_pathology",
    ).to_dict()
    source_pathology_rows = mapping_sequence(source_pathology_report.get("rows"))
    source_pathology_authorizations = mapping_sequence(payload.get("source_pathology_execution_authorizations"))
    source_pathology_authorization_report = execution_authorization_evidence_report(
        source_pathology_authorizations,
        jurisdiction="fi",
        report_kind="finland_source_pathology_execution_authorizations",
    ).to_dict()
    source_pathology_authorization_report_rows = mapping_sequence(
        source_pathology_authorization_report.get("rows")
    )
    source_pathology_authorization_rows = authorization_rows_with_report(
        source_pathology_authorizations,
        source_pathology_authorization_report_rows,
    )
    source_pathology_authorization_report_summary = dict(
        source_pathology_authorization_report.get("summary") or {}
    )
    source_pathology_frontier_items = mapping_sequence(payload.get("source_pathology_frontier_work_items"))
    failed_operation_authorizations = mapping_sequence(payload.get("failed_operation_execution_authorizations"))
    failed_operation_authorization_report = execution_authorization_evidence_report(
        failed_operation_authorizations,
        jurisdiction="fi",
        report_kind="finland_failed_operation_execution_authorizations",
    ).to_dict()
    failed_operation_authorization_report_rows = mapping_sequence(
        failed_operation_authorization_report.get("rows")
    )
    failed_operation_authorization_rows = authorization_rows_with_report(
        failed_operation_authorizations,
        failed_operation_authorization_report_rows,
    )
    failed_operation_authorization_report_summary = dict(
        failed_operation_authorization_report.get("summary") or {}
    )
    failed_operation_frontier_items = mapping_sequence(payload.get("failed_operation_frontier_work_items"))
    frontier_items = (*source_pathology_frontier_items, *failed_operation_frontier_items)
    frontier_work_item_report = frontier_work_item_evidence_report(
        frontier_items,
        jurisdiction="fi",
        report_kind="finland_strict_report_manual_frontiers",
    ).to_dict()
    frontier_work_item_report_rows = mapping_sequence(
        frontier_work_item_report.get("rows")
    )
    frontier_work_item_report_summary = dict(
        frontier_work_item_report.get("summary") or {}
    )
    source_pathology_frontier_report_rows = frontier_work_item_report_rows[
        : len(source_pathology_frontier_items)
    ]
    failed_operation_frontier_report_rows = frontier_work_item_report_rows[
        len(source_pathology_frontier_items) :
    ]
    potential_operations = mapping_sequence(payload.get("potential_operations"))
    potential_operation_report = potential_operation_evidence_report(
        potential_operations,
        jurisdiction="fi",
        report_kind="finland_strict_report_potential_operations",
    ).to_dict()
    potential_operation_rows = mapping_sequence(potential_operation_report.get("rows"))
    potential_operation_summary = dict(potential_operation_report.get("summary") or {})
    sparse_certificates = mapping_sequence(payload.get("sparse_slot_candidate_set_certificates"))
    source_lineage_witnesses = mapping_sequence(payload.get("source_lineage_source_witnesses"))
    source_unit_coverages = mapping_sequence(payload.get("source_unit_coverages"))
    source_unit_coverage_report = source_unit_coverage_evidence_report(
        source_unit_coverages,
        jurisdiction="fi",
        report_kind="finland_strict_report_source_unit_coverage",
    ).to_dict()
    source_unit_coverage_rows = mapping_sequence(source_unit_coverage_report.get("rows"))
    source_unit_coverage_summary = dict(source_unit_coverage_report.get("summary") or {})
    regex_recognition_coverages = mapping_sequence(payload.get("regex_recognition_coverage"))
    regex_recognition_coverage_report = regex_recognition_coverage_evidence_report(
        regex_recognition_coverages,
        jurisdiction="fi",
        report_kind="finland_strict_report_regex_recognition_coverage",
    ).to_dict()
    regex_recognition_coverage_rows = mapping_sequence(
        regex_recognition_coverage_report.get("rows")
    )
    regex_recognition_coverage_summary = dict(
        regex_recognition_coverage_report.get("summary") or {}
    )
    agreement_residuals = mapping_sequence(payload.get("agreement_residuals"))
    agreement_report_rows, agreement_report_summary = strict_report_agreement_surface_rows(
        agreement_residuals,
        payload=payload,
    )
    mutation_boundary_proofs = mapping_sequence(payload.get("mutation_boundary_proofs"))
    projection_rows = mapping_sequence(payload.get("projection_rows"))
    failed_ops = mapping_sequence(payload.get("failed_ops"))
    strict_fail_reasons = string_sequence(payload.get("strict_fail_reasons"))
    source_completeness_row = source_completeness_status_row(payload)
    source_completeness_issues = mapping_sequence(payload.get("source_completeness_issues"))
    temporal_resolution_rows = temporal_resolution_evidence_rows_from_projection_rows(
        projection_rows,
        strict_fail_reasons=strict_fail_reasons,
    )
    recovery_authorization_rows = recovery_execution_authorization_rows_from_projection_rows(
        projection_rows,
        strict_fail_reasons=strict_fail_reasons,
        statute_id=str(payload.get("statute_id") or ""),
    )
    recovery_authorization_report = execution_authorization_evidence_report(
        recovery_authorization_rows,
        jurisdiction="fi",
        report_kind="finland_recovery_execution_authorizations",
    ).to_dict()
    recovery_authorization_report_rows = mapping_sequence(
        recovery_authorization_report.get("rows")
    )
    recoveryauthorization_rows_with_report = authorization_rows_with_report(
        recovery_authorization_rows,
        recovery_authorization_report_rows,
    )
    recovery_authorization_report_summary = dict(
        recovery_authorization_report.get("summary") or {}
    )
    strict_report_candidate_sets = mapping_sequence(payload.get("strict_report_candidate_set_certificates"))
    strict_report_candidate_set_report = candidate_set_evidence_report(
        strict_report_candidate_sets,
        jurisdiction="fi",
        report_kind="finland_strict_report_candidate_sets",
    ).to_dict()
    strict_report_candidate_set_report_rows = mapping_sequence(
        strict_report_candidate_set_report.get("rows")
    )
    strict_report_candidate_set_report_summary = dict(
        strict_report_candidate_set_report.get("summary") or {}
    )
    strict_report_candidate_set_authorizations = mapping_sequence(
        payload.get("strict_report_candidate_set_execution_authorizations")
    )
    strict_report_candidate_set_authorization_report = (
        execution_authorization_evidence_report(
            strict_report_candidate_set_authorizations,
            jurisdiction="fi",
            report_kind="finland_strict_report_candidate_set_authorizations",
        ).to_dict()
    )
    strict_report_candidate_set_authorization_report_rows = mapping_sequence(
        strict_report_candidate_set_authorization_report.get("rows")
    )
    strict_report_candidate_set_authorization_rows = authorization_rows_with_report(
        strict_report_candidate_set_authorizations,
        strict_report_candidate_set_authorization_report_rows,
    )
    strict_report_candidate_set_authorization_report_summary = dict(
        strict_report_candidate_set_authorization_report.get("summary") or {}
    )
    strict_report_candidate_set_frontier_items = mapping_sequence(
        payload.get("strict_report_candidate_set_frontier_work_items")
    )
    ownership_closure = mapping_or_empty(payload.get("ownership_closure_certificate"))
    ownership_closure_report = mapping_or_empty(payload.get("ownership_closure_report"))
    ownership_closure_rows = mapping_sequence(ownership_closure_report.get("rows"))
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
                {
                    **dict(row),
                    "surface": "source_pathology_execution_authorization",
                }
                for row in source_pathology_authorization_rows
            ),
            *(
                {**dict(row), "surface": "source_pathology_frontier_work_item"}
                for row in source_pathology_frontier_report_rows
            ),
            *(
                {
                    **dict(row),
                    "surface": "failed_operation_execution_authorization",
                }
                for row in failed_operation_authorization_rows
            ),
            *(
                {**dict(row), "surface": "failed_operation_frontier_work_item"}
                for row in failed_operation_frontier_report_rows
            ),
            *(
                {"surface": "potential_operation", **dict(row)}
                for row in potential_operation_rows
            ),
            *({"surface": "sparse_slot_candidate_set_certificate", **dict(row)} for row in sparse_certificates),
            *({"surface": "source_lineage_source_witness", **dict(row)} for row in source_lineage_witnesses),
            *({"surface": "source_unit_coverage", **dict(row)} for row in source_unit_coverage_rows),
            *(
                {"surface": "regex_recognition_coverage", **dict(row)}
                for row in regex_recognition_coverage_rows
            ),
            *({"surface": "agreement_residual", **dict(row)} for row in agreement_report_rows),
            *({"surface": "mutation_boundary_proof", **dict(row)} for row in mutation_boundary_proofs),
            *(({"surface": "source_completeness_status", **source_completeness_row},) if source_completeness_row else ()),
            *({"surface": "source_completeness_issue", **dict(row)} for row in source_completeness_issues),
            *({"surface": "temporal_resolution_evidence", **dict(row)} for row in temporal_resolution_rows),
            *(
                {**dict(row), "surface": "recovery_execution_authorization"}
                for row in recoveryauthorization_rows_with_report
            ),
            *(
                {**dict(row), "surface": "strict_report_candidate_set_certificate"}
                for row in strict_report_candidate_set_report_rows
            ),
            *(
                {
                    **dict(row),
                    "surface": "strict_report_candidate_set_execution_authorization",
                }
                for row in strict_report_candidate_set_authorization_rows
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
        "source_pathology_execution_authorization_status_counts": dict(
            source_pathology_authorization_report_summary.get(
                "authorization_status_counts"
            )
            or {}
        ),
        "source_pathology_execution_authorization_strict_blocked_count": int(
            source_pathology_authorization_report_summary.get("strict_blocked_count")
            or 0
        ),
        "source_pathology_frontier_work_item_count": len(source_pathology_frontier_items),
        "failed_operation_execution_authorization_count": len(failed_operation_authorizations),
        "failed_operation_execution_authorization_status_counts": dict(
            failed_operation_authorization_report_summary.get(
                "authorization_status_counts"
            )
            or {}
        ),
        "failed_operation_execution_authorization_strict_blocked_count": int(
            failed_operation_authorization_report_summary.get("strict_blocked_count")
            or 0
        ),
        "failed_operation_frontier_work_item_count": len(failed_operation_frontier_items),
        "potential_operation_count": len(potential_operation_rows),
        "potential_operation_classification_counts": dict(
            potential_operation_summary.get("classification_counts") or {}
        ),
        "potential_operation_family_counts": dict(
            potential_operation_summary.get("operation_family_counts") or {}
        ),
        "frontier_claim_template_status_counts": dict(
            frontier_work_item_report_summary.get(
                "suggested_claim_template_status_counts"
            )
            or {}
        ),
        "frontier_claim_template_kind_counts": dict(
            frontier_work_item_report_summary.get(
                "suggested_claim_template_kind_counts"
            )
            or {}
        ),
        "frontier_work_item_family_counts": dict(
            frontier_work_item_report_summary.get("frontier_family_counts") or {}
        ),
        "frontier_work_item_status_counts": dict(
            frontier_work_item_report_summary.get("frontier_status_counts") or {}
        ),
        "sparse_slot_candidate_set_certificate_count": len(sparse_certificates),
        "source_lineage_source_witness_count": len(source_lineage_witnesses),
        "source_unit_coverage_count": len(source_unit_coverage_rows),
        "source_unit_coverage_status_counts": dict(
            source_unit_coverage_summary.get("coverage_status_counts") or {}
        ),
        "source_unit_coverage_family_counts": dict(
            source_unit_coverage_summary.get("unit_family_counts") or {}
        ),
        "regex_recognition_coverage_count": len(regex_recognition_coverage_rows),
        "regex_recognition_coverage_status_counts": dict(
            regex_recognition_coverage_summary.get("coverage_status_counts") or {}
        ),
        "regex_recognition_unclassified_gap_count": int(
            regex_recognition_coverage_summary.get("unclassified_gap_count") or 0
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
        "source_completeness_issue_kind_counts": count_by_field(
            source_completeness_issues,
            "kind",
        ),
        "source_completeness_issue_family_counts": count_by_field(
            source_completeness_issues,
            "issue_family",
        ),
        "temporal_resolution_evidence_count": len(temporal_resolution_rows),
        "temporal_resolution_status_counts": count_by_field(
            temporal_resolution_rows,
            "temporal_resolution_status",
        ),
        "recovery_execution_authorization_count": len(recovery_authorization_rows),
        "recovery_execution_authorization_status_counts": dict(
            recovery_authorization_report_summary.get("authorization_status_counts")
            or {}
        ),
        "recovery_execution_authorization_strict_blocked_count": int(
            recovery_authorization_report_summary.get("strict_blocked_count") or 0
        ),
        "strict_report_candidate_set_certificate_count": len(strict_report_candidate_sets),
        "strict_report_candidate_set_status_counts": dict(
            strict_report_candidate_set_report_summary.get(
                "candidate_set_status_counts"
            )
            or {}
        ),
        "strict_report_candidate_set_kind_counts": dict(
            strict_report_candidate_set_report_summary.get("candidate_set_kind_counts")
            or {}
        ),
        "strict_report_candidate_set_blocker_family_counts": dict(
            strict_report_candidate_set_report_summary.get("blocker_family_counts")
            or {}
        ),
        "strict_report_candidate_set_execution_authorization_count": len(
            strict_report_candidate_set_authorizations
        ),
        "strict_report_candidate_set_execution_authorization_status_counts": dict(
            strict_report_candidate_set_authorization_report_summary.get(
                "authorization_status_counts"
            )
            or {}
        ),
        "strict_report_candidate_set_execution_authorization_strict_blocked_count": int(
            strict_report_candidate_set_authorization_report_summary.get(
                "strict_blocked_count"
            )
            or 0
        ),
        "strict_report_candidate_set_frontier_work_item_count": len(
            strict_report_candidate_set_frontier_items
        ),
        "strict_report_candidate_set_frontier_status_counts": count_by_field(
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
        "ownership_closure_owned_counts": dict(
            ownership_closure_summary.get("owned_counts") or {}
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
                "regex_coverage_as_replay_authorization",
                "bounded_wildcard_as_semantic_proof",
            ),
        },
    ).to_dict()


__all__ = [
    "finland_strict_report_evidence_surface",
]

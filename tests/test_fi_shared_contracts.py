from typing_extensions import override
from typing import Any, cast

import pytest

from lawvm.core.agreement_residual import (
    AgreementResidual,
    AgreementSurface,
    agreement_surface_evidence_report,
    agreement_surface_from_residuals,
)
from lawvm.core.candidate_set_coverage import (
    CANDIDATE_SET_COMPLETE,
    CANDIDATE_SET_TRUNCATED,
    CandidateSetCoverage,
    candidate_set_evidence_report,
)
from lawvm.core.evidence_contracts import (
    CorpusFindingEvidenceRow,
    CorpusOperationEvidenceRow,
    CorpusRowStatus,
    EvidenceSummary,
    evidence_row_kind,
    evidence_rule_ids,
    validate_corpus_finding_evidence_row,
    validate_corpus_operation_evidence_row,
)
from lawvm.core.compile_result import SourcePathology
from lawvm.core.execution_authorization import (
    ExecutionAuthorization,
    execution_authorization_evidence_report,
    execution_authorization_from_kernel_result,
    validate_execution_authorization,
)
from lawvm.core.evidence_kernel import AuthorizationResult
from lawvm.core.evidence_surface_report import EvidenceSurfaceReport
from lawvm.core.frontend_contract import (
    DerivedCompatibilityArtifact,
    FrontendCapability,
    SurfaceParseResult,
    frontend_capability_matrix_evidence_report,
    frontend_capability_evidence_report,
)
from lawvm.core.frontend_phase_surface import (
    FrontendDiagnostic,
    FrontendPhaseRow,
    FrontendPhaseSurface,
    frontend_diagnostic_findings,
    frontend_phase_surface_evidence_report,
)
from lawvm.core.frontier_work_item import (
    FrontierWorkItem,
    frontier_work_item_claim_closure_report,
    frontier_work_item_claim_template,
    frontier_work_item_claim_template_status,
    frontier_work_item_evidence_report,
    frontier_work_item_with_claim_template,
    validate_frontier_work_item,
)
from lawvm.core.frozen_values import FrozenDict
from lawvm.core.mutation_accounting import build_mutation_invariant_reports
from lawvm.core.mutation_boundary_proof import MutationBoundaryProof
from lawvm.core.mutation_boundary_proof import mutation_boundary_evidence_report
from lawvm.core.mutation_events import MutationEvent
from lawvm.core.ownership_closure import (
    OwnershipClosureCoverage,
    ownership_closure_evidence_report,
)
from lawvm.core.payload_elaboration import (
    PayloadCompletenessWitness,
    PayloadElaborationResult,
    SlotBinding,
    SlotBindingReport,
    payload_elaboration_evidence_report,
)
from lawvm.core.phase_replay_gate import PhaseLocalReplayGate
from lawvm.core.potential_operation import (
    POTENTIAL_OPERATION_COMPILED,
    POTENTIAL_OPERATION_FAILED,
    PotentialOperation,
    potential_operation_evidence_report,
)
from lawvm.core.proof_obligations import (
    PROOF_OBLIGATION_BLOCKED,
    PROOF_OBLIGATION_COMPLETE,
    ProofObligationCoverage,
)
from lawvm.core.regex_recognition_coverage import (
    REGEX_RECOGNITION_FULLY_CLASSIFIED,
    REGEX_RECOGNITION_UNCLASSIFIED_GAP,
    RegexRecognitionCoverage,
    regex_recognition_coverage_evidence_report,
    regex_source_text_hash,
)
from lawvm.core.proof_surfaces import (
    ProofSurface,
    ProofSurfaceRow,
    proof_surface_from_evidence_report,
)
from lawvm.core.provenance_graph import ArtifactRef
from lawvm.core.source_acquisition import (
    SourceAcquisitionAssertion,
    SourceAcquisitionAttestation,
    SourceBundlePolicy,
    source_bundle_evidence_report,
)
from lawvm.core.source_completeness import (
    SourceCompletenessStatus,
    source_completeness_evidence_report,
    source_completeness_status_from_mapping,
)
from lawvm.core.source_pathology import (
    SourcePathologyProjection,
    source_pathology_evidence_report,
    source_pathology_projection,
)
from lawvm.core.source_locator import SourceLocator, source_ref_from_locator
from lawvm.core.source_witness import (
    DigestWitness,
    nested_source_witness_digest_coverage_counts,
    SourceWitness,
    source_witness_digest_coverage,
    source_witness_digest_coverage_counts,
    source_witness_evidence_report,
    source_witness_from_mapping,
    source_witness_role_key,
)
from lawvm.core.source_unit_coverage import (
    SOURCE_UNIT_FRONTIER_WITNESSED,
    SOURCE_UNIT_LINEAGE_WITNESSED,
    SourceUnitCoverage,
    source_unit_coverage_evidence_report,
)
from lawvm.core.token_tape import AnnotatedTokenView, TokenAnnotation, TokenLexeme, TokenTape
from lawvm.contracts import ArtifactEnvelope, ProcessingStatus, to_wire_jsonable
from lawvm.core.replay_contracts import ReplayAmendmentStep, ReplayCheckpoint, ReplaySummary, ReplayTextView
from lawvm.core.verification_contracts import (
    CoverageAttribution,
    CurrentTextVerificationMatrix,
    DivergenceRecord,
    DivergencePartition,
    FilteredDivergenceRecord,
    VerifyIssue,
    VerifySummary,
    current_text_verification_matrix_from_mapping,
)
from lawvm.core.quirks_disposition import QuirksDisposition


def test_execution_authorization_allows_explicit_replay_authorized_rows() -> None:
    authorization = ExecutionAuthorization(
        executable=True,
        replay_authorized=True,
        authorization_status="replay_authorized",
        authorization_rule_id="test_authorized_rule",
        owner_phase="canonical_op_compilation",
        strict_disposition="record",
        required_proofs=(),
        safe_default="execute_lowered_operations",
    )

    data = authorization.to_dict()

    assert data["replay_authorized"] is True
    assert data["required_proofs"] == []
    assert validate_execution_authorization(data) == ()


def test_phase_local_replay_gate_authorizes_only_complete_exact_claim() -> None:
    gate = PhaseLocalReplayGate(
        gate_id="fi-gate-1",
        jurisdiction="fi",
        claim_id="claim-1",
        claim_kind="fi.v1.SPARSE_SLOT_PAYLOAD_RESOLUTION",
        frontier_ref="frontier-1",
        owner_phase="typed_elaboration",
        authorization_rule_id="fi_sparse_slot_phase_gate_v1",
        required_proofs=(
            "target_uniqueness_proof",
            "payload_identity_proof",
            "mutation_boundary_proof",
        ),
        satisfied_proofs=(
            "target_uniqueness_proof",
            "payload_identity_proof",
            "mutation_boundary_proof",
        ),
        candidate_operation_family="sparse_item_payload_resolution",
        candidate_targets=("section:2/subsection:1/item:3",),
        detail={"mutation_boundary_proof_ref": "proof-1"},
    )

    evaluation = gate.evaluate_for_claim(
        claim_id="claim-1",
        claim_kind="fi.v1.SPARSE_SLOT_PAYLOAD_RESOLUTION",
        frontier_ref="frontier-1",
    )
    authorization = gate.to_execution_authorization().to_dict()

    assert evaluation.replay_authorized is True
    assert evaluation.reason_code == "phase_replay_gate_authorized"
    assert gate.to_dict()["schema"] == "lawvm.phase_local_replay_gate.v1"
    assert authorization["replay_authorized"] is True
    assert authorization["executable"] is True
    assert authorization["required_proofs"] == []
    assert authorization["authorization_status"] == "replay_authorized"
    assert "manual_claim_as_phase_replay_gate" in authorization["forbidden_shortcuts"]


def test_phase_local_replay_gate_blocks_mismatch_and_missing_proofs() -> None:
    gate = PhaseLocalReplayGate(
        gate_id="fi-gate-2",
        jurisdiction="fi",
        claim_id="claim-1",
        claim_kind="fi.v1.SPARSE_SLOT_PAYLOAD_RESOLUTION",
        frontier_ref="frontier-1",
        owner_phase="typed_elaboration",
        authorization_rule_id="fi_sparse_slot_phase_gate_v1",
        required_proofs=(
            "target_uniqueness_proof",
            "payload_identity_proof",
            "mutation_boundary_proof",
        ),
        satisfied_proofs=("target_uniqueness_proof",),
    )

    mismatch = gate.evaluate_for_claim(
        claim_id="claim-1",
        claim_kind="fi.v1.SPARSE_SLOT_PAYLOAD_RESOLUTION",
        frontier_ref="different-frontier",
    )
    incomplete = gate.evaluate_for_claim(
        claim_id="claim-1",
        claim_kind="fi.v1.SPARSE_SLOT_PAYLOAD_RESOLUTION",
        frontier_ref="frontier-1",
    )
    authorization = gate.to_execution_authorization().to_dict()

    assert mismatch.replay_authorized is False
    assert mismatch.reason_code == "rejected_phase_replay_gate_frontier_mismatch"
    assert incomplete.replay_authorized is False
    assert incomplete.reason_code == "rejected_phase_replay_gate_missing_proofs"
    assert incomplete.missing_proofs == (
        "payload_identity_proof",
        "mutation_boundary_proof",
    )
    assert authorization["replay_authorized"] is False
    assert authorization["authorization_status"] == "phase_replay_gate_blocked"
    assert authorization["required_proofs"] == [
        "payload_identity_proof",
        "mutation_boundary_proof",
    ]


def test_ownership_closure_coverage_closes_only_zero_unowned_slice() -> None:
    certificate = OwnershipClosureCoverage(
        certificate_id="closure-fi-demo",
        corpus_slice_id="fi-demo-slice",
        source_bundle_hash="sha256:source",
        profile_id="strict",
        interpretation_policy_id="fi-policy",
        graph_snapshot_hash="sha256:graph",
        phase_report_ids={
            "source_artifact_coverage": "report-source",
            "execution_authorization": "report-auth",
            "agreement_residual": "report-residual",
        },
        closed=True,
        unowned_counts={
            "unclassified_source_units": 0,
            "potential_ops_without_status": 0,
            "candidates_without_authorization": 0,
        },
        owned_counts={"replay_authorized_operations": 3},
        detail={
            "closure_dimensions": (
                "source_artifact_coverage",
                "execution_authorization",
                "agreement_residual",
            )
        },
    )

    data = certificate.to_dict()
    report = ownership_closure_evidence_report(certificate, jurisdiction="fi").to_dict()

    assert data["schema"] == "lawvm.ownership_closure_coverage.v1"
    assert data["closure_status"] == "closed"
    assert data["phase_report_ids"]["execution_authorization"] == "report-auth"
    assert report["replay_claims"] is False
    assert report["candidate_effect_claims"] is False
    assert report["summary"]["closed_count"] == 1
    assert report["summary"]["unowned_counts"]["candidates_without_authorization"] == 0
    assert report["summary"]["owned_counts"] == {"replay_authorized_operations": 3}
    assert report["rows"][0]["closed"] is True
    assert report["rows"][0]["surface"] == "ownership_closure_coverage"
    assert report["rows"][0]["row_id"] == "closure-fi-demo"
    assert report["rows"][0]["subject_id"] == "fi-demo-slice"
    assert report["rows"][0]["row_status"] == "closed"
    assert (
        "ownership_closure_coverage_as_full_corpus_omniscience"
        in report["rows"][0]["forbidden_shortcuts"]
    )


def test_ownership_closure_coverage_rejects_false_closed_claims() -> None:
    with pytest.raises(ValueError, match="all unowned_counts to be zero"):
        OwnershipClosureCoverage(
            certificate_id="closure-fi-open",
            corpus_slice_id="fi-demo-slice",
            source_bundle_hash="sha256:source",
            profile_id="strict",
            interpretation_policy_id="fi-policy",
            graph_snapshot_hash="sha256:graph",
            phase_report_ids={"potential_operation_coverage": "report-potentials"},
            closed=True,
            unowned_counts={"potential_ops_without_status": 1},
        )

    with pytest.raises(ValueError, match="requires no failed_gates"):
        OwnershipClosureCoverage(
            certificate_id="closure-fi-open",
            corpus_slice_id="fi-demo-slice",
            source_bundle_hash="sha256:source",
            profile_id="strict",
            interpretation_policy_id="fi-policy",
            graph_snapshot_hash="sha256:graph",
            phase_report_ids={"potential_operation_coverage": "report-potentials"},
            closed=True,
            failed_gates=("potential_operation_coverage",),
        )

    with pytest.raises(ValueError, match="detail.closure_dimensions"):
        OwnershipClosureCoverage(
            certificate_id="closure-fi-ambiguous",
            corpus_slice_id="fi-demo-slice",
            source_bundle_hash="sha256:source",
            profile_id="strict",
            interpretation_policy_id="fi-policy",
            graph_snapshot_hash="sha256:graph",
            phase_report_ids={"potential_operation_coverage": "report-potentials"},
            closed=True,
            unowned_counts={"potential_ops_without_status": 0},
        )


def test_ownership_closure_report_summarizes_open_slices_without_replay_claims() -> None:
    certificate = OwnershipClosureCoverage(
        certificate_id="closure-fi-open",
        corpus_slice_id="fi-demo-slice",
        source_bundle_hash="sha256:source",
        profile_id="strict",
        interpretation_policy_id="fi-policy",
        graph_snapshot_hash="sha256:graph",
        phase_report_ids={"potential_operation_coverage": "report-potentials"},
        closed=False,
        failed_gates=("potential_operation_coverage",),
        unowned_counts={"potential_ops_without_status": 2},
        owned_counts={"failed_ops_visible": 1},
    )

    report = ownership_closure_evidence_report(certificate, jurisdiction="fi").to_dict()

    assert report["truth_claim"] == "bounded ownership accounting closure"
    assert report["replay_claims"] is False
    assert report["summary"]["open_count"] == 1
    assert report["summary"]["failed_gate_counts"] == {"potential_operation_coverage": 1}
    assert report["summary"]["unowned_counts"] == {"potential_ops_without_status": 2}
    assert report["summary"]["owned_counts"] == {"failed_ops_visible": 1}
    assert report["rows"][0]["closure_status"] == "open"
    assert report["rows"][0]["row_status"] == "open"
    assert "open_ownership_closure_as_compile_failure" in report["forbidden_shortcuts"]

    proof_surface = proof_surface_from_evidence_report(report).to_dict()

    assert proof_surface["rows"][0]["row_id"] == "closure-fi-open"
    assert proof_surface["rows"][0]["subject_id"] == "fi-demo-slice"
    assert proof_surface["rows"][0]["row_kind"] == "ownership_closure_coverage"
    assert proof_surface["rows"][0]["proof_status"] == "open"


def test_source_pathology_projection_is_passive_proof_surface_row() -> None:
    pathology = SourcePathology.from_scope(
        code="DESTRUCTIVE_SHAPE_LOSS_RISK",
        message="source payload would risk destructive shape loss",
        source_statute="2001/748",
        target_unit_kind="section",
        target_label="6 §",
        detail={"diagnostic_reason": "partial_body_only"},
    )

    projection = source_pathology_projection(
        pathology,
        jurisdiction="fi",
        affected_phase="payload_elaboration",
        suggested_lane="source_pathology",
        blocks_execution=True,
    )
    report = source_pathology_evidence_report(
        projection,
        jurisdiction="fi",
        report_kind="finland_source_pathology",
    )
    report_data = report.to_dict()
    proof_surface = proof_surface_from_evidence_report(report).to_dict()

    assert isinstance(projection, SourcePathologyProjection)
    assert projection.pathology_kind == "DESTRUCTIVE_SHAPE_LOSS_RISK"
    assert projection.blocks_execution is True
    assert report_data["replay_claims"] is False
    assert report_data["summary"]["source_pathology_count"] == 1
    assert report_data["summary"]["blocking_source_pathology_count"] == 1
    assert report_data["summary"]["pathology_kind_counts"] == {
        "DESTRUCTIVE_SHAPE_LOSS_RISK": 1
    }
    row = report_data["rows"][0]
    assert row["surface"] == "source_pathology"
    assert row["replay_authorized"] is False
    assert row["source_artifact_id"] == "2001/748"
    assert "source_pathology_as_replay_authorization" in row["forbidden_shortcuts"]
    assert proof_surface["surface_kind"] == "finland_source_pathology"
    assert proof_surface["rows"][0]["row_kind"] == "source_pathology"


def test_source_completeness_status_is_passive_authorization_row() -> None:
    status = SourceCompletenessStatus(
        jurisdiction="fi",
        statute_id="2001/1234",
        chain_length=4,
        source_available=3,
        dates_available=4,
    )

    data = status.to_dict()

    assert data["row_status"] == "incomplete"
    assert data["row_id"] == "fi:2001/1234:source-completeness"
    assert data["subject_id"] == "2001/1234"
    assert data["counts"]["missing_sources"] == 1
    assert data["counts"]["missing_dates"] == 0
    assert data["executable"] is False
    assert data["replay_authorized"] is False
    assert data["authorization_ref"] == (
        "fi:2001/1234:source-completeness:source-chain-completeness"
    )
    assert data["execution_authorization"]["replay_authorized"] is False
    assert data["execution_authorization"]["strict_disposition"] == "block"
    assert (
        "source_completeness_status_as_replay_authorization"
        in data["forbidden_shortcuts"]
    )


def test_source_completeness_report_projects_proof_surface_rows() -> None:
    status = source_completeness_status_from_mapping(
        {
            "statute_id": "2001/1234",
            "source_completeness": {
                "chain_length": 2,
                "source_available": 2,
                "dates_available": 2,
            },
        },
        jurisdiction="fi",
    )
    assert status is not None

    report = source_completeness_evidence_report(status, jurisdiction="fi")
    report_data = report.to_dict()
    proof_surface = proof_surface_from_evidence_report(report).to_dict()

    assert report_data["report_kind"] == "source_completeness_status"
    assert report_data["replay_claims"] is False
    assert report_data["summary"]["status_counts"] == {"complete": 1}
    assert report_data["rows"][0]["surface"] == "source_completeness_status"
    assert report_data["rows"][0]["replay_authorized"] is False
    assert proof_surface["surface_kind"] == "source_completeness_status"
    assert proof_surface["rows"][0]["row_kind"] == "source_completeness_status"
    assert proof_surface["rows"][0]["subject_id"] == "2001/1234"
    assert proof_surface["rows"][0]["authorization_ref"] == (
        "fi:2001/1234:source-completeness:source-chain-completeness"
    )


def test_current_text_verification_matrix_marks_email_safe_candidate() -> None:
    matrix = CurrentTextVerificationMatrix(
        current_body_text_contains_target_phrase="yes",
        current_status_page_check="yes",
        source_explicitly_omits_or_repeals_same_text="yes",
        commencement_in_force="not_applicable",
        same_territorial_extent="yes",
        no_later_reinsertion_revival_or_replacement_found="yes",
        target_phrase_in_operative_text_not_commentary="yes",
    )

    data = matrix.to_dict()

    assert matrix.is_email_safe is True
    assert data["blocking_gate_names"] == []
    assert data["commencement_in_force"] == "not_applicable"


def test_current_text_verification_matrix_blocks_public_html_gap() -> None:
    matrix = current_text_verification_matrix_from_mapping(
        {
            "current_body_text_contains_target_phrase": "yes",
            "current_status_page_check": "requires_public_html_review",
            "source_explicitly_omits_or_repeals_same_text": "yes",
            "commencement_in_force": "yes",
            "same_territorial_extent": "yes",
            "no_later_reinsertion_revival_or_replacement_found": "yes",
            "target_phrase_in_operative_text_not_commentary": "yes",
        }
    )

    assert matrix.is_email_safe is False
    assert matrix.blocking_gate_names == ("current_status_page_check",)


def test_current_text_verification_matrix_normalizes_not_applicable_alias() -> None:
    matrix = current_text_verification_matrix_from_mapping(
        {
            "current_body_text_contains_target_phrase": "yes",
            "current_status_page_check": "yes",
            "source_explicitly_omits_or_repeals_same_text": "yes",
            "commencement_in_force": "n/a",
            "same_territorial_extent": "yes",
            "no_later_reinsertion_revival_or_replacement_found": "yes",
            "target_phrase_in_operative_text_not_commentary": "yes",
        }
    )

    assert matrix.commencement_in_force == "not_applicable"
    assert matrix.is_email_safe is True


def test_current_text_verification_matrix_rejects_unknown_gate_status() -> None:
    with pytest.raises(ValueError, match="same_territorial_extent"):
        CurrentTextVerificationMatrix(
            current_body_text_contains_target_phrase="yes",
            current_status_page_check="yes",
            source_explicitly_omits_or_repeals_same_text="yes",
            commencement_in_force="yes",
            same_territorial_extent=cast(Any, "probably"),
            no_later_reinsertion_revival_or_replacement_found="yes",
            target_phrase_in_operative_text_not_commentary="yes",
        )


def test_kernel_authorization_projection_does_not_promote_policy_success_by_default() -> None:
    result = AuthorizationResult(
        subject=ArtifactRef(
            artifact_type="assertion",
            artifact_id="assertion-1",
            content_hash="abc123",
        ),
        policy_id="fi.demo.policy",
        profile_name="fi_strict",
        authorized=True,
        satisfied_clauses=("exists:span_verified",),
        unsatisfied_clauses=(),
        forbidden_present=(),
        evidence_bundle_hash="a" * 64,
    )

    authorization = execution_authorization_from_kernel_result(
        result,
        executable=True,
        owner_phase="semantic_compilation",
    )
    data = authorization.to_dict()

    assert data["replay_authorized"] is False
    assert data["authorization_status"] == "evidence_policy_satisfied_replay_gate_required"
    assert data["required_proofs"] == ["phase_local_replay_authorization"]
    assert (
        "treat_evidence_policy_satisfaction_as_replay_authority"
        in data["forbidden_shortcuts"]
    )
    assert data["detail"]["evidence_kernel"]["evidence_bundle_hash"] == "a" * 64

    report = execution_authorization_evidence_report(authorization, jurisdiction="fi")
    report_data = report.to_dict()
    proof_surface = proof_surface_from_evidence_report(report).to_dict()

    assert report_data["report_kind"] == "execution_authorization"
    assert report_data["replay_claims"] is False
    assert report_data["summary"]["authorization_count"] == 1
    assert report_data["summary"]["replay_authorized_count"] == 0
    assert report_data["summary"]["strict_blocked_count"] == 1
    assert report_data["summary"]["strict_disposition_counts"] == {"block": 1}
    assert report_data["summary"]["quirks_disposition_counts"] == {"record": 1}
    assert report_data["summary"]["validator_status_counts"] == {}
    assert report_data["summary"]["required_proof_counts"] == {
        "phase_local_replay_authorization": 1
    }
    assert report_data["summary"]["claim_flags"]["replay_claims"] is False
    assert report_data["rows"][0]["surface"] == "execution_authorization"
    assert report_data["rows"][0]["subject_id"] == "assertion-1"
    assert report_data["rows"][0]["replay_authorized"] is False
    assert report_data["rows"][0]["required_proofs"] == [
        "phase_local_replay_authorization"
    ]
    assert (
        "evidence_policy_result_as_replay_authority_without_phase_gate"
        in report_data["rows"][0]["forbidden_shortcuts"]
    )
    assert proof_surface["surface_kind"] == "execution_authorization"
    assert proof_surface["rows"][0]["row_kind"] == "execution_authorization"
    assert proof_surface["rows"][0]["authorization_ref"] == "fi.demo.policy"


def test_kernel_authorization_projection_blocks_unsatisfied_policy_clauses() -> None:
    result = AuthorizationResult(
        subject=ArtifactRef(
            artifact_type="assertion",
            artifact_id="assertion-2",
            content_hash="def456",
        ),
        policy_id="fi.demo.policy",
        profile_name="fi_strict",
        authorized=False,
        satisfied_clauses=(),
        unsatisfied_clauses=("exists:span_verified",),
        forbidden_present=("none:retracted",),
        evidence_bundle_hash="b" * 64,
    )

    authorization = execution_authorization_from_kernel_result(
        result,
        executable=True,
        owner_phase="semantic_compilation",
    )
    data = authorization.to_dict()

    assert data["replay_authorized"] is False
    assert data["authorization_status"] == "evidence_policy_unsatisfied"
    assert data["strict_disposition"] == "block"
    assert data["required_proofs"] == [
        "evidence_policy_clause:exists:span_verified",
        "forbidden_evidence_absence:none:retracted",
    ]


def test_kernel_authorization_projection_requires_explicit_replay_gate() -> None:
    result = AuthorizationResult(
        subject=ArtifactRef(
            artifact_type="assertion",
            artifact_id="assertion-3",
            content_hash="ghi789",
        ),
        policy_id="fi.demo.policy",
        profile_name="fi_strict",
        authorized=True,
        satisfied_clauses=("exists:span_verified",),
        unsatisfied_clauses=(),
        forbidden_present=(),
        evidence_bundle_hash="c" * 64,
    )

    authorization = execution_authorization_from_kernel_result(
        result,
        executable=True,
        owner_phase="semantic_compilation",
        replay_authorized_when_policy_satisfied=True,
    )
    data = authorization.to_dict()

    assert data["replay_authorized"] is True
    assert data["authorization_status"] == "replay_authorized"
    assert data["required_proofs"] == []
    assert data["strict_disposition"] == "record"

    report = execution_authorization_evidence_report(authorization, jurisdiction="fi")
    report_data = report.to_dict()

    assert report_data["replay_claims"] is True
    assert report_data["summary"]["authorization_count"] == 1
    assert report_data["summary"]["replay_authorized_count"] == 1
    assert report_data["summary"]["strict_blocked_count"] == 0
    assert report_data["summary"]["strict_disposition_counts"] == {"record": 1}
    assert report_data["summary"]["required_proof_counts"] == {}
    assert report_data["summary"]["claim_flags"]["replay_claims"] is True
    assert report_data["rows"][0]["replay_authorized"] is True


def test_execution_authorization_report_validates_mapping_rows() -> None:
    with pytest.raises(ValueError, match="safe_default is required"):
        execution_authorization_evidence_report(
            {
                "executable": False,
                "replay_authorized": False,
                "authorization_status": "blocked",
                "authorization_rule_id": "bad_rule",
                "owner_phase": "typed_elaboration",
                "strict_disposition": "block",
                "quirks_disposition": "record",
                "required_proofs": ("phase_local_replay_authorization",),
            },
            jurisdiction="fi",
        )


def test_execution_authorization_report_preserves_distinct_same_rule_rows() -> None:
    base = {
        "executable": False,
        "replay_authorized": False,
        "authorization_status": "candidate_set_incomplete_not_replay_authority",
        "authorization_rule_id": "fi_strict_report_candidate_set_operation_cue_coverage",
        "owner_phase": "operation_cue_detection",
        "strict_disposition": "block",
        "quirks_disposition": "record",
        "required_proofs": ("operation_cue_classification_report",),
        "safe_default": "do_not_treat_candidate_set_coverage_as_replay_authorization",
    }
    report = execution_authorization_evidence_report(
        (
            {
                **base,
                "subject_id": "fi:demo:operation-cue-coverage:a",
            },
            {
                **base,
                "subject_id": "fi:demo:operation-cue-coverage:b",
            },
        ),
        jurisdiction="fi",
    )
    data = report.to_dict()
    proof_surface = proof_surface_from_evidence_report(report).to_dict()
    row_ids = [row["row_id"] for row in data["rows"]]

    assert [row["subject_id"] for row in data["rows"]] == [
        "fi:demo:operation-cue-coverage:a",
        "fi:demo:operation-cue-coverage:b",
    ]
    assert data["summary"]["strict_blocked_count"] == 2
    assert data["summary"]["strict_disposition_counts"] == {"block": 2}
    assert data["summary"]["required_proof_counts"] == {
        "operation_cue_classification_report": 2
    }
    assert len(set(row_ids)) == 2
    assert [row["row_id"] for row in proof_surface["rows"]] == row_ids


def test_source_bundle_policy_admission_does_not_authorize_replay() -> None:
    witness = SourceWitness(
        source_role="official_source_xml",
        artifact_id="2024/1",
        source_lane="official_xml",
    )
    assertion = SourceAcquisitionAssertion(
        assertion_id="assertion-1",
        jurisdiction="fi",
        artifact_id="2024/1",
        source_lane="official_xml",
        assertion_kind="source_artifact_available",
        acquisition_status="observed",
        witness=witness,
    )
    attestation = SourceAcquisitionAttestation(
        attestation_id="attestation-1",
        assertion_id="assertion-1",
        attestation_kind="artifact_digest_verified",
        producer_id="lawvm.fetcher",
        attestation_status="verified",
        witness=witness,
    )
    policy = SourceBundlePolicy(
        policy_id="fi.source_bundle.v1",
        jurisdiction="fi",
        admitted_source_lanes=("official_xml", "official_pdf"),
        required_attestation_kinds=("artifact_digest_verified",),
    )

    admission = policy.evaluate(assertion, attestations=(attestation,))
    authorization = admission.to_execution_authorization().to_dict()

    assert admission.admitted is True
    assert admission.admission_status == "source_bundle_admitted"
    assert authorization["executable"] is False
    assert authorization["replay_authorized"] is False
    assert authorization["authorization_status"] == "source_bundle_admitted_not_replay_authority"
    assert (
        "source_bundle_admission_as_replay_authorization"
        in authorization["forbidden_shortcuts"]
    )


def test_source_bundle_policy_blocks_missing_attestations() -> None:
    assertion = SourceAcquisitionAssertion(
        assertion_id="assertion-2",
        jurisdiction="fi",
        artifact_id="1917/1",
        source_lane="official_pdf",
        assertion_kind="pdf_source_atom_available",
        acquisition_status="observed",
    )
    policy = SourceBundlePolicy(
        policy_id="fi.source_bundle.v1",
        jurisdiction="fi",
        admitted_source_lanes=("official_xml", "official_pdf"),
        required_attestation_kinds=("artifact_digest_verified", "ocr_reviewed"),
    )

    admission = policy.evaluate(assertion)
    authorization = admission.to_execution_authorization().to_dict()

    assert admission.admitted is False
    assert admission.admission_status == "source_attestation_missing"
    assert admission.missing_attestation_kinds == (
        "artifact_digest_verified",
        "ocr_reviewed",
    )
    assert authorization["strict_disposition"] == "block"
    assert authorization["authorization_status"] == "source_bundle_policy_unsatisfied"


def test_source_bundle_evidence_report_projects_passive_admissions() -> None:
    witness = SourceWitness(
        source_role="official_source_xml",
        artifact_id="2024/2",
        source_lane="official_xml",
    )
    assertion = SourceAcquisitionAssertion(
        assertion_id="assertion-3",
        jurisdiction="fi",
        artifact_id="2024/2",
        source_lane="official_xml",
        assertion_kind="source_artifact_available",
        acquisition_status="observed",
        witness=witness,
    )
    attestation = SourceAcquisitionAttestation(
        attestation_id="attestation-3",
        assertion_id="assertion-3",
        attestation_kind="artifact_digest_verified",
        producer_id="lawvm-test",
        attestation_status="verified",
        witness=witness,
    )
    policy = SourceBundlePolicy(
        policy_id="fi.source_bundle.v1",
        jurisdiction="fi",
        admitted_source_lanes=("official_xml",),
    )
    admission = policy.evaluate(assertion, attestations=(attestation,))

    report = source_bundle_evidence_report(
        (admission,),
        jurisdiction="fi",
        assertions=(assertion,),
        attestations=(attestation,),
    ).to_dict()

    assert report["report_kind"] == "source_bundle_admission"
    assert report["replay_claims"] is False
    assert report["summary"]["assertion_count"] == 1
    assert report["summary"]["attestation_count"] == 1
    assert report["summary"]["admitted_count"] == 1
    assert report["summary"]["status_counts"] == {"source_bundle_admitted": 1}
    assert {row["surface"] for row in report["rows"]} == {
        "source_acquisition_assertion",
        "source_acquisition_attestation",
        "source_bundle_admission",
    }
    assertion_row = next(row for row in report["rows"] if row["surface"] == "source_acquisition_assertion")
    assert assertion_row["row_id"] == "assertion-3"
    assert assertion_row["subject_id"] == "2024/2"
    assert assertion_row["assertion_ref"] == "assertion-3"
    attestation_row = next(row for row in report["rows"] if row["surface"] == "source_acquisition_attestation")
    assert attestation_row["row_id"] == "attestation-3"
    assert attestation_row["subject_id"] == "assertion-3"
    assert attestation_row["assertion_ref"] == "assertion-3"
    admission_row = next(row for row in report["rows"] if row["surface"] == "source_bundle_admission")
    assert admission_row["row_id"] == "fi.source_bundle.v1:assertion-3"
    assert admission_row["subject_id"] == "assertion-3"
    assert admission_row["assertion_ref"] == "assertion-3"
    assert admission_row["authorization_ref"] == "fi.source_bundle.v1:assertion-3"
    assert admission_row["proof_ref"] == "fi.source_bundle.v1"
    assert admission_row["execution_authorization"]["executable"] is False
    assert admission_row["execution_authorization"]["replay_authorized"] is False
    assert (
        "source_bundle_admission_as_replay_authorization"
        in admission_row["execution_authorization"]["forbidden_shortcuts"]
    )

    surface = proof_surface_from_evidence_report(report).to_dict()
    rows_by_id = {row["row_id"]: row for row in surface["rows"]}
    assert rows_by_id["assertion-3"]["source_refs"] == ["2024/2"]
    assert rows_by_id["attestation-3"]["assertion_refs"] == ["assertion-3"]
    assert rows_by_id["fi.source_bundle.v1:assertion-3"]["authorization_ref"] == (
        "fi.source_bundle.v1:assertion-3"
    )


def test_frontend_phase_surface_marks_compatibility_output_without_replay_claims() -> None:
    diagnostic = FrontendDiagnostic(
        diagnostic_id="fi-demo-residual",
        jurisdiction="fi",
        frontend="finland.demo",
        phase="residual_collection",
        severity="warning",
        rule_id="fi.demo.residual.v1",
        message="demo residual",
        forbidden_shortcuts=("drop_residual",),
    )
    surface = FrontendPhaseSurface(
        jurisdiction="fi",
        frontend="finland.demo",
        schema="lawvm.frontend_phase_surface.v1",
        truth_claim="ClauseAST is primary; ParsedOps are compatibility output.",
        source_hash="abc123",
        source_length=12,
        authority_path=("source_text", "SurfaceClause", "ClauseAST"),
        compatibility_outputs=("ParsedOp",),
        phase_rows=(
            FrontendPhaseRow(
                phase="clause_ast_lowering",
                phase_status="lowered",
                artifact_kind="ClauseAST",
                authority_role="primary_semantic_authority",
                produced=True,
                output_artifacts=("clause_ast",),
            ),
            FrontendPhaseRow(
                phase="parsed_ops_compat",
                phase_status="derived",
                artifact_kind="ParsedOp",
                authority_role="compatibility_projection_not_authority",
                produced=True,
                input_artifacts=("clause_ast",),
                output_artifacts=("parsed_ops",),
            ),
        ),
        diagnostics=(diagnostic,),
    )

    data = surface.to_dict()

    assert data["compatibility_outputs"] == ["ParsedOp"]
    assert data["replay_claims"] is False
    assert data["canonical_effect_claims"] is False
    assert data["phase_rows"][1]["authority_role"] == "compatibility_projection_not_authority"
    assert data["diagnostics"][0]["forbidden_shortcuts"] == ["drop_residual"]


def test_frontend_phase_surface_projects_to_evidence_report_without_authority() -> None:
    surface = FrontendPhaseSurface(
        jurisdiction="fi",
        frontend="finland.johtolause.parse_clause",
        schema="lawvm.frontend_phase_surface.v1",
        truth_claim="ClauseAST authority path diagnostics",
        source_hash="abc123",
        source_length=17,
        authority_path=("source_text", "ClauseAST"),
        compatibility_outputs=("ParsedOp",),
        phase_rows=(
            FrontendPhaseRow(
                phase="clause_ast_lowering",
                phase_status="lowered",
                artifact_kind="ClauseAST",
                authority_role="primary_semantic_authority",
                produced=True,
                output_artifacts=("clause_ast",),
            ),
            FrontendPhaseRow(
                phase="parsed_ops_compat",
                phase_status="derived",
                artifact_kind="ParsedOp",
                authority_role="compatibility_projection_not_authority",
                produced=True,
                input_artifacts=("clause_ast",),
                output_artifacts=("parsed_ops",),
                diagnostic_ids=("diag-1",),
            ),
        ),
        diagnostics=(
            FrontendDiagnostic(
                diagnostic_id="diag-1",
                jurisdiction="fi",
                frontend="finland.johtolause.parse_clause",
                phase="parsed_ops_compat",
                severity="info",
                rule_id="fi.compat.parsed_ops",
                message="ParsedOps are compatibility output.",
            ),
        ),
    )

    report = frontend_phase_surface_evidence_report(surface)
    data = report.to_dict()
    proof_surface = proof_surface_from_evidence_report(report).to_dict()

    assert data["report_kind"] == "frontend_phase_surface"
    assert data["replay_claims"] is False
    assert data["canonical_effect_claims"] is False
    assert data["candidate_effect_claims"] is False
    assert data["dry_run_claims"] is False
    assert data["agreement_claims"] is False
    assert data["summary"]["phase_row_count"] == 2
    assert data["summary"]["diagnostic_count"] == 1
    assert data["summary"]["compatibility_outputs"] == ["ParsedOp"]
    assert [row["surface"] for row in data["rows"]] == [
        "frontend_phase_row",
        "frontend_phase_row",
        "frontend_diagnostic",
    ]
    assert data["rows"][1]["authority_role"] == "compatibility_projection_not_authority"
    assert data["rows"][1]["replay_authorized"] is False
    assert "compatibility_output_as_semantic_authority" in data["forbidden_shortcuts"]
    assert proof_surface["surface_kind"] == "frontend_phase_surface"
    assert proof_surface["rows"][0]["row_kind"] == "frontend_phase_row"
    assert proof_surface["rows"][0]["source_refs"] == ["abc123"]


def test_frontend_capability_declares_supported_waists_without_replay_authority() -> None:
    capability = FrontendCapability(
        frontend_id="fi.demo",
        jurisdiction="fi",
        scope="clause_compiler_spine",
        capability_status="reference_clause_compiler",
        has_token_tape=True,
        has_surface_clause=True,
        has_clause_ast=True,
        compatibility_outputs=("ParsedOp",),
        phase_names=("tokenize", "surface_parse", "clause_ast_lowering"),
        caveats=("capability_declaration_does_not_authorize_replay",),
    )

    data = capability.to_dict()

    assert data["frontend_id"] == "fi.demo"
    assert data["has_token_tape"] is True
    assert data["has_clause_ast"] is True
    assert data["has_replay_apply"] is False
    assert data["has_agreement_surface"] is False
    assert data["compatibility_outputs"] == ["ParsedOp"]
    assert data["caveats"] == ["capability_declaration_does_not_authorize_replay"]

    report = frontend_capability_evidence_report(capability)
    report_data = report.to_dict()
    proof_surface = proof_surface_from_evidence_report(report).to_dict()

    assert report_data["replay_claims"] is False
    assert report_data["canonical_effect_claims"] is False
    assert report_data["summary"]["frontend_capability_count"] == 1
    assert report_data["summary"]["supported_waist_count"] == 3
    assert report_data["rows"][0]["surface"] == "frontend_capability"
    assert report_data["rows"][0]["replay_authorized"] is False
    assert "has_token_tape" in report_data["rows"][0]["supported_waists"]
    assert "frontend_capability_as_replay_authorization" in report_data["forbidden_shortcuts"]
    assert proof_surface["surface_kind"] == "frontend_capability"
    assert proof_surface["rows"][0]["row_kind"] == "frontend_capability"


def test_frontend_capability_matrix_projects_multiple_declarations() -> None:
    finland = FrontendCapability(
        frontend_id="fi.clause",
        jurisdiction="fi",
        scope="clause_compiler_spine",
        capability_status="reference_clause_compiler",
        has_token_tape=True,
        has_surface_clause=True,
        has_clause_ast=True,
        compatibility_outputs=("ParsedOp",),
    )
    diagnostic = FrontendCapability(
        frontend_id="fi.manual_frontier",
        jurisdiction="fi",
        scope="manual_frontier",
        capability_status="diagnostic_frontier_surface",
        has_agreement_surface=True,
        caveats=("capability_declaration_does_not_authorize_replay",),
    )

    report = frontend_capability_matrix_evidence_report(
        (finland, diagnostic),
        jurisdiction="fi",
    )
    data = report.to_dict()
    proof_surface = proof_surface_from_evidence_report(report).to_dict()

    assert data["report_kind"] == "frontend_capability_matrix"
    assert data["replay_claims"] is False
    assert data["summary"]["frontend_capability_count"] == 2
    assert data["summary"]["jurisdiction_counts"] == {"fi": 2}
    assert data["summary"]["status_counts"] == {
        "diagnostic_frontier_surface": 1,
        "reference_clause_compiler": 1,
    }
    assert data["summary"]["supported_waist_counts"]["fi.clause"] == 3
    assert [row["surface"] for row in data["rows"]] == [
        "frontend_capability",
        "frontend_capability",
    ]
    assert data["rows"][0]["replay_authorized"] is False
    assert proof_surface["surface_kind"] == "frontend_capability_matrix"
    assert proof_surface["rows"][0]["row_kind"] == "frontend_capability"


def test_derived_compatibility_artifact_declares_non_authority_boundary() -> None:
    artifact = DerivedCompatibilityArtifact(
        artifact_id="fi:demo:parsed_ops",
        jurisdiction="fi",
        frontend_id="fi.demo",
        artifact_kind="ParsedOp",
        source_artifact_id="fi:demo:clause_ast",
        source_artifact_kind="ClauseAST",
        derivation_phase="parsed_ops_compat",
        phase_status="derived_compatibility_projection",
        lossy=True,
        preserved_fields=("operation_kind",),
        lost_fields=("native_clause_ast_node_identity",),
        input_artifacts=("clause_ast",),
        output_artifacts=("parsed_ops",),
    )

    data = artifact.to_dict()

    assert data["semantic_authority"] is False
    assert data["replay_authorized"] is False
    assert data["lossy"] is True
    assert data["preserved_fields"] == ["operation_kind"]
    assert data["lost_fields"] == ["native_clause_ast_node_identity"]
    assert "compatibility_artifact_as_replay_authorization" in data["forbidden_shortcuts"]


def test_frontend_diagnostics_project_to_governed_findings() -> None:
    diagnostic = FrontendDiagnostic(
        diagnostic_id="diag-1",
        jurisdiction="fi",
        frontend="fi.demo",
        phase="surface_parse",
        severity="warning",
        rule_id="fi.demo.warning",
        message="demo warning",
    )
    blocking = FrontendDiagnostic(
        diagnostic_id="diag-2",
        jurisdiction="fi",
        frontend="fi.demo",
        phase="surface_resolve",
        severity="error",
        rule_id="fi.demo.blocking",
        message="demo blocker",
        blocking=True,
        strict_disposition="block",
    )
    bug = FrontendDiagnostic(
        diagnostic_id="diag-3",
        jurisdiction="fi",
        frontend="fi.demo",
        phase="clause_ast_lowering",
        severity="bug",
        rule_id="fi.demo.bug",
        message="demo bug",
        blocking=True,
        strict_disposition="block",
    )

    findings = frontend_diagnostic_findings((diagnostic, blocking, bug))

    assert [finding.kind for finding in findings] == [
        "PARSE.FRONTEND_DIAGNOSTIC",
        "PARSE.FRONTEND_BLOCKING_DIAGNOSTIC",
        "PARSE.FRONTEND_INTERNAL_ERROR",
    ]
    assert [finding.role for finding in findings] == [
        "observation",
        "obligation",
        "violation",
    ]
    assert findings[0].blocking is False
    assert findings[1].blocking is True
    assert findings[2].blocking is True
    assert findings[0].detail["diagnostic_id"] == "diag-1"


def test_surface_parse_result_records_original_enriched_resolved_waist() -> None:
    result = SurfaceParseResult(
        frontend_id="fi.demo",
        jurisdiction="fi",
        source_hash="abc123",
        parse_status="enriched_resolved",
        original_surface_kind="SurfaceClause",
        original_produced=True,
        enriched_surface_kind="SurfaceClause",
        enriched=True,
        resolved_surface_kind="ResolvedSurfaceClause",
        resolved_produced=True,
        consumed_count=4,
        enrichment_rule_ids=("fi.demo.enrichment.v1",),
        supplementary_surface_kinds=("SurfaceMetaClause",),
        diagnostic_ids=("demo-diagnostic",),
    )

    data = result.to_dict()

    assert data["parse_status"] == "enriched_resolved"
    assert data["original_surface_kind"] == "SurfaceClause"
    assert data["enriched"] is True
    assert data["resolved_produced"] is True
    assert data["enrichment_rule_ids"] == ["fi.demo.enrichment.v1"]
    assert data["supplementary_surface_kinds"] == ["SurfaceMetaClause"]


def test_token_tape_projects_source_preserving_lexemes_and_view() -> None:
    tape = TokenTape(
        source_text="muutetaan 5 §",
        lexemes=(
            TokenLexeme("muutetaan", "muuttaa", "VERB", semantic_code="M"),
            TokenLexeme("5", "5", "NUM", char_start=10, char_end=11),
            TokenLexeme("§", "§", "PYKALA", gram_case="NOM", char_start=12, char_end=13),
        ),
    )
    annotation = TokenAnnotation(
        annotation_id="demo-annotation",
        kind="demo_span",
        start=1,
        end=3,
        sentinel_kind="DEMO_SPAN",
    )
    view = AnnotatedTokenView(tape=tape, annotations=(annotation,), visible_indices=(0, 1))

    tape_data = tape.to_dict()
    view_data = view.to_dict()

    assert tape_data["tape_schema"] == "lawvm.token_tape.v1"
    assert tape_data["lexeme_count"] == 3
    assert tape_data["lexemes"][0]["semantic_code"] == "M"
    assert view_data["source_hash"] == tape.source_hash
    assert view_data["visible_count"] == 2
    assert view_data["structural_count"] == 2
    assert view_data["visible_indices"] == [0, 1]
    assert view_data["structural_view_to_raw"] == [[0, 1], [1, 2]]
    assert view_data["annotations"][0]["sentinel_kind"] == "DEMO_SPAN"
    structural, view_to_raw = view.structural_view_with_map()
    assert [lexeme.text for lexeme in structural] == ["muutetaan", "5"]
    assert view_to_raw == ((0, 1), (1, 2))


def test_annotated_token_view_builds_structural_view_without_mutating_tape() -> None:
    tape = TokenTape(
        source_text="a b c d",
        lexemes=(
            TokenLexeme("a", "a", "WORD"),
            TokenLexeme("b", "b", "WORD"),
            TokenLexeme("c", "c", "WORD"),
            TokenLexeme("d", "d", "WORD"),
        ),
    )
    view = AnnotatedTokenView(
        tape=tape,
        annotations=(
            TokenAnnotation("ann-1", "citation", 1, 3, sentinel_kind="CITATION_SPAN"),
        ),
    )

    structural, view_to_raw = view.structural_view_with_map()

    assert [lexeme.text for lexeme in structural] == ["a", "CITATION_SPAN", "d"]
    assert view_to_raw == ((0, 1), (1, 3), (3, 4))
    assert view.to_dict()["visible_count"] == 0
    assert view.to_dict()["structural_count"] == 3
    assert view.to_dict()["structural_view_to_raw"] == [[0, 1], [1, 3], [3, 4]]
    assert [lexeme.text for lexeme in tape.lexemes] == ["a", "b", "c", "d"]
    assert structural[1].detail["source_preserving_sentinel"] is True
    assert structural[1].detail["annotation_id"] == "ann-1"


def test_annotated_token_view_prefers_outer_annotation_for_overlaps() -> None:
    tape = TokenTape(
        source_text="a b c d",
        lexemes=(
            TokenLexeme("a", "a", "WORD"),
            TokenLexeme("b", "b", "WORD"),
            TokenLexeme("c", "c", "WORD"),
            TokenLexeme("d", "d", "WORD"),
        ),
    )
    view = AnnotatedTokenView(
        tape=tape,
        annotations=(
            TokenAnnotation("inner", "inner", 1, 2, sentinel_kind="INNER"),
            TokenAnnotation("outer", "outer", 0, 3, sentinel_kind="OUTER"),
        ),
    )

    structural, view_to_raw = view.structural_view_with_map()

    assert [lexeme.text for lexeme in structural] == ["OUTER", "d"]
    assert view_to_raw == ((0, 3), (3, 4))


def test_annotated_token_view_rejects_annotation_outside_tape() -> None:
    tape = TokenTape(
        source_text="a",
        lexemes=(TokenLexeme("a", "a", "WORD"),),
    )
    view = AnnotatedTokenView(
        tape=tape,
        annotations=(TokenAnnotation("bad", "bad", 0, 2, sentinel_kind="BAD"),),
    )

    with pytest.raises(ValueError, match="TokenAnnotation.end"):
        view.structural_view_with_map()


def test_payload_elaboration_result_is_projection_only_not_replay_authority() -> None:
    slot_report = SlotBindingReport(
        subject_id="fi:demo",
        jurisdiction="fi",
        owner_phase="payload_elaboration",
        binding_status="complete",
        completeness_kind="complete",
        bindings=(
            SlotBinding(
                binding_id="fi:demo:slot:0",
                source_slot_id="payload:1",
                target_slot_id="subsection:1",
                binding_status="bound",
            ),
        ),
    )
    result = PayloadElaborationResult(
        result_id="fi:demo:payload",
        jurisdiction="fi",
        owner_phase="payload_elaboration",
        elaboration_status="elaborated",
        payload_surface_kind="IRNode",
        completeness_kind="complete",
        elaborated_op_count=1,
        payload_completeness=PayloadCompletenessWitness(
            kind="complete",
            reasons=("no_sparse_or_fragmentary_signals",),
            tail_policy="replace_if_target_scope_requires",
        ),
        slot_binding_report=slot_report,
    )

    data = result.to_dict()

    assert data["replay_authorized"] is False
    assert data["authorization_status"] == "projection_only_not_replay_authority"
    assert data["payload_completeness"]["kind"] == "complete"
    assert data["slot_binding_report"]["binding_count"] == 1
    assert "treat_payload_projection_as_replay_authorization" in data["forbidden_shortcuts"]


def test_payload_elaboration_projection_has_shared_report_read_model() -> None:
    slot_report = SlotBindingReport(
        subject_id="fi:demo",
        jurisdiction="fi",
        owner_phase="payload_elaboration",
        binding_status="complete",
        completeness_kind="complete",
        bindings=(
            SlotBinding(
                binding_id="fi:demo:slot:0",
                source_slot_id="payload:1",
                target_slot_id="subsection:1",
                binding_status="bound",
                operation_id="REPLACE P 5 1",
                binding_rule_id="REPLACE",
            ),
        ),
    )
    result = PayloadElaborationResult(
        result_id="fi:demo:payload",
        jurisdiction="fi",
        owner_phase="payload_elaboration",
        elaboration_status="elaborated",
        payload_surface_kind="IRNode",
        completeness_kind="complete",
        elaborated_op_count=1,
        payload_completeness=PayloadCompletenessWitness(
            kind="complete",
            reasons=("no_sparse_or_fragmentary_signals",),
            tail_policy="replace_if_target_scope_requires",
        ),
        slot_binding_report=slot_report,
    )

    report = payload_elaboration_evidence_report(result, report_kind="finland_payload_elaboration")
    data = report.to_dict()
    proof_surface = proof_surface_from_evidence_report(report).to_dict()

    assert data["report_kind"] == "finland_payload_elaboration"
    assert data["replay_claims"] is False
    assert data["canonical_effect_claims"] is False
    assert data["candidate_effect_claims"] is False
    assert data["dry_run_claims"] is False
    assert data["agreement_claims"] is False
    assert data["summary"]["payload_completeness_witness_count"] == 1
    assert data["summary"]["slot_binding_count"] == 1
    rows = {(row["surface"], row["row_id"]): row for row in data["rows"]}
    payload_row = rows[("payload_elaboration_result", "fi:demo:payload")]
    completeness_row = rows[("payload_completeness_witness", "fi:demo:payload:payload_completeness")]
    binding_row = rows[("slot_binding", "fi:demo:slot:0")]
    assert payload_row["replay_authorized"] is False
    assert completeness_row["completeness_kind"] == "complete"
    assert completeness_row["tail_policy"] == "replace_if_target_scope_requires"
    assert completeness_row["replay_authorized"] is False
    assert binding_row["source_slot_id"] == "payload:1"
    assert binding_row["target_slot_id"] == "subsection:1"
    assert "payload_elaboration_report_as_replay_authorization" in data["forbidden_shortcuts"]
    assert proof_surface["surface_kind"] == "finland_payload_elaboration"
    assert {row["row_kind"] for row in proof_surface["rows"]} == {
        "payload_elaboration_result",
        "payload_completeness_witness",
        "slot_binding_report",
        "slot_binding",
    }


def test_execution_authorization_rejects_hidden_promotion() -> None:
    issues = validate_execution_authorization(
        {
            "executable": False,
            "replay_authorized": True,
            "authorization_status": "bad",
            "authorization_rule_id": "bad_rule",
            "owner_phase": "typed_elaboration",
            "strict_disposition": "record",
            "quirks_disposition": "record",
            "required_proofs": (),
            "safe_default": "block",
        }
    )

    assert "replay_authorized requires executable" in issues


def test_execution_authorization_requires_missing_proofs_for_frontier_rows() -> None:
    with pytest.raises(ValueError, match="non-authorized row must list required_proofs"):
        ExecutionAuthorization(
            executable=False,
            replay_authorized=False,
            authorization_status="manual_claim_required",
            authorization_rule_id="test_manual_rule",
            owner_phase="typed_elaboration",
            strict_disposition="record",
            required_proofs=(),
            safe_default="block_until_claim",
        )


def test_frontier_work_item_requires_non_executable_work() -> None:
    item = FrontierWorkItem(
        work_item_id="uk-manual-frontier-demo",
        jurisdiction="uk",
        source_artifact_id="ukpga/2020/1",
        source_unit_id="eff-1",
        source_witness={"source_role": "affecting_source"},
        target_witness={
            "surface": "effect_feed_affected_provisions",
            "affected_provisions": "s. 1",
        },
        compare_witness={
            "surface": "replay_vs_current_oracle_target_presence",
            "compare_shape": "commensurable",
        },
        owner_phase="typed_elaboration",
        frontier_family="uk_manual_frontier_heading_facet_candidate",
        frontier_status="manual_compile_candidate",
        candidate_operation_family="facet_text_rewrite",
        candidate_targets=("section-1",),
        required_claim_kind="semantic_compile",
        required_validator_checks=("claim_identifies_heading_facet",),
        required_proofs=("mutation_boundary_proof",),
        safe_default="block_until_validated_claim_authorizes_replay",
        forbidden_shortcuts=("unvalidated_manual_claim_execution",),
        authorization_status="manual_claim_required",
    )

    data = item.to_dict()

    assert data["executable"] is False
    assert data["replay_authorized"] is False
    assert data["target_witness"]["affected_provisions"] == "s. 1"
    assert data["compare_witness"]["compare_shape"] == "commensurable"
    assert validate_frontier_work_item(data) == ()


def test_frontier_work_item_rejects_replay_promotion() -> None:
    issues = validate_frontier_work_item(
        {
            "work_item_id": "bad",
            "jurisdiction": "uk",
            "source_artifact_id": "source",
            "source_unit_id": "unit",
            "source_witness": {},
            "owner_phase": "typed_elaboration",
            "frontier_family": "family",
            "frontier_status": "status",
            "required_claim_kind": "claim",
            "required_validator_checks": [],
            "required_proofs": ["proof"],
            "safe_default": "block",
            "forbidden_shortcuts": ["shortcut"],
            "executable": True,
            "replay_authorized": True,
            "authorization_status": "bad",
            "detail": {},
        }
    )

    assert "frontier work items must be non-executable" in issues
    assert "frontier work items must not be replay-authorized" in issues


def test_frontier_work_item_evidence_report_is_passive_shared_surface() -> None:
    item = FrontierWorkItem(
        work_item_id="fi-frontier-demo",
        jurisdiction="fi",
        source_artifact_id="2020/1",
        source_unit_id="section:2",
        source_witness={
            "source_role": "finlex_xml",
            "artifact_id": "2020/1",
            "locator": "finlex://2020/1",
        },
        target_witness={"candidate_targets": ["section:2"]},
        compare_witness={"compare_shape": "source_only"},
        owner_phase="typed_elaboration",
        frontier_family="fi_sparse_item_body_missing",
        frontier_status="manual_claim_needed",
        candidate_operation_family="sparse_item_payload_resolution",
        candidate_targets=("section:2",),
        required_claim_kind="fi.v1.SPARSE_SLOT_PAYLOAD_RESOLUTION",
        required_validator_checks=("validate_sparse_slot_payload_claim",),
        required_proofs=("payload_identity_proof", "mutation_boundary_proof"),
        safe_default="block_until_validated_claim_authorizes_replay",
        forbidden_shortcuts=("manual_claim_as_replay_authorization",),
        authorization_status="blocked_manual_claim_required",
    )

    report = frontier_work_item_evidence_report(item).to_dict()

    assert report["jurisdiction"] == "fi"
    assert report["schema"] == "lawvm.frontier_work_item_report.v1"
    assert report["replay_claims"] is False
    assert report["canonical_effect_claims"] is False
    assert report["candidate_effect_claims"] is False
    assert report["dry_run_claims"] is False
    assert report["agreement_claims"] is False
    assert report["summary"]["frontier_work_item_count"] == 1
    assert report["summary"]["frontier_family_counts"] == {
        "fi_sparse_item_body_missing": 1
    }
    assert report["summary"]["required_validator_check_counts"] == {
        "validate_sparse_slot_payload_claim": 1
    }
    assert report["summary"]["suggested_claim_template_status_counts"] == {
        "__none__": 1
    }
    assert report["summary"]["suggested_claim_template_kind_counts"] == {
        "__none__": 1
    }
    row = report["rows"][0]
    assert row["surface"] == "frontier_work_item"
    assert row["row_id"] == "fi-frontier-demo"
    assert row["frontier_ref"] == "fi-frontier-demo"
    assert row["executable"] is False
    assert row["replay_authorized"] is False
    assert "frontier_work_item_as_replay_authorization" in row["forbidden_shortcuts"]


def test_frontier_work_item_claim_template_is_passive_review_scaffold() -> None:
    item = FrontierWorkItem(
        work_item_id="fi-frontier-template-demo",
        jurisdiction="fi",
        source_artifact_id="2020/1",
        source_unit_id="section:2",
        source_witness={"source_role": "finlex_xml", "artifact_id": "2020/1"},
        target_witness={"candidate_targets": ["section:2"]},
        owner_phase="typed_elaboration",
        frontier_family="fi_sparse_item_body_missing",
        frontier_status="manual_claim_needed",
        candidate_operation_family="sparse_item_payload_resolution",
        candidate_targets=("section:2",),
        required_claim_kind="fi.v1.SPARSE_SLOT_PAYLOAD_RESOLUTION",
        required_validator_checks=("validate_sparse_slot_payload_claim",),
        required_proofs=("payload_identity_proof", "mutation_boundary_proof"),
        safe_default="block_until_validated_claim_authorizes_replay",
        forbidden_shortcuts=("manual_claim_as_replay_authorization",),
        authorization_status="blocked_manual_claim_required",
    )

    import lawvm.finland.claim_kinds as fi_claim_kinds

    assert fi_claim_kinds is not None

    template = frontier_work_item_claim_template(item)

    assert template["schema"] == "lawvm.frontier_work_item_claim_template.v1"
    assert template["frontier_ref"] == "fi-frontier-template-demo"
    assert template["claim_target_seed"] == {
        "frontier_ref": "fi-frontier-template-demo",
        "source_statute": "2020/1",
        "affected_target": "section:2",
    }
    assert template["claim_kind"] == "fi.v1.SPARSE_SLOT_PAYLOAD_RESOLUTION"
    assert template["registered_claim_kind"] is True
    assert template["semantic_compilation_claim"] is True
    assert template["required_target_fields"] == [
        "source_statute",
        "affected_target",
        "source_pathology_code",
    ]
    assert template["required_value_fields"] == [
        "source_quote",
        "candidate_slots",
        "selected_slot",
        "old_text_precondition",
        "target_uniqueness_proof_ref",
        "payload_identity_proof_ref",
        "rejected_candidate_accounting_ref",
        "mutation_boundary_proof_ref",
    ]
    assert template["executable"] is False
    assert template["replay_authorized"] is False
    assert "frontier_claim_template_as_replay_authorization" in template["forbidden_shortcuts"]
    assert frontier_work_item_claim_template_status(template) == "available"


def test_frontier_work_item_with_claim_template_attaches_available_template() -> None:
    import lawvm.finland.claim_kinds as fi_claim_kinds

    assert fi_claim_kinds is not None

    item = frontier_work_item_with_claim_template(
        FrontierWorkItem(
            work_item_id="fi-frontier-template-attached",
            jurisdiction="fi",
            source_artifact_id="2020/1",
            source_unit_id="section:2",
            owner_phase="replay_apply",
            frontier_family="fi_failed_operation_resolution",
            frontier_status="failed_operation_frontier",
            required_claim_kind="fi.v1.FAILED_OPERATION_RESOLUTION",
            required_validator_checks=("validate_failed_operation_resolution_claim",),
            required_proofs=("mutation_boundary_proof",),
            safe_default="block_until_validated_claim_authorizes_replay",
            forbidden_shortcuts=("failed_operation_as_replay_authorization",),
            authorization_status="failed_operation_not_replay_authority",
        )
    ).to_dict()

    assert item["suggested_claim_template_status"] == "available"
    assert item["suggested_claim_template"]["claim_kind"] == "fi.v1.FAILED_OPERATION_RESOLUTION"
    assert item["suggested_claim_template"]["claim_target_seed"] == {
        "frontier_ref": "fi-frontier-template-attached",
        "source_statute": "2020/1",
        "affected_target": "section:2",
    }
    assert item["suggested_claim_template"]["executable"] is False
    assert item["suggested_claim_template"]["replay_authorized"] is False

    report = frontier_work_item_evidence_report(item).to_dict()

    assert report["summary"]["suggested_claim_template_status_counts"] == {
        "available": 1
    }
    assert report["summary"]["suggested_claim_template_kind_counts"] == {
        "fi.v1.FAILED_OPERATION_RESOLUTION": 1
    }


def test_frontier_work_item_claim_closure_report_keeps_phase_gate_closed() -> None:
    item = FrontierWorkItem(
        work_item_id="fi-frontier-claim-closure",
        jurisdiction="fi",
        source_artifact_id="2020/1",
        source_unit_id="section:2",
        owner_phase="typed_elaboration",
        frontier_family="fi_sparse_item_body_missing",
        frontier_status="manual_claim_needed",
        required_claim_kind="fi.v1.SPARSE_SLOT_PAYLOAD_RESOLUTION",
        required_validator_checks=("validate_sparse_slot_payload_claim",),
        required_proofs=("mutation_boundary_proof",),
        safe_default="block_until_validated_claim_authorizes_replay",
        forbidden_shortcuts=("manual_claim_as_replay_authorization",),
        authorization_status="blocked_manual_claim_required",
    )
    assertion = {
        "assertion_id": "claim-1",
        "jurisdiction": "fi",
        "kind": "fi.v1.SPARSE_SLOT_PAYLOAD_RESOLUTION",
        "target": {"frontier_ref": "fi-frontier-claim-closure"},
        "scope": {},
        "value": {},
    }
    result = AuthorizationResult(
        subject=ArtifactRef(
            artifact_type="assertion",
            artifact_id="claim-1",
            content_hash="claim-1",
        ),
        policy_id="fi.v1.SPARSE_SLOT_PAYLOAD_RESOLUTION.strict",
        profile_name="fi_strict",
        authorized=True,
        satisfied_clauses=("exists:span_verified", "exists:entailment_verified"),
        unsatisfied_clauses=(),
        forbidden_present=(),
        evidence_bundle_hash="sha256:" + "a" * 64,
    )

    report = frontier_work_item_claim_closure_report(
        item,
        assertion=assertion,
        authorization_result=result,
    )
    data = report.to_dict()
    surface = proof_surface_from_evidence_report(report).to_dict()

    assert data["schema"] == "lawvm.frontier_work_item_claim_closure_report.v1"
    assert data["replay_claims"] is False
    assert data["summary"]["closure_status_counts"] == {
        "evidence_policy_satisfied_phase_gate_required": 1
    }
    assert data["summary"]["phase_gate_required_count"] == 1
    assert data["summary"]["replay_authorized_count"] == 0
    row = data["rows"][0]
    assert row["policy_authorized"] is True
    assert row["claim_kind_matches"] is True
    assert row["frontier_ref_matches"] is True
    assert row["authorization_subject_matches"] is True
    assert row["phase_gate_required"] is True
    assert row["executable"] is False
    assert row["replay_authorized"] is False
    assert row["required_proofs"] == [
        "mutation_boundary_proof",
        "phase_local_replay_authorization",
    ]
    assert "frontier_claim_closure_as_replay_authorization" in row["forbidden_shortcuts"]
    assert surface["rows"][0]["row_kind"] == "frontier_work_item_claim_closure"
    assert surface["rows"][0]["proof_status"] == "evidence_policy_satisfied_phase_gate_required"
    assert surface["rows"][0]["assertion_refs"] == ["claim-1"]
    assert surface["rows"][0]["authorization_ref"] == (
        "fi.v1.SPARSE_SLOT_PAYLOAD_RESOLUTION.strict"
    )
    assert surface["rows"][0]["frontier_ref"] == "fi-frontier-claim-closure"


def test_frontier_work_item_claim_closure_report_consumes_matching_phase_gate() -> None:
    item = FrontierWorkItem(
        work_item_id="fi-frontier-claim-closure",
        jurisdiction="fi",
        source_artifact_id="2020/1",
        source_unit_id="section:2",
        owner_phase="typed_elaboration",
        frontier_family="fi_sparse_item_body_missing",
        frontier_status="manual_claim_needed",
        required_claim_kind="fi.v1.SPARSE_SLOT_PAYLOAD_RESOLUTION",
        required_validator_checks=("validate_sparse_slot_payload_claim",),
        required_proofs=("payload_identity_proof", "mutation_boundary_proof"),
        safe_default="block_until_validated_claim_authorizes_replay",
        forbidden_shortcuts=("manual_claim_as_replay_authorization",),
        authorization_status="blocked_manual_claim_required",
    )
    assertion = {
        "assertion_id": "claim-1",
        "jurisdiction": "fi",
        "kind": "fi.v1.SPARSE_SLOT_PAYLOAD_RESOLUTION",
        "target": {"frontier_ref": "fi-frontier-claim-closure"},
        "scope": {},
        "value": {},
    }
    result = AuthorizationResult(
        subject=ArtifactRef(
            artifact_type="assertion",
            artifact_id="claim-1",
            content_hash="claim-1",
        ),
        policy_id="fi.v1.SPARSE_SLOT_PAYLOAD_RESOLUTION.strict",
        profile_name="fi_strict",
        authorized=True,
        satisfied_clauses=("exists:span_verified", "exists:entailment_verified"),
        unsatisfied_clauses=(),
        forbidden_present=(),
        evidence_bundle_hash="sha256:" + "a" * 64,
    )
    gate = PhaseLocalReplayGate(
        gate_id="fi-gate-closure-1",
        jurisdiction="fi",
        claim_id="claim-1",
        claim_kind="fi.v1.SPARSE_SLOT_PAYLOAD_RESOLUTION",
        frontier_ref="fi-frontier-claim-closure",
        owner_phase="typed_elaboration",
        authorization_rule_id="fi_sparse_slot_phase_gate_v1",
        required_proofs=("payload_identity_proof", "mutation_boundary_proof"),
        satisfied_proofs=("payload_identity_proof", "mutation_boundary_proof"),
        candidate_operation_family="sparse_item_payload_resolution",
        candidate_targets=("section:2",),
    )

    report = frontier_work_item_claim_closure_report(
        item,
        assertion=assertion,
        authorization_result=result,
        phase_replay_gate=gate,
    )
    data = report.to_dict()

    assert data["replay_claims"] is True
    assert data["summary"]["closure_status_counts"] == {
        "phase_replay_gate_authorized": 1
    }
    assert data["summary"]["phase_gate_required_count"] == 0
    assert data["summary"]["phase_gate_authorized_count"] == 1
    assert data["summary"]["replay_authorized_count"] == 1
    row = data["rows"][0]
    assert row["closure_status"] == "phase_replay_gate_authorized"
    assert row["phase_gate_authorized"] is True
    assert row["executable"] is True
    assert row["replay_authorized"] is True
    assert row["required_proofs"] == []
    assert row["detail"]["phase_replay_gate_evaluation"]["reason_code"] == (
        "phase_replay_gate_authorized"
    )


def test_frontier_work_item_claim_closure_report_blocks_incomplete_phase_gate() -> None:
    item = FrontierWorkItem(
        work_item_id="fi-frontier-claim-closure",
        jurisdiction="fi",
        source_artifact_id="2020/1",
        source_unit_id="section:2",
        owner_phase="typed_elaboration",
        frontier_family="fi_sparse_item_body_missing",
        frontier_status="manual_claim_needed",
        required_claim_kind="fi.v1.SPARSE_SLOT_PAYLOAD_RESOLUTION",
        required_proofs=("payload_identity_proof", "mutation_boundary_proof"),
        safe_default="block_until_validated_claim_authorizes_replay",
        forbidden_shortcuts=("manual_claim_as_replay_authorization",),
        authorization_status="blocked_manual_claim_required",
    )
    assertion = {
        "assertion_id": "claim-1",
        "jurisdiction": "fi",
        "kind": "fi.v1.SPARSE_SLOT_PAYLOAD_RESOLUTION",
        "target": {"frontier_ref": "fi-frontier-claim-closure"},
    }
    result = AuthorizationResult(
        subject=ArtifactRef(
            artifact_type="assertion",
            artifact_id="claim-1",
            content_hash="claim-1",
        ),
        policy_id="fi.v1.SPARSE_SLOT_PAYLOAD_RESOLUTION.strict",
        profile_name="fi_strict",
        authorized=True,
        satisfied_clauses=("exists:span_verified",),
        unsatisfied_clauses=(),
        forbidden_present=(),
        evidence_bundle_hash="sha256:" + "b" * 64,
    )
    gate = PhaseLocalReplayGate(
        gate_id="fi-gate-closure-2",
        jurisdiction="fi",
        claim_id="claim-1",
        claim_kind="fi.v1.SPARSE_SLOT_PAYLOAD_RESOLUTION",
        frontier_ref="fi-frontier-claim-closure",
        owner_phase="typed_elaboration",
        authorization_rule_id="fi_sparse_slot_phase_gate_v1",
        required_proofs=("payload_identity_proof", "mutation_boundary_proof"),
        satisfied_proofs=("payload_identity_proof",),
    )

    report = frontier_work_item_claim_closure_report(
        item,
        assertion=assertion,
        authorization_result=result,
        phase_replay_gate=gate,
    ).to_dict()

    assert report["replay_claims"] is False
    assert report["summary"]["closure_status_counts"] == {
        "rejected_phase_replay_gate_missing_proofs": 1
    }
    assert report["summary"]["phase_gate_required_count"] == 1
    assert report["summary"]["phase_gate_authorized_count"] == 0
    row = report["rows"][0]
    assert row["phase_gate_required"] is True
    assert row["phase_gate_authorized"] is False
    assert row["replay_authorized"] is False
    assert row["detail"]["phase_replay_gate_evaluation"]["missing_proofs"] == [
        "mutation_boundary_proof"
    ]


def test_frontier_work_item_claim_closure_report_exposes_mismatched_claim() -> None:
    item = FrontierWorkItem(
        work_item_id="fi-frontier-claim-closure",
        jurisdiction="fi",
        source_artifact_id="2020/1",
        source_unit_id="section:2",
        owner_phase="typed_elaboration",
        frontier_family="fi_sparse_item_body_missing",
        frontier_status="manual_claim_needed",
        required_claim_kind="fi.v1.SPARSE_SLOT_PAYLOAD_RESOLUTION",
        required_proofs=("mutation_boundary_proof",),
        safe_default="block_until_validated_claim_authorizes_replay",
        forbidden_shortcuts=("manual_claim_as_replay_authorization",),
        authorization_status="blocked_manual_claim_required",
    )
    assertion = {
        "assertion_id": "claim-2",
        "jurisdiction": "fi",
        "kind": "fi.v1.FAILED_OPERATION_RESOLUTION",
        "target": {"frontier_ref": "fi-frontier-claim-closure"},
    }
    result = AuthorizationResult(
        subject=ArtifactRef(
            artifact_type="assertion",
            artifact_id="claim-2",
            content_hash="claim-2",
        ),
        policy_id="fi.v1.FAILED_OPERATION_RESOLUTION.strict",
        profile_name="fi_strict",
        authorized=True,
        satisfied_clauses=("exists:span_verified",),
        unsatisfied_clauses=(),
        forbidden_present=(),
        evidence_bundle_hash="sha256:" + "b" * 64,
    )

    report = frontier_work_item_claim_closure_report(
        item,
        assertion=assertion,
        authorization_result=result,
    ).to_dict()

    assert report["summary"]["closure_status_counts"] == {"claim_kind_mismatch": 1}
    assert report["summary"]["policy_authorized_count"] == 1
    assert report["summary"]["claim_kind_match_count"] == 0
    assert report["summary"]["phase_gate_required_count"] == 0
    assert report["rows"][0]["policy_authorized"] is True
    assert report["rows"][0]["claim_kind_matches"] is False
    assert report["rows"][0]["closure_status"] == "claim_kind_mismatch"
    assert report["rows"][0]["replay_authorized"] is False


def test_frontier_work_item_report_rejects_invalid_mapping_rows() -> None:
    with pytest.raises(ValueError, match="required_proofs is required"):
        frontier_work_item_evidence_report(
            {
                "work_item_id": "bad-frontier",
                "jurisdiction": "fi",
                "source_artifact_id": "2020/1",
                "source_unit_id": "section:2",
                "owner_phase": "typed_elaboration",
                "frontier_family": "family",
                "frontier_status": "manual_claim_needed",
                "required_claim_kind": "claim",
                "safe_default": "block",
                "forbidden_shortcuts": ["shortcut"],
                "executable": False,
                "replay_authorized": False,
                "authorization_status": "blocked",
            }
        )


def test_frontier_work_item_report_enforces_canonical_report_edge_fields() -> None:
    report = frontier_work_item_evidence_report(
        {
            "surface": "caller_supplied_surface",
            "row_id": "caller-row",
            "subject_id": "caller-subject",
            "status": "caller-status",
            "frontier_ref": "caller-frontier",
            "work_item_id": "fi-frontier-canonical",
            "jurisdiction": "fi",
            "source_artifact_id": "2020/1",
            "source_unit_id": "section:2",
            "owner_phase": "typed_elaboration",
            "frontier_family": "fi_sparse_item_body_missing",
            "frontier_status": "manual_claim_needed",
            "required_claim_kind": "fi.v1.SPARSE_SLOT_PAYLOAD_RESOLUTION",
            "safe_default": "block",
            "required_proofs": ["mutation_boundary_proof"],
            "forbidden_shortcuts": ["manual_claim_as_replay_authorization"],
            "executable": False,
            "replay_authorized": False,
            "authorization_status": "blocked_manual_claim_required",
        }
    ).to_dict()

    row = report["rows"][0]
    assert row["surface"] == "frontier_work_item"
    assert row["row_id"] == "fi-frontier-canonical"
    assert row["subject_id"] == "2020/1"
    assert row["row_status"] == "manual_claim_needed"
    assert row["frontier_ref"] == "fi-frontier-canonical"


def test_frontier_work_item_report_projects_to_proof_surface_frontier_ref() -> None:
    report = frontier_work_item_evidence_report(
        FrontierWorkItem(
            work_item_id="uk-frontier-demo",
            jurisdiction="uk",
            source_artifact_id="ukpga/2020/1",
            source_unit_id="eff-1",
            source_witness={
                "source_role": "effect_feed",
                "artifact_id": "ukpga/2020/1",
                "source_unit_id": "eff-1",
                "locator": "https://example.test/effects/1",
            },
            target_witness={"affected_provisions": "s. 1"},
            compare_witness={"compare_shape": "commensurable"},
            owner_phase="typed_elaboration",
            frontier_family="uk_manual_frontier_heading_facet_candidate",
            frontier_status="manual_compile_candidate",
            candidate_operation_family="facet_text_rewrite",
            candidate_targets=("section-1",),
            required_claim_kind="semantic_compile",
            required_validator_checks=("claim_identifies_heading_facet",),
            required_proofs=("mutation_boundary_proof",),
            safe_default="block_until_validated_claim_authorizes_replay",
            forbidden_shortcuts=("unvalidated_manual_claim_execution",),
            authorization_status="manual_claim_required",
        ),
    )

    surface = proof_surface_from_evidence_report(report).to_dict()

    assert surface["surface_kind"] == "frontier_work_item"
    assert surface["claim_flags"]["replay_claims"] is False
    assert surface["rows"][0]["row_kind"] == "frontier_work_item"
    assert surface["rows"][0]["frontier_ref"] == "uk-frontier-demo"
    assert surface["rows"][0]["source_refs"] == [
        "ukpga/2020/1",
        "eff-1",
        "https://example.test/effects/1",
    ]


def test_proof_obligation_coverage_records_blocked_promotion() -> None:
    certificate = ProofObligationCoverage(
        scope_id="uk-frontier:eff-1:proofs",
        phase="typed_elaboration",
        rule_id="test_proof_boundary",
        reason="target is proved but mutation boundary still blocks replay",
        proof_status=PROOF_OBLIGATION_BLOCKED,
        proved_proofs=("target_candidate_set_completeness",),
        missing_proofs=("mutation_boundary_proof",),
        blocker_counts={"mutation_boundary_proof": 1},
        next_promotion_allowed=False,
        next_promotion_requires=("mutation_boundary_proof",),
        detail={"proof_obligation_not_replay_authorization": True},
    )

    data = certificate.to_dict()

    assert data["proof_status"] == "blocked"
    assert data["proved_proofs"] == ["target_candidate_set_completeness"]
    assert data["missing_proofs"] == ["mutation_boundary_proof"]
    assert data["next_promotion_allowed"] is False
    assert data["proof_obligation_not_replay_authorization"] is True


def test_proof_obligation_coverage_complete_rejects_missing_proofs() -> None:
    with pytest.raises(ValueError, match="requires no missing_proofs"):
        ProofObligationCoverage(
            scope_id="uk-frontier:eff-1:proofs",
            phase="typed_elaboration",
            rule_id="test_proof_boundary",
            reason="invalid complete certificate",
            proof_status=PROOF_OBLIGATION_COMPLETE,
            missing_proofs=("mutation_boundary_proof",),
        )


def test_agreement_residual_classifies_without_replay_promotion() -> None:
    residual = AgreementResidual(
        residual_id="uk-broad:ukpga/1938/22",
        jurisdiction="uk",
        agreement_surface="replay_eid_set_vs_current_oracle_eid_set",
        family="non_commensurable_surface",
        agreement_residual_status="frontier",
        owner_phase="compare_oracle_classification",
        rule_id="uk_broad_zero_oracle_retention",
        source_artifact_id="ukpga/1938/22",
        replay_count=420,
        oracle_count=0,
        missing_proofs=("commensurable_oracle_surface",),
        safe_default="classify_residual_without_replay_promotion",
        forbidden_shortcuts=("oracle_score_as_source_truth",),
        detail={"triage_bucket": "zero_oracle_retention"},
    )

    data = residual.to_dict()

    assert data["family"] == "non_commensurable_surface"
    assert data["agreement_residual_status"] == "frontier"
    assert data["missing_proofs"] == ["commensurable_oracle_surface"]
    assert "oracle_score_as_source_truth" in data["forbidden_shortcuts"]


def test_agreement_surface_report_projects_residuals_without_replay_claims() -> None:
    residual = AgreementResidual(
        residual_id="fi:2001/1234:oracle-extra-labels",
        jurisdiction="fi",
        agreement_surface="finlex_html_oracle_compare",
        family="non_commensurable_surface",
        agreement_residual_status="residual",
        owner_phase="oracle_adjudication",
        rule_id="fi_finlex_html_non_commensurable_surface",
        source_artifact_id="2001/1234",
        missing_proofs=("compare_projection_review",),
        safe_default="classify_without_replay_promotion",
        forbidden_shortcuts=("finlex_oracle_as_source_truth",),
    )

    surface = agreement_surface_from_residuals(
        (residual,),
        jurisdiction="fi",
        agreement_surface="finlex_html_oracle_compare",
        materialization_id="fi:2001/1234:materialization",
        comparison_target_id="finlex:2001/1234",
        comparison_kind="residual_classification",
        materialization_kind="legal_text_state",
        comparison_materialization_kind="official_consolidation_view",
        exact_ratio=0.99,
    )
    report = agreement_surface_evidence_report(
        surface,
        report_kind="finland_agreement_surface",
    )
    report_data = report.to_dict()
    proof_surface = proof_surface_from_evidence_report(report).to_dict()

    assert isinstance(surface, AgreementSurface)
    assert report_data["agreement_claims"] is True
    assert report_data["replay_claims"] is False
    assert report_data["summary"]["agreement_residual_count"] == 1
    assert report_data["summary"]["materialization_kind"] == "legal_text_state"
    assert (
        report_data["summary"]["comparison_materialization_kind"]
        == "official_consolidation_view"
    )
    assert report_data["summary"]["residual_family_counts"] == {
        "non_commensurable_surface": 1
    }
    assert report_data["filters"]["materialization_kind"] == "legal_text_state"
    assert report_data["rows"][0]["surface"] == "agreement_residual"
    assert report_data["rows"][0]["replay_authorized"] is False
    assert proof_surface["surface_kind"] == "finland_agreement_surface"
    assert proof_surface["rows"][0]["row_kind"] == "agreement_residual"


def test_agreement_residual_rejects_unknown_family() -> None:
    with pytest.raises(ValueError, match="AgreementResidual.family"):
        AgreementResidual(
            residual_id="bad",
            jurisdiction="uk",
            agreement_surface="surface",
            family=cast(Any, "loose_string"),
            agreement_residual_status="frontier",
            owner_phase="compare_oracle_classification",
            rule_id="bad_rule",
            safe_default="classify",
            forbidden_shortcuts=("shortcut",),
        )


def test_agreement_surface_rejects_unknown_materialization_kind() -> None:
    with pytest.raises(ValueError, match="materialization kind"):
        agreement_surface_from_residuals(
            (),
            jurisdiction="fi",
            agreement_surface="surface",
            materialization_id="mat",
            comparison_target_id="target",
            comparison_kind="compare",
            materialization_kind=cast(Any, "raw_oracle_text"),
        )


def test_mutation_boundary_proof_projects_passive_accounting() -> None:
    report = build_mutation_invariant_reports(
        (
            MutationEvent(
                op_id="op-1",
                source_statute="ukpga/2000/1",
                action="replace",
                helper="replace_text",
                outcome="replaced_node",
                resolved_target_path=(("body", ""), ("section", "1")),
                replaced_paths=((("body", ""), ("section", "2")),),
            ),
        )
    )[0]

    proof = MutationBoundaryProof.from_mutation_invariant_report(
        report,
        proof_id="proof-1",
        jurisdiction="uk",
        materialization_surface="unit_test_replay",
        owner_phase="replay_invariants",
        safe_default="block_or_classify_residual",
        forbidden_shortcuts=("ignore_unexplained_changed_paths",),
    )

    data = proof.to_dict()

    assert data["boundary_proof_status"] == "violated"
    assert data["rule_id"] == "mutation_boundary_path_set_violated"
    assert data["selected_target_paths"] == ["body/section:1"]
    assert data["unexplained_changed_paths"] == ["body/section:2"]
    assert data["result_codes"] == ["REPLAY_APPLY_BOUNDARY_TOUCH_OUTSIDE_TARGET"]
    assert "ignore_unexplained_changed_paths" in data["forbidden_shortcuts"]


def test_mutation_boundary_evidence_report_is_passive_shared_surface() -> None:
    violated = MutationBoundaryProof(
        proof_id="proof-violated",
        jurisdiction="fi",
        materialization_surface="finland_strict_report",
        operation_id="op-1",
        owner_phase="replay_apply",
        rule_id="mutation_boundary_path_set_violated",
        boundary_proof_status="violated",
        selected_target_paths=((("section", "1"),),),
        changed_paths=((("section", "2"),),),
        unexplained_changed_paths=((("section", "2"),),),
        result_codes=("REPLAY_APPLY_BOUNDARY_TOUCH_OUTSIDE_TARGET",),
        path_set_invariant_holds=False,
        safe_default="preserve_report_as_passive_boundary_evidence_not_replay_authorization",
        forbidden_shortcuts=("ignore_unexplained_changed_paths",),
    )
    proved = MutationBoundaryProof(
        proof_id="proof-proved",
        jurisdiction="fi",
        materialization_surface="finland_strict_report",
        operation_id="op-2",
        owner_phase="replay_apply",
        rule_id="mutation_boundary_path_set_proved",
        boundary_proof_status="proved",
        selected_target_paths=((("section", "2"),),),
        changed_paths=((("section", "2"),),),
        covered_changed_paths=((("section", "2"),),),
        safe_default="preserve_report_as_passive_boundary_evidence_not_replay_authorization",
        forbidden_shortcuts=("mutation_boundary_report_as_replay_authorization",),
    )

    report = mutation_boundary_evidence_report(
        (violated, proved),
        report_kind="finland_mutation_boundaries",
    ).to_dict()

    assert report["jurisdiction"] == "fi"
    assert report["schema"] == "lawvm.mutation_boundary_report.v1"
    assert report["replay_claims"] is False
    assert report["canonical_effect_claims"] is False
    assert report["candidate_effect_claims"] is False
    assert report["dry_run_claims"] is False
    assert report["agreement_claims"] is False
    assert report["summary"]["mutation_boundary_proof_count"] == 2
    assert report["summary"]["proved_count"] == 1
    assert report["summary"]["violated_count"] == 1
    assert report["summary"]["result_code_counts"] == {
        "REPLAY_APPLY_BOUNDARY_TOUCH_OUTSIDE_TARGET": 1
    }
    row = report["rows"][0]
    assert row["surface"] == "mutation_boundary_proof"
    assert row["row_id"] == "proof-violated"
    assert row["subject_id"] == "op-1"
    assert row["proof_ref"] == "proof-violated"
    assert row["boundary_proof_status"] == "violated"
    assert "mutation_boundary_proof_as_replay_authorization" in row["forbidden_shortcuts"]
    assert "mutation_boundary_proof_as_replay_authorization" in report["forbidden_shortcuts"]


def test_mutation_boundary_report_projects_to_proof_surface_rows() -> None:
    report = mutation_boundary_evidence_report(
        MutationBoundaryProof(
            proof_id="proof-proved",
            jurisdiction="fi",
            materialization_surface="finland_strict_report",
            operation_id="op-2",
            owner_phase="replay_apply",
            rule_id="mutation_boundary_path_set_proved",
            boundary_proof_status="proved",
            selected_target_paths=((("section", "2"),),),
            changed_paths=((("section", "2"),),),
            covered_changed_paths=((("section", "2"),),),
            safe_default="preserve_report_as_passive_boundary_evidence_not_replay_authorization",
            forbidden_shortcuts=("mutation_boundary_report_as_replay_authorization",),
        ),
        jurisdiction="fi",
    )

    surface = proof_surface_from_evidence_report(report).to_dict()

    assert surface["surface_kind"] == "mutation_boundary_proof"
    assert surface["claim_flags"]["replay_claims"] is False
    assert surface["rows"][0]["row_id"] == "proof-proved"
    assert surface["rows"][0]["subject_id"] == "op-2"
    assert surface["rows"][0]["row_kind"] == "mutation_boundary_proof"
    assert surface["rows"][0]["proof_status"] == "proved"
    assert surface["rows"][0]["proof_refs"] == ["proof-proved"]


def test_mutation_boundary_report_rejects_invalid_mapping_rows() -> None:
    with pytest.raises(ValueError, match="MutationBoundaryProof.status"):
        mutation_boundary_evidence_report(
            {
                "proof_id": "bad-proof",
                "jurisdiction": "fi",
                "materialization_surface": "finland_strict_report",
                "operation_id": "op-1",
                "owner_phase": "replay_apply",
                "rule_id": "bad",
                "boundary_proof_status": "done",
                "safe_default": "classify",
                "forbidden_shortcuts": ["shortcut"],
            },
            jurisdiction="fi",
        )


def test_mutation_boundary_proof_requires_safe_default_and_forbidden_shortcuts() -> None:
    with pytest.raises(ValueError, match="MutationBoundaryProof.safe_default"):
        MutationBoundaryProof(
            proof_id="bad",
            jurisdiction="uk",
            materialization_surface="surface",
            operation_id="op-1",
            owner_phase="replay_invariants",
            rule_id="rule",
            boundary_proof_status="proved",
            forbidden_shortcuts=("shortcut",),
        )
    with pytest.raises(ValueError, match="MutationBoundaryProof.forbidden_shortcuts"):
        MutationBoundaryProof(
            proof_id="bad",
            jurisdiction="uk",
            materialization_surface="surface",
            operation_id="op-1",
            owner_phase="replay_invariants",
            rule_id="rule",
            boundary_proof_status="proved",
            safe_default="classify",
        )


def test_source_witness_normalizes_digest_and_preserves_wire_fields() -> None:
    witness = source_witness_from_mapping(
        {
            "affecting_act_id": "ukpga/2025/1",
            "affecting_provisions": "s. 2",
            "source_sha256": "abc123",
            "source_status": "available",
        },
        default_role="affecting_source",
    )

    data = witness.to_dict()

    assert data["source_role"] == "affecting_source"
    assert data["artifact_id"] == "ukpga/2025/1"
    assert data["source_unit_id"] == "s. 2"
    assert data["digest_algorithm"] == "sha256"
    assert data["digest"] == "abc123"
    assert data["source_sha256"] == "abc123"
    assert data["source_status"] == "available"


def test_source_locator_digest_defaults_to_sha256_and_feeds_source_ref() -> None:
    digest = "a" * 64
    locator = SourceLocator(
        jurisdiction="fi",
        artifact_kind="finlex_akn",
        source_id="finlex:2024/1",
        structural_path="section:1",
        artifact_digest=digest,
    )

    data = locator.to_dict()
    source_ref = source_ref_from_locator(locator)

    assert data["artifact_digest"] == digest
    assert data["artifact_digest_algorithm"] == "sha256"
    assert source_ref.artifact_digest == digest
    assert source_ref.structural_locator == "section:1"


def test_source_locator_rejects_malformed_artifact_digest() -> None:
    with pytest.raises(ValueError, match="artifact_digest must be a lowercase sha256 digest"):
        SourceLocator(
            jurisdiction="fi",
            artifact_kind="finlex_akn",
            source_id="finlex:2024/1",
            artifact_digest="abc123",
        )


def test_source_locator_rejects_unsupported_or_unpaired_digest_algorithm() -> None:
    with pytest.raises(ValueError, match="artifact_digest_algorithm must be sha256"):
        SourceLocator(
            jurisdiction="fi",
            artifact_kind="finlex_akn",
            source_id="finlex:2024/1",
            artifact_digest="a" * 64,
            artifact_digest_algorithm="sha512",
        )
    with pytest.raises(ValueError, match="artifact_digest_algorithm requires artifact_digest"):
        SourceLocator(
            jurisdiction="fi",
            artifact_kind="finlex_akn",
            source_id="finlex:2024/1",
            artifact_digest_algorithm="sha256",
        )


def test_source_ref_from_locator_validates_explicit_artifact_digest() -> None:
    locator = SourceLocator(
        jurisdiction="fi",
        artifact_kind="finlex_akn",
        source_id="finlex:2024/1",
        structural_path="section:1",
    )

    with pytest.raises(ValueError, match="artifact_digest must be a lowercase sha256 digest"):
        source_ref_from_locator(locator, artifact_digest="abc123")


def test_source_witness_computes_preview_digest() -> None:
    witness = source_witness_from_mapping(
        {"text_preview": "source fragment"},
        default_role="source_preview",
        default_artifact_id="ukpga/2025/1",
        default_source_unit_id="eff-1",
    )

    data = witness.to_dict()

    assert data["artifact_id"] == "ukpga/2025/1"
    assert data["bounded_preview"] == "source fragment"
    assert data["preview_digest_algorithm"] == "sha256"
    assert data["preview_digest"]


def test_source_witness_reporting_keys_classify_role_and_digest_coverage() -> None:
    artifact = source_witness_from_mapping(
        {"source_role": "affecting_source", "source_sha256": "abc123"},
        default_role="source_preview",
    ).to_dict()
    preview = source_witness_from_mapping(
        {"source_role": "effect_feed_row", "text_preview": "row text"},
        default_role="source_preview",
    ).to_dict()
    both = source_witness_from_mapping(
        {
            "source_role": "affecting_source_fragment",
            "source_sha256": "abc123",
            "text_preview": "row text",
        },
        default_role="source_preview",
    ).to_dict()

    assert source_witness_role_key(artifact) == "affecting_source"
    assert source_witness_role_key({}) == "__missing__"
    assert source_witness_digest_coverage(artifact) == "artifact_digest"
    assert source_witness_digest_coverage({"source_sha256": "abc123"}) == (
        "artifact_digest"
    )
    assert source_witness_digest_coverage(preview) == "preview_digest"
    assert source_witness_digest_coverage(both) == "artifact_and_preview_digest"
    assert source_witness_digest_coverage({"source_role": "effect_feed_row"}) == (
        "missing_digest"
    )
    assert source_witness_digest_coverage({}) == "missing_source_witness"


def test_source_witness_digest_coverage_counts_are_shared_sorted_summaries() -> None:
    counts = source_witness_digest_coverage_counts(
        (
            {"digest": "abc", "preview_digest": "def"},
            {"digest": "ghi"},
            {"preview_digest": "jkl"},
            {},
        )
    )

    assert counts == {
        "artifact_and_preview_digest": 1,
        "artifact_digest": 1,
        "missing_source_witness": 1,
        "preview_digest": 1,
    }


def test_nested_source_witness_digest_coverage_counts_handles_missing_witnesses() -> None:
    counts = nested_source_witness_digest_coverage_counts(
        (
            {"source_witness": {"digest": "abc"}},
            {"source_witness": {"preview_digest": "def"}},
            {"source_witness": {}},
            {},
        )
    )

    assert counts == {
        "artifact_digest": 1,
        "missing_source_witness": 2,
        "preview_digest": 1,
    }


def test_source_witness_requires_role_and_digest_witness_requires_digest() -> None:
    with pytest.raises(ValueError, match="source_role"):
        SourceWitness(source_role="")
    with pytest.raises(ValueError, match="digest"):
        DigestWitness(digest_algorithm="sha256", digest="")


def test_source_witness_evidence_report_is_passive_shared_surface() -> None:
    report = source_witness_evidence_report(
        (
            SourceWitness(
                source_role="affecting_source",
                artifact_id="ukpga/2025/1",
                source_unit_id="section:2",
                locator="https://example.test/ukpga/2025/1/section/2/data.xml",
                digest=DigestWitness(digest_algorithm="sha256", digest="a" * 64),
                bounded_preview="omit section 3",
                preview_digest=DigestWitness(
                    digest_algorithm="sha256",
                    digest="b" * 64,
                ),
                source_lane="archive_xml",
            ),
            {
                "source_role": "effect_feed_row",
                "artifact_id": "ukpga/2025/1",
                "source_unit_id": "effect-1",
                "text_preview": "effect feed row",
                "source_lane": "effect_feed",
            },
        ),
        jurisdiction="uk",
    )

    data = report.to_dict()

    assert data["schema"] == "lawvm.source_witness_report.v1"
    assert data["replay_claims"] is False
    assert data["canonical_effect_claims"] is False
    assert data["candidate_effect_claims"] is False
    assert data["dry_run_claims"] is False
    assert data["agreement_claims"] is False
    assert data["summary"]["source_witness_count"] == 2
    assert data["summary"]["source_role_counts"] == {
        "affecting_source": 1,
        "effect_feed_row": 1,
    }
    assert data["summary"]["digest_coverage_counts"] == {
        "artifact_and_preview_digest": 1,
        "preview_digest": 1,
    }
    assert data["rows"][0]["surface"] == "source_witness"
    assert data["rows"][0]["row_id"].startswith(
        "affecting_source:ukpga_2025_1:section_2:"
    )
    assert data["rows"][0]["subject_id"] == "ukpga/2025/1"
    assert data["rows"][0]["row_status"] == "artifact_and_preview_digest"
    assert data["rows"][0]["witness_ref"] == data["rows"][0]["row_id"]
    assert "source_witness_as_replay_authorization" in data["forbidden_shortcuts"]


def test_source_witness_evidence_report_projects_to_proof_surface() -> None:
    report = source_witness_evidence_report(
        SourceWitness(
            source_role="base_source",
            artifact_id="fi:2024/1",
            source_unit_id="section:1",
            locator="finlex://2024/1/section/1",
            digest=DigestWitness(digest_algorithm="sha256", digest="c" * 64),
        ),
        jurisdiction="fi",
    )

    surface = proof_surface_from_evidence_report(report).to_dict()

    assert surface["claim_flags"] == {
        "agreement_claims": False,
        "canonical_effect_claims": False,
        "candidate_effect_claims": False,
        "dry_run_claims": False,
        "replay_claims": False,
    }
    assert surface["rows"][0]["row_kind"] == "source_witness"
    assert surface["rows"][0]["proof_status"] == "artifact_digest"
    assert surface["rows"][0]["source_refs"] == [
        "fi:2024/1",
        "section:1",
        "finlex://2024/1/section/1",
    ]


def test_source_witness_row_ids_distinguish_preview_only_same_unit_witnesses() -> None:
    report = source_witness_evidence_report(
        (
            {
                "source_role": "effect_feed_row",
                "artifact_id": "ukpga/2025/1",
                "source_unit_id": "effect-1",
                "text_preview": "first effect row",
            },
            {
                "source_role": "effect_feed_row",
                "artifact_id": "ukpga/2025/1",
                "source_unit_id": "effect-1",
                "text_preview": "second effect row",
            },
        ),
        jurisdiction="uk",
    )
    data = report.to_dict()
    proof_surface = proof_surface_from_evidence_report(report).to_dict()
    row_ids = [row["row_id"] for row in data["rows"]]

    assert len(set(row_ids)) == 2
    assert all(row["row_status"] == "preview_digest" for row in data["rows"])
    assert [row["row_id"] for row in proof_surface["rows"]] == row_ids


def test_source_witness_row_ids_distinguish_locator_only_same_unit_witnesses() -> None:
    report = source_witness_evidence_report(
        (
            {
                "source_role": "source_locator",
                "artifact_id": "fi:2024/1",
                "source_unit_id": "section:1",
                "locator": "finlex://2024/1/section/1/current.xml",
            },
            {
                "source_role": "source_locator",
                "artifact_id": "fi:2024/1",
                "source_unit_id": "section:1",
                "locator": "finlex://2024/1/section/1/enacted.xml",
            },
        ),
        jurisdiction="fi",
    )
    row_ids = [row["row_id"] for row in report.to_dict()["rows"]]

    assert len(set(row_ids)) == 2


def test_source_witness_evidence_report_rejects_non_witness_inputs() -> None:
    with pytest.raises(ValueError, match="source witness report"):
        source_witness_evidence_report(cast(Any, 7), jurisdiction="fi")
    with pytest.raises(ValueError, match="source witness report"):
        source_witness_evidence_report(cast(Any, "not-a-witness"), jurisdiction="fi")
    with pytest.raises(ValueError, match="source witness report"):
        source_witness_evidence_report(cast(Any, [{"source_role": "ok"}, 7]), jurisdiction="fi")


def test_evidence_surface_report_declares_non_replay_claims() -> None:
    report = EvidenceSurfaceReport(
        jurisdiction="uk",
        report_kind="uk_effects_frontier_report",
        schema="lawvm.uk_effects_frontier_report.v1",
        truth_claim="uk_effect_feed_and_frontier_diagnostics_only",
        replay_claims=False,
        canonical_effect_claims=False,
        candidate_effect_claims=False,
        dry_run_claims=False,
        agreement_claims=False,
        summary={"matched_effects": 1, "truncated": False},
        filters={"limit": 1},
        filtered_summary={"matched_effects": 1},
        rows=({"effect_id": "eff-1"},),
        rows_truncated=False,
        detail={"statute_id": "ukpga/2000/1"},
    )

    data = report.to_dict()

    assert data["jurisdiction"] == "uk"
    assert data["replay_claims"] is False
    assert data["canonical_effect_claims"] is False
    assert data["candidate_effect_claims"] is False
    assert data["dry_run_claims"] is False
    assert data["agreement_claims"] is False
    assert data["rows"] == [{"effect_id": "eff-1"}]
    assert data["statute_id"] == "ukpga/2000/1"


def test_proof_surface_rows_are_queryable_without_replay_authority() -> None:
    row = ProofSurfaceRow(
        row_id="row-1",
        subject_id="fi:2001/1234",
        row_kind="temporal_resolution_evidence",
        proof_status="block",
        source_refs=("2025/78",),
        proof_refs=("proof-1",),
        detail={"replay_authorized": False},
    )
    surface = ProofSurface(
        surface_id="fi:strict:demo",
        surface_kind="finland_strict_report",
        jurisdiction="fi",
        profile_id="FINLAND_INGESTION_V1",
        summary={"row_count": 1},
        rows=(row,),
    )
    data = surface.to_dict()

    assert data["surface_id"] == "fi:strict:demo"
    assert data["rows"][0]["row_kind"] == "temporal_resolution_evidence"
    assert data["rows"][0]["proof_status"] == "block"
    assert data["rows"][0]["source_refs"] == ["2025/78"]
    assert data["rows"][0]["detail"]["replay_authorized"] is False


def test_proof_surface_rejects_duplicate_row_ids() -> None:
    row = ProofSurfaceRow(
        row_id="duplicate-row",
        subject_id="fi:2001/1234",
        row_kind="source_witness",
        proof_status="reported",
    )

    with pytest.raises(ValueError, match="unique row_id"):
        ProofSurface(
            surface_id="fi:strict:duplicate-demo",
            surface_kind="finland_strict_report",
            jurisdiction="fi",
            rows=(row, row),
        )


def test_proof_surface_from_evidence_report_rejects_duplicate_explicit_row_ids() -> None:
    report = EvidenceSurfaceReport(
        jurisdiction="fi",
        report_kind="duplicate_rows",
        schema="test.duplicate_rows.v1",
        truth_claim="duplicate row identity fixture",
        replay_claims=False,
        canonical_effect_claims=False,
        candidate_effect_claims=False,
        dry_run_claims=False,
        agreement_claims=False,
        rows=(
            {"surface": "source_witness", "row_id": "duplicate-row"},
            {"surface": "source_witness", "row_id": "duplicate-row"},
        ),
    )

    with pytest.raises(ValueError, match="duplicate-row"):
        proof_surface_from_evidence_report(report)


def test_proof_surface_from_evidence_report_preserves_report_rows_as_read_model() -> None:
    report = EvidenceSurfaceReport(
        jurisdiction="fi",
        report_kind="finland_strict_report",
        schema="lawvm.finland_strict_report.v1",
        truth_claim="strict diagnostics only",
        replay_claims=False,
        canonical_effect_claims=True,
        candidate_effect_claims=False,
        dry_run_claims=False,
        agreement_claims=False,
        summary={"temporal_resolution_evidence_count": 1},
        filters={"profile": "FINLAND_INGESTION_V1"},
        rows=(
            {
                "surface": "temporal_resolution_evidence",
                "rule_id": "fi_time_estimated_effective_date",
                "source": "2025/78",
                "strict_disposition": "block",
                "temporal_resolution_status": "unknown_effective_date",
            },
            {
                "surface": "source_pathology_frontier_work_item",
                "work_item_id": "fi:frontier:1",
                "source_witness": {
                    "artifact_id": "2020/1",
                    "source_unit_id": "section:2",
                    "locator": "finlex://2020/1",
                },
                "frontier_status": "source_pathology_frontier",
            },
        ),
    )

    surface = proof_surface_from_evidence_report(report)
    data = surface.to_dict()

    assert data["surface_kind"] == "finland_strict_report"
    assert data["profile_id"] == "FINLAND_INGESTION_V1"
    assert data["claim_flags"] == {
        "replay_claims": False,
        "canonical_effect_claims": True,
        "candidate_effect_claims": False,
        "dry_run_claims": False,
        "agreement_claims": False,
    }
    assert data["summary"] == {"temporal_resolution_evidence_count": 1}
    assert data["rows"][0]["row_kind"] == "temporal_resolution_evidence"
    assert data["rows"][0]["proof_status"] == "block"
    assert data["rows"][0]["source_refs"] == ["2025/78"]
    assert data["rows"][1]["frontier_ref"] == "fi:frontier:1"
    assert data["rows"][1]["source_refs"] == [
        "2020/1",
        "section:2",
        "finlex://2020/1",
    ]


def test_proof_surface_extracts_refs_from_flat_source_witness_rows() -> None:
    report = EvidenceSurfaceReport(
        jurisdiction="fi",
        report_kind="finland_strict_report",
        schema="lawvm.finland_strict_report.v1",
        truth_claim="strict diagnostics only",
        replay_claims=False,
        canonical_effect_claims=True,
        candidate_effect_claims=False,
        dry_run_claims=False,
        agreement_claims=False,
        rows=(
            {
                "surface": "source_lineage_source_witness",
                "source_role": "finland_source_lineage_amendment",
                "artifact_id": "2020/1",
                "source_unit_id": "2020/1",
                "locator": "finlex://2020/1",
                "source_path": "finlex://2020/1/source.xml",
                "status": "reported",
            },
        ),
    )

    surface = proof_surface_from_evidence_report(report).to_dict()

    assert surface["rows"][0]["row_kind"] == "source_lineage_source_witness"
    assert surface["rows"][0]["source_refs"] == [
        "2020/1",
        "finlex://2020/1",
        "finlex://2020/1/source.xml",
    ]


def test_proof_surface_extracts_refs_from_role_keyed_source_witnesses() -> None:
    report = EvidenceSurfaceReport(
        jurisdiction="uk",
        report_kind="uk_broad_baseline",
        schema="lawvm.uk_broad_baseline.v1",
        truth_claim="uk replay diagnostics only",
        replay_claims=False,
        canonical_effect_claims=False,
        candidate_effect_claims=False,
        dry_run_claims=False,
        agreement_claims=True,
        rows=(
            {
                "surface": "agreement_residual",
                "residual_id": "residual-1",
                "status": "residual",
                "base_source_witness": {
                    "source_role": "uk_broad_base_source",
                    "artifact_id": "ukpga/2000/1",
                    "locator": "https://www.legislation.gov.uk/ukpga/2000/1/enacted/data.xml",
                },
                "oracle_source_witness": {
                    "source_role": "uk_broad_oracle_source",
                    "artifact_id": "ukpga/2000/1",
                    "locator": "https://www.legislation.gov.uk/ukpga/2000/1/data.xml",
                },
            },
        ),
    )

    surface = proof_surface_from_evidence_report(report).to_dict()

    assert surface["rows"][0]["source_refs"] == [
        "ukpga/2000/1",
        "https://www.legislation.gov.uk/ukpga/2000/1/enacted/data.xml",
        "https://www.legislation.gov.uk/ukpga/2000/1/data.xml",
    ]


def test_evidence_surface_report_requires_claim_flags() -> None:
    with pytest.raises(ValueError, match="replay_claims"):
        EvidenceSurfaceReport(
            jurisdiction="uk",
            report_kind="bad",
            schema="schema",
            truth_claim="claim",
            replay_claims=cast(Any, "false"),
            canonical_effect_claims=False,
            candidate_effect_claims=False,
            dry_run_claims=False,
            agreement_claims=False,
        )


def test_candidate_set_coverage_records_bounded_completeness() -> None:
    certificate = CandidateSetCoverage(
        scope_id="uk-candidates:demo",
        candidate_set_kind="uk_candidates_frontier_rows",
        phase="tooling",
        rule_id="uk_candidates_report_candidate_set_projection",
        reason="bounded candidate report projection",
        completeness_status=CANDIDATE_SET_TRUNCATED,
        candidate_count=3,
        candidate_ids=("ukpga/2000/1", "ukpga/2000/2"),
        missing_candidate_count=1,
        blocker_counts={"frontier_truncated": 1},
        blocker_families=("frontier_truncated",),
        next_promotion_allowed=False,
        next_promotion_requires=("candidate_set_completeness", "execution_authorization"),
        detail={"summary_only_projection": False},
    )

    data = certificate.to_dict()

    assert data["completeness_status"] == "truncated"
    assert data["candidate_count"] == 3
    assert data["candidate_ids"] == ["ukpga/2000/1", "ukpga/2000/2"]
    assert data["missing_candidate_count"] == 1
    assert data["next_promotion_allowed"] is False
    assert data["summary_only_projection"] is False


def test_candidate_set_evidence_report_is_passive_shared_surface() -> None:
    complete = CandidateSetCoverage(
        scope_id="fi:demo:source-unit-enumeration",
        candidate_set_kind="fi_strict_report_source_unit_enumeration",
        phase="source_unit_enumeration",
        rule_id="fi_source_unit_enumeration_complete",
        reason="declared source units were enumerated",
        completeness_status=CANDIDATE_SET_COMPLETE,
        candidate_count=2,
        candidate_ids=("source-unit:1", "source-unit:2"),
        missing_candidate_count=0,
        next_promotion_allowed=True,
        next_promotion_requires=("execution_authorization",),
    )
    truncated = CandidateSetCoverage(
        scope_id="fi:demo:operation-cue-coverage",
        candidate_set_kind="fi_strict_report_operation_cue_coverage",
        phase="operation_cue_detection",
        rule_id="fi_operation_cue_coverage_truncated",
        reason="operation cue scan was truncated",
        completeness_status=CANDIDATE_SET_TRUNCATED,
        candidate_count=3,
        candidate_ids=("op:1", "op:2"),
        missing_candidate_count=1,
        blocker_counts={"operation_cue_scan_truncated": 1},
        blocker_families=("operation_cue_scan_truncated",),
        next_promotion_allowed=False,
        next_promotion_requires=("operation_cue_classification_report",),
    )

    report = candidate_set_evidence_report(
        (complete, truncated),
        jurisdiction="fi",
        report_kind="finland_candidate_sets",
    ).to_dict()

    assert report["schema"] == "lawvm.candidate_set_report.v1"
    assert report["replay_claims"] is False
    assert report["canonical_effect_claims"] is False
    assert report["candidate_effect_claims"] is False
    assert report["dry_run_claims"] is False
    assert report["agreement_claims"] is False
    assert report["summary"]["candidate_set_coverage_count"] == 2
    assert report["summary"]["complete_count"] == 1
    assert report["summary"]["incomplete_count"] == 1
    assert report["summary"]["candidate_count"] == 5
    assert report["summary"]["missing_candidate_count"] == 1
    assert report["summary"]["next_promotion_allowed_count"] == 1
    assert report["summary"]["blocker_family_counts"] == {
        "operation_cue_scan_truncated": 1
    }
    row = report["rows"][0]
    assert row["surface"] == "candidate_set_coverage"
    assert row["row_id"] == "fi:demo:source-unit-enumeration"
    assert row["subject_id"] == "fi:demo:source-unit-enumeration"
    assert row["row_status"] == "complete"
    assert "candidate_set_coverage_as_replay_authorization" in row["forbidden_shortcuts"]
    assert "candidate_set_coverage_as_replay_authorization" in report["forbidden_shortcuts"]


def test_potential_operation_evidence_report_is_passive_shared_surface() -> None:
    compiled = PotentialOperation(
        potential_operation_id="canonical-op:lo-1",
        jurisdiction="fi",
        source_artifact_id="fi:2001/1234:strict-report-canonical-ops",
        source_unit_id="lo-1",
        owner_phase="canonical_operation_lowering",
        classification=POTENTIAL_OPERATION_COMPILED,
        operation_family="fi_canonical_operation",
        refs=("lo-1",),
        required_proofs=("source_text_operation_cue_detector",),
        safe_default="do_not_treat_compiled_visible_ops_as_source_cue_exhaustiveness",
    )
    failed = PotentialOperation(
        potential_operation_id="failed-op:abc",
        jurisdiction="fi",
        source_artifact_id="2020/1",
        source_unit_id="chapter:4/section:5",
        source_anchor={
            "basis": "failed_operation_frontier_source_witness",
            "frontier_work_item_id": "fi-failed-operation:2020_1:unsupported:abc",
            "projection_only": True,
            "does_not_claim": ["replay_authorization"],
        },
        owner_phase="replay_apply",
        classification=POTENTIAL_OPERATION_FAILED,
        operation_family="fi_failed_operation",
        target="chapter:4/section:5",
        required_proofs=("failed_operation_reason_classification",),
        safe_default="do_not_treat_failed_operation_as_replay_authority",
    )

    report = potential_operation_evidence_report(
        (compiled, failed),
        jurisdiction="fi",
        report_kind="finland_strict_report_potential_operations",
    ).to_dict()

    assert report["schema"] == "lawvm.potential_operation_coverage.v1"
    assert report["replay_claims"] is False
    assert report["canonical_effect_claims"] is False
    assert report["candidate_effect_claims"] is False
    assert report["summary"]["potential_operation_count"] == 2
    assert report["summary"]["classification_counts"] == {
        "compiled": 1,
        "failed": 1,
    }
    assert report["summary"]["operation_family_counts"] == {
        "fi_canonical_operation": 1,
        "fi_failed_operation": 1,
    }
    assert report["rows"][0]["surface"] == "potential_operation"
    assert report["rows"][0]["row_id"] == "canonical-op:lo-1"
    assert report["rows"][0]["row_status"] == "compiled"
    assert "potential_operation_as_replay_authorization" in report["rows"][0]["forbidden_shortcuts"]
    assert report["rows"][1]["source_anchor"]["basis"] == (
        "failed_operation_frontier_source_witness"
    )
    assert "replay_authorization" in report["rows"][1]["source_anchor"]["does_not_claim"]
    assert report["rows"][1]["row_status"] == "failed"


def test_potential_operation_evidence_report_rejects_invalid_mapping_fields() -> None:
    with pytest.raises(ValueError, match="mapping fields"):
        potential_operation_evidence_report(
            {
                "potential_operation_id": "failed-op:bad",
                "jurisdiction": "fi",
                "source_artifact_id": "2020/1",
                "source_unit_id": "chapter:4/section:5",
                "source_anchor": "not-a-mapping",
                "owner_phase": "replay_apply",
                "classification": POTENTIAL_OPERATION_FAILED,
                "operation_family": "fi_failed_operation",
                "required_proofs": ("failed_operation_reason_classification",),
                "safe_default": "do_not_treat_failed_operation_as_replay_authority",
            },
            jurisdiction="fi",
        )


def test_source_unit_coverage_evidence_report_is_passive_shared_surface() -> None:
    lineage_row = SourceUnitCoverage(
        coverage_id="fi:2001/1234:source-unit-coverage:abc",
        jurisdiction="fi",
        source_artifact_id="2020/1",
        source_unit_id="2020/1",
        owner_phase="source_chain_elaboration",
        coverage_status=SOURCE_UNIT_LINEAGE_WITNESSED,
        unit_family="finland_source_lineage_amendment",
        source_role="finland_source_lineage_amendment",
        source_lane="finland_source_adjudication_lineage",
        refs=("2020/1",),
        required_proofs=("source_artifact_unit_inventory",),
        safe_default="treat_lineage_source_unit_coverage_as_witnessed_only_not_full_enumeration",
    )
    frontier_row = SourceUnitCoverage(
        coverage_id="fi:2001/1234:source-unit-coverage:def",
        jurisdiction="fi",
        source_artifact_id="2020/2",
        source_unit_id="section:2",
        owner_phase="typed_elaboration",
        coverage_status=SOURCE_UNIT_FRONTIER_WITNESSED,
        unit_family="fi_sparse_item_body_missing",
        source_role="finland_source_pathology",
        source_lane="source_pathology",
        refs=("2020/2",),
        required_proofs=("source_artifact_unit_inventory",),
        safe_default="treat_frontier_source_unit_coverage_as_witnessed_only_not_full_enumeration",
    )

    report = source_unit_coverage_evidence_report(
        (lineage_row, frontier_row),
        jurisdiction="fi",
        report_kind="finland_strict_report_source_unit_coverage",
    ).to_dict()

    assert report["schema"] == "lawvm.source_unit_coverage.v1"
    assert report["replay_claims"] is False
    assert report["canonical_effect_claims"] is False
    assert report["candidate_effect_claims"] is False
    assert report["summary"]["source_unit_coverage_count"] == 2
    assert report["summary"]["coverage_status_counts"] == {
        "frontier_witnessed": 1,
        "lineage_witnessed": 1,
    }
    assert report["summary"]["lineage_witnessed_count"] == 1
    assert report["summary"]["frontier_witnessed_count"] == 1
    assert report["rows"][0]["surface"] == "source_unit_coverage"
    assert report["rows"][0]["row_id"] == "fi:2001/1234:source-unit-coverage:abc"
    assert report["rows"][0]["row_status"] == "lineage_witnessed"
    assert "source_unit_coverage_as_replay_authorization" in report["rows"][0]["forbidden_shortcuts"]
    assert "source_unit_coverage_as_complete_source_enumeration" in report["forbidden_shortcuts"]


def test_regex_recognition_coverage_reports_unclassified_skipped_spans() -> None:
    text = "lisätään 5 §:ään kuitenkin uusi 2 momentti"
    row = RegexRecognitionCoverage(
        coverage_id="fi:regex:1",
        jurisdiction="fi",
        recognizer_id="fi_insert_subsection_fallback",
        owner_phase="surface_syntax_frontend",
        source_artifact_id="2020/1",
        source_text_hash=regex_source_text_hash(text),
        matched_span=(9, len(text)),
        coverage_status=REGEX_RECOGNITION_UNCLASSIFIED_GAP,
        semantic_slots={
            "action": "INSERT",
            "target_section": "5",
            "target_subsections": (2,),
        },
        ignored_spans=(
            {
                "span": (17, 27),
                "classification": "unclassified",
                "text_preview": "kuitenkin ",
                "could_alter_meaning": True,
            },
        ),
        required_proofs=("regex_skipped_span_classification",),
    )

    report = regex_recognition_coverage_evidence_report(
        row,
        jurisdiction="fi",
    ).to_dict()

    assert report["schema"] == "lawvm.regex_recognition_coverage.v1"
    assert report["replay_claims"] is False
    assert report["summary"]["regex_recognition_coverage_count"] == 1
    assert report["summary"]["unclassified_gap_count"] == 1
    assert report["summary"]["coverage_status_counts"] == {"unclassified_gap": 1}
    projected = report["rows"][0]
    assert projected["surface"] == "regex_recognition_coverage"
    assert projected["row_status"] == "unclassified_gap"
    assert projected["ignored_spans"][0]["could_alter_meaning"] is True
    assert "bounded_wildcard_as_semantic_proof" in projected["forbidden_shortcuts"]
    assert "regex_coverage_as_replay_authorization" in report["forbidden_shortcuts"]


def test_regex_recognition_coverage_rejects_malformed_mapping_spans() -> None:
    text = "lisätään 5 §:ään uusi 2 momentti"

    with pytest.raises(ValueError, match="exactly two offsets"):
        regex_recognition_coverage_evidence_report(
            {
                "coverage_id": "fi:regex:bad",
                "jurisdiction": "fi",
                "recognizer_id": "fi_insert_subsection_fallback",
                "owner_phase": "surface_syntax_frontend",
                "source_artifact_id": "2020/1",
                "source_text_hash": regex_source_text_hash(text),
                "matched_span": [0],
                "coverage_status": REGEX_RECOGNITION_FULLY_CLASSIFIED,
                "safe_default": "treat_regex_recognition_as_parse_evidence_not_replay_authority",
                "forbidden_shortcuts": ("regex_coverage_as_replay_authorization",),
            },
            jurisdiction="fi",
        )

    with pytest.raises(ValueError, match="integer offset"):
        RegexRecognitionCoverage(
            coverage_id="fi:regex:bad-bool",
            jurisdiction="fi",
            recognizer_id="fi_insert_subsection_fallback",
            owner_phase="surface_syntax_frontend",
            source_artifact_id="2020/1",
            source_text_hash=regex_source_text_hash(text),
            matched_span=(0, True),
            coverage_status=REGEX_RECOGNITION_FULLY_CLASSIFIED,
        )


def test_regex_recognition_coverage_rejects_ignored_span_outside_match() -> None:
    text = "lisätään 5 §:ään kuitenkin uusi 2 momentti"

    with pytest.raises(ValueError, match="within matched_span"):
        RegexRecognitionCoverage(
            coverage_id="fi:regex:bad-ignored-span",
            jurisdiction="fi",
            recognizer_id="fi_insert_subsection_fallback",
            owner_phase="surface_syntax_frontend",
            source_artifact_id="2020/1",
            source_text_hash=regex_source_text_hash(text),
            matched_span=(10, len(text)),
            coverage_status=REGEX_RECOGNITION_UNCLASSIFIED_GAP,
            ignored_spans=(
                {
                    "span": (0, 9),
                    "classification": "unclassified",
                    "could_alter_meaning": True,
                },
            ),
            required_proofs=("regex_skipped_span_classification",),
        )


def test_regex_recognition_coverage_rejects_inconsistent_gap_status() -> None:
    text = "lisätään 5 §:ään kuitenkin uusi 2 momentti"

    with pytest.raises(ValueError, match="cannot be fully_classified"):
        RegexRecognitionCoverage(
            coverage_id="fi:regex:bad-status",
            jurisdiction="fi",
            recognizer_id="fi_insert_subsection_fallback",
            owner_phase="surface_syntax_frontend",
            source_artifact_id="2020/1",
            source_text_hash=regex_source_text_hash(text),
            matched_span=(0, len(text)),
            coverage_status=REGEX_RECOGNITION_FULLY_CLASSIFIED,
            ignored_spans=(
                {
                    "span": (17, 27),
                    "classification": "unclassified",
                    "text_preview": "kuitenkin ",
                    "could_alter_meaning": True,
                },
            ),
        )

    with pytest.raises(ValueError, match="requires an unclassified"):
        RegexRecognitionCoverage(
            coverage_id="fi:regex:bad-empty-gap",
            jurisdiction="fi",
            recognizer_id="fi_insert_subsection_fallback",
            owner_phase="surface_syntax_frontend",
            source_artifact_id="2020/1",
            source_text_hash=regex_source_text_hash(text),
            matched_span=(0, len(text)),
            coverage_status=REGEX_RECOGNITION_UNCLASSIFIED_GAP,
            ignored_spans=(
                {
                    "span": (17, 21),
                    "classification": "drafting_connector",
                    "text_preview": "uusi",
                    "could_alter_meaning": False,
                },
            ),
            required_proofs=("regex_skipped_span_classification",),
        )

    with pytest.raises(ValueError, match="regex_skipped_span_classification"):
        RegexRecognitionCoverage(
            coverage_id="fi:regex:bad-missing-proof",
            jurisdiction="fi",
            recognizer_id="fi_insert_subsection_fallback",
            owner_phase="surface_syntax_frontend",
            source_artifact_id="2020/1",
            source_text_hash=regex_source_text_hash(text),
            matched_span=(0, len(text)),
            coverage_status=REGEX_RECOGNITION_UNCLASSIFIED_GAP,
            ignored_spans=(
                {
                    "span": (17, 27),
                    "classification": "unclassified",
                    "text_preview": "kuitenkin ",
                    "could_alter_meaning": True,
                },
            ),
        )


def test_regex_recognition_coverage_normalizes_mapping_rows() -> None:
    text = "lisätään 5 §:ään uusi 2 momentti"

    report = regex_recognition_coverage_evidence_report(
        {
            "coverage_id": "fi:regex:mapping",
            "jurisdiction": "fi",
            "recognizer_id": "fi_insert_subsection_fallback",
            "owner_phase": "surface_syntax_frontend",
            "source_artifact_id": "2020/1",
            "source_text_hash": regex_source_text_hash(text),
            "matched_span": (0, len(text)),
            "coverage_status": REGEX_RECOGNITION_FULLY_CLASSIFIED,
            "safe_default": "treat_regex_recognition_as_parse_evidence_not_replay_authority",
            "forbidden_shortcuts": ("local_shortcut_guard",),
        },
        jurisdiction="fi",
    ).to_dict()

    row = report["rows"][0]
    assert row["matched_span"] == [0, len(text)]
    assert row["required_proofs"] == []
    assert "local_shortcut_guard" in row["forbidden_shortcuts"]
    assert "bounded_wildcard_as_semantic_proof" in row["forbidden_shortcuts"]


def test_candidate_set_report_rejects_invalid_mapping_rows() -> None:
    with pytest.raises(ValueError, match="candidate_count must be an integer"):
        candidate_set_evidence_report(
            {
                "scope_id": "bad-candidates",
                "candidate_set_kind": "bad",
                "phase": "tooling",
                "rule_id": "bad_rule",
                "reason": "bad row",
                "completeness_status": CANDIDATE_SET_COMPLETE,
                "candidate_count": "1",
                "missing_candidate_count": 0,
            },
            jurisdiction="fi",
        )


def test_candidate_set_report_projects_to_proof_surface_rows() -> None:
    report = candidate_set_evidence_report(
        CandidateSetCoverage(
            scope_id="fi:demo:source-unit-enumeration",
            candidate_set_kind="fi_strict_report_source_unit_enumeration",
            phase="source_unit_enumeration",
            rule_id="fi_source_unit_enumeration_complete",
            reason="declared source units were enumerated",
            completeness_status=CANDIDATE_SET_COMPLETE,
            candidate_count=1,
            candidate_ids=("source-unit:1",),
            missing_candidate_count=0,
        ),
        jurisdiction="fi",
    )

    proof_surface = proof_surface_from_evidence_report(report).to_dict()

    assert proof_surface["surface_kind"] == "candidate_set_coverage"
    assert proof_surface["claim_flags"]["candidate_effect_claims"] is False
    assert proof_surface["rows"][0]["row_id"] == "fi:demo:source-unit-enumeration"
    assert proof_surface["rows"][0]["subject_id"] == "fi:demo:source-unit-enumeration"
    assert proof_surface["rows"][0]["row_kind"] == "candidate_set_coverage"
    assert proof_surface["rows"][0]["proof_status"] == "complete"
    assert proof_surface["rows"][0]["proof_refs"] == ["fi_source_unit_enumeration_complete"]


def test_proof_surface_synthesizes_scope_sensitive_candidate_set_row_ids() -> None:
    report = EvidenceSurfaceReport(
        jurisdiction="fi",
        report_kind="raw_candidate_set_projection",
        schema="test.raw_candidate_set_projection.v1",
        truth_claim="raw candidate-set rows without explicit row ids",
        replay_claims=False,
        canonical_effect_claims=False,
        candidate_effect_claims=False,
        dry_run_claims=False,
        agreement_claims=False,
        rows=(
            {
                "surface": "candidate_set_coverage",
                "candidate_set_kind": "fi_strict_report_operation_cue_coverage",
                "scope_id": "fi:demo:operation-cue-coverage:a",
                "completeness_status": "complete",
            },
            {
                "surface": "candidate_set_coverage",
                "candidate_set_kind": "fi_strict_report_operation_cue_coverage",
                "scope_id": "fi:demo:operation-cue-coverage:b",
                "completeness_status": "complete",
            },
        ),
    )

    proof_surface = proof_surface_from_evidence_report(report).to_dict()
    row_ids = [row["row_id"] for row in proof_surface["rows"]]

    assert len(row_ids) == 2
    assert len(set(row_ids)) == 2
    assert all(row_id.startswith("candidate-set:") for row_id in row_ids)


def test_candidate_set_coverage_rejects_partial_promotion() -> None:
    with pytest.raises(ValueError, match="next_promotion_allowed"):
        CandidateSetCoverage(
            scope_id="scope",
            candidate_set_kind="kind",
            phase="tooling",
            rule_id="rule",
            reason="bad promotion",
            completeness_status=CANDIDATE_SET_TRUNCATED,
            candidate_count=1,
            missing_candidate_count=1,
            next_promotion_allowed=True,
        )


def test_candidate_set_coverage_complete_requires_no_missing_candidates() -> None:
    with pytest.raises(ValueError, match="missing_candidate_count=0"):
        CandidateSetCoverage(
            scope_id="scope",
            candidate_set_kind="kind",
            phase="tooling",
            rule_id="rule",
            reason="bad complete status",
            completeness_status=CANDIDATE_SET_COMPLETE,
            candidate_count=1,
            missing_candidate_count=1,
        )


def test_processing_status_validates_degraded_blockers() -> None:
    assert ProcessingStatus(kind="partial", blockers=cast(Any, ["missing.source"])).blockers == (
        "missing.source",
    )

    with pytest.raises(ValueError, match="requires at least one blocker"):
        ProcessingStatus(kind="partial")

    with pytest.raises(ValueError, match="must not carry blockers"):
        ProcessingStatus(kind="complete", blockers=("unexpected",))


def test_artifact_envelope_validates_identity_fields() -> None:
    with pytest.raises(ValueError, match="schema"):
        ArtifactEnvelope(schema="", producer="tests", version="1", payload={})


def test_replay_summary_to_dict_is_json_friendly() -> None:
    summary = ReplaySummary(
        jurisdiction="no",
        base_id="no/lov/2005-05-20-28",
        as_of="2026-03-29",
        amendment_count=3,
        applied_count=2,
        op_count=5,
        steps=(
            ReplayAmendmentStep(source_id="2006-01-01-1", amendment_status="applied", op_count=2),
            ReplayAmendmentStep(source_id="2007-01-01-2", amendment_status="skipped", op_count=0),
        ),
        text_view=ReplayTextView(content="hello"),
    )

    data = summary.to_dict()

    assert data["jurisdiction"] == "no"
    assert data["steps"][0]["source_id"] == "2006-01-01-1"
    assert data["text_view"]["content"] == "hello"


def test_replay_contracts_reject_invalid_envelope_shapes() -> None:
    with pytest.raises(ValueError, match="ReplayAmendmentStep.source_id"):
        ReplayAmendmentStep(source_id="")

    with pytest.raises(ValueError, match="op_count"):
        ReplayAmendmentStep(source_id="source", op_count=-1)

    with pytest.raises(ValueError, match="ReplayTextView.format"):
        ReplayTextView(format="")

    with pytest.raises(ValueError, match="ReplaySummary.as_of"):
        ReplaySummary(jurisdiction="no", base_id="base", as_of="")

    with pytest.raises(ValueError, match="divergence_count"):
        ReplaySummary(jurisdiction="no", base_id="base", as_of="2026-01-01", divergence_count=-1)

    with pytest.raises(ValueError, match="op_count"):
        ReplaySummary(
            jurisdiction="no",
            base_id="base",
            as_of="2026-01-01",
            op_count=1,
            steps=(ReplayAmendmentStep(source_id="source", op_count=2),),
        )

    with pytest.raises(ValueError, match="amendment_count"):
        ReplaySummary(
            jurisdiction="no",
            base_id="base",
            as_of="2026-01-01",
            amendment_count=1,
            steps=(
                ReplayAmendmentStep(source_id="source-1"),
                ReplayAmendmentStep(source_id="source-2"),
            ),
        )

    with pytest.raises(ValueError, match="step_index"):
        ReplayCheckpoint(
            parent_id="base",
            amendment_id="amending",
            step_index=1,
            total_steps=1,
            serialize_text=lambda: "",
        )


def test_replay_contracts_freeze_detail_and_normalize_steps() -> None:
    step_detail = {"events": ["applied"]}
    step = ReplayAmendmentStep(source_id="source", detail=step_detail)
    steps = [step]

    summary = ReplaySummary(
        jurisdiction="no",
        base_id="base",
        as_of="2026-01-01",
        steps=cast(Any, steps),
        detail={"nested": {"ids": ["source"]}},
    )

    steps.clear()
    step_detail["events"].append("mutated")

    assert summary.steps == (step,)
    assert isinstance(step.detail, FrozenDict)
    assert step.detail["events"] == ("applied",)
    assert summary.detail["nested"]["ids"] == ("source",)


def test_verify_summary_to_dict_embeds_nested_records() -> None:
    summary = VerifySummary(
        jurisdiction="ee",
        base_id="113032019003",
        as_of="2022-06-01",
        consistent=False,
        issue_count=1,
        divergence_count=1,
        issues=(VerifyIssue(code="parse.bad", message="bad parse", stage="parse"),),
        divergences=(
            DivergenceRecord(
                address="section:1",
                kind="MISMATCH",
                replay_text="a",
                oracle_text="b",
                score=0.5,
                touched=True,
            ),
        ),
        coverage=CoverageAttribution(
            touched_divergence_count=1,
            untouched_divergence_count=0,
        ),
    )

    data = summary.to_dict()

    assert data["issues"][0]["code"] == "parse.bad"
    assert data["divergences"][0]["address"] == "section:1"
    assert data["coverage"]["touched_divergence_count"] == 1


def test_verify_contracts_reject_invalid_envelope_shapes() -> None:
    with pytest.raises(ValueError, match="VerifyIssue.code"):
        VerifyIssue(code="", message="bad")

    with pytest.raises(ValueError, match="severity"):
        VerifyIssue(code="parse.bad", message="bad", severity=cast(Any, "fatal"))

    with pytest.raises(ValueError, match="score"):
        DivergenceRecord(address="section:1", kind="MISMATCH", score=1.5)

    with pytest.raises(ValueError, match="rule_id"):
        FilteredDivergenceRecord(
            divergence=DivergenceRecord(address="section:1", kind="MISMATCH"),
            rule_id="",
            reason="covered by child",
        )

    with pytest.raises(ValueError, match="touched_path_count"):
        CoverageAttribution(touched_path_count=-1)

    with pytest.raises(ValueError, match="jurisdiction"):
        VerifySummary(jurisdiction="", base_id="base")

    with pytest.raises(ValueError, match="issue_count"):
        VerifySummary(
            jurisdiction="ee",
            base_id="base",
            issue_count=2,
            issues=(VerifyIssue(code="parse.bad", message="bad parse"),),
        )

    with pytest.raises(ValueError, match="consistent=True"):
        VerifySummary(
            jurisdiction="ee",
            base_id="base",
            consistent=True,
            divergences=(DivergenceRecord(address="section:1", kind="MISMATCH"),),
        )


def test_verify_contracts_freeze_detail_and_normalize_lanes() -> None:
    issue_detail = {"paths": ["section:1"]}
    divergence_detail = {"rules": ["oracle_projection"]}
    coverage_detail = {"sources": ["op-1"]}
    issue = VerifyIssue(code="parse.bad", message="bad parse", detail=issue_detail)
    divergence = DivergenceRecord(address="section:1", kind="MISMATCH", detail=divergence_detail)
    coverage = CoverageAttribution(detail=coverage_detail)
    issues = [issue]
    divergences = [divergence]

    summary = VerifySummary(
        jurisdiction="ee",
        base_id="base",
        issues=cast(Any, issues),
        divergences=cast(Any, divergences),
        coverage=coverage,
        detail={"summary": {"ids": ["base"]}},
    )

    issues.clear()
    divergences.clear()
    issue_detail["paths"].append("mutated")
    divergence_detail["rules"].append("mutated")
    coverage_detail["sources"].append("mutated")

    assert summary.issues == (issue,)
    assert summary.divergences == (divergence,)
    assert isinstance(issue.detail, FrozenDict)
    assert issue.detail["paths"] == ("section:1",)
    assert divergence.detail["rules"] == ("oracle_projection",)
    assert coverage.detail["sources"] == ("op-1",)
    assert summary.detail["summary"]["ids"] == ("base",)


def test_divergence_partition_preserves_filtered_rule_evidence() -> None:
    divergence = DivergenceRecord(address="section:1", kind="MISMATCH")
    primary = [divergence]
    filtered = [
        FilteredDivergenceRecord(
            divergence=divergence,
            rule_id="verify.prefix_descendant_suppressed",
            reason="parent divergence covered by child divergence",
        )
    ]

    partition = DivergencePartition(
        primary=cast(Any, primary),
        filtered=cast(Any, filtered),
    )
    primary.clear()
    filtered.clear()

    assert partition.primary == (divergence,)
    assert partition.filtered[0].divergence is divergence
    assert partition.filtered[0].rule_id == "verify.prefix_descendant_suppressed"

    with pytest.raises(ValueError, match="filtered must contain FilteredDivergenceRecord"):
        DivergencePartition(primary=(), filtered=cast(Any, ("not-a-filtered-record",)))


def test_evidence_summary_to_dict_preserves_tuple_fields() -> None:
    summary = EvidenceSummary(
        jurisdiction="fi",
        base_id="1991/1707",
        primary_tier="oracle_ready",
        claim_count=3,
        tiers=("oracle_ready", "strict_fail"),
        claim_kinds=("oracle_stale", "html_xml_drift"),
        trigger_sources=("frontend",),
        artifact_families=("oracle",),
    )

    data = summary.to_dict()

    assert data["primary_tier"] == "oracle_ready"
    assert data["tiers"] == ("oracle_ready", "strict_fail")
    assert data["claim_kinds"] == ("oracle_stale", "html_xml_drift")


def test_corpus_operation_evidence_row_to_dict_preserves_unsupported_status() -> None:
    row = CorpusOperationEvidenceRow(
        row_id="row-1",
        frontend_id="open_law_maryland",
        source_artifact_id="editorial-actions/x.xml",
        effect_family="expire",
        evidence_status=CorpusRowStatus.UNSUPPORTED,
        blocking=True,
        strict_disposition="block",
        quirks_disposition=QuirksDisposition.RECORD_UNSUPPORTED,
        finding_ids=("open_law_expire_lifecycle_not_replayed",),
    )

    data = row.to_dict()

    assert data["evidence_status"] == "unsupported"
    assert data["finding_ids"] == ("open_law_expire_lifecycle_not_replayed",)
    assert validate_corpus_operation_evidence_row(data) == ()


def test_corpus_finding_evidence_row_to_dict_is_json_friendly() -> None:
    row = CorpusFindingEvidenceRow(
        finding_id="row-1:finding",
        frontend_id="open_law_maryland",
        family="unsupported",
        rule_id="open_law_expire_lifecycle_not_replayed",
        phase="lifecycle",
        message="recorded",
        strict_disposition="block",
        quirks_disposition=QuirksDisposition.RECORD,
        blocking=True,
        evidence={"path": ("a", "b")},
    )

    data = row.to_dict()

    assert data["rule_id"] == "open_law_expire_lifecycle_not_replayed"
    assert data["evidence"] == {"path": ("a", "b")}
    assert validate_corpus_finding_evidence_row(data) == ()


def test_evidence_contracts_freeze_detail_lanes() -> None:
    summary = EvidenceSummary(
        jurisdiction="fi",
        base_id="1991/1707",
        tiers=cast(Any, ["oracle_ready"]),
        detail={"nested": {"ids": ["summary"]}},
    )
    op_detail = {"reason": "unsupported", "ids": ["row-1"]}
    op_row = CorpusOperationEvidenceRow(
        row_id="row-1",
        frontend_id="starter",
        source_artifact_id="act.xml",
        evidence_status=CorpusRowStatus.UNSUPPORTED,
        blocking=True,
        strict_disposition="block",
        quirks_disposition=QuirksDisposition.RECORD,
        detail=op_detail,
    )
    finding_evidence = {"path": ["a", "b"]}
    finding_row = CorpusFindingEvidenceRow(
        finding_id="row-1:finding",
        frontend_id="starter",
        family="unsupported",
        rule_id="starter.rule",
        phase="parse",
        message="recorded",
        strict_disposition="record",
        quirks_disposition=QuirksDisposition.RECORD,
        evidence=finding_evidence,
    )

    op_detail["ids"].append("mutated")
    finding_evidence["path"].append("mutated")

    assert summary.tiers == ("oracle_ready",)
    assert summary.detail["nested"]["ids"] == ("summary",)
    assert isinstance(op_row.detail, FrozenDict)
    assert op_row.detail["ids"] == ("row-1",)
    assert finding_row.evidence["path"] == ("a", "b")


def test_corpus_operation_evidence_validation_rejects_unexplained_non_claim() -> None:
    issues = validate_corpus_operation_evidence_row({
        "row_id": "row-1",
        "frontend_id": "starter",
        "source_artifact_id": "act.xml",
        "evidence_status": "unsupported",
        "blocking": True,
        "strict_disposition": "record",
        "quirks_disposition": "record",
        "finding_ids": (),
        "detail": {},
    })

    assert "unsupported row must carry finding_ids or reason-bearing detail" in issues
    assert "blocking row must have blocking strict_disposition" in issues


def test_corpus_operation_evidence_row_rejects_invalid_construction() -> None:
    with pytest.raises(ValueError, match="unsupported row must carry finding_ids"):
        CorpusOperationEvidenceRow(
            row_id="row-1",
            frontend_id="starter",
            source_artifact_id="act.xml",
            evidence_status=CorpusRowStatus.UNSUPPORTED,
            blocking=True,
            strict_disposition="block",
            quirks_disposition=QuirksDisposition.RECORD,
        )


def test_corpus_operation_evidence_validation_rejects_blocking_match_without_justification() -> None:
    issues = validate_corpus_operation_evidence_row({
        "row_id": "row-1",
        "frontend_id": "starter",
        "source_artifact_id": "act.xml",
        "evidence_status": "matched",
        "blocking": True,
        "strict_disposition": "block",
        "quirks_disposition": "record",
        "finding_ids": ("positive_projection",),
        "detail": {},
    })

    assert issues == ("matched row cannot be blocking without blocking_justification detail",)


def test_corpus_finding_evidence_row_rejects_invalid_construction() -> None:
    with pytest.raises(ValueError, match="finding_id is required"):
        CorpusFindingEvidenceRow(
            finding_id="",
            frontend_id="starter",
            family="unsupported",
            rule_id="starter.rule",
            phase="P1",
            message="bad",
            strict_disposition="record",
            quirks_disposition=QuirksDisposition.RECORD,
        )


def test_corpus_finding_evidence_validation_rejects_bad_shapes() -> None:
    issues = validate_corpus_finding_evidence_row({
        "finding_id": "",
        "frontend_id": "starter",
        "rule_id": "starter.rule",
        "phase": "P1",
        "message": "bad",
        "strict_disposition": "record",
        "quirks_disposition": "record",
        "blocking": "yes",
        "evidence": [],
        "related_row_ids": "row-1",
    })

    assert "finding_id is required" in issues
    assert "blocking must be a boolean" in issues
    assert "evidence must be a mapping" in issues
    assert "related_row_ids must be a list or tuple" in issues


def test_evidence_rule_ids_extracts_stable_detail_rule_ids() -> None:
    row = CorpusOperationEvidenceRow(
        row_id="row-1",
        frontend_id="new_zealand",
        source_artifact_id="act_public_2020_1",
        evidence_status=CorpusRowStatus.ACCEPTED,
        strict_disposition="candidate_only",
        quirks_disposition=QuirksDisposition.CANDIDATE_ONLY,
        finding_ids=("nz_existing_finding",),
        detail={
            "reason": "candidate canonical effect emitted but not replayed",
            "blocking_rule_id": "nz_effect_readiness_amendment_semantics_not_extracted",
            "operation_target_blocking_rule_id": "nz_target_address_duplicate_source_path",
            "effect_blocking_rule_id": "nz_operation_surface_effect_lowering_not_implemented",
            "candidate_witness_rule_id": "nz_repeal_candidate_from_history_note_payload_witness",
            "preflight_blocking_rule_id": "nz_effect_preflight_candidate_operation_missing",
            "declared_recovery_rule_ids": ["section_move_replace_destination_rebind"],
            "declared_migration_rule_ids": (),
            "matched_allowance_rule_ids": ("section_materialization_root_move_destination_rebind",),
        },
    )

    assert evidence_rule_ids(row.to_dict()) == {
        "nz_existing_finding",
        "nz_effect_readiness_amendment_semantics_not_extracted",
        "nz_target_address_duplicate_source_path",
        "nz_operation_surface_effect_lowering_not_implemented",
        "nz_repeal_candidate_from_history_note_payload_witness",
        "nz_effect_preflight_candidate_operation_missing",
        "section_move_replace_destination_rebind",
        "section_materialization_root_move_destination_rebind",
    }


def test_evidence_rule_ids_allows_stable_reason_rule_ids() -> None:
    row = CorpusOperationEvidenceRow(
        row_id="row-1",
        frontend_id="starter",
        source_artifact_id="act.xml",
        evidence_status=CorpusRowStatus.UNSUPPORTED,
        blocking=True,
        strict_disposition="block",
        quirks_disposition=QuirksDisposition.RECORD,
        detail={"reason": "starter.unsupported.v1"},
    )

    assert evidence_rule_ids(row.to_dict()) == {"starter.unsupported.v1"}


def test_evidence_rule_ids_scans_detail_and_evidence_maps_when_both_exist() -> None:
    row = {
        "row_id": "row-1",
        "frontend_id": "starter",
        "source_artifact_id": "act.xml",
        "evidence_status": "unsupported",
        "strict_disposition": "block",
        "quirks_disposition": "record",
        "detail": {"candidate_witness_rule_id": "starter.detail_witness"},
        "evidence": {"blocking_rule_id": "starter.evidence_blocker"},
    }

    assert evidence_rule_ids(row) == {"starter.detail_witness", "starter.evidence_blocker"}


def test_evidence_row_kind_classifies_shared_evidence_rows() -> None:
    assert evidence_row_kind({"row_id": "operation-1"}) == "operation"
    assert evidence_row_kind({"finding_id": "finding-1"}) == "finding"
    assert evidence_row_kind({"rule_id": "starter.unsupported.v1"}) == "finding"


def test_to_wire_jsonable_normalizes_nested_runtime_shapes() -> None:
    class Weird:
        @override
        def __repr__(self) -> str:
            return "<weird>"

    got = to_wire_jsonable({
        "tuple": (1, 2),
        "set": {"a", "b"},
        "nested": {"value": Weird()},
    })

    assert got["tuple"] == [1, 2]
    assert sorted(got["set"]) == ["a", "b"]
    assert got["nested"]["value"] == "<weird>"


def test_artifact_envelope_to_wire_jsonable_serializes_schema_and_status() -> None:
    envelope = ArtifactEnvelope(
        schema="lawvm.test",
        producer="tests",
        version="1",
        payload={
            "body": {"kind": "content", "text": "hello"},
            "tags": {"a", "b"},
        },
        processing_status=ProcessingStatus(kind="partial", blockers=("missing.source",)),
    )

    got = to_wire_jsonable(envelope)

    assert got["schema"] == "lawvm.test"
    assert got["producer"] == "tests"
    assert got["version"] == "1"
    assert got["payload"]["body"]["text"] == "hello"
    assert sorted(got["payload"]["tags"]) == ["a", "b"]
    assert got["processing_status"] == {"kind": "partial", "blockers": ["missing.source"]}

from __future__ import annotations

import pytest

from lawvm.core.proof_gate_summary import (
    ProofGateSummary,
    proof_gate_summary_from_surfaces,
)


def test_proof_gate_summary_buckets_frontiers_without_replay_authority() -> None:
    summary = proof_gate_summary_from_surfaces(
        schema="lawvm.test.proof_gate_summary.v1",
        scope="unit-test",
        closed=False,
        failed_gates=("failed_ops_present",),
        unowned_counts={"unproved_mutation_boundary_proofs": 2, "zero": 0},
        manual_or_other_frontier_work_items=[
            {
                "owner_phase": "apply",
                "frontier_status": "failed_operation_frontier",
                "required_claim_kind": "fi.v1.FAILED_OPERATION_RESOLUTION",
            },
            {
                "owner_phase": "oracle",
                "frontier_status": "comparison_frontier",
                "required_claim_kind": "oracle_residual_review",
            },
        ],
        coverage_frontier_work_items=[
            {
                "owner_phase": "candidate_set",
                "frontier_status": "partial_candidate_set_frontier",
                "required_claim_kind": "fi.v1.OPERATION_CUE_EXHAUSTIVENESS_CERTIFICATE",
            },
        ],
        candidate_set_certificates=[
            {
                "candidate_set_kind": "fi_strict_report_operation_cue_coverage",
                "completeness_status": "partial",
            }
        ],
        evidence_summary={
            "source_completeness": {
                "chain_length": 5,
                "dates_available": 3,
                "missing_dates": 2,
                "missing_sources": 1,
                "source_available": 4,
            },
            "source_unit_coverage_status_counts": {
                "blocked": 1,
                "covered": 3,
                "unclassified": 2,
            },
            "potential_operation_classification_counts": {
                "compiled": 2,
                "blocked": 1,
                "unclassified": 4,
            },
            "regex_recognition_coverage_status_counts": {"unclassified_gap": 3},
            "regex_recognition_unclassified_gap_count": 3,
            "temporal_resolution_status_counts": {
                "fixed_date": 4,
                "future_effective_date": 1,
                "unknown_effective_date": 2,
                "unresolved_contingent": 1,
            },
        },
        manual_claim_kind_prefixes=("fi.v1.",),
    ).to_dict()

    assert summary["open_gate_signal_count"] == 25
    assert summary["ownership_failed_gate_counts"] == {"failed_ops_present": 1}
    assert summary["unowned_counts"] == {"unproved_mutation_boundary_proofs": 2}
    assert summary["frontier_work_item_count"] == 3
    assert summary["manual_claim_frontier_count"] == 1
    assert summary["coverage_frontier_count"] == 1
    assert summary["other_frontier_count"] == 1
    assert summary["required_claim_kind_counts"] == {
        "fi.v1.FAILED_OPERATION_RESOLUTION": 1,
        "fi.v1.OPERATION_CUE_EXHAUSTIVENESS_CERTIFICATE": 1,
        "oracle_residual_review": 1,
    }
    assert summary["manual_frontier_required_claim_kind_counts"] == {
        "fi.v1.FAILED_OPERATION_RESOLUTION": 1,
    }
    assert summary["coverage_frontier_required_claim_kind_counts"] == {
        "fi.v1.OPERATION_CUE_EXHAUSTIVENESS_CERTIFICATE": 1,
    }
    assert summary["other_frontier_required_claim_kind_counts"] == {
        "oracle_residual_review": 1,
    }
    assert summary["candidate_set_completeness_counts"] == {"partial": 1}
    assert summary["source_completeness_counts"] == {
        "chain_length": 5,
        "dates_available": 3,
        "missing_dates": 2,
        "missing_sources": 1,
        "source_available": 4,
    }
    assert summary["source_completeness_missing_count"] == 3
    assert summary["source_unit_coverage_status_counts"] == {
        "blocked": 1,
        "covered": 3,
        "unclassified": 2,
    }
    assert summary["source_unit_unresolved_count"] == 3
    assert summary["potential_operation_classification_counts"] == {
        "blocked": 1,
        "compiled": 2,
        "unclassified": 4,
    }
    assert summary["potential_operation_unresolved_count"] == 5
    assert summary["regex_recognition_coverage_status_counts"] == {
        "unclassified_gap": 3,
    }
    assert summary["regex_recognition_unclassified_gap_count"] == 3
    assert summary["temporal_resolution_status_counts"] == {
        "fixed_date": 4,
        "future_effective_date": 1,
        "unknown_effective_date": 2,
        "unresolved_contingent": 1,
    }
    assert summary["temporal_resolution_unresolved_count"] == 4
    assert "replay_authorization" in summary["does_not_claim"]
    assert "source_chain_completeness" in summary["does_not_claim"]
    assert "regex_recognition_gap_closure" in summary["does_not_claim"]
    assert "source_unit_unresolved_closure" in summary["does_not_claim"]
    assert "potential_operation_unresolved_closure" in summary["does_not_claim"]
    assert "temporal_resolution_closure" in summary["does_not_claim"]


def test_proof_gate_summary_requires_replay_authorization_disclaimer() -> None:
    with pytest.raises(ValueError, match="replay_authorization"):
        ProofGateSummary(
            schema="lawvm.test.proof_gate_summary.v1",
            scope="unit-test",
            closed=True,
            open_gate_signal_count=0,
            ownership_failed_gate_count=0,
            does_not_claim=("proof_closure",),
        )


def test_proof_gate_summary_accepts_single_mapping_inputs() -> None:
    summary = proof_gate_summary_from_surfaces(
        schema="lawvm.test.proof_gate_summary.v1",
        scope="unit-test",
        closed=False,
        failed_gates=(),
        manual_or_other_frontier_work_items={
            "owner_phase": "source",
            "frontier_status": "manual_claim_needed",
            "required_claim_kind": "fi.v1.SOURCE_UNIT_ENUMERATION_CERTIFICATE",
        },
        candidate_set_certificates={
            "candidate_set_kind": "fi_strict_report_source_unit_enumeration",
            "completeness_status": "partial",
        },
        manual_claim_kind_prefixes=("fi.v1.",),
    ).to_dict()

    assert summary["manual_claim_frontier_count"] == 1
    assert summary["incomplete_candidate_set_count"] == 1
    assert summary["open_gate_signal_count"] == 2


def test_proof_gate_summary_rejects_boolean_count_values() -> None:
    with pytest.raises(ValueError, match="count values"):
        proof_gate_summary_from_surfaces(
            schema="lawvm.test.proof_gate_summary.v1",
            scope="unit-test",
            closed=False,
            failed_gates=(),
            unowned_counts={"invalid_bool_count": True},
        )

    with pytest.raises(ValueError, match="count values"):
        ProofGateSummary(
            schema="lawvm.test.proof_gate_summary.v1",
            scope="unit-test",
            closed=False,
            open_gate_signal_count=0,
            ownership_failed_gate_count=0,
            unowned_counts={"invalid_bool_count": False},
        )

    with pytest.raises(ValueError, match="regex_recognition_unclassified_gap_count"):
        proof_gate_summary_from_surfaces(
            schema="lawvm.test.proof_gate_summary.v1",
            scope="unit-test",
            closed=False,
            failed_gates=(),
            evidence_summary={"regex_recognition_unclassified_gap_count": True},
        )

    with pytest.raises(ValueError, match="source_unit_coverage_status_counts"):
        proof_gate_summary_from_surfaces(
            schema="lawvm.test.proof_gate_summary.v1",
            scope="unit-test",
            closed=False,
            failed_gates=(),
            evidence_summary={"source_unit_coverage_status_counts": "bad"},
        )

    with pytest.raises(ValueError, match="source_completeness"):
        proof_gate_summary_from_surfaces(
            schema="lawvm.test.proof_gate_summary.v1",
            scope="unit-test",
            closed=False,
            failed_gates=(),
            evidence_summary={"source_completeness": {"missing_sources": -1}},
        )

    with pytest.raises(ValueError, match="temporal_resolution_status_counts"):
        proof_gate_summary_from_surfaces(
            schema="lawvm.test.proof_gate_summary.v1",
            scope="unit-test",
            closed=False,
            failed_gates=(),
            evidence_summary={"temporal_resolution_status_counts": "bad"},
        )

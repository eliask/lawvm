from __future__ import annotations

import json

from scripts import uk_oracle_suspect_candidates as candidates


def _write_report(tmp_path, rows):
    path = tmp_path / "uk_broad.report.json"
    path.write_text(json.dumps({"rows": rows}))
    return path


def test_retained_repeal_oracle_branch_is_high_confidence(tmp_path) -> None:
    report = _write_report(
        tmp_path,
        [
            {
                "statute_id": "ukpga/1992/20",
                "triage_bucket": "retained_repeal_oracle_branch",
                "aligned": 93.33,
                "n_replay": 28,
                "n_oracle": 30,
                "n_only_in_replayed": 0,
                "n_only_in_oracle": 2,
                "replay_only_eid_samples": [],
                "oracle_only_eid_samples": ["section-6", "section-7"],
                "oracle_only_uncompiled_addition_eid_samples": ["section-7"],
                "oracle_only_uncompiled_addition_change_ids": ["d7p1"],
                "retained_repeal_oracle_targets": ["section-6", "section-7"],
                "base_source_status": "available",
                "base_source_locator": "https://www.legislation.gov.uk/ukpga/1992/20/enacted/data.xml",
                "base_source_size": 1234,
                "base_source_number_of_provisions": "42",
                "base_source_has_body": True,
                "base_source_has_schedules": False,
                "base_source_witness_digest_coverage": "artifact_and_preview_digest",
                "base_source_witness": {
                    "source_role": "uk_broad_base_source",
                    "source_status": "available",
                    "locator": "https://www.legislation.gov.uk/ukpga/1992/20/enacted/data.xml",
                    "digest": "base-digest",
                    "preview_digest": "base-preview-digest",
                    "source_lane": "enacted_xml",
                    "bounded_preview": "<Legislation>base</Legislation>",
                },
                "oracle_source_status": "available",
                "oracle_source_locator": "https://www.legislation.gov.uk/ukpga/1992/20/data.xml",
                "oracle_source_size": 567,
                "oracle_source_number_of_provisions": "40",
                "oracle_source_has_body": True,
                "oracle_source_has_schedules": False,
                "oracle_source_witness_digest_coverage": "artifact_and_preview_digest",
                "oracle_source_witness": {
                    "source_role": "uk_broad_oracle_source",
                    "source_status": "available",
                    "locator": "https://www.legislation.gov.uk/ukpga/1992/20/data.xml",
                    "digest": "oracle-digest",
                    "preview_digest": "oracle-preview-digest",
                    "source_lane": "current_xml",
                    "bounded_preview": "<Legislation>oracle</Legislation>",
                },
                "n_effects": 1,
                "n_ops": 1,
                "n_compiled_source_chain_ids": 1,
                "n_manual_frontier_records": 1,
                "n_compile_rejections": 0,
                "n_blocking_compile_rejections": 0,
                "n_mutation_boundary_reports": 1,
                "n_mutation_boundary_unexplained_reports": 0,
                "n_mutation_boundary_unexplained_paths": 0,
                "source_chain_frontier": False,
                "source_chain_frontier_reasons": [],
                "manual_frontier_status_counts": {
                    "deterministic_frontend_supported": 1
                },
                "manual_frontier_authorization_status_counts": {
                    "replay_authorized": 1
                },
                "manual_frontier_rule_counts": {
                    "uk_manual_frontier_deterministic_supported": 1
                },
                "mutation_boundary_proof_rule_counts": {
                    "mutation_boundary_path_set_proved": 1
                },
                "mutation_boundary_proof_status_counts": {"proved": 1},
                "agreement_residual": {
                    "owner_phase": "compare_oracle_classification",
                    "missing_proofs": [],
                    "forbidden_shortcuts": ["oracle_score_as_source_truth"],
                    "safe_default": "classify_residual_without_replay_promotion",
                },
            }
        ],
    )

    rows = candidates.load_candidates(report)

    assert len(rows) == 1
    assert rows[0].statute_id == "ukpga/1992/20"
    assert rows[0].candidate_family == "oracle_retains_source_repealed_state"
    assert rows[0].confidence == "high"
    assert rows[0].rank == 100
    assert rows[0].retained_repeal_targets == ("section-6", "section-7")
    assert rows[0].oracle_only_uncompiled_addition_samples == ("section-7",)
    assert rows[0].oracle_only_uncompiled_addition_change_ids == ("d7p1",)
    assert len(rows[0].source_witnesses) == 2
    assert rows[0].source_witnesses[0].source_role == "uk_broad_base_source"
    assert rows[0].source_witnesses[0].digest == "base-digest"
    assert rows[0].source_witnesses[0].digest_coverage == (
        "artifact_and_preview_digest"
    )
    assert rows[0].source_witnesses[1].source_lane == "current_xml"
    assert rows[0].execution_witness.n_effects == 1
    assert rows[0].execution_witness.n_ops == 1
    assert rows[0].execution_witness.manual_frontier_authorization_status_counts == {
        "replay_authorized": 1
    }
    assert rows[0].execution_witness.mutation_boundary_proof_rule_counts == {
        "mutation_boundary_path_set_proved": 1
    }
    assert rows[0].missing_proofs == ()


def test_oracle_expansion_without_effects_is_source_chain_lead(tmp_path) -> None:
    report = _write_report(
        tmp_path,
        [
            {
                "statute_id": "ukpga/1986/58",
                "triage_bucket": "oracle_expansion_without_effects",
                "aligned": 87.5,
                "n_replay": 14,
                "n_oracle": 16,
                "n_only_in_replayed": 0,
                "n_only_in_oracle": 2,
                "oracle_only_eid_samples": [
                    "schedule-1-crossheading-acts",
                    "schedule-1-crossheading-instruments",
                ],
                "agreement_residual": {
                    "owner_phase": "effect_metadata_frontend",
                    "missing_proofs": ["source_identity"],
                    "forbidden_shortcuts": ["source_or_target_over_promotion"],
                    "safe_default": "classify_residual_without_replay_promotion",
                },
            }
        ],
    )

    rows = candidates.load_candidates(report)

    assert len(rows) == 1
    assert rows[0].candidate_family == "oracle_addition_without_compiled_source_chain"
    assert rows[0].confidence == "source_chain_lead"
    assert rows[0].missing_proofs == ("source_identity",)


def test_manual_and_source_frontier_rows_are_not_oracle_suspect_candidates(tmp_path) -> None:
    report = _write_report(
        tmp_path,
        [
            {
                "statute_id": "ukpga/1970/30",
                "triage_bucket": "manual_compile_frontier_residual",
                "aligned": 1.28,
                "n_only_in_oracle": 77,
                "agreement_residual": {"missing_proofs": ["source_identity"]},
            },
            {
                "statute_id": "ukpga/1857/35",
                "triage_bucket": "source_frontier:base_and_oracle_metadata_only",
                "aligned": 0,
                "n_only_in_oracle": 0,
                "agreement_residual": {"missing_proofs": ["source_identity"]},
            },
            {
                "statute_id": "ukpga/1842/97",
                "triage_bucket": "body_oracle_first_paragraph_sectionization_residual",
                "aligned": 80.0,
                "n_only_in_oracle": 1,
                "agreement_residual": {
                    "missing_proofs": ["topology_or_eid_scheme_reconciliation"]
                },
            },
        ],
    )

    rows = candidates.load_candidates(report)

    assert rows == []


def test_clean_replay_only_row_is_high_confidence(tmp_path) -> None:
    report = _write_report(
        tmp_path,
        [
            {
                "statute_id": "ukpga/2000/1",
                "triage_bucket": "high_fidelity_after_grounding",
                "aligned": 99.0,
                "n_replay": 101,
                "n_oracle": 100,
                "n_only_in_replayed": 1,
                "n_only_in_oracle": 0,
                "n_blocking_compile_rejections": 0,
                "n_mutation_boundary_unexplained_reports": 0,
                "n_mutation_boundary_unexplained_paths": 0,
                "source_chain_frontier_reasons": [],
                "replay_only_eid_samples": ["section-10"],
                "agreement_residual": {
                    "owner_phase": "compare_oracle_classification",
                    "missing_proofs": [],
                    "forbidden_shortcuts": ["candidate_as_replay_authorization"],
                },
            }
        ],
    )

    rows = candidates.load_candidates(report, min_confidence="high")

    assert len(rows) == 1
    assert rows[0].candidate_family == "oracle_missing_source_backed_replay_state"
    assert rows[0].confidence == "high"
    assert rows[0].replay_only_samples == ("section-10",)


def test_output_summary_preserves_forbidden_shortcuts(tmp_path) -> None:
    report = _write_report(
        tmp_path,
        [
            {
                "statute_id": "ukpga/1992/20",
                "triage_bucket": "retained_repeal_oracle_branch",
                "n_only_in_oracle": 1,
                "retained_repeal_oracle_targets": ["section-6"],
                "agreement_residual": {"missing_proofs": []},
            }
        ],
    )

    rows = candidates.load_candidates(report)
    payload = json.loads(candidates._emit_json(rows))

    assert payload["report_kind"] == "uk_oracle_suspect_candidates.v1"
    assert payload["summary"]["candidate_count"] == 1
    assert payload["summary"]["truth_claim"] == (
        "oracle_suspect_candidate_report_not_source_truth"
    )
    assert "candidate_as_replay_authorization" in payload["summary"][
        "forbidden_shortcuts"
    ]

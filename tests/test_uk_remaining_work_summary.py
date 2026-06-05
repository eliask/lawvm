from __future__ import annotations

import json

import pytest

from scripts import uk_remaining_work_summary as remaining


def _write_report(tmp_path, rows, summary=None):
    path = tmp_path / "uk_broad.report.json"
    path.write_text(
        json.dumps(
            {
                "jurisdiction": "uk",
                "report_kind": "uk_broad_baseline_agreement_report",
                "schema": "lawvm.uk_broad_baseline_agreement_report.v1",
                "summary": summary or {},
                "rows": rows,
            }
        )
    )
    return path


def _write_effective_oracle_review(tmp_path, rows):
    path = tmp_path / "uk_effective_oracle_review.json"
    path.write_text(
        json.dumps(
            {
                "jurisdiction": "uk",
                "report_kind": "uk_effective_oracle_review",
                "schema": "lawvm.uk_effective_oracle_review.v1",
                "summary": {},
                "rows": rows,
            }
        )
    )
    return path


def test_rejects_non_broad_baseline_report(tmp_path) -> None:
    path = tmp_path / "other.report.json"
    path.write_text(
        json.dumps(
            {
                "jurisdiction": "fi",
                "report_kind": "uk_broad_baseline_agreement_report",
                "schema": "lawvm.uk_broad_baseline_agreement_report.v1",
                "rows": [],
            }
        )
    )

    with pytest.raises(ValueError, match="not a UK broad-baseline report"):
        remaining.load_remaining_work(path)


def test_remaining_work_groups_rows_by_proof_boundary(tmp_path) -> None:
    report = _write_report(
        tmp_path,
        [
            {
                "statute_id": "ukpga/2000/1",
                "score_status": "scored",
                "triage_bucket": "high_fidelity_after_grounding",
                "aligned": 100.0,
            },
            {
                "statute_id": "ukpga/2000/2",
                "score_status": "scored",
                "triage_bucket": "manual_compile_frontier_residual",
                "aligned": 75.0,
                "n_only_in_oracle": 10,
                "agreement_residual": {
                    "owner_phase": "typed_elaboration",
                    "missing_proofs": [
                        "target_identity",
                        "payload_or_boundary_identity",
                    ],
                },
            },
            {
                "statute_id": "uksi/2009/41",
                "score_status": "scored",
                "triage_bucket": "effect_feed_absent_frontier",
                "aligned": 60.0,
                "n_only_in_oracle": 20,
                "agreement_residual": {
                    "owner_phase": "effect_metadata_frontend",
                    "missing_proofs": ["source_identity"],
                },
            },
            {
                "statute_id": "ukpga/1939/89",
                "score_status": "scored",
                "triage_bucket": "oracle_addition_source_chain_frontier",
                "aligned": 90.0,
                "n_only_in_oracle": 2,
                "agreement_residual": {
                    "owner_phase": "effect_metadata_frontend",
                    "missing_proofs": [
                        "source_identity",
                        "source_chain_completeness",
                    ],
                },
            },
            {
                "statute_id": "ukpga/1850/1",
                "score_status": "source_frontier",
                "triage_bucket": "source_frontier:base_metadata_only",
                "agreement_residual": {
                    "owner_phase": "affecting_source_extraction",
                    "missing_proofs": ["source_identity"],
                },
            },
        ],
        summary={
            "completion_gate_clean": True,
            "active_unclassified_residual_count": 0,
        },
    )

    payload = remaining.load_remaining_work(report)

    assert payload["report_kind"] == "uk_remaining_work_summary.v1"
    assert payload["summary"]["row_count"] == 5
    assert payload["summary"]["scored_count"] == 4
    assert payload["summary"]["source_frontier_count"] == 1
    assert payload["summary"]["completion_gate_clean"] is True
    assert [lane["lane_id"] for lane in payload["lanes"]] == [
        "manual_compilation_frontier",
        "effect_source_footing_gap",
        "source_footing_gap",
    ]
    manual_lane = payload["lanes"][0]
    assert manual_lane["owner_phase"] == "typed_elaboration"
    assert manual_lane["mean_aligned"] == 75.0
    assert manual_lane["missing_proof_counts"] == {
        "payload_or_boundary_identity": 1,
        "target_identity": 1,
    }
    assert manual_lane["sample_statutes"] == ("ukpga/2000/2",)
    assert "manual_frontier_as_replay_authorization" in manual_lane[
        "forbidden_shortcuts"
    ]
    assert payload["lanes"][1]["triage_bucket_counts"] == {
        "effect_feed_absent_frontier": 1,
        "oracle_addition_source_chain_frontier": 1,
    }
    assert "work_lane_as_replay_authorization" in payload["forbidden_shortcuts"]


def test_lane_filtered_items_are_non_executable_work_queue_rows(tmp_path) -> None:
    report = _write_report(
        tmp_path,
        [
            {
                "statute_id": "uksi/2009/41",
                "score_status": "scored",
                "triage_bucket": "effect_feed_absent_frontier",
                "aligned": 60.0,
                "n_replay": 12,
                "n_oracle": 20,
                "n_only_in_oracle": 8,
                "source_chain_frontier_reasons": ["effect_feed_pages_absent"],
                "oracle_only_eid_samples": ["regulation-10"],
                "base_source_status": "available",
                "base_source_locator": "https://www.legislation.gov.uk/uksi/2009/41/enacted/data.xml",
                "oracle_source_status": "available",
                "oracle_source_locator": "https://www.legislation.gov.uk/uksi/2009/41/data.xml",
                "agreement_residual": {
                    "owner_phase": "effect_metadata_frontend",
                    "missing_proofs": ["source_identity"],
                },
            },
            {
                "statute_id": "ukpga/2000/2",
                "score_status": "scored",
                "triage_bucket": "manual_compile_frontier_residual",
                "aligned": 75.0,
                "n_only_in_oracle": 10,
                "agreement_residual": {
                    "owner_phase": "typed_elaboration",
                    "missing_proofs": ["target_identity"],
                },
            },
        ],
    )

    payload = remaining.load_remaining_work(
        report,
        include_items=True,
        item_lane_ids=frozenset({"effect_source_footing_gap"}),
    )

    assert payload["summary"]["item_count"] == 1
    assert payload["summary"]["item_lane_filter"] == ["effect_source_footing_gap"]
    item = payload["items"][0]
    assert item["work_item_id"] == "uk-remaining:effect_source_footing_gap:uksi/2009/41"
    assert item["status"] == "non_executable_work_item"
    assert item["executable"] is False
    assert item["replay_authorized"] is False
    assert item["lane_id"] == "effect_source_footing_gap"
    assert item["owner_phase"] == "effect_metadata_frontend"
    assert item["source_chain_frontier_reasons"] == ("effect_feed_pages_absent",)
    assert item["missing_proofs"] == ("source_identity",)
    assert item["oracle_only_eid_samples"] == ("regulation-10",)
    assert item["base_source_status"] == "available"
    assert "effect_absence_as_replay_permission" in item["forbidden_shortcuts"]
    authorization = item["execution_authorization"]
    assert authorization["executable"] is False
    assert authorization["replay_authorized"] is False
    assert authorization["authorization_status"] == "non_executable_work_item"
    assert (
        authorization["authorization_rule_id"]
        == "uk_remaining_work_effect_source_footing_gap_non_executable"
    )
    assert authorization["required_proofs"] == ["source_identity"]
    assert (
        authorization["safe_default"]
        == "classify_or_queue_without_replay_promotion"
    )
    frontier = item["frontier_work_item"]
    assert frontier["work_item_id"] == item["work_item_id"]
    assert frontier["jurisdiction"] == "uk"
    assert frontier["frontier_family"] == "effect_source_footing_gap"
    assert frontier["frontier_status"] == "effect_feed_absent_frontier"
    assert frontier["executable"] is False
    assert frontier["replay_authorized"] is False
    assert frontier["authorization_status"] == "non_executable_work_item"
    assert frontier["required_proofs"] == ["source_identity"]
    assert frontier["source_witness"]["base"]["source_status"] == "available"
    assert (
        frontier["source_witness"]["base"]["locator"]
        == "https://www.legislation.gov.uk/uksi/2009/41/enacted/data.xml"
    )
    assert frontier["target_witness"]["oracle_only_eid_samples"] == ["regulation-10"]
    assert frontier["compare_witness"]["n_only_in_oracle"] == 8
    candidate_set = item["candidate_set_certificate"]
    assert candidate_set["scope_id"] == item["work_item_id"]
    assert (
        candidate_set["candidate_set_kind"]
        == "remaining_work_residual_eid_samples"
    )
    assert candidate_set["phase"] == "effect_metadata_frontend"
    assert candidate_set["completeness_status"] == "partial"
    assert candidate_set["candidate_count"] == 8
    assert candidate_set["candidate_ids"] == ["regulation-10"]
    assert candidate_set["missing_candidate_count"] == 7
    assert candidate_set["selected_candidate_ids"] == []
    assert candidate_set["blocker_counts"] == {"source_identity": 1}
    assert candidate_set["blocker_families"] == ["source_identity"]
    assert candidate_set["next_promotion_allowed"] is False
    assert candidate_set["next_promotion_requires"] == ["source_identity"]


def test_manual_frontier_items_export_replay_neutral_evidence_counters(
    tmp_path,
) -> None:
    report = _write_report(
        tmp_path,
        [
            {
                "statute_id": "ukpga/2000/8",
                "score_status": "scored",
                "triage_bucket": "manual_compile_frontier_residual",
                "aligned": 74.85,
                "n_only_in_oracle": 3,
                "oracle_only_eid_samples": ["section-1", "section-2"],
                "source_chain_frontier_reasons": [
                    "manual_frontier_source_insufficient"
                ],
                "manual_frontier_status_counts": {
                    "manual_compile_candidate": 2,
                    "source_insufficient": 1,
                },
                "manual_frontier_rule_counts": {
                    "uk_manual_frontier_schedule_list_entry_candidate": 2,
                    "uk_manual_frontier_source_payload_without_instruction_context": 1,
                },
                "manual_frontier_work_item_family_counts": {
                    "uk_manual_frontier_schedule_list_entry_candidate": 2,
                },
                "compile_rejection_rule_counts": {
                    "uk_effect_lowering_no_supported_action_rejected": 1,
                    "zero_count_should_drop": 0,
                },
                "blocking_compile_rejection_rule_counts": {
                    "uk_effect_repeal_table_structural_repeal_unresolved": 1,
                },
                "mutation_boundary_proof_status_counts": {"proved": 4},
                "mutation_boundary_proof_rule_counts": {
                    "mutation_boundary_path_set_proved": 4,
                },
                "mutation_boundary_result_code_counts": {"ok": 4},
                "n_mutation_boundary_unexplained_reports": 0,
                "n_mutation_boundary_unexplained_paths": 0,
                "agreement_residual": {
                    "owner_phase": "typed_elaboration",
                    "missing_proofs": [
                        "target_identity",
                        "payload_or_boundary_identity",
                        "mutation_boundary_proof",
                    ],
                },
            },
        ],
    )

    payload = remaining.load_remaining_work(
        report,
        include_items=True,
        item_lane_ids=frozenset({"manual_compilation_frontier"}),
    )

    item = payload["items"][0]
    assert item["executable"] is False
    assert item["replay_authorized"] is False
    assert item["manual_frontier_status_counts"] == {
        "manual_compile_candidate": 2,
        "source_insufficient": 1,
    }
    assert item["manual_frontier_rule_counts"] == {
        "uk_manual_frontier_schedule_list_entry_candidate": 2,
        "uk_manual_frontier_source_payload_without_instruction_context": 1,
    }
    assert item["manual_frontier_work_item_family_counts"] == {
        "uk_manual_frontier_schedule_list_entry_candidate": 2,
    }
    assert item["compile_rejection_rule_counts"] == {
        "uk_effect_lowering_no_supported_action_rejected": 1,
    }
    assert item["blocking_compile_rejection_rule_counts"] == {
        "uk_effect_repeal_table_structural_repeal_unresolved": 1,
    }
    assert item["mutation_boundary_proof_status_counts"] == {"proved": 4}
    assert item["mutation_boundary_proof_rule_counts"] == {
        "mutation_boundary_path_set_proved": 4,
    }
    assert item["mutation_boundary_result_code_counts"] == {"ok": 4}
    assert item["mutation_boundary_unexplained_report_count"] == 0
    assert item["mutation_boundary_unexplained_path_count"] == 0
    detail = item["frontier_work_item"]["detail"]
    assert detail["evidence_counters"]["compile_rejection_rule_counts"] == {
        "uk_effect_lowering_no_supported_action_rejected": 1,
    }
    assert detail["evidence_counters"]["manual_frontier_status_counts"] == {
        "manual_compile_candidate": 2,
        "source_insufficient": 1,
    }
    assert item["execution_authorization"]["authorization_status"] == (
        "non_executable_work_item"
    )


def test_item_export_rejects_unknown_lane(tmp_path) -> None:
    report = _write_report(tmp_path, [])

    with pytest.raises(ValueError, match="unknown lane id"):
        remaining.load_remaining_work(
            report,
            include_items=True,
            item_lane_ids=frozenset({"not_a_lane"}),
        )


def test_item_export_can_limit_rows_per_lane(tmp_path) -> None:
    report = _write_report(
        tmp_path,
        [
            {
                "statute_id": "ukpga/2000/1",
                "score_status": "scored",
                "triage_bucket": "manual_compile_frontier_residual",
                "aligned": 70.0,
            },
            {
                "statute_id": "ukpga/2000/2",
                "score_status": "scored",
                "triage_bucket": "manual_compile_frontier_residual",
                "aligned": 71.0,
            },
            {
                "statute_id": "ukpga/2000/3",
                "score_status": "scored",
                "triage_bucket": "manual_compile_frontier_residual",
                "aligned": 72.0,
            },
            {
                "statute_id": "uksi/2009/41",
                "score_status": "scored",
                "triage_bucket": "effect_feed_absent_frontier",
                "aligned": 60.0,
            },
            {
                "statute_id": "uksi/2009/42",
                "score_status": "scored",
                "triage_bucket": "effect_feed_absent_frontier",
                "aligned": 61.0,
            },
            {
                "statute_id": "ukpga/1920/50",
                "score_status": "scored",
                "triage_bucket": "retained_repeal_oracle_branch",
                "aligned": 0.0,
            },
        ],
    )

    payload = remaining.load_remaining_work(
        report,
        include_items=True,
        item_limit_per_lane=1,
    )

    assert payload["summary"]["item_count"] == 3
    assert payload["summary"]["item_limit_per_lane"] == 1
    assert payload["summary"]["item_exported_lane_counts"] == {
        "effect_source_footing_gap": 1,
        "manual_compilation_frontier": 1,
        "oracle_suspect_review": 1,
    }
    assert payload["summary"]["item_exported_lane_count"] == 3
    assert payload["summary"]["item_expected_row_count"] == 6
    assert payload["summary"]["item_exported_row_count"] == 3
    assert payload["summary"]["item_unexported_row_count"] == 3
    assert payload["summary"]["item_fully_exported"] is False
    assert payload["summary"]["item_unexported_lane_ids"] == []
    assert payload["summary"]["item_authorization_status_counts"] == {
        "non_executable_work_item": 3,
    }
    assert payload["summary"]["item_safety_gap_counts"] == {}
    assert [item["lane_id"] for item in payload["items"]] == [
        "manual_compilation_frontier",
        "effect_source_footing_gap",
        "oracle_suspect_review",
    ]


def test_item_export_global_limit_still_caps_per_lane_sampling(tmp_path) -> None:
    report = _write_report(
        tmp_path,
        [
            {
                "statute_id": "ukpga/2000/1",
                "score_status": "scored",
                "triage_bucket": "manual_compile_frontier_residual",
                "aligned": 70.0,
            },
            {
                "statute_id": "uksi/2009/41",
                "score_status": "scored",
                "triage_bucket": "effect_feed_absent_frontier",
                "aligned": 60.0,
            },
        ],
    )

    payload = remaining.load_remaining_work(
        report,
        include_items=True,
        item_limit=1,
        item_limit_per_lane=1,
    )

    assert payload["summary"]["item_count"] == 1
    assert payload["summary"]["item_expected_row_count"] == 2
    assert payload["summary"]["item_exported_row_count"] == 1
    assert payload["summary"]["item_unexported_row_count"] == 1
    assert payload["summary"]["item_fully_exported"] is False
    assert payload["summary"]["item_unexported_lane_ids"] == [
        "effect_source_footing_gap"
    ]
    assert payload["items"][0]["lane_id"] == "manual_compilation_frontier"


def test_item_export_summary_counts_safety_gaps() -> None:
    summary = remaining._item_export_summary(
        [
            {
                "lane_id": "manual_compilation_frontier",
                "executable": True,
                "replay_authorized": False,
                "execution_authorization": {},
                "frontier_work_item": {},
                "candidate_set_certificate": {},
            },
            {
                "lane_id": "effect_source_footing_gap",
                "executable": False,
                "replay_authorized": True,
                "execution_authorization": {
                    "authorization_status": "unexpected_authorized"
                },
                "frontier_work_item": {"work_item_id": "x"},
                "candidate_set_certificate": {"scope_id": "x"},
            },
        ],
        {
            "manual_compilation_frontier": ({},),
            "effect_source_footing_gap": ({},),
        },
        lane_ids=frozenset(),
    )

    assert summary["item_safety_gap_counts"] == {
        "executable_items": 1,
        "missing_candidate_set_certificate": 1,
        "missing_execution_authorization": 1,
        "missing_frontier_work_item": 1,
        "replay_authorized_items": 1,
    }
    assert summary["item_authorization_status_counts"] == {
        "": 1,
        "unexpected_authorized": 1,
    }
    assert summary["item_fully_exported"] is True


def test_item_export_summary_honors_lane_filter_for_coverage() -> None:
    summary = remaining._item_export_summary(
        [
            {
                "lane_id": "effect_source_footing_gap",
                "executable": False,
                "replay_authorized": False,
                "execution_authorization": {
                    "authorization_status": "non_executable_work_item"
                },
                "frontier_work_item": {"work_item_id": "x"},
                "candidate_set_certificate": {"scope_id": "x"},
            },
        ],
        {
            "manual_compilation_frontier": ({},),
            "effect_source_footing_gap": ({},),
        },
        lane_ids=frozenset({"effect_source_footing_gap"}),
    )

    assert summary["item_expected_row_count"] == 1
    assert summary["item_exported_row_count"] == 1
    assert summary["item_unexported_row_count"] == 0
    assert summary["item_unexported_lane_ids"] == []
    assert summary["item_fully_exported"] is True


def test_item_safety_gap_cli_gate_passes_for_non_executable_items(
    tmp_path,
    capsys,
) -> None:
    report = _write_report(
        tmp_path,
        [
            {
                "statute_id": "ukpga/2000/1",
                "score_status": "scored",
                "triage_bucket": "manual_compile_frontier_residual",
                "aligned": 70.0,
            },
        ],
    )

    assert remaining.main(
        [
            str(report),
            "--include-items",
            "--fail-on-item-safety-gaps",
            "--format",
            "json",
        ]
    ) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["summary"]["item_safety_gap_counts"] == {}


def test_item_coverage_gap_cli_gate_passes_for_full_export(
    tmp_path,
    capsys,
) -> None:
    report = _write_report(
        tmp_path,
        [
            {
                "statute_id": "ukpga/2000/1",
                "score_status": "scored",
                "triage_bucket": "manual_compile_frontier_residual",
                "aligned": 70.0,
            },
        ],
    )

    assert remaining.main(
        [
            str(report),
            "--include-items",
            "--fail-on-item-coverage-gaps",
            "--format",
            "json",
        ]
    ) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["summary"]["item_fully_exported"] is True


def test_item_coverage_gap_cli_gate_fails_for_limited_export(
    tmp_path,
    capsys,
) -> None:
    report = _write_report(
        tmp_path,
        [
            {
                "statute_id": "ukpga/2000/1",
                "score_status": "scored",
                "triage_bucket": "manual_compile_frontier_residual",
                "aligned": 70.0,
            },
            {
                "statute_id": "ukpga/2000/2",
                "score_status": "scored",
                "triage_bucket": "manual_compile_frontier_residual",
                "aligned": 71.0,
            },
        ],
    )

    assert remaining.main(
        [
            str(report),
            "--include-items",
            "--item-limit",
            "1",
            "--fail-on-item-coverage-gaps",
            "--format",
            "json",
        ]
    ) == 1
    output = json.loads(capsys.readouterr().out)
    assert output["summary"]["item_fully_exported"] is False


def test_pure_non_textual_effect_rows_get_distinct_non_replay_lane(tmp_path) -> None:
    report = _write_report(
        tmp_path,
        [
            {
                "statute_id": "ukpga/2025/26",
                "score_status": "scored",
                "triage_bucket": "nonreplay_effect_frontier",
                "aligned": 75.0,
                "n_only_in_oracle": 10,
                "manual_frontier_status_counts": {"non_textual_or_out_of_scope": 3},
                "agreement_residual": {
                    "owner_phase": "effect_metadata_frontend",
                    "missing_proofs": ["applicability_or_non_textual_semantics"],
                },
            },
            {
                "statute_id": "nia/2001/11",
                "score_status": "scored",
                "triage_bucket": "nonreplay_effect_frontier",
                "aligned": 50.0,
                "n_only_in_oracle": 20,
                "manual_frontier_status_counts": {
                    "manual_compile_candidate": 1,
                    "non_textual_or_out_of_scope": 8,
                },
                "agreement_residual": {
                    "owner_phase": "canonical_op_compilation",
                    "missing_proofs": ["canonical_operation_compilation"],
                },
            },
        ],
    )

    payload = remaining.load_remaining_work(report)
    lane_by_id = {lane["lane_id"]: lane for lane in payload["lanes"]}

    assert lane_by_id["manual_compilation_frontier"]["row_count"] == 1
    assert lane_by_id["non_textual_or_out_of_scope_effect_frontier"][
        "row_count"
    ] == 1
    assert lane_by_id["non_textual_or_out_of_scope_effect_frontier"][
        "owner_phase"
    ] == "effect_metadata_frontend"
    assert "commencement_effect_as_text_mutation" in lane_by_id[
        "non_textual_or_out_of_scope_effect_frontier"
    ]["forbidden_shortcuts"]


def test_temporal_commencement_rows_get_distinct_temporal_lane(tmp_path) -> None:
    report = _write_report(
        tmp_path,
        [
            {
                "statute_id": "ukpga/1978/9",
                "score_status": "scored",
                "triage_bucket": "temporal_commencement_frontier",
                "aligned": 82.57,
                "manual_frontier_status_counts": {
                    "deterministic_frontend_supported": 1
                },
                "compile_rejection_rule_counts": {
                    "uk_effect_undated_applied_si_commencement_unresolved": 1
                },
                "agreement_residual": {
                    "owner_phase": "effect_metadata_frontend",
                    "missing_proofs": ["temporal_extent_applicability"],
                },
            },
            {
                "statute_id": "ukpga/1862/19",
                "score_status": "scored",
                "triage_bucket": "no_compiled_ops_frontier",
                "aligned": 87.3,
                "agreement_residual": {
                    "owner_phase": "canonical_op_compilation",
                    "missing_proofs": ["source_identity"],
                },
            },
            {
                "statute_id": "ukpga/2000/99",
                "score_status": "scored",
                "triage_bucket": "no_compiled_ops_frontier",
                "aligned": 88.0,
                "agreement_residual": {
                    "owner_phase": "canonical_op_compilation",
                    "missing_proofs": ["canonical_operation_compilation"],
                },
            },
        ],
    )

    payload = remaining.load_remaining_work(report)
    lane_by_id = {lane["lane_id"]: lane for lane in payload["lanes"]}

    assert lane_by_id["temporal_commencement_frontier"]["row_count"] == 1
    assert lane_by_id["temporal_commencement_frontier"][
        "work_kind"
    ] == "temporal_commencement_materialization_proof_gap"
    assert lane_by_id["temporal_commencement_frontier"][
        "missing_proof_counts"
    ] == {"temporal_extent_applicability": 1}
    assert "undated_commencement_as_commenced_state" in lane_by_id[
        "temporal_commencement_frontier"
    ]["forbidden_shortcuts"]
    assert lane_by_id["effect_source_footing_gap"]["row_count"] == 1
    assert lane_by_id["canonical_or_temporal_frontier"]["row_count"] == 1


def test_nonreplay_rows_route_to_manual_or_source_footing_when_proofs_say_so(
    tmp_path,
) -> None:
    report = _write_report(
        tmp_path,
        [
            {
                "statute_id": "nia/2001/11",
                "score_status": "scored",
                "triage_bucket": "nonreplay_effect_frontier",
                "aligned": 25.25,
                "source_chain_frontier_reasons": [
                    "manual_frontier_manual_compile_candidate"
                ],
                "manual_frontier_status_counts": {
                    "manual_compile_candidate": 1,
                    "non_textual_or_out_of_scope": 8,
                },
                "agreement_residual": {
                    "owner_phase": "effect_metadata_frontend",
                    "missing_proofs": ["source_identity"],
                },
            },
            {
                "statute_id": "ukpga/2009/9",
                "score_status": "scored",
                "triage_bucket": "nonreplay_effect_frontier",
                "aligned": 56.39,
                "source_chain_frontier_reasons": [
                    "effect_rows_not_admitted_by_replay_lens",
                    "manual_frontier_source_insufficient",
                ],
                "manual_frontier_status_counts": {
                    "source_insufficient": 1,
                    "non_textual_or_out_of_scope": 1,
                },
                "agreement_residual": {
                    "owner_phase": "effect_metadata_frontend",
                    "missing_proofs": ["source_identity"],
                },
            },
        ],
    )

    payload = remaining.load_remaining_work(report)
    lane_by_id = {lane["lane_id"]: lane for lane in payload["lanes"]}

    assert lane_by_id["manual_compilation_frontier"]["row_count"] == 1
    assert lane_by_id["effect_source_footing_gap"]["row_count"] == 1
    assert "canonical_or_temporal_frontier" not in lane_by_id


def test_oracle_suspect_and_zero_oracle_get_distinct_lanes(tmp_path) -> None:
    report = _write_report(
        tmp_path,
        [
            {
                "statute_id": "ukpga/1920/50",
                "score_status": "scored",
                "triage_bucket": "retained_repeal_oracle_branch",
                "aligned": 0.0,
                "n_only_in_oracle": 29,
                "agreement_residual": {
                    "owner_phase": "compare_oracle_classification",
                    "missing_proofs": [],
                },
            },
            {
                "statute_id": "eur/2020/992",
                "score_status": "scored",
                "triage_bucket": "zero_oracle_retention",
                "aligned": 0.0,
                "n_only_in_replayed": 3,
                "agreement_residual": {
                    "owner_phase": "compare_oracle_classification",
                    "missing_proofs": ["commensurable_oracle_surface"],
                },
            },
        ],
    )

    payload = remaining.load_remaining_work(report)

    lane_by_id = {lane["lane_id"]: lane for lane in payload["lanes"]}
    assert lane_by_id["oracle_suspect_review"]["priority_rank"] == 60
    assert lane_by_id["non_commensurable_oracle_surface"]["priority_rank"] == 30
    assert lane_by_id["oracle_suspect_review"]["missing_proof_counts"] == {}
    assert lane_by_id["non_commensurable_oracle_surface"][
        "missing_proof_counts"
    ] == {"commensurable_oracle_surface": 1}


def test_effective_oracle_review_overlay_splits_refuted_retained_repeals(
    tmp_path,
) -> None:
    report = _write_report(
        tmp_path,
        [
            {
                "statute_id": "ukpga/1920/50",
                "score_status": "scored",
                "triage_bucket": "retained_repeal_oracle_branch",
                "aligned": 0.0,
                "n_only_in_oracle": 29,
                "agreement_residual": {
                    "owner_phase": "compare_oracle_classification",
                    "missing_proofs": [],
                },
            },
            {
                "statute_id": "eur/2020/2220",
                "score_status": "scored",
                "triage_bucket": "retained_repeal_oracle_branch",
                "aligned": 50.0,
                "n_only_in_oracle": 10,
                "agreement_residual": {
                    "owner_phase": "compare_oracle_classification",
                    "missing_proofs": ["editorial_policy_review"],
                },
            },
        ],
    )
    effective_review = _write_effective_oracle_review(
        tmp_path,
        [
            {
                "statute_id": "ukpga/1920/50",
                "review_status": "refuted_by_dated_current_xml",
                "refutation_reason": "Dated current XML already dots the text.",
                "remaining_question": "Treat as whole-act current XML projection.",
                "agreement_residual": {
                    "owner_phase": "compare_oracle_classification",
                    "missing_proofs": [],
                },
            },
            {
                "statute_id": "eur/2020/2220",
                "review_status": "plausible_true_divergence",
                "refutation_reason": "No marker in dated current XML.",
                "remaining_question": "Check savings, extent, or revival.",
                "agreement_residual": {
                    "owner_phase": "compare_oracle_classification",
                    "missing_proofs": [
                        "savings_extent_or_revival_review",
                        "editorial_policy_review",
                    ],
                },
            },
        ],
    )

    payload = remaining.load_remaining_work(
        report,
        effective_oracle_review_path=effective_review,
        include_items=True,
        item_lane_ids=frozenset({"effective_oracle_review_frontier"}),
    )

    lane_by_id = {lane["lane_id"]: lane for lane in payload["lanes"]}
    assert lane_by_id["oracle_suspect_review"]["row_count"] == 1
    assert lane_by_id["oracle_suspect_review"]["missing_proof_counts"] == {
        "editorial_policy_review": 1,
        "savings_extent_or_revival_review": 1,
    }
    assert lane_by_id["effective_oracle_review_frontier"]["row_count"] == 1
    assert lane_by_id["effective_oracle_review_frontier"][
        "missing_proof_counts"
    ] == {}
    assert lane_by_id["effective_oracle_review_frontier"][
        "priority_rank"
    ] == 55
    assert "effective_oracle_witness_as_replay_authority" in lane_by_id[
        "effective_oracle_review_frontier"
    ]["forbidden_shortcuts"]
    assert payload["summary"]["effective_oracle_review_status_counts"] == {
        "plausible_true_divergence": 1,
        "refuted_by_dated_current_xml": 1,
    }
    assert payload["summary"]["item_count"] == 1
    detail = payload["items"][0]["frontier_work_item"]["detail"]
    assert detail["effective_oracle_review_status"] == "refuted_by_dated_current_xml"
    assert detail["effective_oracle_refutation_reason"] == (
        "Dated current XML already dots the text."
    )
    assert "refuted_by_dated_current_xml=1" in remaining._emit_text(payload)


def test_metadata_only_source_frontiers_get_source_pathology_lane(tmp_path) -> None:
    report = _write_report(
        tmp_path,
        [
            {
                "statute_id": "ukpga/1868/91",
                "score_status": "source_frontier",
                "triage_bucket": "source_frontier:base_and_oracle_metadata_only",
                "source_frontier_reason": "base_and_oracle_metadata_only",
                "base_source_status": "metadata_only",
                "oracle_source_status": "metadata_only",
                "source_frontier_work_item": {
                    "required_proofs": [
                        "source_identity",
                        "official_source_body_or_accepted_source_pathology",
                    ]
                },
                "agreement_residual": {
                    "owner_phase": "affecting_source_extraction",
                    "missing_proofs": ["source_identity"],
                },
            },
            {
                "statute_id": "ukpga/2000/1",
                "score_status": "source_frontier",
                "triage_bucket": "source_frontier:base_absent",
                "source_frontier_reason": "base_absent",
                "base_source_status": "absent",
                "oracle_source_status": "available",
                "agreement_residual": {
                    "owner_phase": "affecting_source_extraction",
                    "missing_proofs": ["source_identity"],
                },
            },
        ],
    )

    payload = remaining.load_remaining_work(report)

    lane_by_id = {lane["lane_id"]: lane for lane in payload["lanes"]}
    assert lane_by_id["metadata_only_source_pathology_frontier"]["row_count"] == 1
    assert lane_by_id["metadata_only_source_pathology_frontier"][
        "priority_rank"
    ] == 35
    assert lane_by_id["metadata_only_source_pathology_frontier"][
        "source_status_pair_counts"
    ] == {"base:metadata_only|oracle:metadata_only": 1}
    assert lane_by_id["metadata_only_source_pathology_frontier"][
        "missing_proof_counts"
    ] == {
        "official_source_body_or_accepted_source_pathology": 1,
        "source_identity": 1,
    }
    assert lane_by_id["source_footing_gap"]["row_count"] == 1
    text = remaining._emit_text(payload)
    assert "metadata_only_source_pathology_frontier: n=1" in text
    assert "source_statuses=base:metadata_only|oracle:metadata_only=1" in text
    assert "metadata_only_xml_as_executable_text" in lane_by_id[
        "metadata_only_source_pathology_frontier"
    ]["forbidden_shortcuts"]


def test_unknown_scored_bucket_is_high_priority_gate_work(tmp_path) -> None:
    report = _write_report(
        tmp_path,
        [
            {
                "statute_id": "ukpga/2000/1",
                "score_status": "scored",
                "triage_bucket": "new_unclassified_bucket",
                "aligned": 50.0,
            }
        ],
        summary={
            "completion_gate_clean": False,
            "completion_gate_failure_counts": {"active_unclassified_residuals": 1},
        },
    )

    payload = remaining.load_remaining_work(report)

    assert payload["summary"]["completion_gate_clean"] is False
    assert payload["summary"]["completion_gate_failure_counts"] == {
        "active_unclassified_residuals": 1
    }
    assert payload["summary"]["unknown_scored_triage_buckets"] == [
        "new_unclassified_bucket"
    ]
    assert payload["lanes"][0]["lane_id"] == "unclassified_or_gate_failure"
    assert "completion_gate_failure_as_score_noise" in payload["lanes"][0][
        "forbidden_shortcuts"
    ]


def test_text_and_tsv_emitters_are_stable(tmp_path) -> None:
    report = _write_report(
        tmp_path,
        [
            {
                "statute_id": "ukpga/2000/1",
                "score_status": "scored",
                "triage_bucket": "high_fidelity_after_grounding",
                "aligned": "100.0",
            },
            {
                "statute_id": "ukpga/2000/2",
                "score_status": "scored",
                "triage_bucket": "manual_compile_frontier_residual",
                "aligned": "75.0",
                "agreement_residual": {
                    "owner_phase": "typed_elaboration",
                    "missing_proofs": ["target_identity"],
                },
            },
        ],
    )
    payload = remaining.load_remaining_work(report)

    text = remaining._emit_text(payload)
    tsv = remaining._emit_tsv(payload)

    assert "UK remaining-work summary" in text
    assert "manual_compilation_frontier: n=1 scored=1" in text
    assert "proofs=target_identity=1" in text
    assert tsv.splitlines()[0].startswith("lane_id\tpriority_rank\towner_phase")
    assert "manual_compilation_frontier\t90\ttyped_elaboration" in tsv

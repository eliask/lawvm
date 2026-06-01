from __future__ import annotations

import json
import sys
from types import SimpleNamespace

from lawvm.core.mutation_events import MutationEvent

import scripts.uk_broad_baseline as uk_broad_baseline


def test_score_one_reports_too_small_current_as_source_frontier(monkeypatch) -> None:
    class FakeFarchive:
        def __init__(self, _path):
            pass

        def get(self, locator: str) -> bytes | None:
            if locator.endswith("/enacted/data.xml"):
                return b"""<?xml version="1.0"?>
<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation"
    NumberOfProvisions="1">
  <Body><P1 id="section-1"><Pnumber>1</Pnumber><P1para>Text.</P1para></P1></Body>
</Legislation>"""
            if locator.endswith("/data.xml"):
                return b"HTTP 300 Multiple Choices"
            return None

        def close(self) -> None:
            pass

    monkeypatch.setitem(
        sys.modules,
        "farchive",
        SimpleNamespace(Farchive=FakeFarchive),
    )

    row = uk_broad_baseline.score_one("ukpga/1945/9")

    assert row["score_status"] == "source_frontier"
    assert row["source_frontier_reason"] == "oracle_too_small"
    assert row["base_source_status"] == "available"
    assert row["oracle_source_status"] == "too_small"
    assert "error" not in row


def test_normalized_compare_eids_uses_uk_misses_compare_lens() -> None:
    replay, oracle = uk_broad_baseline._normalized_compare_eids(
        {"section-1", "p00090"},
        {"section-1"},
        oracle_physical_eid_aliases={},
        oracle_visible_number_eid_aliases={},
    )

    assert replay == {"section-1"}
    assert oracle == {"section-1"}


def test_summarize_results_counts_frontiers_and_zero_oracle_retention() -> None:
    summary = uk_broad_baseline.summarize_results(
        [
            {
                "statute_id": "ukpga/1938/22",
                "score_status": "scored",
                "aligned": 0.0,
                "n_replay": 420,
                "n_oracle": 0,
                "n_zero_oracle_retention_eids": 420,
            },
            {
                "statute_id": "ukpga/1992/41",
                "score_status": "scored",
                "aligned": 64.0,
                "aligned_excluding_grounding_collateral": 98.7,
                "n_grounding_collateral": 169,
                "n_replay": 469,
                "n_oracle": 304,
            },
            {
                "statute_id": "ukpga/1986/61",
                "score_status": "scored",
                "aligned": 50.9,
                "aligned_excluding_grounding_collateral": 50.9,
                "n_grounding_collateral": 100,
                "n_replay": 389,
                "n_oracle": 568,
                "manual_frontier_status_counts": {
                    "deterministic_frontend_candidate": 2,
                    "manual_compile_candidate": 3,
                },
                "manual_frontier_rule_counts": {
                    "uk_manual_frontier_parser_or_extraction_candidate": 2,
                    "uk_manual_frontier_repeal_table_candidate": 3,
                },
                "manual_frontier_owner_phase_counts": {
                    "affecting_source_extraction": 2,
                    "typed_elaboration": 3,
                },
                "manual_frontier_rule_owner_phase_counts": {
                    "affecting_source_extraction:uk_manual_frontier_parser_or_extraction_candidate": 2,
                    "typed_elaboration:uk_manual_frontier_repeal_table_candidate": 3,
                },
                "compile_rejection_owner_phase_counts": {
                    "affecting_source_extraction": 4,
                    "typed_elaboration": 1,
                },
                "compile_rejection_authorization_status_counts": {
                    "effect_feed_parse_diagnostic_evidence_only": 4,
                    "lowering_compile_blocked": 1,
                },
                "compile_rejection_missing_proof_counts": {
                    "canonical_operation_compilation": 1,
                    "canonical_operation_or_replay_authorization": 4,
                },
                "compile_rejection_rule_owner_phase_counts": {
                    "affecting_source_extraction:uk_effect_source_gap": 4,
                    "typed_elaboration:uk_effect_target_gap": 1,
                },
                "blocking_compile_rejection_owner_phase_counts": {
                    "typed_elaboration": 1,
                },
                "blocking_compile_rejection_authorization_status_counts": {
                    "lowering_compile_blocked": 1,
                },
                "blocking_compile_rejection_missing_proof_counts": {
                    "canonical_operation_compilation": 1,
                },
                "blocking_compile_rejection_rule_owner_phase_counts": {
                    "typed_elaboration:uk_effect_target_gap": 1,
                },
                "manual_frontier_manual_compile_candidate_rule_counts": {
                    "uk_manual_frontier_repeal_table_candidate": 3,
                },
                "manual_frontier_manual_compile_candidate_rule_owner_phase_counts": {
                    "typed_elaboration:uk_manual_frontier_repeal_table_candidate": 3,
                },
                "manual_frontier_deterministic_candidate_rule_counts": {
                    "uk_manual_frontier_parser_or_extraction_candidate": 2,
                },
                "manual_frontier_deterministic_candidate_rule_owner_phase_counts": {
                    "affecting_source_extraction:uk_manual_frontier_parser_or_extraction_candidate": 2,
                },
                "manual_frontier_template_status_counts": {
                    "available": 3,
                    "not_available": 2,
                },
                "manual_frontier_template_gap_status_counts": {
                    "deterministic_frontend_candidate": 2,
                },
                "manual_frontier_template_gap_rule_counts": {
                    "uk_manual_frontier_parser_or_extraction_candidate": 2,
                },
                "n_mutation_events": 4,
                "n_mutation_boundary_reports": 4,
                "n_mutation_boundary_unexplained_reports": 1,
                "n_mutation_boundary_unexplained_paths": 2,
                "mutation_boundary_result_code_counts": {
                    "REPLAY_APPLY_BOUNDARY_TOUCH_OUTSIDE_TARGET": 1,
                    "REPLAY_MISSING_PRIMARY_TARGET_CONSUMPTION": 1,
                },
                "mutation_boundary_helper_counts": {
                    "apply_op": 3,
                    "replace_text": 1,
                },
            },
            {
                "statute_id": "ukpga/1961/60",
                "score_status": "scored",
                "aligned": 22.7,
                "aligned_excluding_grounding_collateral": 22.7,
                "unaligned": 22.7,
                "n_grounding_collateral": 0,
                "n_replay": 5,
                "n_oracle": 22,
                "base_source_status": "metadata_only",
            },
            {
                "statute_id": "eur/2019/1841",
                "score_status": "scored",
                "aligned": 61.8,
                "aligned_excluding_grounding_collateral": 61.8,
                "unaligned": 100.0,
                "n_grounding_collateral": 0,
                "n_replay": 34,
                "n_oracle": 21,
            },
            {
                "statute_id": "uksi/2000/1043",
                "score_status": "scored",
                "aligned": 77.7,
                "aligned_excluding_grounding_collateral": 77.7,
                "unaligned": 75.3,
                "n_grounding_collateral": 1,
                "n_replay": 168,
                "n_oracle": 215,
                "n_ops": 0,
            },
            {
                "statute_id": "ukpga/1945/9",
                "score_status": "source_frontier",
                "source_frontier_reason": "base_too_small",
            },
            {
                "statute_id": "ukpga/1945/10",
                "score_status": "source_frontier",
                "source_frontier_reason": "base_too_small",
            },
            {
                "statute_id": "ukpga/1946/1",
                "error": "RuntimeError: boom",
            },
        ]
    )

    assert len(summary["scored"]) == 6
    assert len(summary["errored"]) == 1
    assert len(summary["source_frontier"]) == 2
    assert summary["source_frontier_reasons"] == {"base_too_small": 2}
    assert summary["source_chain_frontier_reasons"] == {
        "base_too_small": 2,
        "effect_rows_absent_or_unpublished": 1,
    }
    assert summary["source_chain_frontier_statutes"] == {
        "base_too_small": ["ukpga/1945/10", "ukpga/1945/9"],
        "effect_rows_absent_or_unpublished": ["uksi/2000/1043"],
    }
    assert summary["non_manual_source_chain_frontier_count"] == 1
    assert summary["non_manual_source_chain_frontier_statutes"] == ["uksi/2000/1043"]
    assert summary["source_or_oracle_pathology_frontier_count"] == 2
    assert summary["source_or_oracle_pathology_frontier_statutes"] == [
        "ukpga/1945/10",
        "ukpga/1945/9",
    ]
    assert summary["zero_oracle_retention_count"] == 1
    assert summary["zero_oracle_retention_statutes"] == ["ukpga/1938/22"]
    assert summary["zero_oracle_retention_eids"] == 420
    assert summary["triage_buckets"] == {
        "base_metadata_only_frontier": 1,
        "error": 1,
        "high_fidelity_after_grounding": 1,
        "no_effect_rows_frontier": 1,
        "residual_after_grounding": 1,
        "source_frontier:base_too_small": 2,
        "structural_match_eid_scheme_residual": 1,
        "zero_oracle_retention": 1,
    }
    assert summary["triage_bucket_statutes"] == {
        "base_metadata_only_frontier": ["ukpga/1961/60"],
        "error": ["ukpga/1946/1"],
        "high_fidelity_after_grounding": ["ukpga/1992/41"],
        "no_effect_rows_frontier": ["uksi/2000/1043"],
        "residual_after_grounding": ["ukpga/1986/61"],
        "source_frontier:base_too_small": ["ukpga/1945/10", "ukpga/1945/9"],
        "structural_match_eid_scheme_residual": ["eur/2019/1841"],
        "zero_oracle_retention": ["ukpga/1938/22"],
    }
    assert summary["agreement_residual_family_counts"] == {
        "agreement": 1,
        "error": 1,
        "non_commensurable_surface": 2,
        "replay_bug": 1,
        "source_footing_gap": 3,
        "topology_granularity_mismatch": 1,
    }
    assert summary["agreement_residual_status_counts"] == {
        "error": 1,
        "frontier": 5,
        "residual": 3,
    }
    assert summary["agreement_residual_owner_phase_counts"] == {
        "affecting_source_extraction": 2,
        "compare_oracle_classification": 4,
        "effect_metadata_frontend": 1,
        "replay_invariants": 1,
        "source_pathology_manual_frontier": 1,
    }
    assert summary["agreement_residual_rule_counts"] == {
        "uk_broad_base_metadata_only_frontier": 1,
        "uk_broad_error": 1,
        "uk_broad_high_fidelity_after_grounding": 1,
        "uk_broad_no_effect_rows_frontier": 1,
        "uk_broad_residual_after_grounding": 1,
        "uk_broad_source_frontier:base_too_small": 2,
        "uk_broad_structural_match_eid_scheme_residual": 1,
        "uk_broad_zero_oracle_retention": 1,
    }
    assert summary["active_unclassified_residual_count"] == 2
    assert summary["active_unclassified_residual_statutes"] == [
        "eur/2019/1841",
        "ukpga/1986/61",
    ]
    assert summary["deterministic_frontend_candidate_count"] == 2
    assert summary["deterministic_frontend_candidate_statutes"] == [
        "ukpga/1986/61",
    ]
    assert summary["manual_frontier_status_counts"] == {
        "deterministic_frontend_candidate": 2,
        "manual_compile_candidate": 3,
    }
    assert summary["manual_frontier_rule_counts"] == {
        "uk_manual_frontier_parser_or_extraction_candidate": 2,
        "uk_manual_frontier_repeal_table_candidate": 3,
    }
    assert summary["manual_frontier_owner_phase_counts"] == {
        "affecting_source_extraction": 2,
        "typed_elaboration": 3,
    }
    assert summary["manual_frontier_rule_owner_phase_counts"] == {
        "affecting_source_extraction:uk_manual_frontier_parser_or_extraction_candidate": 2,
        "typed_elaboration:uk_manual_frontier_repeal_table_candidate": 3,
    }
    assert summary["compile_rejection_owner_phase_counts"] == {
        "affecting_source_extraction": 4,
        "typed_elaboration": 1,
    }
    assert summary["compile_rejection_authorization_status_counts"] == {
        "effect_feed_parse_diagnostic_evidence_only": 4,
        "lowering_compile_blocked": 1,
    }
    assert summary["compile_rejection_missing_proof_counts"] == {
        "canonical_operation_compilation": 1,
        "canonical_operation_or_replay_authorization": 4,
    }
    assert summary["compile_rejection_rule_owner_phase_counts"] == {
        "affecting_source_extraction:uk_effect_source_gap": 4,
        "typed_elaboration:uk_effect_target_gap": 1,
    }
    assert summary["blocking_compile_rejection_owner_phase_counts"] == {
        "typed_elaboration": 1,
    }
    assert summary["blocking_compile_rejection_authorization_status_counts"] == {
        "lowering_compile_blocked": 1,
    }
    assert summary["blocking_compile_rejection_missing_proof_counts"] == {
        "canonical_operation_compilation": 1,
    }
    assert summary["blocking_compile_rejection_rule_owner_phase_counts"] == {
        "typed_elaboration:uk_effect_target_gap": 1,
    }
    assert summary["manual_frontier_manual_compile_candidate_rule_counts"] == {
        "uk_manual_frontier_repeal_table_candidate": 3,
    }
    assert summary[
        "manual_frontier_manual_compile_candidate_rule_owner_phase_counts"
    ] == {
        "typed_elaboration:uk_manual_frontier_repeal_table_candidate": 3,
    }
    assert summary["manual_frontier_deterministic_candidate_rule_counts"] == {
        "uk_manual_frontier_parser_or_extraction_candidate": 2,
    }
    assert summary[
        "manual_frontier_deterministic_candidate_rule_owner_phase_counts"
    ] == {
        "affecting_source_extraction:uk_manual_frontier_parser_or_extraction_candidate": 2,
    }
    assert summary["manual_frontier_template_status_counts"] == {
        "available": 3,
        "not_available": 2,
    }
    assert summary["manual_frontier_template_gap_status_counts"] == {
        "deterministic_frontend_candidate": 2,
    }
    assert summary["manual_frontier_template_gap_rule_counts"] == {
        "uk_manual_frontier_parser_or_extraction_candidate": 2,
    }
    assert summary["manual_frontier_template_gap_count"] == 2
    assert summary["mutation_boundary_event_count"] == 4
    assert summary["mutation_boundary_report_count"] == 4
    assert summary["mutation_boundary_unexplained_report_count"] == 1
    assert summary["mutation_boundary_unexplained_path_count"] == 2
    assert summary["mutation_boundary_result_code_counts"] == {
        "REPLAY_APPLY_BOUNDARY_TOUCH_OUTSIDE_TARGET": 1,
        "REPLAY_MISSING_PRIMARY_TARGET_CONSUMPTION": 1,
    }
    assert summary["mutation_boundary_helper_counts"] == {
        "apply_op": 3,
        "replace_text": 1,
    }
    assert summary["mutation_boundary_unexplained_statutes"] == [
        "ukpga/1986/61",
    ]


def test_broad_baseline_report_envelope_declares_agreement_scope(tmp_path) -> None:
    snapshot_path = tmp_path / "snapshot.json"
    report = uk_broad_baseline.uk_broad_baseline_report_jsonable(
        [
            {
                "statute_id": "ukpga/1992/41",
                "score_status": "scored",
                "aligned": 98.7,
                "aligned_excluding_grounding_collateral": 98.7,
                "unaligned": 98.7,
                "n_replay": 10,
                "n_oracle": 10,
            },
            {
                "statute_id": "ukpga/1945/9",
                "score_status": "source_frontier",
                "source_frontier_reason": "base_too_small",
            },
        ],
        ids=["ukpga/1992/41", "ukpga/1945/9"],
        snapshot_path=snapshot_path,
    )

    assert report["jurisdiction"] == "uk"
    assert report["report_kind"] == "uk_broad_baseline_agreement_report"
    assert report["schema"] == "lawvm.uk_broad_baseline_agreement_report.v1"
    assert (
        report["truth_claim"]
        == "uk_replay_oracle_agreement_regression_guard_not_source_truth"
    )
    assert report["replay_claims"] is True
    assert report["agreement_claims"] is True
    assert report["canonical_effect_claims"] is False
    assert report["candidate_effect_claims"] is False
    assert report["dry_run_claims"] is False
    assert report["summary"]["scored_count"] == 1
    assert report["summary"]["source_frontier_count"] == 1
    assert report["summary"]["manual_frontier_template_gap_count"] == 0
    assert report["summary"]["zero_oracle_retention_statutes"] == []
    assert report["summary"]["agreement_residual_family_counts"] == {
        "agreement": 1,
        "source_footing_gap": 1,
    }
    assert report["filters"]["snapshot_path"] == str(snapshot_path)
    assert report["written_paths"] == [str(snapshot_path)]
    assert "agreement_as_execution_authorization" in report["forbidden_shortcuts"]
    assert "mutation_boundary_proof" in report["next_promotion_requires"]
    assert len(report["rows"]) == 2
    assert report["rows"][0]["agreement_residual"]["family"] == "agreement"
    assert report["rows"][0]["agreement_residual"]["status"] == "residual"
    assert report["rows"][1]["agreement_residual"]["family"] == "source_footing_gap"


def test_mutation_boundary_diagnostics_reports_unexplained_paths() -> None:
    diagnostics = uk_broad_baseline._mutation_boundary_diagnostics(
        [
            MutationEvent(
                op_id="op-1",
                source_statute="ukpga/2000/1",
                action="replace",
                helper="replace_text",
                outcome="replaced_node",
                resolved_target_path=(("body", ""), ("section", "1")),
                replaced_paths=((("body", ""), ("section", "2")),),
            )
        ]
    )

    assert diagnostics["n_mutation_events"] == 1
    assert diagnostics["n_mutation_boundary_reports"] == 1
    assert diagnostics["n_mutation_boundary_unexplained_reports"] == 1
    assert diagnostics["n_mutation_boundary_unexplained_paths"] == 1
    assert diagnostics["mutation_boundary_result_code_counts"] == {
        "REPLAY_APPLY_BOUNDARY_TOUCH_OUTSIDE_TARGET": 1,
    }
    assert diagnostics["mutation_boundary_helper_counts"] == {"replace_text": 1}
    assert diagnostics["mutation_boundary_unexplained_samples"] == [
        {
            "op_id": "op-1",
            "helper": "replace_text",
            "outcome": "replaced_node",
            "result_codes": ["REPLAY_APPLY_BOUNDARY_TOUCH_OUTSIDE_TARGET"],
            "unexplained_paths": ["body/section:2"],
        }
    ]


def test_summarize_results_counts_grounding_dominated_residuals() -> None:
    summary = uk_broad_baseline.summarize_results(
        [
            {
                "statute_id": "eur/2019/2018",
                "score_status": "scored",
                "aligned": 17.4,
                "aligned_excluding_grounding_collateral": 41.0,
                "n_grounding_collateral": 165,
                "n_replay": 287,
                "n_oracle": 62,
            },
        ]
    )

    assert summary["triage_buckets"] == {"grounding_dominated_residual": 1}
    assert summary["active_unclassified_residual_count"] == 1


def test_summarize_results_counts_effect_feed_absent_frontier() -> None:
    summary = uk_broad_baseline.summarize_results(
        [
            {
                "statute_id": "uksi/2000/1043",
                "score_status": "scored",
                "aligned": 77.7,
                "aligned_excluding_grounding_collateral": 77.7,
                "unaligned": 75.4,
                "n_grounding_collateral": 0,
                "n_replay": 167,
                "n_oracle": 215,
                "n_ops": 0,
                "n_only_in_oracle": 48,
                "n_only_in_replayed": 0,
                "compile_rejection_rule_counts": {
                    "uk_effect_feed_pages_absent_recorded": 1,
                },
                "n_blocking_compile_rejections": 0,
            },
        ]
    )

    assert summary["triage_buckets"] == {"effect_feed_absent_frontier": 1}
    assert summary["source_chain_frontier_reasons"] == {"effect_feed_pages_absent": 1}
    assert summary["source_chain_frontier_statutes"] == {
        "effect_feed_pages_absent": ["uksi/2000/1043"],
    }


def test_summarize_results_counts_no_effect_rows_frontier() -> None:
    summary = uk_broad_baseline.summarize_results(
        [
            {
                "statute_id": "ukpga/1976/83",
                "score_status": "scored",
                "aligned": 83.7,
                "aligned_excluding_grounding_collateral": 83.7,
                "unaligned": 30.2,
                "n_grounding_collateral": 0,
                "n_replay": 123,
                "n_oracle": 147,
                "n_effects": 0,
                "n_ops": 0,
                "n_compile_rejections": 0,
                "n_blocking_compile_rejections": 0,
            },
        ]
    )

    assert summary["triage_buckets"] == {"no_effect_rows_frontier": 1}
    assert summary["source_chain_frontier_reasons"] == {
        "effect_rows_absent_or_unpublished": 1
    }
    assert summary["source_chain_frontier_statutes"] == {
        "effect_rows_absent_or_unpublished": ["ukpga/1976/83"],
    }


def test_summarize_results_counts_empty_effect_feed_frontier() -> None:
    summary = uk_broad_baseline.summarize_results(
        [
            {
                "statute_id": "uksi/2012/1206",
                "score_status": "scored",
                "aligned": 88.6,
                "aligned_excluding_grounding_collateral": 88.6,
                "unaligned": 88.6,
                "n_grounding_collateral": 0,
                "n_replay": 31,
                "n_oracle": 35,
                "n_effects": 0,
                "n_ops": 0,
                "compile_rejection_rule_counts": {
                    "uk_effect_feed_empty_recorded": 1,
                },
                "n_blocking_compile_rejections": 0,
            },
        ]
    )

    assert summary["triage_buckets"] == {"no_effect_rows_frontier": 1}
    assert summary["source_chain_frontier_reasons"] == {"effect_feed_empty": 1}
    assert summary["source_chain_frontier_statutes"] == {
        "effect_feed_empty": ["uksi/2012/1206"],
    }
    assert summary["non_manual_source_chain_frontier_count"] == 0
    assert summary["non_manual_source_chain_frontier_statutes"] == []
    assert summary["empty_effect_feed_frontier_count"] == 1
    assert summary["empty_effect_feed_frontier_statutes"] == ["uksi/2012/1206"]


def test_summarize_results_counts_nonreplay_effect_frontier() -> None:
    summary = uk_broad_baseline.summarize_results(
        [
            {
                "statute_id": "ukpga/1901/7",
                "score_status": "scored",
                "aligned": 91.7,
                "aligned_excluding_grounding_collateral": 91.7,
                "unaligned": 32.8,
                "n_grounding_collateral": 0,
                "n_replay": 22,
                "n_oracle": 24,
                "n_effects": 1,
                "n_ops": 0,
                "n_compile_rejections": 1,
                "n_blocking_compile_rejections": 0,
                "compile_rejection_rule_counts": {
                    "uk_effect_missing_structural_payload_rejected": 1,
                },
            },
        ]
    )

    assert summary["triage_buckets"] == {"nonreplay_effect_frontier": 1}
    assert summary["source_chain_frontier_reasons"] == {
        "effect_rows_missing_structural_payload": 1
    }
    assert summary["source_chain_frontier_statutes"] == {
        "effect_rows_missing_structural_payload": ["ukpga/1901/7"],
    }


def test_summarize_results_splits_non_admitted_replay_lens_rows() -> None:
    summary = uk_broad_baseline.summarize_results(
        [
            {
                "statute_id": "uksi/2009/3023",
                "score_status": "scored",
                "aligned": 60.0,
                "aligned_excluding_grounding_collateral": 60.0,
                "unaligned": 80.0,
                "n_grounding_collateral": 0,
                "n_replay": 3,
                "n_oracle": 5,
                "n_effects": 1,
                "n_ops": 0,
                "n_compile_rejections": 1,
                "n_blocking_compile_rejections": 0,
                "compile_rejection_rule_counts": {
                    "uk_effect_missing_structural_payload_rejected": 1,
                },
                "manual_frontier_status_counts": {
                    "non_textual_or_out_of_scope": 1,
                },
            },
        ]
    )

    assert summary["triage_buckets"] == {"nonreplay_effect_frontier": 1}
    assert summary["source_chain_frontier_reasons"] == {
        "effect_rows_not_admitted_by_replay_lens": 1
    }
    assert summary["source_chain_frontier_statutes"] == {
        "effect_rows_not_admitted_by_replay_lens": ["uksi/2009/3023"],
    }
    assert summary["non_manual_source_chain_frontier_count"] == 0
    assert summary["non_manual_source_chain_frontier_statutes"] == []
    assert summary["replay_lens_frontier_count"] == 1
    assert summary["replay_lens_frontier_statutes"] == ["uksi/2009/3023"]


def test_summarize_results_splits_mixed_replay_lens_and_source_insufficient_rows() -> None:
    summary = uk_broad_baseline.summarize_results(
        [
            {
                "statute_id": "ukpga/1868/119",
                "score_status": "scored",
                "aligned": 91.4,
                "aligned_excluding_grounding_collateral": 91.4,
                "unaligned": 27.6,
                "n_grounding_collateral": 0,
                "n_replay": 32,
                "n_oracle": 35,
                "n_effects": 29,
                "n_ops": 0,
                "n_compile_rejections": 48,
                "n_blocking_compile_rejections": 1,
                "compile_rejection_rule_counts": {
                    "uk_effect_lowering_no_supported_action_rejected": 27,
                    "uk_effect_structural_pseudo_definition_target_rejected": 1,
                },
                "manual_frontier_status_counts": {
                    "non_textual_or_out_of_scope": 28,
                    "source_insufficient": 1,
                },
            },
        ]
    )

    assert summary["triage_buckets"] == {"nonreplay_effect_frontier": 1}
    assert summary["source_chain_frontier_reasons"] == {
        "effect_rows_not_admitted_by_replay_lens": 1,
        "manual_frontier_source_insufficient": 1,
    }
    assert summary["source_chain_frontier_statutes"] == {
        "effect_rows_not_admitted_by_replay_lens": ["ukpga/1868/119"],
        "manual_frontier_source_insufficient": ["ukpga/1868/119"],
    }
    assert summary["non_manual_source_chain_frontier_count"] == 0
    assert summary["non_manual_source_chain_frontier_statutes"] == []
    assert summary["replay_lens_frontier_count"] == 1
    assert summary["replay_lens_frontier_statutes"] == ["ukpga/1868/119"]


def test_summarize_results_counts_compile_rejection_dominated_residuals() -> None:
    summary = uk_broad_baseline.summarize_results(
        [
            {
                "statute_id": "ukpga/1986/61",
                "score_status": "scored",
                "aligned": 50.9,
                "aligned_excluding_grounding_collateral": 50.9,
                "unaligned": 50.5,
                "n_grounding_collateral": 100,
                "n_replay": 389,
                "n_oracle": 568,
                "n_only_in_oracle": 279,
                "n_only_in_replayed": 11,
                "n_compile_rejections": 168,
                "n_blocking_compile_rejections": 90,
                "compile_rejection_rule_counts": {
                    "uk_effect_lowering_no_supported_action_rejected": 28,
                    "uk_effect_repeal_table_structural_repeal": 62,
                },
            },
        ]
    )

    assert summary["triage_buckets"] == {"compile_rejection_dominated_residual": 1}


def test_summarize_results_classifies_retained_repeal_oracle_branch() -> None:
    summary = uk_broad_baseline.summarize_results(
        [
            {
                "statute_id": "ukpga/1973/33",
                "score_status": "scored",
                "aligned": 50.0,
                "aligned_excluding_grounding_collateral": 50.0,
                "unaligned": 50.0,
                "n_grounding_collateral": 0,
                "n_replay": 19,
                "n_oracle": 38,
                "n_only_in_oracle": 19,
                "n_only_in_replayed": 0,
                "n_ops": 1,
                "n_compile_rejections": 0,
                "n_blocking_compile_rejections": 0,
                "retained_repeal_oracle_targets": ["section-1"],
                "n_retained_repeal_oracle_targets": 1,
            },
        ]
    )

    assert summary["triage_buckets"] == {"retained_repeal_oracle_branch": 1}
    assert summary["active_unclassified_residual_count"] == 0


def test_summarize_results_counts_retained_eu_mixed_representation_residuals() -> None:
    summary = uk_broad_baseline.summarize_results(
        [
            {
                "statute_id": "eur/2019/1021",
                "score_status": "scored",
                "aligned": 51.2,
                "aligned_excluding_grounding_collateral": 53.2,
                "unaligned": 23.6,
                "n_grounding_collateral": 49,
                "n_replay": 211,
                "n_oracle": 203,
                "n_only_in_oracle": 95,
                "n_only_in_replayed": 103,
                "n_compile_rejections": 171,
                "n_blocking_compile_rejections": 127,
            },
        ]
    )

    assert summary["triage_buckets"] == {
        "retained_eu_mixed_representation_residual": 1
    }


def test_summarize_results_counts_bounded_low_volume_residuals() -> None:
    summary = uk_broad_baseline.summarize_results(
        [
            {
                "statute_id": "ukpga/1976/38",
                "score_status": "scored",
                "aligned": 91.9,
                "aligned_excluding_grounding_collateral": 91.9,
                "unaligned": 91.9,
                "n_grounding_collateral": 0,
                "n_replay": 91,
                "n_oracle": 99,
                "n_only_in_oracle": 8,
                "n_only_in_replayed": 0,
                "n_compile_rejections": 11,
                "n_blocking_compile_rejections": 5,
            },
        ]
    )

    assert summary["triage_buckets"] == {"bounded_low_volume_residual": 1}
    assert summary["active_unclassified_residual_count"] == 0


def test_summarize_results_routes_low_volume_manual_frontier_residuals() -> None:
    summary = uk_broad_baseline.summarize_results(
        [
            {
                "statute_id": "ukpga/1887/55",
                "score_status": "scored",
                "aligned": 86.67,
                "aligned_excluding_grounding_collateral": 86.67,
                "unaligned": 86.67,
                "n_grounding_collateral": 0,
                "n_replay": 91,
                "n_oracle": 105,
                "n_only_in_oracle": 14,
                "n_only_in_replayed": 0,
                "n_compile_rejections": 16,
                "n_blocking_compile_rejections": 16,
                "blocking_compile_rejection_rule_counts": {
                    "uk_effect_table_entry_instruction_rejected": 10,
                    "uk_effect_repeal_table_structural_repeal_unresolved": 3,
                },
            },
        ]
    )

    assert summary["triage_buckets"] == {"manual_compile_frontier_residual": 1}
    assert summary["active_unclassified_residual_count"] == 0


def test_summarize_results_routes_large_manual_frontier_residuals() -> None:
    summary = uk_broad_baseline.summarize_results(
        [
            {
                "statute_id": "ukpga/1990/8",
                "score_status": "scored",
                "aligned": 83.81,
                "aligned_excluding_grounding_collateral": 83.81,
                "unaligned": 74.4,
                "n_grounding_collateral": 0,
                "n_replay": 6906,
                "n_oracle": 8240,
                "n_only_in_oracle": 1334,
                "n_only_in_replayed": 0,
                "n_compile_rejections": 3655,
                "n_blocking_compile_rejections": 130,
                "manual_frontier_status_counts": {
                    "manual_compile_candidate": 46,
                    "source_insufficient": 72,
                    "deterministic_frontend_supported": 1548,
                },
                "blocking_compile_rejection_rule_counts": {
                    "uk_effect_schedule_note_target_rejected": 16,
                    "uk_effect_table_entry_instruction_rejected": 8,
                },
            },
        ]
    )

    assert summary["triage_buckets"] == {"manual_compile_frontier_residual": 1}


def test_summarize_results_aggregates_manual_frontier_authorization() -> None:
    summary = uk_broad_baseline.summarize_results(
        [
            {
                "statute_id": "ukpga/1990/8",
                "score_status": "scored",
                "aligned": 83.81,
                "aligned_excluding_grounding_collateral": 83.81,
                "unaligned": 74.4,
                "n_grounding_collateral": 0,
                "n_replay": 6906,
                "n_oracle": 8240,
                "n_only_in_oracle": 1334,
                "n_only_in_replayed": 0,
                "n_compile_rejections": 3655,
                "n_blocking_compile_rejections": 130,
                "manual_frontier_authorization_status_counts": {
                    "manual_claim_required": 2,
                    "source_insufficient": 1,
                },
                "manual_frontier_authorization_status_owner_phase_counts": {
                    "typed_elaboration:manual_claim_required": 2,
                    "affecting_source_extraction:source_insufficient": 1,
                },
                "manual_frontier_missing_proof_counts": {
                    "mutation_boundary_proof": 2,
                    "official_source_witness": 1,
                },
                "manual_frontier_work_item_family_counts": {
                    "uk_manual_frontier_heading_facet_candidate": 2,
                    "uk_manual_frontier_source_insufficient": 1,
                },
                "manual_frontier_work_item_authorization_status_counts": {
                    "manual_claim_required": 2,
                    "source_insufficient": 1,
                },
            },
        ]
    )

    assert summary["manual_frontier_authorization_status_counts"] == {
        "manual_claim_required": 2,
        "source_insufficient": 1,
    }
    assert summary["manual_frontier_authorization_status_owner_phase_counts"] == {
        "affecting_source_extraction:source_insufficient": 1,
        "typed_elaboration:manual_claim_required": 2,
    }
    assert summary["manual_frontier_missing_proof_counts"] == {
        "mutation_boundary_proof": 2,
        "official_source_witness": 1,
    }
    assert summary["manual_frontier_work_item_family_counts"] == {
        "uk_manual_frontier_heading_facet_candidate": 2,
        "uk_manual_frontier_source_insufficient": 1,
    }
    assert summary["manual_frontier_work_item_authorization_status_counts"] == {
        "manual_claim_required": 2,
        "source_insufficient": 1,
    }


def test_compile_rejection_bucket_ignores_nonblocking_observations() -> None:
    summary = uk_broad_baseline.summarize_results(
        [
            {
                "statute_id": "ukpga/1998/17",
                "score_status": "scored",
                "aligned": 88.8,
                "aligned_excluding_grounding_collateral": 88.8,
                "unaligned": 82.1,
                "n_grounding_collateral": 42,
                "n_replay": 1314,
                "n_oracle": 1424,
                "n_only_in_oracle": 159,
                "n_only_in_replayed": 49,
                "n_compile_rejections": 314,
                "n_blocking_compile_rejections": 7,
            },
        ]
    )

    assert summary["triage_buckets"] == {"residual_after_grounding": 1}


def test_triage_bucket_for_row_is_added_to_one_row_output(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        uk_broad_baseline,
        "score_one",
        lambda _statute_id: {
            "statute_id": "ukpga/1961/60",
            "score_status": "scored",
            "aligned": 22.7,
            "aligned_excluding_grounding_collateral": 22.7,
            "unaligned": 22.7,
            "n_replay": 5,
            "n_oracle": 22,
            "base_source_status": "metadata_only",
        },
    )

    assert uk_broad_baseline.main(["--one", "ukpga/1961/60"]) == 0
    row = json.loads(capsys.readouterr().out)

    assert row["triage_bucket"] == "base_metadata_only_frontier"
    assert row["source_chain_frontier"] is False
    assert row["source_chain_frontier_reason"] == ""
    assert row["source_chain_frontier_reasons"] == []


def test_source_chain_frontier_reason_is_added_to_one_row_output(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(
        uk_broad_baseline,
        "score_one",
        lambda _statute_id: {
            "statute_id": "ukpga/1976/83",
            "score_status": "scored",
            "aligned": 83.7,
            "aligned_excluding_grounding_collateral": 83.7,
            "unaligned": 30.2,
            "n_replay": 123,
            "n_oracle": 147,
            "n_effects": 0,
            "n_ops": 0,
        },
    )

    assert uk_broad_baseline.main(["--one", "ukpga/1976/83"]) == 0
    row = json.loads(capsys.readouterr().out)

    assert row["triage_bucket"] == "no_effect_rows_frontier"
    assert row["source_chain_frontier"] is True
    assert row["source_chain_frontier_reason"] == "effect_rows_absent_or_unpublished"
    assert row["source_chain_frontier_reasons"] == [
        "effect_rows_absent_or_unpublished"
    ]


def test_source_chain_frontier_reason_reports_empty_effect_feed(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(
        uk_broad_baseline,
        "score_one",
        lambda _statute_id: {
            "statute_id": "uksi/2012/1206",
            "score_status": "scored",
            "aligned": 88.6,
            "aligned_excluding_grounding_collateral": 88.6,
            "unaligned": 88.6,
            "n_replay": 31,
            "n_oracle": 35,
            "n_effects": 0,
            "n_ops": 0,
            "compile_rejection_rule_counts": {
                "uk_effect_feed_empty_recorded": 1,
            },
        },
    )

    assert uk_broad_baseline.main(["--one", "uksi/2012/1206"]) == 0
    row = json.loads(capsys.readouterr().out)

    assert row["triage_bucket"] == "no_effect_rows_frontier"
    assert row["source_chain_frontier"] is True
    assert row["source_chain_frontier_reason"] == "effect_feed_empty"
    assert row["source_chain_frontier_reasons"] == ["effect_feed_empty"]


def test_source_chain_frontier_marks_source_insufficient_manual_rows() -> None:
    summary = uk_broad_baseline.summarize_results(
        [
            {
                "statute_id": "uksi/2000/1043",
                "score_status": "scored",
                "aligned": 88.53,
                "aligned_excluding_grounding_collateral": 88.53,
                "unaligned": 88.53,
                "n_grounding_collateral": 0,
                "n_replay": 168,
                "n_oracle": 215,
                "n_only_in_oracle": 47,
                "n_only_in_replayed": 0,
                "n_effects": 54,
                "n_ops": 37,
                "manual_frontier_status_counts": {
                    "deterministic_frontend_supported": 32,
                    "source_insufficient": 19,
                },
            },
        ]
    )

    assert summary["triage_buckets"] == {"manual_compile_frontier_residual": 1}
    assert summary["source_chain_frontier_reasons"] == {
        "manual_frontier_source_insufficient": 1
    }
    assert summary["source_chain_frontier_statutes"] == {
        "manual_frontier_source_insufficient": ["uksi/2000/1043"],
    }


def test_source_chain_frontier_row_preserves_multiple_reasons(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        uk_broad_baseline,
        "score_one",
        lambda _statute_id: {
            "statute_id": "ukpga/1901/7",
            "score_status": "scored",
            "aligned": 91.7,
            "aligned_excluding_grounding_collateral": 91.7,
            "unaligned": 32.8,
            "n_replay": 22,
            "n_oracle": 24,
            "n_effects": 1,
            "n_ops": 0,
            "n_compile_rejections": 1,
            "compile_rejection_rule_counts": {
                "uk_effect_missing_structural_payload_rejected": 1,
            },
            "manual_frontier_status_counts": {"source_insufficient": 1},
        },
    )

    assert uk_broad_baseline.main(["--one", "ukpga/1901/7"]) == 0
    row = json.loads(capsys.readouterr().out)

    assert row["source_chain_frontier"] is True
    assert (
        row["source_chain_frontier_reason"]
        == "effect_rows_missing_structural_payload"
    )
    assert row["source_chain_frontier_reasons"] == [
        "effect_rows_missing_structural_payload",
        "manual_frontier_source_insufficient",
    ]


def test_run_driver_can_fail_on_active_unclassified_residuals(monkeypatch, capsys) -> None:
    def fake_run(*_args, **_kwargs):
        row = {
            "statute_id": "ukpga/1986/61",
            "score_status": "scored",
            "aligned": 50.9,
            "aligned_excluding_grounding_collateral": 50.9,
            "unaligned": 50.5,
            "n_replay": 289,
            "n_oracle": 568,
            "n_only_in_oracle": 279,
            "n_only_in_replayed": 0,
        }
        return SimpleNamespace(returncode=0, stdout=json.dumps(row), stderr="")

    monkeypatch.setattr(uk_broad_baseline.subprocess, "run", fake_run)

    assert (
        uk_broad_baseline.run_driver(
            ["ukpga/1986/61"],
            None,
            fail_on_active_unclassified_residuals=True,
        )
        == 1
    )
    assert "active_unclassified_residuals=1: ukpga/1986/61" in capsys.readouterr().out


def test_run_driver_writes_broad_baseline_report(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    def fake_run(*_args, **_kwargs):
        row = {
            "statute_id": "ukpga/1992/41",
            "score_status": "scored",
            "aligned": 98.7,
            "aligned_excluding_grounding_collateral": 98.7,
            "unaligned": 98.7,
            "n_replay": 10,
            "n_oracle": 10,
        }
        return SimpleNamespace(returncode=0, stdout=json.dumps(row), stderr="")

    monkeypatch.setattr(uk_broad_baseline.subprocess, "run", fake_run)
    snapshot_path = tmp_path / "snapshot.json"
    report_path = tmp_path / "report.json"

    assert (
        uk_broad_baseline.run_driver(
            ["ukpga/1992/41"],
            snapshot_path,
            report_path,
        )
        == 0
    )

    report = json.loads(report_path.read_text())
    assert report["report_kind"] == "uk_broad_baseline_agreement_report"
    assert report["filters"]["ids"] == ["ukpga/1992/41"]
    assert report["filters"]["snapshot_path"] == str(snapshot_path)
    assert report["summary"]["scored_count"] == 1
    assert report["rows"][0]["triage_bucket"] == "high_fidelity_after_grounding"
    assert "Wrote broad-baseline evidence report" in capsys.readouterr().out


def test_run_driver_fail_flag_accepts_manual_frontier_residuals(monkeypatch, capsys) -> None:
    def fake_run(*_args, **_kwargs):
        row = {
            "statute_id": "ukpga/1990/8",
            "score_status": "scored",
            "aligned": 83.81,
            "aligned_excluding_grounding_collateral": 83.81,
            "unaligned": 74.4,
            "n_replay": 6906,
            "n_oracle": 8240,
            "n_only_in_oracle": 1334,
            "n_only_in_replayed": 0,
            "manual_frontier_status_counts": {
                "manual_compile_candidate": 46,
            },
        }
        return SimpleNamespace(returncode=0, stdout=json.dumps(row), stderr="")

    monkeypatch.setattr(uk_broad_baseline.subprocess, "run", fake_run)

    assert (
        uk_broad_baseline.run_driver(
            ["ukpga/1990/8"],
            None,
            fail_on_active_unclassified_residuals=True,
        )
        == 0
    )
    assert "active_unclassified_residuals=0" in capsys.readouterr().out


def test_run_driver_can_fail_on_deterministic_frontend_candidates(
    monkeypatch,
    capsys,
) -> None:
    def fake_run(*_args, **_kwargs):
        row = {
            "statute_id": "ukpga/2008/17",
            "score_status": "scored",
            "aligned": 99.5,
            "aligned_excluding_grounding_collateral": 99.5,
            "unaligned": 88.8,
            "n_replay": 4930,
            "n_oracle": 4955,
            "manual_frontier_status_counts": {
                "deterministic_frontend_candidate": 1,
                "manual_compile_candidate": 16,
            },
            "manual_frontier_deterministic_candidate_rule_counts": {
                "uk_manual_frontier_table_entry_candidate": 1,
            },
        }
        return SimpleNamespace(returncode=0, stdout=json.dumps(row), stderr="")

    monkeypatch.setattr(uk_broad_baseline.subprocess, "run", fake_run)

    assert (
        uk_broad_baseline.run_driver(
            ["ukpga/2008/17"],
            None,
            fail_on_deterministic_frontend_candidates=True,
        )
        == 1
    )
    out = capsys.readouterr().out
    assert "deterministic_frontend_candidates=1: ukpga/2008/17" in out
    assert (
        "deterministic_frontend_candidate_rule_counts: "
        "uk_manual_frontier_table_entry_candidate=1"
    ) in out


def test_run_driver_can_fail_on_manual_frontier_template_gaps(monkeypatch, capsys) -> None:
    def fake_run(*_args, **_kwargs):
        row = {
            "statute_id": "ukpga/2008/17",
            "score_status": "scored",
            "aligned": 86.0,
            "aligned_excluding_grounding_collateral": 86.0,
            "unaligned": 86.0,
            "n_replay": 100,
            "n_oracle": 110,
            "manual_frontier_status_counts": {
                "deterministic_frontend_candidate": 2,
            },
            "manual_frontier_template_status_counts": {
                "not_available": 2,
            },
            "manual_frontier_template_gap_rule_counts": {
                "uk_manual_frontier_parser_or_extraction_candidate": 2,
            },
        }
        return SimpleNamespace(returncode=0, stdout=json.dumps(row), stderr="")

    monkeypatch.setattr(uk_broad_baseline.subprocess, "run", fake_run)

    assert (
        uk_broad_baseline.run_driver(
            ["ukpga/2008/17"],
            None,
            fail_on_manual_frontier_template_gaps=True,
        )
        == 1
    )
    out = capsys.readouterr().out
    assert "manual_frontier_template_gaps: " in out
    assert "uk_manual_frontier_parser_or_extraction_candidate=2" in out


def test_run_driver_can_fail_on_non_manual_source_chain_frontier(
    monkeypatch,
    capsys,
) -> None:
    def fake_run(*_args, **_kwargs):
        row = {
            "statute_id": "uksi/2012/1206",
            "score_status": "scored",
            "aligned": 88.6,
            "aligned_excluding_grounding_collateral": 88.6,
            "unaligned": 88.6,
            "n_replay": 31,
            "n_oracle": 35,
            "n_ops": 0,
            "n_effects": 0,
        }
        return SimpleNamespace(returncode=0, stdout=json.dumps(row), stderr="")

    monkeypatch.setattr(uk_broad_baseline.subprocess, "run", fake_run)

    assert (
        uk_broad_baseline.run_driver(
            ["uksi/2012/1206"],
            None,
            fail_on_non_manual_source_chain_frontier=True,
        )
        == 1
    )
    out = capsys.readouterr().out
    assert "source_chain_frontier[effect_rows_absent_or_unpublished]: uksi/2012/1206" in out
    assert "non_manual_source_chain_frontier=1: uksi/2012/1206" in out


def test_run_driver_non_manual_source_chain_flag_allows_empty_effect_feed(
    monkeypatch,
    capsys,
) -> None:
    def fake_run(*_args, **_kwargs):
        row = {
            "statute_id": "uksi/2012/1206",
            "score_status": "scored",
            "aligned": 88.6,
            "aligned_excluding_grounding_collateral": 88.6,
            "unaligned": 88.6,
            "n_replay": 31,
            "n_oracle": 35,
            "n_ops": 0,
            "n_effects": 0,
            "compile_rejection_rule_counts": {
                "uk_effect_feed_empty_recorded": 1,
            },
        }
        return SimpleNamespace(returncode=0, stdout=json.dumps(row), stderr="")

    monkeypatch.setattr(uk_broad_baseline.subprocess, "run", fake_run)

    assert (
        uk_broad_baseline.run_driver(
            ["uksi/2012/1206"],
            None,
            fail_on_non_manual_source_chain_frontier=True,
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "source_chain_frontier[effect_feed_empty]: uksi/2012/1206" in out
    assert "non_manual_source_chain_frontier=0" in out
    assert "empty_effect_feed_frontier=1: uksi/2012/1206" in out


def test_run_driver_non_manual_source_chain_flag_allows_source_or_oracle_pathology(
    monkeypatch,
    capsys,
) -> None:
    def fake_run(*_args, **_kwargs):
        row = {
            "statute_id": "ukpga/1945/9",
            "score_status": "source_frontier",
            "source_frontier_reason": "base_too_small",
        }
        return SimpleNamespace(returncode=0, stdout=json.dumps(row), stderr="")

    monkeypatch.setattr(uk_broad_baseline.subprocess, "run", fake_run)

    assert (
        uk_broad_baseline.run_driver(
            ["ukpga/1945/9"],
            None,
            fail_on_non_manual_source_chain_frontier=True,
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "source_chain_frontier[base_too_small]: ukpga/1945/9" in out
    assert "non_manual_source_chain_frontier=0" in out
    assert "source_or_oracle_pathology_frontier=1: ukpga/1945/9" in out


def test_run_driver_non_manual_source_chain_flag_allows_manual_source_insufficient(
    monkeypatch,
    capsys,
) -> None:
    def fake_run(*_args, **_kwargs):
        row = {
            "statute_id": "ukpga/1990/8",
            "score_status": "scored",
            "aligned": 83.8,
            "aligned_excluding_grounding_collateral": 83.8,
            "unaligned": 74.4,
            "n_replay": 6906,
            "n_oracle": 8240,
            "manual_frontier_status_counts": {
                "source_insufficient": 2,
            },
        }
        return SimpleNamespace(returncode=0, stdout=json.dumps(row), stderr="")

    monkeypatch.setattr(uk_broad_baseline.subprocess, "run", fake_run)

    assert (
        uk_broad_baseline.run_driver(
            ["ukpga/1990/8"],
            None,
            fail_on_non_manual_source_chain_frontier=True,
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "source_chain_frontier[manual_frontier_source_insufficient]: ukpga/1990/8" in out
    assert "non_manual_source_chain_frontier=0" in out

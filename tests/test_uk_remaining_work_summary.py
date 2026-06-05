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

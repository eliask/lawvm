from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

from lawvm.core.regex_recognition_coverage import (
    REGEX_RECOGNITION_UNCLASSIFIED_GAP,
    RegexRecognitionCoverage,
)
from lawvm.finland.ops import FailedOp
from lawvm.finland.strict_profile import FINLAND_INGESTION_V1
from lawvm.tools import strict_report
from lawvm.finland.op_provenance import serialized_provenance_from_bags


def test_load_strict_run_reads_source_pathology_codes(tmp_path, monkeypatch) -> None:
    strict_dir = tmp_path / "strict_runs"
    strict_dir.mkdir()
    run = strict_dir / "20260328T0000_demo.csv"
    run.write_text(
        "statute_id,n_canonical,n_failed,source_pathology_codes,"
        "source_pathology_diagnostic_reasons,html_noncommensurable_reason,"
        "contingent_effective_sources,fail_reasons,source_incomplete,chain_length,"
        "source_available,elapsed_s,error\n"
        "1994/1472,10,0,"
        "MALFORMED_BROAD_REPLACE_BODY|DESTRUCTIVE_SHAPE_LOSS_RISK,"
        "live_body_dominates_amend_body|partial_body_only,"
        "oracle_extra_scoped_labels:chapter:15/section:1,,"
        "APPLY.SOURCE_PATHOLOGY_DETECTED,0,43,43,1.00,\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(strict_report, "_strict_runs_dir", lambda: Path(strict_dir))

    rows = strict_report._load_strict_run("demo")

    assert rows is not None
    assert rows[0]["source_pathology_codes"] == [
        "MALFORMED_BROAD_REPLACE_BODY",
        "DESTRUCTIVE_SHAPE_LOSS_RISK",
    ]
    assert rows[0]["source_pathology_diagnostic_reasons"] == [
        "live_body_dominates_amend_body",
        "partial_body_only",
    ]
    assert rows[0]["source_pathology_rows"] == []
    assert rows[0]["html_noncommensurable_reason"] == ("oracle_extra_scoped_labels:chapter:15/section:1")
    assert rows[0]["ownership_closure_failed_gates"] == []
    assert rows[0]["ownership_closure_unowned_counts"] == {}
    assert rows[0]["ownership_closure_owned_counts"] == {}
    assert rows[0]["proof_gate_open_signal_count"] == 0
    assert rows[0]["proof_gate_manual_frontier_count"] == 0
    assert rows[0]["proof_gate_coverage_frontier_count"] == 0
    assert rows[0]["proof_gate_other_frontier_count"] == 0
    assert rows[0]["proof_gate_frontier_claim_closure_phase_gate_required_count"] == 0
    assert rows[0]["proof_gate_frontier_claim_closure_phase_gate_authorized_count"] == 0
    assert rows[0]["proof_gate_frontier_claim_closure_replay_authorized_count"] == 0
    assert rows[0]["proof_gate_source_completeness_missing_count"] == 0
    assert rows[0]["proof_gate_source_unit_unresolved_count"] == 0
    assert rows[0]["proof_gate_potential_operation_unresolved_count"] == 0
    assert rows[0]["proof_gate_regex_unclassified_gap_count"] == 0
    assert rows[0]["proof_gate_temporal_resolution_unresolved_count"] == 0
    assert rows[0]["proof_gate_source_pathology_authorization_blocked_count"] == 0
    assert rows[0]["proof_gate_failed_operation_authorization_blocked_count"] == 0
    assert rows[0]["proof_gate_recovery_authorization_blocked_count"] == 0
    assert rows[0]["proof_gate_candidate_set_authorization_blocked_count"] == 0
    assert rows[0]["proof_gate_source_pathology_authorization_status_counts"] == {}
    assert rows[0]["proof_gate_failed_operation_authorization_status_counts"] == {}
    assert rows[0]["proof_gate_recovery_authorization_status_counts"] == {}
    assert rows[0]["proof_gate_candidate_set_authorization_status_counts"] == {}
    assert rows[0]["proof_gate_required_claim_kind_counts"] == {}
    assert rows[0]["proof_gate_frontier_status_counts"] == {}
    assert rows[0]["proof_gate_manual_claim_kind_counts"] == {}
    assert rows[0]["proof_gate_manual_frontier_status_counts"] == {}
    assert rows[0]["proof_gate_coverage_claim_kind_counts"] == {}
    assert rows[0]["proof_gate_coverage_frontier_status_counts"] == {}
    assert rows[0]["proof_gate_other_claim_kind_counts"] == {}
    assert rows[0]["proof_gate_other_frontier_status_counts"] == {}
    assert rows[0]["proof_gate_frontier_claim_closure_status_counts"] == {}
    assert rows[0]["candidate_set_statuses"] == []
    assert rows[0]["candidate_set_blockers"] == []
    assert rows[0]["source_completeness_issue_kinds"] == []
    assert rows[0]["source_completeness_issue_families"] == []
    assert rows[0]["source_completeness_issue_reasons"] == []


def test_load_strict_run_ignores_legacy_adjudication_kinds_column(tmp_path, monkeypatch) -> None:
    strict_dir = tmp_path / "strict_runs"
    strict_dir.mkdir()
    run = strict_dir / "20260328T0000_demo.csv"
    run.write_text(
        "\n".join(
            [
                "statute_id,n_canonical,n_failed,source_pathology_codes,"
                "source_pathology_diagnostic_reasons,html_noncommensurable_reason,"
                "contingent_effective_sources,fail_reasons,source_incomplete,chain_length,"
                "source_available,elapsed_s,error",
                "1994/1472,10,0,"
                "MALFORMED_BROAD_REPLACE_BODY|DESTRUCTIVE_SHAPE_LOSS_RISK,"
                "live_body_dominates_amend_body|partial_body_only,"
                "oracle_extra_scoped_labels:chapter:15/section:1,,"
                "APPLY.SOURCE_PATHOLOGY_DETECTED,0,43,43,1.00,",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(strict_report, "_strict_runs_dir", lambda: Path(strict_dir))

    rows = strict_report._load_strict_run("demo")

    assert rows is not None
    assert rows[0]["projection_kinds"] == []


def test_load_strict_run_ignores_legacy_n_adjudications_column(tmp_path, monkeypatch) -> None:
    strict_dir = tmp_path / "strict_runs"
    strict_dir.mkdir()
    run = strict_dir / "20260328T0000_demo.csv"
    run.write_text(
        "\n".join(
            [
                (
                    "statute_id,n_canonical,n_failed,n_adjudications,"
                    "projection_kinds,source_pathology_codes,source_pathology_diagnostic_reasons,"
                    "html_noncommensurable_reason,contingent_effective_sources,fail_reasons,"
                    "source_incomplete,chain_length,source_available,elapsed_s,error"
                ),
                ("1994/1472,10,0,7,,,,,,APPLY.SOURCE_PATHOLOGY_DETECTED,0,43,43,1.00,"),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(strict_report, "_strict_runs_dir", lambda: Path(strict_dir))

    rows = strict_report._load_strict_run("demo")

    assert rows is not None
    assert rows[0]["n_projection_rows"] == 0


def test_to_json_counts_replay_meta_regex_recognition_coverage() -> None:
    coverage = RegexRecognitionCoverage(
        coverage_id="fi-regex-1",
        jurisdiction="fi",
        recognizer_id="fi_insert_subsection_fallback",
        owner_phase="parse",
        source_artifact_id="2020/1",
        source_text_hash="abc",
        matched_span=(0, 42),
        coverage_status=REGEX_RECOGNITION_UNCLASSIFIED_GAP,
        semantic_slots={"action": "INSERT"},
        ignored_spans=(
            {
                "span": (10, 20),
                "classification": "unclassified",
                "text_preview": "kuitenkin ",
                "could_alter_meaning": True,
            },
        ),
        required_proofs=("regex_skipped_span_classification",),
    )

    payload = strict_report._to_json(
        {
            "statute_id": "2019/1",
            "profile": FINLAND_INGESTION_V1,
            "canonical_ops": [],
            "failed_ops": [],
            "projection_rows": [],
            "source_pathologies": [],
            "strict_fail_reasons": [],
            "regex_recognition_coverage": [coverage.to_dict()],
        }
    )

    assert (
        payload["evidence_surface_report"]["summary"][
            "regex_recognition_coverage_count"
        ]
        == 1
    )
    assert payload["proof_gate_summary"]["regex_recognition_unclassified_gap_count"] == 1
    assert payload["proof_gate_summary"]["open_gate_signal_count"] >= 1


def test_save_strict_run_writes_source_pathology_codes(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(strict_report, "_strict_runs_dir", lambda: Path(tmp_path))

    path = strict_report._save_strict_run(
        [
            {
                "sid": "2001/1234",
                "n_canonical": 4,
                "n_failed": 0,
                "n_projection_rows": 2,
                "n_source_pathologies": 1,
                "n_contingent_effective_dates": 0,
                "projection_kinds": ["ELAB.SOURCE_PATHOLOGY", "ELAB.STRICT_REJECTED_SOURCE_PATHOLOGY"],
                "source_pathology_codes": ["DESTRUCTIVE_SHAPE_LOSS_RISK"],
                "source_pathology_rows": [
                    {
                        "code": "DESTRUCTIVE_SHAPE_LOSS_RISK",
                        "message": "Replay encountered a source pathology.",
                        "source_statute": "2001/1234",
                        "target_unit_kind": "section",
                        "target_label": "6 §",
                        "detail": {"diagnostic_reason": "partial_body_only"},
                    }
                ],
                "source_pathology_diagnostic_reasons": ["partial_body_only"],
                "source_completeness_issue_kinds": ["APPLY.SOURCE_INCOMPLETE"],
                "source_completeness_issue_families": ["oracle_version_effective_after_cutoff"],
                "source_completeness_issue_reasons": ["2020/1 eff 2020-02-01 > cutoff 2020-01-01"],
                "html_noncommensurable_reason": "oracle_extra_scoped_labels:chapter:15/section:1",
                "contingent_effective_sources": [],
                "fail_reasons": ["APPLY.SOURCE_PATHOLOGY_DETECTED"],
                "ownership_closure_status": "open",
                "ownership_closure_failed_gates": [
                    "candidate_set_fi_strict_report_operation_cue_coverage_partial"
                ],
                "ownership_closure_unowned_counts": {
                    "incomplete_candidate_set_coverages": 4,
                },
                "ownership_closure_owned_counts": {
                    "canonical_ops": 4,
                    "failed_ops_visible": 1,
                },
                "proof_gate_open_signal_count": 18,
                "proof_gate_manual_frontier_count": 1,
                "proof_gate_coverage_frontier_count": 4,
                "proof_gate_other_frontier_count": 2,
                "proof_gate_frontier_claim_closure_phase_gate_required_count": 2,
                "proof_gate_frontier_claim_closure_phase_gate_authorized_count": 1,
                "proof_gate_frontier_claim_closure_replay_authorized_count": 1,
                "proof_gate_source_completeness_missing_count": 3,
                "proof_gate_source_unit_unresolved_count": 3,
                "proof_gate_potential_operation_unresolved_count": 5,
                "proof_gate_regex_unclassified_gap_count": 3,
                "proof_gate_temporal_resolution_unresolved_count": 4,
                "proof_gate_source_pathology_authorization_blocked_count": 6,
                "proof_gate_failed_operation_authorization_blocked_count": 7,
                "proof_gate_recovery_authorization_blocked_count": 2,
                "proof_gate_candidate_set_authorization_blocked_count": 4,
                "proof_gate_source_pathology_authorization_status_counts": {
                    "source_pathology_blocked": 6,
                },
                "proof_gate_failed_operation_authorization_status_counts": {
                    "failed_operation_blocked": 7,
                },
                "proof_gate_recovery_authorization_status_counts": {
                    "strict_recovery_blocked": 2,
                },
                "proof_gate_candidate_set_authorization_status_counts": {
                    "candidate_set_incomplete_not_replay_authority": 4,
                },
                "proof_gate_required_claim_kind_counts": {
                    "fi.v1.FAILED_OPERATION_RESOLUTION": 1,
                    "fi.v1.SOURCE_UNIT_ENUMERATION_CERTIFICATE": 2,
                    "source_pathology_resolution": 2,
                },
                "proof_gate_frontier_status_counts": {
                    "failed_operation_frontier": 1,
                    "partial_candidate_set_frontier": 4,
                    "source_pathology_frontier": 2,
                },
                "proof_gate_manual_claim_kind_counts": {
                    "fi.v1.FAILED_OPERATION_RESOLUTION": 1,
                },
                "proof_gate_manual_frontier_status_counts": {
                    "failed_operation_frontier": 1,
                },
                "proof_gate_coverage_claim_kind_counts": {
                    "fi.v1.SOURCE_UNIT_ENUMERATION_CERTIFICATE": 2,
                },
                "proof_gate_coverage_frontier_status_counts": {
                    "partial_candidate_set_frontier": 4,
                },
                "proof_gate_other_claim_kind_counts": {
                    "source_pathology_resolution": 2,
                },
                "proof_gate_other_frontier_status_counts": {
                    "source_pathology_frontier": 2,
                },
                "proof_gate_frontier_claim_closure_status_counts": {
                    "evidence_policy_satisfied_phase_gate_required": 2,
                    "phase_replay_gate_authorized": 1,
                },
                "candidate_set_statuses": [
                    "fi_strict_report_operation_cue_coverage:partial"
                ],
                "candidate_set_blockers": [
                    "fi_strict_report_operation_cue_coverage:operation_cue_coverage_gap"
                ],
                "source_incomplete": False,
                "chain_length": 1,
                "source_available": 1,
                "elapsed_s": 0.5,
                "error": "",
            }
        ],
        "demo",
        "2026-03-28T12:00",
    )

    text = path.read_text(encoding="utf-8")
    assert "n_source_pathologies" in text
    assert "source_pathology_codes" in text
    assert "source_pathology_rows_json" in text
    assert "source_pathology_diagnostic_reasons" in text
    assert "source_completeness_issue_kinds" in text
    assert "source_completeness_issue_families" in text
    assert "source_completeness_issue_reasons" in text
    assert "html_noncommensurable_reason" in text
    assert "ownership_closure_failed_gates" in text
    assert "ownership_closure_unowned_counts" in text
    assert "ownership_closure_owned_counts" in text
    assert "proof_gate_open_signal_count" in text
    assert "proof_gate_source_completeness_missing_count" in text
    assert "proof_gate_source_unit_unresolved_count" in text
    assert "proof_gate_potential_operation_unresolved_count" in text
    assert "proof_gate_regex_unclassified_gap_count" in text
    assert "proof_gate_temporal_resolution_unresolved_count" in text
    assert "proof_gate_source_pathology_authorization_blocked_count" in text
    assert "proof_gate_failed_operation_authorization_blocked_count" in text
    assert "proof_gate_recovery_authorization_blocked_count" in text
    assert "proof_gate_candidate_set_authorization_blocked_count" in text
    assert "proof_gate_source_pathology_authorization_status_counts" in text
    assert "proof_gate_failed_operation_authorization_status_counts" in text
    assert "proof_gate_recovery_authorization_status_counts" in text
    assert "proof_gate_candidate_set_authorization_status_counts" in text
    assert "proof_gate_required_claim_kind_counts" in text
    assert "proof_gate_manual_claim_kind_counts" in text
    assert "proof_gate_coverage_claim_kind_counts" in text
    assert "proof_gate_other_claim_kind_counts" in text
    assert "proof_gate_frontier_claim_closure_phase_gate_required_count" in text
    assert "proof_gate_frontier_claim_closure_phase_gate_authorized_count" in text
    assert "proof_gate_frontier_claim_closure_replay_authorized_count" in text
    assert "proof_gate_frontier_claim_closure_status_counts" in text
    assert "fi.v1.FAILED_OPERATION_RESOLUTION" in text
    assert "source_pathology_resolution" in text
    assert "phase_replay_gate_authorized" in text
    assert "partial_candidate_set_frontier" in text
    assert "candidate_set_statuses" in text
    assert "candidate_set_blockers" in text
    assert "DESTRUCTIVE_SHAPE_LOSS_RISK" in text
    assert "partial_body_only" in text
    assert "APPLY.SOURCE_INCOMPLETE" in text
    assert "oracle_version_effective_after_cutoff" in text
    assert "2020/1 eff 2020-02-01 > cutoff 2020-01-01" in text
    assert "candidate_set_fi_strict_report_operation_cue_coverage_partial" in text
    assert "fi_strict_report_operation_cue_coverage:partial" in text
    assert "fi_strict_report_operation_cue_coverage:operation_cue_coverage_gap" in text
    assert '""target_unit_kind"": ""section""' in text
    assert '""target_label"": ""6 \\u00a7""' in text
    assert "oracle_extra_scoped_labels:chapter:15/section:1" in text

    loaded = strict_report._load_strict_run("demo")
    assert loaded is not None
    assert loaded[0]["proof_gate_open_signal_count"] == 18
    assert loaded[0]["ownership_closure_unowned_counts"] == {
        "incomplete_candidate_set_coverages": 4,
    }
    assert loaded[0]["ownership_closure_owned_counts"] == {
        "canonical_ops": 4,
        "failed_ops_visible": 1,
    }
    assert loaded[0]["proof_gate_manual_frontier_count"] == 1
    assert loaded[0]["proof_gate_coverage_frontier_count"] == 4
    assert loaded[0]["proof_gate_other_frontier_count"] == 2
    assert loaded[0]["proof_gate_frontier_claim_closure_phase_gate_required_count"] == 2
    assert loaded[0]["proof_gate_frontier_claim_closure_phase_gate_authorized_count"] == 1
    assert loaded[0]["proof_gate_frontier_claim_closure_replay_authorized_count"] == 1
    assert loaded[0]["proof_gate_source_completeness_missing_count"] == 3
    assert loaded[0]["proof_gate_source_unit_unresolved_count"] == 3
    assert loaded[0]["proof_gate_potential_operation_unresolved_count"] == 5
    assert loaded[0]["proof_gate_regex_unclassified_gap_count"] == 3
    assert loaded[0]["proof_gate_temporal_resolution_unresolved_count"] == 4
    assert loaded[0]["proof_gate_source_pathology_authorization_blocked_count"] == 6
    assert loaded[0]["proof_gate_failed_operation_authorization_blocked_count"] == 7
    assert loaded[0]["proof_gate_recovery_authorization_blocked_count"] == 2
    assert loaded[0]["proof_gate_candidate_set_authorization_blocked_count"] == 4
    assert loaded[0]["proof_gate_source_pathology_authorization_status_counts"] == {
        "source_pathology_blocked": 6,
    }
    assert loaded[0]["proof_gate_failed_operation_authorization_status_counts"] == {
        "failed_operation_blocked": 7,
    }
    assert loaded[0]["proof_gate_recovery_authorization_status_counts"] == {
        "strict_recovery_blocked": 2,
    }
    assert loaded[0]["proof_gate_candidate_set_authorization_status_counts"] == {
        "candidate_set_incomplete_not_replay_authority": 4,
    }
    assert loaded[0]["proof_gate_required_claim_kind_counts"] == {
        "fi.v1.FAILED_OPERATION_RESOLUTION": 1,
        "source_pathology_resolution": 2,
        "fi.v1.SOURCE_UNIT_ENUMERATION_CERTIFICATE": 2,
    }
    assert loaded[0]["proof_gate_frontier_status_counts"] == {
        "failed_operation_frontier": 1,
        "partial_candidate_set_frontier": 4,
        "source_pathology_frontier": 2,
    }
    assert loaded[0]["proof_gate_manual_claim_kind_counts"] == {
        "fi.v1.FAILED_OPERATION_RESOLUTION": 1,
    }
    assert loaded[0]["proof_gate_manual_frontier_status_counts"] == {
        "failed_operation_frontier": 1,
    }
    assert loaded[0]["proof_gate_coverage_claim_kind_counts"] == {
        "fi.v1.SOURCE_UNIT_ENUMERATION_CERTIFICATE": 2,
    }
    assert loaded[0]["proof_gate_coverage_frontier_status_counts"] == {
        "partial_candidate_set_frontier": 4,
    }
    assert loaded[0]["proof_gate_other_claim_kind_counts"] == {
        "source_pathology_resolution": 2,
    }
    assert loaded[0]["proof_gate_other_frontier_status_counts"] == {
        "source_pathology_frontier": 2,
    }
    assert loaded[0]["proof_gate_frontier_claim_closure_status_counts"] == {
        "evidence_policy_satisfied_phase_gate_required": 2,
        "phase_replay_gate_authorized": 1,
    }


def test_load_strict_run_reads_source_pathology_rows_json(tmp_path, monkeypatch) -> None:
    strict_dir = tmp_path / "strict_runs"
    strict_dir.mkdir()
    run = strict_dir / "20260328T0000_demo.csv"
    rows_json = json.dumps(
        [
            {
                "code": "DESTRUCTIVE_SHAPE_LOSS_RISK",
                "target_unit_kind": "section",
                "target_label": "6 §",
                "detail": {"diagnostic_reason": "partial_body_only"},
            }
        ],
        ensure_ascii=False,
    ).replace('"', '""')
    run.write_text(
        "\n".join(
            [
                (
                    "statute_id,n_canonical,n_failed,n_projection_rows,n_source_pathologies,"
                    "n_contingent_effective_dates,projection_kinds,source_pathology_codes,"
                    "source_pathology_rows_json,source_pathology_diagnostic_reasons,"
                    "html_noncommensurable_reason,contingent_effective_sources,fail_reasons,"
                    "source_incomplete,chain_length,source_available,elapsed_s,error"
                ),
                (
                    "1994/1472,10,0,2,1,0,"
                    "APPLY.SOURCE_PATHOLOGY_DETECTED,"
                    "DESTRUCTIVE_SHAPE_LOSS_RISK,"
                    f'"{rows_json}",'
                    "partial_body_only,,APPLY.SOURCE_PATHOLOGY_DETECTED,0,43,43,1.00,"
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(strict_report, "_strict_runs_dir", lambda: Path(strict_dir))

    rows = strict_report._load_strict_run("demo")

    assert rows is not None
    assert rows[0]["source_pathology_rows"] == [
        {
            "code": "DESTRUCTIVE_SHAPE_LOSS_RISK",
            "target_unit_kind": "section",
            "target_label": "6 §",
            "detail": {"diagnostic_reason": "partial_body_only"},
        }
    ]


def test_show_corpus_summary_reports_source_pathology_codes(capsys) -> None:
    strict_report._show_corpus_summary(
        [
            {
                "sid": "1994/1472",
                "source_incomplete": False,
                "n_canonical": 4,
                "n_failed": 0,
                "n_projection_rows": 2,
                "n_source_pathologies": 2,
                "n_contingent_effective_dates": 1,
                "projection_kinds": ["ELAB.SOURCE_PATHOLOGY", "ELAB.STRICT_REJECTED_SOURCE_PATHOLOGY"],
                "source_pathology_codes": ["MALFORMED_BROAD_REPLACE_BODY", "DESTRUCTIVE_SHAPE_LOSS_RISK"],
                "source_pathology_diagnostic_reasons": ["live_body_dominates_amend_body", "partial_body_only"],
                "source_completeness_issue_kinds": ["APPLY.SOURCE_INCOMPLETE"],
                "source_completeness_issue_families": ["oracle_version_effective_after_cutoff"],
                "source_completeness_issue_reasons": ["2020/1 eff 2020-02-01 > cutoff 2020-01-01"],
                "html_noncommensurable_reason": "oracle_extra_scoped_labels:chapter:15/section:1",
                "contingent_effective_sources": ["2005/544"],
                "fail_reasons": ["APPLY.SOURCE_PATHOLOGY_DETECTED"],
                "ownership_closure_failed_gates": [
                    "candidate_set_fi_strict_report_operation_cue_coverage_partial"
                ],
                "ownership_closure_unowned_counts": {
                    "incomplete_candidate_set_coverages": 4,
                },
                "ownership_closure_owned_counts": {
                    "canonical_ops": 4,
                    "failed_ops_visible": 1,
                },
                "proof_gate_open_signal_count": 18,
                "proof_gate_manual_frontier_count": 1,
                "proof_gate_coverage_frontier_count": 4,
                "proof_gate_other_frontier_count": 2,
                "proof_gate_frontier_claim_closure_phase_gate_required_count": 2,
                "proof_gate_frontier_claim_closure_phase_gate_authorized_count": 1,
                "proof_gate_frontier_claim_closure_replay_authorized_count": 1,
                "proof_gate_source_completeness_missing_count": 3,
                "proof_gate_source_unit_unresolved_count": 3,
                "proof_gate_potential_operation_unresolved_count": 5,
                "proof_gate_regex_unclassified_gap_count": 3,
                "proof_gate_temporal_resolution_unresolved_count": 4,
                "proof_gate_source_pathology_authorization_blocked_count": 6,
                "proof_gate_failed_operation_authorization_blocked_count": 7,
                "proof_gate_recovery_authorization_blocked_count": 2,
                "proof_gate_candidate_set_authorization_blocked_count": 4,
                "proof_gate_source_pathology_authorization_status_counts": {
                    "source_pathology_blocked": 6,
                },
                "proof_gate_failed_operation_authorization_status_counts": {
                    "failed_operation_blocked": 7,
                },
                "proof_gate_recovery_authorization_status_counts": {
                    "strict_recovery_blocked": 2,
                },
                "proof_gate_candidate_set_authorization_status_counts": {
                    "candidate_set_incomplete_not_replay_authority": 4,
                },
                "proof_gate_required_claim_kind_counts": {
                    "fi.v1.FAILED_OPERATION_RESOLUTION": 1,
                    "source_pathology_resolution": 2,
                    "fi.v1.SOURCE_UNIT_ENUMERATION_CERTIFICATE": 2,
                },
                "proof_gate_frontier_status_counts": {
                    "failed_operation_frontier": 1,
                    "partial_candidate_set_frontier": 4,
                    "source_pathology_frontier": 2,
                },
                "proof_gate_manual_claim_kind_counts": {
                    "fi.v1.FAILED_OPERATION_RESOLUTION": 1,
                },
                "proof_gate_manual_frontier_status_counts": {
                    "failed_operation_frontier": 1,
                },
                "proof_gate_coverage_claim_kind_counts": {
                    "fi.v1.SOURCE_UNIT_ENUMERATION_CERTIFICATE": 2,
                },
                "proof_gate_coverage_frontier_status_counts": {
                    "partial_candidate_set_frontier": 4,
                },
                "proof_gate_other_claim_kind_counts": {
                    "source_pathology_resolution": 2,
                },
                "proof_gate_other_frontier_status_counts": {
                    "source_pathology_frontier": 2,
                },
                "proof_gate_frontier_claim_closure_status_counts": {
                    "evidence_policy_satisfied_phase_gate_required": 2,
                    "phase_replay_gate_authorized": 1,
                },
                "candidate_set_statuses": [
                    "fi_strict_report_operation_cue_coverage:partial"
                ],
                "candidate_set_blockers": [
                    "fi_strict_report_operation_cue_coverage:operation_cue_coverage_gap"
                ],
                "chain_length": 43,
                "source_available": 43,
                "error": "",
            }
        ],
        "demo",
    )

    out = capsys.readouterr().out
    assert "Source pathology codes" in out
    assert "MALFORMED_BROAD_REPLACE_BODY" in out
    assert "DESTRUCTIVE_SHAPE_LOSS_RISK" in out
    assert "Source pathology diagnostic reasons" in out
    assert "live_body_dominates_amend_body" in out
    assert "partial_body_only" in out
    assert "Source completeness issue kinds" in out
    assert "APPLY.SOURCE_INCOMPLETE" in out
    assert "Source completeness issue families" in out
    assert "oracle_version_effective_after_cutoff" in out
    assert "Source completeness issue reasons" in out
    assert "2020/1 eff 2020-02-01 > cutoff 2020-01-01" in out
    assert "HTML/XML noncommensurable reasons" in out
    assert "oracle_extra_scoped_labels:chapter:15/section:1" in out
    assert "Contingent effective-date sources" in out
    assert "2005/544" in out
    assert "Ownership closure failed gates" in out
    assert "candidate_set_fi_strict_report_operation_cue_coverage_partial" in out
    assert "Ownership closure unowned counts" in out
    assert "incomplete_candidate_set_coverages" in out
    assert "Ownership closure owned counts" in out
    assert "canonical_ops" in out
    assert "failed_ops_visible" in out
    assert "Proof-gate summary" in out
    assert "open gate signals      : 18" in out
    assert "manual frontiers       : 1" in out
    assert "coverage frontiers     : 4" in out
    assert "other frontiers        : 2" in out
    assert "closure phase gates required: 2" in out
    assert "closure phase gates authorized: 1" in out
    assert "closure replay-authorized rows: 1" in out
    assert "missing source-chain facts: 3" in out
    assert "unresolved source units: 3" in out
    assert "unresolved potential ops: 5" in out
    assert "regex unclassified gaps: 3" in out
    assert "blocked source pathology authorizations: 6" in out
    assert "blocked failed operation authorizations: 7" in out
    assert "blocked candidate set authorizations: 4" in out
    assert "source pathology authorization statuses" in out
    assert "source_pathology_blocked" in out
    assert "failed operation authorization statuses" in out
    assert "failed_operation_blocked" in out
    assert "recovery authorization statuses" in out
    assert "strict_recovery_blocked" in out
    assert "candidate set authorization statuses" in out
    assert "candidate_set_incomplete_not_replay_authority" in out
    assert "fi.v1.FAILED_OPERATION_RESOLUTION" in out
    assert "source_pathology_resolution" in out
    assert "partial_candidate_set_frontier" in out
    assert "1.00 signals/statute" in out
    assert "manual frontier claim kinds" in out
    assert "coverage proof requirements" in out
    assert "frontier claim closure statuses" in out
    assert "evidence_policy_satisfied_phase_gate_required" in out
    assert "phase_replay_gate_authorized" in out
    assert "Candidate-set statuses" in out
    assert "fi_strict_report_operation_cue_coverage:partial" in out
    assert "Candidate-set blockers" in out
    assert "fi_strict_report_operation_cue_coverage:operation_cue_coverage_gap" in out


def test_print_facade_summary_includes_source_pathology_reasons(capsys) -> None:
    facade = SimpleNamespace(
        source_pathology_rows=lambda: (
            {
                "code": "PARTIAL_WHOLE_SECTION_PAYLOAD",
                "detail": {"diagnostic_reason": "live_body_dominates_amend_body"},
            },
        ),
    )

    strict_report._print_facade_summary(facade)

    out = capsys.readouterr().out
    assert "Pathologies  : PARTIAL_WHOLE_SECTION_PAYLOAD" in out
    assert "Pathology reasons : live_body_dominates_amend_body" in out


def test_print_facade_summary_accepts_explicit_html_noncomm_reason(capsys) -> None:
    facade = SimpleNamespace(
        source_pathology_rows=lambda: (),
    )

    strict_report._print_facade_summary(
        facade,
        html_noncommensurable_reason="oracle_extra_scoped_labels:chapter:15/section:1",
    )

    out = capsys.readouterr().out
    assert "HTML/XML reason : oracle_extra_scoped_labels:chapter:15/section:1" in out


def test_format_report_verbose_prefers_typed_provenance_tags() -> None:
    cr = {
        "statute_id": "2001/1234",
        "replay_mode": "legal_pit",
        "compile_mode": "strict",
        "profile": FINLAND_INGESTION_V1,
        "compiled_ops": [
            {
                "op_id": "op-1",
                "description": "typed extraction op",
                "provenance": serialized_provenance_from_bags(extraction_tags=("extraction_fallback_heuristic",)),
            },
            {
                "op_id": "op-2",
                "description": "typed scope op",
                "provenance": serialized_provenance_from_bags(scope_tags=("chapter_scope_from_preamble",)),
            },
            {
                "op_id": "op-3",
                "description": "typed target op",
                "provenance": serialized_provenance_from_bags(target_guessing_tags=("normalize_item_like_target",)),
            },
        ],
    }

    out = strict_report._format_report(cr, verbose=True)

    assert "extraction_fallback_heuristic" in out
    assert "chapter_scope_from_preamble" in out
    assert "normalize_item_like_target" in out


def test_format_report_verbose_ignores_legacy_resolution_hint_tags() -> None:
    cr = {
        "statute_id": "2001/1234",
        "replay_mode": "legal_pit",
        "compile_mode": "strict",
        "profile": FINLAND_INGESTION_V1,
        "compiled_ops": [
            {
                "op_id": "op-1",
                "description": "legacy only op",
                "resolution_hint": "legacy_only_tag",
            },
        ],
    }

    out = strict_report._format_report(cr, verbose=True)

    assert "legacy_only_tag" not in out
    assert "canonical" in out


def test_to_json_preserves_projection_row_detail() -> None:
    cr = SimpleNamespace(
        statute_id="2001/1234",
        replay_mode="legal_pit",
        compile_mode="strict",
        profile=FINLAND_INGESTION_V1,
        projection_rows=lambda: (
            {
                "kind": "PARSE.SEMANTIC_COLLAPSE_MOVE_RENUMBER",
                "message": "destinationless move/relabel observed",
                "source": "2020/1",
                "detail": {
                    "collapse_kind": "destinationless_move_relabel",
                    "destination_missing": True,
                },
            },
        ),
    )

    payload = strict_report._to_json(cr)

    assert payload["projection_rows"][0]["detail"]["collapse_kind"] == "destinationless_move_relabel"
    assert payload["projection_rows"][0]["detail"]["destination_missing"] is True
    assert payload["source_pathologies"] == []


def test_format_report_surfaces_source_completeness_issue_reason() -> None:
    cr = {
        "statute_id": "2001/1234",
        "profile": FINLAND_INGESTION_V1,
        "canonical_ops": [],
        "failed_ops": [],
        "projection_rows": [
            {
                "kind": "APPLY.SOURCE_INCOMPLETE",
                "message": "Oracle/source lineage appears incomplete or suspect.",
                "source": "",
                "detail": {
                    "oracle_suspect": "2020/1 eff 2020-02-01 > cutoff 2020-01-01",
                    "message": "Oracle/source lineage appears incomplete or suspect.",
                },
            }
        ],
        "source_pathologies": [],
        "strict_fail_reasons": ["APPLY.SOURCE_INCOMPLETE"],
        "source_adjudication": SimpleNamespace(
            lineage=({"included": True, "effective_date": "2020-01-01"},),
        ),
    }

    out = strict_report._format_report(cr, verbose=False)
    payload = strict_report._to_json(cr)
    evidence = payload["evidence_surface_report"]

    assert "source_available : 1  (100%)" in out
    assert (
        "APPLY.SOURCE_INCOMPLETE[oracle_version_effective_after_cutoff: "
        "2020/1 eff 2020-02-01 > cutoff 2020-01-01]"
    ) in out
    assert payload["source_completeness_issues"][0]["kind"] == "APPLY.SOURCE_INCOMPLETE"
    assert (
        payload["source_completeness_issues"][0]["issue_family"]
        == "oracle_version_effective_after_cutoff"
    )
    assert (
        payload["source_completeness_issues"][0]["detail"]["oracle_suspect"]
        == "2020/1 eff 2020-02-01 > cutoff 2020-01-01"
    )
    assert evidence["summary"]["source_completeness_issue_count"] == 1
    assert evidence["summary"]["source_completeness_issue_kind_counts"] == {
        "APPLY.SOURCE_INCOMPLETE": 1
    }
    assert evidence["summary"]["source_completeness_issue_family_counts"] == {
        "oracle_version_effective_after_cutoff": 1
    }
    issue_rows = [
        row for row in evidence["rows"] if row["surface"] == "source_completeness_issue"
    ]
    assert issue_rows[0]["detail"]["oracle_suspect"] == (
        "2020/1 eff 2020-02-01 > cutoff 2020-01-01"
    )


def test_to_json_preserves_failed_op_rule_and_scope_detail() -> None:
    failed_op = FailedOp.from_scope(
        amendment_id="2020/1",
        description="replace chapter-scoped section",
        reason="no deterministic path",
        reason_code="no_deterministic_path",
        target_unit_kind="section",
        target_section="5",
        target_chapter="4",
    )
    cr = SimpleNamespace(
        statute_id="2001/1234",
        replay_mode="legal_pit",
        compile_mode="strict",
        profile=FINLAND_INGESTION_V1,
        canonical_ops=[],
        failed_ops=[failed_op],
        projection_rows=lambda: (),
        source_pathology_rows=lambda: (),
    )

    payload = strict_report._to_json(cr)

    assert payload["failed_ops"] == [
        {
            "amendment_id": "2020/1",
            "description": "replace chapter-scoped section",
            "reason": "no deterministic path",
            "reason_code": "no_deterministic_path",
            "target_statute_id": None,
            "target_unit_kind": "section",
            "target_section": "5",
            "target_chapter": "4",
            "target_part": None,
            "target_subsection": None,
            "target_item": None,
            "source": "2020/1",
            "target_kind": "P",
        }
    ]
    authorizations = payload["failed_operation_execution_authorizations"]
    assert len(authorizations) == 1
    assert authorizations[0]["authorization_status"] == "failed_operation_not_replay_authority"
    assert authorizations[0]["executable"] is False
    assert authorizations[0]["replay_authorized"] is False
    assert authorizations[0]["owner_phase"] == "replay_apply"
    assert authorizations[0]["detail"]["target_label"] == "chapter:4/section:5"
    frontier_items = payload["failed_operation_frontier_work_items"]
    assert len(frontier_items) == 1
    assert frontier_items[0]["frontier_family"] == "fi_failed_operation_resolution"
    assert frontier_items[0]["required_claim_kind"] == "fi.v1.FAILED_OPERATION_RESOLUTION"
    assert frontier_items[0]["authorization_status"] == "failed_operation_not_replay_authority"
    assert frontier_items[0]["candidate_targets"] == ["chapter:4/section:5"]
    assert frontier_items[0]["source_witness"]["source_role"] == "finland_failed_operation"
    assert frontier_items[0]["source_witness"]["source_lane"] == "failed_operation"
    assert frontier_items[0]["source_witness"]["preview_digest"]
    assert payload["ownership_closure_coverage"]["unowned_counts"][
        "failed_ops_without_frontier_work_item"
    ] == 0
    assert "failed_ops_present" in payload["ownership_closure_coverage"]["failed_gates"]
    assert payload["evidence_surface_report"]["summary"][
        "failed_operation_frontier_work_item_count"
    ] == 1
    assert payload["evidence_surface_report"]["summary"][
        "frontier_claim_template_status_counts"
    ] == {"available": 1}
    assert payload["evidence_surface_report"]["summary"][
        "frontier_claim_template_kind_counts"
    ] == {"fi.v1.FAILED_OPERATION_RESOLUTION": 1}
    source_unit_coverages = payload["source_unit_coverages"]
    assert len(source_unit_coverages) == 1
    assert source_unit_coverages[0]["coverage_status"] == "frontier_witnessed"
    assert source_unit_coverages[0]["source_role"] == "finland_failed_operation"
    assert payload["evidence_surface_report"]["summary"]["source_unit_coverage_count"] == 1
    assert payload["evidence_surface_report"]["summary"][
        "source_unit_coverage_status_counts"
    ] == {"frontier_witnessed": 1}
    potential_ops = payload["potential_operations"]
    assert len(potential_ops) == 1
    assert potential_ops[0]["classification"] == "failed"
    assert potential_ops[0]["operation_family"] == "fi_failed_operation"
    assert potential_ops[0]["target"] == "chapter:4/section:5"
    assert potential_ops[0]["source_anchor"]["basis"] == (
        "failed_operation_frontier_source_witness"
    )
    assert potential_ops[0]["source_anchor"]["frontier_work_item_id"] == (
        frontier_items[0]["work_item_id"]
    )
    assert potential_ops[0]["source_anchor"]["source_role"] == "finland_failed_operation"
    assert potential_ops[0]["source_anchor"]["source_lane"] == "failed_operation"
    assert potential_ops[0]["source_anchor"]["preview_digest"] == (
        frontier_items[0]["source_witness"]["preview_digest"]
    )
    assert "replay_authorization" in potential_ops[0]["source_anchor"]["does_not_claim"]
    assert potential_ops[0]["safe_default"] == (
        "treat_failed_operation_as_non_executable_frontier_until_source_target_payload_and_boundary_are_proven"
    )
    operation_cue_certificate = next(
        row
        for row in payload["strict_report_candidate_set_coverages"]
        if row["candidate_set_kind"] == "fi_strict_report_operation_cue_coverage"
    )
    assert operation_cue_certificate["candidate_ids"] == [
        potential_ops[0]["potential_operation_id"]
    ]
    assert operation_cue_certificate["completeness_status"] == "partial"
    assert operation_cue_certificate["visible_scope"] == (
        "strict_report_potential_operation_rows"
    )
    assert payload["evidence_surface_report"]["summary"]["potential_operation_count"] == 1
    assert payload["evidence_surface_report"]["summary"][
        "potential_operation_classification_counts"
    ] == {"failed": 1}
    proof_gates = payload["proof_gate_summary"]
    assert proof_gates["schema"] == "lawvm.fi.strict_report.proof_gate_summary.v1"
    assert proof_gates["closed"] is False
    assert proof_gates["manual_claim_frontier_count"] == 1
    assert proof_gates["coverage_frontier_count"] == 4
    assert proof_gates["other_frontier_count"] == 0
    assert proof_gates["open_gate_signal_count"] == 23
    assert proof_gates["failed_operation_authorization_blocked_count"] == 1
    assert proof_gates["failed_operation_authorization_status_counts"] == {
        "failed_operation_not_replay_authority": 1,
    }
    assert proof_gates["candidate_set_authorization_blocked_count"] == 4
    assert proof_gates["candidate_set_authorization_status_counts"] == {
        "candidate_set_incomplete_not_replay_authority": 4,
    }
    assert proof_gates["frontier_status_counts"] == {
        "failed_operation_frontier": 1,
        "partial_candidate_set_frontier": 4,
    }
    assert proof_gates["required_claim_kind_counts"] == {
        "fi.v1.FAILED_OPERATION_RESOLUTION": 1,
        "fi.v1.OPERATION_CUE_EXHAUSTIVENESS_CERTIFICATE": 2,
        "fi.v1.SOURCE_UNIT_ENUMERATION_CERTIFICATE": 2,
    }
    assert proof_gates["manual_frontier_required_claim_kind_counts"] == {
        "fi.v1.FAILED_OPERATION_RESOLUTION": 1,
    }
    assert proof_gates["coverage_frontier_required_claim_kind_counts"] == {
        "fi.v1.OPERATION_CUE_EXHAUSTIVENESS_CERTIFICATE": 2,
        "fi.v1.SOURCE_UNIT_ENUMERATION_CERTIFICATE": 2,
    }
    assert proof_gates["manual_frontier_status_counts"] == {
        "failed_operation_frontier": 1,
    }
    assert proof_gates["coverage_frontier_status_counts"] == {
        "partial_candidate_set_frontier": 4,
    }
    assert proof_gates["other_frontier_required_claim_kind_counts"] == {}
    assert proof_gates["other_frontier_status_counts"] == {}
    assert proof_gates["candidate_set_completeness_counts"] == {"partial": 4}
    assert "replay_authorization" in proof_gates["does_not_claim"]


def test_format_report_includes_compact_proof_gate_summary() -> None:
    failed_op = FailedOp.from_scope(
        amendment_id="2020/1",
        description="replace chapter-scoped section",
        reason="no deterministic path",
        reason_code="no_deterministic_path",
        target_unit_kind="section",
        target_section="5",
        target_chapter="4",
    )
    cr = SimpleNamespace(
        statute_id="2001/1234",
        replay_mode="legal_pit",
        compile_mode="strict",
        profile=FINLAND_INGESTION_V1,
        canonical_ops=[],
        failed_ops=[failed_op],
        projection_rows=lambda: (),
        source_pathology_rows=lambda: (),
    )

    out = strict_report._format_report(cr)

    assert "Ownership closure" in out
    assert "owned counts     :" in out
    assert "failed_ops_visible=1" in out
    assert "Proof gate summary" in out
    assert "manual frontiers : 1" in out
    assert "coverage frontiers: 4" in out
    assert "other frontiers   : 0" in out
    assert "source units      : 0 unresolved" in out
    assert "source chain      : 0 missing" in out
    assert "potential ops     : 0 unresolved" in out
    assert "open gate signals: 23" in out
    assert "regex gaps        : 0" in out
    assert "temporal facts    : 0 unresolved" in out
    assert "source pathology auth: 0 blocked" in out
    assert "failed op auth    : 1 blocked" in out
    assert "recovery auth     : 0 blocked" in out
    assert "candidate set auth: 4 blocked" in out
    assert "source path auth statuses: {}" in out
    assert "failed op auth statuses  : failed_operation_not_replay_authority=1" in out
    assert "recovery auth statuses   : {}" in out
    assert "candidate set auth statuses: candidate_set_incomplete_not_replay_authority=4" in out
    assert "fi.v1.FAILED_OPERATION_RESOLUTION=1" in out
    assert "manual claims    : fi.v1.FAILED_OPERATION_RESOLUTION=1" in out
    assert "coverage proofs  : fi.v1.OPERATION_CUE_EXHAUSTIVENESS_CERTIFICATE=2" in out


def test_to_json_counts_temporal_resolution_gaps_in_proof_gates() -> None:
    payload = strict_report._to_json(
        {
            "statute_id": "2001/1234",
            "replay_mode": "legal_pit",
            "compile_mode": "strict",
            "profile": FINLAND_INGESTION_V1,
            "projection_rows": [
                {
                    "kind": "TIME.ESTIMATED_EFFECTIVE_DATE",
                    "message": "Effective date estimated.",
                    "source": "2021/2",
                    "detail": {"step": "text_regex"},
                }
            ],
        }
    )

    proof_gates = payload["proof_gate_summary"]
    assert proof_gates["temporal_resolution_status_counts"] == {
        "unknown_effective_date": 1,
    }
    assert proof_gates["temporal_resolution_unresolved_count"] == 1
    assert "temporal_resolution_closure" in proof_gates["does_not_claim"]


def test_to_json_counts_blocked_recovery_authorizations_in_proof_gates() -> None:
    payload = strict_report._to_json(
        {
            "statute_id": "2001/1234",
            "replay_mode": "legal_pit",
            "compile_mode": "strict",
            "profile": FINLAND_INGESTION_V1,
            "projection_rows": [
                {
                    "kind": "APPLY.STRICT_REJECTED_UNCOVERED_BODY",
                    "message": "Uncovered body recovery was rejected.",
                    "source": "2021/2",
                },
                {
                    "kind": "LOWER.CONTEXT_DEPENDENT_ANCHOR_RESOLUTION",
                    "message": "Anchor resolved from source context.",
                    "source": "2021/3",
                },
            ],
            "strict_fail_reasons": ["APPLY.STRICT_REJECTED_UNCOVERED_BODY"],
        }
    )

    proof_gates = payload["proof_gate_summary"]
    assert payload["evidence_surface_report"]["summary"][
        "recovery_execution_authorization_strict_blocked_count"
    ] == 1
    assert proof_gates["recovery_authorization_status_counts"] == {
        "recovery_projection_not_replay_authority": 1,
        "strict_recovery_blocked": 1,
    }
    assert proof_gates["recovery_authorization_blocked_count"] == 1
    assert "recovery_authorization_closure" in proof_gates["does_not_claim"]


def test_to_json_uses_projection_rows_when_available() -> None:
    cr = SimpleNamespace(
        statute_id="2001/1234",
        replay_mode="legal_pit",
        compile_mode="strict",
        profile=FINLAND_INGESTION_V1,
        projection_rows=lambda: (
            {
                "kind": "PARSE.SEMANTIC_COLLAPSE_MOVE_RENUMBER",
                "message": "destinationless move/relabel observed",
                "source": "2020/1",
                "detail": {
                    "collapse_kind": "destinationless_move_relabel",
                    "destination_missing": True,
                },
            },
        ),
    )

    payload = strict_report._to_json(cr)

    assert payload["projection_rows"][0]["kind"] == "PARSE.SEMANTIC_COLLAPSE_MOVE_RENUMBER"
    assert payload["projection_rows"][0]["source"] == "2020/1"
    assert payload["projection_rows"][0]["detail"]["collapse_kind"] == "destinationless_move_relabel"


def test_to_json_ignores_legacy_dict_adjudications_field() -> None:
    payload = strict_report._to_json(
        {
            "statute_id": "2001/1234",
            "replay_mode": "legal_pit",
            "compile_mode": "strict",
            "profile": FINLAND_INGESTION_V1,
            "adjudications": [
                {
                    "kind": "PARSE.SEMANTIC_COLLAPSE_MOVE_RENUMBER",
                    "message": "destinationless move/relabel observed",
                    "source": "2020/1",
                    "detail": {"collapse_kind": "destinationless_move_relabel"},
                }
            ],
        }
    )

    assert payload["projection_rows"] == []


def test_to_json_preserves_source_pathology_target_unit_kind() -> None:
    payload = strict_report._to_json(
        {
            "statute_id": "2001/1234",
            "replay_mode": "legal_pit",
            "compile_mode": "strict",
            "profile": FINLAND_INGESTION_V1,
            "source_pathologies": [
                {
                    "code": "DESTRUCTIVE_SHAPE_LOSS_RISK",
                    "message": "source pathology",
                    "source_statute": "2001/748",
                    "target_unit_kind": "chapter",
                    "target_kind": "L",
                    "target_label": "4a luku",
                    "detail": {"diagnostic_reason": "partial_body_only"},
                }
            ],
        }
    )

    pathology = payload["source_pathologies"][0]
    assert pathology["target_unit_kind"] == "chapter"
    assert "target_kind" not in pathology
    assert pathology["target_label"] == "4a luku"
    authorization = payload["source_pathology_execution_authorizations"][0]
    frontier_item = payload["source_pathology_frontier_work_items"][0]
    assert authorization["executable"] is False
    assert authorization["replay_authorized"] is False
    assert authorization["authorization_status"] == "source_pathology_not_replay_authority"
    assert frontier_item["jurisdiction"] == "fi"
    assert frontier_item["executable"] is False
    assert frontier_item["replay_authorized"] is False
    assert frontier_item["required_claim_kind"] == "fi.v1.MUTATION_BOUNDARY_RESOLUTION"
    assert frontier_item["source_witness"]["preview_digest_algorithm"] == "sha256"
    assert frontier_item["source_witness"]["preview_digest"]
    assert frontier_item["detail"]["execution_authorization"]["replay_authorized"] is False
    assert payload["evidence_surface_report"]["summary"]["source_pathology_count"] == 1
    assert payload["evidence_surface_report"]["summary"]["source_pathology_kind_counts"] == {
        "DESTRUCTIVE_SHAPE_LOSS_RISK": 1
    }
    source_pathology_rows = [
        row
        for row in payload["evidence_surface_report"]["rows"]
        if row["surface"] == "source_pathology"
    ]
    assert len(source_pathology_rows) == 1
    assert source_pathology_rows[0]["replay_authorized"] is False
    assert source_pathology_rows[0]["affected_phase"] == "replay_apply"
    assert payload["evidence_surface_report"]["summary"][
        "source_pathology_frontier_source_witness_digest_coverage_counts"
    ] == {"preview_digest": 1}
    assert payload["source_unit_coverages"][0]["coverage_status"] == "frontier_witnessed"
    assert payload["source_unit_coverages"][0]["source_role"] == "finland_source_pathology"
    assert payload["evidence_surface_report"]["summary"]["source_unit_coverage_count"] == 1
    assert payload["evidence_surface_report"]["summary"][
        "source_unit_coverage_status_counts"
    ] == {"frontier_witnessed": 1}
    assert payload["evidence_surface_report"]["summary"][
        "frontier_claim_template_status_counts"
    ] == {"available": 1}
    assert payload["evidence_surface_report"]["summary"][
        "frontier_claim_template_kind_counts"
    ] == {"fi.v1.MUTATION_BOUNDARY_RESOLUTION": 1}


def test_to_json_counts_registered_source_pathology_frontiers_as_manual() -> None:
    payload = strict_report._to_json(
        {
            "statute_id": "2001/1234",
            "replay_mode": "legal_pit",
            "compile_mode": "strict",
            "profile": FINLAND_INGESTION_V1,
            "source_pathologies": [
                {
                    "code": "EMPTY_OPERATIVE_BODY",
                    "message": "source pathology",
                    "source_statute": "2001/748",
                    "target_unit_kind": "section",
                    "target_label": "4 §",
                    "detail": {"diagnostic_reason": "empty_body"},
                }
            ],
        }
    )

    proof_gates = payload["proof_gate_summary"]
    assert proof_gates["frontier_work_item_count"] == 5
    assert proof_gates["manual_claim_frontier_count"] == 1
    assert proof_gates["coverage_frontier_count"] == 4
    assert proof_gates["other_frontier_count"] == 0
    assert proof_gates["source_pathology_authorization_blocked_count"] == 1
    assert proof_gates["required_claim_kind_counts"] == {
        "fi.v1.OPERATION_CUE_EXHAUSTIVENESS_CERTIFICATE": 2,
        "fi.v1.SOURCE_PATHOLOGY_RESOLUTION": 1,
        "fi.v1.SOURCE_UNIT_ENUMERATION_CERTIFICATE": 2,
    }
    assert proof_gates["manual_frontier_required_claim_kind_counts"] == {
        "fi.v1.SOURCE_PATHOLOGY_RESOLUTION": 1,
    }
    assert proof_gates["manual_frontier_status_counts"] == {
        "source_acquisition_frontier": 1,
    }
    assert proof_gates["other_frontier_required_claim_kind_counts"] == {}
    assert proof_gates["other_frontier_status_counts"] == {}


def test_to_json_exports_open_ownership_closure_coverage_without_replay_claims() -> None:
    payload = strict_report._to_json(
        {
            "statute_id": "2001/1234",
            "profile": FINLAND_INGESTION_V1,
            "canonical_ops": [SimpleNamespace(op_id="lo-visible-1")],
            "failed_ops": [],
            "projection_rows": [],
            "source_pathologies": [],
            "strict_fail_reasons": [],
        }
    )

    certificate = payload["ownership_closure_coverage"]
    report = payload["ownership_closure_report"]
    surface = payload["evidence_surface_report"]

    assert certificate["schema"] == "lawvm.ownership_closure_coverage.v1"
    assert certificate["closure_status"] == "open"
    assert certificate["closed"] is False
    assert certificate["corpus_slice_id"] == "fi:2001/1234:strict-report-visible-surfaces"
    assert certificate["source_bundle_hash"].startswith("sha256:")
    assert certificate["graph_snapshot_hash"].startswith("sha256:")
    assert certificate["failed_gates"] == [
        "candidate_set_fi_strict_report_visible_operation_rows_partial",
        "candidate_set_fi_strict_report_source_lineage_units_unavailable",
        "candidate_set_fi_strict_report_source_unit_enumeration_unavailable",
        "candidate_set_fi_strict_report_operation_cue_coverage_partial",
    ]
    assert certificate["unowned_counts"] == {
        "incomplete_candidate_set_coverages": 4,
        "candidate_set_coverages_without_execution_authorization": 0,
        "incomplete_candidate_set_coverages_without_frontier_work_item": 0,
        "failed_ops_without_frontier_work_item": 0,
        "operation_cues_without_candidate_coverage_certificate": 0,
        "source_units_without_enumeration_certificate": 0,
        "strict_fail_reasons_without_closure": 0,
        "unproved_mutation_boundary_proofs": 0,
    }
    assert certificate["owned_counts"]["canonical_ops"] == 1
    assert certificate["owned_counts"]["strict_report_candidate_set_authorizations"] == 4
    candidate_sets = payload["strict_report_candidate_set_coverages"]
    assert [row["candidate_set_kind"] for row in candidate_sets] == [
        "fi_strict_report_visible_operation_rows",
        "fi_strict_report_source_lineage_units",
        "fi_strict_report_source_unit_enumeration",
        "fi_strict_report_operation_cue_coverage",
    ]
    assert [row["completeness_status"] for row in candidate_sets] == [
        "partial",
        "unavailable",
        "unavailable",
        "partial",
    ]
    assert candidate_sets[0]["candidate_ids"] == ["canonical-op:lo-visible-1"]
    assert payload["potential_operations"][0]["potential_operation_id"] == (
        "canonical-op:lo-visible-1"
    )
    assert payload["potential_operations"][0]["classification"] == "compiled"
    assert candidate_sets[2]["next_promotion_allowed"] is False
    assert candidate_sets[3]["next_promotion_requires"] == [
        "independent_source_text_cue_detector",
        "operation_cue_classification_report",
        "parser_gap_frontier_items_for_unclassified_cues",
    ]
    candidate_set_authorizations = payload["strict_report_candidate_set_execution_authorizations"]
    assert len(candidate_set_authorizations) == 4
    assert {
        row["candidate_set_kind"]: row["authorization_status"]
        for row in candidate_set_authorizations
    } == {
        "fi_strict_report_visible_operation_rows": "candidate_set_incomplete_not_replay_authority",
        "fi_strict_report_source_lineage_units": "candidate_set_incomplete_not_replay_authority",
        "fi_strict_report_source_unit_enumeration": "candidate_set_incomplete_not_replay_authority",
        "fi_strict_report_operation_cue_coverage": "candidate_set_incomplete_not_replay_authority",
    }
    assert all(row["replay_authorized"] is False for row in candidate_set_authorizations)
    candidate_set_frontier_items = payload["strict_report_candidate_set_frontier_work_items"]
    assert len(candidate_set_frontier_items) == 4
    assert all(row["executable"] is False for row in candidate_set_frontier_items)
    assert all(row["replay_authorized"] is False for row in candidate_set_frontier_items)
    assert {
        row["frontier_status"]
        for row in candidate_set_frontier_items
    } == {
        "partial_candidate_set_frontier",
        "unavailable_candidate_set_frontier",
    }
    assert report["report_kind"] == "finland_strict_report_ownership_closure"
    assert report["replay_claims"] is False
    assert report["canonical_effect_claims"] is False
    assert surface["summary"]["strict_report_candidate_set_coverage_count"] == 4
    assert surface["summary"]["strict_report_candidate_set_status_counts"] == {
        "partial": 2,
        "unavailable": 2,
    }
    assert surface["summary"]["strict_report_candidate_set_execution_authorization_count"] == 4
    assert surface["summary"][
        "strict_report_candidate_set_execution_authorization_status_counts"
    ] == {"candidate_set_incomplete_not_replay_authority": 4}
    assert surface["summary"]["strict_report_candidate_set_frontier_work_item_count"] == 4
    assert surface["summary"]["ownership_closure_coverage_count"] == 1
    assert surface["summary"]["ownership_closure_status"] == "open"
    assert surface["summary"]["ownership_closure_failed_gate_counts"] == {
        "candidate_set_fi_strict_report_operation_cue_coverage_partial": 1,
        "candidate_set_fi_strict_report_source_lineage_units_unavailable": 1,
        "candidate_set_fi_strict_report_source_unit_enumeration_unavailable": 1,
        "candidate_set_fi_strict_report_visible_operation_rows_partial": 1,
    }
    assert surface["summary"]["ownership_closure_owned_counts"]["canonical_ops"] == 1
    assert (
        surface["summary"]["ownership_closure_owned_counts"][
            "strict_report_candidate_set_authorizations"
        ]
        == 4
    )
    closure_rows = [
        row
        for row in surface["rows"]
        if row["surface"] == "ownership_closure_coverage"
    ]
    assert len(closure_rows) == 1
    assert closure_rows[0]["closed"] is False


def test_to_json_exports_sparse_slot_candidate_certificates() -> None:
    payload = strict_report._to_json(
        {
            "statute_id": "2001/1234",
            "replay_mode": "legal_pit",
            "compile_mode": "strict",
            "profile": FINLAND_INGESTION_V1,
            "projection_rows": [
                {
                    "kind": "ELAB.SPARSE_SLOT_BINDING",
                    "message": "Frontend elaboration recorded sparse slot ownership.",
                    "source": "2010/100",
                    "detail": {
                        "source_statute": "2010/100",
                        "target_unit_kind": "section",
                        "target_norm": "3",
                        "target_chapter": "",
                        "op_description": "REPLACE 3 § 1 mom",
                        "op_type": "REPLACE",
                        "target_paragraph": 1,
                        "target_item": "",
                        "target_special": "",
                        "payload_slot_index": 1,
                        "payload_slot_label": "1",
                    },
                }
            ],
        }
    )

    certificates = payload["sparse_slot_candidate_set_coverages"]
    assert len(certificates) == 1
    assert certificates[0]["candidate_set_kind"] == "fi_sparse_payload_slot_assignment"
    assert certificates[0]["completeness_status"] == "partial"
    assert certificates[0]["selected_candidate_ids"] == ["payload-slot:1:1"]
    assert certificates[0]["next_promotion_allowed"] is False
    report = payload["evidence_surface_report"]
    assert report["jurisdiction"] == "fi"
    assert report["report_kind"] == "finland_strict_report"
    assert report["replay_claims"] is False
    assert report["canonical_effect_claims"] is True
    assert report["agreement_claims"] is False
    assert report["summary"]["sparse_slot_candidate_set_coverage_count"] == 1


def test_to_json_exports_source_adjudication_agreement_residual() -> None:
    payload = strict_report._to_json(
        SimpleNamespace(
            statute_id="2001/1234",
            replay_mode="legal_pit",
            compile_mode="strict",
            profile=FINLAND_INGESTION_V1,
            canonical_ops=[],
            failed_ops=[],
            projection_rows=lambda: (),
            source_pathology_rows=lambda: (),
            strict_fail_reasons=[],
            source_adjudication=SimpleNamespace(
                statute_id="2001/1234",
                replay_mode="legal_pit",
                html_noncommensurable_reason="oracle_extra_scoped_labels:chapter:15/section:1",
                cutoff_date="2024-01-01",
                oracle_version_amendment_id="2024/1",
                oracle_suspect="",
                lineage=(
                    {
                        "sequence": 1,
                        "statute_id": "2024/1",
                        "title": "Test source",
                        "effective_date": "2024-01-01",
                        "issue_date": "2023-12-15",
                        "sort_mode": "legal_pit",
                        "included": True,
                        "selection_basis": "",
                    },
                ),
            ),
        )
    )

    lineage_witnesses = payload["source_lineage_source_witnesses"]
    assert len(lineage_witnesses) == 1
    assert lineage_witnesses[0]["source_role"] == "finland_source_lineage_amendment"
    assert lineage_witnesses[0]["artifact_id"] == "2024/1"
    assert lineage_witnesses[0]["preview_digest"]
    source_unit_coverages = payload["source_unit_coverages"]
    assert len(source_unit_coverages) == 1
    assert source_unit_coverages[0]["coverage_status"] == "lineage_witnessed"
    assert source_unit_coverages[0]["unit_family"] == "finland_source_lineage_amendment"
    assert source_unit_coverages[0]["safe_default"] == (
        "treat_lineage_source_unit_coverage_as_witnessed_only_not_full_enumeration"
    )
    source_unit_certificate = next(
        row
        for row in payload["strict_report_candidate_set_coverages"]
        if row["candidate_set_kind"] == "fi_strict_report_source_unit_enumeration"
    )
    assert source_unit_certificate["completeness_status"] == "partial"
    assert source_unit_certificate["source_unit_coverage_count"] == 1
    assert source_unit_certificate["next_promotion_allowed"] is False
    residuals = payload["agreement_residuals"]
    assert len(residuals) == 1
    assert residuals[0]["family"] == "non_commensurable_surface"
    assert residuals[0]["agreement_residual_status"] == "residual"
    assert residuals[0]["detail"]["html_noncommensurable_reason"] == ("oracle_extra_scoped_labels:chapter:15/section:1")
    report = payload["evidence_surface_report"]
    assert report["summary"]["source_lineage_source_witness_count"] == 1
    assert report["summary"]["source_unit_coverage_count"] == 1
    assert report["summary"]["source_unit_coverage_status_counts"] == {
        "lineage_witnessed": 1
    }
    assert report["summary"]["source_lineage_source_witness_digest_coverage_counts"] == {"preview_digest": 1}
    assert report["summary"]["agreement_residual_count"] == 1
    assert report["summary"]["agreement_materialization_kind"] == "legal_text_state"
    assert (
        report["summary"]["agreement_comparison_materialization_kind"]
        == "official_consolidation_view"
    )
    assert report["summary"]["source_completeness_status_count"] == 1
    assert report["summary"]["source_completeness"] == {
        "chain_length": 1,
        "source_available": 1,
        "dates_available": 1,
        "missing_sources": 0,
        "missing_dates": 0,
    }
    assert [row["surface"] for row in report["rows"]] == [
        "source_lineage_source_witness",
        "source_unit_coverage",
        "agreement_residual",
        "source_completeness_status",
        "strict_report_candidate_set_coverage",
        "strict_report_candidate_set_coverage",
        "strict_report_candidate_set_coverage",
        "strict_report_candidate_set_coverage",
        "strict_report_candidate_set_execution_authorization",
        "strict_report_candidate_set_execution_authorization",
        "strict_report_candidate_set_execution_authorization",
        "strict_report_candidate_set_execution_authorization",
        "strict_report_candidate_set_frontier_work_item",
        "strict_report_candidate_set_frontier_work_item",
        "strict_report_candidate_set_frontier_work_item",
        "strict_report_candidate_set_frontier_work_item",
        "ownership_closure_coverage",
    ]


def test_to_json_exports_mutation_boundary_proofs() -> None:
    payload = strict_report._to_json(
        {
            "statute_id": "2001/1234",
            "profile": FINLAND_INGESTION_V1,
            "canonical_ops": [],
            "failed_ops": [],
            "projection_rows": [],
            "source_pathologies": [],
            "strict_fail_reasons": [],
            "apply_mutation_invariant_reports": [
                {
                    "op_id": "op-1",
                    "helper": "_apply_deterministic_subsection_op",
                    "outcome": "applied",
                    "touched_paths": [[["chapter", "1"], ["section", "2"]]],
                    "changed_paths": [[["chapter", "1"], ["section", "2"]]],
                    "allowed_roots": [[["chapter", "1"], ["section", "2"]]],
                    "allowed_effect_region_paths": [[["chapter", "1"], ["section", "2"]]],
                    "permitted_paths": [[["chapter", "1"], ["section", "2"]]],
                    "covered_changed_paths": [[["chapter", "1"], ["section", "2"]]],
                    "path_set_invariant_holds": True,
                }
            ],
        }
    )

    proofs = payload["mutation_boundary_proofs"]
    assert len(proofs) == 1
    assert proofs[0]["operation_id"] == "op-1"
    assert proofs[0]["status"] == "proved"
    assert proofs[0]["owner_phase"] == "replay_apply"
    report = payload["evidence_surface_report"]
    assert report["summary"]["mutation_boundary_proof_count"] == 1
    assert report["rows"][0]["surface"] == "mutation_boundary_proof"


def test_format_report_surfaces_target_scoped_projection_row_detail() -> None:
    cr = SimpleNamespace(
        statute_id="2001/1234",
        replay_mode="legal_pit",
        compile_mode="strict",
        profile=FINLAND_INGESTION_V1,
        projection_rows=lambda: (
            {
                "kind": "LOWER.CONTEXT_DEPENDENT_ANCHOR_RESOLUTION",
                "message": "Compilation required context-dependent anchor resolution.",
                "source": "2020/1",
                "detail": {
                    "tag": "chapter_scope_from_preamble",
                    "target_unit_kind": "section",
                    "target_norm": "35",
                    "target_chapter": "5",
                },
            },
        ),
    )

    out = strict_report._format_report(cr, verbose=False)

    assert "LOWER.CONTEXT_DEPENDENT_ANCHOR_RESOLUTION" in out
    assert "source: 2020/1" in out
    assert "detail: target(kind=section, norm=35, chapter=5); tag=chapter_scope_from_preamble" in out


def test_format_report_surfaces_failed_op_reason_code() -> None:
    failed_op = FailedOp.from_scope(
        amendment_id="2020/1",
        description="replace chapter-scoped section",
        reason="no deterministic path",
        reason_code="no_deterministic_path",
        target_unit_kind="section",
        target_section="5",
        target_chapter="4",
    )
    cr = SimpleNamespace(
        statute_id="2001/1234",
        replay_mode="legal_pit",
        compile_mode="strict",
        profile=FINLAND_INGESTION_V1,
        canonical_ops=[],
        failed_ops=[failed_op],
        strict_fail_reasons=["failed_ops"],
        projection_rows=lambda: (),
        source_pathology_rows=lambda: (),
    )

    out = strict_report._format_report(cr, verbose=False)

    assert "no deterministic path" in out
    assert "no_deterministic_path" in out
    assert "section 5" in out


def test_format_report_surfaces_candidate_set_closure_boundaries() -> None:
    cr = {
        "statute_id": "2001/1234",
        "replay_mode": "legal_pit",
        "compile_mode": "strict",
        "profile": FINLAND_INGESTION_V1,
        "canonical_ops": [SimpleNamespace(op_id="lo-visible-1")],
        "failed_ops": [],
        "projection_rows": [],
        "source_pathologies": [],
        "strict_fail_reasons": [],
    }

    out = strict_report._format_report(cr, verbose=False)

    assert "Ownership closure" in out
    assert "status           : open" in out
    assert "candidate_set_fi_strict_report_source_unit_enumeration_unavailable" in out
    assert "Candidate set certificates" in out
    assert "fi_strict_report_source_unit_enumeration: unavailable" in out
    assert "fi_strict_report_operation_cue_coverage: partial" in out
    assert "replay_authorized=False" in out
    assert "candidate_set_incomplete_not_replay_authority" in out
    assert "independent_source_text_cue_detector" in out


def test_format_report_uses_projection_rows_when_available() -> None:
    cr = SimpleNamespace(
        statute_id="2001/1234",
        replay_mode="legal_pit",
        compile_mode="strict",
        profile=FINLAND_INGESTION_V1,
        projection_rows=lambda: (
            {
                "kind": "LOWER.CONTEXT_DEPENDENT_ANCHOR_RESOLUTION",
                "message": "Compilation required context-dependent anchor resolution.",
                "source": "2020/1",
                "detail": {
                    "tag": "chapter_scope_from_preamble",
                    "target_unit_kind": "section",
                    "target_norm": "35",
                    "target_chapter": "5",
                },
            },
        ),
    )

    out = strict_report._format_report(cr, verbose=False)

    assert "LOWER.CONTEXT_DEPENDENT_ANCHOR_RESOLUTION" in out
    assert "source: 2020/1" in out
    assert "detail: target(kind=section, norm=35, chapter=5); tag=chapter_scope_from_preamble" in out


def test_format_report_surfaces_source_pathology_projection_row_detail() -> None:
    cr = SimpleNamespace(
        statute_id="1997/1339",
        replay_mode="legal_pit",
        compile_mode="strict",
        profile=FINLAND_INGESTION_V1,
        projection_rows=lambda: (
            {
                "kind": "ELAB.SOURCE_PATHOLOGY",
                "message": "Replay encountered a source pathology.",
                "source": "2001/748",
                "detail": {
                    "code": "DESTRUCTIVE_SHAPE_LOSS_RISK",
                    "target_unit_kind": "section",
                    "target_label": "6 §",
                    "diagnostic_reason": "partial_body_only",
                },
            },
        ),
    )

    out = strict_report._format_report(cr, verbose=False)

    assert "ELAB.SOURCE_PATHOLOGY" in out
    assert "source: 2001/748" in out
    assert (
        "detail: code=DESTRUCTIVE_SHAPE_LOSS_RISK; target(kind=section); target_label=6 §; diagnostic_reason=partial_body_only"
        in out
    )


def test_build_facade_for_statute_preserves_projection_row_detail(monkeypatch) -> None:
    def fake_compile_fi_facade(
        statute_id: str,
        *,
        replay_mode: str = "legal_pit",
    ):
        assert statute_id == "1990/1295"
        assert replay_mode == "legal_pit"
        return SimpleNamespace(
            finding_ledger=(
                SimpleNamespace(
                    role="obligation",
                    kind="LOWER.CONTEXT_DEPENDENT_ANCHOR_RESOLUTION",
                    detail={
                        "tag": "chapter_scope_from_preamble",
                        "target_unit_kind": "section",
                        "target_norm": "35",
                        "target_chapter": "5",
                    },
                ),
            )
        )

    monkeypatch.setattr("lawvm.finland.compile.compile_fi_facade", fake_compile_fi_facade)

    facade = strict_report._build_facade_for_statute("1990/1295", mode="legal_pit")

    obligations = tuple(f for f in facade.finding_ledger if f.role == "obligation")
    assert len(obligations) == 1
    obl = obligations[0]
    assert obl.kind == "LOWER.CONTEXT_DEPENDENT_ANCHOR_RESOLUTION"
    assert obl.detail["tag"] == "chapter_scope_from_preamble"
    assert obl.detail["target_norm"] == "35"
    assert obl.detail["target_chapter"] == "5"


def test_compile_one_replays_quietly(monkeypatch) -> None:
    def fake_replay_xml(
        statute_id: str,
        *,
        quiet: bool = False,
        compiled_ops_out=None,
        replay_meta_out=None,
        lo_ops_out=None,
        failed_ops_out=None,
        strict_profile=None,
        strict_johto_temporal: bool = False,
    ):
        assert statute_id == "1990/1295"
        assert quiet is True
        assert strict_profile is not None
        assert strict_profile.name == "finland_ingestion_v1"
        assert strict_johto_temporal is True
        return SimpleNamespace(
            source_adjudication=None,
            finding_ledger=(),
            verdict=None,
            bundle=SimpleNamespace(structural_ops=()),
        )

    monkeypatch.setattr("lawvm.finland.replay_entrypoint.replay_xml", fake_replay_xml)

    def fake_compile_fi_facade_from_replay(**kwargs):
        strict_profile = kwargs.get("strict_profile")
        assert strict_profile is not None
        assert strict_profile.name == "finland_ingestion_v1"
        return SimpleNamespace(
            finding_ledger=(),
            verdict=None,
            bundle=SimpleNamespace(structural_ops=()),
        )

    monkeypatch.setattr(
        "lawvm.finland.compile.compile_fi_facade_from_replay",
        fake_compile_fi_facade_from_replay,
    )

    row = strict_report._compile_one((1, "1990/1295"))

    assert row["sid"] == "1990/1295"


def test_compile_one_prefers_typed_source_adjudication_lineage_over_replay_meta(monkeypatch) -> None:
    def fake_replay_xml(
        statute_id: str,
        *,
        quiet: bool = False,
        compiled_ops_out=None,
        replay_meta_out=None,
        lo_ops_out=None,
        failed_ops_out=None,
        strict_profile=None,
        strict_johto_temporal: bool = False,
    ):
        assert statute_id == "1990/1295"
        assert quiet is True
        assert strict_profile is not None
        assert strict_profile.name == "finland_ingestion_v1"
        assert strict_johto_temporal is True
        if replay_meta_out is not None:
            replay_meta_out.update(
                {
                    "lineage": [
                        {"included": False, "effective_date": ""},
                        {"included": False, "effective_date": ""},
                    ]
                }
            )
        return SimpleNamespace(
            source_adjudication=SimpleNamespace(
                lineage=({"included": True, "effective_date": "2025-01-01"},),
                html_noncommensurable_reason="",
            ),
            finding_ledger=(),
            verdict=None,
            bundle=SimpleNamespace(structural_ops=()),
        )

    monkeypatch.setattr("lawvm.finland.replay_entrypoint.replay_xml", fake_replay_xml)
    monkeypatch.setattr(
        "lawvm.finland.compile.compile_fi_facade_from_replay",
        lambda **kwargs: SimpleNamespace(
            finding_ledger=(),
            verdict=None,
            bundle=SimpleNamespace(structural_ops=()),
        ),
    )

    row = strict_report._compile_one((1, "1990/1295"))

    assert row["chain_length"] == 1
    assert row["source_available"] == 1


def test_compile_one_hydrates_source_adjudication_from_replay_meta(monkeypatch) -> None:
    def fake_replay_xml(
        statute_id: str,
        *,
        quiet: bool = False,
        compiled_ops_out=None,
        replay_meta_out=None,
        lo_ops_out=None,
        failed_ops_out=None,
        strict_profile=None,
        strict_johto_temporal: bool = False,
    ):
        assert statute_id == "1990/1295"
        assert quiet is True
        assert strict_profile is not None
        assert strict_profile.name == "finland_ingestion_v1"
        assert strict_johto_temporal is True
        if replay_meta_out is not None:
            replay_meta_out.update(
                {
                    "lineage": [
                        {"included": True, "effective_date": "2025-01-01"},
                        {"included": False, "effective_date": ""},
                    ],
                    "oracle_version_amendment_id": "raw-mid",
                }
            )
        return SimpleNamespace(
            source_adjudication=None,
            finding_ledger=(),
            verdict=None,
            bundle=SimpleNamespace(structural_ops=()),
        )

    monkeypatch.setattr("lawvm.finland.replay_entrypoint.replay_xml", fake_replay_xml)
    monkeypatch.setattr(
        "lawvm.finland.compile.compile_fi_facade_from_replay",
        lambda **kwargs: SimpleNamespace(
            finding_ledger=(),
            verdict=None,
            bundle=SimpleNamespace(structural_ops=()),
        ),
    )

    row = strict_report._compile_one((1, "1990/1295"))

    assert row["chain_length"] == 2
    assert row["source_available"] == 1


def test_strict_report_main_suppresses_raw_replay_failed_chatter_for_1978_38(monkeypatch, capsys) -> None:
    def fake_replay_xml(_statute_id: str, **_kwargs):
        print("REPLACE 10 luku otsikko → FAILED")
        print("INSERT 10 luku 16 § 2 mom → FAILED")
        return SimpleNamespace(source_adjudication=None)

    def fake_compile_fi_facade_from_replay(**_kwargs):
        return SimpleNamespace(
            finding_ledger=(),
            verdict=None,
            bundle=SimpleNamespace(structural_ops=(), temporal_events=()),
            source_pathology_rows=lambda: (),
        )

    monkeypatch.setattr("lawvm.finland.replay_entrypoint.replay_xml", fake_replay_xml)
    monkeypatch.setattr(
        "lawvm.finland.compile.compile_fi_facade_from_replay",
        fake_compile_fi_facade_from_replay,
    )

    strict_report.main(
        Namespace(
            statute_id="1991/1",
            mode="legal_pit",
            facade=False,
            json_output=False,
            verbose=False,
        )
    )

    out = capsys.readouterr().out

    assert "REPLACE 10 luku otsikko → FAILED" not in out
    assert "INSERT 10 luku 16 § 2 mom → FAILED" not in out

from __future__ import annotations

import json

from scripts import uk_completion_audit as audit


def _write_broad_report(tmp_path, summary=None, **overrides):
    path = tmp_path / "uk_broad.report.json"
    payload = {
        "jurisdiction": "uk",
        "report_kind": "uk_broad_baseline_agreement_report",
        "schema": "lawvm.uk_broad_baseline_agreement_report.v1",
        "summary": {
            "completion_gate_clean": True,
            "active_unclassified_residual_count": 0,
            "deterministic_frontend_candidate_count": 0,
            "non_manual_source_chain_frontier_count": 0,
            "manual_frontier_template_gap_count": 0,
            "mutation_boundary_unexplained_report_count": 0,
            "mutation_boundary_unexplained_path_count": 0,
        },
    }
    payload["summary"].update(summary or {})
    payload.update(overrides)
    path.write_text(json.dumps(payload))
    return path


def _write_remaining_report(tmp_path, summary=None, **overrides):
    path = tmp_path / "uk_remaining.json"
    payload = {
        "report_kind": "uk_remaining_work_summary.v1",
        "summary": {
            "completion_gate_clean": True,
            "active_unclassified_residual_count": 0,
            "deterministic_frontend_candidate_count": 0,
            "non_manual_source_chain_frontier_count": 0,
            "mutation_boundary_unexplained_report_count": 0,
            "mutation_boundary_unexplained_path_count": 0,
            "lane_count": 2,
            "lane_counts": {
                "manual_compilation_frontier": 2,
                "oracle_suspect_review": 1,
            },
            "item_count": 3,
            "item_expected_row_count": 3,
            "item_exported_row_count": 3,
            "item_fully_exported": True,
            "item_unexported_row_count": 0,
            "item_unexported_lane_ids": [],
            "item_exported_lane_count": 2,
            "item_exported_lane_counts": {
                "manual_compilation_frontier": 2,
                "oracle_suspect_review": 1,
            },
            "item_authorization_status_counts": {
                "non_executable_work_item": 3,
            },
            "item_safety_gap_counts": {},
        },
    }
    payload["summary"].update(summary or {})
    payload.update(overrides)
    path.write_text(json.dumps(payload))
    return path


def _gate(payload, gate_id):
    return {gate["gate_id"]: gate for gate in payload["gates"]}[gate_id]


def test_completion_audit_supported_when_all_gates_pass(tmp_path) -> None:
    broad = _write_broad_report(tmp_path)
    remaining = _write_remaining_report(tmp_path)

    payload = audit.load_completion_audit(broad, remaining)

    assert payload["report_kind"] == "uk_completion_audit.v1"
    assert payload["truth_claim"] == "completion_declaration_audit_not_replay_authority"
    assert payload["summary"]["supported"] is True
    assert payload["summary"]["failed_gate_count"] == 0
    assert payload["summary"]["item_count"] == 3
    assert payload["declaration"]["status"] == "supported"
    assert "manual_frontier_as_executable" in payload["forbidden_shortcuts"]
    assert _gate(payload, "remaining_items_non_executable")["status"] == "pass"
    assert "status=supported" in audit._emit_text(payload)


def test_completion_audit_blocks_unclassified_residuals(tmp_path) -> None:
    broad = _write_broad_report(
        tmp_path,
        summary={
            "completion_gate_clean": False,
            "active_unclassified_residual_count": 1,
        },
    )
    remaining = _write_remaining_report(tmp_path)

    payload = audit.load_completion_audit(broad, remaining)

    assert payload["summary"]["supported"] is False
    assert payload["declaration"]["status"] == "not_supported"
    assert _gate(payload, "broad_completion_gate_clean")["status"] == "fail"
    assert _gate(payload, "broad_active_unclassified_residuals")["observed"] == 1


def test_completion_audit_blocks_item_safety_gaps(tmp_path) -> None:
    broad = _write_broad_report(tmp_path)
    remaining = _write_remaining_report(
        tmp_path,
        summary={
            "item_safety_gap_counts": {"replay_authorized_item": 1},
        },
    )

    payload = audit.load_completion_audit(broad, remaining)

    assert payload["summary"]["supported"] is False
    assert _gate(payload, "remaining_item_safety_gaps")["status"] == "fail"
    assert payload["summary"]["item_safety_gap_counts"] == {
        "replay_authorized_item": 1
    }


def test_completion_audit_blocks_executable_remaining_items(tmp_path) -> None:
    broad = _write_broad_report(tmp_path)
    remaining = _write_remaining_report(
        tmp_path,
        summary={
            "item_authorization_status_counts": {
                "non_executable_work_item": 2,
                "replay_authorized": 1,
            },
        },
    )

    payload = audit.load_completion_audit(broad, remaining)

    assert payload["summary"]["supported"] is False
    assert _gate(payload, "remaining_items_non_executable")["observed"] == {
        "replay_authorized": 1
    }


def test_completion_audit_blocks_incomplete_item_export(tmp_path) -> None:
    broad = _write_broad_report(tmp_path)
    remaining = _write_remaining_report(
        tmp_path,
        summary={
            "item_count": 2,
            "item_exported_row_count": 2,
            "item_fully_exported": False,
            "item_unexported_row_count": 1,
            "item_unexported_lane_ids": ["oracle_suspect_review"],
            "item_exported_lane_count": 1,
            "item_exported_lane_counts": {"manual_compilation_frontier": 2},
        },
    )

    payload = audit.load_completion_audit(broad, remaining)

    assert payload["summary"]["supported"] is False
    assert _gate(payload, "remaining_items_fully_exported")["status"] == "fail"
    assert _gate(payload, "remaining_item_unexported_rows")["observed"] == 1
    assert _gate(payload, "remaining_item_unexported_lanes")["observed"] == (
        "oracle_suspect_review",
    )


def test_completion_audit_cli_fail_on_incomplete(tmp_path, capsys) -> None:
    broad = _write_broad_report(
        tmp_path,
        summary={"deterministic_frontend_candidate_count": 1},
    )
    remaining = _write_remaining_report(tmp_path)

    exit_code = audit.main(
        [str(broad), str(remaining), "--fail-on-incomplete", "--format", "text"]
    )
    out = capsys.readouterr().out

    assert exit_code == 1
    assert "status=not_supported" in out
    assert "broad_deterministic_frontend_candidates" in out


def test_completion_audit_rejects_wrong_input_report_kind(tmp_path) -> None:
    broad = _write_broad_report(tmp_path, report_kind="other")
    remaining = _write_remaining_report(tmp_path)

    payload = audit.load_completion_audit(broad, remaining)

    assert payload["summary"]["supported"] is False
    assert _gate(payload, "broad_report_kind")["status"] == "fail"

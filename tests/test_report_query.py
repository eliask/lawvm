import json
from argparse import Namespace

import pytest

from lawvm.tools.report_query import (
    ReportQueryFilters,
    filter_report_query_records,
    format_report_query_rows,
    load_report_query_records,
    main,
    report_query_rows_to_jsonable,
)


def test_report_query_filters_nested_operation_evidence_row(tmp_path) -> None:
    path = tmp_path / "operation_audits.jsonl"
    path.write_text(
        json.dumps({
            "op_id": "local-op-1",
            "status": "metadata_matched",
            "evidence_row": {
                "row_id": "row-1",
                "frontend_id": "open_law_maryland",
                "source_artifact_id": "editorial-actions/x.xml",
                "source_locator": "10|27|02|annos",
                "status": "matched",
                "blocking": False,
                "strict_disposition": "record",
                "quirks_disposition": "record",
                "finding_ids": ["open_law_metadata_target_replayed"],
                "detail": {
                    "operation_family": "replace",
                    "changed_paths": ["10|27|02", "10|27|03"],
                    "reason": "candidate canonical effect emitted but not replayed",
                    "operation_target_blocking_rule_id": "nz_target_address_duplicate_source_path",
                    "candidate_witness_rule_id": "nz_repeal_candidate_from_history_note_payload_witness",
                    "matched_allowance_rule_ids": ["section_move_replace_destination_rebind"],
                },
                "evidence": {
                    "projection_lane": "secondary-map",
                    "latest_oracle_text_rule_id": "nz_latest_oracle_text_secondary_rule",
                },
            },
        })
        + "\n",
        encoding="utf-8",
    )

    records = load_report_query_records((path,), validate=True)
    selected = filter_report_query_records(
        records,
        ReportQueryFilters(
            status="matched",
            locator="10|27|02|annos",
            detail=(("operation_family", "replace"), ("changed_paths", "10|27|02|10|27|03")),
        ),
    )

    assert len(selected) == 1
    assert selected[0].validation_issues == ()
    assert "row-1 matched editorial-actions/x.xml 10|27|02|annos" in format_report_query_rows(selected)
    assert len(filter_report_query_records(
        records,
        ReportQueryFilters(rule_id="nz_repeal_candidate_from_history_note_payload_witness"),
    )) == 1
    assert len(filter_report_query_records(
        records,
        ReportQueryFilters(rule_id="section_move_replace_destination_rebind"),
    )) == 1
    assert len(filter_report_query_records(
        records,
        ReportQueryFilters(rule_id="nz_target_address_duplicate_source_path"),
    )) == 1
    assert len(filter_report_query_records(
        records,
        ReportQueryFilters(rule_id="nz_latest_oracle_text_secondary_rule"),
    )) == 1
    assert len(filter_report_query_records(
        records,
        ReportQueryFilters(detail=(("projection_lane", "secondary-map"),)),
    )) == 1
    payload = report_query_rows_to_jsonable(records)
    assert "nz_target_address_duplicate_source_path" in payload[0]["rule_ids"]
    assert "section_move_replace_destination_rebind" in payload[0]["rule_ids"]
    assert "nz_latest_oracle_text_secondary_rule" in payload[0]["rule_ids"]
    rendered = format_report_query_rows(records)
    assert "nz_repeal_candidate_from_history_note_payload_witness" in rendered
    assert "nz_target_address_duplicate_source_path" in rendered
    assert "section_move_replace_destination_rebind" in rendered
    assert filter_report_query_records(
        records,
        ReportQueryFilters(rule_id="candidate canonical effect emitted but not replayed"),
    ) == ()
    assert filter_report_query_records(
        records,
        ReportQueryFilters(detail=(("operation_family", "insert"),)),
    ) == ()


def test_report_query_filters_direct_finding_rows_by_rule_and_phase(tmp_path) -> None:
    path = tmp_path / "findings.jsonl"
    path.write_text(
        json.dumps({
            "finding_id": "row-1:finding",
            "frontend_id": "starter",
            "family": "unsupported",
            "rule_id": "starter.unsupported.v1",
            "phase": "planning",
            "message": "unsupported family",
            "source_artifact_id": "act.xml",
            "related_row_ids": ["row-1"],
            "blocking": True,
            "strict_disposition": "block",
            "quirks_disposition": "record",
            "evidence": {"codify_path": "a|b"},
        })
        + "\n",
        encoding="utf-8",
    )

    records = load_report_query_records((path,), validate=True)
    selected = filter_report_query_records(records, ReportQueryFilters(rule_id="starter.unsupported.v1", phase="planning", blocking=True))
    payload = report_query_rows_to_jsonable(selected)

    assert len(payload) == 1
    assert payload[0]["row_kind"] == "finding"
    assert payload[0]["validation_issues"] == []
    assert payload[0]["rule_ids"] == ["starter.unsupported.v1"]


def test_report_query_expands_nested_evidence_finding_rows(tmp_path) -> None:
    path = tmp_path / "bundle.jsonl"
    path.write_text(
        json.dumps({
            "statute_id": "1990/1295",
            "evidence": {
                "finding_rows": [
                    {
                        "finding_id": "finland:1990/1295:legal_pit:evidence_context_degraded:chain_completeness",
                        "frontend_id": "finland",
                        "family": "evidence_context_degraded",
                        "rule_id": "evidence_context_degraded:chain_completeness",
                        "phase": "evidence_context",
                        "message": "chain rail offline",
                        "source_artifact_id": "1990/1295",
                        "blocking": True,
                        "strict_disposition": "block",
                        "quirks_disposition": "record_degraded",
                        "evidence": {
                            "kind": "evidence_context_degraded",
                            "rail": "chain_completeness",
                        },
                    }
                ],
            },
        })
        + "\n",
        encoding="utf-8",
    )

    records = load_report_query_records((path,), validate=True)
    selected = filter_report_query_records(
        records,
        ReportQueryFilters(
            rule_id="evidence_context_degraded:chain_completeness",
            phase="evidence_context",
            blocking=True,
        ),
    )

    assert len(selected) == 1
    assert selected[0].validation_issues == ()
    assert "evidence_context_degraded:chain_completeness" in format_report_query_rows(selected)


def test_report_query_validation_reports_malformed_rows(tmp_path) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text(
        json.dumps({
            "evidence_row": {
                "row_id": "row-1",
                "frontend_id": "starter",
                "source_artifact_id": "act.xml",
                "status": "unsupported",
                "blocking": True,
                "strict_disposition": "record",
                "quirks_disposition": "record",
                "finding_ids": [],
                "detail": {},
            }
        })
        + "\n",
        encoding="utf-8",
    )

    records = load_report_query_records((path,), validate=True)

    assert "unsupported row must carry finding_ids or reason-bearing detail" in records[0].validation_issues
    assert "blocking row must have blocking strict_disposition" in records[0].validation_issues


def test_report_query_main_exits_nonzero_on_selected_validation_failure(tmp_path, capsys) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text(
        json.dumps({
            "evidence_row": {
                "row_id": "row-1",
                "frontend_id": "starter",
                "source_artifact_id": "act.xml",
                "status": "unsupported",
                "blocking": True,
                "strict_disposition": "record",
                "quirks_disposition": "record",
                "finding_ids": [],
                "detail": {},
            }
        })
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(SystemExit):
        main(
            Namespace(
                report_command="query",
                paths=[str(path)],
                row_id="",
                status="unsupported",
                rule_id="",
                phase="",
                source_artifact="",
                source_unit="",
                locator="",
                blocking=False,
                detail=[],
                limit=20,
                validate=True,
                json=False,
            )
        )

    assert "invalid=2" in capsys.readouterr().out

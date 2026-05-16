from __future__ import annotations

from lawvm.core.evidence_contracts import (
    validate_corpus_finding_evidence_row,
    validate_corpus_operation_evidence_row,
)
from lawvm.new_zealand.effect_candidates import build_effect_candidate_preflight, build_effect_candidate_surface
from lawvm.new_zealand.effect_readiness import build_effect_readiness_surface
from lawvm.new_zealand.evidence_pack import build_evidence_pack_report, write_evidence_pack_jsonl
from lawvm.new_zealand.instruction_workqueue import build_instruction_workqueue
from lawvm.new_zealand.operation_surface import build_operation_surface
from lawvm.new_zealand.payload_surface import build_payload_surface
from lawvm.new_zealand.source_tree import parse_nz_source_document
from lawvm.new_zealand.version_diff import NZArchivedVersion, NZArchivedVersionDateWindow
from lawvm.tools.cli import _build_parser
from lawvm.tools.report_query import load_report_query_records


def test_nz_evidence_pack_bundles_existing_rows_without_replay_claim(tmp_path) -> None:
    operation_surface = build_operation_surface(
        parse_nz_source_document(_TARGET_XML),
        work_id="act_public_2020_1",
        archived_dependency_work_ids=frozenset({"act_public_2025_4"}),
    )
    payload_surface = build_payload_surface(
        operation_surface,
        dependency_documents={"act_public_2025_4": parse_nz_source_document(_AMENDMENT_XML)},
    )
    effect_readiness = build_effect_readiness_surface(operation_surface, payload_surface)
    instruction_workqueue = build_instruction_workqueue(operation_surface, payload_surface, effect_readiness)
    effect_candidates = build_effect_candidate_surface(
        operation_surface,
        payload_surface,
        effect_readiness,
        instruction_workqueue,
        source_version_date_windows={
            "2025-01-01": NZArchivedVersionDateWindow(
                work_id="act_public_2020_1",
                requested_version_date="2025-01-01",
                on_or_before=NZArchivedVersion(
                    version_id="act_public_2020_1_en_2024-12-31",
                    xml_locator="before.xml",
                    version_date="2024-12-31",
                ),
                on_or_after=NZArchivedVersion(
                    version_id="act_public_2020_1_en_2025-01-02",
                    xml_locator="after.xml",
                    version_date="2025-01-02",
                ),
            ),
        },
    )
    preflight = build_effect_candidate_preflight(effect_candidates)
    report = build_evidence_pack_report(
        work_id="act_public_2020_1",
        operation_surface=operation_surface,
        effect_candidates=effect_candidates,
        candidate_preflight=preflight,
        instruction_workqueue=instruction_workqueue,
    )

    summary = report.summary()
    assert summary["replay_claims"] is False
    assert summary["canonical_effect_claims"] is False
    assert summary["operation_evidence_rows"] == 2
    assert summary["operation_finding_rows"] == 0
    assert summary["effect_candidate_evidence_rows"] == 2
    assert summary["effect_candidate_emitted_rows"] == 1
    assert summary["effect_candidate_operation_missing_rows"] == 0
    assert summary["effect_candidate_status_counts"] == {"blocked": 1, "candidate_emitted": 1}
    assert summary["effect_candidate_action_counts"] == {"__none__": 1, "repeal": 1}
    assert summary["effect_candidate_operation_family_counts"] == {"amended": 1, "repealed": 1}
    assert summary["effect_candidate_blocked_operation_family_counts"] == {"amended": 1}
    assert summary["effect_candidate_blocked_operation_family_rule_counts"] == {
        "amended|nz_effect_readiness_amendment_semantics_not_extracted": 1,
    }
    assert summary["effect_candidate_blocked_operation_family_payload_shape_counts"] == {
        "amended|empty_or_stub": 1,
    }
    assert summary["effect_candidate_blocked_operation_family_payload_safety_counts"] == {
        "amended|unsafe_opaque_or_unclassified": 1,
    }
    assert summary["effect_candidate_blocked_operation_family_target_status_counts"] == {
        "amended|candidate": 1,
    }
    assert summary["effect_candidate_blocked_operation_family_instruction_status_counts"] == {
        "amended|blocked_instruction_opaque_or_unclassified": 1,
    }
    assert summary["effect_candidate_witness_rule_counts"] == {
        "nz_repeal_candidate_from_history_note_payload_witness": 1,
    }
    assert summary["effect_candidate_action_witness_rule_counts"] == {
        "repeal|nz_repeal_candidate_from_history_note_payload_witness": 1,
    }
    assert summary["effect_candidate_action_source_change_text_witness_status_counts"] == {
        "repeal|__none__": 1,
    }
    assert summary["effect_candidate_text_replace_witness_support_status_counts"] == {"__none__": 2}
    assert summary["effect_candidate_action_text_replace_witness_support_status_counts"] == {
        "__none__|__none__": 1,
        "repeal|__none__": 1,
    }
    assert summary["effect_candidate_blocked_operation_family_source_change_text_witness_status_counts"] == {
        "amended|__none__": 1,
    }
    assert summary["effect_candidate_blocking_rule_counts"] == {
        "nz_effect_readiness_amendment_semantics_not_extracted": 1,
    }
    assert summary["effect_candidate_latest_oracle_text_status_counts"] == {
        "not_applicable_not_direct_text_substitution": 1,
    }
    assert summary["effect_candidate_source_version_date_window_status_counts"] == {
        "missing_amendment_date_iso": 1,
        "source_version_date_window_available": 1,
    }
    assert summary["effect_candidate_source_change_text_witness_status_counts"] == {"__none__": 2}
    assert summary["effect_candidate_repeal_payload_corroboration_status_counts"] == {
        "not_required_non_direct_repeal_payload": 1,
    }
    assert summary["effect_candidate_operations"] == 1
    assert summary["candidate_preflight_replayable_candidate_operations"] == 1
    assert summary["candidate_preflight_source_change_only_candidate_rows"] == 0
    assert summary["candidate_preflight_target_recovery_candidate_rows"] == 0
    assert summary["candidate_preflight_operations_to_replay"] == 0
    assert summary["candidate_preflight_blocking_rule_counts"] == {
        "nz_effect_readiness_amendment_semantics_not_extracted": 1,
    }
    assert summary["candidate_preflight_evidence_rows"] == 2
    assert summary["candidate_preflight_finding_rows"] == 1
    assert summary["instruction_workqueue_evidence_rows"] == 2
    assert summary["instruction_workqueue_queue_status_counts"] == {"blocked": 1, "not_required": 1}
    assert summary["instruction_workqueue_candidate_rows"] == 0
    assert summary["instruction_workqueue_review_rows"] == 0
    assert summary["instruction_workqueue_blocked_rows"] == 1
    assert summary["instruction_workqueue_not_required_rows"] == 1
    assert summary["instruction_workqueue_latest_oracle_text_status_counts"] == {
        "not_applicable_not_direct_text_substitution": 2
    }
    assert summary["instruction_workqueue_latest_oracle_target_resolution_status_counts"] == {"__none__": 2}
    assert summary["row_kind_counts"] == {"finding": 1, "operation": 8}
    assert summary["surface_status_counts"]["effect-candidates|accepted"] == 1
    assert summary["surface_status_counts"]["effect-candidates|unsupported"] == 1
    assert summary["surface_status_counts"]["candidate-preflight|accepted"] == 1
    assert summary["surface_status_counts"]["candidate-preflight|unsupported"] == 1
    assert (
        summary["surface_rule_id_counts"][
            "effect-candidates|nz_repeal_candidate_from_history_note_payload_witness"
        ]
        == 1
    )
    assert (
        summary["surface_rule_id_counts"][
            "candidate-preflight|nz_repeal_candidate_from_history_note_payload_witness"
        ]
        == 1
    )
    assert summary["blocking_rule_id_counts"]["nz_effect_readiness_amendment_semantics_not_extracted"] == 3
    assert summary["total_evidence_rows"] == 9
    payload = report.to_jsonable(row_limit=3)
    assert payload["replay_claims"] is False
    assert payload["canonical_effect_claims"] is False
    assert payload["filtered_evidence_rows"] == 9
    assert payload["rows_truncated"] is True
    assert payload["rows_omitted"] == 6
    filtered_payload = report.to_jsonable(
        row_limit=10,
        row_kind="operation",
        status="unsupported",
        rule_id="nz_effect_readiness_amendment_semantics_not_extracted",
        blocking=True,
    )
    assert filtered_payload["summary"]["total_evidence_rows"] == 9
    assert filtered_payload["filters"] == {
        "row_kind": "operation",
        "status": "unsupported",
        "rule_id": "nz_effect_readiness_amendment_semantics_not_extracted",
        "blocking": True,
    }
    assert filtered_payload["filtered_summary"]["total_evidence_rows"] == 3
    assert filtered_payload["filtered_summary"]["row_kind_counts"] == {"operation": 3}
    assert filtered_payload["filtered_summary"]["surface_status_counts"] == {
        "candidate-preflight|unsupported": 1,
        "effect-candidates|unsupported": 1,
        "instruction-workqueue|unsupported": 1,
    }
    assert filtered_payload["filtered_evidence_rows"] == 3
    assert {row["row_id"] for row in filtered_payload["evidence_rows"]} == {
        "nz-effect-candidate-2",
        "preflight:nz-effect-candidate-2",
        "nz-instruction-workqueue-2",
    }

    path = tmp_path / "nz_evidence_pack.jsonl"
    count = write_evidence_pack_jsonl(report, path)
    records = load_report_query_records((path,), validate=True)

    assert count == 9
    assert len(records) == 9
    assert all(record.validation_issues == () for record in records)
    for record in records:
        row = record.evidence_row
        if record.row_kind == "finding":
            assert validate_corpus_finding_evidence_row(row) == ()
        else:
            assert validate_corpus_operation_evidence_row(row) == ()
    filtered_path = tmp_path / "nz_evidence_pack_filtered.jsonl"
    filtered_count = write_evidence_pack_jsonl(
        report,
        filtered_path,
        surface="effect-candidates",
        row_kind="operation",
        status="accepted",
    )
    filtered_records = load_report_query_records((filtered_path,), validate=True)

    assert filtered_count == 1
    assert len(filtered_records) == 1
    assert {record.evidence_row["row_id"] for record in filtered_records} == {
        "nz-effect-candidate-1",
    }
    preflight_payload = report.to_jsonable(surface="candidate-preflight", status="accepted")
    assert preflight_payload["filters"] == {
        "surface": "candidate-preflight",
        "status": "accepted",
    }
    assert preflight_payload["filtered_summary"]["total_evidence_rows"] == 1
    assert preflight_payload["filtered_evidence_rows"] == 1
    assert preflight_payload["evidence_rows"][0]["row_id"] == "preflight:nz-effect-candidate-1"
    witness_payload = report.to_jsonable(rule_id="nz_repeal_candidate_from_history_note_payload_witness")
    assert witness_payload["filters"] == {
        "rule_id": "nz_repeal_candidate_from_history_note_payload_witness",
    }
    assert witness_payload["filtered_evidence_rows"] == 2
    assert {row["row_id"] for row in witness_payload["evidence_rows"]} == {
        "nz-effect-candidate-1",
        "preflight:nz-effect-candidate-1",
    }
    candidate_record = next(record for record in records if record.evidence_row["row_id"] == "nz-effect-candidate-1")
    assert candidate_record.evidence_row["detail"]["source_version_date_window_status"] == (
        "source_version_date_window_available"
    )
    assert candidate_record.evidence_row["detail"]["source_version_on_or_before_version_id"] == (
        "act_public_2020_1_en_2024-12-31"
    )
    preflight_record = next(
        record for record in records if record.evidence_row["row_id"] == "preflight:nz-effect-candidate-1"
    )
    assert preflight_record.evidence_row["detail"]["source_version_date_window_truth_claim"] == (
        "source_version_date_window_not_effective_date"
    )


def test_nz_evidence_pack_cli_parse_defaults() -> None:
    parser = _build_parser()

    args = parser.parse_args(["nz-corpus", "evidence-pack", "--work-id", "act_public_1957_87"])

    assert args.command == "nz-corpus"
    assert args.nz_corpus_command == "evidence-pack"
    assert args.db == "data/nz_legislation.farchive"
    assert args.work_id == "act_public_1957_87"
    assert args.limit == 40
    assert args.surface == ""
    assert args.row_kind == ""
    assert args.status == ""
    assert args.rule_id == ""
    assert args.blocking is False
    assert args.output_jsonl is None


_TARGET_XML = b"""\
<act>
  <body>
    <prov id="S1"><label>1</label><heading>First target</heading>
      <notes>
        <history-note id="HN1">
          <amended-provision>Section 1</amended-provision>
          <amending-operation>repealed</amending-operation>
          <amendment-date>1 January 2025</amendment-date>
          <amending-provision href="A3">section 3</amending-provision>
          <amending-leg>Example Amendment Act 2025</amending-leg>
          Section 1: repealed by section 3 of the Example Amendment Act 2025 (2025 No 4).
        </history-note>
      </notes>
    </prov>
    <prov id="S2"><label>2</label><heading>Second target</heading>
      <notes>
        <history-note id="HN2">
          <amended-provision>Section 2</amended-provision>
          <amending-operation>amended</amending-operation>
          <amending-provision href="A4">section 4</amending-provision>
          <amending-leg>Example Amendment Act 2025</amending-leg>
          Section 2: amended by section 4 of the Example Amendment Act 2025 (2025 No 4).
        </history-note>
      </notes>
    </prov>
  </body>
</act>
"""

_AMENDMENT_XML = b"""\
<act>
  <body>
    <prov id="A3"><label>3</label><heading>Repeal</heading></prov>
    <prov id="A4"><label>4</label><heading>Amend</heading></prov>
  </body>
</act>
"""

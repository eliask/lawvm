from __future__ import annotations

import argparse
import dataclasses
import json

from lawvm.core.frontier_work_item import FrontierWorkItem, validate_frontier_work_item
from lawvm.new_zealand.effect_readiness import (
    NZInstructionSemanticCandidateFamily,
    NZInstructionSemanticCandidateStatus,
    build_effect_readiness_surface,
)
from lawvm.new_zealand.frontier_work_items import (
    frontier_work_items,
    frontier_work_items_summary,
    main as nz_frontier_main,
    nz_frontier_work_item_from_workqueue_row,
)
from lawvm.new_zealand.instruction_workqueue import (
    NZInstructionWorkQueueReport,
    NZInstructionWorkQueueRow,
    NZLatestOracleTextStatus,
    NZStructuralSubfamily,
    NZStructuralSubfamilyStatus,
    NZWorkQueueStatus,
    build_instruction_workqueue,
)
from lawvm.new_zealand.operation_surface import build_operation_surface
from lawvm.new_zealand.payload_surface import (
    NZPayloadInstructionSafety,
    NZPayloadInstructionShape,
    build_payload_surface,
)
from lawvm.new_zealand.source_tree import parse_nz_source_document
from lawvm.tools.cli import _build_parser


def _blocked_schedule_indirection_row() -> NZInstructionWorkQueueRow:
    return NZInstructionWorkQueueRow(
        row_id="nz-instruction-workqueue-1",
        operation_row_id="nz-op-1",
        effect_readiness_row_id="nz-er-1",
        queue_status=NZWorkQueueStatus.BLOCKED,
        operation_family="amended",
        target_address="section:12/subsection:3",
        effect_readiness_status="blocked_text_or_structural_amendment_semantics_not_extracted",
        blocking_rule_id="nz_effect_readiness_amendment_semantics_not_extracted",
        amending_work_id="act_public_2025_4",
        amending_provision_hrefs=("A7",),
        instruction_semantic_candidate_status=NZInstructionSemanticCandidateStatus.BLOCKED_INSTRUCTION_INDIRECTION,
        instruction_semantic_candidate_family=NZInstructionSemanticCandidateFamily.SCHEDULE_OR_OMNIBUS_INDIRECTION,
        instruction_semantic_rule_id="nz_instruction_semantics_blocked_schedule_or_omnibus_indirection",
        payload_instruction_shape=NZPayloadInstructionShape.SCHEDULE_INDIRECTION,
        payload_instruction_safety=NZPayloadInstructionSafety.UNSAFE_SCHEDULE_OR_OMNIBUS_INDIRECTION,
        payload_match_headings=("Amendments to principal Act",),
        payload_text_snippets=("The Schedule amends the principal Act.",),
        payload_structural_subfamily_status=NZStructuralSubfamilyStatus.BLOCKED_SCHEDULE_INDIRECTION_PAYLOAD,
        payload_structural_subfamily=NZStructuralSubfamily.SCHEDULE_INDIRECTION_PAYLOAD,
        payload_structural_subfamily_rule_id="nz_instruction_structural_subfamily_schedule_indirection_payload_blocked",
        latest_oracle_text_status=NZLatestOracleTextStatus.NOT_APPLICABLE_NOT_DIRECT_TEXT_SUBSTITUTION,
        latest_oracle_text_rule_id="nz_instruction_latest_oracle_text_not_applicable",
    )


def _candidate_row() -> NZInstructionWorkQueueRow:
    return NZInstructionWorkQueueRow(
        row_id="nz-instruction-workqueue-2",
        operation_row_id="nz-op-2",
        effect_readiness_row_id="nz-er-2",
        queue_status=NZWorkQueueStatus.CANDIDATE,
        operation_family="amended",
        target_address="section:1",
        effect_readiness_status="blocked_text_or_structural_amendment_semantics_not_extracted",
        blocking_rule_id="nz_effect_readiness_amendment_semantics_not_extracted",
        amending_work_id="act_public_2025_4",
        amending_provision_hrefs=("A3",),
        instruction_semantic_candidate_status=NZInstructionSemanticCandidateStatus.CANDIDATE_ONLY_INSTRUCTION_SEMANTICS,
        instruction_semantic_candidate_family=NZInstructionSemanticCandidateFamily.AMEND_INSTRUCTION,
        instruction_semantic_rule_id="nz_instruction_semantics_candidate_direct_instruction",
        payload_instruction_shape=NZPayloadInstructionShape.DIRECT_AMENDED_BY_INSTRUCTION,
        payload_instruction_safety=NZPayloadInstructionSafety.CANDIDATE_ONLY_SEMANTIC_CLASSIFICATION,
        payload_match_headings=("Amend",),
        payload_text_snippets=("Section 1 is amended by replacing Old with New.",),
    )


def test_adapter_maps_blocked_row_to_frontier_work_item_with_witnesses_options_and_next_action() -> None:
    row = _blocked_schedule_indirection_row()

    item = nz_frontier_work_item_from_workqueue_row(row, work_id="act_public_2020_1")

    assert isinstance(item, FrontierWorkItem)
    # Contract: non-executable, never replay-authorized.
    assert item.executable is False
    assert item.replay_authorized is False
    assert validate_frontier_work_item(item.to_dict()) == ()

    assert item.work_item_id == "nz-frontier-act_public_2020_1-nz-instruction-workqueue-1"
    assert item.jurisdiction == "nz"
    assert item.frontier_status == "blocked"
    assert item.frontier_family == "schedule_indirection_payload"
    assert item.candidate_operation_family == "schedule_indirection_resolution"
    assert item.required_claim_kind == "nz_instruction_semantic_compile"

    # Source witness carries the amending provision + payload snippet witnesses.
    assert item.source_witness["amending_work_id"] == "act_public_2025_4"
    assert item.source_witness["amending_provision_hrefs"] == ("A7",)
    assert item.source_witness["payload_text_snippets"] == (
        "The Schedule amends the principal Act.",
    )
    assert item.source_witness["preview_digest"]

    # Target candidate witness + candidate target.
    assert item.target_witness["target_address"] == "section:12/subsection:3"
    assert item.candidate_targets == ("section:12/subsection:3",)

    # Payload (latest-oracle) compare witness present and flagged non-authoritative.
    assert (
        item.compare_witness["latest_oracle_text_status"]
        == "not_applicable_not_direct_text_substitution"
    )
    assert item.compare_witness["latest_oracle_text_not_payload_authority"] is True

    # Options + guidance + adjudication prompt + next action.
    assert tuple(item.detail["probable_options"]) == (
        "resolve_schedule_indirection_to_direct_instructions",
    )
    assert item.detail["exhaustive_options_available"] is False
    assert "schedule" in item.detail["adjudication_prompt"].lower()
    assert item.detail["next_action"]
    assert item.guidance_refs
    assert item.required_validator_checks
    assert item.required_proofs
    assert item.forbidden_shortcuts


def test_frontier_work_items_selects_only_blocked_and_review_rows() -> None:
    report = NZInstructionWorkQueueReport(
        work_id="act_public_2020_1",
        rows=(_blocked_schedule_indirection_row(), _candidate_row()),
    )

    items = frontier_work_items(report)

    assert len(items) == 1
    assert items[0].frontier_status == "blocked"

    # The report convenience method routes through the same adapter.
    assert report.frontier_work_items() == items

    summary = frontier_work_items_summary(items)
    assert summary["frontier_work_item_count"] == 1
    assert summary["frontier_status_counts"] == {"blocked": 1}
    assert summary["replay_claims"] is False
    assert summary["canonical_effect_claims"] is False


def test_exhaustive_options_available_set_for_finite_choice_families() -> None:
    # section-after-insert enumerates a finite (closed) choice set.
    row = dataclasses.replace(
        _blocked_schedule_indirection_row(),
        payload_structural_subfamily="section_after_insert_payload",
    )
    finite = nz_frontier_work_item_from_workqueue_row(row, work_id="act_public_2020_1")
    assert finite.detail["exhaustive_options_available"] is True


def test_review_row_from_real_surfaces_projects_a_frontier_item() -> None:
    target_xml = b"""\
<act>
  <body>
    <prov id="S1"><label>1</label><heading>Target</heading>
      <notes>
        <history-note id="HN1">
          <amended-provision>Section 1</amended-provision>
          <amending-operation>amended</amending-operation>
          <amending-provision href="A3">section 3</amending-provision>
          <amending-leg>Example Amendment Act 2025</amending-leg>
          Section 1: amended by section 3 of the Example Amendment Act 2025 (2025 No 4).
        </history-note>
      </notes>
    </prov>
  </body>
</act>
"""
    amendment_xml = b"""\
<act>
  <body>
    <prov id="A3"><label>3</label><heading>Amend</heading>
      <prov.body><para><text>Amendment(s) incorporated in the Act(s).</text></para></prov.body>
    </prov>
  </body>
</act>
"""
    operation_surface = build_operation_surface(
        parse_nz_source_document(target_xml),
        work_id="act_public_2020_1",
        archived_dependency_work_ids=frozenset({"act_public_2025_4"}),
    )
    payload_surface = build_payload_surface(
        operation_surface,
        dependency_documents={"act_public_2025_4": parse_nz_source_document(amendment_xml)},
    )
    effect_readiness = build_effect_readiness_surface(operation_surface, payload_surface)
    report = build_instruction_workqueue(operation_surface, payload_surface, effect_readiness)

    review_rows = [row for row in report.rows if row.queue_status in {"blocked", "review"}]
    assert review_rows, "expected at least one blocked/review row"

    items = frontier_work_items(report)
    assert len(items) == len(review_rows)
    for item in items:
        assert item.executable is False
        assert item.replay_authorized is False
        assert validate_frontier_work_item(item.to_dict()) == ()
        assert item.detail["next_action"]


def test_cli_frontier_emit_text_and_json(capsys, monkeypatch) -> None:
    parser = _build_parser()
    args = parser.parse_args(
        ["nz-corpus", "frontier", "--work-id", "act_public_2020_1", "--json"]
    )
    assert args.command == "nz-corpus"
    assert args.nz_corpus_command == "frontier"

    report = NZInstructionWorkQueueReport(
        work_id="act_public_2020_1",
        rows=(_blocked_schedule_indirection_row(), _candidate_row()),
    )
    monkeypatch.setattr(
        "lawvm.new_zealand.frontier_work_items.build_archived_work_instruction_workqueue",
        lambda db, work_id: report,
    )

    json_args = argparse.Namespace(
        db="data/nz_legislation.farchive",
        work_id="act_public_2020_1",
        limit=40,
        summary_only=False,
        frontier_status="",
        frontier_family="",
        candidate_operation_family="",
        json=True,
    )
    nz_frontier_main(json_args)
    payload = json.loads(capsys.readouterr().out)
    assert payload["jurisdiction"] == "nz"
    assert payload["report_kind"] == "frontier_work_items"
    assert payload["replay_claims"] is False
    assert payload["canonical_effect_claims"] is False
    assert payload["summary"]["frontier_work_item_count"] == 1
    assert len(payload["rows"]) == 1
    assert payload["rows"][0]["frontier_status"] == "blocked"
    assert payload["rows"][0]["detail"]["next_action"]

    text_args = argparse.Namespace(
        db="data/nz_legislation.farchive",
        work_id="act_public_2020_1",
        limit=40,
        summary_only=False,
        frontier_status="blocked",
        frontier_family="",
        candidate_operation_family="",
        json=False,
    )
    nz_frontier_main(text_args)
    text_out = capsys.readouterr().out
    assert "frontier_work_items=1" in text_out
    assert "nz-frontier-act_public_2020_1-nz-instruction-workqueue-1" in text_out
    assert "next_action=" in text_out

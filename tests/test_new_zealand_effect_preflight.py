from __future__ import annotations

from lawvm.core.evidence_contracts import (
    validate_corpus_finding_evidence_row,
    validate_corpus_operation_evidence_row,
)
from lawvm.core.ir import LegalAddress, LegalOperation, TextPatchSpec, TextSelector
from lawvm.core.semantic_types import StructuralAction, TextPatchKindEnum
from lawvm.new_zealand.effect_candidates import (
    NZCanonicalEffectCandidateReport,
    NZCanonicalEffectCandidateRow,
    _SourceChangeTextWitness,
    build_effect_candidate_preflight,
    build_effect_candidate_surface,
    preflight_main,
    write_preflight_evidence_jsonl,
)
from lawvm.new_zealand.effect_readiness import build_effect_readiness_surface
from lawvm.new_zealand.instruction_workqueue import build_instruction_workqueue
from lawvm.new_zealand.operation_surface import build_operation_surface
from lawvm.new_zealand.payload_surface import build_payload_surface
from lawvm.new_zealand.source_tree import parse_nz_source_document
from lawvm.tools.cli import _build_parser
from lawvm.tools.report_query import load_report_query_records


def test_effect_candidate_preflight_accepts_complete_candidate_set_without_replay() -> None:
    candidate_report = _candidate_report(
        target_xml=b"""\
<act>
  <body>
    <prov id="S1"><label>1</label><heading>Target</heading>
      <notes>
        <history-note id="HN1">
          <amended-provision>Section 1</amended-provision>
          <amending-operation>repealed</amending-operation>
          <amending-provision href="A3">section 3</amending-provision>
          <amending-leg>Example Amendment Act 2025</amending-leg>
          Section 1: repealed by section 3 of the Example Amendment Act 2025 (2025 No 4).
        </history-note>
      </notes>
    </prov>
  </body>
</act>
""",
        amendment_xml=b"""\
<act>
  <body><prov id="A3"><label>3</label><heading>Repeal</heading></prov></body>
</act>
""",
    )

    report = build_effect_candidate_preflight(candidate_report)
    summary = report.summary()

    assert summary["preflight_status"] == "ready_for_dry_run_replay"
    assert summary["candidate_operations"] == 1
    assert summary["blocked_rows"] == 0
    assert summary["operations_to_replay"] == 1
    assert summary["replay_claims"] is False
    assert summary["dry_run_only"] is True
    assert summary["blocking_rule_id"] == ""
    assert len(report.operations_for_dry_run_replay()) == 1
    assert report.finding_evidence_rows() == ()
    evidence_rows = report.operation_evidence_rows()
    assert len(evidence_rows) == 1
    evidence_row = evidence_rows[0].to_dict()
    assert evidence_row["status"] == "accepted"
    assert evidence_row["strict_disposition"] == "candidate_only_preflight"
    assert evidence_row["detail"]["replay_claims"] is False
    assert evidence_row["detail"]["operation_lowering_readiness_status"] == "ready_for_amending_act_payload_extraction"
    assert evidence_row["detail"]["operation_target_address_status"] == "candidate"
    assert evidence_row["detail"]["operation_dependency_status"] == "amending_work_resolved_archived"
    assert evidence_row["detail"]["candidate_operation_missing"] is False
    assert evidence_row["detail"]["candidate_witness_rule_id"] == "nz_repeal_candidate_from_history_note_payload_witness"
    assert "not_replayed" in evidence_row["detail"]["candidate_provenance_tags"]
    assert validate_corpus_operation_evidence_row(evidence_row) == ()


def test_effect_candidate_preflight_accepts_complete_text_replace_candidate_set_without_replay() -> None:
    target_xml = b"""\
<act>
  <body>
    <prov id="S21"><label>21</label><heading>Target</heading>
      <prov.body><para><text>The provision already contains new words.</text></para></prov.body>
      <notes>
        <history-note id="HN1">
          <amended-provision>Section 21</amended-provision>
          <amending-operation>amended</amending-operation>
          <amending-provision href="A3">section 3</amending-provision>
          <amending-leg>Example Amendment Act 2025</amending-leg>
          Section 21: amended by section 3 of the Example Amendment Act 2025 (2025 No 4).
        </history-note>
      </notes>
    </prov>
  </body>
</act>
"""
    amendment_xml = b"""\
<act>
  <body>
    <prov id="A3"><label>3</label><heading>Text replacement</heading>
      <prov.body><para><text>In section 21, replace old words with new words.</text></para></prov.body>
    </prov>
  </body>
</act>
"""
    target_document = parse_nz_source_document(target_xml)
    operation_surface = build_operation_surface(
        target_document,
        work_id="act_public_2020_1",
        archived_dependency_work_ids=frozenset({"act_public_2025_4"}),
    )
    payload_surface = build_payload_surface(
        operation_surface,
        dependency_documents={"act_public_2025_4": parse_nz_source_document(amendment_xml)},
    )
    effect_readiness = build_effect_readiness_surface(operation_surface, payload_surface)
    instruction_workqueue = build_instruction_workqueue(
        operation_surface,
        payload_surface,
        effect_readiness,
        target_document,
    )
    candidate_report = build_effect_candidate_surface(
        operation_surface,
        payload_surface,
        effect_readiness,
        instruction_workqueue,
    )

    report = build_effect_candidate_preflight(candidate_report)
    summary = report.summary()
    evidence_row = report.operation_evidence_rows()[0].to_dict()

    assert summary["preflight_status"] == "ready_for_dry_run_replay"
    assert summary["candidate_operations"] == 1
    assert summary["blocked_rows"] == 0
    assert summary["operations_to_replay"] == 1
    assert len(report.operations_for_dry_run_replay()) == 1
    assert evidence_row["canonical_family"] == "text_replace"
    assert evidence_row["detail"]["instruction_subfamily"] == "direct_single_text_substitution"
    assert evidence_row["detail"]["latest_oracle_text_status"] == "oracle_new_text_only"
    assert evidence_row["detail"]["replay_claims"] is False
    assert validate_corpus_operation_evidence_row(evidence_row) == ()


def test_effect_candidate_preflight_blocks_source_change_only_text_replace_candidate() -> None:
    target_xml = b"""\
<act>
  <body>
    <prov id="S21"><label>21</label><heading>Target</heading>
      <prov.body><para><text>The provision now contains later words.</text></para></prov.body>
      <notes>
        <history-note id="HN1">
          <amended-provision>Section 21</amended-provision>
          <amending-operation>amended</amending-operation>
          <amending-provision href="A3">section 3</amending-provision>
          <amending-leg>Example Amendment Act 2025</amending-leg>
          Section 21: amended, on 1 January 2025, by section 3 of the Example Amendment Act 2025 (2025 No 4).
        </history-note>
      </notes>
    </prov>
  </body>
</act>
"""
    amendment_xml = b"""\
<act>
  <body>
    <prov id="A3"><label>3</label><heading>Text replacement</heading>
      <prov.body><para><text>In section 21, replace old words with new words.</text></para></prov.body>
    </prov>
  </body>
</act>
"""
    target_document = parse_nz_source_document(target_xml)
    operation_surface = build_operation_surface(
        target_document,
        work_id="act_public_2020_1",
        archived_dependency_work_ids=frozenset({"act_public_2025_4"}),
    )
    payload_surface = build_payload_surface(
        operation_surface,
        dependency_documents={"act_public_2025_4": parse_nz_source_document(amendment_xml)},
    )
    effect_readiness = build_effect_readiness_surface(operation_surface, payload_surface)
    instruction_workqueue = build_instruction_workqueue(
        operation_surface,
        payload_surface,
        effect_readiness,
        target_document,
    )
    candidate_report = build_effect_candidate_surface(
        operation_surface,
        payload_surface,
        effect_readiness,
        instruction_workqueue,
        source_change_text_witnesses={
            "nz-opw-1": _SourceChangeTextWitness(
                status="observed_single_replacement",
                rule_id="nz_source_change_text_observed_single_replacement",
                change_window_truth_claim="source_change_window_not_effective_date",
                requested_date="2025-01-01",
                before_version_id="act_public_2020_1_en_2024-12-31",
                before_xml_locator="before.xml",
                on_or_after_version_id="act_public_2020_1_en_2025-01-01",
                on_or_after_xml_locator="after.xml",
                target_source_path=("prov:21",),
                before_old_text_occurrences=1,
                before_new_text_occurrences=0,
                on_or_after_old_text_occurrences=0,
                on_or_after_new_text_occurrences=1,
            ),
        },
    )

    report = build_effect_candidate_preflight(candidate_report)
    summary = report.summary()
    blocked_rows = report.to_jsonable()["blocked_rows"]
    operation_row = report.operation_evidence_rows()[0].to_dict()
    finding_row = report.finding_evidence_rows()[0].to_dict()

    assert summary["preflight_status"] == "blocked_source_change_only_candidates"
    assert summary["candidate_operations"] == 1
    assert summary["replayable_candidate_operations"] == 0
    assert summary["source_change_only_candidate_rows"] == 1
    assert summary["blocked_rows"] == 1
    assert summary["operations_to_replay"] == 0
    assert summary["blocking_rule_id"] == "nz_effect_preflight_source_change_only_candidates_not_dry_run_replayable"
    assert summary["blocking_rule_counts"] == {
        "nz_effect_preflight_source_change_only_candidates_not_dry_run_replayable": 1,
    }
    assert report.operations_for_dry_run_replay() == ()
    assert blocked_rows[0]["preflight_blocking_rule_id"] == (
        "nz_effect_preflight_source_change_only_candidates_not_dry_run_replayable"
    )
    assert operation_row["status"] == "unsupported"
    assert operation_row["detail"]["status"] == "blocked_source_change_only_candidate"
    assert operation_row["detail"]["candidate_witness_rule_id"] == (
        "nz_text_replace_candidate_from_archived_source_change_witness"
    )
    assert operation_row["detail"]["reason"] == (
        "nz_effect_preflight_source_change_only_candidates_not_dry_run_replayable"
    )
    assert operation_row["detail"]["candidate_operation_missing"] is False
    assert validate_corpus_operation_evidence_row(operation_row) == ()
    assert finding_row["rule_id"] == "nz_effect_preflight_source_change_only_candidates_not_dry_run_replayable"
    assert finding_row["related_row_ids"] == ("nz-effect-candidate-1",)
    assert validate_corpus_finding_evidence_row(finding_row) == ()


def test_effect_candidate_preflight_blocks_target_recovery_text_replace_candidate() -> None:
    target_xml = b"""\
<act>
  <body>
    <prov id="S21"><label>21</label><heading>Target</heading>
      <subprov>
        <para><text>The purposes are:</text>
          <label-para><label>a</label><para><text>new words are present.</text></para></label-para>
        </para>
      </subprov>
      <notes>
        <history-note id="HN1">
          <amended-provision>Section 21(a)</amended-provision>
          <amending-operation>amended</amending-operation>
          <amending-provision href="A3">section 3</amending-provision>
          <amending-leg>Example Amendment Act 2025</amending-leg>
          Section 21(a): amended by section 3 of the Example Amendment Act 2025 (2025 No 4).
        </history-note>
      </notes>
    </prov>
  </body>
</act>
"""
    amendment_xml = b"""\
<act>
  <body>
    <prov id="A3"><label>3</label><heading>Text replacement</heading>
      <prov.body><para><text>In section 21(a), replace old words with new words.</text></para></prov.body>
    </prov>
  </body>
</act>
"""
    target_document = parse_nz_source_document(target_xml)
    operation_surface = build_operation_surface(
        target_document,
        work_id="act_public_2020_1",
        archived_dependency_work_ids=frozenset({"act_public_2025_4"}),
    )
    payload_surface = build_payload_surface(
        operation_surface,
        dependency_documents={"act_public_2025_4": parse_nz_source_document(amendment_xml)},
    )
    effect_readiness = build_effect_readiness_surface(operation_surface, payload_surface)
    instruction_workqueue = build_instruction_workqueue(
        operation_surface,
        payload_surface,
        effect_readiness,
        target_document,
    )
    candidate_report = build_effect_candidate_surface(
        operation_surface,
        payload_surface,
        effect_readiness,
        instruction_workqueue,
    )

    report = build_effect_candidate_preflight(candidate_report)
    summary = report.summary()
    blocked_rows = report.to_jsonable()["blocked_rows"]
    operation_row = report.operation_evidence_rows()[0].to_dict()
    finding_row = report.finding_evidence_rows()[0].to_dict()

    assert candidate_report.rows[0].status == "candidate_emitted"
    assert candidate_report.rows[0].latest_oracle_target_resolution_status == "via_unlabeled_source_carrier"
    assert summary["preflight_status"] == "blocked_target_recovery_candidates"
    assert summary["candidate_operations"] == 1
    assert summary["replayable_candidate_operations"] == 0
    assert summary["source_change_only_candidate_rows"] == 0
    assert summary["target_recovery_candidate_rows"] == 1
    assert summary["blocked_rows"] == 1
    assert summary["operations_to_replay"] == 0
    assert summary["blocking_rule_id"] == "nz_effect_preflight_target_recovery_candidates_not_dry_run_replayable"
    assert summary["blocking_rule_counts"] == {
        "nz_effect_preflight_target_recovery_candidates_not_dry_run_replayable": 1,
    }
    assert report.operations_for_dry_run_replay() == ()
    assert blocked_rows[0]["preflight_blocking_rule_id"] == (
        "nz_effect_preflight_target_recovery_candidates_not_dry_run_replayable"
    )
    assert operation_row["status"] == "unsupported"
    assert operation_row["detail"]["status"] == "blocked_target_recovery_candidate"
    assert operation_row["detail"]["reason"] == (
        "nz_effect_preflight_target_recovery_candidates_not_dry_run_replayable"
    )
    assert operation_row["detail"]["latest_oracle_target_resolution_status"] == "via_unlabeled_source_carrier"
    assert validate_corpus_operation_evidence_row(operation_row) == ()
    assert finding_row["rule_id"] == "nz_effect_preflight_target_recovery_candidates_not_dry_run_replayable"
    assert finding_row["related_row_ids"] == ("nz-effect-candidate-1",)
    assert validate_corpus_finding_evidence_row(finding_row) == ()


def test_effect_candidate_preflight_labels_mixed_non_replayable_candidate_set() -> None:
    candidate_report = NZCanonicalEffectCandidateReport(
        work_id="act_public_2020_1",
        rows=(
            NZCanonicalEffectCandidateRow(
                row_id="nz-effect-candidate-1",
                operation_row_id="nz-opw-1",
                effect_readiness_row_id="nz-effect-ready-1",
                status="candidate_emitted",
                action="text_replace",
                target_address="section:21",
                operation=_text_replace_operation(
                    op_id="nz:act_public_2020_1:nz-opw-1:text_replace",
                    witness_rule_id="nz_text_replace_candidate_from_archived_source_change_witness",
                ),
                latest_oracle_target_resolution_status="exact_source_path",
            ),
            NZCanonicalEffectCandidateRow(
                row_id="nz-effect-candidate-2",
                operation_row_id="nz-opw-2",
                effect_readiness_row_id="nz-effect-ready-2",
                status="candidate_emitted",
                action="text_replace",
                target_address="section:21/paragraph:a",
                operation=_text_replace_operation(
                    op_id="nz:act_public_2020_1:nz-opw-2:text_replace",
                    witness_rule_id="nz_text_replace_candidate_from_direct_instruction_workqueue",
                ),
                latest_oracle_target_resolution_status="via_unlabeled_source_carrier",
            ),
        ),
    )

    report = build_effect_candidate_preflight(candidate_report)
    summary = report.summary()
    finding_row = report.finding_evidence_rows()[0].to_dict()

    assert summary["preflight_status"] == "blocked_non_replayable_candidates"
    assert summary["candidate_operations"] == 2
    assert summary["replayable_candidate_operations"] == 0
    assert summary["source_change_only_candidate_rows"] == 1
    assert summary["target_recovery_candidate_rows"] == 1
    assert summary["blocked_rows"] == 2
    assert summary["operations_to_replay"] == 0
    assert summary["blocking_rule_id"] == "nz_effect_preflight_non_replayable_candidates_not_dry_run_replayable"
    assert summary["blocking_rule_counts"] == {
        "nz_effect_preflight_source_change_only_candidates_not_dry_run_replayable": 1,
        "nz_effect_preflight_target_recovery_candidates_not_dry_run_replayable": 1,
    }
    assert report.operations_for_dry_run_replay() == ()
    assert finding_row["rule_id"] == "nz_effect_preflight_non_replayable_candidates_not_dry_run_replayable"
    assert finding_row["related_row_ids"] == ("nz-effect-candidate-1", "nz-effect-candidate-2")
    assert validate_corpus_finding_evidence_row(finding_row) == ()


def test_effect_candidate_preflight_counts_candidate_with_two_non_replayable_traits_once() -> None:
    candidate_report = NZCanonicalEffectCandidateReport(
        work_id="act_public_2020_1",
        rows=(
            NZCanonicalEffectCandidateRow(
                row_id="nz-effect-candidate-1",
                operation_row_id="nz-opw-1",
                effect_readiness_row_id="nz-effect-ready-1",
                status="candidate_emitted",
                action="text_replace",
                target_address="section:21/paragraph:a",
                operation=_text_replace_operation(
                    op_id="nz:act_public_2020_1:nz-opw-1:text_replace",
                    witness_rule_id="nz_text_replace_candidate_from_archived_source_change_witness",
                ),
                latest_oracle_target_resolution_status="via_unlabeled_source_carrier",
            ),
        ),
    )

    report = build_effect_candidate_preflight(candidate_report)
    summary = report.summary()
    blocked_row = report.to_jsonable()["blocked_rows"][0]
    operation_row = report.operation_evidence_rows()[0].to_dict()
    finding_row = report.finding_evidence_rows()[0].to_dict()

    assert summary["preflight_status"] == "blocked_non_replayable_candidates"
    assert summary["candidate_operations"] == 1
    assert summary["replayable_candidate_operations"] == 0
    assert summary["source_change_only_candidate_rows"] == 1
    assert summary["target_recovery_candidate_rows"] == 1
    assert summary["blocked_rows"] == 1
    assert summary["blocking_rule_counts"] == {
        "nz_effect_preflight_source_change_only_candidates_not_dry_run_replayable": 1,
        "nz_effect_preflight_target_recovery_candidates_not_dry_run_replayable": 1,
    }
    assert blocked_row["preflight_blocking_rule_ids"] == (
        "nz_effect_preflight_source_change_only_candidates_not_dry_run_replayable",
        "nz_effect_preflight_target_recovery_candidates_not_dry_run_replayable",
    )
    assert operation_row["detail"]["status"] == "blocked_non_replayable_candidate"
    assert operation_row["detail"]["reason"] == (
        "nz_effect_preflight_non_replayable_candidates_not_dry_run_replayable"
    )
    assert operation_row["detail"]["row_blocking_rule_ids"] == (
        "nz_effect_preflight_source_change_only_candidates_not_dry_run_replayable",
        "nz_effect_preflight_target_recovery_candidates_not_dry_run_replayable",
    )
    assert finding_row["related_row_ids"] == ("nz-effect-candidate-1",)
    assert validate_corpus_operation_evidence_row(operation_row) == ()
    assert validate_corpus_finding_evidence_row(finding_row) == ()


def test_effect_candidate_preflight_blocks_mixed_candidate_set_but_preserves_row_evidence() -> None:
    candidate_report = _candidate_report(
        target_xml=b"""\
<act>
  <body>
    <prov id="S1"><label>1</label><heading>First target</heading>
      <notes>
        <history-note id="HN1">
          <amended-provision>Section 1</amended-provision>
          <amending-operation>repealed</amending-operation>
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
""",
        amendment_xml=b"""\
<act>
  <body>
    <prov id="A3"><label>3</label><heading>Repeal</heading></prov>
    <prov id="A4"><label>4</label><heading>Amend</heading></prov>
  </body>
</act>
""",
    )

    report = build_effect_candidate_preflight(candidate_report)
    summary = report.summary()

    assert summary["preflight_status"] == "blocked_incomplete_candidate_set"
    assert summary["candidate_operations"] == 1
    assert summary["blocked_rows"] == 1
    assert summary["operations_to_replay"] == 0
    assert report.operations_for_dry_run_replay() == ()
    assert summary["blocking_rule_id"] == "nz_effect_preflight_refused_blocked_candidate_rows"
    assert summary["blocking_rule_counts"] == {
        "nz_effect_readiness_amendment_semantics_not_extracted": 1,
    }
    operation_rows = [row.to_dict() for row in report.operation_evidence_rows()]
    assert [row["status"] for row in operation_rows] == ["accepted", "unsupported"]
    assert operation_rows[0]["detail"]["batch_blocked"] is True
    assert operation_rows[1]["detail"]["reason"] == "nz_effect_readiness_amendment_semantics_not_extracted"
    assert operation_rows[1]["detail"]["batch_blocking_rule_id"] == (
        "nz_effect_preflight_refused_blocked_candidate_rows"
    )
    assert all(validate_corpus_operation_evidence_row(row) == () for row in operation_rows)
    finding_rows = [row.to_dict() for row in report.finding_evidence_rows()]
    assert len(finding_rows) == 1
    assert finding_rows[0]["rule_id"] == "nz_effect_preflight_refused_blocked_candidate_rows"
    assert finding_rows[0]["phase"] == "preflight"
    assert finding_rows[0]["blocking"] is True
    assert validate_corpus_finding_evidence_row(finding_rows[0]) == ()


def test_effect_candidate_preflight_blocks_empty_candidate_report_with_distinct_rule() -> None:
    report = build_effect_candidate_preflight(
        NZCanonicalEffectCandidateReport(work_id="act_public_2020_1", rows=())
    )
    summary = report.summary()

    assert summary["preflight_status"] == "blocked_no_candidate_rows"
    assert summary["candidate_operations"] == 0
    assert summary["blocked_rows"] == 0
    assert summary["operations_to_replay"] == 0
    assert summary["blocking_rule_id"] == "nz_effect_preflight_no_candidate_rows"
    assert report.operation_evidence_rows() == ()
    finding_rows = [row.to_dict() for row in report.finding_evidence_rows()]
    assert len(finding_rows) == 1
    assert finding_rows[0]["rule_id"] == "nz_effect_preflight_no_candidate_rows"
    assert finding_rows[0]["related_row_ids"] == ()
    assert finding_rows[0]["strict_disposition"] == "block"
    assert validate_corpus_finding_evidence_row(finding_rows[0]) == ()


def test_effect_candidate_preflight_blocks_emitted_candidate_without_operation() -> None:
    candidate_report = NZCanonicalEffectCandidateReport(
        work_id="act_public_2020_1",
        rows=(
            NZCanonicalEffectCandidateRow(
                row_id="nz-effect-candidate-1",
                operation_row_id="nz-opw-1",
                effect_readiness_row_id="nz-effect-ready-1",
                status="candidate_emitted",
                action="repeal",
                target_address="section:1",
                operation=None,
            ),
        ),
    )
    report = build_effect_candidate_preflight(candidate_report)
    summary = report.summary()

    assert summary["preflight_status"] == "blocked_candidate_operation_missing"
    assert summary["candidate_operations"] == 0
    assert summary["blocked_rows"] == 1
    assert summary["operations_to_replay"] == 0
    assert summary["blocking_rule_id"] == "nz_effect_preflight_candidate_operation_missing"
    assert summary["blocking_rule_counts"] == {
        "nz_effect_preflight_candidate_operation_missing": 1,
    }
    blocked_rows = report.to_jsonable()["blocked_rows"]
    assert blocked_rows[0]["preflight_blocking_rule_id"] == "nz_effect_preflight_candidate_operation_missing"
    assert report.operations_for_dry_run_replay() == ()
    operation_rows = [row.to_dict() for row in report.operation_evidence_rows()]
    assert operation_rows[0]["status"] == "unsupported"
    assert operation_rows[0]["detail"]["status"] == "blocked_candidate_operation_missing"
    assert operation_rows[0]["detail"]["reason"] == "nz_effect_preflight_candidate_operation_missing"
    assert operation_rows[0]["detail"]["candidate_operation_missing"] is True
    assert operation_rows[0]["detail"]["batch_blocking_rule_id"] == (
        "nz_effect_preflight_candidate_operation_missing"
    )
    assert validate_corpus_operation_evidence_row(operation_rows[0]) == ()
    finding_rows = [row.to_dict() for row in report.finding_evidence_rows()]
    assert finding_rows[0]["rule_id"] == "nz_effect_preflight_candidate_operation_missing"
    assert finding_rows[0]["related_row_ids"] == ("nz-effect-candidate-1",)
    assert validate_corpus_finding_evidence_row(finding_rows[0]) == ()


def test_preflight_text_cli_prints_emitted_candidate_missing_operation(monkeypatch, capsys) -> None:
    candidate_report = NZCanonicalEffectCandidateReport(
        work_id="act_public_2020_1",
        rows=(
            NZCanonicalEffectCandidateRow(
                row_id="nz-effect-candidate-1",
                operation_row_id="nz-opw-1",
                effect_readiness_row_id="nz-effect-ready-1",
                status="candidate_emitted",
                action="repeal",
                target_address="section:1",
                operation=None,
            ),
        ),
    )

    def fake_build_archived_work_effect_candidate_preflight(*_args):
        return build_effect_candidate_preflight(candidate_report)

    monkeypatch.setattr(
        "lawvm.new_zealand.effect_candidates.build_archived_work_effect_candidate_preflight",
        fake_build_archived_work_effect_candidate_preflight,
    )
    args = _build_parser().parse_args(["nz-corpus", "candidate-preflight", "--work-id", "act_public_2020_1"])

    preflight_main(args)

    output = capsys.readouterr().out
    assert "preflight_status=blocked_candidate_operation_missing" in output
    assert "replayable_candidate_operations=0" in output
    assert "source_change_only_candidate_rows=0" in output
    assert "target_recovery_candidate_rows=0" in output
    assert "blocking_rule_id=nz_effect_preflight_candidate_operation_missing" in output
    assert "nz-effect-candidate-1\tnz-opw-1\tnz_effect_preflight_candidate_operation_missing" in output


def test_write_effect_candidate_preflight_evidence_jsonl_is_report_query_compatible(tmp_path) -> None:
    report = build_effect_candidate_preflight(
        _candidate_report(
            target_xml=b"""\
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
""",
            amendment_xml=b"""\
<act>
  <body><prov id="A3"><label>3</label><heading>Amend</heading></prov></body>
</act>
""",
        )
    )
    path = tmp_path / "nz_effect_preflight_evidence.jsonl"

    count = write_preflight_evidence_jsonl(report, path)
    records = load_report_query_records((path,), validate=True)

    assert count == 2
    assert len(records) == 2
    assert all(record.validation_issues == () for record in records)
    assert {record.row_kind for record in records} == {"operation", "finding"}
    assert any(
        record.evidence_row.get("rule_id") == "nz_effect_preflight_refused_blocked_candidate_rows"
        for record in records
    )


def test_nz_candidate_preflight_cli_parse_defaults() -> None:
    parser = _build_parser()

    args = parser.parse_args(["nz-corpus", "candidate-preflight", "--work-id", "act_public_1957_87"])

    assert args.command == "nz-corpus"
    assert args.nz_corpus_command == "candidate-preflight"
    assert args.db == "data/nz_legislation.farchive"
    assert args.work_id == "act_public_1957_87"
    assert args.limit == 40
    assert args.evidence_rows is False
    assert args.evidence_jsonl is None


def _candidate_report(*, target_xml: bytes, amendment_xml: bytes):
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
    return build_effect_candidate_surface(operation_surface, payload_surface, effect_readiness)


def _text_replace_operation(*, op_id: str, witness_rule_id: str) -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=(("section", "21"),)),
        payload=None,
        text_patch=TextPatchSpec(
            kind=TextPatchKindEnum.REPLACE,
            selector=TextSelector(match_text="old words", occurrence=1),
            replacement="new words",
        ),
        provenance_tags=("new_zealand", "candidate_only", "not_replayed"),
        witness_rule_id=witness_rule_id,
    )

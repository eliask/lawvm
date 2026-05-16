from __future__ import annotations

import argparse

from lawvm.core.evidence_contracts import validate_corpus_operation_evidence_row
from lawvm.new_zealand import effect_candidates
from lawvm.new_zealand.effect_candidates import NZCanonicalEffectCandidateReport
from lawvm.new_zealand.effect_candidates import NZCanonicalEffectCandidateRow
from lawvm.new_zealand.effect_candidates import _SourceChangeTextWitness
from lawvm.new_zealand.effect_candidates import _source_change_text_status
from lawvm.new_zealand.effect_candidates import build_effect_candidate_surface
from lawvm.new_zealand.effect_candidates import write_evidence_jsonl
from lawvm.new_zealand.effect_readiness import build_effect_readiness_surface
from lawvm.new_zealand.instruction_workqueue import build_instruction_workqueue
from lawvm.new_zealand.operation_surface import build_operation_surface
from lawvm.new_zealand.payload_surface import build_payload_surface
from lawvm.new_zealand.source_tree import parse_nz_source_document
from lawvm.new_zealand.version_diff import NZArchivedVersion, NZArchivedVersionDateWindow
from lawvm.tools.report_query import load_report_query_records
from lawvm.tools.cli import _build_parser


def test_build_effect_candidate_surface_emits_repeal_legal_operation_candidate_only() -> None:
    target_xml = b"""\
<act>
  <body>
    <prov id="S1"><label>1</label><heading>Target</heading>
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
  </body>
</act>
"""
    amendment_xml = b"""\
<act>
  <body><prov id="A3"><label>3</label><heading>Repeal</heading></prov></body>
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
    instruction_workqueue = build_instruction_workqueue(operation_surface, payload_surface, effect_readiness)

    report = build_effect_candidate_surface(
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
    payload = report.to_jsonable()

    assert payload["replay_claims"] is False
    assert payload["canonical_effect_candidate_claims"] is True
    assert report.summary()["candidate_status_counts"] == {"candidate_emitted": 1}
    assert report.summary()["candidate_emitted_rows"] == 1
    assert report.summary()["candidate_operation_missing_rows"] == 0
    assert report.summary()["candidate_operations"] == 1
    assert report.summary()["candidate_blocking_rule_counts"] == {}
    assert report.summary()["candidate_witness_rule_counts"] == {
        "nz_repeal_candidate_from_history_note_payload_witness": 1,
    }
    assert report.summary()["candidate_action_witness_rule_counts"] == {
        "repeal|nz_repeal_candidate_from_history_note_payload_witness": 1,
    }
    row = report.rows[0]
    assert row.operation_family == "repealed"
    assert row.action == "repeal"
    assert row.target_address == "section:1"
    assert row.operation is not None
    assert row.source_path
    assert row.source_xml_path
    assert row.source_kind == "prov"
    assert row.operation_text == "repealed"
    assert row.amended_provision == "Section 1"
    assert row.amending_work_id == "act_public_2025_4"
    assert "repealed by section 3" in row.witness_text
    assert row.operation.payload is None
    assert row.operation.source is not None
    assert row.operation.source.statute_id == "act_public_2025_4"
    assert "candidate_only" in row.operation.provenance_tags
    assert row.payload_match_count == 1
    assert row.payload_role == "amending_provision_witness"
    assert row.payload_semantics_status == "operation_witness_sufficient_no_enacted_payload_required"
    assert row.payload_instruction_shape == "empty_or_stub"
    assert row.payload_instruction_safety == "unsafe_opaque_or_unclassified"
    assert row.instruction_semantic_candidate_status == "not_required_for_repeal_candidate"
    assert row.instruction_semantic_candidate_family == "repeal_without_enacted_payload"
    assert row.instruction_semantic_rule_id == "nz_instruction_semantics_not_required_repeal"
    assert row.repeal_payload_corroboration_status == "not_required_non_direct_repeal_payload"
    assert row.repeal_payload_corroboration_rule_id == "nz_repeal_payload_corroboration_not_required_non_direct_payload"
    assert row.payload_match_headings == ("Repeal",)
    evidence_rows = report.operation_evidence_rows()
    assert len(evidence_rows) == 1
    assert validate_corpus_operation_evidence_row(evidence_rows[0].to_dict()) == ()
    assert evidence_rows[0].to_dict()["status"] == "accepted"
    assert evidence_rows[0].to_dict()["source_locator"] == row.source_xml_path
    assert evidence_rows[0].to_dict()["canonical_family"] == "repeal"
    assert evidence_rows[0].to_dict()["detail"]["source_xml_path"] == row.source_xml_path
    assert evidence_rows[0].to_dict()["detail"]["witness_text"] == row.witness_text
    assert (
        evidence_rows[0].to_dict()["detail"]["candidate_witness_rule_id"]
        == "nz_repeal_candidate_from_history_note_payload_witness"
    )
    assert "not_replayed" in evidence_rows[0].to_dict()["detail"]["candidate_provenance_tags"]
    assert (
        evidence_rows[0].to_dict()["detail"]["repeal_payload_corroboration_status"]
        == "not_required_non_direct_repeal_payload"
    )


def test_effect_candidate_summary_separates_emitted_rows_from_executable_operations() -> None:
    report = NZCanonicalEffectCandidateReport(
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
    summary = report.summary()

    assert summary["candidate_status_counts"] == {"candidate_emitted": 1}
    assert summary["candidate_emitted_rows"] == 1
    assert summary["candidate_operation_missing_rows"] == 1
    assert summary["candidate_operations"] == 0
    assert summary["candidate_witness_rule_counts"] == {"__missing_operation__": 1}
    evidence_row = report.operation_evidence_rows()[0].to_dict()
    assert evidence_row["status"] == "unsupported"
    assert evidence_row["blocking"] is True
    assert evidence_row["detail"]["reason"] == "nz_effect_candidate_emitted_operation_missing"
    assert evidence_row["detail"]["candidate_operation_missing"] is True
    assert validate_corpus_operation_evidence_row(evidence_row) == ()


def test_build_effect_candidate_surface_corroborates_direct_repeal_range_payload() -> None:
    target_xml = b"""\
<act>
  <body>
    <prov id="S26"><label>26</label><heading>Target</heading>
      <notes>
        <history-note id="HN1">
          <amended-provision>Section 26</amended-provision>
          <amending-operation>repealed</amending-operation>
          <amendment-date>1 January 2025</amendment-date>
          <amending-provision href="A3">section 3</amending-provision>
          <amending-leg>Example Amendment Act 2025</amending-leg>
          Section 26: repealed by section 3 of the Example Amendment Act 2025 (2025 No 4).
        </history-note>
      </notes>
    </prov>
  </body>
</act>
"""
    amendment_xml = b"""\
<act>
  <body>
    <prov id="A3"><label>3</label><heading>Sections 25 to 27 repealed</heading>
      <prov.body><para><text>Repeal sections 25 to 27.</text></para></prov.body>
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

    report = build_effect_candidate_surface(operation_surface, payload_surface)
    row = report.rows[0]

    assert row.status == "candidate_emitted"
    assert row.action == "repeal"
    assert row.repeal_payload_corroboration_status == "corroborated_direct_repeal_payload_target"
    assert row.repeal_payload_corroboration_rule_id == "nz_repeal_payload_target_corroborated"
    assert row.repeal_payload_cited_targets == ("25", "26", "27")
    assert report.summary()["operation_family_counts"] == {"repealed": 1}
    assert report.summary()["blocked_operation_family_counts"] == {}
    assert report.summary()["blocked_operation_family_rule_counts"] == {}
    assert report.summary()["blocked_operation_family_instruction_status_counts"] == {}
    assert report.summary()["repeal_payload_corroboration_status_counts"] == {
        "corroborated_direct_repeal_payload_target": 1,
    }
    filtered = report.to_jsonable(
        row_limit=10,
        action="repeal",
        operation_family="repealed",
        repeal_payload_corroboration_status="corroborated_direct_repeal_payload_target",
        operation_target_address_status="candidate",
    )
    assert len(filtered["rows"]) == 1
    assert filtered["filters"] == {
        "action": "repeal",
        "operation_family": "repealed",
        "repeal_payload_corroboration_status": "corroborated_direct_repeal_payload_target",
        "operation_target_address_status": "candidate",
    }
    assert filtered["filtered_summary"]["rows"] == 1
    assert filtered["filtered_summary"]["candidate_status_counts"] == {"candidate_emitted": 1}
    assert filtered["rows"][0]["operation_family"] == "repealed"
    assert filtered["rows"][0]["repeal_payload_cited_targets"] == ["25", "26", "27"]
    empty_filtered = report.to_jsonable(summary_only=True, candidate_status="blocked")
    assert empty_filtered["filters"] == {"candidate_status": "blocked"}
    assert empty_filtered["filtered_summary"]["rows"] == 0
    assert "rows" not in empty_filtered
    selected_rows = report.filtered_rows(
        action="repeal",
        operation_family="repealed",
        repeal_payload_corroboration_status="corroborated_direct_repeal_payload_target",
        operation_dependency_status="amending_work_resolved_archived",
    )
    assert len(report.operation_evidence_rows_for(selected_rows)) == 1


def test_build_effect_candidate_surface_blocks_direct_repeal_payload_target_mismatch() -> None:
    target_xml = b"""\
<act>
  <body>
    <prov id="S28"><label>28</label><heading>Target</heading>
      <notes>
        <history-note id="HN1">
          <amended-provision>Section 28</amended-provision>
          <amending-operation>repealed</amending-operation>
          <amendment-date>1 January 2025</amendment-date>
          <amending-provision href="A3">section 3</amending-provision>
          <amending-leg>Example Amendment Act 2025</amending-leg>
          Section 28: repealed by section 3 of the Example Amendment Act 2025 (2025 No 4).
        </history-note>
      </notes>
    </prov>
  </body>
</act>
"""
    amendment_xml = b"""\
<act>
  <body>
    <prov id="A3"><label>3</label><heading>Sections 25 to 27 repealed</heading>
      <prov.body><para><text>Repeal sections 25 to 27.</text></para></prov.body>
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

    report = build_effect_candidate_surface(operation_surface, payload_surface)
    row = report.rows[0]
    evidence_row = report.operation_evidence_rows()[0].to_dict()

    assert row.status == "blocked"
    assert row.operation is None
    assert row.blocking_rule_id == "nz_repeal_payload_target_mismatch"
    assert row.repeal_payload_corroboration_status == "blocked_direct_repeal_payload_target_mismatch"
    assert row.repeal_payload_cited_targets == ("25", "26", "27")
    assert report.summary()["candidate_status_counts"] == {"blocked": 1}
    assert report.summary()["candidate_blocking_rule_counts"] == {"nz_repeal_payload_target_mismatch": 1}
    assert evidence_row["status"] == "unsupported"
    assert evidence_row["blocking"] is True
    assert evidence_row["detail"]["repeal_payload_corroboration_rule_id"] == "nz_repeal_payload_target_mismatch"


def test_build_effect_candidate_surface_emits_text_replace_candidate_from_owned_instruction_workqueue() -> None:
    target_xml = b"""\
<act>
  <body>
    <prov id="S21"><label>21</label><heading>Target</heading>
      <prov.body><para><text>The provision already contains new words.</text></para></prov.body>
      <notes>
        <history-note id="HN1">
          <amended-provision>Section 21</amended-provision>
          <amending-operation>amended</amending-operation>
          <amendment-date>1 January 2025</amendment-date>
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

    report = build_effect_candidate_surface(
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
        source_change_text_witnesses={
            "nz-opw-1": _SourceChangeTextWitness(
                status="observed_single_replacement",
                rule_id="nz_source_change_text_observed_single_replacement",
                change_window_truth_claim="source_change_window_not_effective_date",
                requested_date="2025-01-01",
                before_version_id="act_public_2020_1_en_2024-12-31",
                before_xml_locator="before.xml",
                on_or_after_version_id="act_public_2020_1_en_2025-01-02",
                on_or_after_xml_locator="after.xml",
                target_source_path=("prov:21",),
                before_old_text_occurrences=1,
                before_new_text_occurrences=0,
                on_or_after_old_text_occurrences=0,
                on_or_after_new_text_occurrences=1,
            ),
        },
    )
    payload = report.to_jsonable()

    assert report.summary()["candidate_status_counts"] == {"candidate_emitted": 1}
    assert report.summary()["candidate_action_counts"] == {"text_replace": 1}
    assert report.summary()["instruction_subfamily_status_counts"] == {
        "candidate_direct_single_text_substitution": 1,
    }
    assert report.summary()["latest_oracle_text_status_counts"] == {"oracle_new_text_only": 1}
    assert report.summary()["candidate_action_source_change_text_witness_status_counts"] == {
        "text_replace|observed_single_replacement": 1,
    }
    assert report.summary()["text_replace_witness_support_status_counts"] == {
        "latest_oracle_and_source_change_observed": 1,
    }
    assert report.summary()["candidate_action_text_replace_witness_support_status_counts"] == {
        "text_replace|latest_oracle_and_source_change_observed": 1,
    }
    assert report.summary()["blocked_operation_family_source_change_text_witness_status_counts"] == {}
    row = report.rows[0]
    assert row.action == "text_replace"
    assert row.target_address == "section:21"
    assert row.amendment_date_iso == "2025-01-01"
    assert row.source_version_date_window_status == "source_version_date_window_available"
    assert row.source_version_date_window_truth_claim == "source_version_date_window_not_effective_date"
    assert row.source_version_on_or_before_version_id == "act_public_2020_1_en_2024-12-31"
    assert row.source_version_on_or_after_version_id == "act_public_2020_1_en_2025-01-02"
    assert row.source_change_text_witness_status == "observed_single_replacement"
    assert row.source_change_text_witness_truth_claim == "source_text_change_witness_not_replay_proof"
    assert row.source_change_text_change_window_truth_claim == "source_change_window_not_effective_date"
    assert row.source_change_text_target_source_path == ("prov:21",)
    assert row.source_change_text_before_old_occurrences == 1
    assert row.source_change_text_before_new_occurrences == 0
    assert row.source_change_text_on_or_after_old_occurrences == 0
    assert row.source_change_text_on_or_after_new_occurrences == 1
    assert row.text_replace_witness_support_status == "latest_oracle_and_source_change_observed"
    assert row.text_replace_witness_support_rule_id == (
        "nz_text_replace_witness_support_latest_oracle_and_source_change_observed"
    )
    assert row.text_replace_witness_support_truth_claim == "text_replace_witness_support_not_replay_proof"
    assert report.filtered_rows(source_change_text_witness_status="observed_single_replacement") == (row,)
    assert report.filtered_rows(source_change_text_witness_status="missing_change_window_witness") == ()
    assert report.filtered_rows(
        text_replace_witness_support_status="latest_oracle_and_source_change_observed"
    ) == (row,)
    assert report.filtered_rows(
        text_replace_witness_support_status="source_change_observed_latest_oracle_unavailable"
    ) == ()
    assert row.operation is not None
    assert row.operation.source is not None
    assert row.operation.source.effective == "1 January 2025"
    assert row.operation.payload is None
    assert row.operation.text_patch is not None
    assert row.operation.text_patch.selector.occurrence == 1
    assert row.operation.text_patch.selector.match_text == "old words"
    assert row.operation.text_patch.replacement == "new words"
    assert row.operation.witness_rule_id == "nz_text_replace_candidate_from_direct_instruction_workqueue"
    assert "latest_oracle_text_witness" in row.operation.provenance_tags
    assert row.instruction_subfamily == "direct_single_text_substitution"
    assert row.latest_oracle_text_status == "oracle_new_text_only"
    assert row.latest_oracle_target_resolution_status == "exact_source_path"
    assert payload["rows"][0]["operation"]["text_patch"] == {
        "kind": "replace",
        "selector": {
            "match_text": "old words",
            "occurrence": 1,
        },
        "replacement": "new words",
    }
    evidence_row = report.operation_evidence_rows()[0].to_dict()
    assert evidence_row["status"] == "accepted"
    assert evidence_row["canonical_family"] == "text_replace"
    assert evidence_row["detail"]["instruction_subfamily"] == "direct_single_text_substitution"
    assert evidence_row["detail"]["latest_oracle_text_status"] == "oracle_new_text_only"
    assert evidence_row["detail"]["source_version_date_window_status"] == "source_version_date_window_available"
    assert evidence_row["detail"]["source_version_on_or_after_version_id"] == "act_public_2020_1_en_2025-01-02"
    assert evidence_row["detail"]["source_change_text_witness_status"] == "observed_single_replacement"
    assert evidence_row["detail"]["source_change_text_change_window_truth_claim"] == (
        "source_change_window_not_effective_date"
    )
    assert evidence_row["detail"]["text_replace_witness_support_status"] == (
        "latest_oracle_and_source_change_observed"
    )
    assert evidence_row["detail"]["source_change_text_on_or_after_new_occurrences"] == 1
    assert validate_corpus_operation_evidence_row(evidence_row) == ()


def test_build_effect_candidate_surface_emits_text_replace_candidate_from_matching_multi_clause() -> None:
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
    <prov id="A3"><label>3</label><heading>Text replacements</heading>
      <prov.body>
        <para><text>1 In section 21, replace old words with new words. 2 In section 22, replace stale words with fresh words.</text></para>
      </prov.body>
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

    report = build_effect_candidate_surface(
        operation_surface,
        payload_surface,
        effect_readiness,
        instruction_workqueue,
    )
    row = report.rows[0]

    assert report.summary()["candidate_status_counts"] == {"candidate_emitted": 1}
    assert row.action == "text_replace"
    assert row.operation is not None
    assert row.operation.text_patch is not None
    assert row.operation.text_patch.selector.occurrence == 1
    assert row.operation.text_patch.selector.match_text == "old words"
    assert row.operation.text_patch.replacement == "new words"
    assert row.instruction_subfamily_status == "candidate_direct_multi_clause_text_substitution"
    assert row.instruction_subfamily_rule_id == (
        "nz_instruction_semantics_direct_multi_clause_text_substitution_candidate"
    )
    assert row.latest_oracle_text_status == "oracle_new_text_only"


def test_build_effect_candidate_surface_emits_each_place_text_replace_candidate() -> None:
    target_xml = b"""\
<act>
  <body>
    <prov id="S21"><label>21</label><heading>Target</heading>
      <prov.body><para><text>The lawyer may consult another lawyer.</text></para></prov.body>
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
      <prov.body><para><text>In section 21, replace solicitor with lawyer in each place.</text></para></prov.body>
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

    report = build_effect_candidate_surface(
        operation_surface,
        payload_surface,
        effect_readiness,
        instruction_workqueue,
    )
    row = report.rows[0]

    assert report.summary()["candidate_status_counts"] == {"candidate_emitted": 1}
    assert report.summary()["candidate_action_counts"] == {"text_replace": 1}
    assert report.summary()["instruction_subfamily_status_counts"] == {
        "candidate_direct_each_place_text_substitution": 1,
    }
    assert report.summary()["latest_oracle_text_status_counts"] == {
        "oracle_new_text_only_each_place": 1,
    }
    assert row.action == "text_replace"
    assert row.instruction_subfamily == "direct_each_place_text_substitution"
    assert row.text_substitution_scope == "inline_text_each_place"
    assert row.latest_oracle_old_text_occurrences == 0
    assert row.latest_oracle_new_text_occurrences == 2
    assert row.operation is not None
    assert row.operation.text_patch is not None
    assert row.operation.text_patch.selector.occurrence == 0
    assert row.operation.text_patch.selector.match_text == "solicitor"
    assert row.operation.text_patch.replacement == "lawyer"
    assert (
        report.filtered_rows(instruction_subfamily="direct_each_place_text_substitution")[0].row_id
        == row.row_id
    )


def test_build_effect_candidate_surface_emits_text_replace_candidate_from_omitting_substituting() -> None:
    target_xml = b"""\
<act>
  <body>
    <prov id="S21"><label>21</label><heading>Target</heading>
      <subprov id="S21-2"><label>2</label><para><text>The reminder notice that contains the required particulars.</text></para></subprov>
      <notes>
        <history-note id="HN1">
          <amended-provision>Section 21(2)</amended-provision>
          <amending-operation>amended</amending-operation>
          <amending-provision href="A3">section 3</amending-provision>
          <amending-leg>Example Amendment Act 2025</amending-leg>
          Section 21(2): amended by section 3 of the Example Amendment Act 2025 (2025 No 4).
        </history-note>
      </notes>
    </prov>
  </body>
</act>
"""
    amendment_xml = b"""\
<act>
  <body>
    <prov id="A3"><label>3</label><heading>Text substitution</heading>
      <prov.body><para><text>Section is amended by omitting 21(2) in the prescribed form containing and substituting that contains.</text></para></prov.body>
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

    report = build_effect_candidate_surface(
        operation_surface,
        payload_surface,
        effect_readiness,
        instruction_workqueue,
    )
    row = report.rows[0]

    assert report.summary()["candidate_status_counts"] == {"candidate_emitted": 1}
    assert row.action == "text_replace"
    assert row.operation is not None
    assert row.operation.text_patch is not None
    assert row.operation.text_patch.selector.occurrence == 1
    assert row.operation.text_patch.selector.match_text == "in the prescribed form containing"
    assert row.operation.text_patch.replacement == "that contains"
    assert row.instruction_subfamily_status == "candidate_direct_omitting_substituting_text_substitution"
    assert row.instruction_subfamily_rule_id == (
        "nz_instruction_semantics_direct_omitting_substituting_text_substitution_candidate"
    )


def test_build_effect_candidate_surface_blocks_text_replace_without_latest_oracle_witness() -> None:
    target_xml = b"""\
<act>
  <body>
    <prov id="S21"><label>21</label><heading>Target</heading>
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
    instruction_workqueue = build_instruction_workqueue(operation_surface, payload_surface, effect_readiness)

    report = build_effect_candidate_surface(
        operation_surface,
        payload_surface,
        effect_readiness,
        instruction_workqueue,
    )

    assert report.summary()["candidate_status_counts"] == {"blocked": 1}
    assert report.summary()["candidate_blocking_rule_counts"] == {
        "nz_text_replace_candidate_latest_oracle_witness_unavailable": 1,
    }
    assert report.summary()["blocked_operation_family_instruction_subfamily_status_counts"] == {
        "amended|candidate_direct_single_text_substitution": 1,
    }
    assert report.summary()["latest_oracle_text_status_counts"] == {
        "not_run_target_document_unavailable": 1,
    }
    assert report.rows[0].operation is None
    assert report.rows[0].blocking_rule_id == "nz_text_replace_candidate_latest_oracle_witness_unavailable"
    assert report.rows[0].instruction_subfamily == "direct_single_text_substitution"
    assert report.rows[0].latest_oracle_text_status == "not_run_target_document_unavailable"
    assert report.rows[0].text_replace_witness_support_status == "no_text_replace_witness_support"


def test_build_effect_candidate_surface_emits_text_replace_from_archived_source_change_witness() -> None:
    target_xml = b"""\
<act>
  <body>
    <prov id="S21"><label>21</label><heading>Target</heading>
      <prov.body><para><text>The provision now contains later words.</text></para></prov.body>
      <notes>
        <history-note id="HN1">
          <amended-provision>Section 21</amended-provision>
          <amending-operation>amended</amending-operation>
          <amendment-date>1 January 2025</amendment-date>
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

    report = build_effect_candidate_surface(
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
    row = report.rows[0]

    assert report.summary()["candidate_status_counts"] == {"candidate_emitted": 1}
    assert report.summary()["candidate_witness_rule_counts"] == {
        "nz_text_replace_candidate_from_archived_source_change_witness": 1,
    }
    assert row.action == "text_replace"
    assert row.operation is not None
    assert row.operation.witness_rule_id == "nz_text_replace_candidate_from_archived_source_change_witness"
    assert "source_change_text_witness" in row.operation.provenance_tags
    assert "latest_oracle_text_witness" not in row.operation.provenance_tags
    assert row.latest_oracle_text_status == "oracle_neither_old_nor_new_text"
    assert row.source_change_text_witness_status == "observed_single_replacement"
    assert row.text_replace_witness_support_status == "source_change_observed_latest_oracle_unavailable"
    assert row.operation.text_patch is not None
    assert row.operation.text_patch.selector.occurrence == 1
    assert row.operation.text_patch.selector.match_text == "old words"
    assert row.operation.text_patch.replacement == "new words"
    evidence_row = report.operation_evidence_rows()[0].to_dict()
    assert evidence_row["status"] == "accepted"
    assert evidence_row["detail"]["candidate_witness_rule_id"] == (
        "nz_text_replace_candidate_from_archived_source_change_witness"
    )
    assert evidence_row["detail"]["text_replace_witness_support_status"] == (
        "source_change_observed_latest_oracle_unavailable"
    )
    assert validate_corpus_operation_evidence_row(evidence_row) == ()


def test_build_effect_candidate_surface_keeps_partial_source_change_text_witness_blocked() -> None:
    target_xml = b"""\
<act>
  <body>
    <prov id="S21"><label>21</label><heading>Target</heading>
      <prov.body><para><text>The provision now contains later words.</text></para></prov.body>
      <notes>
        <history-note id="HN1">
          <amended-provision>Section 21</amended-provision>
          <amending-operation>amended</amending-operation>
          <amendment-date>1 January 2025</amendment-date>
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

    report = build_effect_candidate_surface(
        operation_surface,
        payload_surface,
        effect_readiness,
        instruction_workqueue,
        source_change_text_witnesses={
            "nz-opw-1": _SourceChangeTextWitness(
                status="partial_text_change_observed",
                rule_id="nz_source_change_text_partial_text_change_observed",
                requested_date="2025-01-01",
                target_source_path=("prov:21",),
                before_old_text_occurrences=1,
                before_new_text_occurrences=0,
                on_or_after_old_text_occurrences=1,
                on_or_after_new_text_occurrences=1,
            ),
        },
    )
    row = report.rows[0]

    assert report.summary()["candidate_status_counts"] == {"blocked": 1}
    assert row.operation is None
    assert row.blocking_rule_id == "nz_text_replace_candidate_latest_oracle_witness_unavailable"
    assert row.latest_oracle_text_status == "oracle_neither_old_nor_new_text"
    assert row.source_change_text_witness_status == "partial_text_change_observed"
    assert row.text_replace_witness_support_status == "no_text_replace_witness_support"


def test_build_effect_candidate_surface_rejects_source_change_witness_for_different_target() -> None:
    target_xml = b"""\
<act>
  <body>
    <prov id="S21"><label>21</label><heading>Target</heading>
      <prov.body><para><text>The provision now contains later words.</text></para></prov.body>
      <notes>
        <history-note id="HN1">
          <amended-provision>Section 21</amended-provision>
          <amending-operation>amended</amending-operation>
          <amendment-date>1 January 2025</amendment-date>
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

    report = build_effect_candidate_surface(
        operation_surface,
        payload_surface,
        effect_readiness,
        instruction_workqueue,
        source_change_text_witnesses={
            "nz-opw-1": _SourceChangeTextWitness(
                status="observed_single_replacement",
                rule_id="nz_source_change_text_observed_single_replacement",
                target_source_path=("prov:99",),
                before_old_text_occurrences=1,
                before_new_text_occurrences=0,
                on_or_after_old_text_occurrences=0,
                on_or_after_new_text_occurrences=1,
            ),
        },
    )
    row = report.rows[0]

    assert report.summary()["candidate_status_counts"] == {"blocked": 1}
    assert row.operation is None
    assert row.blocking_rule_id == "nz_text_replace_candidate_latest_oracle_witness_unavailable"
    assert row.source_change_text_witness_status == "observed_single_replacement"
    assert row.source_change_text_target_source_path == ("prov:99",)
    assert row.latest_oracle_target_source_path == ("prov:21",)
    assert row.text_replace_witness_support_status == "source_change_observed_target_mismatch"
    assert row.text_replace_witness_support_rule_id == (
        "nz_text_replace_witness_support_source_change_observed_target_mismatch"
    )


def test_source_change_text_each_place_witness_requires_preserved_replacement_count() -> None:
    assert (
        _source_change_text_status(
            before_old=2,
            before_new=0,
            after_old=0,
            after_new=2,
            scope="inline_text_each_place",
        )
        == "observed_each_place_replacement"
    )
    assert (
        _source_change_text_status(
            before_old=2,
            before_new=0,
            after_old=0,
            after_new=1,
            scope="inline_text_each_place",
        )
        == "partial_text_change_observed"
    )
    assert (
        _source_change_text_status(
            before_old=2,
            before_new=0,
            after_old=0,
            after_new=3,
            scope="inline_text_each_place",
        )
        == "partial_text_change_observed"
    )


def test_build_effect_candidate_surface_keeps_non_repeal_rows_blocked() -> None:
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
      <prov.body><para><text>Section 1 is amended by replacing Old with New.</text></para></prov.body>
    </prov>
  </body>
</act>
"""
    operation_surface = build_operation_surface(
        parse_nz_source_document(target_xml),
        archived_dependency_work_ids=frozenset({"act_public_2025_4"}),
    )
    payload_surface = build_payload_surface(
        operation_surface,
        dependency_documents={"act_public_2025_4": parse_nz_source_document(amendment_xml)},
    )
    effect_readiness = build_effect_readiness_surface(operation_surface, payload_surface)
    instruction_workqueue = build_instruction_workqueue(operation_surface, payload_surface, effect_readiness)

    report = build_effect_candidate_surface(
        operation_surface,
        payload_surface,
        effect_readiness,
        instruction_workqueue,
    )

    assert report.summary()["candidate_status_counts"] == {"blocked": 1}
    assert report.summary()["blocked_operation_family_payload_shape_counts"] == {
        "amended|direct_amended_by_instruction": 1,
    }
    assert report.summary()["blocked_operation_family_payload_safety_counts"] == {
        "amended|candidate_only_semantic_classification": 1,
    }
    assert report.summary()["blocked_operation_family_target_status_counts"] == {
        "amended|candidate": 1,
    }
    assert report.summary()["payload_structural_subfamily_status_counts"] == {
        "blocked_structural_amend_payload_not_lowered": 1,
    }
    assert report.summary()["payload_structural_subfamily_counts"] == {
        "direct_amend_payload": 1,
    }
    assert report.rows[0].operation is None
    assert report.rows[0].blocking_rule_id == "nz_effect_readiness_amendment_semantics_not_extracted"
    assert report.rows[0].payload_match_count == 1
    assert report.rows[0].payload_role == "amending_provision_witness"
    assert report.rows[0].payload_semantics_status == "amending_provision_witness_not_enacted_payload"
    assert report.rows[0].payload_instruction_shape == "direct_amended_by_instruction"
    assert report.rows[0].payload_instruction_safety == "candidate_only_semantic_classification"
    assert report.rows[0].instruction_semantic_candidate_status == "candidate_only_instruction_semantics"
    assert report.rows[0].instruction_semantic_candidate_family == "amend_instruction"
    assert report.rows[0].instruction_semantic_rule_id == "nz_instruction_semantics_candidate_direct_instruction"
    assert report.rows[0].payload_match_headings == ("Amend",)
    assert report.rows[0].payload_match_labels == ("3",)
    assert report.rows[0].payload_match_texts == ("Section 1 is amended by replacing Old with New.",)
    assert report.rows[0].payload_structural_subfamily_status == "blocked_structural_amend_payload_not_lowered"
    assert report.rows[0].payload_structural_subfamily == "direct_amend_payload"
    assert (
        report.rows[0].payload_structural_subfamily_rule_id
        == "nz_instruction_structural_subfamily_direct_amend_payload_blocked"
    )
    evidence_row = report.operation_evidence_rows()[0].to_dict()
    assert evidence_row["status"] == "unsupported"
    assert evidence_row["blocking"] is True
    assert evidence_row["detail"]["payload_match_headings"] == ("Amend",)
    assert evidence_row["detail"]["payload_semantics_status"] == "amending_provision_witness_not_enacted_payload"
    assert evidence_row["detail"]["payload_instruction_shape"] == "direct_amended_by_instruction"
    assert evidence_row["detail"]["payload_instruction_safety"] == "candidate_only_semantic_classification"
    assert evidence_row["detail"]["instruction_semantic_candidate_status"] == "candidate_only_instruction_semantics"
    assert evidence_row["detail"]["instruction_semantic_candidate_family"] == "amend_instruction"
    assert evidence_row["detail"]["instruction_semantic_rule_id"] == "nz_instruction_semantics_candidate_direct_instruction"
    assert evidence_row["detail"]["payload_match_texts"] == ("Section 1 is amended by replacing Old with New.",)
    assert evidence_row["detail"]["payload_structural_subfamily"] == "direct_amend_payload"
    assert (
        evidence_row["detail"]["payload_structural_subfamily_rule_id"]
        == "nz_instruction_structural_subfamily_direct_amend_payload_blocked"
    )
    assert validate_corpus_operation_evidence_row(evidence_row) == ()
    filtered = report.filtered_rows(
        payload_instruction_shape="direct_amended_by_instruction",
        payload_instruction_safety="candidate_only_semantic_classification",
        instruction_semantic_candidate_status="candidate_only_instruction_semantics",
        payload_structural_subfamily_status="blocked_structural_amend_payload_not_lowered",
        payload_structural_subfamily="direct_amend_payload",
    )
    assert filtered == report.rows
    assert report.filtered_rows(payload_instruction_shape="direct_insert_instruction") == ()


def test_build_effect_candidate_surface_preserves_upstream_target_blocker_context() -> None:
    target_xml = b"""\
<act>
  <body>
    <prov id="S1"><label>1</label><heading>First duplicate</heading>
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
    <prov id="S1B"><label>1</label><heading>Second duplicate</heading></prov>
  </body>
</act>
"""
    operation_surface = build_operation_surface(
        parse_nz_source_document(target_xml),
        archived_dependency_work_ids=frozenset({"act_public_2025_4"}),
    )
    payload_surface = build_payload_surface(operation_surface, dependency_documents={})
    effect_readiness = build_effect_readiness_surface(operation_surface, payload_surface)

    report = build_effect_candidate_surface(operation_surface, payload_surface, effect_readiness)
    row = report.rows[0]
    evidence_row = report.operation_evidence_rows()[0].to_dict()

    assert row.status == "blocked"
    assert row.blocking_rule_id == "nz_effect_readiness_operation_not_payload_ready"
    assert row.operation_family == "amended"
    assert row.operation_lowering_readiness_status == "blocked_duplicate_source_path"
    assert row.operation_target_surface_status == "duplicate_source_path"
    assert row.operation_target_hint_status == "parsed"
    assert row.operation_target_address_status == "blocked_duplicate_source_path"
    assert row.operation_target_blocking_rule_id == "nz_target_address_duplicate_source_path"
    assert row.operation_dependency_status == "amending_work_resolved_archived"
    assert evidence_row["detail"]["operation_family"] == "amended"
    assert evidence_row["detail"]["operation_lowering_readiness_status"] == "blocked_duplicate_source_path"
    assert evidence_row["detail"]["operation_target_blocking_rule_id"] == "nz_target_address_duplicate_source_path"
    assert validate_corpus_operation_evidence_row(evidence_row) == ()


def test_write_effect_candidate_evidence_jsonl_is_report_query_compatible(tmp_path) -> None:
    target_xml = b"""\
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
"""
    amendment_xml = b"""\
<act>
  <body><prov id="A3"><label>3</label><heading>Repeal</heading></prov></body>
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
    report = build_effect_candidate_surface(operation_surface, payload_surface)
    path = tmp_path / "nz_effect_candidate_evidence.jsonl"

    count = write_evidence_jsonl(report, path)
    records = load_report_query_records((path,), validate=True)

    assert count == 1
    assert len(records) == 1
    assert records[0].validation_issues == ()
    assert records[0].evidence_row["status"] == "accepted"


def test_nz_effect_candidates_text_cli_prints_filtered_context(monkeypatch, capsys) -> None:
    report = NZCanonicalEffectCandidateReport(
        work_id="act_public_2020_1",
        rows=(
            NZCanonicalEffectCandidateRow(
                row_id="candidate-row",
                operation_row_id="op-candidate",
                effect_readiness_row_id="ready-candidate",
                status="candidate_emitted",
                action="repeal",
                operation_family="repealed",
                target_address="section:1",
            ),
            NZCanonicalEffectCandidateRow(
                row_id="blocked-row",
                operation_row_id="op-blocked",
                effect_readiness_row_id="ready-blocked",
                status="blocked",
                action="text_replace",
                operation_family="amended",
                target_address="section:2",
                blocking_rule_id="nz_effect_readiness_amendment_semantics_not_extracted",
            ),
        ),
    )
    monkeypatch.setattr(effect_candidates, "build_archived_work_effect_candidate_surface", lambda _db, _work_id: report)
    args = argparse.Namespace(
        db="unused.farchive",
        work_id="act_public_2020_1",
        limit=10,
        summary_only=False,
        candidate_status="blocked",
        action="",
        operation_family="",
        blocking_rule="",
        instruction_subfamily_status="",
        instruction_subfamily="",
        payload_structural_subfamily_status="",
        payload_structural_subfamily="",
        repeal_payload_corroboration_status="",
        operation_lowering_readiness_status="",
        operation_target_address_status="",
        operation_dependency_status="",
        payload_instruction_shape="",
        payload_instruction_safety="",
        instruction_semantic_candidate_status="",
        latest_oracle_text_status="",
        text_replace_witness_support_status="",
        source_change_text_witness_status="",
        evidence_rows=False,
        evidence_jsonl=None,
        json=False,
    )

    effect_candidates.main(args)
    output = capsys.readouterr().out

    assert "rows=2 filtered_rows=1 filters={'candidate_status': 'blocked'}" in output
    assert "blocked-row\top-blocked\tblocked\ttext_replace\tsection:2" in output
    assert "candidate-row" not in output


def test_nz_effect_candidates_cli_parse_defaults() -> None:
    parser = _build_parser()

    args = parser.parse_args(["nz-corpus", "effect-candidates", "--work-id", "act_public_1957_87"])

    assert args.command == "nz-corpus"
    assert args.nz_corpus_command == "effect-candidates"
    assert args.db == "data/nz_legislation.farchive"
    assert args.work_id == "act_public_1957_87"
    assert args.limit == 40
    assert args.candidate_status == ""
    assert args.action == ""
    assert args.operation_family == ""
    assert args.blocking_rule == ""
    assert args.instruction_subfamily_status == ""
    assert args.instruction_subfamily == ""
    assert args.payload_structural_subfamily_status == ""
    assert args.payload_structural_subfamily == ""
    assert args.repeal_payload_corroboration_status == ""
    assert args.operation_lowering_readiness_status == ""
    assert args.operation_target_address_status == ""
    assert args.operation_dependency_status == ""
    assert args.payload_instruction_shape == ""
    assert args.payload_instruction_safety == ""
    assert args.instruction_semantic_candidate_status == ""
    assert args.latest_oracle_text_status == ""
    assert args.text_replace_witness_support_status == ""
    assert args.source_change_text_witness_status == ""
    assert args.evidence_rows is False
    assert args.evidence_jsonl is None

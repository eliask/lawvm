from __future__ import annotations

import argparse
import json

from lawvm.core.evidence_contracts import validate_corpus_operation_evidence_row
from lawvm.new_zealand.effect_readiness import build_effect_readiness_surface
from lawvm.new_zealand import instruction_workqueue
from lawvm.new_zealand.instruction_workqueue import (
    NZInstructionWorkQueueReport,
    NZInstructionWorkQueueRow,
    build_instruction_workqueue,
    write_evidence_jsonl,
)
from lawvm.new_zealand.operation_surface import build_operation_surface
from lawvm.new_zealand.payload_surface import build_payload_surface
from lawvm.new_zealand.source_tree import parse_nz_source_document
from lawvm.tools.cli import _build_parser
from lawvm.tools.report_query import load_report_query_records


def test_build_instruction_workqueue_lists_candidate_direct_rows_without_replay_claim() -> None:
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
        work_id="act_public_2020_1",
        archived_dependency_work_ids=frozenset({"act_public_2025_4"}),
    )
    payload_surface = build_payload_surface(
        operation_surface,
        dependency_documents={"act_public_2025_4": parse_nz_source_document(amendment_xml)},
    )
    effect_readiness = build_effect_readiness_surface(operation_surface, payload_surface)

    report = build_instruction_workqueue(operation_surface, payload_surface, effect_readiness)

    assert report.summary()["candidate_instruction_rows"] == 1
    assert report.summary()["blocked_instruction_rows"] == 0
    assert report.summary()["payload_instruction_shape_counts"] == {
        "direct_amended_by_instruction": 1,
    }
    assert report.summary()["replay_claims"] is False
    assert report.to_jsonable(row_limit=10, queue_status="candidate")["canonical_effect_claims"] is False
    assert len(report.to_jsonable(row_limit=10, instruction_family="amend_instruction")["rows"]) == 1
    assert len(report.to_jsonable(row_limit=10, instruction_shape="direct_insert_instruction")["rows"]) == 0
    filtered_payload = report.to_jsonable(row_limit=10, instruction_family="amend_instruction")
    assert filtered_payload["summary"]["rows"] == 1
    assert filtered_payload["filters"] == {"instruction_family": "amend_instruction"}
    assert filtered_payload["filtered_summary"]["rows"] == 1
    assert filtered_payload["filtered_summary"]["instruction_semantic_candidate_family_counts"] == {
        "amend_instruction": 1,
    }
    empty_payload = report.to_jsonable(summary_only=True, instruction_shape="direct_insert_instruction")
    assert empty_payload["filters"] == {"instruction_shape": "direct_insert_instruction"}
    assert empty_payload["filtered_summary"]["rows"] == 0
    assert "rows" not in empty_payload
    row = report.rows[0]
    assert row.queue_status == "candidate"
    assert row.operation_family == "amended"
    assert row.target_address == "section:1"
    assert row.effect_readiness_status == "blocked_text_or_structural_amendment_semantics_not_extracted"
    assert row.blocking_rule_id == "nz_effect_readiness_amendment_semantics_not_extracted"
    assert row.amending_work_id == "act_public_2025_4"
    assert row.amending_provision_hrefs == ("A3",)
    assert row.instruction_semantic_candidate_family == "amend_instruction"
    assert row.instruction_semantic_rule_id == "nz_instruction_semantics_candidate_direct_instruction"
    assert row.payload_instruction_shape == "direct_amended_by_instruction"
    assert row.payload_match_headings == ("Amend",)
    assert row.payload_text_snippets == ("Section 1 is amended by replacing Old with New.",)
    evidence_row = report.operation_evidence_rows()[0].to_dict()
    assert evidence_row["status"] == "unsupported"
    assert evidence_row["blocking"] is True
    assert evidence_row["detail"]["queue_status"] == "candidate"
    assert evidence_row["detail"]["replay_claims"] is False
    assert validate_corpus_operation_evidence_row(evidence_row) == ()


def test_build_instruction_workqueue_keeps_repeal_as_not_required() -> None:
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

    report = build_instruction_workqueue(operation_surface, payload_surface)

    assert report.summary()["not_required_rows"] == 1
    assert report.rows[0].queue_status == "not_required"
    assert report.rows[0].instruction_semantic_candidate_family == "repeal_without_enacted_payload"
    evidence_row = report.operation_evidence_rows()[0].to_dict()
    assert evidence_row["status"] == "skipped"
    assert evidence_row["blocking"] is False
    assert evidence_row["detail"]["reason"] == "repeal candidate is owned by effect-candidates surface"
    assert validate_corpus_operation_evidence_row(evidence_row) == ()


def test_build_instruction_workqueue_splits_retrospective_notes_into_review_bucket() -> None:
    target_xml = b"""\
<act>
  <body>
    <prov id="S1"><label>1</label><heading>Target</heading>
      <notes>
        <history-note id="HN1">
          <amended-provision>Section 1</amended-provision>
          <amending-operation>inserted</amending-operation>
          <amending-provision href="A3">section 3</amending-provision>
          <amending-leg>Example Amendment Act 2025</amending-leg>
          Section 1: inserted by section 3 of the Example Amendment Act 2025 (2025 No 4).
        </history-note>
      </notes>
    </prov>
  </body>
</act>
"""
    amendment_xml = b"""\
<act>
  <body>
    <prov id="A3"><label>3</label><heading>Insertion summary</heading>
      <prov.body><para><text>This section inserted section 1 of the principal Act.</text></para></prov.body>
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

    report = build_instruction_workqueue(operation_surface, payload_surface)

    assert report.summary()["candidate_instruction_rows"] == 0
    assert report.summary()["review_instruction_rows"] == 1
    assert report.rows[0].queue_status == "review"
    assert report.rows[0].instruction_semantic_candidate_status == "review_retrospective_incorporated_note"
    assert report.rows[0].instruction_semantic_rule_id == "nz_instruction_semantics_review_retrospective_incorporated_note"
    assert report.rows[0].payload_instruction_safety == "review_retrospective_incorporated_note"
    assert report.rows[0].payload_structural_subfamily_status == "review_retrospective_incorporated_payload"
    assert report.rows[0].payload_structural_subfamily == "retrospective_incorporated_note"
    assert (
        report.rows[0].payload_structural_subfamily_rule_id
        == "nz_instruction_structural_subfamily_retrospective_incorporated_note_review"
    )


def test_build_instruction_workqueue_classifies_structural_insert_payload_without_lowering() -> None:
    target_xml = b"""\
<act>
  <body>
    <prov id="S2"><label>2</label><heading>Interpretation</heading>
      <notes>
        <history-note id="HN1">
          <amended-provision>Section 2(1)</amended-provision>
          <amending-operation>inserted</amending-operation>
          <amending-provision href="A44">section 44</amending-provision>
          <amending-leg>Example Amendment Act 2025</amending-leg>
          Section 2(1) term: inserted by section 44 of the Example Amendment Act 2025 (2025 No 4).
        </history-note>
      </notes>
    </prov>
  </body>
</act>
"""
    amendment_xml = b"""\
<act>
  <body>
    <prov id="A44"><label>44</label><heading>Section 2 amended</heading>
      <prov.body><para><text>In section 2(1), insert in its appropriate alphabetical order: term means a thing.</text></para></prov.body>
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

    report = build_instruction_workqueue(operation_surface, payload_surface)
    row = report.rows[0]
    evidence_row = report.operation_evidence_rows()[0].to_dict()

    assert row.queue_status == "candidate"
    assert row.instruction_subfamily_status == "not_text_substitution_shape"
    assert row.instruction_subfamily_rule_id == "nz_instruction_subfamily_not_text_substitution_shape"
    assert row.payload_structural_subfamily_status == "blocked_definition_alphabetical_insert_payload_not_lowered"
    assert row.payload_structural_subfamily == "definition_alphabetical_insert_payload"
    assert (
        row.payload_structural_subfamily_rule_id
        == "nz_instruction_structural_subfamily_definition_alphabetical_insert_payload_blocked"
    )
    assert report.summary()["payload_structural_subfamily_status_counts"] == {
        "blocked_definition_alphabetical_insert_payload_not_lowered": 1,
    }
    assert (
        report.to_jsonable(
            row_limit=10,
            payload_structural_subfamily_status="blocked_definition_alphabetical_insert_payload_not_lowered",
        )["rows"][0]["row_id"]
        == row.row_id
    )
    assert (
        report.filtered_rows(payload_structural_subfamily="definition_alphabetical_insert_payload")[0].row_id
        == row.row_id
    )
    assert evidence_row["status"] == "unsupported"
    assert evidence_row["blocking"] is True
    assert (
        evidence_row["detail"]["payload_structural_subfamily_rule_id"]
        == "nz_instruction_structural_subfamily_definition_alphabetical_insert_payload_blocked"
    )


def test_build_instruction_workqueue_classifies_direct_amended_insert_payload_by_operation_family() -> None:
    target_xml = b"""\
<act>
  <body>
    <prov id="S2"><label>2</label><heading>Interpretation</heading>
      <subprov id="S2-1"><label>1</label><para><text>Definitions.</text></para></subprov>
      <notes>
        <history-note id="HN1">
          <amended-provision>Section 2(1)</amended-provision>
          <amending-operation>inserted</amending-operation>
          <amending-provision href="A44">section 44</amending-provision>
          <amending-leg>Example Amendment Act 2025</amending-leg>
          Section 2(1): inserted by section 44 of the Example Amendment Act 2025 (2025 No 4).
        </history-note>
      </notes>
    </prov>
  </body>
</act>
"""
    amendment_xml = b"""\
<act>
  <body>
    <prov id="A44"><label>44</label><heading>Section 2 amended</heading>
      <prov.body><para><text>Section is amended by inserting in the definition of 2(1) infringement notice, after paragraph (ba), the following paragraph: (bb) section 99; or.</text></para></prov.body>
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

    report = build_instruction_workqueue(operation_surface, payload_surface)
    row = report.rows[0]

    assert row.operation_family == "inserted"
    assert row.payload_instruction_shape == "direct_amended_by_instruction"
    assert row.instruction_subfamily_status == "not_text_substitution_shape"
    assert row.instruction_subfamily_rule_id == "nz_instruction_subfamily_not_text_substitution_shape"
    assert row.payload_structural_subfamily_status == "blocked_paragraph_after_insert_payload_not_lowered"
    assert row.payload_structural_subfamily == "paragraph_after_insert_payload"
    assert (
        row.payload_structural_subfamily_rule_id
        == "nz_instruction_structural_subfamily_paragraph_after_insert_payload_blocked"
    )


def test_build_instruction_workqueue_classifies_cross_heading_insert_payload() -> None:
    target_xml = b"""\
<act>
  <body>
    <part id="P2"><label>2</label><heading>Existing heading</heading>
      <notes>
        <history-note id="HN1">
          <amended-provision>Heading</amended-provision>
          <amending-operation>inserted</amending-operation>
          <amending-provision href="A3">section 3</amending-provision>
          <amending-leg>Example Amendment Act 2025</amending-leg>
          Heading: inserted by section 3 of the Example Amendment Act 2025 (2025 No 4).
        </history-note>
      </notes>
    </part>
  </body>
</act>
"""
    amendment_xml = b"""\
<act>
  <body>
    <prov id="A3"><label>3</label><heading>Cross-heading inserted</heading>
      <prov.body><para><text>After, insert: section 29 Use of language.</text></para></prov.body>
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

    report = build_instruction_workqueue(operation_surface, payload_surface)
    row = report.rows[0]

    assert row.target_address == "part:2/heading"
    assert row.payload_structural_subfamily_status == "blocked_cross_heading_insert_payload_not_lowered"
    assert row.payload_structural_subfamily == "cross_heading_insert_payload"
    assert (
        row.payload_structural_subfamily_rule_id
        == "nz_instruction_structural_subfamily_cross_heading_insert_payload_blocked"
    )


def test_build_instruction_workqueue_classifies_section_after_insert_payload_with_blank_anchor() -> None:
    target_xml = b"""\
<act>
  <body>
    <prov id="S21B"><label>21B</label><heading>New section</heading>
      <notes>
        <history-note id="HN1">
          <amended-provision>Section 21B</amended-provision>
          <amending-operation>inserted</amending-operation>
          <amending-provision href="A3">section 3</amending-provision>
          <amending-leg>Example Amendment Act 2025</amending-leg>
          Section 21B: inserted by section 3 of the Example Amendment Act 2025 (2025 No 4).
        </history-note>
      </notes>
    </prov>
  </body>
</act>
"""
    amendment_xml = b"""\
<act>
  <body>
    <prov id="A3"><label>3</label><heading>New section 21B inserted</heading>
      <prov.body><para><text>After, insert: section 21A 21B Requirements for notices.</text></para></prov.body>
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

    report = build_instruction_workqueue(operation_surface, payload_surface)
    row = report.rows[0]

    assert row.target_address == "section:21B"
    assert row.payload_structural_subfamily_status == "blocked_section_after_insert_payload_not_lowered"
    assert row.payload_structural_subfamily == "section_after_insert_payload"
    assert (
        row.payload_structural_subfamily_rule_id
        == "nz_instruction_structural_subfamily_section_after_insert_payload_blocked"
    )


def test_build_instruction_workqueue_classifies_reversed_section_after_insert_payload() -> None:
    target_xml = b"""\
<act>
  <body>
    <prov id="S209A"><label>209A</label><heading>New section</heading>
      <notes>
        <history-note id="HN1">
          <amended-provision>Section 209A</amended-provision>
          <amending-operation>inserted</amending-operation>
          <amending-provision href="A3">section 3</amending-provision>
          <amending-leg>Example Amendment Act 2025</amending-leg>
          Section 209A: inserted by section 3 of the Example Amendment Act 2025 (2025 No 4).
        </history-note>
      </notes>
    </prov>
  </body>
</act>
"""
    amendment_xml = b"""\
<act>
  <body>
    <prov id="A3"><label>3</label><heading>New section 209A inserted</heading>
      <prov.body><para><text>The following section is inserted after: section 209 209A Chief executive may approve forms.</text></para></prov.body>
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

    report = build_instruction_workqueue(operation_surface, payload_surface)
    row = report.rows[0]

    assert row.target_address == "section:209A"
    assert row.payload_structural_subfamily_status == "blocked_section_after_insert_payload_not_lowered"
    assert row.payload_structural_subfamily == "section_after_insert_payload"
    assert (
        row.payload_structural_subfamily_rule_id
        == "nz_instruction_structural_subfamily_section_after_insert_payload_blocked"
    )


def test_build_instruction_workqueue_classifies_historical_inserted_note_payload() -> None:
    target_xml = b"""\
<act>
  <body>
    <prov id="S21"><label>21</label><heading>Target</heading>
      <subprov id="S21-3A"><label>3A</label><para><text>Inserted text.</text></para></subprov>
      <notes>
        <history-note id="HN1">
          <amended-provision>Section 21(3A)</amended-provision>
          <amending-operation>inserted</amending-operation>
          <amending-provision href="A3">section 3</amending-provision>
          <amending-leg>Example Amendment Act 2025</amending-leg>
          Section 21(3A): inserted by section 3 of the Example Amendment Act 2025 (2025 No 4).
        </history-note>
      </notes>
    </prov>
  </body>
</act>
"""
    amendment_xml = b"""\
<act>
  <body>
    <prov id="A3"><label>3</label><heading>Historical note</heading>
      <prov.body><para><text>This subsection inserted s . of the principal Act 21(3A) to (3D)</text></para></prov.body>
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

    report = build_instruction_workqueue(operation_surface, payload_surface)
    row = report.rows[0]

    assert row.target_address == "section:21/subsection:3A"
    assert row.payload_structural_subfamily_status == "blocked_historical_inserted_note_payload_not_lowered"
    assert row.payload_structural_subfamily == "historical_inserted_note_payload"
    assert (
        row.payload_structural_subfamily_rule_id
        == "nz_instruction_structural_subfamily_historical_inserted_note_payload_blocked"
    )


def test_build_instruction_workqueue_classifies_mixed_text_and_structural_insert_payload() -> None:
    target_xml = b"""\
<act>
  <body>
    <prov id="S21"><label>21</label><heading>Target</heading>
      <subprov id="S21-2AA"><label>2AA</label><para><text>Inserted text.</text></para></subprov>
      <notes>
        <history-note id="HN1">
          <amended-provision>Section 21(2AA)</amended-provision>
          <amending-operation>inserted</amending-operation>
          <amending-provision href="A3">section 3</amending-provision>
          <amending-leg>Example Amendment Act 2025</amending-leg>
          Section 21(2AA): inserted by section 3 of the Example Amendment Act 2025 (2025 No 4).
        </history-note>
      </notes>
    </prov>
  </body>
</act>
"""
    amendment_xml = b"""\
<act>
  <body>
    <prov id="A3"><label>3</label><heading>Section 21 amended</heading>
      <prov.body><para><text>1 Section is amended by omitting 21(2) old words and substituting new words. 2 Section is amended by inserting the following subsection after 21 subsection: 2AA New text.</text></para></prov.body>
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

    report = build_instruction_workqueue(operation_surface, payload_surface)
    row = report.rows[0]

    assert row.target_address == "section:21/subsection:2AA"
    assert row.payload_structural_subfamily_status == "blocked_mixed_text_and_structural_insert_payload_not_lowered"
    assert row.payload_structural_subfamily == "mixed_text_and_structural_insert_payload"
    assert (
        row.payload_structural_subfamily_rule_id
        == "nz_instruction_structural_subfamily_mixed_text_and_structural_insert_payload_blocked"
    )


def test_build_instruction_workqueue_classifies_direct_text_insert_payload() -> None:
    target_xml = b"""\
<act>
  <body>
    <prov id="S79"><label>79</label><heading>Interpretation</heading>
      <subprov id="S79-1"><label>1</label><para><text>Definition text.</text></para></subprov>
      <notes>
        <history-note id="HN1">
          <amended-provision>Section 79(1)</amended-provision>
          <amending-operation>amended</amending-operation>
          <amending-provision href="A3">section 3</amending-provision>
          <amending-leg>Example Amendment Act 2025</amending-leg>
          Section 79(1): amended by section 3 of the Example Amendment Act 2025 (2025 No 4).
        </history-note>
      </notes>
    </prov>
  </body>
</act>
"""
    amendment_xml = b"""\
<act>
  <body>
    <prov id="A3"><label>3</label><heading>Section 79 amended</heading>
      <prov.body><para><text>In section 79(1), definition of traffic offence, paragraph (a), after the Transport Act 1962, insert the Road User Charges Act 1977.</text></para></prov.body>
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

    report = build_instruction_workqueue(operation_surface, payload_surface)
    row = report.rows[0]

    assert row.target_address == "section:79/subsection:1"
    assert row.payload_structural_subfamily_status == "blocked_text_insert_payload_not_lowered"
    assert row.payload_structural_subfamily == "direct_text_insert_payload"
    assert (
        row.payload_structural_subfamily_rule_id
        == "nz_instruction_structural_subfamily_direct_text_insert_payload_blocked"
    )


def test_build_instruction_workqueue_classifies_direct_amended_text_insert_payload() -> None:
    target_xml = b"""\
<act>
  <body>
    <prov id="S80"><label>80</label><heading>Payment</heading>
      <notes>
        <history-note id="HN1">
          <amended-provision>Section 80</amended-provision>
          <amending-operation>amended</amending-operation>
          <amending-provision href="A3">section 3</amending-provision>
          <amending-leg>Example Amendment Act 2025</amending-leg>
          Section 80: amended by section 3 of the Example Amendment Act 2025 (2025 No 4).
        </history-note>
      </notes>
    </prov>
  </body>
</act>
"""
    amendment_xml = b"""\
<act>
  <body>
    <prov id="A3"><label>3</label><heading>Section 80 amended</heading>
      <prov.body><para><text>Section 80 is amended by inserting or the Sentencing Act 2002 after this Act.</text></para></prov.body>
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

    report = build_instruction_workqueue(operation_surface, payload_surface)
    row = report.rows[0]

    assert row.payload_instruction_shape == "direct_amended_by_instruction"
    assert row.payload_structural_subfamily_status == "blocked_text_insert_payload_not_lowered"
    assert row.payload_structural_subfamily == "direct_text_insert_payload"
    assert (
        row.payload_structural_subfamily_rule_id
        == "nz_instruction_structural_subfamily_direct_text_insert_payload_blocked"
    )


def test_build_instruction_workqueue_classifies_incorporated_amendment_stub_payload() -> None:
    target_xml = b"""\
<act>
  <body>
    <prov id="S142"><label>142</label><heading>Target</heading>
      <subprov id="S142-2A"><label>2A</label><para><text>Inserted text.</text></para></subprov>
      <notes>
        <history-note id="HN1">
          <amended-provision>Section 142(2A)</amended-provision>
          <amending-operation>inserted</amending-operation>
          <amending-provision href="A3">section 3</amending-provision>
          <amending-leg>Example Amendment Act 2025</amending-leg>
          Section 142(2A): inserted by section 3 of the Example Amendment Act 2025 (2025 No 4).
        </history-note>
      </notes>
    </prov>
  </body>
</act>
"""
    amendment_xml = b"""\
<act>
  <body>
    <prov id="A3"><label>3</label><heading>Target amended</heading>
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

    report = build_instruction_workqueue(operation_surface, payload_surface)
    row = report.rows[0]

    assert row.payload_instruction_shape == "other_instruction"
    assert row.payload_structural_subfamily_status == "blocked_incorporated_amendment_stub_payload"
    assert row.payload_structural_subfamily == "incorporated_amendment_stub_payload"
    assert (
        row.payload_structural_subfamily_rule_id
        == "nz_instruction_structural_subfamily_incorporated_amendment_stub_payload_blocked"
    )


def test_build_instruction_workqueue_classifies_mixed_repeal_substitute_payload() -> None:
    target_xml = b"""\
<act>
  <body>
    <prov id="S2"><label>2</label><heading>Interpretation</heading>
      <subprov id="S2-1"><label>1</label><para><text>Definitions.</text></para></subprov>
      <notes>
        <history-note id="HN1">
          <amended-provision>Section 2(1)</amended-provision>
          <amending-operation>replaced</amending-operation>
          <amending-provision href="A44">section 44</amending-provision>
          <amending-leg>Example Amendment Act 2025</amending-leg>
          Section 2(1): replaced by section 44 of the Example Amendment Act 2025 (2025 No 4).
        </history-note>
      </notes>
    </prov>
  </body>
</act>
"""
    amendment_xml = b"""\
<act>
  <body>
    <prov id="A44"><label>44</label><heading>Section 2 amended</heading>
      <prov.body><para><text>Section is amended by repealing 2(1) paragraphs (f) to (g) of the definition of infringement notice and substituting the following paragraphs: f section 66; or g section 139.</text></para></prov.body>
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

    report = build_instruction_workqueue(operation_surface, payload_surface)
    row = report.rows[0]

    assert row.operation_family == "replaced"
    assert row.payload_structural_subfamily_status == "blocked_mixed_repeal_substitute_payload_not_lowered"
    assert row.payload_structural_subfamily == "mixed_repeal_substitute_payload"
    assert (
        row.payload_structural_subfamily_rule_id
        == "nz_instruction_structural_subfamily_mixed_repeal_substitute_payload_blocked"
    )


def test_build_instruction_workqueue_classifies_multi_section_replace_payload() -> None:
    target_xml = b"""\
<act>
  <body>
    <prov id="S23A"><label>23A</label><heading>Old section</heading>
      <notes>
        <history-note id="HN1">
          <amended-provision>Section 23A</amended-provision>
          <amending-operation>replaced</amending-operation>
          <amending-provision href="A3">section 3</amending-provision>
          <amending-leg>Example Amendment Act 2025</amending-leg>
          Section 23A: replaced by section 3 of the Example Amendment Act 2025 (2025 No 4).
        </history-note>
      </notes>
    </prov>
  </body>
</act>
"""
    amendment_xml = b"""\
<act>
  <body>
    <prov id="A3"><label>3</label><heading>Sections 23A and 24 replaced</heading>
      <prov.body><para><text>Replace with: sections 23A and 24 23A Service of documents. 24 Method of service.</text></para></prov.body>
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

    report = build_instruction_workqueue(operation_surface, payload_surface)
    row = report.rows[0]

    assert row.payload_instruction_shape == "direct_substitute_replace_instruction"
    assert row.payload_structural_subfamily_status == "blocked_multi_section_replace_payload_not_lowered"
    assert row.payload_structural_subfamily == "multi_section_replace_payload"
    assert (
        row.payload_structural_subfamily_rule_id
        == "nz_instruction_structural_subfamily_multi_section_replace_payload_blocked"
    )


def test_build_instruction_workqueue_classifies_whole_provision_substitution_payload() -> None:
    target_xml = b"""\
<act>
  <body>
    <prov id="S81"><label>81</label><heading>Old section</heading>
      <notes>
        <history-note id="HN1">
          <amended-provision>Section 81</amended-provision>
          <amending-operation>replaced</amending-operation>
          <amending-provision href="A3">section 3</amending-provision>
          <amending-leg>Example Amendment Act 2025</amending-leg>
          Section 81: replaced by section 3 of the Example Amendment Act 2025 (2025 No 4).
        </history-note>
      </notes>
    </prov>
  </body>
</act>
"""
    amendment_xml = b"""\
<act>
  <body>
    <prov id="A3"><label>3</label><heading>New section 81 substituted</heading>
      <prov.body><para><text>Section 81 is repealed and the following section substituted: 81 Time to pay.</text></para></prov.body>
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

    report = build_instruction_workqueue(operation_surface, payload_surface)
    row = report.rows[0]

    assert row.payload_instruction_shape == "direct_substitute_replace_instruction"
    assert row.payload_structural_subfamily_status == "blocked_whole_provision_substitution_payload_not_lowered"
    assert row.payload_structural_subfamily == "whole_provision_substitution_payload"
    assert (
        row.payload_structural_subfamily_rule_id
        == "nz_instruction_structural_subfamily_whole_provision_substitution_payload_blocked"
    )


def test_build_instruction_workqueue_classifies_direct_single_text_substitution_candidate() -> None:
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

    report = build_instruction_workqueue(operation_surface, payload_surface)
    row = report.rows[0]

    assert report.summary()["direct_single_text_substitution_candidates"] == 1
    assert report.summary()["instruction_subfamily_status_counts"] == {
        "candidate_direct_single_text_substitution": 1,
    }
    assert report.summary()["target_citation_status_counts"] == {"matched": 1}
    assert report.summary()["text_substitution_scope_counts"] == {"inline_text_single_occurrence": 1}
    assert row.instruction_subfamily_status == "candidate_direct_single_text_substitution"
    assert row.instruction_subfamily == "direct_single_text_substitution"
    assert row.instruction_subfamily_rule_id == "nz_instruction_semantics_direct_single_text_substitution_candidate"
    assert row.payload_structural_subfamily_status == ""
    assert row.payload_structural_subfamily == ""
    assert row.payload_structural_subfamily_rule_id == ""
    assert row.instruction_clause_count == 1
    assert row.explicit_target_citation == "section 21"
    assert row.target_citation_status == "matched"
    assert row.old_text == "old words"
    assert row.new_text == "new words"
    assert row.text_substitution_scope == "inline_text_single_occurrence"
    filtered = report.to_jsonable(row_limit=10, instruction_subfamily="direct_single_text_substitution")
    assert len(filtered["rows"]) == 1
    assert (
        report.to_jsonable(
            row_limit=10,
            payload_structural_subfamily_status="blocked_structural_insert_payload_not_lowered",
        )["rows"]
        == []
    )
    evidence_row = report.operation_evidence_rows()[0].to_dict()
    assert evidence_row["detail"]["instruction_subfamily"] == "direct_single_text_substitution"
    assert evidence_row["detail"]["old_text"] == "old words"


def test_build_instruction_workqueue_records_latest_oracle_text_witness_for_text_substitution() -> None:
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

    report = build_instruction_workqueue(operation_surface, payload_surface, target_document=target_document)
    row = report.rows[0]
    evidence_row = report.operation_evidence_rows()[0].to_dict()

    assert row.instruction_subfamily == "direct_single_text_substitution"
    assert row.latest_oracle_text_status == "oracle_new_text_only"
    assert row.latest_oracle_text_rule_id == "nz_instruction_latest_oracle_text_oracle_new_text_only"
    assert row.latest_oracle_target_resolution_status == "exact_source_path"
    assert row.latest_oracle_target_resolution_rule_id == "nz_instruction_latest_oracle_target_exact_source_path"
    assert row.latest_oracle_target_source_path == ("prov:21",)
    assert row.latest_oracle_old_text_occurrences == 0
    assert row.latest_oracle_new_text_occurrences == 1
    assert report.summary()["latest_oracle_text_status_counts"] == {"oracle_new_text_only": 1}
    assert evidence_row["detail"]["latest_oracle_text_status"] == "oracle_new_text_only"
    assert evidence_row["detail"]["latest_oracle_new_text_occurrences"] == 1
    assert evidence_row["detail"]["latest_oracle_target_resolution"] == {
        "rule_id": "nz_instruction_latest_oracle_target_exact_source_path",
        "phase": "oracle",
        "blocking": False,
        "strict_disposition": "record",
        "quirks_disposition": "record",
        "family": "target_resolution",
        "reason": "latest oracle source node resolved for instruction text witness",
        "target_resolution_status": "resolved",
        "source_target": "section:21",
        "candidate_count": 1,
        "selected_target": "prov:21",
        "selected_target_differs_from_source": True,
        "scope_confidence": "explicit_source",
        "jurisdiction_status": "exact_source_path",
        "source_path": ("prov:21",),
    }


def test_build_instruction_workqueue_records_latest_oracle_text_when_new_contains_old_text() -> None:
    target_xml = b"""\
<act>
  <body>
    <prov id="S21"><label>21</label><heading>Target</heading>
      <prov.body><para><text>The Registrar or the chief executive may act.</text></para></prov.body>
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
      <prov.body><para><text>In section 21, replace Registrar with Registrar or the chief executive.</text></para></prov.body>
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

    report = build_instruction_workqueue(operation_surface, payload_surface, target_document=target_document)

    assert report.rows[0].latest_oracle_text_status == "oracle_new_text_contains_old_text"
    assert report.rows[0].latest_oracle_old_text_occurrences == 1
    assert report.rows[0].latest_oracle_new_text_occurrences == 1


def test_build_instruction_workqueue_parses_full_payload_text_not_truncated_snippet() -> None:
    old_text = " ".join(f"old{i}" for i in range(80))
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
    amendment_xml = f"""\
<act>
  <body>
    <prov id="A3"><label>3</label><heading>Text replacement</heading>
      <prov.body><para><text>In section 21, replace {old_text} with new words.</text></para></prov.body>
    </prov>
  </body>
</act>
""".encode()
    operation_surface = build_operation_surface(
        parse_nz_source_document(target_xml),
        work_id="act_public_2020_1",
        archived_dependency_work_ids=frozenset({"act_public_2025_4"}),
    )
    payload_surface = build_payload_surface(
        operation_surface,
        dependency_documents={"act_public_2025_4": parse_nz_source_document(amendment_xml)},
    )

    report = build_instruction_workqueue(operation_surface, payload_surface)
    row = report.rows[0]

    assert row.instruction_subfamily_status == "candidate_direct_single_text_substitution"
    assert row.old_text == old_text
    assert row.new_text == "new words"
    assert row.payload_text_snippets[0].endswith("...")
    assert len(row.payload_text_snippets[0]) < len(row.old_text)


def test_build_instruction_workqueue_blocks_latest_oracle_text_when_target_granularity_is_not_indexed() -> None:
    target_xml = b"""\
<act>
  <body>
    <prov id="S21"><label>21</label><heading>Target</heading>
      <subprov id="S21-1"><label>1</label><para><text>Paragraph text has new words.</text></para></subprov>
      <notes>
        <history-note id="HN1">
          <amended-provision>Section 21(1)(a)</amended-provision>
          <amending-operation>amended</amending-operation>
          <amending-provision href="A3">section 3</amending-provision>
          <amending-leg>Example Amendment Act 2025</amending-leg>
          Section 21(1)(a): amended by section 3 of the Example Amendment Act 2025 (2025 No 4).
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
      <prov.body><para><text>In section 21(1)(a), replace old words with new words.</text></para></prov.body>
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

    report = build_instruction_workqueue(operation_surface, payload_surface, target_document=target_document)

    assert report.rows[0].latest_oracle_text_status == "blocked_target_granularity_not_indexed"
    assert (
        report.rows[0].latest_oracle_text_rule_id
        == "nz_instruction_latest_oracle_text_target_granularity_not_indexed"
    )
    assert report.rows[0].latest_oracle_target_source_path == ("prov:21", "subprov:1")


def test_build_instruction_workqueue_resolves_latest_oracle_text_at_label_para_granularity() -> None:
    target_xml = b"""\
<act>
  <body>
    <prov id="S21"><label>21</label><heading>Target</heading>
      <subprov id="S21-1"><label>1</label>
        <para><text>Intro:</text>
          <label-para><label>a</label><para><text>The paragraph contains new words.</text></para></label-para>
        </para>
      </subprov>
      <notes>
        <history-note id="HN1">
          <amended-provision>Section 21(1)(a)</amended-provision>
          <amending-operation>amended</amending-operation>
          <amending-provision href="A3">section 3</amending-provision>
          <amending-leg>Example Amendment Act 2025</amending-leg>
          Section 21(1)(a): amended by section 3 of the Example Amendment Act 2025 (2025 No 4).
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
      <prov.body><para><text>In section 21(1)(a), replace old words with new words.</text></para></prov.body>
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

    report = build_instruction_workqueue(operation_surface, payload_surface, target_document=target_document)

    assert report.rows[0].latest_oracle_text_status == "oracle_new_text_only"
    assert report.rows[0].latest_oracle_target_resolution_status == "exact_source_path"
    assert report.rows[0].latest_oracle_target_source_path == ("prov:21", "subprov:1", "label-para:a")
    assert report.rows[0].latest_oracle_new_text_occurrences == 1


def test_build_instruction_workqueue_resolves_latest_oracle_text_through_unlabeled_source_carrier() -> None:
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

    report = build_instruction_workqueue(operation_surface, payload_surface, target_document=target_document)

    assert report.rows[0].latest_oracle_text_status == "oracle_new_text_only"
    assert report.rows[0].latest_oracle_target_resolution_status == "via_unlabeled_source_carrier"
    assert (
        report.rows[0].latest_oracle_target_resolution_rule_id
        == "nz_instruction_latest_oracle_target_via_unlabeled_source_carrier"
    )
    assert report.rows[0].latest_oracle_target_source_path == ("prov:21", "subprov#2", "label-para:a")
    evidence_row = report.operation_evidence_rows()[0].to_dict()
    assert evidence_row["detail"]["latest_oracle_target_resolution"]["target_resolution_status"] == "recovered"
    assert (
        evidence_row["detail"]["latest_oracle_target_resolution"]["jurisdiction_status"]
        == "via_unlabeled_source_carrier"
    )
    assert (
        evidence_row["detail"]["latest_oracle_target_resolution"]["selected_target"]
        == "prov:21/subprov#2/label-para:a"
    )


def test_build_instruction_workqueue_latest_oracle_text_ignores_presentation_punctuation_spacing() -> None:
    target_xml = b"""\
<act>
  <body>
    <prov id="S21"><label>21</label><heading>Target</heading>
      <prov.body><para><text>The provision mentions sections 86A , 86C , 86D , and 86DA.</text></para></prov.body>
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
      <prov.body><para><text>In section 21, replace sections 86A, 86C, and 86D with sections 86A, 86C, 86D, and 86DA.</text></para></prov.body>
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

    report = build_instruction_workqueue(operation_surface, payload_surface, target_document=target_document)

    assert report.rows[0].latest_oracle_text_status == "oracle_new_text_only"
    assert report.rows[0].latest_oracle_new_text_occurrences == 1


def test_build_instruction_workqueue_blocks_multi_clause_text_substitution_subfamily() -> None:
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
    <prov id="A3"><label>3</label><heading>Text replacements</heading>
      <prov.body>
        <para><text>In section 21, replace old words with new words.</text></para>
        <para><text>In section 22, replace stale words with fresh words.</text></para>
      </prov.body>
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

    report = build_instruction_workqueue(operation_surface, payload_surface)

    assert report.summary()["direct_single_text_substitution_candidates"] == 0
    assert report.rows[0].instruction_subfamily_status == "blocked_multi_clause_payload"
    assert report.rows[0].instruction_subfamily_rule_id == "nz_instruction_semantics_blocked_multi_clause_payload"
    assert report.rows[0].instruction_clause_count == 2


def test_build_instruction_workqueue_extracts_matching_numbered_multi_clause_text_substitution() -> None:
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
    <prov id="A3"><label>3</label><heading>Text replacements</heading>
      <prov.body>
        <para><text>1 In section 21, replace old words with new words. 2 In section 22, replace stale words with fresh words.</text></para>
      </prov.body>
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

    report = build_instruction_workqueue(operation_surface, payload_surface)
    row = report.rows[0]

    assert report.summary()["direct_single_text_substitution_candidates"] == 1
    assert row.instruction_subfamily_status == "candidate_direct_multi_clause_text_substitution"
    assert row.instruction_subfamily == "direct_single_text_substitution"
    assert row.instruction_subfamily_rule_id == (
        "nz_instruction_semantics_direct_multi_clause_text_substitution_candidate"
    )
    assert row.instruction_clause_count == 2
    assert row.explicit_target_citation == "section 21"
    assert row.target_citation_status == "matched_in_multi_clause_payload"
    assert row.old_text == "old words"
    assert row.new_text == "new words"


def test_build_instruction_workqueue_extracts_matching_numbered_multi_clause_with_empty_in_carrier() -> None:
    target_xml = b"""\
<act>
  <body>
    <prov id="S21"><label>21</label><heading>Target</heading>
      <subprov id="S21-2"><label>2</label><para><text>Target text.</text></para>
        <label-para><label>b</label><para><text>Paragraph text.</text></para>
          <label-para><label>i</label><para><text>Nested paragraph text.</text></para></label-para>
        </label-para>
      </subprov>
      <notes>
        <history-note id="HN1">
          <amended-provision>Section 21(2)(b)(i)</amended-provision>
          <amending-operation>amended</amending-operation>
          <amending-provision href="A3">section 3</amending-provision>
          <amending-leg>Example Amendment Act 2025</amending-leg>
          Section 21(2)(b)(i): amended by section 3 of the Example Amendment Act 2025 (2025 No 4).
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
        <para><text>1 In , replace section 21(2)(b)(i) at the address with by a payment method. 2 In , replace section 21(2)(b)(ii) received at that address with received at the notice address.</text></para>
      </prov.body>
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

    report = build_instruction_workqueue(operation_surface, payload_surface)
    row = report.rows[0]

    assert row.instruction_subfamily_status == "candidate_direct_multi_clause_text_substitution"
    assert row.explicit_target_citation == "section 21(2)(b)(i)"
    assert row.old_text == "at the address"
    assert row.new_text == "by a payment method"


def test_build_instruction_workqueue_does_not_absorb_numbered_structural_replace_clause() -> None:
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
    <prov id="A3"><label>3</label><heading>Mixed replacements</heading>
      <prov.body>
        <para><text>1 In section 21, replace old words with new words. 2 Replace with: section 22 New structural text.</text></para>
      </prov.body>
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

    report = build_instruction_workqueue(operation_surface, payload_surface)
    row = report.rows[0]

    assert row.instruction_subfamily_status == "candidate_direct_multi_clause_text_substitution"
    assert row.old_text == "old words"
    assert row.new_text == "new words"
    assert "Replace with:" not in row.new_text


def test_build_instruction_workqueue_blocks_numbered_multi_clause_without_matching_target() -> None:
    target_xml = b"""\
<act>
  <body>
    <prov id="S23"><label>23</label><heading>Target</heading>
      <notes>
        <history-note id="HN1">
          <amended-provision>Section 23</amended-provision>
          <amending-operation>amended</amending-operation>
          <amending-provision href="A3">section 3</amending-provision>
          <amending-leg>Example Amendment Act 2025</amending-leg>
          Section 23: amended by section 3 of the Example Amendment Act 2025 (2025 No 4).
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
    operation_surface = build_operation_surface(
        parse_nz_source_document(target_xml),
        work_id="act_public_2020_1",
        archived_dependency_work_ids=frozenset({"act_public_2025_4"}),
    )
    payload_surface = build_payload_surface(
        operation_surface,
        dependency_documents={"act_public_2025_4": parse_nz_source_document(amendment_xml)},
    )

    report = build_instruction_workqueue(operation_surface, payload_surface)
    row = report.rows[0]

    assert report.summary()["direct_single_text_substitution_candidates"] == 0
    assert row.instruction_subfamily_status == "blocked_multi_clause_no_matching_target"
    assert row.instruction_subfamily_rule_id == "nz_instruction_semantics_blocked_multi_clause_no_matching_target"
    assert row.target_citation_status == "no_match"
    assert row.instruction_clause_count == 2


def test_build_instruction_workqueue_blocks_ambiguous_numbered_multi_clause_same_target() -> None:
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
    <prov id="A3"><label>3</label><heading>Text replacements</heading>
      <prov.body>
        <para><text>1 In section 21, replace old words with new words. 2 In section 21, replace stale words with fresh words.</text></para>
      </prov.body>
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

    report = build_instruction_workqueue(operation_surface, payload_surface)
    row = report.rows[0]

    assert report.summary()["direct_single_text_substitution_candidates"] == 0
    assert row.instruction_subfamily_status == "blocked_multi_clause_target_ambiguous"
    assert row.instruction_subfamily_rule_id == "nz_instruction_semantics_blocked_multi_clause_target_ambiguous"
    assert row.target_citation_status == "ambiguous"
    assert row.instruction_clause_count == 2


def test_build_instruction_workqueue_blocks_text_substitution_target_mismatch() -> None:
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
      <prov.body><para><text>In section 22, replace old words with new words.</text></para></prov.body>
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

    report = build_instruction_workqueue(operation_surface, payload_surface)

    assert report.summary()["direct_single_text_substitution_candidates"] == 0
    assert report.rows[0].instruction_subfamily_status == "blocked_target_citation_mismatch"
    assert report.rows[0].instruction_subfamily_rule_id == "nz_instruction_semantics_blocked_target_citation_mismatch"
    assert report.rows[0].explicit_target_citation == "section 22"
    assert report.rows[0].target_citation_status == "mismatch"


def test_build_instruction_workqueue_classifies_each_place_text_substitution_candidate() -> None:
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
      <prov.body><para><text>In section 21, replace solicitor with lawyer in each place.</text></para></prov.body>
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

    report = build_instruction_workqueue(operation_surface, payload_surface)

    assert report.summary()["direct_single_text_substitution_candidates"] == 0
    assert report.summary()["direct_each_place_text_substitution_candidates"] == 1
    assert report.rows[0].instruction_subfamily_status == "candidate_direct_each_place_text_substitution"
    assert report.rows[0].instruction_subfamily == "direct_each_place_text_substitution"
    assert report.summary()["target_citation_status_counts"] == {"matched": 1}
    assert report.summary()["text_substitution_scope_counts"] == {"inline_text_each_place": 1}
    assert (
        report.rows[0].instruction_subfamily_rule_id
        == "nz_instruction_semantics_direct_each_place_text_substitution_candidate"
    )
    assert report.rows[0].old_text == "solicitor"
    assert report.rows[0].new_text == "lawyer"
    assert report.rows[0].text_substitution_scope == "inline_text_each_place"


def test_build_instruction_workqueue_classifies_omitting_substituting_text_substitution_candidate() -> None:
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

    report = build_instruction_workqueue(operation_surface, payload_surface, target_document=target_document)
    row = report.rows[0]

    assert row.instruction_subfamily_status == "candidate_direct_omitting_substituting_text_substitution"
    assert row.instruction_subfamily == "direct_single_text_substitution"
    assert row.instruction_subfamily_rule_id == (
        "nz_instruction_semantics_direct_omitting_substituting_text_substitution_candidate"
    )
    assert row.explicit_target_citation == "section 21(2)"
    assert row.target_citation_status == "matched"
    assert row.old_text == "in the prescribed form containing"
    assert row.new_text == "that contains"
    assert row.latest_oracle_text_status == "oracle_new_text_only"


def test_build_instruction_workqueue_blocks_omitting_substituting_each_place() -> None:
    target_xml = b"""\
<act>
  <body>
    <prov id="S21"><label>21</label><heading>Target</heading>
      <subprov id="S21-3A"><label>3A</label><para><text>Target text.</text></para></subprov>
      <notes>
        <history-note id="HN1">
          <amended-provision>Section 21(3A)</amended-provision>
          <amending-operation>amended</amending-operation>
          <amending-provision href="A3">section 3</amending-provision>
          <amending-leg>Example Amendment Act 2025</amending-leg>
          Section 21(3A): amended by section 3 of the Example Amendment Act 2025 (2025 No 4).
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
      <prov.body><para><text>is amended by omitting Section 21(3A) the defendant to pay the infringement fee in each place where it appears and substituting in each case the infringement fee to be paid.</text></para></prov.body>
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

    report = build_instruction_workqueue(operation_surface, payload_surface)
    row = report.rows[0]

    assert row.instruction_subfamily_status == "candidate_direct_each_place_omitting_substituting_text_substitution"
    assert row.instruction_subfamily == "direct_each_place_text_substitution"
    assert (
        row.instruction_subfamily_rule_id
        == "nz_instruction_semantics_direct_each_place_omitting_substituting_text_substitution_candidate"
    )
    assert row.old_text == "the defendant to pay the infringement fee"
    assert row.new_text == "the infringement fee to be paid"
    assert row.text_substitution_scope == "inline_text_each_place"


def test_build_instruction_workqueue_extracts_matching_multi_clause_omitting_substituting() -> None:
    target_xml = b"""\
<act>
  <body>
    <prov id="S21"><label>21</label><heading>Target</heading>
      <subprov id="S21-1"><label>1</label><para><text>New first text.</text></para>
        <label-para><label>b</label><para><text>providing particulars of a reminder notice in accordance with requirements.</text></para></label-para>
      </subprov>
      <notes>
        <history-note id="HN1">
          <amended-provision>Section 21(1)(b)</amended-provision>
          <amending-operation>amended</amending-operation>
          <amending-provision href="A3">section 3</amending-provision>
          <amending-leg>Example Amendment Act 2025</amending-leg>
          Section 21(1)(b): amended by section 3 of the Example Amendment Act 2025 (2025 No 4).
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
      <prov.body><para><text>1 Section is amended by omitting 21(1)(b) filing in a Court a copy of a reminder notice and substituting providing particulars of a reminder notice in accordance with requirements. 2 Section is amended by omitting 21(2) old words and substituting new words.</text></para></prov.body>
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

    report = build_instruction_workqueue(operation_surface, payload_surface, target_document=target_document)
    row = report.rows[0]

    assert row.instruction_subfamily_status == "candidate_direct_multi_clause_omitting_substituting_text_substitution"
    assert row.instruction_subfamily_rule_id == (
        "nz_instruction_semantics_direct_multi_clause_omitting_substituting_text_substitution_candidate"
    )
    assert row.explicit_target_citation == "section 21(1)(b)"
    assert row.old_text == "filing in a Court a copy of a reminder notice"
    assert row.new_text == "providing particulars of a reminder notice in accordance with requirements"
    assert row.latest_oracle_text_status == "oracle_new_text_only"


def test_build_instruction_workqueue_blocks_structural_omitting_substituting_payload() -> None:
    target_xml = b"""\
<act>
  <body>
    <prov id="S2"><label>2</label><heading>Target</heading>
      <subprov id="S2-1"><label>1</label><para><text>Target text.</text></para></subprov>
      <notes>
        <history-note id="HN1">
          <amended-provision>Section 2(1)</amended-provision>
          <amending-operation>amended</amending-operation>
          <amending-provision href="A3">section 3</amending-provision>
          <amending-leg>Example Amendment Act 2025</amending-leg>
          Section 2(1): amended by section 3 of the Example Amendment Act 2025 (2025 No 4).
        </history-note>
      </notes>
    </prov>
  </body>
</act>
"""
    amendment_xml = b"""\
<act>
  <body>
    <prov id="A3"><label>3</label><heading>Structural substitution</heading>
      <prov.body><para><text>Section is amended by omitting 2(1) paragraphs (f) to (g) of the definition of infringement notice and substituting the following paragraphs: f section 66; or g section 139.</text></para></prov.body>
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

    report = build_instruction_workqueue(operation_surface, payload_surface)
    row = report.rows[0]

    assert row.instruction_subfamily_status == "blocked_structural_omitting_substituting_payload"
    assert row.instruction_subfamily_rule_id == "nz_instruction_semantics_blocked_structural_omitting_substituting_payload"
    assert row.explicit_target_citation == "section 2(1)"


def test_build_instruction_workqueue_blocks_flattened_structural_tail_after_omitting_substituting() -> None:
    target_xml = b"""\
<act>
  <body>
    <prov id="S78B"><label>78B</label><heading>Target</heading>
      <subprov id="S78B-1"><label>1</label>
        <label-para><label>a</label><para><text>Target text.</text></para></label-para>
      </subprov>
      <notes>
        <history-note id="HN1">
          <amended-provision>Section 78B(1)(a)</amended-provision>
          <amending-operation>amended</amending-operation>
          <amending-provision href="A3">section 3</amending-provision>
          <amending-leg>Example Amendment Act 2025</amending-leg>
          Section 78B(1)(a): amended by section 3 of the Example Amendment Act 2025 (2025 No 4).
        </history-note>
      </notes>
    </prov>
  </body>
</act>
"""
    amendment_xml = b"""\
<act>
  <body>
    <prov id="A3"><label>3</label><heading>Flattened structural payload</heading>
      <prov.body><para><text>1 is amended by omitting Section 78B(1)(a) old text and substituting new text. 2 is amended by repealing subparagraph (ii) and substituting the following subparagraphs.</text></para></prov.body>
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

    report = build_instruction_workqueue(operation_surface, payload_surface)
    row = report.rows[0]

    assert row.instruction_subfamily_status == "blocked_structural_omitting_substituting_payload"
    assert row.instruction_subfamily_rule_id == "nz_instruction_semantics_blocked_structural_omitting_substituting_payload"
    assert row.explicit_target_citation == "section 78B(1)(a)"
    assert "2 is amended" in row.new_text


def test_write_instruction_workqueue_evidence_jsonl_is_report_query_compatible(tmp_path) -> None:
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
        work_id="act_public_2020_1",
        archived_dependency_work_ids=frozenset({"act_public_2025_4"}),
    )
    payload_surface = build_payload_surface(
        operation_surface,
        dependency_documents={"act_public_2025_4": parse_nz_source_document(amendment_xml)},
    )
    report = build_instruction_workqueue(operation_surface, payload_surface)
    path = tmp_path / "instruction_workqueue.jsonl"

    count = write_evidence_jsonl(report, path)
    records = load_report_query_records((path,), validate=True)

    assert count == 1
    assert len(records) == 1
    assert records[0].validation_issues == ()
    assert records[0].evidence_row["detail"]["instruction_semantic_candidate_family"] == "amend_instruction"


def test_nz_instruction_workqueue_cli_evidence_rows_respect_filters(monkeypatch, capsys, tmp_path) -> None:
    report = NZInstructionWorkQueueReport(
        work_id="act_public_2020_1",
        rows=(
            NZInstructionWorkQueueRow(
                row_id="candidate-row",
                operation_row_id="op-candidate",
                effect_readiness_row_id="ready-candidate",
                queue_status="candidate",
                operation_family="amended",
                target_address="section:1",
                effect_readiness_status="blocked_text_or_structural_amendment_semantics_not_extracted",
                blocking_rule_id="nz_effect_readiness_amendment_semantics_not_extracted",
                amending_work_id="act_public_2025_4",
                amending_provision_hrefs=("A3",),
                instruction_semantic_candidate_status="candidate_only_instruction_semantics",
                instruction_semantic_candidate_family="amend_instruction",
                instruction_semantic_rule_id="nz_instruction_semantics_candidate_direct_instruction",
                payload_instruction_shape="direct_amended_by_instruction",
                payload_instruction_safety="candidate_only_semantic_classification",
                payload_match_headings=("Amend",),
                payload_text_snippets=("Section 1 is amended by replacing Old with New.",),
            ),
            NZInstructionWorkQueueRow(
                row_id="blocked-row",
                operation_row_id="op-blocked",
                effect_readiness_row_id="ready-blocked",
                queue_status="blocked",
                operation_family="amended",
                target_address="section:2",
                effect_readiness_status="blocked_payload_witness_not_available",
                blocking_rule_id="nz_effect_readiness_payload_witness_not_available",
                amending_work_id="act_public_2025_5",
                amending_provision_hrefs=("A4",),
                instruction_semantic_candidate_status="blocked_payload_witness_not_available",
                instruction_semantic_candidate_family="",
                instruction_semantic_rule_id="nz_instruction_semantics_payload_witness_not_available",
                payload_instruction_shape="",
                payload_instruction_safety="",
                payload_match_headings=(),
                payload_text_snippets=(),
            ),
        ),
    )
    monkeypatch.setattr(instruction_workqueue, "build_archived_work_instruction_workqueue", lambda _db, _work_id: report)
    evidence_path = tmp_path / "filtered_instruction_workqueue.jsonl"
    args = argparse.Namespace(
        db="unused.farchive",
        work_id="act_public_2020_1",
        limit=10,
        summary_only=False,
        queue_status="blocked",
        instruction_family="",
        instruction_shape="",
        instruction_subfamily_status="",
        instruction_subfamily="",
        payload_structural_subfamily_status="",
        payload_structural_subfamily="",
        candidate_only=False,
        evidence_rows=True,
        evidence_jsonl=str(evidence_path),
        json=True,
    )

    instruction_workqueue.main(args)
    payload = json.loads(capsys.readouterr().out)
    written_rows = [json.loads(line) for line in evidence_path.read_text(encoding="utf-8").splitlines()]

    assert [row["row_id"] for row in payload["rows"]] == ["blocked-row"]
    assert payload["summary"]["rows"] == 2
    assert payload["filters"] == {"queue_status": "blocked"}
    assert payload["filtered_summary"]["rows"] == 1
    assert payload["filtered_summary"]["queue_status_counts"] == {"blocked": 1}
    assert [row["row_id"] for row in payload["evidence"]["operation_rows"]] == ["blocked-row"]
    assert payload["evidence"]["operation_rows"][0]["detail"]["queue_status"] == "blocked"
    assert payload["evidence_jsonl"]["rows"] == 1
    assert [row["row_id"] for row in written_rows] == ["blocked-row"]


def test_nz_instruction_workqueue_text_cli_prints_filtered_context(monkeypatch, capsys) -> None:
    report = NZInstructionWorkQueueReport(
        work_id="act_public_2020_1",
        rows=(
            NZInstructionWorkQueueRow(
                row_id="candidate-row",
                operation_row_id="op-candidate",
                effect_readiness_row_id="ready-candidate",
                queue_status="candidate",
                operation_family="amended",
                target_address="section:1",
                effect_readiness_status="blocked_text_or_structural_amendment_semantics_not_extracted",
                blocking_rule_id="nz_effect_readiness_amendment_semantics_not_extracted",
                amending_work_id="act_public_2025_4",
                amending_provision_hrefs=("A3",),
                instruction_semantic_candidate_status="candidate_only_instruction_semantics",
                instruction_semantic_candidate_family="amend_instruction",
                instruction_semantic_rule_id="nz_instruction_semantics_candidate_direct_instruction",
                payload_instruction_shape="direct_amended_by_instruction",
                payload_instruction_safety="candidate_only_semantic_classification",
                payload_match_headings=("Amend",),
                payload_text_snippets=("Section 1 is amended by replacing Old with New.",),
            ),
            NZInstructionWorkQueueRow(
                row_id="blocked-row",
                operation_row_id="op-blocked",
                effect_readiness_row_id="ready-blocked",
                queue_status="blocked",
                operation_family="amended",
                target_address="section:2",
                effect_readiness_status="blocked_payload_witness_not_available",
                blocking_rule_id="nz_effect_readiness_payload_witness_not_available",
                amending_work_id="act_public_2025_5",
                amending_provision_hrefs=("A4",),
                instruction_semantic_candidate_status="blocked_payload_witness_not_available",
                instruction_semantic_candidate_family="",
                instruction_semantic_rule_id="nz_instruction_semantics_payload_witness_not_available",
                payload_instruction_shape="",
                payload_instruction_safety="",
                payload_match_headings=(),
                payload_text_snippets=(),
            ),
        ),
    )
    monkeypatch.setattr(instruction_workqueue, "build_archived_work_instruction_workqueue", lambda _db, _work_id: report)
    args = argparse.Namespace(
        db="unused.farchive",
        work_id="act_public_2020_1",
        limit=10,
        summary_only=False,
        queue_status="blocked",
        instruction_family="",
        instruction_shape="",
        instruction_subfamily_status="",
        instruction_subfamily="",
        payload_structural_subfamily_status="",
        payload_structural_subfamily="",
        candidate_only=False,
        evidence_rows=False,
        evidence_jsonl=None,
        json=False,
    )

    instruction_workqueue.main(args)
    output = capsys.readouterr().out

    assert "rows=2 filtered_rows=1 filters={'queue_status': 'blocked'}" in output
    assert "blocked-row\tblocked\t-" in output
    assert "candidate-row" not in output


def test_nz_instruction_workqueue_cli_parse_defaults() -> None:
    parser = _build_parser()

    args = parser.parse_args(["nz-corpus", "instruction-workqueue", "--work-id", "act_public_1957_87"])

    assert args.command == "nz-corpus"
    assert args.nz_corpus_command == "instruction-workqueue"
    assert args.db == "data/nz_legislation.farchive"
    assert args.work_id == "act_public_1957_87"
    assert args.limit == 40
    assert args.summary_only is False
    assert args.queue_status == ""
    assert args.instruction_family == ""
    assert args.instruction_shape == ""
    assert args.instruction_subfamily_status == ""
    assert args.instruction_subfamily == ""
    assert args.payload_structural_subfamily_status == ""
    assert args.payload_structural_subfamily == ""
    assert args.candidate_only is False
    assert args.evidence_rows is False
    assert args.evidence_jsonl is None

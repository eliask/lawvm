from __future__ import annotations

from lawvm.core.evidence_contracts import (
    validate_corpus_finding_evidence_row,
    validate_corpus_operation_evidence_row,
)
from lawvm.new_zealand.operation_surface import build_operation_surface, classify_operation_family, parse_target_hint
from lawvm.new_zealand.operation_surface import write_evidence_jsonl
from lawvm.new_zealand.source_tree import parse_nz_source_document
from lawvm.tools.report_query import load_report_query_records
from lawvm.tools.cli import _build_parser


def test_classify_operation_family_normalizes_known_and_unclassified_values() -> None:
    assert classify_operation_family(" editorial changes ") == "editorial change"
    assert classify_operation_family("Sections 1 and 2 brought into force") == "brought into force"
    assert classify_operation_family("repealed") == "repealed"
    assert classify_operation_family("") == "__missing__"
    assert classify_operation_family("full sentence that is not an operation") == "__unclassified__"


def test_parse_target_hint_extracts_bounded_structural_hints() -> None:
    assert parse_target_hint("Section 12(3)").to_jsonable() == {
        "status": "parsed",
        "kind": "section",
        "label": "12",
        "subsection": "3",
        "paragraphs": [],
        "facet": "",
        "raw": "Section 12(3)",
    }
    assert parse_target_hint("Section 78B(1)\ufeff(a)\ufeff(viii)").to_jsonable()["paragraphs"] == ["a", "viii"]
    assert parse_target_hint("Section 1 heading").to_jsonable()["facet"] == "heading"
    assert parse_target_hint("Schedule 2").to_jsonable()["kind"] == "schedule"
    assert parse_target_hint("Heading").to_jsonable()["status"] == "attached_facet"
    assert parse_target_hint("Title").to_jsonable()["status"] == "document_facet"
    assert parse_target_hint("").to_jsonable()["status"] == "missing"
    assert parse_target_hint("Section 2(1) and (2)").to_jsonable() == {
        "status": "compound_target_unparsed",
        "kind": "section",
        "label": "2",
        "subsection": "1",
        "paragraphs": [],
        "facet": "",
        "raw": "Section 2(1) and (2)",
    }
    assert parse_target_hint("Sections 1 and 2").to_jsonable()["status"] == "compound_target_unparsed"


def test_build_operation_surface_extracts_history_witness_rows() -> None:
    xml = b"""\
<act>
  <cover><title>Example Act 2020</title></cover>
  <end>
    <notes>
      <history-note id="HN-doc">
        <amended-provision>Title</amended-provision>
        <amending-operation>repealed</amending-operation>
        <amendment-date>1 January 2025</amendment-date>
        <amending-provision>section 2</amending-provision>
        <amending-leg>Example Amendment Act 2025</amending-leg>
        Title: repealed, on 1 January 2025, by section 2 of the Example Amendment Act 2025 (2025 No 4).
      </history-note>
    </notes>
  </end>
  <body>
    <prov id="S1"><label>1</label><heading>Title</heading>
      <notes>
        <history-note id="HN-prov">
          <amended-provision>Section 1</amended-provision>
          <amending-operation>amended</amending-operation>
        <amendment-date>1 January 2025</amendment-date>
        <amending-provision href="amend-3">section 3</amending-provision>
        <amending-leg>Example Amendment Act 2025</amending-leg>
        Section 1: amended, on 1 January 2025, by section 3 of the Example Amendment Act 2025 (2025 No 4).
      </history-note>
      </notes>
    </prov>
  </body>
</act>
"""

    report = build_operation_surface(
        parse_nz_source_document(xml, xml_locator="xml", version_id="version"),
        work_id="act_public_2020_1",
        archived_dependency_work_ids=frozenset({"act_public_2025_4"}),
    )

    assert report.to_jsonable()["replay_claims"] is False
    assert report.summary()["rows"] == 2
    assert report.summary()["operation_status_counts"] == {"classified": 2}
    assert report.summary()["operation_family_counts"] == {"amended": 1, "repealed": 1}
    assert report.summary()["target_surface_status_counts"] == {
        "attached_structural_node": 1,
        "document_level_facet": 1,
    }
    assert report.summary()["target_hint_status_counts"] == {"document_facet": 1, "parsed": 1}
    assert report.summary()["target_hint_kind_counts"] == {"document": 1, "section": 1}
    assert report.summary()["target_address_status_counts"] == {
        "blocked_document_level_facet": 1,
        "candidate": 1,
    }
    assert report.summary()["dependency_status_counts"] == {"amending_work_resolved_archived": 2}
    assert report.summary()["amending_provision_href_status_counts"] == {
        "missing": 1,
        "present": 1,
    }
    assert report.summary()["lowering_readiness_status_counts"] == {
        "blocked_non_structural_facet": 1,
        "ready_for_amending_act_payload_extraction": 1,
    }
    assert report.summary()["effect_lowering_status"] == "blocked"
    assert "rows" not in report.to_jsonable(summary_only=True)
    limited_json = report.to_jsonable(row_limit=1)
    assert len(limited_json["rows"]) == 1
    assert limited_json["rows_truncated"] is True
    assert limited_json["rows_omitted"] == 1
    evidence_json = report.to_jsonable(row_limit=1, include_evidence_rows=True)
    assert len(evidence_json["evidence"]["operation_rows"]) == 1
    assert [row["rule_id"] for row in evidence_json["evidence"]["finding_rows"]] == [
        "nz_target_address_document_level_facet",
        "nz_lowering_readiness_blocked_non_structural_facet",
    ]
    evidence_rows = report.operation_evidence_rows()
    assert len(evidence_rows) == 2
    assert all(validate_corpus_operation_evidence_row(row.to_dict()) == () for row in evidence_rows)
    assert evidence_rows[1].to_dict()["status"] == "unsupported"
    assert evidence_rows[1].to_dict()["finding_ids"] == ()
    assert evidence_rows[1].to_dict()["detail"]["lowering_readiness_status"] == (
        "ready_for_amending_act_payload_extraction"
    )
    assert report.rows[0].source_path == ("document",)
    assert report.rows[0].source_zone == "end_history"
    assert report.rows[0].target_surface_status == "document_level_facet"
    assert report.rows[0].amending_work_id == "act_public_2025_4"
    assert report.rows[1].source_path == ("prov:1",)
    assert report.rows[1].source_zone == "primary_body"
    assert report.rows[1].attached_node_xml_id == "S1"
    assert report.rows[1].target_hint.kind == "section"
    assert report.rows[1].target_hint.label == "1"
    assert report.rows[1].target_address_candidate.address == "section:1"
    assert report.rows[1].target_address_candidate.status == "candidate"
    assert report.rows[1].amending_provision_hrefs == ("amend-3",)
    assert report.rows[1].lowering_readiness_status == "ready_for_amending_act_payload_extraction"


def test_operation_surface_json_filters_rows_and_related_evidence() -> None:
    xml = b"""\
<act>
  <end>
    <notes>
      <history-note id="HN-doc">
        <amended-provision>Title</amended-provision>
        <amending-operation>repealed</amending-operation>
        <amending-leg>Example Amendment Act 2025</amending-leg>
        Title: repealed, on 1 January 2025, by section 2 of the Example Amendment Act 2025 (2025 No 4).
      </history-note>
    </notes>
  </end>
  <body>
    <prov id="S1"><label>1</label><heading>Target</heading>
      <notes>
        <history-note id="HN-prov">
          <amended-provision>Section 1</amended-provision>
          <amending-operation>amended</amending-operation>
          <amending-provision href="amend-3">section 3</amending-provision>
          <amending-leg>Example Amendment Act 2025</amending-leg>
          Section 1: amended, on 1 January 2025, by section 3 of the Example Amendment Act 2025 (2025 No 4).
        </history-note>
      </notes>
    </prov>
  </body>
</act>
"""
    report = build_operation_surface(
        parse_nz_source_document(xml),
        archived_dependency_work_ids=frozenset({"act_public_2025_4"}),
    )

    payload = report.to_jsonable(
        row_limit=10,
        include_evidence_rows=True,
        target_address_status="blocked_document_level_facet",
    )

    assert payload["summary"]["rows"] == 2
    assert payload["filters"] == {"target_address_status": "blocked_document_level_facet"}
    assert payload["filtered_summary"]["rows"] == 1
    assert payload["filtered_summary"]["findings"] == 2
    assert payload["filtered_summary"]["target_address_status_counts"] == {"blocked_document_level_facet": 1}
    assert len(payload["rows"]) == 1
    assert payload["rows"][0]["target_address_candidate"]["status"] == "blocked_document_level_facet"
    assert [finding["rule_id"] for finding in payload["findings"]] == [
        "nz_target_address_document_level_facet",
        "nz_lowering_readiness_blocked_non_structural_facet",
    ]
    assert len(payload["evidence"]["operation_rows"]) == 1
    assert [row["rule_id"] for row in payload["evidence"]["finding_rows"]] == [
        "nz_target_address_document_level_facet",
        "nz_lowering_readiness_blocked_non_structural_facet",
    ]
    assert report.filtered_rows(operation_family="amended")[0].target_address_candidate.address == "section:1"


def test_write_evidence_jsonl_emits_report_query_compatible_rows(tmp_path) -> None:
    xml = b"""\
<act>
  <body>
    <prov id="S1"><label>1</label><heading>Title</heading>
      <notes>
        <history-note id="HN-missing">Section 1: changed without structured operation.</history-note>
      </notes>
    </prov>
  </body>
</act>
"""
    report = build_operation_surface(parse_nz_source_document(xml, version_id="version"), work_id="act_public_2020_1")
    path = tmp_path / "nz_operation_evidence.jsonl"

    count = write_evidence_jsonl(report, path)
    records = load_report_query_records((path,), validate=True)

    assert count == 4
    assert len(records) == 4
    assert all(record.validation_issues == () for record in records)
    assert {record.row_kind for record in records} == {"finding", "operation"}


def test_build_operation_surface_emits_findings_for_missing_or_unclassified_operations() -> None:
    xml = b"""\
<act>
  <body>
    <prov id="S1"><label>1</label><heading>Title</heading>
      <notes>
        <history-note id="HN-missing">Section 1: changed without structured operation.</history-note>
        <history-note id="HN-unclassified"><amending-operation>bespoke operation prose</amending-operation></history-note>
      </notes>
    </prov>
  </body>
</act>
"""

    report = build_operation_surface(parse_nz_source_document(xml))

    assert report.summary()["operation_status_counts"] == {"missing": 1, "unclassified": 1}
    assert report.summary()["target_hint_status_counts"] == {"missing": 2}
    assert report.summary()["target_address_status_counts"] == {"blocked_target_hint_missing": 2}
    assert report.summary()["lowering_readiness_status_counts"] == {
        "blocked_operation_missing": 1,
        "blocked_operation_unclassified": 1,
    }
    assert [finding["rule_id"] for finding in report.findings] == [
        "nz_operation_surface_missing",
        "nz_target_address_hint_missing",
        "nz_lowering_readiness_blocked_operation_missing",
        "nz_operation_surface_unclassified",
        "nz_target_address_hint_missing",
        "nz_lowering_readiness_blocked_operation_unclassified",
    ]
    assert all(finding["blocking"] is True for finding in report.findings)
    finding_rows = report.finding_evidence_rows()
    assert len(finding_rows) == 6
    assert all(validate_corpus_finding_evidence_row(row.to_dict()) == () for row in finding_rows)
    assert report.operation_evidence_rows()[0].to_dict()["finding_ids"] == (
        "nz-opw-1:nz_operation_surface_missing",
        "nz-opw-1:nz_target_address_hint_missing",
        "nz-opw-1:nz_lowering_readiness_blocked_operation_missing",
    )


def test_build_operation_surface_records_duplicate_target_and_unarchived_dependency() -> None:
    xml = b"""\
<act>
  <body>
    <prov id="S1"><label>1</label><heading>First duplicate</heading>
      <notes>
        <history-note id="HN1">
          <amended-provision>Section 1</amended-provision>
          <amending-operation>amended</amending-operation>
          Section 1: amended by section 3 of the Example Amendment Act 2025 (2025 No 4).
        </history-note>
      </notes>
    </prov>
    <prov id="S1B"><label>1</label><heading>Second duplicate</heading></prov>
  </body>
</act>
"""

    report = build_operation_surface(parse_nz_source_document(xml), archived_dependency_work_ids=frozenset())

    assert report.rows[0].target_surface_status == "duplicate_source_path"
    assert report.rows[0].target_address_candidate.status == "blocked_duplicate_source_path"
    assert report.rows[0].dependency_status == "amending_work_resolved_unarchived"
    assert report.rows[0].lowering_readiness_status == "blocked_amending_work_resolved_unarchived"
    assert [finding["rule_id"] for finding in report.findings] == [
        "nz_target_address_duplicate_source_path",
        "nz_history_note_dependency_unarchived",
        "nz_lowering_readiness_blocked_amending_work_resolved_unarchived",
    ]
    assert report.findings[0]["target_resolution"]["target_resolution_status"] == "rejected"
    assert report.findings[0]["target_resolution"]["source_target"] == "Section 1"
    assert report.findings[0]["target_resolution"]["jurisdiction_status"] == "blocked_duplicate_source_path"
    assert report.findings[0]["target_resolution"]["strict_disposition"] == "block"
    assert report.findings[1]["blocking"] is False


def test_build_operation_surface_classifies_compound_target_without_resolving() -> None:
    xml = b"""\
<act>
  <body>
    <prov id="S2"><label>2</label><heading>Target</heading>
      <notes>
        <history-note id="HN1">
          <amended-provision>Section 2(1) and (2)</amended-provision>
          <amending-operation>amended</amending-operation>
          <amending-provision href="A3">section 3</amending-provision>
          <amending-leg>Example Amendment Act 2025</amending-leg>
          Section 2(1) and (2): amended by section 3 of the Example Amendment Act 2025 (2025 No 4).
        </history-note>
      </notes>
    </prov>
  </body>
</act>
"""

    report = build_operation_surface(
        parse_nz_source_document(xml),
        archived_dependency_work_ids=frozenset({"act_public_2025_4"}),
    )

    assert report.summary()["target_hint_status_counts"] == {"compound_target_unparsed": 1}
    assert report.summary()["target_address_status_counts"] == {
        "blocked_target_hint_compound_target_unparsed": 1,
    }
    assert report.rows[0].target_hint.kind == "section"
    assert report.rows[0].target_hint.label == "2"
    assert report.rows[0].target_hint.subsection == "1"
    assert report.rows[0].target_address_candidate.address == ""
    assert report.rows[0].target_address_candidate.blocking_rule_id == (
        "nz_target_address_hint_compound_target_unparsed"
    )
    assert report.rows[0].lowering_readiness_status == "blocked_target_hint_compound_target_unparsed"
    assert [finding["rule_id"] for finding in report.findings] == [
        "nz_target_address_hint_compound_target_unparsed",
        "nz_lowering_readiness_blocked_target_hint_compound_target_unparsed",
    ]
    assert all(finding["blocking"] is True for finding in report.findings)


def test_build_operation_surface_classifies_same_label_rebirth_duplicate_without_resolving() -> None:
    xml = b"""\
<act>
  <schedule.group>
    <schedule id="SCH1-old" deletion-status="repealed"><label>1</label><heading>Old schedule</heading>
      <notes>
        <history-note id="HN1">
          <amended-provision>Schedule 1</amended-provision>
          <amending-operation>repealed</amending-operation>
          Schedule 1: repealed by section 3 of the Example Amendment Act 2025 (2025 No 4).
        </history-note>
      </notes>
    </schedule>
    <schedule id="SCH1-new"><label>1</label><heading>New schedule</heading></schedule>
  </schedule.group>
</act>
"""

    report = build_operation_surface(
        parse_nz_source_document(xml),
        archived_dependency_work_ids=frozenset({"act_public_2025_4"}),
    )

    assert report.rows[0].target_surface_status == "same_label_rebirth_duplicate"
    assert report.rows[0].target_address_candidate.status == "blocked_same_label_rebirth_duplicate"
    assert report.rows[0].lowering_readiness_status == "blocked_same_label_rebirth_duplicate"
    assert [finding["rule_id"] for finding in report.findings] == [
        "nz_target_address_same_label_rebirth_duplicate",
        "nz_lowering_readiness_blocked_same_label_rebirth_duplicate",
    ]
    assert all(finding["blocking"] is True for finding in report.findings)


def test_build_operation_surface_resolves_end_skeleton_duplicates_with_named_observation() -> None:
    xml = b"""\
<act>
  <body>
    <prov id="S1"><label>1</label><heading>Live title</heading>
      <notes>
        <history-note id="HN1">
          <amended-provision>Section 1</amended-provision>
          <amending-operation>repealed</amending-operation>
          Section 1: repealed by section 3 of the Example Amendment Act 2025 (2025 No 4).
        </history-note>
      </notes>
    </prov>
  </body>
  <end>
    <skeletons>
      <skeleton.act>
        <skeleton.act.body>
          <prov id="SK1"><label>1</label><heading>Historical title</heading></prov>
        </skeleton.act.body>
      </skeleton.act>
    </skeletons>
  </end>
</act>
"""

    report = build_operation_surface(
        parse_nz_source_document(xml),
        archived_dependency_work_ids=frozenset({"act_public_2025_4"}),
    )

    assert report.summary()["target_surface_status_counts"] == {"skeleton_duplicate_resolved": 1}
    assert report.summary()["target_address_status_counts"] == {"candidate": 1}
    assert report.summary()["lowering_readiness_status_counts"] == {
        "ready_for_amending_act_payload_extraction": 1,
    }
    assert report.rows[0].target_surface_status == "skeleton_duplicate_resolved"
    assert report.rows[0].target_address_candidate.address == "section:1"
    assert [finding["rule_id"] for finding in report.findings] == [
        "nz_target_address_skeleton_duplicate_resolved",
    ]
    assert report.findings[0]["blocking"] is False
    assert report.findings[0]["target_resolution"] == {
        "rule_id": "nz_target_address_skeleton_duplicate_resolved",
        "family": "target_resolution",
        "phase": "P6",
        "reason": "source path duplicate is caused by non-current end skeleton nodes; primary node target kept",
        "blocking": False,
        "strict_disposition": "warn",
        "quirks_disposition": "warn",
        "target_resolution_status": "recovered",
        "source_target": "Section 1",
        "candidate_count": 1,
        "target_candidates": (
            {
                "target": "section:1",
                "reason": "primary_non_skeleton_source_node",
                "source_path": ("prov:1",),
                "target_address_status": "candidate",
            },
        ),
        "selected_target": "section:1",
        "selected_target_differs_from_source": True,
        "scope_confidence": "explicit_source",
        "jurisdiction_status": "candidate",
        "target_surface_status": "skeleton_duplicate_resolved",
        "target_hint_status": "parsed",
        "target_hint_kind": "section",
        "source_path": ("prov:1",),
        "source_xml_path": "/act/body/prov/notes/history-note",
    }
    assert report.operation_evidence_rows()[0].to_dict()["finding_ids"] == (
        "nz-opw-1:nz_target_address_skeleton_duplicate_resolved",
    )


def test_build_operation_surface_does_not_recover_skeleton_duplicate_without_address() -> None:
    xml = b"""\
<act>
  <body>
    <part id="P2"><label>2</label><heading>Live part</heading>
      <prov id="S22"><label>22</label><heading>Live section</heading>
        <notes>
          <history-note id="HN1">
            <amended-provision>Section 22(1) first proviso</amended-provision>
            <amending-operation>repealed</amending-operation>
            Section 22(1) first proviso: repealed by section 3 of the Example Amendment Act 2025 (2025 No 4).
          </history-note>
        </notes>
      </prov>
    </part>
  </body>
  <end>
    <skeletons>
      <skeleton.act>
        <skeleton.act.body>
          <part id="SP2"><label>2</label><heading>Historical part</heading>
            <prov id="SS22"><label>22</label><heading>Historical section</heading></prov>
          </part>
        </skeleton.act.body>
      </skeleton.act>
    </skeletons>
  </end>
</act>
"""

    report = build_operation_surface(
        parse_nz_source_document(xml),
        archived_dependency_work_ids=frozenset({"act_public_2025_4"}),
    )

    assert report.summary()["target_surface_status_counts"] == {"skeleton_duplicate_resolved": 1}
    assert report.summary()["target_address_status_counts"] == {"blocked_target_hint_unparsed": 1}
    assert [finding["rule_id"] for finding in report.findings] == [
        "nz_target_address_hint_unparsed",
        "nz_lowering_readiness_blocked_target_hint_unparsed",
    ]
    assert report.findings[0]["target_resolution"]["target_resolution_status"] == "rejected"
    assert "selected_target" not in report.findings[0]["target_resolution"]


def test_build_operation_surface_resolves_attached_heading_from_context_with_named_observation() -> None:
    xml = b"""\
<act>
  <body>
    <part id="P1"><label>1</label><heading>Live part</heading>
      <notes>
        <history-note id="HN1">
          <amended-provision>Heading</amended-provision>
          <amending-operation>replaced</amending-operation>
          Heading: replaced by section 3 of the Example Amendment Act 2025 (2025 No 4).
        </history-note>
      </notes>
    </part>
  </body>
</act>
"""

    report = build_operation_surface(
        parse_nz_source_document(xml),
        archived_dependency_work_ids=frozenset({"act_public_2025_4"}),
    )

    assert report.summary()["target_hint_status_counts"] == {"attached_facet": 1}
    assert report.summary()["target_address_status_counts"] == {"candidate": 1}
    assert report.summary()["lowering_readiness_status_counts"] == {
        "ready_for_amending_act_payload_extraction": 1,
    }
    assert report.rows[0].target_address_candidate.address == "part:1/heading"
    assert [finding["rule_id"] for finding in report.findings] == [
        "nz_target_address_attached_heading_from_context",
    ]
    assert report.findings[0]["blocking"] is False
    assert report.findings[0]["target_resolution"]["target_resolution_status"] == "recovered"
    assert report.findings[0]["target_resolution"]["source_target"] == "Heading"
    assert report.findings[0]["target_resolution"]["selected_target"] == "part:1/heading"
    assert report.findings[0]["target_resolution"]["scope_confidence"] == "explicit_source_with_context"
    assert report.operation_evidence_rows()[0].to_dict()["finding_ids"] == (
        "nz-opw-1:nz_target_address_attached_heading_from_context",
    )


def test_nz_operation_surface_cli_parse_defaults() -> None:
    parser = _build_parser()

    args = parser.parse_args(["nz-corpus", "operation-surface", "--work-id", "act_public_1957_87"])

    assert args.command == "nz-corpus"
    assert args.nz_corpus_command == "operation-surface"
    assert args.db == "data/nz_legislation.farchive"
    assert args.work_id == "act_public_1957_87"
    assert args.summary_only is False
    assert args.operation_family == ""
    assert args.target_address_status == ""
    assert args.dependency_status == ""
    assert args.lowering_readiness_status == ""
    assert args.target_hint_status == ""
    assert args.evidence_rows is False
    assert args.evidence_jsonl is None

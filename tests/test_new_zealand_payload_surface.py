from __future__ import annotations

from lawvm.new_zealand.operation_surface import build_operation_surface
from lawvm.new_zealand.payload_surface import build_payload_surface
from lawvm.new_zealand.source_tree import parse_nz_source_document
from lawvm.tools.cli import _build_parser


def test_build_payload_surface_links_ready_operation_to_amending_node() -> None:
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
  <body>
    <prov id="A3"><label>3</label><heading>Repeal</heading><prov.body><para><text>Section 1 is repealed.</text></para></prov.body></prov>
  </body>
</act>
"""
    operation_surface = build_operation_surface(
        parse_nz_source_document(target_xml, version_id="target-version"),
        work_id="act_public_2020_1",
        archived_dependency_work_ids=frozenset({"act_public_2025_4"}),
    )

    report = build_payload_surface(
        operation_surface,
        dependency_documents={
            "act_public_2025_4": parse_nz_source_document(amendment_xml, version_id="amendment-version"),
        },
    )

    assert report.to_jsonable(summary_only=True)["replay_claims"] is False
    assert report.to_jsonable(summary_only=True)["enacted_payload_claims"] is False
    assert report.summary()["payload_status_counts"] == {"payload_found": 1}
    assert report.summary()["payload_role_counts"] == {"amending_provision_witness": 1}
    assert report.summary()["payload_semantics_status_counts"] == {
        "operation_witness_sufficient_no_enacted_payload_required": 1,
    }
    assert report.summary()["payload_instruction_shape_counts"] == {
        "direct_repeal_replace_instruction": 1,
    }
    assert report.summary()["payload_instruction_safety_counts"] == {
        "candidate_only_semantic_classification": 1,
    }
    assert report.rows[0].payload_status == "payload_found"
    assert report.rows[0].payload_role == "amending_provision_witness"
    assert report.rows[0].payload_semantics_status == "operation_witness_sufficient_no_enacted_payload_required"
    assert report.rows[0].payload_instruction_shape == "direct_repeal_replace_instruction"
    assert report.rows[0].payload_instruction_safety == "candidate_only_semantic_classification"
    assert report.rows[0].matches[0].xml_id == "A3"
    assert report.rows[0].matches[0].heading == "Repeal"
    assert report.rows[0].matches[0].text == "Section 1 is repealed."


def test_build_payload_surface_preserves_blocked_payload_cases() -> None:
    target_xml = b"""\
<act>
  <body>
    <prov id="S1"><label>1</label><heading>Target</heading>
      <notes>
        <history-note id="HN1">
          <amended-provision>Section 1</amended-provision>
          <amending-operation>amended</amending-operation>
          <amending-provision href="MISSING">section 3</amending-provision>
          <amending-leg>Example Amendment Act 2025</amending-leg>
          Section 1: amended by section 3 of the Example Amendment Act 2025 (2025 No 4).
        </history-note>
      </notes>
    </prov>
    <prov id="S2"><label>2</label><heading>Second target</heading>
      <notes>
        <history-note id="HN2">
          <amended-provision>Section 2</amended-provision>
          <amending-operation>amended</amending-operation>
          <amending-provision>section 4</amending-provision>
          <amending-leg>Example Amendment Act 2025</amending-leg>
          Section 2: amended by section 4 of the Example Amendment Act 2025 (2025 No 4).
        </history-note>
      </notes>
    </prov>
  </body>
</act>
"""
    amendment_xml = b"""\
<act>
  <body><prov id="A3"><label>3</label><heading>Amend</heading></prov></body>
</act>
"""
    operation_surface = build_operation_surface(
        parse_nz_source_document(target_xml),
        archived_dependency_work_ids=frozenset({"act_public_2025_4"}),
    )

    report = build_payload_surface(
        operation_surface,
        dependency_documents={"act_public_2025_4": parse_nz_source_document(amendment_xml)},
    )

    assert report.summary()["payload_status_counts"] == {
        "blocked_payload_href_missing": 1,
        "blocked_payload_href_not_found": 1,
    }
    assert report.summary()["payload_semantics_status_counts"] == {
        "payload_witness_not_available": 2,
    }
    assert report.summary()["payload_instruction_shape_counts"] == {"__none__": 2}
    assert report.summary()["payload_instruction_safety_counts"] == {"__none__": 2}
    assert report.rows[0].blocking_rule_id == "nz_payload_href_not_found"
    assert report.rows[1].blocking_rule_id == "nz_payload_href_missing"


def test_build_payload_surface_marks_retrospective_note_as_review_safety() -> None:
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
        archived_dependency_work_ids=frozenset({"act_public_2025_4"}),
    )

    report = build_payload_surface(
        operation_surface,
        dependency_documents={"act_public_2025_4": parse_nz_source_document(amendment_xml)},
    )

    assert report.summary()["payload_instruction_shape_counts"] == {
        "retrospective_incorporated_note": 1,
    }
    assert report.summary()["payload_instruction_safety_counts"] == {
        "review_retrospective_incorporated_note": 1,
    }
    assert report.rows[0].payload_instruction_shape == "retrospective_incorporated_note"
    assert report.rows[0].payload_instruction_safety == "review_retrospective_incorporated_note"


def test_payload_surface_json_filters_rows_without_changing_summary() -> None:
    target_xml = b"""\
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
          <amending-operation>inserted</amending-operation>
          <amending-provision href="A4">section 4</amending-provision>
          <amending-leg>Example Amendment Act 2025</amending-leg>
          Section 2: inserted by section 4 of the Example Amendment Act 2025 (2025 No 4).
        </history-note>
      </notes>
    </prov>
  </body>
</act>
"""
    amendment_xml = b"""\
<act>
  <body>
    <prov id="A3"><label>3</label><heading>Repeal</heading><prov.body><para><text>Section 1 is repealed.</text></para></prov.body></prov>
    <prov id="A4"><label>4</label><heading>Insert</heading><prov.body><para><text>Insert new section 2 after section 1.</text></para></prov.body></prov>
  </body>
</act>
"""
    operation_surface = build_operation_surface(
        parse_nz_source_document(target_xml),
        archived_dependency_work_ids=frozenset({"act_public_2025_4"}),
    )
    report = build_payload_surface(
        operation_surface,
        dependency_documents={"act_public_2025_4": parse_nz_source_document(amendment_xml)},
    )

    payload = report.to_jsonable(
        row_limit=10,
        instruction_shape="direct_repeal_replace_instruction",
    )

    assert payload["summary"]["payload_instruction_shape_counts"] == {
        "direct_insert_instruction": 1,
        "direct_repeal_replace_instruction": 1,
    }
    assert payload["filters"] == {"instruction_shape": "direct_repeal_replace_instruction"}
    assert payload["filtered_summary"]["rows"] == 1
    assert payload["filtered_summary"]["payload_instruction_shape_counts"] == {
        "direct_repeal_replace_instruction": 1,
    }
    assert len(payload["rows"]) == 1
    assert payload["rows"][0]["operation_family"] == "repealed"
    empty_payload = report.to_jsonable(summary_only=True, instruction_safety="unsafe_opaque_or_unclassified")
    assert empty_payload["filters"] == {"instruction_safety": "unsafe_opaque_or_unclassified"}
    assert empty_payload["filtered_summary"]["rows"] == 0
    assert "rows" not in empty_payload
    assert report.filtered_rows(operation_family="inserted")[0].payload_instruction_shape == "direct_insert_instruction"


def test_build_payload_surface_blocks_rows_not_ready_for_payload_extraction() -> None:
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

    report = build_payload_surface(operation_surface, dependency_documents={})

    assert report.summary()["payload_status_counts"] == {"blocked_operation_not_payload_ready": 1}
    assert report.summary()["payload_role_counts"] == {"__none__": 1}
    assert report.summary()["payload_semantics_status_counts"] == {
        "payload_witness_not_available": 1,
    }
    assert report.summary()["payload_instruction_shape_counts"] == {"__none__": 1}
    assert report.summary()["payload_instruction_safety_counts"] == {"__none__": 1}
    assert report.rows[0].blocking_rule_id == "nz_payload_operation_not_payload_ready"
    assert report.rows[0].operation_lowering_readiness_status == "blocked_duplicate_source_path"
    assert report.rows[0].operation_target_surface_status == "duplicate_source_path"
    assert report.rows[0].operation_target_hint_status == "parsed"
    assert report.rows[0].operation_target_address_status == "blocked_duplicate_source_path"
    assert report.rows[0].operation_target_blocking_rule_id == "nz_target_address_duplicate_source_path"


def test_nz_payload_surface_cli_parse_defaults() -> None:
    parser = _build_parser()

    args = parser.parse_args(["nz-corpus", "payload-surface", "--work-id", "act_public_1957_87"])

    assert args.command == "nz-corpus"
    assert args.nz_corpus_command == "payload-surface"
    assert args.db == "data/nz_legislation.farchive"
    assert args.work_id == "act_public_1957_87"
    assert args.limit == 40
    assert args.payload_status == ""
    assert args.operation_family == ""
    assert args.instruction_shape == ""
    assert args.instruction_safety == ""

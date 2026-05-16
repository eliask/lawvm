from __future__ import annotations

from lawvm.new_zealand.effect_readiness import build_effect_readiness_surface
from lawvm.new_zealand.operation_surface import build_operation_surface
from lawvm.new_zealand.payload_surface import build_payload_surface
from lawvm.new_zealand.source_tree import parse_nz_source_document
from lawvm.tools.cli import _build_parser


def test_build_effect_readiness_marks_payload_found_repeal_as_ready_without_replay_claim() -> None:
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

    report = build_effect_readiness_surface(operation_surface, payload_surface)

    assert report.to_jsonable(summary_only=True)["canonical_effect_claims"] is False
    assert report.summary()["effect_readiness_status_counts"] == {"ready_for_canonical_effect_lowering": 1}
    assert report.summary()["canonical_family_candidate_counts"] == {"repeal": 1}
    assert report.summary()["payload_semantics_status_counts"] == {
        "operation_witness_sufficient_no_enacted_payload_required": 1,
    }
    assert report.summary()["payload_instruction_shape_counts"] == {
        "empty_or_stub": 1,
    }
    assert report.summary()["payload_instruction_safety_counts"] == {
        "unsafe_opaque_or_unclassified": 1,
    }
    assert report.summary()["instruction_semantic_candidate_status_counts"] == {
        "not_required_for_repeal_candidate": 1,
    }
    assert report.summary()["instruction_semantic_candidate_family_counts"] == {
        "repeal_without_enacted_payload": 1,
    }
    assert report.summary()["instruction_semantic_rule_id_counts"] == {
        "nz_instruction_semantics_not_required_repeal": 1,
    }
    assert report.rows[0].target_address == "section:1"
    assert report.rows[0].payload_match_count == 1
    assert report.rows[0].payload_role == "amending_provision_witness"
    assert report.rows[0].payload_semantics_status == "operation_witness_sufficient_no_enacted_payload_required"
    assert report.rows[0].payload_instruction_shape == "empty_or_stub"
    assert report.rows[0].payload_instruction_safety == "unsafe_opaque_or_unclassified"
    assert report.rows[0].instruction_semantic_candidate_status == "not_required_for_repeal_candidate"
    assert report.rows[0].instruction_semantic_candidate_family == "repeal_without_enacted_payload"
    assert report.rows[0].instruction_semantic_rule_id == "nz_instruction_semantics_not_required_repeal"
    assert report.rows[0].payload_match_kinds == ("prov",)
    assert report.rows[0].payload_match_headings == ("Repeal",)


def test_build_effect_readiness_keeps_payload_found_amendment_blocked() -> None:
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

    report = build_effect_readiness_surface(operation_surface, payload_surface)

    assert report.summary()["effect_readiness_status_counts"] == {
        "blocked_text_or_structural_amendment_semantics_not_extracted": 1,
    }
    assert report.summary()["payload_semantics_status_counts"] == {
        "amending_provision_witness_not_enacted_payload": 1,
    }
    assert report.summary()["payload_instruction_shape_counts"] == {"direct_amended_by_instruction": 1}
    assert report.summary()["payload_instruction_safety_counts"] == {
        "candidate_only_semantic_classification": 1,
    }
    assert report.summary()["instruction_semantic_candidate_status_counts"] == {
        "candidate_only_instruction_semantics": 1,
    }
    assert report.summary()["instruction_semantic_candidate_family_counts"] == {
        "amend_instruction": 1,
    }
    assert report.summary()["instruction_semantic_rule_id_counts"] == {
        "nz_instruction_semantics_candidate_direct_instruction": 1,
    }
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


def test_build_effect_readiness_preserves_payload_blockers() -> None:
    target_xml = b"""\
<act>
  <body>
    <prov id="S1"><label>1</label><heading>Target</heading>
      <notes>
        <history-note id="HN1">
          <amended-provision>Section 1</amended-provision>
          <amending-operation>repealed</amending-operation>
          <amending-leg>Example Amendment Act 2025</amending-leg>
          Section 1: repealed by the Example Amendment Act 2025 (2025 No 4).
        </history-note>
      </notes>
    </prov>
  </body>
</act>
"""
    operation_surface = build_operation_surface(
        parse_nz_source_document(target_xml),
        archived_dependency_work_ids=frozenset({"act_public_2025_4"}),
    )
    payload_surface = build_payload_surface(operation_surface, dependency_documents={})

    report = build_effect_readiness_surface(operation_surface, payload_surface)

    assert report.summary()["effect_readiness_status_counts"] == {
        "blocked_operation_not_payload_ready": 1,
    }
    assert report.summary()["instruction_semantic_candidate_status_counts"] == {
        "blocked_payload_witness_not_available": 1,
    }
    assert report.rows[0].blocking_rule_id == "nz_effect_readiness_operation_not_payload_ready"
    assert report.rows[0].instruction_semantic_rule_id == "nz_instruction_semantics_payload_witness_not_available"
    assert report.rows[0].operation_lowering_readiness_status == "blocked_citation_unparsed"
    assert report.rows[0].operation_target_surface_status == "attached_structural_node"
    assert report.rows[0].operation_target_hint_status == "parsed"
    assert report.rows[0].operation_target_address_status == "candidate"
    assert report.rows[0].operation_target_blocking_rule_id == ""
    assert report.rows[0].operation_dependency_status == "citation_unparsed"


def test_effect_readiness_json_filters_rows_without_changing_summary() -> None:
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
    amendment_xml = b"""\
<act>
  <body>
    <prov id="A3"><label>3</label><heading>Repeal</heading></prov>
    <prov id="A4"><label>4</label><heading>Amend</heading>
      <prov.body><para><text>Section 2 is amended by replacing Old with New.</text></para></prov.body>
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
    report = build_effect_readiness_surface(operation_surface, payload_surface)

    payload = report.to_jsonable(
        row_limit=10,
        effect_readiness_status="ready_for_canonical_effect_lowering",
    )

    assert payload["summary"]["effect_readiness_status_counts"] == {
        "blocked_text_or_structural_amendment_semantics_not_extracted": 1,
        "ready_for_canonical_effect_lowering": 1,
    }
    assert payload["filters"] == {"effect_readiness_status": "ready_for_canonical_effect_lowering"}
    assert payload["filtered_summary"]["rows"] == 1
    assert payload["filtered_summary"]["effect_readiness_status_counts"] == {
        "ready_for_canonical_effect_lowering": 1,
    }
    assert len(payload["rows"]) == 1
    assert payload["rows"][0]["operation_family"] == "repealed"
    empty_payload = report.to_jsonable(summary_only=True, operation_family="editorial change")
    assert empty_payload["filters"] == {"operation_family": "editorial change"}
    assert empty_payload["filtered_summary"]["rows"] == 0
    assert "rows" not in empty_payload
    assert report.filtered_rows(operation_family="amended")[0].effect_readiness_status == (
        "blocked_text_or_structural_amendment_semantics_not_extracted"
    )


def test_build_effect_readiness_marks_retrospective_note_for_review_not_direct_candidate() -> None:
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
    payload_surface = build_payload_surface(
        operation_surface,
        dependency_documents={"act_public_2025_4": parse_nz_source_document(amendment_xml)},
    )

    report = build_effect_readiness_surface(operation_surface, payload_surface)

    assert report.summary()["instruction_semantic_candidate_status_counts"] == {
        "review_retrospective_incorporated_note": 1,
    }
    assert report.summary()["instruction_semantic_rule_id_counts"] == {
        "nz_instruction_semantics_review_retrospective_incorporated_note": 1,
    }
    assert report.rows[0].payload_instruction_shape == "retrospective_incorporated_note"
    assert report.rows[0].instruction_semantic_candidate_status == "review_retrospective_incorporated_note"
    assert report.rows[0].instruction_semantic_candidate_family == "insert_instruction"
    assert (
        report.rows[0].instruction_semantic_rule_id
        == "nz_instruction_semantics_review_retrospective_incorporated_note"
    )


def test_nz_effect_readiness_cli_parse_defaults() -> None:
    parser = _build_parser()

    args = parser.parse_args(["nz-corpus", "effect-readiness", "--work-id", "act_public_1957_87"])

    assert args.command == "nz-corpus"
    assert args.nz_corpus_command == "effect-readiness"
    assert args.db == "data/nz_legislation.farchive"
    assert args.work_id == "act_public_1957_87"
    assert args.limit == 40
    assert args.effect_readiness_status == ""
    assert args.operation_family == ""
    assert args.payload_status == ""
    assert args.instruction_semantic_candidate_status == ""
    assert args.operation_target_address_status == ""

from __future__ import annotations

from typing import Any

from lawvm.new_zealand.benchmark import build_nz_benchmark_report
from lawvm.new_zealand.dependencies import extract_dependency_report
from lawvm.new_zealand.source_tree import parse_nz_source_document
from lawvm.new_zealand.version_diff import diff_source_documents
from lawvm.tools.cli import _build_parser


class _FakeArchive:
    def __init__(self, rows: dict[str, bytes]) -> None:
        self.rows = rows

    def get(self, locator: str, *, at: object | None = None) -> bytes | None:
        return self.rows.get(locator)

    def locators(self, pattern: str = "%") -> list[str]:
        if pattern == "%":
            return sorted(self.rows)
        if pattern.endswith("%"):
            prefix = pattern[:-1]
            return sorted(locator for locator in self.rows if locator.startswith(prefix))
        return sorted(locator for locator in self.rows if locator == pattern)

    def close(self) -> None:
        return None


def _source_coverage_report_fixture() -> dict[str, Any]:
    before_xml = b"""\
<act>
  <cover><title>Example Act 2020</title></cover>
  <body>
    <prov id="S1"><label>1</label><heading>Title</heading><prov.body><para><text>Old text</text></para></prov.body></prov>
    <prov id="S2"><label>2</label><heading>Existing section</heading></prov>
  </body>
</act>
"""
    after_xml = b"""\
<act date.as.at="2025-01-01">
  <cover><title>Example Act 2020</title></cover>
  <body>
    <prov id="S1"><label>1</label><heading>Title</heading><prov.body><para><text>New text</text></para></prov.body>
      <notes>
        <history-note>
          <amended-provision>Section 1</amended-provision>
          <amending-operation>amended</amending-operation>
          <amending-provision href="A4">section 4</amending-provision>
          Section 1: amended, on 1 January 2025, by section 4 of the Example Amendment Act 2024 (2024 No 10).
        </history-note>
      </notes>
    </prov>
    <prov id="S2"><label>2</label><heading>Existing section</heading></prov>
    <prov id="S3"><label>3</label><heading>New section</heading></prov>
  </body>
  <end>
    <reprint.notes>
      <reprint.amend><citation>Example Amendment Act 2024 (2024 No 10): section 4</citation></reprint.amend>
      <reprint.amend><citation>Unparsed non-Act instrument</citation></reprint.amend>
    </reprint.notes>
  </end>
</act>
"""
    work_id = "act_public_2020_1"
    before = parse_nz_source_document(
        before_xml,
        xml_locator="archive://nz/act_public_2020_1/2024-01-01.xml",
        version_id="act_public_2020_1_en_2024-01-01",
    )
    after = parse_nz_source_document(
        after_xml,
        xml_locator="archive://nz/act_public_2020_1/2025-01-01.xml",
        version_id="act_public_2020_1_en_2025-01-01",
    )
    dependencies = extract_dependency_report(
        xml_bytes=after_xml,
        xml_locator=after.xml_locator,
        work_id=work_id,
        version_id=after.version_id,
    )
    version_diff = diff_source_documents(before, after)

    return {
        "jurisdiction": "nz",
        "report_kind": "source_coverage",
        "source_lane": "archived_consolidated_xml",
        "truth_claim": "source_witness_inventory",
        "replay_claims": False,
        "work_id": work_id,
        "latest_version_id": after.version_id,
        "latest_xml_locator": after.xml_locator,
        "source_summary": after.summary(),
        "dependency_summary": {
            "amending_works": [ref.to_jsonable() for ref in dependencies.amending_works],
            "diagnostics": list(dependencies.diagnostics),
            "history_note_count": dependencies.history_note_count,
            "reprint_amendment_count": dependencies.reprint_amendment_count,
        },
        "version_diff_summary": version_diff.summary(),
    }


def test_nz_benchmark_source_coverage_report_shape_is_source_only() -> None:
    report = _source_coverage_report_fixture()

    assert report["jurisdiction"] == "nz"
    assert report["report_kind"] == "source_coverage"
    assert report["source_lane"] == "archived_consolidated_xml"
    assert report["truth_claim"] == "source_witness_inventory"
    assert report["replay_claims"] is False
    assert "score" not in report
    assert "replay_similarity" not in report
    assert "effect_claims" not in report


def test_nz_benchmark_source_coverage_report_counts_source_witnesses() -> None:
    report = _source_coverage_report_fixture()

    assert report["source_summary"]["nodes"] == 3
    assert report["source_summary"]["history_witnesses"] == 1
    assert report["source_summary"]["amending_works"] == 1
    assert report["dependency_summary"]["history_note_count"] == 1
    assert report["dependency_summary"]["reprint_amendment_count"] == 1
    assert [ref["work_id"] for ref in report["dependency_summary"]["amending_works"]] == [
        "act_public_2024_10",
    ]
    assert [diag["rule_id"] for diag in report["dependency_summary"]["diagnostics"]] == [
        "nz_dependency_reprint_amend_unparsed",
    ]
    assert report["version_diff_summary"]["change_counts"] == {"changed": 1, "added": 1}


def test_build_nz_benchmark_report_is_archive_first_and_blocks_replay(tmp_path) -> None:
    before_version = "act_public_2020_1_en_2024-01-01"
    after_version = "act_public_2020_1_en_2025-01-01"
    dep_version = "act_public_2024_10_en_2024-12-01"
    before_xml_locator = "https://www.legislation.govt.nz/act/public/2020/1/en/2024-01-01.xml"
    after_xml_locator = "https://www.legislation.govt.nz/act/public/2020/1/en/2025-01-01.xml"
    dep_xml_locator = "https://www.legislation.govt.nz/act/public/2024/10/en/2024-12-01.xml"
    before_xml = b"""\
<act>
  <cover><title>Example Act 2020</title></cover>
  <body>
    <prov id="S1"><label>1</label><heading>Title</heading><prov.body><para><text>Old text</text></para></prov.body></prov>
  </body>
</act>
"""
    after_xml = b"""\
<act date.as.at="2025-01-01">
  <cover><title>Example Act 2020</title></cover>
  <body>
    <prov id="S1"><label>1</label><heading>Title</heading><prov.body><para><text>New text</text></para></prov.body>
      <notes>
        <history-note>
          <amended-provision>Section 1</amended-provision>
          <amending-operation>amended</amending-operation>
          <amending-provision href="A4">section 4</amending-provision>
          Section 1: amended, on 1 January 2025, by section 4 of the Example Amendment Act 2024 (2024 No 10).
        </history-note>
      </notes>
    </prov>
    <prov id="S2"><label>2</label><heading>New section</heading></prov>
  </body>
</act>
"""
    archive = _FakeArchive(
        {
            f"https://api.legislation.govt.nz/v0/versions/{before_version}/": (
                b'{"version_id":"act_public_2020_1_en_2024-01-01","formats":[{"type":"xml",'
                b'"url":"https://www.legislation.govt.nz/act/public/2020/1/en/latest.xml"}]}'
            ),
            f"https://api.legislation.govt.nz/v0/versions/{after_version}/": (
                b'{"version_id":"act_public_2020_1_en_2025-01-01","formats":[{"type":"xml",'
                b'"url":"https://www.legislation.govt.nz/act/public/2020/1/en/latest.xml"}]}'
            ),
            f"https://api.legislation.govt.nz/v0/versions/{dep_version}/": (
                b'{"version_id":"act_public_2024_10_en_2024-12-01","formats":[{"type":"xml",'
                b'"url":"https://www.legislation.govt.nz/act/public/2024/10/en/latest.xml"}]}'
            ),
            before_xml_locator: before_xml,
            after_xml_locator: after_xml,
            dep_xml_locator: (
                b"<act><cover><title>Example Amendment Act 2024</title></cover>"
                b"<body><prov id=\"A4\"><label>4</label><heading>Amendment payload</heading>"
                b"<prov.body><para><text>Section 1 is amended by replacing Old with New.</text></para></prov.body>"
                b"</prov></body></act>"
            ),
        }
    )

    report = build_nz_benchmark_report(
        archive,
        db_path=tmp_path / "nz.farchive",
        work_ids=("act_public_2020_1",),
        include_diffs=True,
        include_payloads=True,
    )

    payload = report.to_jsonable()
    assert payload["jurisdiction"] == "nz"
    assert payload["report_kind"] == "benchmark_source_coverage"
    assert payload["truth_claim"] == "source_witness_inventory"
    assert payload["replay_claims"] is False
    assert payload["selection_context"] == {
        "available_work_count": 2,
        "requested_work_count": 1,
        "requested_work_ids_sample": ["act_public_2020_1"],
        "requested_work_ids_omitted": 0,
        "selected_work_count": 1,
        "selected_work_ids_sample": ["act_public_2020_1"],
        "selected_work_ids_omitted": 0,
        "max_works": None,
        "truncated_by_max_works": False,
    }
    summary = report.summary()
    assert summary["selection_context"] == payload["selection_context"]
    assert summary["works"] == 1
    assert summary["source_parsed"] == 1
    assert summary["dependency_edges"] == 1
    assert summary["dependency_edges_archived"] == 1
    assert summary["history_operation_counts"] == {"amended": 1}
    assert summary["operation_witness_rows"] == 1
    assert summary["target_hint_status_counts"] == {"parsed": 1}
    assert summary["target_hint_kind_counts"] == {"section": 1}
    assert summary["target_address_status_counts"] == {"candidate": 1}
    assert summary["amending_provision_href_status_counts"] == {"present": 1}
    assert summary["lowering_readiness_status_counts"] == {"ready_for_amending_act_payload_extraction": 1}
    assert summary["operation_surface_findings"] == 0
    assert summary["payload_status_counts"] == {"payload_found": 1}
    assert summary["payload_role_counts"] == {"amending_provision_witness": 1}
    assert summary["payload_semantics_status_counts"] == {
        "amending_provision_witness_not_enacted_payload": 1,
    }
    assert summary["payload_instruction_shape_counts"] == {"direct_amended_by_instruction": 1}
    assert summary["payload_instruction_safety_counts"] == {
        "candidate_only_semantic_classification": 1,
    }
    assert summary["payload_found"] == 1
    assert summary["effect_readiness_status_counts"] == {
        "blocked_text_or_structural_amendment_semantics_not_extracted": 1,
    }
    assert summary["instruction_semantic_candidate_status_counts"] == {
        "candidate_only_instruction_semantics": 1,
    }
    assert summary["instruction_semantic_candidate_family_counts"] == {
        "amend_instruction": 1,
    }
    assert summary["instruction_semantic_rule_id_counts"] == {
        "nz_instruction_semantics_candidate_direct_instruction": 1,
    }
    assert summary["ready_for_canonical_effect_lowering"] == 0
    assert summary["effect_candidate_status_counts"] == {"blocked": 1}
    assert summary["effect_candidate_operation_family_counts"] == {"amended": 1}
    assert summary["effect_candidate_blocked_operation_family_counts"] == {"amended": 1}
    assert summary["effect_candidate_blocked_operation_family_rule_counts"] == {
        "amended|nz_effect_readiness_amendment_semantics_not_extracted": 1,
    }
    assert summary["effect_candidate_blocked_operation_family_payload_shape_counts"] == {
        "amended|direct_amended_by_instruction": 1,
    }
    assert summary["effect_candidate_blocked_operation_family_payload_safety_counts"] == {
        "amended|candidate_only_semantic_classification": 1,
    }
    assert summary["effect_candidate_blocked_operation_family_target_status_counts"] == {
        "amended|candidate": 1,
    }
    assert summary["effect_candidate_blocked_operation_family_instruction_status_counts"] == {
        "amended|candidate_only_instruction_semantics": 1,
    }
    assert summary["effect_candidate_blocked_operation_family_instruction_subfamily_status_counts"] == {
        "amended|blocked_omitting_substituting_parse_failed": 1,
    }
    assert summary["effect_candidate_witness_rule_counts"] == {}
    assert summary["effect_candidate_action_witness_rule_counts"] == {}
    assert summary["effect_candidate_text_replace_witness_support_status_counts"] == {"__none__": 1}
    assert summary["effect_candidate_action_text_replace_witness_support_status_counts"] == {
        "__none__|__none__": 1,
    }
    assert summary["effect_candidate_action_source_change_text_witness_status_counts"] == {}
    assert summary["effect_candidate_blocked_operation_family_source_change_text_witness_status_counts"] == {
        "amended|__none__": 1,
    }
    assert summary["effect_candidate_source_version_date_window_status_counts"] == {
        "missing_amendment_date_iso": 1,
    }
    assert summary["effect_candidate_source_change_text_witness_status_counts"] == {"__none__": 1}
    assert summary["effect_candidate_repeal_payload_corroboration_status_counts"] == {}
    assert summary["effect_candidate_emitted_rows"] == 0
    assert summary["effect_candidate_operation_missing_rows"] == 0
    assert summary["effect_candidate_operations"] == 0
    assert summary["effect_preflight_status_counts"] == {"blocked_incomplete_candidate_set": 1}
    assert summary["effect_preflight_replayable_candidate_operations"] == 0
    assert summary["effect_preflight_source_change_only_candidate_rows"] == 0
    assert summary["effect_preflight_target_recovery_candidate_rows"] == 0
    assert summary["effect_preflight_operations_to_replay"] == 0
    assert summary["effect_preflight_blocking_rule_counts"] == {
        "nz_effect_readiness_amendment_semantics_not_extracted": 1,
    }
    triage = summary["triage_exemplars"]
    assert triage["effect_candidate_blocked_operation_family_rule"] == {
        "amended|nz_effect_readiness_amendment_semantics_not_extracted": ["act_public_2020_1"],
    }
    assert triage["effect_candidate_blocked_operation_family_payload_shape"] == {
        "amended|direct_amended_by_instruction": ["act_public_2020_1"],
    }
    assert triage["effect_candidate_blocked_operation_family_payload_safety"] == {
        "amended|candidate_only_semantic_classification": ["act_public_2020_1"],
    }
    assert triage["effect_candidate_blocked_operation_family_target_status"] == {
        "amended|candidate": ["act_public_2020_1"],
    }
    assert triage["effect_candidate_source_change_text_witness_status"] == {
        "__none__": ["act_public_2020_1"],
    }
    assert triage["effect_candidate_blocked_operation_family_instruction_subfamily_status"] == {
        "amended|blocked_omitting_substituting_parse_failed": ["act_public_2020_1"],
    }
    assert triage["effect_preflight_blocking_rule"] == {
        "nz_effect_readiness_amendment_semantics_not_extracted": ["act_public_2020_1"],
    }
    assert triage["effect_preflight_status"] == {
        "blocked_incomplete_candidate_set": ["act_public_2020_1"],
    }
    assert triage["ready_candidate_work_ids"] == []
    assert summary["snapshot_diffs"] == 1
    assert summary["replay_blocked"] == 1
    assert summary["replay_blocking_rule_id"] == "nz_replay_canonical_effects_not_implemented"
    assert summary["oracle_agreement_blocked"] == 1
    assert summary["oracle_agreement_blocking_rule_id"] == "nz_oracle_agreement_candidate_replay_missing"
    work = report.work_reports[0]
    assert work.replay_status == "blocked"
    assert work.oracle_agreement_status == "blocked_no_candidate_replay"
    assert work.history_operation_counts == {"amended": 1}
    assert work.operation_witness_rows == 1
    assert work.target_hint_status_counts == {"parsed": 1}
    assert work.target_address_status_counts == {"candidate": 1}
    assert work.amending_provision_href_status_counts == {"present": 1}
    assert work.lowering_readiness_status_counts == {"ready_for_amending_act_payload_extraction": 1}
    assert work.payload_status_counts == {"payload_found": 1}
    assert work.payload_role_counts == {"amending_provision_witness": 1}

    assert work.payload_semantics_status_counts == {
        "amending_provision_witness_not_enacted_payload": 1,
    }
    assert work.payload_instruction_shape_counts == {"direct_amended_by_instruction": 1}
    assert work.payload_instruction_safety_counts == {
        "candidate_only_semantic_classification": 1,
    }
    assert work.payload_found == 1
    assert work.effect_readiness_status_counts == {
        "blocked_text_or_structural_amendment_semantics_not_extracted": 1,
    }
    assert work.instruction_semantic_candidate_status_counts == {
        "candidate_only_instruction_semantics": 1,
    }
    assert work.instruction_semantic_candidate_family_counts == {
        "amend_instruction": 1,
    }
    assert work.instruction_semantic_rule_id_counts == {
        "nz_instruction_semantics_candidate_direct_instruction": 1,
    }
    assert work.ready_for_canonical_effect_lowering == 0
    assert work.effect_candidate_status_counts == {"blocked": 1}
    assert work.effect_candidate_operation_family_counts == {"amended": 1}
    assert work.effect_candidate_blocked_operation_family_counts == {"amended": 1}
    assert work.effect_candidate_blocked_operation_family_rule_counts == {
        "amended|nz_effect_readiness_amendment_semantics_not_extracted": 1,
    }
    assert work.effect_candidate_blocked_operation_family_payload_shape_counts == {
        "amended|direct_amended_by_instruction": 1,
    }
    assert work.effect_candidate_blocked_operation_family_payload_safety_counts == {
        "amended|candidate_only_semantic_classification": 1,
    }
    assert work.effect_candidate_blocked_operation_family_target_status_counts == {
        "amended|candidate": 1,
    }
    assert work.effect_candidate_blocked_operation_family_instruction_status_counts == {
        "amended|candidate_only_instruction_semantics": 1,
    }
    assert work.effect_candidate_blocked_operation_family_instruction_subfamily_status_counts == {
        "amended|blocked_omitting_substituting_parse_failed": 1,
    }
    assert work.effect_candidate_witness_rule_counts == {}
    assert work.effect_candidate_action_witness_rule_counts == {}
    assert work.effect_candidate_text_replace_witness_support_status_counts == {"__none__": 1}
    assert work.effect_candidate_action_text_replace_witness_support_status_counts == {
        "__none__|__none__": 1,
    }
    assert work.effect_candidate_action_source_change_text_witness_status_counts == {}
    assert work.effect_candidate_blocked_operation_family_source_change_text_witness_status_counts == {
        "amended|__none__": 1,
    }
    assert work.effect_candidate_source_version_date_window_status_counts == {
        "missing_amendment_date_iso": 1,
    }
    assert work.effect_candidate_source_change_text_witness_status_counts == {"__none__": 1}
    assert work.effect_candidate_repeal_payload_corroboration_status_counts == {}
    assert work.effect_candidate_emitted_rows == 0
    assert work.effect_candidate_operation_missing_rows == 0
    assert work.effect_candidate_operations == 0
    assert work.effect_preflight_status == "blocked_incomplete_candidate_set"
    assert work.effect_preflight_replayable_candidate_operations == 0
    assert work.effect_preflight_source_change_only_candidate_rows == 0
    assert work.effect_preflight_target_recovery_candidate_rows == 0
    assert work.effect_preflight_operations_to_replay == 0
    assert work.effect_preflight_blocking_rule_counts == {
        "nz_effect_readiness_amendment_semantics_not_extracted": 1,
    }
    assert work.snapshot_change_count == 2
    assert [finding["rule_id"] for finding in work.findings] == [
        "nz_replay_canonical_effects_not_implemented",
        "nz_oracle_agreement_candidate_replay_missing",
    ]


def test_build_nz_benchmark_report_records_max_work_selection_context(tmp_path) -> None:
    archive = _FakeArchive(
        {
            "https://api.legislation.govt.nz/v0/versions/act_public_2020_1_en_2025-01-01/": b"{}",
            "https://api.legislation.govt.nz/v0/versions/act_public_2021_2_en_2025-01-01/": b"{}",
        }
    )

    report = build_nz_benchmark_report(
        archive,
        db_path=tmp_path / "nz.farchive",
        max_works=0,
    )
    context = report.selection_context()

    assert report.summary()["works"] == 0
    assert report.to_jsonable()["selection_context"] == context
    assert context == {
        "available_work_count": 2,
        "requested_work_count": 0,
        "requested_work_ids_sample": [],
        "requested_work_ids_omitted": 0,
        "selected_work_count": 0,
        "selected_work_ids_sample": [],
        "selected_work_ids_omitted": 0,
        "max_works": 0,
        "truncated_by_max_works": True,
    }


def test_nz_benchmark_cli_parse_defaults() -> None:
    parser = _build_parser()

    args = parser.parse_args(["nz-corpus", "benchmark", "--work-id", "act_public_2020_1"])

    assert args.command == "nz-corpus"
    assert args.nz_corpus_command == "benchmark"
    assert args.db == "data/nz_legislation.farchive"
    assert args.work_id == ["act_public_2020_1"]
    assert args.include_diffs is False
    assert args.include_payloads is False

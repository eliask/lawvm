from __future__ import annotations

import json
from pathlib import Path
import xml.etree.ElementTree as ET
from typing import Any

import pytest

import lawvm.uk_legislation.uk_amendment_replay as uk_replay_mod
from lawvm.core.ir import (
    IRNode,
    IRStatute,
    LegalAddress,
    LegalOperation,
    OperationSource,
    StructuralAction,
    TextPatchSpec,
    TextSelector,
)
from lawvm.core.semantic_types import FacetKind, IRNodeKind, TextPatchKindEnum
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.uk_legislation.uk_amendment_replay import (
    UKEffectRecord,
    UKReplayPipeline,
    UKReplayExecutor,
    _order_uk_effects_for_replay,
    _order_schedule_materialization_ops,
    _uk_source_provision_order_key,
    _uk_op_allowed_by_authority_mode,
    _fragment_substitution,
    _NOTE_METADATA_SOURCE_FALLBACK,
    _NOTE_FRAGMENT_SUB,
    _NOTE_REWRITE_WITNESS,
    _NOTE_TEXT_REWRITE_RULE,
    _NOTE_PRECEDING_EID,
    _order_uk_text_patch_preimage_chains,
    _repeal_tail_for_substituted_series_replacement,
    _retarget_substituted_series_to_replaced_anchor,
    _split_metadata_provisions,
    _parse_ref,
    _parse_affected_target,
    compile_effect_to_ir_ops,
    extract_provision_element_from_bytes,
    load_effects_for_statute,
    load_effects_for_statute_from_archive,
    parse_effects_from_bytes,
    parse_effects_from_feeds,
    parse_effects_from_metadata,
)

_AVAILABLE_XML_BYTES = b"<Legislation>" + (b"x" * 128) + b"</Legislation>"


def replay_uk_ops(base, ops, **kwargs):
    return UKReplayPipeline(Path(".")).apply_ops(base, ops, **kwargs)


def _replace_patch(match: str, replacement: str, occurrence: int = 0) -> TextPatchSpec:
    return TextPatchSpec(
        kind=TextPatchKindEnum.REPLACE,
        selector=TextSelector(match_text=match, occurrence=occurrence),
        replacement=replacement,
    )


def _append_patch(insertion: str) -> TextPatchSpec:
    return TextPatchSpec(
        kind=TextPatchKindEnum.APPEND,
        selector=TextSelector(match_text="TEXT_END", occurrence=0),
        replacement=insertion,
    )


def _minimal_uk_effect(
    effect_id: str,
    *,
    affecting_provisions: str,
    effective_date: str = "2024-11-18",
    affected_provisions: str = "Sch. 9 para. 129(2)(a)",
) -> UKEffectRecord:
    return UKEffectRecord(
        effect_id=effect_id,
        effect_type="words inserted",
        applied=True,
        requires_applied=False,
        modified="",
        affected_uri="/id/ukpga/2024/3",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2024",
        affected_number="3",
        affected_provisions=affected_provisions,
        affecting_uri="/id/uksi/2024/1012",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2024",
        affecting_number="1012",
        affecting_provisions=affecting_provisions,
        affecting_title="Test Regulations",
        in_force_dates=[{"date": effective_date, "prospective": "false"}],
    )


def test_parse_effects_from_bytes_records_malformed_feed_page() -> None:
    parse_rejections: list[dict[str, Any]] = []

    records = parse_effects_from_bytes(
        [b"<feed><entry></feed>"],
        parse_rejections_out=parse_rejections,
    )

    assert records == []
    assert len(parse_rejections) == 1
    rejection = parse_rejections[0]
    assert rejection["rule_id"] == "uk_effect_feed_xml_parse_rejected"
    assert rejection["family"] == "source_pathology"
    assert rejection["phase"] == "parse"
    assert rejection["feed_index"] == 0
    assert rejection["blocking"] is True
    assert rejection["strict_disposition"] == "block"
    assert rejection["quirks_disposition"] == "record"
    assert "parse_error" in rejection


def test_fragment_substitution_provenance_only_swallows_json_decode_errors(
    monkeypatch,
) -> None:
    valid_op = LegalOperation(
        op_id="op-1",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=(("section", "1"),)),
        provenance_tags=(
            _NOTE_FRAGMENT_SUB + '[{"original": "old text", "replacement": "new text"}]',
        ),
    )
    malformed_op = LegalOperation(
        op_id="op-2",
        sequence=2,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=(("section", "1"),)),
        provenance_tags=(_NOTE_FRAGMENT_SUB + "{not json",),
    )
    unexpected_op = LegalOperation(
        op_id="op-3",
        sequence=3,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=(("section", "1"),)),
        provenance_tags=(_NOTE_FRAGMENT_SUB + "[]",),
    )

    assert _fragment_substitution(valid_op) == [
        {"original": "old text", "replacement": "new text"}
    ]
    assert _fragment_substitution(malformed_op) is None

    def raise_unexpected(_payload: str) -> object:
        raise RuntimeError("unexpected provenance parser failure")

    monkeypatch.setattr(uk_replay_mod.json, "loads", raise_unexpected)
    with pytest.raises(RuntimeError, match="unexpected provenance parser failure"):
        _fragment_substitution(unexpected_op)


def test_parse_effects_from_bytes_records_entry_missing_effect() -> None:
    parse_rejections: list[dict[str, Any]] = []
    feed = b"""
    <feed xmlns="http://www.w3.org/2005/Atom"
          xmlns:ukm="http://www.legislation.gov.uk/namespaces/metadata">
      <entry>
        <id>tag:example,2026:no-effect</id>
        <title>No effect payload</title>
      </entry>
    </feed>
    """

    records = parse_effects_from_bytes(
        [feed],
        parse_rejections_out=parse_rejections,
    )

    assert records == []
    assert parse_rejections == [
        {
            "rule_id": "uk_effect_feed_entry_missing_effect_rejected",
            "family": "source_pathology",
            "phase": "parse",
            "feed_index": 0,
            "entry_index": 0,
            "entry_id": "tag:example,2026:no-effect",
            "entry_title": "No effect payload",
            "reason": "UK effect feed entry did not contain a ukm:Effect payload.",
            "blocking": True,
            "strict_disposition": "block",
            "quirks_disposition": "record",
        }
    ]


def test_parse_effects_from_bytes_keeps_existing_api_without_rejection_sink() -> None:
    assert parse_effects_from_bytes([b"<feed><entry></feed>"]) == []


def test_parse_effects_from_feeds_records_malformed_local_feed_page(tmp_path: Path) -> None:
    feed_path = tmp_path / "data.feed"
    feed_path.write_text("<feed><entry></feed>", encoding="utf-8")
    parse_rejections: list[dict[str, Any]] = []

    records = parse_effects_from_feeds(
        [feed_path],
        parse_rejections_out=parse_rejections,
    )

    assert records == []
    assert len(parse_rejections) == 1
    rejection = parse_rejections[0]
    assert rejection["rule_id"] == "uk_effect_feed_xml_parse_rejected"
    assert rejection["feed_locator"] == str(feed_path)
    assert rejection["blocking"] is True


def test_parse_effects_from_feeds_records_missing_local_feed_page(tmp_path: Path) -> None:
    feed_path = tmp_path / "missing.feed"
    parse_rejections: list[dict[str, Any]] = []

    records = parse_effects_from_feeds(
        [feed_path],
        parse_rejections_out=parse_rejections,
    )

    assert records == []
    assert parse_rejections == [
        {
            "rule_id": "uk_effect_feed_file_missing_rejected",
            "family": "source_pathology",
            "phase": "acquisition",
            "feed_index": 0,
            "feed_path": str(feed_path),
            "reason": "UK local effect feed file was listed but missing on disk.",
            "blocking": True,
            "strict_disposition": "block",
            "quirks_disposition": "record",
        }
    ]


def test_parse_effects_from_metadata_records_malformed_xml(tmp_path: Path) -> None:
    metadata_path = tmp_path / "metadata.xml"
    metadata_path.write_text("<Legislation><UnappliedEffects></Legislation>", encoding="utf-8")
    parse_rejections: list[dict[str, Any]] = []

    records = parse_effects_from_metadata(
        metadata_path,
        parse_rejections_out=parse_rejections,
        statute_id="ukpga/2000/10",
    )

    assert records == []
    assert len(parse_rejections) == 1
    rejection = parse_rejections[0]
    assert rejection["rule_id"] == "uk_metadata_xml_parse_failed_rejected"
    assert rejection["family"] == "source_pathology"
    assert rejection["phase"] == "parse"
    assert rejection["statute_id"] == "ukpga/2000/10"
    assert rejection["metadata_path"] == str(metadata_path)
    assert rejection["exception_type"] == "ParseError"
    assert rejection["blocking"] is True
    assert rejection["strict_disposition"] == "block"
    assert rejection["quirks_disposition"] == "record"
    assert "parse_error" in rejection


def test_parse_effects_from_metadata_valid_empty_xml_has_no_rejection(tmp_path: Path) -> None:
    metadata_path = tmp_path / "metadata.xml"
    metadata_path.write_text("<Legislation><UnappliedEffects /></Legislation>", encoding="utf-8")
    parse_rejections: list[dict[str, Any]] = []

    records = parse_effects_from_metadata(metadata_path, parse_rejections_out=parse_rejections)

    assert records == []
    assert parse_rejections == []


def test_load_effects_for_statute_threads_metadata_parse_rejections(tmp_path: Path) -> None:
    stat_dir = tmp_path / "ukpga/2000/10"
    stat_dir.mkdir(parents=True)
    (stat_dir / "metadata.xml").write_text("<Legislation><UnappliedEffects></Legislation>", encoding="utf-8")
    parse_rejections: list[dict[str, Any]] = []

    records = load_effects_for_statute(
        "ukpga/2000/10",
        tmp_path,
        parse_rejections_out=parse_rejections,
    )

    assert records == []
    assert len(parse_rejections) == 1
    assert parse_rejections[0]["rule_id"] == "uk_metadata_xml_parse_failed_rejected"
    assert parse_rejections[0]["statute_id"] == "ukpga/2000/10"
    assert parse_rejections[0]["metadata_path"] == str(stat_dir / "metadata.xml")


def test_load_effects_for_statute_threads_local_feed_parse_rejections(tmp_path: Path) -> None:
    stat_dir = tmp_path / "ukpga/2000/10"
    stat_dir.mkdir(parents=True)
    (stat_dir / "data.feed").write_text("<feed><entry></feed>", encoding="utf-8")
    parse_rejections: list[dict[str, Any]] = []

    records = load_effects_for_statute(
        "ukpga/2000/10",
        tmp_path,
        parse_rejections_out=parse_rejections,
    )

    assert records == []
    assert len(parse_rejections) == 1
    assert parse_rejections[0]["rule_id"] == "uk_effect_feed_xml_parse_rejected"
    assert parse_rejections[0]["feed_locator"] == str(stat_dir / "data.feed")


class _FakeUKArchiveConn:
    def __init__(self, locators: list[str]) -> None:
        self._locators = locators

    def execute(self, _query: str, _params: tuple[str]) -> "_FakeUKArchiveConn":
        return self

    def fetchall(self) -> list[tuple[str]]:
        return [(locator,) for locator in self._locators]


class _FakeUKArchive:
    def __init__(self, payloads: dict[str, bytes], *, locators: list[str] | None = None) -> None:
        self._payloads = payloads
        self._conn = _FakeUKArchiveConn(locators if locators is not None else list(payloads))

    def get(self, locator: str) -> bytes | None:
        return self._payloads.get(locator)


def test_load_effects_from_archive_threads_feed_parse_rejections() -> None:
    locator = "https://www.legislation.gov.uk/changes/affected/ukpga/2000/10/data.feed"
    archive = _FakeUKArchive({locator: b"<feed><entry></feed>"})
    parse_rejections: list[dict[str, Any]] = []

    records = load_effects_for_statute_from_archive(
        "ukpga/2000/10",
        archive,
        parse_rejections_out=parse_rejections,
    )

    assert records == []
    assert len(parse_rejections) == 1
    assert parse_rejections[0]["rule_id"] == "uk_effect_feed_xml_parse_rejected"
    assert parse_rejections[0]["feed_locator"] == locator


def test_load_effects_from_archive_records_missing_indexed_feed_payload() -> None:
    locator = "https://www.legislation.gov.uk/changes/affected/ukpga/2000/10/data.feed"
    archive = _FakeUKArchive({}, locators=[locator])
    parse_rejections: list[dict[str, Any]] = []

    records = load_effects_for_statute_from_archive(
        "ukpga/2000/10",
        archive,
        parse_rejections_out=parse_rejections,
    )

    assert records == []
    assert parse_rejections == [
        {
            "rule_id": "uk_effect_feed_locator_payload_missing_rejected",
            "family": "source_pathology",
            "phase": "acquisition",
            "statute_id": "ukpga/2000/10",
            "feed_locator": locator,
            "reason": "UK effect feed locator was indexed but payload bytes were missing from the archive.",
            "blocking": True,
            "strict_disposition": "block",
            "quirks_disposition": "record",
        }
    ]


def test_load_effects_from_archive_records_absent_feed_pages() -> None:
    archive = _FakeUKArchive({}, locators=[])
    parse_rejections: list[dict[str, Any]] = []

    records = load_effects_for_statute_from_archive(
        "ukpga/2000/10",
        archive,
        parse_rejections_out=parse_rejections,
    )

    assert records == []
    assert parse_rejections == [
        {
            "rule_id": "uk_effect_feed_pages_absent_recorded",
            "family": "source_pathology",
            "phase": "acquisition",
            "statute_id": "ukpga/2000/10",
            "feed_pattern": "%/changes/affected/ukpga/2000/10/%",
            "reason": "No UK effect feed page locators were present in the archive for this statute.",
            "blocking": False,
            "strict_disposition": "record",
            "quirks_disposition": "record",
        }
    ]


def test_pipeline_compile_ops_threads_feed_parse_rejections() -> None:
    locator = "https://www.legislation.gov.uk/changes/affected/ukpga/2000/10/data.feed"
    archive = _FakeUKArchive({locator: b"<feed><entry></feed>"})
    pipeline = UKReplayPipeline(Path("."))
    parse_rejections: list[dict[str, Any]] = []

    compiled = pipeline.compile_ops_for_statute(
        "ukpga/2000/10",
        archive=archive,
        effect_feed_parse_rejections_out=parse_rejections,
    )

    assert compiled == []
    assert len(parse_rejections) == 1
    assert parse_rejections[0]["rule_id"] == "uk_effect_feed_xml_parse_rejected"
    assert parse_rejections[0]["feed_locator"] == locator


def test_pipeline_compile_ops_threads_missing_feed_payload_rejection() -> None:
    locator = "https://www.legislation.gov.uk/changes/affected/ukpga/2000/10/data.feed"
    archive = _FakeUKArchive({}, locators=[locator])
    pipeline = UKReplayPipeline(Path("."))
    parse_rejections: list[dict[str, Any]] = []

    compiled = pipeline.compile_ops_for_statute(
        "ukpga/2000/10",
        archive=archive,
        effect_feed_parse_rejections_out=parse_rejections,
    )

    assert compiled == []
    assert len(parse_rejections) == 1
    assert parse_rejections[0]["rule_id"] == "uk_effect_feed_locator_payload_missing_rejected"
    assert parse_rejections[0]["feed_locator"] == locator


_LEG_NS = "http://www.legislation.gov.uk/namespaces/legislation"


def test_compile_whole_schedule_target_prefers_schedule_payload() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P1 xmlns="{_LEG_NS}">
          <BlockAmendment>
            <P2>
              <Pnumber>2</Pnumber>
              <Text>Unrelated paragraph 2.</Text>
            </P2>
          </BlockAmendment>
          <BlockAmendment>
            <Schedule eId="schedule-2">
              <Number>2</Number>
              <Title>Schedule 2</Title>
              <ScheduleBody>
                <P1 eId="schedule-2-paragraph-1">
                  <Pnumber>1</Pnumber>
                  <Text>Schedule text.</Text>
                </P1>
              </ScheduleBody>
            </Schedule>
          </BlockAmendment>
        </P1>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_schedule_insert",
        effect_type="inserted",
        applied=True,
        requires_applied=False,
        modified="2015-01-01",
        affected_uri="/id/ukpga/2003/31",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2003",
        affected_number="31",
        affected_provisions="Sch. 2",
        affecting_uri="/id/ukpga/2015/9",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2015",
        affecting_number="9",
        affecting_provisions="s. 73(2)",
        affecting_title="Deregulation Act 2015",
        in_force_dates=[{"date": "2015-03-26", "prospective": "false"}],
    )

    lowering_records: list[dict[str, Any]] = []

    ops = compile_effect_to_ir_ops(
        effect,
        extracted_el,
        sequence=0,
        lowering_rejections_out=lowering_records,
    )

    assert len(ops) == 1
    assert ops[0].payload is not None
    assert ops[0].payload.kind == IRNodeKind.SCHEDULE
    assert ops[0].payload.label == "2"
    assert [child.kind for child in ops[0].payload.children] == [IRNodeKind.PARAGRAPH]
    assert "uk_effect_broad_schedule_flat_payload_rejected" not in {
        record["rule_id"] for record in lowering_records
    }


def test_compile_rejects_flat_text_as_whole_schedule_replacement_payload() -> None:
    extracted_el = ET.fromstring(
        f"""
        <BlockAmendment xmlns="{_LEG_NS}">
          <Text>6 Recovery of grants from voluntary organisations Expenditure on grants to voluntary organisations</Text>
        </BlockAmendment>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_flat_schedule_replace_payload",
        effect_type="substituted",
        applied=True,
        requires_applied=True,
        modified="2013-10-11",
        affected_uri="/id/asp/2000/2",
        affected_class="ScottishAct",
        affected_year="2000",
        affected_number="2",
        affected_provisions="Sch. 2",
        affecting_uri="/id/ssi/2000/307",
        affecting_class="ScottishStatutoryInstrument",
        affecting_year="2000",
        affecting_number="307",
        affecting_provisions="art. 2",
        affecting_title="Budget (Scotland) Act 2000 Amendment Order 2000",
        comments="",
        in_force_dates=[],
    )
    lowering_records: list[dict[str, Any]] = []

    ops = compile_effect_to_ir_ops(
        effect,
        extracted_el,
        sequence=0,
        lowering_rejections_out=lowering_records,
    )

    assert ops == []
    rejection = lowering_records[0]
    assert rejection["rule_id"] == "uk_effect_broad_schedule_flat_payload_rejected"
    assert rejection["family"] == "payload_coverage_filter"
    assert rejection["reason_code"] == "broad_schedule_or_part_replace_payload_undercovered"
    assert rejection["target"] == "schedule:2"
    assert rejection["blocking"] is True
    assert rejection["strict_disposition"] == "block"


def test_compile_empty_effect_type_as_if_words_omitted_does_not_repeal_broad_section() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P1 xmlns="{_LEG_NS}" id="article-2">
          <Pnumber>2</Pnumber>
          <P1para>
            <Text>Section 11(9) of the Act shall have effect in relation to the
            financial year prior to commencement as if the words “use of
            resources and” were omitted.</Text>
          </P1para>
        </P1>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_empty_type_as_if_words_omitted",
        effect_type="",
        applied=True,
        requires_applied=True,
        modified="2018-04-06",
        affected_uri="/id/asp/2000/1",
        affected_class="ScottishAct",
        affected_year="2000",
        affected_number="1",
        affected_provisions="s. 11",
        affecting_uri="/id/ssi/2000/11",
        affecting_class="ScottishStatutoryInstrument",
        affecting_year="2000",
        affecting_number="11",
        affecting_provisions="reg. 2",
        affecting_title="Test Transitional Order",
        comments="",
        in_force_dates=[],
    )
    lowering_records: list[dict[str, Any]] = []

    ops = compile_effect_to_ir_ops(
        effect,
        extracted_el,
        sequence=0,
        lowering_rejections_out=lowering_records,
    )

    assert ops == []
    rejection = lowering_records[0]
    assert rejection["rule_id"] == "uk_effect_empty_type_as_if_words_omitted_rejected"
    assert rejection["family"] == "temporal_recovery"
    assert rejection["reason_code"] == "empty_effect_type_temporary_as_if_word_omission"
    assert rejection["blocking"] is True
    assert rejection["strict_disposition"] == "block"


def test_compile_synthesizes_local_eids_for_inserted_body_payload_descendants() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P3 xmlns="{_LEG_NS}" id="section-97-2-b">
          <Pnumber>b</Pnumber>
          <Text>after subsection (5) insert—</Text>
          <BlockAmendment>
            <P2>
              <Pnumber>5A</Pnumber>
              <P2para>
                <Text>Charges may be imposed on—</Text>
                <P3>
                  <Pnumber>a</Pnumber>
                  <Text>persons who disclose data,</Text>
                </P3>
                <P3>
                  <Pnumber>b</Pnumber>
                  <Text>persons who receive results.</Text>
                </P3>
                <P3>
                  <Pnumber>c</Pnumber>
                  <Text>persons who audit results.</Text>
                </P3>
              </P2para>
            </P2>
          </BlockAmendment>
        </P3>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_inserted_subsection_descendant_eids",
        effect_type="inserted",
        applied=True,
        requires_applied=True,
        modified="2021-10-12",
        affected_uri="/id/asp/2000/1",
        affected_class="ScottishAct",
        affected_year="2000",
        affected_number="1",
        affected_provisions="s. 11(5A)",
        affecting_uri="/id/asp/2010/13",
        affecting_class="ScottishAct",
        affecting_year="2010",
        affecting_number="13",
        affecting_provisions="s. 97(2)(b)",
        affecting_title="Criminal Justice and Licensing (Scotland) Act 2010",
        comments="",
        in_force_dates=[{"date": "2010-10-06", "prospective": "false"}],
    )
    lowering_records: list[dict[str, Any]] = []

    ops = compile_effect_to_ir_ops(
        effect,
        extracted_el,
        sequence=0,
        lowering_rejections_out=lowering_records,
    )

    assert len(ops) == 1
    payload = ops[0].payload
    assert payload is not None
    assert payload.attrs["eId"] == "section-11-5a"
    assert [child.attrs["eId"] for child in payload.children] == [
        "section-11-5a-a",
        "section-11-5a-b",
        "section-11-5a-c",
    ]
    assert lowering_records[0]["rule_id"] == "uk_payload_descendant_eid_synthesis"
    assert lowering_records[0]["strict_disposition"] == "record"


def test_compile_insert_uses_extracted_after_section_anchor() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P1 xmlns="{_LEG_NS}">
          <Pnumber>73</Pnumber>
          <Text>After section 97 of the 2003 Act, insert-</Text>
          <BlockAmendment>
            <P1 eId="section-97za">
              <Pnumber>97ZA</Pnumber>
              <Title>Meaning of creditor</Title>
              <Text>Inserted section text.</Text>
            </P1>
          </BlockAmendment>
        </P1>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_extracted_after_section_anchor",
        effect_type="inserted",
        applied=True,
        requires_applied=True,
        modified="2021-01-06",
        affected_uri="/id/asp/2003/2",
        affected_class="ScottishAct",
        affected_year="2003",
        affected_number="2",
        affected_provisions="s. 97ZA",
        affecting_uri="/id/asp/2015/6",
        affecting_class="ScottishAct",
        affecting_year="2015",
        affecting_number="6",
        affecting_provisions="s. 73",
        affecting_title="Community Empowerment (Scotland) Act 2015",
        comments="",
        in_force_dates=[{"date": "2021-02-24", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.INSERT
    assert ops[0].target == LegalAddress((("section", "97za"),))
    assert f"{_NOTE_PRECEDING_EID}section-97" in ops[0].provenance_tags


def test_compile_inserted_section_p1group_title_becomes_heading_carrier() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P1 xmlns="{_LEG_NS}">
          <Pnumber>26</Pnumber>
          <Text>After section 16 insert-</Text>
          <BlockAmendment>
            <P1group>
              <Title>Committal for sentence of young offenders</Title>
              <P1 eId="section-16A">
                <Pnumber>16A</Pnumber>
                <Text>Inserted section text.</Text>
              </P1>
            </P1group>
          </BlockAmendment>
        </P1>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_inserted_section_p1group_heading",
        effect_type="inserted",
        applied=True,
        requires_applied=True,
        modified="2021-01-06",
        affected_uri="/id/ukpga/2020/17",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2020",
        affected_number="17",
        affected_provisions="s. 16A",
        affecting_uri="/id/ukpga/2021/11",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2021",
        affecting_number="11",
        affecting_provisions="Sch. 13 para. 26(2)",
        affecting_title="Counter-Terrorism and Sentencing Act 2021",
        comments="",
        in_force_dates=[{"date": "2021-06-29", "prospective": "false"}],
    )
    lowering_records: list[dict[str, Any]] = []

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0, lowering_rejections_out=lowering_records)

    assert len(ops) == 1
    payload = ops[0].payload
    assert payload is not None
    assert payload.kind is IRNodeKind.SECTION
    assert payload.label == "16A"
    assert payload.children[0].kind is IRNodeKind.HEADING
    assert payload.children[0].text == "Committal for sentence of young offenders"
    assert payload.children[0].attrs == {
        "source_tag": "P1group",
        "source_rule_id": "uk_inserted_section_p1group_heading_carrier",
    }
    assert lowering_records[0]["rule_id"] == "uk_effect_inserted_section_p1group_heading_carrier_lowered"
    assert lowering_records[0]["family"] == "payload_normalization"
    assert lowering_records[0]["blocking"] is False


def test_compile_inserted_section_without_p1group_title_does_not_invent_heading_carrier() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P1 xmlns="{_LEG_NS}">
          <Pnumber>10</Pnumber>
          <Text>After section 20 insert-</Text>
          <BlockAmendment>
            <P1 eId="section-20A">
              <Pnumber>20A</Pnumber>
              <Text>Inserted section text.</Text>
            </P1>
          </BlockAmendment>
        </P1>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_inserted_section_no_p1group_heading",
        effect_type="inserted",
        applied=True,
        requires_applied=True,
        modified="2016-01-01",
        affected_uri="/id/ukpga/2010/1",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2010",
        affected_number="1",
        affected_provisions="s. 20A",
        affecting_uri="/id/ukpga/2016/1",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2016",
        affecting_number="1",
        affecting_provisions="s. 10",
        affecting_title="Test Act",
        comments="",
        in_force_dates=[{"date": "2016-01-01", "prospective": "false"}],
    )
    lowering_records: list[dict[str, Any]] = []

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0, lowering_rejections_out=lowering_records)

    assert len(ops) == 1
    assert ops[0].payload is not None
    assert [child.kind for child in ops[0].payload.children] == []
    assert lowering_records == []


def test_compile_grouped_insert_chains_extracted_after_section_anchor() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P1 xmlns="{_LEG_NS}">
          <Pnumber>10</Pnumber>
          <Text>After section 20 of the 2003 Act, insert-</Text>
          <BlockAmendment>
            <P1 eId="section-20a">
              <Pnumber>20A</Pnumber>
              <Text>First inserted section.</Text>
            </P1>
            <P1 eId="section-20b">
              <Pnumber>20B</Pnumber>
              <Text>Second inserted section.</Text>
            </P1>
            <P1 eId="section-20c">
              <Pnumber>20C</Pnumber>
              <Text>Third inserted section.</Text>
            </P1>
          </BlockAmendment>
        </P1>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_grouped_after_section_anchor",
        effect_type="inserted",
        applied=True,
        requires_applied=True,
        modified="2016-01-01",
        affected_uri="/id/asp/2003/2",
        affected_class="ScottishAct",
        affected_year="2003",
        affected_number="2",
        affected_provisions="s. 20A 20B 20C",
        affecting_uri="/id/asp/2016/18",
        affecting_class="ScottishAct",
        affecting_year="2016",
        affecting_number="18",
        affecting_provisions="s. 10",
        affecting_title="Test Act",
        comments="",
        in_force_dates=[{"date": "2016-01-01", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert [op.target for op in ops] == [
        LegalAddress((("section", "20a"),)),
        LegalAddress((("section", "20b"),)),
        LegalAddress((("section", "20c"),)),
    ]
    assert f"{_NOTE_PRECEDING_EID}section-20" in ops[0].provenance_tags
    assert f"{_NOTE_PRECEDING_EID}section-20a" in ops[1].provenance_tags
    assert f"{_NOTE_PRECEDING_EID}section-20b" in ops[2].provenance_tags


def test_replay_source_anchored_section_order_is_observed_not_shape_gap() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P1 xmlns="{_LEG_NS}">
          <Pnumber>73</Pnumber>
          <Text>After section 97 of the 2003 Act, insert-</Text>
          <BlockAmendment>
            <P1 eId="section-97za">
              <Pnumber>97ZA</Pnumber>
              <Title>Meaning of creditor</Title>
              <Text>Inserted section text.</Text>
            </P1>
          </BlockAmendment>
        </P1>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_source_anchored_section_order",
        effect_type="inserted",
        applied=True,
        requires_applied=True,
        modified="2021-01-06",
        affected_uri="/id/asp/2003/2",
        affected_class="ScottishAct",
        affected_year="2003",
        affected_number="2",
        affected_provisions="s. 97ZA",
        affecting_uri="/id/asp/2015/6",
        affecting_class="ScottishAct",
        affecting_year="2015",
        affecting_number="6",
        affecting_provisions="s. 73",
        affecting_title="Community Empowerment (Scotland) Act 2015",
        comments="",
        in_force_dates=[{"date": "2021-02-24", "prospective": "false"}],
    )
    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)
    base = IRStatute(
        statute_id="asp/2003/2",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            children=(
                IRNode(
                    kind=IRNodeKind.P1GROUP,
                    label=None,
                    children=(
                        IRNode(kind=IRNodeKind.SECTION, label="97", attrs={"eId": "section-97"}),
                        IRNode(kind=IRNodeKind.SECTION, label="97A", attrs={"eId": "section-97A"}),
                    ),
                ),
            ),
        ),
    )
    adjudications: list[CompileAdjudication] = []

    replayed = replay_uk_ops(base, ops, adjudications_out=adjudications)

    assert [child.label for child in replayed.body.children[0].children] == ["97", "97ZA", "97A"]
    assert "uk_replay_section_order_shape_gap" not in {adjudication.kind for adjudication in adjudications}
    assert [adjudication.kind for adjudication in adjudications] == [
        "uk_replay_source_anchored_order_observed"
    ]
    assert adjudications[0].detail["blocking"] is False


def test_split_metadata_provisions_carries_active_subsection_context() -> None:
    assert _split_metadata_provisions("s. 90(2), paragraph (b) is repealed") == [
        "s. 90(2)",
        "s. 90(2)(b)",
    ]


def test_split_metadata_schedule_space_separated_sibling_paragraphs() -> None:
    assert _split_metadata_provisions("sch. 5 para. 11 12") == [
        "sch. 5 para. 11",
        "sch. 5 para. 12",
    ]


def test_split_metadata_schedule_space_separated_alnum_sibling_paragraphs() -> None:
    assert _split_metadata_provisions("sch. 2A para. 9B 9C and cross-headings") == [
        "sch. 2A para. 9B",
        "sch. 2A para. 9C",
    ]


def test_compile_repealed_paragraph_ref_inherits_active_subsection_context() -> None:
    effect = UKEffectRecord(
        effect_id="uk_test_repealed_paragraph_ref_inherits_subsection",
        effect_type="repealed",
        applied=True,
        requires_applied=False,
        modified="2011-01-01",
        affected_uri="/id/asp/2011/9",
        affected_class="ScottishAct",
        affected_year="2011",
        affected_number="9",
        affected_provisions="s. 90(2), paragraph (b) is repealed",
        affecting_uri="/id/asp/2011/9",
        affecting_class="ScottishAct",
        affecting_year="2011",
        affecting_number="9",
        affecting_provisions="s. 90(2)",
        affecting_title="Test Act",
        in_force_dates=[{"date": "2011-01-01", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el=None, sequence=0)
    target_paths = [op.target.path for op in ops]

    assert len(ops) == 2
    assert (("section", "90"), ("subsection", "2"), ("paragraph", "b")) in target_paths
    assert (("section", "90"), ("subsection", "2")) in target_paths
    assert (("section", "90"), ("subsection", "b")) not in target_paths


def test_compile_nested_schedule_subparagraph_paragraph_repeal_not_sibling_expanded() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P4 xmlns="{_LEG_NS}">
          <Text>in sub-paragraph (2), paragraph (b) is repealed,</Text>
        </P4>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_nested_schedule_subparagraph_paragraph_repeal",
        effect_type="repealed",
        applied=True,
        requires_applied=False,
        modified="2010-10-01",
        affected_uri="/id/asp/2000/1/schedule/2/paragraph/7/2/b",
        affected_class="ScottishAct",
        affected_year="2000",
        affected_number="1",
        affected_provisions="sch. 2 para. 7(2)(b)",
        affecting_uri="/id/asp/2010/8",
        affecting_class="ScottishAct",
        affecting_year="2010",
        affecting_number="8",
        affecting_provisions="s. 118(8)(e)(i)",
        affecting_title="Test Act",
        in_force_dates=[{"date": "2010-10-01", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 1
    assert ops[0].target.path == (
        ("schedule", "2"),
        ("paragraph", "7"),
        ("subparagraph", "2"),
        ("item", "b"),
    )


def test_compile_schedule_space_separated_sibling_paragraph_targets() -> None:
    extracted_el = ET.fromstring(
        f"""
        <BlockAmendment xmlns="{_LEG_NS}">
          <P1>
            <Pnumber>11</Pnumber>
            <Text>Paragraph 11 text.</Text>
          </P1>
          <P1>
            <Pnumber>12</Pnumber>
            <Text>Paragraph 12 text.</Text>
          </P1>
        </BlockAmendment>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_schedule_space_sibling_paragraphs",
        effect_type="substituted",
        applied=True,
        requires_applied=False,
        modified="2015-04-01",
        affected_uri="/id/asp/2013/11",
        affected_class="ScottishAct",
        affected_year="2013",
        affected_number="11",
        affected_provisions="sch. 5 para. 11 12",
        affecting_uri="/id/ssi/2015/123",
        affecting_class="ScottishStatutoryInstrument",
        affecting_year="2015",
        affecting_number="123",
        affecting_provisions="art. 8",
        affecting_title="Test SSI",
        in_force_dates=[{"date": "2015-04-01", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert [op.target.path for op in ops] == [
        (("schedule", "5"), ("paragraph", "11")),
        (("schedule", "5"), ("paragraph", "12")),
    ]


def test_compile_part_target_prefers_direct_part_number_over_descendant_section() -> None:
    extracted_el = ET.fromstring(
        f"""
        <BlockAmendment xmlns="{_LEG_NS}">
          <Part>
            <Number>PART A1</Number>
            <Title>Part A1</Title>
            <Pblock>
              <Title>Children: reporting obligations</Title>
              <P1>
                <Pnumber>A1A</Pnumber>
                <Text>Inserted section.</Text>
              </P1>
            </Pblock>
          </Part>
        </BlockAmendment>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_insert_part_a1",
        effect_type="inserted",
        applied=True,
        requires_applied=False,
        modified="2016-03-16",
        affected_uri="/id/ukpga/2010/9",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2010",
        affected_number="9",
        affected_provisions="Pt. A1",
        affecting_uri="/id/ukpga/2016/7",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2016",
        affecting_number="7",
        affecting_provisions="s. 5",
        affecting_title="Welfare Reform and Work Act 2016",
        in_force_dates=[{"date": "2016-03-16", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 1
    assert ops[0].payload is not None
    assert ops[0].payload.kind == IRNodeKind.PART
    assert [child.kind for child in ops[0].payload.children] == [IRNodeKind.CROSSHEADING]


def test_compile_unnumbered_schedule_target_preserves_schedule_shape() -> None:
    extracted_el = ET.fromstring(
        f"""
        <BlockAmendment xmlns="{_LEG_NS}">
          <Schedule eId="schedule">
            <Number>SCHEDULE</Number>
            <Title>Further provision</Title>
            <ScheduleBody>
              <P1 eId="schedule-paragraph-1">
                <Pnumber>1</Pnumber>
                <Text>Paragraph text.</Text>
              </P1>
            </ScheduleBody>
          </Schedule>
        </BlockAmendment>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_unnumbered_schedule_insert",
        effect_type="inserted",
        applied=True,
        requires_applied=False,
        modified="2006-11-08",
        affected_uri="/id/ukpga/2000/10",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="10",
        affected_provisions="Sch.",
        affecting_uri="/id/ukpga/2006/48",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2006",
        affecting_number="48",
        affecting_provisions="s. 30(2)",
        affecting_title="Commissioner for Older People (Wales) Act 2006",
        in_force_dates=[{"date": "2006-11-08", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 1
    assert ops[0].target.path == (("schedule", ""),)
    assert ops[0].payload is not None
    assert ops[0].payload.kind == IRNodeKind.SCHEDULE
    assert ops[0].payload.label == ""
    assert [child.kind for child in ops[0].payload.children] == [IRNodeKind.PARAGRAPH]


def test_compile_unlabelled_schedule_nested_target_selects_matching_sibling() -> None:
    extracted_el = ET.fromstring(
        f"""
        <BlockAmendment xmlns="{_LEG_NS}">
          <P2>
            <Pnumber>7</Pnumber>
            <Text>Subparagraph seven.</Text>
          </P2>
          <P2>
            <Pnumber>8</Pnumber>
            <Text>Subparagraph eight.</Text>
          </P2>
        </BlockAmendment>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_unlabelled_schedule_nested_target",
        effect_type="inserted",
        applied=True,
        requires_applied=False,
        modified="2024-05-24",
        affected_uri="/id/ukpga/2000/10",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="10",
        affected_provisions="Sch. para. 5(8)",
        affecting_uri="/id/ukpga/2024/13",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2024",
        affecting_number="13",
        affecting_provisions="s. 25(3)",
        affecting_title="Victims and Prisoners Act 2024",
        in_force_dates=[{"date": "2024-05-24", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 1
    assert ops[0].target.path == (("schedule", ""), ("paragraph", "5"), ("subparagraph", "8"))
    assert ops[0].payload is not None
    assert ops[0].payload.kind == IRNodeKind.SUBPARAGRAPH
    assert ops[0].payload.label == "8"
    assert "Subparagraph eight." in ops[0].payload.text


def test_compile_unlabelled_schedule_multi_sibling_ref_expands_targets() -> None:
    extracted_el = ET.fromstring(
        f"""
        <BlockAmendment xmlns="{_LEG_NS}">
          <P2>
            <Pnumber>7</Pnumber>
            <Text>Subparagraph seven.</Text>
          </P2>
          <P2>
            <Pnumber>8</Pnumber>
            <Text>Subparagraph eight.</Text>
          </P2>
        </BlockAmendment>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_unlabelled_schedule_multi_sibling_ref",
        effect_type="inserted",
        applied=True,
        requires_applied=False,
        modified="2024-05-24",
        affected_uri="/id/ukpga/2000/10",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="10",
        affected_provisions="Sch. para. 5(7)(8)",
        affecting_uri="/id/ukpga/2024/13",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2024",
        affecting_number="13",
        affecting_provisions="s. 25(3)",
        affecting_title="Victims and Prisoners Act 2024",
        in_force_dates=[{"date": "2024-05-24", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 2
    assert [op.target.path for op in ops] == [
        (("schedule", ""), ("paragraph", "5"), ("subparagraph", "7")),
        (("schedule", ""), ("paragraph", "5"), ("subparagraph", "8")),
    ]
    payloads = [payload for op in ops if (payload := op.payload) is not None]
    assert [payload.label for payload in payloads] == ["7", "8"]


def test_split_metadata_mixed_numeric_to_alphanumeric_range_keeps_middle_section() -> None:
    assert _split_metadata_provisions("s. 60-61A") == [
        "s. 60",
        "s. 61",
        "s. 61A",
    ]


def test_retarget_substituted_series_anchors_first_replacement_to_replaced_subsection() -> None:
    retargeted = _retarget_substituted_series_to_replaced_anchor(
        "substituted for s. 39(5)",
        ["s. 39(5A)", "s. 39(5B)", "s. 39(5C)"],
    )

    assert retargeted == ["s. 39(5)", "s. 39(5B)", "s. 39(5C)"]


def test_retarget_substituted_series_single_new_subsection_to_first_replaced_anchor() -> None:
    retargeted = _retarget_substituted_series_to_replaced_anchor(
        "substituted for s. 3(5)(6)",
        ["s. 3(5A)"],
    )

    assert retargeted == ["s. 3(5)"]


def test_repeal_tail_for_substituted_series_single_new_subsection() -> None:
    tail = _repeal_tail_for_substituted_series_replacement(
        "substituted for s. 3(5)(6)",
        ["s. 3(5A)"],
    )

    assert tail == ["s. 3(6)"]


def test_split_metadata_whitespace_compressed_sibling_subsections() -> None:
    assert _split_metadata_provisions("s. 62(7) (8)") == [
        "s. 62(7)",
        "s. 62(8)",
    ]


def test_split_metadata_parenthesized_stemmed_alnum_range() -> None:
    assert _split_metadata_provisions("s. 33(1ZA)-(1ZF)") == [
        "s. 33(1ZA)",
        "s. 33(1ZB)",
        "s. 33(1ZC)",
        "s. 33(1ZD)",
        "s. 33(1ZE)",
        "s. 33(1ZF)",
    ]


def test_split_metadata_schedule_range_plus_trailing_sibling() -> None:
    assert _split_metadata_provisions("Sch. 7 para. 10(1)-(1D) (2)") == [
        "Sch. 7 para. 10(1)",
        "Sch. 7 para. 10(1A)",
        "Sch. 7 para. 10(1B)",
        "Sch. 7 para. 10(1C)",
        "Sch. 7 para. 10(1D)",
        "Sch. 7 para. 10(2)",
    ]


def test_split_metadata_repeated_anchor_keeps_shared_schedule_stem() -> None:
    assert _split_metadata_provisions("Sch. 7 para. 10(4)(a) and 10(5)") == [
        "Sch. 7 para. 10(4)(a)",
        "Sch. 7 para. 10(5)",
    ]


def test_split_metadata_mixed_nested_schedule_sibling_shorthand() -> None:
    assert _split_metadata_provisions("Sch. 7 para. 10(4)(a)(5)") == [
        "Sch. 7 para. 10(4)(a)",
        "Sch. 7 para. 10(5)",
    ]


def test_split_metadata_nested_lettered_then_roman_schedule_item_stays_nested() -> None:
    assert _split_metadata_provisions("Sch. 10 para. 4(b)(ii)") == [
        "Sch. 10 para. 4(b)(ii)",
    ]


def test_split_metadata_nested_lettered_then_unrelated_alpha_suffix_stays_nested() -> None:
    assert _split_metadata_provisions("Sch. 9 para. 128(6)(a)(zi)") == [
        "Sch. 9 para. 128(6)(a)(zi)",
    ]


def test_split_metadata_lettered_alpha_suffix_sibling_family_still_expands() -> None:
    assert _split_metadata_provisions("Sch. 9 para. 128(6)(d)(da)") == [
        "Sch. 9 para. 128(6)(d)",
        "Sch. 9 para. 128(6)(da)",
    ]


def test_compile_before_anchor_insert_places_nested_alpha_label_before_named_sibling() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P2 xmlns="{_LEG_NS}">
          <Pnumber>13</Pnumber>
          <P2para>
            <Text>In paragraph 128, in sub-paragraph (6)(a), before sub-paragraph (i) insert-</Text>
            <P3>
              <Pnumber>zi</Pnumber>
              <P3para>
                <Text>the amount of the member's lifetime allowance previously-used amount is equal to or greater than the member's lifetime allowance,</Text>
              </P3para>
            </P3>
          </P2para>
        </P2>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_insert_128_6_a_zi",
        effect_type="inserted",
        applied=True,
        requires_applied=True,
        modified="2024-04-06",
        affected_uri="/id/ukpga/2024/3",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2024",
        affected_number="3",
        affected_provisions="Sch. 9 para. 128(6)(a)(zi)",
        affecting_uri="/id/uksi/2024/356",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2024",
        affecting_number="356",
        affecting_provisions="reg. 4(13)",
        affecting_title="Test Regulations",
        in_force_dates=[{"date": "2024-04-06", "prospective": "false"}],
    )
    ops = compile_effect_to_ir_ops(effect, extracted_el)
    statute = IRStatute(
        statute_id="ukpga/2024/3",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, children=()),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="9",
                attrs={"eId": "schedule-9"},
                children=(
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="128",
                        attrs={"eId": "schedule-9-paragraph-128"},
                        children=(
                            IRNode(
                                kind=IRNodeKind.SUBPARAGRAPH,
                                label="6",
                                attrs={"eId": "schedule-9-paragraph-128-6"},
                                children=(
                                    IRNode(
                                        kind=IRNodeKind.ITEM,
                                        label="a",
                                        attrs={"eId": "schedule-9-paragraph-128-6-a"},
                                        children=(
                                            IRNode(
                                                kind=IRNodeKind.ITEM,
                                                label="i",
                                                text="first item",
                                                attrs={"eId": "schedule-9-paragraph-128-6-a-i"},
                                            ),
                                            IRNode(
                                                kind=IRNodeKind.ITEM,
                                                label="ii",
                                                text="second item",
                                                attrs={"eId": "schedule-9-paragraph-128-6-a-ii"},
                                            ),
                                        ),
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )

    adjudications: list[CompileAdjudication] = []
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)
    for op in ops:
        executor.apply_op(op)

    assert len(ops) == 1
    assert ops[0].target.path == (
        ("schedule", "9"),
        ("paragraph", "128"),
        ("subparagraph", "6"),
        ("item", "a"),
        ("item", "zi"),
    )
    item_a = executor.statute.supplements[0].children[0].children[0].children[0]
    assert [child.label for child in item_a.children] == ["zi", "i", "ii"]
    assert item_a.children[0].attrs["eId"] == "schedule-9-paragraph-128-6-a-zi"
    assert [adjudication.kind for adjudication in adjudications] == [
        "uk_replay_source_anchored_order_observed"
    ]
    assert adjudications[0].detail["blocking"] is False


def test_extract_provision_bytes_keeps_enclosing_instruction_when_only_inline_amendment() -> None:
    xml_bytes = f"""
    <Legislation xmlns="{_LEG_NS}">
      <Body>
        <P1 id="section-57">
          <Pnumber>57</Pnumber>
          <P2 id="section-57-2">
            <Pnumber>2</Pnumber>
            <P2para>
              <Text>
                In subsection (4), for paragraphs (a) and (b) substitute
                <InlineAmendment>"in the presence of a representative of the postal operator"</InlineAmendment>.
              </Text>
            </P2para>
          </P2>
        </P1>
      </Body>
    </Legislation>
    """.encode()

    extracted = extract_provision_element_from_bytes(xml_bytes, "s. 57(2)")

    assert extracted is not None
    assert extracted.tag.split("}")[-1] == "P2"


def test_parse_ref_preserves_regulation_kind_for_secondary_legislation() -> None:
    assert _parse_ref("reg. 46(2)") == (("regulation", "46"), (None, "2"))
    assert _parse_ref("regs. 1(2)") == (("regulation", "1"), (None, "2"))


def test_extract_provision_bytes_keeps_block_substitution_instruction_context() -> None:
    xml_bytes = f"""
    <Legislation xmlns="{_LEG_NS}">
      <Body>
        <P1 id="regulation-46">
          <Pnumber>46</Pnumber>
          <P1para>
            <P2 id="regulation-46-2">
              <Pnumber>2</Pnumber>
              <P2para>
                <Text>In section 25, in subsection (2), for "with" to the end substitute—</Text>
                <BlockAmendment>
                  <UnorderedList Decoration="none">
                    <ListItem>
                      <Para><Text>that is a works contract with an estimated value equal to or greater than £2,000,000.</Text></Para>
                    </ListItem>
                  </UnorderedList>
                </BlockAmendment>
              </P2para>
            </P2>
          </P1para>
        </P1>
      </Body>
    </Legislation>
    """.encode()

    extracted = extract_provision_element_from_bytes(xml_bytes, "reg. 46(2)")

    assert extracted is not None
    assert extracted.tag.split("}")[-1] == "P2"
    extracted_text = "".join(extracted.itertext())
    assert 'for "with" to the end substitute' in extracted_text
    assert "£2,000,000" in extracted_text


def test_extract_provision_bytes_prefers_following_block_amendment_after_insert_leadin() -> None:
    xml_bytes = f"""
    <Legislation xmlns="{_LEG_NS}">
      <Body>
        <P1 id="schedule-4-paragraph-4">
          <P1para>
            <P2 id="schedule-4-paragraph-4-3">
              <Pnumber>3</Pnumber>
              <P2para><Text>After subsection (6) insert—</Text></P2para>
            </P2>
            <BlockAmendment>
              <P2>
                <Pnumber>7</Pnumber>
                <P2para><Text>Inserted subsection 7.</Text></P2para>
              </P2>
              <P2>
                <Pnumber>8</Pnumber>
                <P2para><Text>Inserted subsection 8.</Text></P2para>
              </P2>
            </BlockAmendment>
          </P1para>
        </P1>
      </Body>
    </Legislation>
    """.encode()

    extracted = extract_provision_element_from_bytes(xml_bytes, "Sch. 4 para. 4(3)")

    assert extracted is not None
    assert extracted.tag.split("}")[-1] == "BlockAmendment"
    assert "Inserted subsection 7." in ET.tostring(extracted, encoding="unicode")


def test_extract_provision_bytes_keeps_descendant_block_insert_instruction_context() -> None:
    xml_bytes = f"""
    <Legislation xmlns="{_LEG_NS}">
      <Body>
        <P1 id="regulation-42">
          <P1para>
            <P2 id="regulation-42-2">
              <Pnumber>2</Pnumber>
              <P2para>
                <Text>In section 171, in subsection (1), after the definition of "2013 Act" insert-</Text>
                <BlockAmendment>
                  <UnorderedList Class="Definition" Decoration="none">
                    <ListItem>
                      <Para><Text>"corporate joint committee" has the same meaning as in section 68 of this Act;</Text></Para>
                    </ListItem>
                  </UnorderedList>
                </BlockAmendment>
                <AppendText>.</AppendText>
              </P2para>
            </P2>
          </P1para>
        </P1>
      </Body>
    </Legislation>
    """.encode()

    extracted = extract_provision_element_from_bytes(xml_bytes, "reg. 42(2)")

    assert extracted is not None
    assert extracted.tag.split("}")[-1] == "P2"
    extracted_text = "".join(extracted.itertext())
    assert 'after the definition of "2013 Act" insert-' in extracted_text
    assert "corporate joint committee" in extracted_text


def test_compile_words_inserted_after_fragment_to_text_replace() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P3 xmlns="{_LEG_NS}" id="schedule-1-paragraph-83-a">
          <Pnumber>a</Pnumber>
          <Text>in sub-paragraph (1) and (2) after "approval" insert "or confirmed";</Text>
        </P3>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_words_inserted_after_fragment",
        effect_type="words inserted",
        applied=True,
        requires_applied=False,
        modified="2006-04-01",
        affected_uri="/id/ukpga/2000/21",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="21",
        affected_provisions="Sch. 7A para. 1(1)(2)",
        affecting_uri="/id/wsi/2005/3238",
        affecting_class="WelshStatutoryInstrument",
        affecting_year="2005",
        affecting_number="3238",
        affecting_provisions="Sch. 1 para. 83(a)",
        affecting_title="School Organisation Proposals by the National Council for Education and Training for Wales Regulations 2005",
        in_force_dates=[{"date": "2006-04-01", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 2
    assert [op.action.value for op in ops] == ["text_replace", "text_replace"]
    assert [op.payload for op in ops] == [None, None]
    assert [op.target.path for op in ops] == [
        (("schedule", "7a"), ("paragraph", "1"), ("subparagraph", "1")),
        (("schedule", "7a"), ("paragraph", "1"), ("subparagraph", "2")),
    ]
    patches = []
    for op in ops:
        assert op.text_patch is not None
        patches.append(op.text_patch)
    assert [patch.selector.match_text for patch in patches] == ["approval", "approval"]
    assert [patch.replacement for patch in patches] == [
        "approval or confirmed",
        "approval or confirmed",
    ]
    assert all(op.text_patch is not None for op in ops)


def test_compile_words_inserted_before_fragment_to_text_replace() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P1 xmlns="{_LEG_NS}" id="schedule-8-paragraph-6">
          <Pnumber>6</Pnumber>
          <Text>In section 106(7) (Wales: orders and regulations), before "may not" insert "or regulations under section 69F".</Text>
        </P1>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_words_inserted_before_fragment",
        effect_type="words inserted",
        applied=True,
        requires_applied=False,
        modified="2022-09-28",
        affected_uri="/id/ukpga/2000/22",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="22",
        affected_provisions="s. 106(7)",
        affecting_uri="/id/asc/2021/1",
        affecting_class="ActOfSeneddCymru",
        affecting_year="2021",
        affecting_number="1",
        affecting_provisions="Sch. 8 para. 6",
        affecting_title="Curriculum and Assessment (Wales) Act 2021",
        in_force_dates=[{"date": "2022-05-05", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].payload is None
    assert ops[0].target.path == (("section", "106"), ("subsection", "7"))
    assert ops[0].text_patch is not None
    assert ops[0].text_patch.selector.match_text == "may not"
    assert ops[0].text_patch.replacement == "or regulations under section 69F may not"
    assert ops[0].text_patch is not None


def test_compile_words_inserted_at_end_to_text_replace() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P2 xmlns="{_LEG_NS}" id="schedule-19-paragraph-5-4">
          <Pnumber>4</Pnumber>
          <Text>4 In subsection (1A), at the end insert "(subject to section 33A)".</Text>
        </P2>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_words_inserted_at_end",
        effect_type="words inserted",
        applied=True,
        requires_applied=False,
        modified="2023-07-13",
        affected_uri="/id/ukpga/2000/23",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="23",
        affected_provisions="s. 33(1A)",
        affecting_uri="/id/ukpga/2017/3",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2017",
        affecting_number="3",
        affecting_provisions="Sch. 19 para. 5(4)",
        affecting_title="Policing and Crime Act 2017",
        in_force_dates=[{"date": "2017-03-31", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].payload is None
    assert ops[0].target.path == (("section", "33"), ("subsection", "1a"))
    assert ops[0].text_patch is not None
    assert ops[0].text_patch.kind is TextPatchKindEnum.APPEND
    assert ops[0].text_patch.selector.match_text == "TEXT_END"
    assert ops[0].text_patch.replacement == "(subject to section 33A)"
    assert ops[0].text_patch is not None


def test_compile_words_inserted_at_end_of_that_paragraph_to_text_replace() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P3 xmlns="{_LEG_NS}" id="schedule-paragraph-8-2-b">
          <Pnumber>b</Pnumber>
          <Text>b at the end of that paragraph insert “or is Scottish Water,”.</Text>
        </P3>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_words_inserted_at_end_of_that_paragraph",
        effect_type="words inserted",
        applied=True,
        requires_applied=False,
        modified="2003-06-25",
        affected_uri="/id/asp/2001/10",
        affected_class="ScottishAct",
        affected_year="2001",
        affected_number="10",
        affected_provisions="s. 11(1)(b)",
        affecting_uri="/id/ssi/2003/331",
        affecting_class="ScottishStatutoryInstrument",
        affecting_year="2003",
        affecting_number="331",
        affecting_provisions="Sch. para. 8(2)(b)",
        affecting_title="Test Amendment Regulations",
        in_force_dates=[{"date": "2003-06-25", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].payload is None
    assert ops[0].target.path == (
        ("section", "11"),
        ("subsection", "1"),
        ("paragraph", "b"),
    )
    assert ops[0].text_patch is not None
    assert ops[0].text_patch.kind is TextPatchKindEnum.APPEND
    assert ops[0].text_patch.selector.match_text == "TEXT_END"
    assert ops[0].text_patch.replacement == "or is Scottish Water,"


def test_compile_words_substituted_from_quoted_anchor_to_end_with_block_payload() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P2 xmlns="{_LEG_NS}" id="regulation-46-2">
          <Pnumber>2</Pnumber>
          <P2para>
            <Text>In section 25, in subsection (2), for “with” to the end substitute—</Text>
            <BlockAmendment>
              <UnorderedList Decoration="none">
                <ListItem>
                  <Para><Text>that is a works contract with an estimated value equal to or greater than £2,000,000.</Text></Para>
                </ListItem>
              </UnorderedList>
            </BlockAmendment>
          </P2para>
        </P2>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_quoted_anchor_to_end_block_substitution",
        effect_type="words substituted",
        applied=True,
        requires_applied=False,
        modified="2025-02-24",
        affected_uri="/id/asc/2023/1",
        affected_class="WelshAct",
        affected_year="2023",
        affected_number="1",
        affected_provisions="s. 25(2)",
        affecting_uri="/id/wsi/2024/782",
        affecting_class="WelshStatutoryInstrument",
        affecting_year="2024",
        affecting_number="782",
        affecting_provisions="reg. 46(2)",
        affecting_title="The Procurement (Wales) Regulations 2024",
        in_force_dates=[{"date": "2025-02-24", "prospective": "false"}],
    )
    lowering_rejections: list[dict[str, Any]] = []

    ops = compile_effect_to_ir_ops(
        effect,
        extracted_el,
        sequence=0,
        lowering_rejections_out=lowering_rejections,
    )

    assert lowering_rejections == []
    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].target.path == (("section", "25"), ("subsection", "2"))
    assert ops[0].text_patch is not None
    assert ops[0].text_patch.selector.match_text == "TEXT_FROM_with_TO_END"
    assert ops[0].text_patch.replacement == (
        "that is a works contract with an estimated value equal to or greater than £2,000,000."
    )
    assert _fragment_substitution(ops[0]) == [
        {
            "original": "TEXT_FROM_with_TO_END",
            "replacement": (
                "that is a works contract with an estimated value equal to or greater than £2,000,000."
            ),
        }
    ]
    assert (
        f"{_NOTE_TEXT_REWRITE_RULE}uk_effect_quoted_anchor_to_end_block_substitution_text_patch"
        in ops[0].provenance_tags
    )


def test_compile_words_inserted_after_definition_with_block_payload() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P2 xmlns="{_LEG_NS}" id="regulation-42-2">
          <Pnumber>2</Pnumber>
          <P2para>
            <Text>In section 171 (interpretation), in subsection (1), after the definition of “2013 Act” insert—</Text>
            <BlockAmendment>
              <UnorderedList Class="Definition" Decoration="none">
                <ListItem>
                  <Para><Text>“corporate joint committee” has the same meaning as in section 68 of this Act;</Text></Para>
                </ListItem>
              </UnorderedList>
            </BlockAmendment>
            <AppendText>.</AppendText>
          </P2para>
        </P2>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_after_definition_block_insertion",
        effect_type="words inserted",
        applied=True,
        requires_applied=True,
        modified="2025-10-04",
        affected_uri="/id/asc/2021/1",
        affected_class="WelshAct",
        affected_year="2021",
        affected_number="1",
        affected_provisions="s. 171(1)",
        affecting_uri="/id/wsi/2021/1349",
        affecting_class="WelshStatutoryInstrument",
        affecting_year="2021",
        affecting_number="1349",
        affecting_provisions="reg. 42(2)",
        affecting_title="The Local Government and Elections (Wales) Act 2021 (Consequential Amendments) Regulations 2021",
        in_force_dates=[{"date": "2021-12-03", "prospective": "false"}],
    )
    lowering_rejections: list[dict[str, Any]] = []

    ops = compile_effect_to_ir_ops(
        effect,
        extracted_el,
        sequence=0,
        lowering_rejections_out=lowering_rejections,
    )

    assert lowering_rejections == []
    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].target.path == (("section", "171"), ("subsection", "1"))
    assert ops[0].text_patch is not None
    assert ops[0].text_patch.selector.match_text == "TEXT_AFTER_DEFINITION_2013 Act"
    assert ops[0].text_patch.replacement == (
        "“corporate joint committee” has the same meaning as in section 68 of this Act;"
    )
    assert (
        f"{_NOTE_TEXT_REWRITE_RULE}uk_effect_after_definition_text_insertion_patch"
        in ops[0].provenance_tags
    )

    base = IRStatute(
        statute_id="asc/2021/1",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="171",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1",
                            text="“2013 Act” means the Local Government (Democracy) (Wales) Act 2013; “existing” means existing text;",
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )

    replayed = replay_uk_ops(base, ops)
    replayed_text = replayed.body.children[0].children[0].text
    assert replayed_text == (
        "“2013 Act” means the Local Government (Democracy) (Wales) Act 2013; "
        "“corporate joint committee” has the same meaning as in section 68 of this Act; "
        "“existing” means existing text;"
    )


def test_compile_words_inserted_after_definition_with_optional_article() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P3 xmlns="{_LEG_NS}" id="schedule-paragraph-3-2-a">
          <Pnumber>a</Pnumber>
          <Text>a after the definition of the “2002 Act” insert— “the 2011 Regulations” means the Civil Jurisdiction and Judgments (Maintenance) Regulations 2011 ( S.I. 2011/1484 ); ;</Text>
        </P3>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_after_definition_optional_article",
        effect_type="words inserted",
        applied=True,
        requires_applied=True,
        modified="2024-01-30",
        affected_uri="/id/asp/2007/3",
        affected_class="ScottishAct",
        affected_year="2007",
        affected_number="3",
        affected_provisions="s. 221",
        affecting_uri="/id/ssi/2012/301",
        affecting_class="ScottishStatutoryInstrument",
        affecting_year="2012",
        affecting_number="301",
        affecting_provisions="Sch. para. 3(2)(a)",
        affecting_title="Test Regulations",
        in_force_dates=[{"date": "2012-11-06", "prospective": "false"}],
    )
    lowering_records: list[dict[str, Any]] = []

    ops = compile_effect_to_ir_ops(
        effect,
        extracted_el,
        sequence=0,
        lowering_rejections_out=lowering_records,
    )

    assert lowering_records == []
    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].target.path == (("section", "221"),)
    assert ops[0].text_patch is not None
    assert ops[0].text_patch.selector.match_text == "TEXT_AFTER_DEFINITION_2002 Act"
    assert ops[0].text_patch.replacement == (
        "“the 2011 Regulations” means the Civil Jurisdiction and Judgments "
        "(Maintenance) Regulations 2011 ( S.I. 2011/1484 ); ;"
    )
    assert (
        f"{_NOTE_TEXT_REWRITE_RULE}uk_effect_after_definition_text_insertion_patch"
        in ops[0].provenance_tags
    )


def test_compile_words_inserted_after_definitions_with_block_payload() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P1 xmlns="{_LEG_NS}" id="section-106-8">
          <Pnumber>8</Pnumber>
          <P1para>
            <Text>In section 31 (interpretation), in subsection (1), after the definitions of “directed” and “intrusive” insert—</Text>
            <BlockAmendment>
              <UnorderedList Class="Definition" Decoration="none">
                <ListItem>
                  <Para><Text>“joint surveillance operation” means a case involving at least two police forces in Scotland working together;</Text></Para>
                </ListItem>
              </UnorderedList>
            </BlockAmendment>
            <AppendText>.</AppendText>
          </P1para>
        </P1>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_after_definitions_block_insertion",
        effect_type="words inserted",
        applied=True,
        requires_applied=True,
        modified="2025-10-04",
        affected_uri="/id/asp/2000/11",
        affected_class="ScottishAct",
        affected_year="2000",
        affected_number="11",
        affected_provisions="s. 31(1)",
        affecting_uri="/id/asp/2010/13",
        affecting_class="ScottishAct",
        affecting_year="2010",
        affecting_number="13",
        affecting_provisions="s. 106(8)",
        affecting_title="Criminal Justice and Licensing (Scotland) Act 2010",
        in_force_dates=[{"date": "2011-03-22", "prospective": "false"}],
    )
    lowering_rejections: list[dict[str, Any]] = []

    ops = compile_effect_to_ir_ops(
        effect,
        extracted_el,
        sequence=0,
        lowering_rejections_out=lowering_rejections,
    )

    assert lowering_rejections == []
    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].target.path == (("section", "31"), ("subsection", "1"))
    assert ops[0].text_patch is not None
    assert ops[0].text_patch.selector.match_text == "TEXT_AFTER_DEFINITION_intrusive"
    assert ops[0].text_patch.replacement == (
        "“joint surveillance operation” means a case involving at least two "
        "police forces in Scotland working together;"
    )
    assert (
        f"{_NOTE_TEXT_REWRITE_RULE}uk_effect_after_definitions_text_insertion_patch"
        in ops[0].provenance_tags
    )

    base = IRStatute(
        statute_id="asp/2000/11",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="31",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1",
                            text=(
                                "“directed” means directed surveillance; "
                                "“intrusive” means intrusive surveillance; "
                                "“other” means other text;"
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )

    replayed = replay_uk_ops(base, ops)
    replayed_text = replayed.body.children[0].children[0].text
    assert replayed_text == (
        "“directed” means directed surveillance; "
        "“intrusive” means intrusive surveillance; "
        "“joint surveillance operation” means a case involving at least two "
        "police forces in Scotland working together; "
        "“other” means other text;"
    )


@pytest.mark.parametrize(
    (
        "source_text",
        "effect_type",
        "affected_provisions",
        "expected_match",
        "expected_replacement",
        "expected_occurrence",
        "expected_action",
        "expected_rule_id",
    ),
    [
        (
            "a at the beginning of subsection (1) insert “ Subject to subsection (4A), ” ,",
            "words inserted",
            "s. 49(1)",
            "TEXT_BEGINNING",
            "Subject to subsection (4A),",
            0,
            StructuralAction.TEXT_REPLACE,
            "uk_effect_beginning_text_insertion_patch",
        ),
        (
            "5 In section 22, in subsection (5), at the beginning of paragraph (b) "
            "insert “ except where the account and the report are published by the body, ” .",
            "words inserted",
            "s. 22(5)(b)",
            "TEXT_BEGINNING",
            "except where the account and the report are published by the body,",
            0,
            StructuralAction.TEXT_REPLACE,
            "uk_effect_beginning_text_insertion_patch",
        ),
        (
            "b in subsection (6), at the beginning there shall be inserted "
            "“ Subject to subsection (6A) below, ” ;",
            "words inserted",
            "s. 18(6)",
            "TEXT_BEGINNING",
            "Subject to subsection (6A) below,",
            0,
            StructuralAction.TEXT_REPLACE,
            "uk_effect_beginning_text_insertion_patch",
        ),
        (
            "b in subsection (4), after “(1)” there shall be inserted "
            "“ or subsection (1A) ” .",
            "words inserted",
            "s. 24(4)",
            "(1)",
            "(1) or subsection (1A) ",
            0,
            StructuralAction.TEXT_REPLACE,
            "uk_effect_after_quoted_anchor_insert_text_patch",
        ),
        (
            "b after the word “Act” there shall be inserted the words "
            "“ or under section 56 or 63 of the Title Conditions (Scotland) Act 2003 (asp 9) ” ;",
            "words inserted",
            "s. 25",
            "Act",
            "Act or under section 56 or 63 of the Title Conditions (Scotland) Act 2003 (asp 9) ",
            0,
            StructuralAction.TEXT_REPLACE,
            "uk_effect_after_quoted_anchor_insert_text_patch",
        ),
        (
            "a the word “or” at the end of paragraph (a) is repealed;",
            "word repealed",
            "s. 16(6)(a)",
            "or",
            "",
            -1,
            StructuralAction.TEXT_REPEAL,
            "uk_effect_final_quoted_word_repeal_text_patch",
        ),
        (
            "i the word “and” which appears immediately after paragraph (a) is repealed,",
            "word repealed",
            "s. 19(5)",
            "TEXT_WORD_and_IMMEDIATELY_FOLLOWING_paragraph_a",
            "",
            0,
            StructuralAction.TEXT_REPEAL,
            "uk_effect_contextual_adjacent_word_repeal_text_patch",
        ),
        (
            "i the word “not” is inserted after the word “is” where it second appears,",
            "word inserted",
            "sch. 2 para. 2",
            "is",
            "is not",
            2,
            StructuralAction.TEXT_REPLACE,
            "uk_effect_word_inserted_after_word_where_ordinal_text_patch",
        ),
        (
            "b in subsection (2), after “section” where it first occurs "
            "insert “ or any other section ” .",
            "words inserted",
            "s. 79(2)",
            "section",
            "section or any other section ",
            1,
            StructuralAction.TEXT_REPLACE,
            "uk_effect_after_quoted_anchor_where_ordinal_insert_text_patch",
        ),
        (
            "c repeal the words “or to the Scottish Crime and Drug Enforcement Agency”.",
            "words repealed",
            "s. 24(2)(b)",
            "or to the Scottish Crime and Drug Enforcement Agency",
            "",
            0,
            StructuralAction.TEXT_REPEAL,
            "uk_effect_repeal_quoted_words_text_patch",
        ),
        (
            "b in subsection (1)(a), for the words from “an”, where second occurring, "
            "to “surveillance” substitute “ the authorisation ” ,",
            "words substituted",
            "s. 16(1)(a)",
            "TEXT_FROM_an_TO_surveillance",
            "the authorisation",
            2,
            StructuralAction.TEXT_REPLACE,
            "uk_effect_range_occurrence_substitution_text_patch",
        ),
        (
            "7 In section 14(5)(a), for the words from “member” to “and” "
            "substitute constable of the Police Service; and aa another case.",
            "words substituted",
            "s. 14(5)(a)",
            "TEXT_FROM_member_TO_and",
            "constable of the Police Service; and aa another case.",
            0,
            StructuralAction.TEXT_REPLACE,
            "uk_effect_range_unquoted_substitution_text_patch",
        ),
        (
            "b in paragraph (c), omit the words “in other respects,”.",
            "words omitted",
            "s. 13(5)(c)",
            "in other respects,",
            "",
            0,
            StructuralAction.TEXT_REPEAL,
            "uk_effect_direct_quoted_word_omission_text_patch",
        ),
        (
            "3 In section 12(2)(a), immediately before the word “Audit” "
            "insert “ Public ” .",
            "word inserted",
            "s. 12(2)(a)",
            "Audit",
            "Public Audit",
            0,
            StructuralAction.TEXT_REPLACE,
            "uk_effect_immediately_before_word_insert_text_patch",
        ),
        (
            "a in paragraph 1, immediately before the word “Audit”, "
            "where it occurs for the second time, insert “ Public ” ,",
            "word inserted",
            "sch. 3 para. 1",
            "Audit",
            "Public Audit",
            2,
            StructuralAction.TEXT_REPLACE,
            "uk_effect_immediately_before_word_ordinal_insert_text_patch",
        ),
        (
            "2 In Part 1, after “A National Crime Agency officer” insert— "
            "A member of the Royal Navy Police.",
            "words inserted",
            "Sch. 3 Pt. 1",
            "A National Crime Agency officer",
            "A National Crime Agency officer A member of the Royal Navy Police.",
            0,
            StructuralAction.TEXT_REPLACE,
            "uk_effect_after_quoted_anchor_block_insert_text_patch",
        ),
        (
            "7 In section 47 (interpretation), after “Part—” insert— "
            "“ central institution ” means— the Bank of England;",
            "words inserted",
            "s. 47",
            "Part—",
            "Part— “ central institution ” means— the Bank of England;",
            0,
            StructuralAction.TEXT_REPLACE,
            "uk_effect_after_quoted_anchor_definition_entry_block_insert_text_patch",
        ),
        (
            "22 In Part 3, for “An officer of the department of the Secretary of State "
            "for Business, Energy and Industrial Strategy” substitute— "
            "An officer of the department of the Secretary of State for Business and Trade.",
            "words substituted",
            "Sch. 3 Pt. 3",
            (
                "An officer of the department of the Secretary of State for "
                "Business, Energy and Industrial Strategy"
            ),
            (
                "An officer of the department of the Secretary of State for "
                "Business and Trade."
            ),
            0,
            StructuralAction.TEXT_REPLACE,
            "uk_effect_quoted_anchor_block_substitution_text_patch",
        ),
        (
            "i leave out “a solicitor” and insert “ a practising solicitor ” ,",
            "words omitted",
            "s. 15(3)(c)",
            "a solicitor",
            "a practising solicitor",
            0,
            StructuralAction.TEXT_REPLACE,
            "uk_effect_leave_out_and_insert_text_patch",
        ),
        (
            "ii after “caution”, where last occurring, insert "
            "“ or to give such other security as the sheriff thinks fit ” ,",
            "words inserted",
            "s. 58(6)",
            "caution",
            "caution or to give such other security as the sheriff thinks fit ",
            -1,
            StructuralAction.TEXT_REPLACE,
            "uk_effect_after_quoted_anchor_last_occurrence_insert_text_patch",
        ),
        (
            "ii in paragraph (b), for the words “not exceeding one year from” "
            "substitute not exceeding— i one year; or ii 3 years, from ;",
            "words substituted",
            "s. 47(6)(b)",
            "not exceeding one year from",
            "not exceeding— i one year; or ii 3 years, from ;",
            0,
            StructuralAction.TEXT_REPLACE,
            "uk_effect_quoted_anchor_block_substitution_text_patch",
        ),
        (
            "b for “the adult”, where first occurring, substitute “ an adult with incapacity ” ,",
            "words substituted",
            "s. 64(1)",
            "the adult",
            "an adult with incapacity",
            1,
            StructuralAction.TEXT_REPLACE,
            "uk_effect_post_quoted_where_ordinal_substitution_text_patch",
        ),
        (
            "i after “association”, where it first occurs, insert "
            "“ (in this Part, the “professional association”) ” ,",
            "words inserted",
            "s. 63(1)(a)",
            "association",
            "association (in this Part, the “professional association”) ",
            1,
            StructuralAction.TEXT_REPLACE,
            "uk_effect_after_quoted_anchor_where_ordinal_nested_quote_insert_text_patch",
        ),
        (
            "a in sub-paragraph (3), for the words from “to the period” "
            "to the end of that sub-paragraph substitute — a in the case of "
            "a public body, the end date;",
            "words substituted",
            "s. 41(2)(a)(iii)",
            "TEXT_FROM_to the period_TO_END",
            "a in the case of a public body, the end date;",
            0,
            StructuralAction.TEXT_REPLACE,
            "uk_effect_anchor_to_end_block_substitution_text_patch",
        ),
        (
            "b in subsection (6), for the words from “the” where it first appears "
            "to the end substitute— a the Public Services Reform (Scotland) Act 2010 "
            "have the same meanings in that subsection as in that Act; .",
            "words substituted",
            "s. 58(6)",
            "TEXT_FROM_the_TO_END",
            (
                "a the Public Services Reform (Scotland) Act 2010 have the same "
                "meanings in that subsection as in that Act;"
            ),
            1,
            StructuralAction.TEXT_REPLACE,
            "uk_effect_range_to_end_ordinal_block_substitution_text_patch",
        ),
        (
            "in sub-paragraph (3), for the words from “, any of the following "
            "provisions of the ANO 2016” to the end, substitute “— a article "
            "265E(2)(b)(ii) of the ANO 2016; b regulation 3(5)(b). .",
            "words substituted",
            "Sch. 9 para. 1(3)",
            "TEXT_FROM_, any of the following provisions of the ANO 2016_TO_END",
            "a article 265E(2)(b)(ii) of the ANO 2016; b regulation 3(5)(b).",
            0,
            StructuralAction.TEXT_REPLACE,
            "uk_effect_range_to_end_open_quote_block_substitution_text_patch",
        ),
    ],
)
def test_compile_additional_frontier_text_patch_idioms(
    source_text: str,
    effect_type: str,
    affected_provisions: str,
    expected_match: str,
    expected_replacement: str,
    expected_occurrence: int,
    expected_action: StructuralAction,
    expected_rule_id: str,
) -> None:
    extracted_el = ET.fromstring(
        f"""
        <P3 xmlns="{_LEG_NS}" id="test-frontier-idiom">
          <Pnumber>x</Pnumber>
          <Text>{source_text}</Text>
        </P3>
        """
    )
    effect = UKEffectRecord(
        effect_id=f"uk_test_{expected_rule_id}",
        effect_type=effect_type,
        applied=True,
        requires_applied=True,
        modified="2025-10-04",
        affected_uri="/id/asp/2001/2",
        affected_class="ScottishAct",
        affected_year="2001",
        affected_number="2",
        affected_provisions=affected_provisions,
        affecting_uri="/id/asp/2019/17",
        affecting_class="ScottishAct",
        affecting_year="2019",
        affecting_number="17",
        affecting_provisions="sch. para. 3",
        affecting_title="Test Act",
        in_force_dates=[{"date": "2024-01-01", "prospective": "false"}],
    )
    lowering_rejections: list[dict[str, Any]] = []

    ops = compile_effect_to_ir_ops(
        effect,
        extracted_el,
        sequence=0,
        lowering_rejections_out=lowering_rejections,
    )

    assert lowering_rejections == []
    assert len(ops) == 1
    assert ops[0].action is expected_action
    assert ops[0].text_patch is not None
    assert ops[0].text_patch.selector.match_text == expected_match
    assert ops[0].text_patch.selector.occurrence == expected_occurrence
    if expected_action is StructuralAction.TEXT_REPEAL:
        assert ops[0].text_patch.replacement is None
    else:
        assert ops[0].text_patch.replacement == expected_replacement
    assert f"{_NOTE_TEXT_REWRITE_RULE}{expected_rule_id}" in ops[0].provenance_tags


def test_compile_grouped_anchor_occurrence_substitution_uses_parent_source_context() -> None:
    source_root = ET.fromstring(
        f"""
        <Legislation xmlns="{_LEG_NS}">
          <P3 id="schedule-1-paragraph-8-4-a">
            <Pnumber>a</Pnumber>
            <P3para>
              <Text>for “the Regulation of Care (Scotland) Act 2001”—</Text>
              <P4 id="schedule-1-paragraph-8-4-a-i">
                <Pnumber>i</Pnumber>
                <Text>the first time it appears, substitute “the Public Services Reform (Scotland) Act 2010”;</Text>
              </P4>
            </P3para>
          </P3>
        </Legislation>
        """
    )
    extracted_el = next(
        el for el in source_root.iter() if el.get("id") == "schedule-1-paragraph-8-4-a-i"
    )
    effect = UKEffectRecord(
        effect_id="uk_test_grouped_anchor_occurrence_substitution",
        effect_type="words substituted",
        applied=True,
        requires_applied=True,
        modified="2017-04-25",
        affected_uri="/id/asp/2000/4",
        affected_class="ScottishAct",
        affected_year="2000",
        affected_number="4",
        affected_provisions="Sch. 1 para. 1",
        affecting_uri="/id/ssi/2011/211",
        affecting_class="ScottishStatutoryInstrument",
        affecting_year="2011",
        affecting_number="211",
        affecting_provisions="Sch. 1 para. 8(4)(a)(i)",
        affecting_title="Test Regulations",
        in_force_dates=[{"date": "2011-04-01", "prospective": "false"}],
    )
    lowering_records: list[dict[str, Any]] = []

    ops = compile_effect_to_ir_ops(
        effect,
        extracted_el,
        sequence=0,
        lowering_rejections_out=lowering_records,
        source_root=source_root,
    )

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].target.path == (("schedule", "1"), ("paragraph", "1"))
    assert ops[0].text_patch is not None
    assert ops[0].text_patch.selector.match_text == "the Regulation of Care (Scotland) Act 2001"
    assert ops[0].text_patch.selector.occurrence == 1
    assert ops[0].text_patch.replacement == "the Public Services Reform (Scotland) Act 2010"
    assert (
        f"{_NOTE_TEXT_REWRITE_RULE}uk_effect_grouped_anchor_occurrence_substitution_text_patch"
        in ops[0].provenance_tags
    )
    assert [
        record["rule_id"]
        for record in lowering_records
        if record["rule_id"] == "uk_effect_grouped_anchor_occurrence_substitution_text_patch"
    ] == ["uk_effect_grouped_anchor_occurrence_substitution_text_patch"]
    assert next(
        record["source_parent_id"]
        for record in lowering_records
        if record["rule_id"] == "uk_effect_grouped_anchor_occurrence_substitution_text_patch"
    ) == "schedule-1-paragraph-8-4-a"


def test_compile_grouped_anchor_occurrence_substitution_does_not_invent_anchor_without_parent() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P4 xmlns="{_LEG_NS}" id="schedule-1-paragraph-8-4-a-i">
          <Pnumber>i</Pnumber>
          <Text>the first time it appears, substitute “the Public Services Reform (Scotland) Act 2010”;</Text>
        </P4>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_grouped_anchor_occurrence_substitution_no_parent",
        effect_type="words substituted",
        applied=True,
        requires_applied=True,
        modified="2017-04-25",
        affected_uri="/id/asp/2000/4",
        affected_class="ScottishAct",
        affected_year="2000",
        affected_number="4",
        affected_provisions="Sch. 1 para. 1",
        affecting_uri="/id/ssi/2011/211",
        affecting_class="ScottishStatutoryInstrument",
        affecting_year="2011",
        affecting_number="211",
        affecting_provisions="Sch. 1 para. 8(4)(a)(i)",
        affecting_title="Test Regulations",
        in_force_dates=[{"date": "2011-04-01", "prospective": "false"}],
    )
    lowering_records: list[dict[str, Any]] = []

    ops = compile_effect_to_ir_ops(
        effect,
        extracted_el,
        sequence=0,
        lowering_rejections_out=lowering_records,
    )

    assert ops == []
    assert "uk_effect_overlap_substitution_unlowered" in {
        record["rule_id"] for record in lowering_records
    }


def test_compile_labeled_end_range_refines_text_patch_target() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P4 xmlns="{_LEG_NS}" id="section-60-2-a-i">
          <Pnumber>i</Pnumber>
          <Text>i for the words from “shall” to the end of paragraph (b) substitute “ may ” ,</Text>
        </P4>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_labeled_end_range_target_refined",
        effect_type="words substituted",
        applied=True,
        requires_applied=True,
        modified="2008-04-01",
        affected_uri="/id/asp/2000/4",
        affected_class="ScottishAct",
        affected_year="2000",
        affected_number="4",
        affected_provisions="s. 58(6)",
        affecting_uri="/id/asp/2007/10",
        affecting_class="ScottishAct",
        affecting_year="2007",
        affecting_number="10",
        affecting_provisions="s. 60(2)(a)(i)",
        affecting_title="Adult Support and Protection (Scotland) Act 2007",
        in_force_dates=[{"date": "2008-04-01", "prospective": "false"}],
    )
    lowering_records: list[dict[str, Any]] = []

    ops = compile_effect_to_ir_ops(
        effect,
        extracted_el,
        sequence=0,
        lowering_rejections_out=lowering_records,
    )

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].target.path == (("section", "58"), ("subsection", "6"), ("paragraph", "b"))
    assert ops[0].text_patch is not None
    assert ops[0].text_patch.selector.match_text == "TEXT_FROM_shall_TO_END"
    assert ops[0].text_patch.replacement == "may"
    assert (
        f"{_NOTE_TEXT_REWRITE_RULE}uk_effect_labeled_end_range_substitution_text_patch"
        in ops[0].provenance_tags
    )
    assert [record["rule_id"] for record in lowering_records] == [
        "uk_effect_labeled_end_range_target_refined"
    ]
    assert lowering_records[0]["blocking"] is False
    assert lowering_records[0]["strict_disposition"] == "record"
    assert lowering_records[0]["original_target"] == "section:58/subsection:6"
    assert lowering_records[0]["refined_target"] == "section:58/subsection:6/paragraph:b"


def test_compile_labeled_end_range_blocks_incompatible_target_refinement() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P4 xmlns="{_LEG_NS}" id="schedule-1-paragraph-8-4-a-i">
          <Pnumber>i</Pnumber>
          <Text>i for the words from “shall” to the end of paragraph (b) substitute “ may ” ,</Text>
        </P4>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_labeled_end_range_target_refinement_rejected",
        effect_type="words substituted",
        applied=True,
        requires_applied=True,
        modified="2011-04-01",
        affected_uri="/id/asp/2000/4",
        affected_class="ScottishAct",
        affected_year="2000",
        affected_number="4",
        affected_provisions="Sch. 1 para. 1",
        affecting_uri="/id/ssi/2011/211",
        affecting_class="ScottishStatutoryInstrument",
        affecting_year="2011",
        affecting_number="211",
        affecting_provisions="Sch. 1 para. 8(4)(a)(i)",
        affecting_title="Test Regulations",
        in_force_dates=[{"date": "2011-04-01", "prospective": "false"}],
    )
    lowering_records: list[dict[str, Any]] = []

    ops = compile_effect_to_ir_ops(
        effect,
        extracted_el,
        sequence=0,
        lowering_rejections_out=lowering_records,
    )

    assert ops == []
    assert [record["rule_id"] for record in lowering_records] == [
        "uk_effect_labeled_end_range_target_refinement_rejected"
    ]
    assert lowering_records[0]["blocking"] is True
    assert lowering_records[0]["strict_disposition"] == "block"
    assert lowering_records[0]["target"] == "schedule:1/paragraph:1"
    assert lowering_records[0]["target_suffix_kind"] == "paragraph"
    assert lowering_records[0]["target_suffix_label"] == "b"


def test_compile_definition_entry_repeal_uses_definition_selector() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P4 xmlns="{_LEG_NS}" id="schedule-paragraph-3-6-a-iv">
          <Pnumber>iv</Pnumber>
          <Text>the definition of “quality contract” is repealed</Text>
        </P4>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_definition_entry_repeal",
        effect_type="words repealed",
        applied=True,
        requires_applied=True,
        modified="2024-01-23",
        affected_uri="/id/asp/2001/2",
        affected_class="ScottishAct",
        affected_year="2001",
        affected_number="2",
        affected_provisions="s. 48(1)",
        affecting_uri="/id/asp/2019/17",
        affecting_class="ScottishAct",
        affecting_year="2019",
        affecting_number="17",
        affecting_provisions="sch. para. 3(6)(a)(iv)",
        affecting_title="Transport (Scotland) Act 2019",
        in_force_dates=[{"date": "2023-12-04", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPEAL
    assert ops[0].target.path == (("section", "48"), ("subsection", "1"))
    assert ops[0].text_patch is not None
    assert ops[0].text_patch.selector.match_text == "TEXT_DEFINITION_ENTRY_quality contract"
    assert (
        f"{_NOTE_TEXT_REWRITE_RULE}uk_effect_definition_entry_repeal_text_patch"
        in ops[0].provenance_tags
    )

    base = IRStatute(
        statute_id="asp/2001/2",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="48",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1",
                            text=(
                                "“quality contract” means a contract scheme; "
                                "“quality partnership scheme” means a quality partnership scheme "
                                "or a quality contract scheme;"
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )

    replayed = replay_uk_ops(base, ops)
    assert replayed.body.children[0].children[0].text == (
        "“quality partnership scheme” means a quality partnership scheme "
        "or a quality contract scheme;"
    )


def test_compile_definition_anchor_comma_entry_insert_and_substitution() -> None:
    insert_el = ET.fromstring(
        f"""
        <P3 xmlns="{_LEG_NS}" id="schedule-4-paragraph-7-4-a">
          <Pnumber>a</Pnumber>
          <Text>a after the definition of “the 1988 Act”, insert— “ the 2016 Act ” means the Private Housing (Tenancies) (Scotland) Act 2016, ,</Text>
        </P3>
        """
    )
    substitute_el = ET.fromstring(
        f"""
        <P2 xmlns="{_LEG_NS}" id="schedule-2-paragraph-7-6">
          <Pnumber>6</Pnumber>
          <Text>6 In section 111, for the definition of “registered social landlord”, substitute— “ registered social landlord ” means a body registered in the register maintained under section 20(1) of the Housing (Scotland) Act 2010 (asp 17), .</Text>
        </P2>
        """
    )
    insert_effect = UKEffectRecord(
        effect_id="uk_test_definition_anchor_comma_insert",
        effect_type="words inserted",
        applied=True,
        requires_applied=True,
        modified="2017-12-01",
        affected_uri="/id/asp/2001/10",
        affected_class="ScottishAct",
        affected_year="2001",
        affected_number="10",
        affected_provisions="s. 111",
        affecting_uri="/id/asp/2016/19",
        affecting_class="ScottishAct",
        affecting_year="2016",
        affecting_number="19",
        affecting_provisions="Sch. 4 para. 7(4)(a)",
        affecting_title="Private Housing (Tenancies) (Scotland) Act 2016",
        in_force_dates=[{"date": "2017-12-01", "prospective": "false"}],
    )
    substitute_effect = UKEffectRecord(
        effect_id="uk_test_definition_anchor_comma_substitute",
        effect_type="words substituted",
        applied=True,
        requires_applied=True,
        modified="2012-04-01",
        affected_uri="/id/asp/2001/10",
        affected_class="ScottishAct",
        affected_year="2001",
        affected_number="10",
        affected_provisions="s. 111",
        affecting_uri="/id/asp/2010/17",
        affecting_class="ScottishAct",
        affecting_year="2010",
        affecting_number="17",
        affecting_provisions="Sch. 2 para. 7(6)",
        affecting_title="Housing (Scotland) Act 2010",
        in_force_dates=[{"date": "2012-04-01", "prospective": "false"}],
    )

    insert_ops = compile_effect_to_ir_ops(insert_effect, insert_el, sequence=0)
    substitute_ops = compile_effect_to_ir_ops(substitute_effect, substitute_el, sequence=1)

    assert len(insert_ops) == 1
    assert insert_ops[0].text_patch is not None
    assert insert_ops[0].text_patch.selector.match_text == "TEXT_AFTER_DEFINITION_the 1988 Act"
    assert (
        f"{_NOTE_TEXT_REWRITE_RULE}uk_effect_after_definition_text_insertion_patch"
        in insert_ops[0].provenance_tags
    )
    assert len(substitute_ops) == 1
    assert substitute_ops[0].text_patch is not None
    assert (
        substitute_ops[0].text_patch.selector.match_text
        == "TEXT_DEFINITION_ENTRY_registered social landlord"
    )
    assert (
        f"{_NOTE_TEXT_REWRITE_RULE}uk_effect_definition_entry_substitution_text_patch"
        in substitute_ops[0].provenance_tags
    )


def test_compile_multiple_definition_entry_repeals_preserves_each_selector() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P3 xmlns="{_LEG_NS}" id="section-114-4-a">
          <Pnumber>a</Pnumber>
          <Text>omit the definitions of “building safety risk” and “relevant risk”;</Text>
        </P3>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_multiple_definition_entry_repeal",
        effect_type="words omitted",
        applied=True,
        requires_applied=True,
        modified="2025-02-11",
        affected_uri="/id/ukpga/2022/30",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2022",
        affected_number="30",
        affected_provisions="Sch. 8 para. 1(1)",
        affecting_uri="/id/ukpga/2024/22",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2024",
        affecting_number="22",
        affecting_provisions="s. 114(4)(a)",
        affecting_title="Leasehold and Freehold Reform Act 2024",
        in_force_dates=[{"date": "2024-10-31", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 2
    assert [op.action for op in ops] == [
        StructuralAction.TEXT_REPEAL,
        StructuralAction.TEXT_REPEAL,
    ]
    assert [op.target.path for op in ops] == [
        (("schedule", "8"), ("paragraph", "1"), ("subparagraph", "1")),
        (("schedule", "8"), ("paragraph", "1"), ("subparagraph", "1")),
    ]
    assert [op.text_patch.selector.match_text for op in ops if op.text_patch is not None] == [
        "TEXT_DEFINITION_ENTRY_building safety risk",
        "TEXT_DEFINITION_ENTRY_relevant risk",
    ]
    assert all(
        f"{_NOTE_TEXT_REWRITE_RULE}uk_effect_definition_entry_repeal_text_patch"
        in op.provenance_tags
        for op in ops
    )

    base = IRStatute(
        statute_id="ukpga/2022/30",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(),
        ),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="8",
                children=(
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="1",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SUBPARAGRAPH,
                                label="1",
                                text=(
                                    "“building safety risk” means a safety risk; "
                                    "“kept definition” means a retained definition; "
                                    "“relevant risk” means a relevant risk;"
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )

    replayed = replay_uk_ops(base, ops)
    assert replayed.supplements[0].children[0].children[0].text == (
        "“kept definition” means a retained definition;"
    )


def test_compile_multiple_declarative_definition_entry_repeals_preserves_each_selector() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P2 xmlns="{_LEG_NS}" id="section-32-3">
          <Pnumber>3</Pnumber>
          <Text>3 In section 28(1)(interpretation), the definitions of “United Kingdom national” and “United Kingdom resident” are repealed.</Text>
        </P2>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_multiple_declarative_definition_entry_repeal",
        effect_type="words repealed",
        applied=True,
        requires_applied=True,
        modified="2021-10-12",
        affected_uri="/id/asp/2001/13",
        affected_class="ScottishAct",
        affected_year="2001",
        affected_number="13",
        affected_provisions="s. 28(1)",
        affecting_uri="/id/asp/2010/13",
        affecting_class="ScottishAct",
        affecting_year="2010",
        affecting_number="13",
        affecting_provisions="s. 32(3)",
        affecting_title="Criminal Justice and Licensing (Scotland) Act 2010",
        in_force_dates=[{"date": "2011-03-28", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 2
    assert [op.action for op in ops] == [
        StructuralAction.TEXT_REPEAL,
        StructuralAction.TEXT_REPEAL,
    ]
    assert [op.target.path for op in ops] == [
        (("section", "28"), ("subsection", "1")),
        (("section", "28"), ("subsection", "1")),
    ]
    assert [op.text_patch.selector.match_text for op in ops if op.text_patch is not None] == [
        "TEXT_DEFINITION_ENTRY_United Kingdom national",
        "TEXT_DEFINITION_ENTRY_United Kingdom resident",
    ]
    assert all(
        f"{_NOTE_TEXT_REWRITE_RULE}uk_effect_definition_entry_repeal_text_patch"
        in op.provenance_tags
        for op in ops
    )


def test_compile_definition_child_repeal_uses_bounded_definition_child_selector() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P1 xmlns="{_LEG_NS}" id="regulation-3">
          <Pnumber>3</Pnumber>
          <Text>In section 42, in subsection (2), in the definition of “relevant provision”, omit paragraph (d).</Text>
        </P1>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_definition_child_repeal",
        effect_type="words omitted",
        applied=True,
        requires_applied=True,
        modified="2022-04-22",
        affected_uri="/id/ukpga/2020/12",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2020",
        affected_number="12",
        affected_provisions="s. 42(2)",
        affecting_uri="/id/nisr/2021/259",
        affecting_class="NorthernIrelandStatutoryRule",
        affecting_year="2021",
        affecting_number="259",
        affecting_provisions="reg. 3",
        affecting_title="Test Regulations",
        in_force_dates=[{"date": "2021-09-29", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPEAL
    assert ops[0].target.path == (("section", "42"), ("subsection", "2"))
    assert ops[0].text_patch is not None
    assert (
        ops[0].text_patch.selector.match_text
        == "TEXT_DEFINITION_CHILD_PARAGRAPH_relevant provision\x1fd"
    )
    assert (
        f"{_NOTE_TEXT_REWRITE_RULE}uk_effect_definition_child_repeal_text_patch"
        in ops[0].provenance_tags
    )


def test_compile_definition_child_substitution_uses_bounded_definition_child_selector() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P3 xmlns="{_LEG_NS}" id="schedule-4-paragraph-242-c">
          <Pnumber>c</Pnumber>
          <Text>c in the definition of “review partner”, for paragraph (c) substitute— an integrated care board, or .</Text>
        </P3>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_definition_child_substitution",
        effect_type="words substituted",
        applied=True,
        requires_applied=True,
        modified="2025-07-10",
        affected_uri="/id/ukpga/2022/32",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2022",
        affected_number="32",
        affected_provisions="s. 36(1)",
        affecting_uri="/id/ukpga/2022/31",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2022",
        affecting_number="31",
        affecting_provisions="Sch. 4 para. 242(c)",
        affecting_title="Health and Care Act 2022",
        in_force_dates=[{"date": "2022-07-01", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].target.path == (("section", "36"), ("subsection", "1"))
    assert ops[0].text_patch is not None
    assert (
        ops[0].text_patch.selector.match_text
        == "TEXT_DEFINITION_CHILD_PARAGRAPH_review partner\x1fc"
    )
    assert ops[0].text_patch.replacement == "an integrated care board, or"
    assert (
        f"{_NOTE_TEXT_REWRITE_RULE}uk_effect_definition_child_substitution_text_patch"
        in ops[0].provenance_tags
    )


def test_compile_definition_range_to_end_substitution_uses_bounded_selector() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P3 xmlns="{_LEG_NS}" id="schedule-4-paragraph-9-5-a">
          <Pnumber>a</Pnumber>
          <Text>a in the definition of “mental disorder”, for the words from “means” to the end substitute “has the meaning given by section 328 of the 2003 Act” ;</Text>
        </P3>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_definition_range_to_end_substitution",
        effect_type="words substituted",
        applied=True,
        requires_applied=True,
        modified="2017-04-24",
        affected_uri="/id/asp/2000/4",
        affected_class="ScottishAct",
        affected_year="2000",
        affected_number="4",
        affected_provisions="s. 87(1)",
        affecting_uri="/id/asp/2003/13",
        affecting_class="ScottishAct",
        affecting_year="2003",
        affecting_number="13",
        affecting_provisions="Sch. 4 para. 9(5)(a)",
        affecting_title="Mental Health (Care and Treatment) (Scotland) Act 2003",
        in_force_dates=[{"date": "2005-10-05", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].target.path == (("section", "87"), ("subsection", "1"))
    assert ops[0].text_patch is not None
    assert (
        ops[0].text_patch.selector.match_text
        == "TEXT_IN_DEFINITION_mental disorder\x1fFROM\x1fmeans\x1fTO_END"
    )
    assert ops[0].text_patch.replacement == "has the meaning given by section 328 of the 2003 Act"
    assert (
        f"{_NOTE_TEXT_REWRITE_RULE}uk_effect_definition_range_to_end_substitution_text_patch"
        in ops[0].provenance_tags
    )


def test_compile_rejects_whole_act_metadata_when_source_names_external_act_target() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P1 xmlns="{_LEG_NS}" id="schedule-6-paragraph-17">
          <Pnumber>17</Pnumber>
          <Text>In Schedule 4 to the Town and Country Planning (Scotland) Act 1997, in paragraph 8(2), for the words from “an officer” to the end substitute “a member of the staff of the Scottish Administration.”</Text>
        </P1>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_external_act_whole_act_target",
        effect_type="",
        applied=True,
        requires_applied=True,
        modified="2018-02-21",
        affected_uri="/id/asp/2002/11",
        affected_class="ScottishAct",
        affected_year="2002",
        affected_number="11",
        affected_provisions="Act",
        affecting_uri="/id/asp/2002/11",
        affecting_class="ScottishAct",
        affecting_year="2002",
        affecting_number="11",
        affecting_provisions="Sch. 6 para. 17",
        affecting_title="Scottish Public Services Ombudsman Act 2002",
    )
    lowering_rejections: list[dict[str, Any]] = []

    ops = compile_effect_to_ir_ops(
        effect,
        extracted_el,
        sequence=0,
        lowering_rejections_out=lowering_rejections,
    )

    assert ops == []
    assert lowering_rejections[0]["rule_id"] == "uk_effect_external_act_target_rejected"
    assert lowering_rejections[0]["reason_code"] == "external_act_target_in_source_text"
    assert lowering_rejections[0]["source_named_target"] == (
        "Town and Country Planning (Scotland) Act 1997"
    )
    assert lowering_rejections[0]["blocking"] is True
    assert lowering_rejections[0]["strict_disposition"] == "block"


def test_compile_rejects_partial_whole_act_repeal_scope() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P1 xmlns="{_LEG_NS}" id="schedule-4-paragraph-42">
          <Pnumber>42</Pnumber>
          <Text>The whole Act (other than sections 13 and 16) is repealed.</Text>
        </P1>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_partial_whole_act_repeal",
        effect_type="repealed in part",
        applied=True,
        requires_applied=True,
        modified="2014-11-27",
        affected_uri="/id/asp/2003/5",
        affected_class="ScottishAct",
        affected_year="2003",
        affected_number="5",
        affected_provisions="Act",
        affecting_uri="/id/asp/2007/14",
        affecting_class="ScottishAct",
        affecting_year="2007",
        affecting_number="14",
        affecting_provisions="Sch. 4 para. 42",
        affecting_title="The Transport and Works (Scotland) Act 2007",
    )
    lowering_rejections: list[dict[str, Any]] = []

    ops = compile_effect_to_ir_ops(
        effect,
        extracted_el,
        sequence=0,
        lowering_rejections_out=lowering_rejections,
    )

    assert ops == []
    assert lowering_rejections[0]["rule_id"] == "uk_effect_partial_whole_act_repeal_rejected"
    assert lowering_rejections[0]["reason_code"] == "partial_whole_act_repeal_unsupported"
    assert lowering_rejections[0]["exception_provisions"] == "sections 13 and 16"
    assert lowering_rejections[0]["blocking"] is True
    assert lowering_rejections[0]["strict_disposition"] == "block"


def test_compile_words_inserted_after_definition_child_with_block_payload() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P1 xmlns="{_LEG_NS}" id="regulation-28">
          <Pnumber>28</Pnumber>
          <P1para>
            <Text>In section 47, in subsection (6), in the definition of “local authority”, after paragraph (a) insert—</Text>
            <BlockAmendment>
              <P3>
                <Pnumber>aa</Pnumber>
                <P3para><Text>a corporate joint committee;</Text></P3para>
              </P3>
            </BlockAmendment>
            <AppendText>.</AppendText>
          </P1para>
        </P1>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_after_definition_child_block_insertion",
        effect_type="words inserted",
        applied=True,
        requires_applied=True,
        modified="2025-10-04",
        affected_uri="/id/asc/2021/1",
        affected_class="WelshAct",
        affected_year="2021",
        affected_number="1",
        affected_provisions="s. 47(6)",
        affecting_uri="/id/wsi/2021/1349",
        affecting_class="WelshStatutoryInstrument",
        affecting_year="2021",
        affecting_number="1349",
        affecting_provisions="reg. 28",
        affecting_title="The Local Government and Elections (Wales) Act 2021 (Consequential Amendments) Regulations 2021",
        in_force_dates=[{"date": "2021-12-03", "prospective": "false"}],
    )
    lowering_rejections: list[dict[str, Any]] = []

    ops = compile_effect_to_ir_ops(
        effect,
        extracted_el,
        sequence=0,
        lowering_rejections_out=lowering_rejections,
    )

    assert lowering_rejections == []
    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].target.path == (("section", "47"), ("subsection", "6"))
    assert ops[0].text_patch is not None
    assert (
        ops[0].text_patch.selector.match_text
        == "TEXT_AFTER_DEFINITION_PARAGRAPH_local authority_AFTER_a"
    )
    assert ops[0].text_patch.replacement == "a corporate joint committee;"
    assert (
        f"{_NOTE_TEXT_REWRITE_RULE}uk_effect_after_definition_child_text_insertion_patch"
        in ops[0].provenance_tags
    )

    base = IRStatute(
        statute_id="asc/2021/1",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="47",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="6",
                            text=(
                                "In this section- “local authority” means- "
                                "a principal council; a community council;"
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )

    replayed = replay_uk_ops(base, ops)
    replayed_text = replayed.body.children[0].children[0].text
    assert replayed_text == (
        "In this section- “local authority” means- "
        "a principal council; a corporate joint committee; a community council;"
    )


def test_compile_words_inserted_at_end_of_numbered_subparagraph_to_text_replace() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P3 xmlns="{_LEG_NS}" id="schedule-2-paragraph-5-a">
          <Pnumber>a</Pnumber>
          <Text>a at the end of sub-paragraph (2) (Commission's general powers to include acquisition and disposal of land) there is inserted “ , or other property ” , and</Text>
        </P3>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_words_inserted_at_end_of_numbered_subparagraph",
        effect_type="words inserted",
        applied=True,
        requires_applied=False,
        modified="2010-01-01",
        affected_uri="/id/asp/2000/7",
        affected_class="ScottishAct",
        affected_year="2000",
        affected_number="7",
        affected_provisions="Sch. 1 para. 2(2)",
        affecting_uri="/id/asp/2010/11",
        affecting_class="ScottishAct",
        affecting_year="2010",
        affecting_number="11",
        affecting_provisions="sch. 2 para. 5(a)",
        affecting_title="Test Amendment Act",
        in_force_dates=[{"date": "2010-01-01", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].payload is None
    assert ops[0].target.path == (
        ("schedule", "1"),
        ("paragraph", "2"),
        ("subparagraph", "2"),
    )
    assert ops[0].text_patch is not None
    assert ops[0].text_patch.kind is TextPatchKindEnum.APPEND
    assert ops[0].text_patch.selector.match_text == "TEXT_END"
    assert ops[0].text_patch.replacement == ", or other property"


def test_compile_for_the_words_substitute_to_text_replace() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P1 xmlns="{_LEG_NS}" id="schedule-4-paragraph-11">
          <Pnumber>11</Pnumber>
          <Text>11 In paragraph 4(6), for the words “Mental Health (Scotland) Act 1984 (c. 36)” substitute “Mental Health (Care and Treatment) (Scotland) Act 2003 (asp 13)”.</Text>
        </P1>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_for_the_words_substitute",
        effect_type="words substituted",
        applied=True,
        requires_applied=False,
        modified="2005-10-05",
        affected_uri="/id/asp/2001/10",
        affected_class="ScottishAct",
        affected_year="2001",
        affected_number="10",
        affected_provisions="Sch. 7 para. 4(6)",
        affecting_uri="/id/asp/2003/13",
        affecting_class="ScottishAct",
        affecting_year="2003",
        affecting_number="13",
        affecting_provisions="Sch. 4 para. 11",
        affecting_title="Test Amendment Act",
        in_force_dates=[{"date": "2005-10-05", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].payload is None
    assert ops[0].target.path == (
        ("schedule", "7"),
        ("paragraph", "4"),
        ("subparagraph", "6"),
    )
    assert ops[0].text_patch is not None
    assert ops[0].text_patch.selector.match_text == "Mental Health (Scotland) Act 1984 (c. 36)"
    assert (
        ops[0].text_patch.replacement
        == "Mental Health (Care and Treatment) (Scotland) Act 2003 (asp 13)"
    )


def test_compile_corresponding_table_entry_word_substitution_with_rowspan_source_context() -> None:
    source_root = ET.fromstring(
        """
        <Legislation>
          <Body>
            <P2 id="regulation-5-1">
              <Pnumber>1</Pnumber>
              <Text>In a provision listed in column 1 of the table in Part 1 of the Schedule,
              for the words in the corresponding entry in column 2 of the table substitute
              “2 May 2022”.</Text>
            </P2>
            <Schedule id="schedule">
              <Part id="schedule-part-1">
                <Table>
                  <thead>
                    <tr><th>Column 1</th><th>Column 2</th></tr>
                  </thead>
                  <tbody>
                    <tr>
                      <td>Section 14D(6)(a) of the Hydrocarbon Oil Duties Act 1979</td>
                      <td rowspan="2">“the commencement of section 282 of the Criminal Justice Act 2003”</td>
                    </tr>
                    <tr>
                      <td>Section 14F(6) of the Hydrocarbon Oil Duties Act 1979
                      (as it extends to Northern Ireland and otherwise as to be inserted
                      by paragraph 9(6) of Schedule 11 to the Finance Act 2020)</td>
                    </tr>
                  </tbody>
                </Table>
                <Table>
                  <thead>
                    <tr><th>Provision</th><th>Date of commencement</th></tr>
                  </thead>
                  <tbody>
                    <tr><td>Section 14F(6)</td><td>1.1.2000</td></tr>
                  </tbody>
                </Table>
              </Part>
            </Schedule>
          </Body>
        </Legislation>
        """
    )
    extracted_el = source_root.find(".//P2")
    assert extracted_el is not None
    effect = UKEffectRecord(
        effect_id="uk_test_table_corresponding_entry_words",
        effect_type="words substituted",
        applied=True,
        requires_applied=False,
        modified="2022-04-28",
        affected_uri="/id/ukpga/1979/5",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="1979",
        affected_number="5",
        affected_provisions="s. 14F(6)",
        affecting_uri="/id/uksi/2022/500",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2022",
        affecting_number="500",
        affecting_provisions="reg. 5(1) Sch. Pt. 1",
        affecting_title="Test Table Amendment Regulations",
        in_force_dates=[{"date": "2022-04-28", "prospective": "false"}],
    )
    lowering_records: list[dict[str, Any]] = []

    ops = compile_effect_to_ir_ops(
        effect,
        extracted_el,
        sequence=0,
        lowering_rejections_out=lowering_records,
        source_root=source_root,
    )

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].target.path == (("section", "14f"), ("subsection", "6"))
    assert ops[0].text_patch is not None
    assert (
        ops[0].text_patch.selector.match_text
        == "the commencement of section 282 of the Criminal Justice Act 2003"
    )
    assert ops[0].text_patch.replacement == "2 May 2022"
    assert any(
        record["rule_id"] == "uk_effect_corresponding_table_entry_word_substitution"
        and record["family"] == "source_table_elaboration"
        and record["blocking"] is False
        for record in lowering_records
    )


def test_compile_corresponding_table_entry_word_substitution_matches_plural_section_list() -> None:
    source_root = ET.fromstring(
        """
        <Legislation>
          <Body>
            <P2 id="regulation-2-1">
              <Pnumber>1</Pnumber>
              <Text>In the provisions listed in column 1 of the table in Part 1 of the Schedule,
              for the words in the corresponding entry in column 2 of that table substitute
              “the general limit in a magistrates' court”.</Text>
            </P2>
            <Schedule id="schedule">
              <Part id="schedule-part-1">
                <Table>
                  <thead><tr><th>Column 1 (provision)</th><th>Column 2</th></tr></thead>
                  <tbody>
                    <tr>
                      <td>Sections 39(5)(a)(i) and 43(3)(a)(i) of the Domestic Abuse Act 2021</td>
                      <td>“12 months”</td>
                    </tr>
                  </tbody>
                </Table>
              </Part>
            </Schedule>
          </Body>
        </Legislation>
        """
    )
    extracted_el = source_root.find(".//P2")
    assert extracted_el is not None
    effect = UKEffectRecord(
        effect_id="uk_test_table_plural_section_list_words",
        effect_type="words substituted",
        applied=True,
        requires_applied=False,
        modified="2023-02-07",
        affected_uri="/id/ukpga/2021/17",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2021",
        affected_number="17",
        affected_provisions="s. 43(3)(a)(i)",
        affecting_uri="/id/uksi/2023/149",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2023",
        affecting_number="149",
        affecting_provisions="reg. 2(1) Sch. Pt. 1 table",
        affecting_title="Test Table Amendment Regulations",
        in_force_dates=[{"date": "2023-02-07", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(
        effect,
        extracted_el,
        sequence=0,
        lowering_rejections_out=[],
        source_root=source_root,
    )

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].target.path == (
        ("section", "43"),
        ("subsection", "3"),
        ("paragraph", "a"),
        ("subparagraph", "i"),
    )
    assert ops[0].text_patch is not None
    assert ops[0].text_patch.selector.match_text == "12 months"
    assert ops[0].text_patch.replacement == "the general limit in a magistrates' court"


def test_compile_corresponding_table_entry_word_substitution_blocks_without_source_context() -> None:
    extracted_el = ET.fromstring(
        """
        <P2 id="regulation-5-1">
          <Pnumber>1</Pnumber>
          <Text>In a provision listed in column 1 of the table in Part 1 of the Schedule,
          for the words in the corresponding entry in column 2 of the table substitute
          “2 May 2022”.</Text>
        </P2>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_table_corresponding_entry_words_blocked",
        effect_type="words substituted",
        applied=True,
        requires_applied=False,
        modified="2022-04-28",
        affected_uri="/id/ukpga/1979/5",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="1979",
        affected_number="5",
        affected_provisions="s. 14F(6)",
        affecting_uri="/id/uksi/2022/500",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2022",
        affecting_number="500",
        affecting_provisions="reg. 5(1) Sch. Pt. 1",
        affecting_title="Test Table Amendment Regulations",
        in_force_dates=[{"date": "2022-04-28", "prospective": "false"}],
    )
    lowering_records: list[dict[str, Any]] = []

    ops = compile_effect_to_ir_ops(
        effect,
        extracted_el,
        sequence=0,
        lowering_rejections_out=lowering_records,
    )

    assert ops == []
    assert any(
        record["rule_id"]
        == "uk_effect_corresponding_table_entry_word_substitution_unresolved"
        and record["reason_code"] == "source_root_unavailable"
        and record["blocking"] is True
        and record["strict_disposition"] == "block"
        for record in lowering_records
    )


def test_compile_range_from_first_occurrence_to_text_replace() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P3 xmlns="{_LEG_NS}" id="schedule-3-paragraph-20-a">
          <Pnumber>a</Pnumber>
          <Text>a in sub-paragraph (b), for the words from “a” where it first occurs to “(c.41)” substitute “ an employee of a relevant authority ” ; and</Text>
        </P3>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_range_from_first_occurrence",
        effect_type="words substituted",
        applied=True,
        requires_applied=False,
        modified="2005-08-02",
        affected_uri="/id/asp/2001/10",
        affected_class="ScottishAct",
        affected_year="2001",
        affected_number="10",
        affected_provisions="Sch. 1 para. 2(b)",
        affecting_uri="/id/asp/2005/5",
        affecting_class="ScottishAct",
        affecting_year="2005",
        affecting_number="5",
        affecting_provisions="Sch. 3 para. 20(a)",
        affecting_title="Test Amendment Act",
        in_force_dates=[{"date": "2005-08-02", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].payload is None
    assert ops[0].target.path == (
        ("schedule", "1"),
        ("paragraph", "2"),
        ("item", "b"),
    )
    assert ops[0].text_patch is not None
    assert ops[0].text_patch.kind is TextPatchKindEnum.REPLACE
    assert ops[0].text_patch.selector.match_text == "TEXT_FROM_a_TO_(c.41)"
    assert ops[0].text_patch.replacement == "an employee of a relevant authority"


def test_compile_same_anchor_adjacent_occurrence_range_to_text_replace() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P4 xmlns="{_LEG_NS}" id="section-3-4-a-ii">
          <Pnumber>ii</Pnumber>
          <Text>ii for the words from “objectives”, where it first occurs, to “objectives”, where it second occurs, substitute “ authority's plan (or revised plan) under section 3F ” , and</Text>
        </P4>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_same_anchor_adjacent_occurrence_range",
        effect_type="words substituted",
        applied=True,
        requires_applied=False,
        modified="2017-08-01",
        affected_uri="/id/asp/2000/6",
        affected_class="ScottishAct",
        affected_year="2000",
        affected_number="6",
        affected_provisions="s. 6(1)(a)",
        affecting_uri="/id/asp/2016/8",
        affecting_class="ScottishAct",
        affecting_year="2016",
        affecting_number="8",
        affecting_provisions="s. 3(4)(a)(ii)",
        affecting_title="Test Amendment Act",
        in_force_dates=[{"date": "2017-08-01", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].target.path == (
        ("section", "6"),
        ("subsection", "1"),
        ("paragraph", "a"),
    )
    assert ops[0].text_patch is not None
    assert ops[0].text_patch.kind is TextPatchKindEnum.REPLACE
    assert ops[0].text_patch.selector.match_text == "TEXT_FROM_objectives_TO_objectives"
    assert ops[0].text_patch.selector.occurrence == 1
    assert ops[0].text_patch.replacement == "authority's plan (or revised plan) under section 3F"
    assert (
        f"{_NOTE_TEXT_REWRITE_RULE}uk_effect_same_anchor_adjacent_occurrence_range_substitution_text_patch"
        in ops[0].provenance_tags
    )


def test_compile_range_with_independent_end_occurrence_to_text_replace() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P2 xmlns="{_LEG_NS}" id="section-60-2">
          <Pnumber>2</Pnumber>
          <Text>for the words from “notify”, where first occurring, to “Guardian”, where second occurring, substitute “ notify the Public Guardian ”</Text>
        </P2>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_range_independent_end_occurrence",
        effect_type="words substituted",
        applied=True,
        requires_applied=False,
        modified="2007-09-05",
        affected_uri="/id/asp/2000/4",
        affected_class="ScottishAct",
        affected_year="2000",
        affected_number="4",
        affected_provisions="s. 55",
        affecting_uri="/id/asp/2007/10",
        affecting_class="ScottishAct",
        affecting_year="2007",
        affecting_number="10",
        affecting_provisions="s. 60",
        affecting_title="Test Amendment Act",
        in_force_dates=[{"date": "2007-09-05", "prospective": "false"}],
    )
    lowering_records: list[dict[str, Any]] = []

    ops = compile_effect_to_ir_ops(
        effect,
        extracted_el,
        sequence=0,
        lowering_rejections_out=lowering_records,
    )

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].text_patch is not None
    assert ops[0].text_patch.selector.match_text == "TEXT_FROM_notify_TO_Guardian"
    assert ops[0].text_patch.selector.occurrence == 1
    assert ops[0].text_patch.selector.end_occurrence == 2
    assert ops[0].text_patch.replacement == "notify the Public Guardian"
    assert (
        f"{_NOTE_TEXT_REWRITE_RULE}uk_effect_range_independent_end_occurrence_substitution_text_patch"
        in ops[0].provenance_tags
    )
    assert any(
        record.get("rule_id") == "uk_effect_range_independent_end_occurrence_text_patch"
        and record.get("strict_disposition") == "record"
        and not record.get("blocking")
        for record in lowering_records
    )


def test_compile_insert_after_words_inserted_by_source_sibling() -> None:
    source_root = ET.fromstring(
        f"""
        <P2 xmlns="{_LEG_NS}" id="schedule-7-paragraph-15-12">
          <Pnumber>12</Pnumber>
          <P3 id="schedule-7-paragraph-15-12-a">
            <Pnumber>a</Pnumber>
            <Text>a for “a police force” substitute “ the Police Service ” ,</Text>
          </P3>
          <P3 id="schedule-7-paragraph-15-12-b">
            <Pnumber>b</Pnumber>
            <Text>b after the words inserted by sub-paragraph (a) insert “ or to the Police Investigations and Review Commissioner ” ,</Text>
          </P3>
        </P2>
        """
    )
    extracted_el = source_root.find(f"./{{{_LEG_NS}}}P3[@id='schedule-7-paragraph-15-12-b']")
    assert extracted_el is not None
    effect = UKEffectRecord(
        effect_id="uk_test_after_words_inserted_by_sibling",
        effect_type="words inserted",
        applied=True,
        requires_applied=False,
        modified="2013-04-01",
        affected_uri="/id/asp/2000/11",
        affected_class="ScottishAct",
        affected_year="2000",
        affected_number="11",
        affected_provisions="s. 24(2)(b)",
        affecting_uri="/id/asp/2012/8",
        affecting_class="ScottishAct",
        affecting_year="2012",
        affecting_number="8",
        affecting_provisions="sch. 7 para. 15(12)(b)",
        affecting_title="Test Amendment Act",
        in_force_dates=[{"date": "2013-04-01", "prospective": "false"}],
    )
    lowering_records: list[dict[str, Any]] = []

    ops = compile_effect_to_ir_ops(
        effect,
        extracted_el,
        sequence=0,
        lowering_rejections_out=lowering_records,
        source_root=source_root,
    )

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].target.path == (
        ("section", "24"),
        ("subsection", "2"),
        ("paragraph", "b"),
    )
    assert ops[0].text_patch is not None
    assert ops[0].text_patch.selector.match_text == "the Police Service"
    assert (
        ops[0].text_patch.replacement
        == "the Police Service or to the Police Investigations and Review Commissioner"
    )
    assert (
        f"{_NOTE_TEXT_REWRITE_RULE}uk_effect_after_words_inserted_by_sibling_text_patch"
        in ops[0].provenance_tags
    )
    assert any(
        record.get("rule_id") == "uk_effect_after_words_inserted_by_sibling_text_patch"
        and record.get("family") == "source_context_elaboration"
        and record.get("strict_disposition") == "record"
        for record in lowering_records
    )


def test_compile_block_insert_after_words_inserted_by_source_sibling() -> None:
    source_root = ET.fromstring(
        f"""
        <P4 xmlns="{_LEG_NS}" id="schedule-7-paragraph-15-5-d-i">
          <Pnumber>i</Pnumber>
          <P5 id="schedule-7-paragraph-15-5-d-i-A">
            <Pnumber>A</Pnumber>
            <Text>A for the words from “by” to “Agency” substitute — i by, or on the application of, a constable of the Police Service; ,</Text>
          </P5>
          <P5 id="schedule-7-paragraph-15-5-d-i-B">
            <Pnumber>B</Pnumber>
            <Text>B after the words inserted by paragraph (A) insert— ii by the Police Investigations and Review Commissioner; or iii by, or on the application of, a staff officer of the Police Investigations and Review Commissioner ,</Text>
          </P5>
        </P4>
        """
    )
    extracted_el = source_root.find(f"./{{{_LEG_NS}}}P5[@id='schedule-7-paragraph-15-5-d-i-B']")
    assert extracted_el is not None
    effect = UKEffectRecord(
        effect_id="uk_test_block_after_words_inserted_by_sibling",
        effect_type="words inserted",
        applied=True,
        requires_applied=False,
        modified="2013-04-01",
        affected_uri="/id/asp/2000/11",
        affected_class="ScottishAct",
        affected_year="2000",
        affected_number="11",
        affected_provisions="s. 11(4)(a)",
        affecting_uri="/id/asp/2012/8",
        affecting_class="ScottishAct",
        affecting_year="2012",
        affecting_number="8",
        affecting_provisions="sch. 7 para. 15(5)(d)(i) (B)",
        affecting_title="Test Amendment Act",
        in_force_dates=[{"date": "2013-04-01", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0, source_root=source_root)

    assert len(ops) == 1
    assert ops[0].text_patch is not None
    assert ops[0].text_patch.selector.match_text == (
        "i by, or on the application of, a constable of the Police Service; ,"
    )
    assert "ii by the Police Investigations and Review Commissioner" in (ops[0].text_patch.replacement or "")
    assert (
        f"{_NOTE_TEXT_REWRITE_RULE}uk_effect_after_words_inserted_by_sibling_text_patch"
        in ops[0].provenance_tags
    )


def test_executor_range_text_patch_uses_independent_end_occurrence() -> None:
    statute = IRStatute(
        statute_id="asp/2000/4",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="55",
                    text="notify the Guardian and notify the Guardian within 7 days",
                    children=(),
                ),
            ),
        ),
        supplements=(),
    )
    executor: Any = UKReplayExecutor(statute)
    op = LegalOperation(
        op_id="uk_test_range_independent_end_occurrence_apply",
        sequence=0,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=(("section", "55"),)),
        text_patch=TextPatchSpec(
            kind=TextPatchKindEnum.REPLACE,
            selector=TextSelector(
                match_text="TEXT_FROM_notify_TO_Guardian",
                occurrence=1,
                end_occurrence=2,
            ),
            replacement="notify the Public Guardian",
        ),
    )

    executor.apply_op(op)

    assert executor.statute.body.children[0].text == "notify the Public Guardian within 7 days"


def test_compile_range_to_end_second_occurrence_to_text_replace() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P3 xmlns="{_LEG_NS}" id="section-106-2-a">
          <Pnumber>a</Pnumber>
          <Text>a in subsection (1), for the words from “the” where it second occurs to the end substitute “ any of the persons mentioned in subsection (1A) may grant authorisations ” , and</Text>
        </P3>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_range_to_end_second_occurrence",
        effect_type="words substituted",
        applied=True,
        requires_applied=False,
        modified="2011-03-28",
        affected_uri="/id/asp/2000/11",
        affected_class="ScottishAct",
        affected_year="2000",
        affected_number="11",
        affected_provisions="s. 10(1)",
        affecting_uri="/id/asp/2010/13",
        affecting_class="ScottishAct",
        affecting_year="2010",
        affecting_number="13",
        affecting_provisions="s. 106(2)(a)",
        affecting_title="Test Amendment Act",
        in_force_dates=[{"date": "2011-03-28", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].target.path == (("section", "10"), ("subsection", "1"))
    assert ops[0].text_patch is not None
    assert ops[0].text_patch.kind is TextPatchKindEnum.REPLACE
    assert ops[0].text_patch.selector.match_text == "TEXT_FROM_the_TO_END"
    assert ops[0].text_patch.selector.occurrence == 2
    assert (
        ops[0].text_patch.replacement
        == "any of the persons mentioned in subsection (1A) may grant authorisations"
    )


def test_compile_range_comma_before_substitute_to_text_replace() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P2 xmlns="{_LEG_NS}" id="regulation-2-2">
          <Pnumber>2</Pnumber>
          <Text>for the words from “means—” to “(and”, substitute “replacement text”</Text>
        </P2>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_range_comma_before_substitute",
        effect_type="words substituted",
        applied=True,
        requires_applied=False,
        modified="2019-06-01",
        affected_uri="/id/ukpga/2018/16",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2018",
        affected_number="16",
        affected_provisions="s. 20(1)",
        affecting_uri="/id/uksi/2019/859",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2019",
        affecting_number="859",
        affecting_provisions="reg. 2(2)",
        affecting_title="Test Amendment Regulations",
        in_force_dates=[{"date": "2019-06-01", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].target.path == (("section", "20"), ("subsection", "1"))
    assert ops[0].text_patch is not None
    assert ops[0].text_patch.selector.match_text == "TEXT_FROM_means\u2014_TO_(and"
    assert ops[0].text_patch.replacement == "replacement text"


def test_compile_range_to_end_of_subsection_to_text_replace() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P2 xmlns="{_LEG_NS}" id="regulation-2-3">
          <Pnumber>3</Pnumber>
          <Text>for the words from “Act—” to the end of the subsection, substitute “replacement text”</Text>
        </P2>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_range_to_end_of_subsection",
        effect_type="words substituted",
        applied=True,
        requires_applied=False,
        modified="2019-06-01",
        affected_uri="/id/ukpga/2018/16",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2018",
        affected_number="16",
        affected_provisions="s. 20(2)",
        affecting_uri="/id/uksi/2019/859",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2019",
        affecting_number="859",
        affecting_provisions="reg. 2(3)",
        affecting_title="Test Amendment Regulations",
        in_force_dates=[{"date": "2019-06-01", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].target.path == (("section", "20"), ("subsection", "2"))
    assert ops[0].text_patch is not None
    assert ops[0].text_patch.selector.match_text == "TEXT_FROM_Act\u2014_TO_END"
    assert ops[0].text_patch.replacement == "replacement text"


def test_compile_wherever_it_appears_to_text_replace() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P3 xmlns="{_LEG_NS}" id="section-26-1-a">
          <Pnumber>a</Pnumber>
          <Text>a for “exit day”, wherever it appears, substitute “ IP completion day ” ,</Text>
        </P3>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_wherever_it_appears",
        effect_type="words substituted",
        applied=True,
        requires_applied=False,
        modified="2020-01-31",
        affected_uri="/id/ukpga/2018/16",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2018",
        affected_number="16",
        affected_provisions="s. 6",
        affecting_uri="/id/ukpga/2020/1",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2020",
        affecting_number="1",
        affecting_provisions="s. 26(1)(a)",
        affecting_title="Test Amendment Act",
        in_force_dates=[{"date": "2020-01-31", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].target.path == (("section", "6"),)
    assert ops[0].text_patch is not None
    assert ops[0].text_patch.selector.match_text == "exit day"
    assert ops[0].text_patch.selector.occurrence == 0
    assert ops[0].text_patch.replacement == " IP completion day "


def test_compile_wherever_occurring_records_all_occurrences_lowering_observation() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P3 xmlns="{_LEG_NS}" id="schedule-paragraph-21-2-b">
          <Pnumber>b</Pnumber>
          <Text>b in subsections (2), (3) and (6), for “the Information Centre”, wherever occurring, substitute “NHS England” .</Text>
        </P3>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_wherever_occurring",
        effect_type="words substituted",
        applied=True,
        requires_applied=True,
        modified="2023-02-02",
        affected_uri="/id/ukpga/2021/3",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2021",
        affected_number="3",
        affected_provisions="s. 7A(6)",
        affecting_uri="/id/uksi/2023/98",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2023",
        affecting_number="98",
        affecting_provisions="Sch. para. 21(2)(b)",
        affecting_title="Test Amendment Regulations",
        in_force_dates=[{"date": "2023-02-01", "prospective": "false"}],
    )
    lowering_records: list[dict[str, object]] = []

    ops = compile_effect_to_ir_ops(
        effect,
        extracted_el,
        sequence=0,
        lowering_rejections_out=lowering_records,
    )

    assert len(ops) == 1
    assert ops[0].text_patch is not None
    assert ops[0].text_patch.selector.match_text == "the Information Centre"
    assert ops[0].text_patch.selector.occurrence == 0
    assert lowering_records == [
        {
            "rule_id": "uk_effect_wherever_occurring_substitution_text_patch",
            "family": "text_rewrite_lowering",
            "phase": "lowering",
            "effect_id": "uk_test_wherever_occurring",
            "affecting_act_id": "uksi/2023/98",
            "affected_provisions": "s. 7A(6)",
            "affecting_provisions": "Sch. para. 21(2)(b)",
            "effect_type": "words substituted",
            "reason": (
                "UK effect source explicitly applies a word-level text rewrite "
                "wherever/in each place it occurs; lowering preserves that as "
                "an all-occurrences text patch scoped to the affected target."
            ),
            "reason_code": "explicit_all_occurrences_text_patch",
            "blocking": False,
            "strict_disposition": "record",
            "quirks_disposition": "record",
            "extracted_tag": "P3",
            "has_extracted_source": True,
            "extracted_text_preview": (
                "b b in subsections (2), (3) and (6), for “the Information Centre”, "
                "wherever occurring, substitute “NHS England” ."
            ),
            "target_ref": "s. 7A(6)",
            "target": "section:7a/subsection:6",
            "text_match": "the Information Centre",
            "replacement": "NHS England",
            "occurrence": 0,
        }
    ]


def test_compile_both_places_there_is_substituted_records_all_occurrences_observation() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P3 xmlns="{_LEG_NS}" id="schedule-paragraph-50-a">
          <Pnumber>a</Pnumber>
          <Text>a in subsection (7), for “local” in both places where it occurs, there is substituted “bus”;</Text>
        </P3>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_both_places_there_is_substituted",
        effect_type="word substituted",
        applied=True,
        requires_applied=True,
        modified="2005-08-04",
        affected_uri="/id/asp/2001/2",
        affected_class="ScottishAct",
        affected_year="2001",
        affected_number="2",
        affected_provisions="s. 41(7)",
        affecting_uri="/id/asp/2005/12",
        affecting_class="ScottishAct",
        affecting_year="2005",
        affecting_number="12",
        affecting_provisions="s. 50(a)",
        affecting_title="Test Amendment Act",
        in_force_dates=[{"date": "2005-08-04", "prospective": "false"}],
    )
    lowering_records: list[dict[str, object]] = []

    ops = compile_effect_to_ir_ops(
        effect,
        extracted_el,
        sequence=0,
        lowering_rejections_out=lowering_records,
    )

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].target.path == (("section", "41"), ("subsection", "7"))
    assert ops[0].text_patch is not None
    assert ops[0].text_patch.selector.match_text == "local"
    assert ops[0].text_patch.selector.occurrence == 0
    assert ops[0].text_patch.replacement == "bus"
    assert [
        record["rule_id"]
        for record in lowering_records
        if record["rule_id"] == "uk_effect_all_occurrences_substitution_text_patch"
    ] == ["uk_effect_all_occurrences_substitution_text_patch"]
    all_occurrence_record = lowering_records[0]
    assert all_occurrence_record["family"] == "text_rewrite_lowering"
    assert all_occurrence_record["reason_code"] == "explicit_all_occurrences_text_patch"
    assert all_occurrence_record["blocking"] is False
    assert all_occurrence_record["strict_disposition"] == "record"
    assert all_occurrence_record["target"] == "section:41/subsection:7"
    assert all_occurrence_record["text_match"] == "local"
    assert all_occurrence_record["replacement"] == "bus"
    assert all_occurrence_record["occurrence"] == 0


def test_compile_after_anchor_each_occasion_insert_records_all_occurrences_observation() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P2 xmlns="{_LEG_NS}" id="regulation-6-6">
          <Pnumber>6</Pnumber>
          <Text>In section 218, after “court”, on each occasion where it appears, insert “or the First-tier Tribunal”.</Text>
        </P2>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_after_anchor_each_occasion_insert",
        effect_type="words inserted",
        applied=True,
        requires_applied=True,
        modified="2024-01-30",
        affected_uri="/id/asp/2007/3",
        affected_class="ScottishAct",
        affected_year="2007",
        affected_number="3",
        affected_provisions="s. 218",
        affecting_uri="/id/ssi/2019/51",
        affecting_class="ScottishStatutoryInstrument",
        affecting_year="2019",
        affecting_number="51",
        affecting_provisions="reg. 6(6)",
        affecting_title="Test Regulations",
        in_force_dates=[{"date": "2019-03-06", "prospective": "false"}],
    )
    lowering_records: list[dict[str, Any]] = []

    ops = compile_effect_to_ir_ops(
        effect,
        extracted_el,
        sequence=0,
        lowering_rejections_out=lowering_records,
    )

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].target.path == (("section", "218"),)
    assert ops[0].text_patch is not None
    assert ops[0].text_patch.selector.match_text == "court"
    assert ops[0].text_patch.selector.occurrence == 0
    assert ops[0].text_patch.replacement == "court or the First-tier Tribunal"
    assert (
        f"{_NOTE_TEXT_REWRITE_RULE}uk_effect_after_quoted_anchor_each_occasion_insert_text_patch"
        in ops[0].provenance_tags
    )
    assert [
        record["rule_id"]
        for record in lowering_records
        if record["rule_id"] == "uk_effect_after_quoted_anchor_each_occasion_insert_text_patch"
    ] == ["uk_effect_after_quoted_anchor_each_occasion_insert_text_patch"]


def test_compile_after_each_occurrence_insert_records_all_occurrences_observation() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P4 xmlns="{_LEG_NS}" id="article-21-2-i">
          <Pnumber>i</Pnumber>
          <Text>i after each occurrence of “spouse” insert “or civil partner”; and</Text>
        </P4>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_after_each_occurrence_insert",
        effect_type="words inserted",
        applied=True,
        requires_applied=True,
        modified="2005-12-05",
        affected_uri="/id/asp/2001/11",
        affected_class="ScottishAct",
        affected_year="2001",
        affected_number="11",
        affected_provisions="s. 1(2)(b)",
        affecting_uri="/id/ssi/2005/623",
        affecting_class="ScottishStatutoryInstrument",
        affecting_year="2005",
        affecting_number="623",
        affecting_provisions="art. 21(2)(i)",
        affecting_title="Test Regulations",
        in_force_dates=[{"date": "2005-12-05", "prospective": "false"}],
    )
    lowering_records: list[dict[str, Any]] = []

    ops = compile_effect_to_ir_ops(
        effect,
        extracted_el,
        sequence=0,
        lowering_rejections_out=lowering_records,
    )

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].target.path == (("section", "1"), ("subsection", "2"), ("paragraph", "b"))
    assert ops[0].text_patch is not None
    assert ops[0].text_patch.selector.match_text == "spouse"
    assert ops[0].text_patch.selector.occurrence == 0
    assert ops[0].text_patch.replacement == "spouse or civil partner"
    assert (
        f"{_NOTE_TEXT_REWRITE_RULE}uk_effect_after_quoted_anchor_all_occurrences_insert_text_patch"
        in ops[0].provenance_tags
    )
    assert [
        record["rule_id"]
        for record in lowering_records
        if record["rule_id"] == "uk_effect_after_quoted_anchor_all_occurrences_insert_text_patch"
    ] == ["uk_effect_after_quoted_anchor_all_occurrences_insert_text_patch"]


def test_compile_after_anchor_both_places_where_it_appears_records_all_occurrences_observation() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P3 xmlns="{_LEG_NS}" id="regulation-6-4-b">
          <Pnumber>b</Pnumber>
          <Text>b after “court”, in both places where it appears, insert “or the First-tier Tribunal”.</Text>
        </P3>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_after_anchor_both_places_where_it_appears_insert",
        effect_type="words inserted",
        applied=True,
        requires_applied=True,
        modified="2024-01-30",
        affected_uri="/id/asp/2007/3",
        affected_class="ScottishAct",
        affected_year="2007",
        affected_number="3",
        affected_provisions="s. 216(4)",
        affecting_uri="/id/ssi/2019/51",
        affecting_class="ScottishStatutoryInstrument",
        affecting_year="2019",
        affecting_number="51",
        affecting_provisions="reg. 6(4)(b)",
        affecting_title="Test Regulations",
        in_force_dates=[{"date": "2019-03-06", "prospective": "false"}],
    )
    lowering_records: list[dict[str, Any]] = []

    ops = compile_effect_to_ir_ops(
        effect,
        extracted_el,
        sequence=0,
        lowering_rejections_out=lowering_records,
    )

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].target.path == (("section", "216"), ("subsection", "4"))
    assert ops[0].text_patch is not None
    assert ops[0].text_patch.selector.match_text == "court"
    assert ops[0].text_patch.selector.occurrence == 0
    assert ops[0].text_patch.replacement == "court or the First-tier Tribunal"
    assert (
        f"{_NOTE_TEXT_REWRITE_RULE}uk_effect_after_quoted_anchor_all_occurrences_insert_text_patch"
        in ops[0].provenance_tags
    )
    assert [
        record["rule_id"]
        for record in lowering_records
        if record["rule_id"] == "uk_effect_after_quoted_anchor_all_occurrences_insert_text_patch"
    ] == ["uk_effect_after_quoted_anchor_all_occurrences_insert_text_patch"]


def test_compile_definition_scoped_all_occurrences_insert_and_replay() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P4 xmlns="{_LEG_NS}" id="regulation-6-2-a-ii">
          <Pnumber>ii</Pnumber>
          <Text>ii in the definition of “an action for removing from heritable property” after “decree”, in both places where it appears, insert “, order”, and</Text>
        </P4>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_definition_scoped_all_occurrences_insert",
        effect_type="word inserted",
        applied=True,
        requires_applied=True,
        modified="2024-01-30",
        affected_uri="/id/asp/2007/3",
        affected_class="ScottishAct",
        affected_year="2007",
        affected_number="3",
        affected_provisions="s. 214",
        affecting_uri="/id/ssi/2019/51",
        affecting_class="ScottishStatutoryInstrument",
        affecting_year="2019",
        affecting_number="51",
        affecting_provisions="reg. 6(2)(a)(ii)",
        affecting_title="Test Regulations",
        in_force_dates=[{"date": "2019-03-06", "prospective": "false"}],
    )
    lowering_records: list[dict[str, Any]] = []

    ops = compile_effect_to_ir_ops(
        effect,
        extracted_el,
        sequence=0,
        lowering_rejections_out=lowering_records,
    )

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].target.path == (("section", "214"),)
    assert ops[0].text_patch is not None
    assert (
        ops[0].text_patch.selector.match_text
        == "TEXT_IN_DEFINITION_an action for removing from heritable property\x1fAFTER_EACH\x1fdecree"
    )
    assert ops[0].text_patch.replacement == "decree, order"
    assert [
        record["rule_id"]
        for record in lowering_records
        if record["rule_id"] == "uk_effect_in_definition_after_anchor_all_occurrences_insert_text_patch"
    ] == ["uk_effect_in_definition_after_anchor_all_occurrences_insert_text_patch"]

    statute = IRStatute(
        statute_id="asp/2007/3",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="214",
                    text=(
                        "“an action for removing from heritable property” means an action "
                        "for decree of removing or a decree for removing;"
                    ),
                    attrs={"eId": "section-214"},
                ),
            ),
        ),
    )
    executor = UKReplayExecutor(statute)
    executor.apply_op(ops[0])

    assert executor.statute.body.children[0].text == (
        "“an action for removing from heritable property” means an action "
        "for decree, order of removing or a decree, order for removing;"
    )


def test_compile_opening_words_substitution_preserves_children() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P4 xmlns="{_LEG_NS}" id="section-46-2-b-i">
          <Pnumber>i</Pnumber>
          <Text>i for the opening words substitute “Regulations under subsection (2) may, in particular, make provision for or in connection with—” ,</Text>
        </P4>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_opening_words_substitution",
        effect_type="words substituted",
        applied=True,
        requires_applied=False,
        modified="2025-12-06",
        affected_uri="/id/asp/2001/10",
        affected_class="ScottishAct",
        affected_year="2001",
        affected_number="10",
        affected_provisions="s. 27(3)",
        affecting_uri="/id/asp/2025/13",
        affecting_class="ScottishAct",
        affecting_year="2025",
        affecting_number="13",
        affecting_provisions="s. 46(2)(b)(i)",
        affecting_title="Test Amendment Act",
        in_force_dates=[{"date": "2025-12-06", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].target.path == (("section", "27"), ("subsection", "3"))
    assert ops[0].text_patch is not None
    assert ops[0].text_patch.selector.match_text == "TEXT_OPENING_WORDS"
    assert (
        ops[0].text_patch.replacement
        == "Regulations under subsection (2) may, in particular, make provision for or in connection with\u2014"
    )

    base = IRStatute(
        statute_id="asp/2001/10",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="27",
                    attrs={"eId": "section-27"},
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="3",
                            text="Old opening words may include\u2014",
                            attrs={"eId": "section-27-3"},
                            children=(
                                IRNode(kind=IRNodeKind.PARAGRAPH, label="a", text="first paragraph"),
                                IRNode(kind=IRNodeKind.PARAGRAPH, label="b", text="second paragraph"),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )

    replayed = replay_uk_ops(base, ops)
    subsection = replayed.body.children[0].children[0]
    assert (
        subsection.text
        == "Regulations under subsection (2) may, in particular, make provision for or in connection with\u2014"
    )
    assert [child.label for child in subsection.children] == ["a", "b"]


def test_compile_after_child_insert_appends_to_named_child() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P4 xmlns="{_LEG_NS}" id="schedule-paragraph-8-2-a-i">
          <Pnumber>i</Pnumber>
          <Text>i after sub-paragraph (i) insert “or”; and</Text>
        </P4>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_after_child_insert",
        effect_type="word inserted",
        applied=True,
        requires_applied=False,
        modified="2003-06-25",
        affected_uri="/id/asp/2001/10",
        affected_class="ScottishAct",
        affected_year="2001",
        affected_number="10",
        affected_provisions="s. 11(1)(b)",
        affecting_uri="/id/ssi/2003/331",
        affecting_class="ScottishStatutoryInstrument",
        affecting_year="2003",
        affecting_number="331",
        affecting_provisions="Sch. para. 8(2)(a)(i)",
        affecting_title="Test Amendment Regulations",
        in_force_dates=[{"date": "2003-06-25", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].target.path == (
        ("section", "11"),
        ("subsection", "1"),
        ("paragraph", "b"),
    )
    assert ops[0].text_patch is not None
    assert ops[0].text_patch.selector.match_text == "TEXT_AFTER_CHILD_subparagraph_i"
    assert ops[0].text_patch.replacement == "or"

    base = IRStatute(
        statute_id="asp/2001/10",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="11",
                    attrs={"eId": "section-11"},
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1",
                            attrs={"eId": "section-11-1"},
                            children=(
                                IRNode(
                                    kind=IRNodeKind.PARAGRAPH,
                                    label="b",
                                    attrs={"eId": "section-11-1-b"},
                                    children=(
                                        IRNode(
                                            kind=IRNodeKind.SUBPARAGRAPH,
                                            label="i",
                                            text="first condition",
                                            attrs={"eId": "section-11-1-b-i"},
                                        ),
                                        IRNode(
                                            kind=IRNodeKind.SUBPARAGRAPH,
                                            label="ii",
                                            text="second condition",
                                            attrs={"eId": "section-11-1-b-ii"},
                                        ),
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )

    replayed = replay_uk_ops(base, ops)
    children = replayed.body.children[0].children[0].children[0].children
    assert children[0].text == "first condition or"
    assert children[1].text == "second condition"


def test_compile_words_inserted_insert_at_end_to_text_replace() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P3 xmlns="{_LEG_NS}" id="schedule-10-paragraph-8-2-b">
          <Pnumber>b</Pnumber>
          <Text>b in paragraph (c) insert at the end “or on shared equity terms,”;</Text>
        </P3>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_words_inserted_insert_at_end",
        effect_type="words inserted",
        applied=True,
        requires_applied=False,
        modified="2011-04-01",
        affected_uri="/id/asp/2001/10",
        affected_class="ScottishAct",
        affected_year="2001",
        affected_number="10",
        affected_provisions="s. 71(4)(c)",
        affecting_uri="/id/asp/2010/17",
        affecting_class="ScottishAct",
        affecting_year="2010",
        affecting_number="17",
        affecting_provisions="Sch. 10 para. 8(2)(b)",
        affecting_title="Test Amendment Act",
        in_force_dates=[{"date": "2011-04-01", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].payload is None
    assert ops[0].target.path == (
        ("section", "71"),
        ("subsection", "4"),
        ("paragraph", "c"),
    )
    assert ops[0].text_patch is not None
    assert ops[0].text_patch.kind is TextPatchKindEnum.APPEND
    assert ops[0].text_patch.selector.match_text == "TEXT_END"
    assert ops[0].text_patch.replacement == "or on shared equity terms,"


def test_compile_direct_words_are_repealed_to_text_repeal() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P3 xmlns="{_LEG_NS}" id="schedule-2-paragraph-7-7-b">
          <Pnumber>b</Pnumber>
          <Text>b in paragraph 2(1), the words “or section 66 of this Act” are repealed.</Text>
        </P3>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_direct_words_are_repealed",
        effect_type="words repealed",
        applied=True,
        requires_applied=False,
        modified="2012-04-01",
        affected_uri="/id/asp/2001/10",
        affected_class="ScottishAct",
        affected_year="2001",
        affected_number="10",
        affected_provisions="Sch. 9 para. 2(1)",
        affecting_uri="/id/asp/2010/17",
        affecting_class="ScottishAct",
        affecting_year="2010",
        affecting_number="17",
        affecting_provisions="Sch. 2 para. 7(7)(b)",
        affecting_title="Test Amendment Act",
        in_force_dates=[{"date": "2012-04-01", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPEAL
    assert ops[0].payload is None
    assert ops[0].target.path == (
        ("schedule", "9"),
        ("paragraph", "2"),
        ("subparagraph", "1"),
    )
    assert ops[0].text_patch is not None
    assert ops[0].text_patch.kind is TextPatchKindEnum.DELETE
    assert ops[0].text_patch.selector.match_text == "or section 66 of this Act"


def test_compile_contextual_preceding_word_repeal_uses_adjacent_anchor() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P4 xmlns="{_LEG_NS}" id="section-7-2-b-i">
          <Pnumber>i</Pnumber>
          <Text>i the word “and” immediately preceding paragraph (b) is repealed,</Text>
        </P4>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_contextual_preceding_word_repeal",
        effect_type="word repealed",
        applied=True,
        requires_applied=False,
        modified="2020-01-01",
        affected_uri="/id/asp/2001/10",
        affected_class="ScottishAct",
        affected_year="2001",
        affected_number="10",
        affected_provisions="s. 35(3)",
        affecting_uri="/id/asp/2020/1",
        affecting_class="ScottishAct",
        affecting_year="2020",
        affecting_number="1",
        affecting_provisions="s. 7(2)(b)(i)",
        affecting_title="Test Amendment Act",
        in_force_dates=[{"date": "2020-01-01", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPEAL
    assert ops[0].target.path == (("section", "35"), ("subsection", "3"))
    assert ops[0].text_patch is not None
    assert ops[0].text_patch.selector.match_text == (
        "TEXT_WORD_and_IMMEDIATELY_PRECEDING_paragraph_b"
    )

    base = IRStatute(
        statute_id="asp/2001/10",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="35",
                    attrs={"eId": "section-35"},
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="3",
                            attrs={"eId": "section-35-3"},
                            children=(
                                IRNode(
                                    kind=IRNodeKind.PARAGRAPH,
                                    label="a",
                                    text="first condition, and",
                                    attrs={"eId": "section-35-3-a"},
                                ),
                                IRNode(
                                    kind=IRNodeKind.PARAGRAPH,
                                    label="b",
                                    text="second condition",
                                    attrs={"eId": "section-35-3-b"},
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )

    replayed = replay_uk_ops(base, ops)
    paragraphs = replayed.body.children[0].children[0].children
    assert paragraphs[0].text == "first condition"
    assert paragraphs[1].text == "second condition"


def test_compile_contextual_following_word_repeal_uses_anchor_node() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P3 xmlns="{_LEG_NS}" id="section-54-a">
          <Pnumber>a</Pnumber>
          <Text>a the word “and” which follows paragraph (c) is repealed,</Text>
        </P3>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_contextual_following_word_repeal",
        effect_type="word repealed",
        applied=True,
        requires_applied=False,
        modified="2020-01-01",
        affected_uri="/id/asp/2001/10",
        affected_class="ScottishAct",
        affected_year="2001",
        affected_number="10",
        affected_provisions="Sch. 5 para. 8",
        affecting_uri="/id/asp/2020/1",
        affecting_class="ScottishAct",
        affecting_year="2020",
        affecting_number="1",
        affecting_provisions="s. 54(a)",
        affecting_title="Test Amendment Act",
        in_force_dates=[{"date": "2020-01-01", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPEAL
    assert ops[0].text_patch is not None
    assert ops[0].text_patch.selector.match_text == (
        "TEXT_WORD_and_IMMEDIATELY_FOLLOWING_paragraph_c"
    )

    base = IRStatute(
        statute_id="asp/2001/10",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=()),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="5",
                attrs={"eId": "schedule-5"},
                children=(
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="8",
                        attrs={"eId": "schedule-5-paragraph-8"},
                        children=(
                            IRNode(kind=IRNodeKind.PARAGRAPH, label="c", text="third condition and"),
                            IRNode(kind=IRNodeKind.PARAGRAPH, label="d", text="fourth condition"),
                        ),
                    ),
                ),
            ),
        ),
    )

    replayed = replay_uk_ops(base, ops)
    paragraph_8 = replayed.supplements[0].children[0]
    assert paragraph_8.children[0].text == "third condition"
    assert paragraph_8.children[1].text == "fourth condition"


def test_contextual_following_word_repeal_records_anchor_kind_recovery() -> None:
    op = LegalOperation(
        op_id="uk_test_contextual_following_word_kind_recovery",
        sequence=1,
        action=StructuralAction.TEXT_REPEAL,
        target=LegalAddress(path=(("schedule", "5"), ("paragraph", "8"))),
        text_patch=TextPatchSpec(
            kind=TextPatchKindEnum.DELETE,
            selector=TextSelector(match_text="TEXT_WORD_and_IMMEDIATELY_FOLLOWING_paragraph_c", occurrence=0),
            replacement=None,
        ),
    )
    base = IRStatute(
        statute_id="asp/2001/10",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=()),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="5",
                attrs={"eId": "schedule-5"},
                children=(
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="8",
                        attrs={"eId": "schedule-5-paragraph-8"},
                        children=(
                            IRNode(kind=IRNodeKind.ITEM, label="c", text="third condition and"),
                            IRNode(kind=IRNodeKind.ITEM, label="d", text="fourth condition"),
                        ),
                    ),
                ),
            ),
        ),
    )
    adjudications: list[CompileAdjudication] = []

    replayed = replay_uk_ops(base, [op], adjudications_out=adjudications)

    paragraph_8 = replayed.supplements[0].children[0]
    assert paragraph_8.children[0].text == "third condition"
    assert [adjudication.kind for adjudication in adjudications] == [
        "uk_replay_contextual_word_anchor_kind_normalized"
    ]
    assert adjudications[0].detail["blocking"] is False
    assert adjudications[0].detail["strict_disposition"] == "record"


def test_compile_contextual_target_word_repeal_uses_target_node() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P4 xmlns="{_LEG_NS}" id="section-155-a-ii">
          <Pnumber>ii</Pnumber>
          <Text>ii the word “and” immediately following subsection (4)(a) is repealed, and</Text>
        </P4>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_contextual_target_word_repeal",
        effect_type="word repealed",
        applied=True,
        requires_applied=False,
        modified="2012-02-22",
        affected_uri="/id/asp/2001/10",
        affected_class="ScottishAct",
        affected_year="2001",
        affected_number="10",
        affected_provisions="s. 14(4)(a)",
        affecting_uri="/id/asp/2010/17",
        affecting_class="ScottishAct",
        affecting_year="2010",
        affecting_number="17",
        affecting_provisions="s. 155(a)(ii)",
        affecting_title="Test Amendment Act",
        in_force_dates=[{"date": "2012-02-22", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPEAL
    assert ops[0].target.path == (
        ("section", "14"),
        ("subsection", "4"),
        ("paragraph", "a"),
    )
    assert ops[0].text_patch is not None
    assert ops[0].text_patch.selector.match_text == (
        "TEXT_WORD_and_IMMEDIATELY_FOLLOWING_paragraph_a"
    )

    base = IRStatute(
        statute_id="asp/2001/10",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="14",
                    attrs={"eId": "section-14"},
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="4",
                            attrs={"eId": "section-14-4"},
                            children=(
                                IRNode(
                                    kind=IRNodeKind.PARAGRAPH,
                                    label="a",
                                    text="first condition and",
                                    attrs={"eId": "section-14-4-a"},
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )

    replayed = replay_uk_ops(base, ops)
    assert replayed.body.children[0].children[0].children[0].text == "first condition"


def test_compile_nested_contextual_word_repeal_uses_child_anchor() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P4 xmlns="{_LEG_NS}" id="section-58-c-v">
          <Pnumber>v</Pnumber>
          <Text>v the word “and” immediately following paragraph (c)(ii) is repealed,</Text>
        </P4>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_nested_contextual_word_repeal",
        effect_type="word repealed",
        applied=True,
        requires_applied=False,
        modified="2016-04-15",
        affected_uri="/id/asp/2003/2",
        affected_class="ScottishAct",
        affected_year="2003",
        affected_number="2",
        affected_provisions="s. 61(6)(c)",
        affecting_uri="/id/asp/2015/6",
        affecting_class="ScottishAct",
        affecting_year="2015",
        affecting_number="6",
        affecting_provisions="s. 58(c)(v)",
        affecting_title="Test Amendment Act",
        in_force_dates=[{"date": "2016-04-15", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPEAL
    assert ops[0].target.path == (
        ("section", "61"),
        ("subsection", "6"),
        ("paragraph", "c"),
    )
    assert ops[0].text_patch is not None
    assert ops[0].text_patch.selector.match_text == (
        "TEXT_WORD_and_IMMEDIATELY_FOLLOWING_subparagraph_ii"
    )

    base = IRStatute(
        statute_id="asp/2003/2",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="61",
                    attrs={"eId": "section-61"},
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="6",
                            attrs={"eId": "section-61-6"},
                            children=(
                                IRNode(
                                    kind=IRNodeKind.PARAGRAPH,
                                    label="c",
                                    attrs={"eId": "section-61-6-c"},
                                    children=(
                                        IRNode(
                                            kind=IRNodeKind.SUBPARAGRAPH,
                                            label="i",
                                            text="the community body;",
                                            attrs={"eId": "section-61-6-c-i"},
                                        ),
                                        IRNode(
                                            kind=IRNodeKind.SUBPARAGRAPH,
                                            label="ii",
                                            text="the owner; and",
                                            attrs={"eId": "section-61-6-c-ii"},
                                        ),
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )

    replayed = replay_uk_ops(base, ops)
    paragraph = replayed.body.children[0].children[0].children[0]
    assert paragraph.children[0].text == "the community body;"
    assert paragraph.children[1].text == "the owner;"


def test_compile_after_second_insert_to_occurrence_text_replace() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P4 xmlns="{_LEG_NS}" id="section-155-c-ii">
          <Pnumber>ii</Pnumber>
          <Text>ii in subsection (6), after second “section” insert “ 14A(9) or ”.</Text>
        </P4>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_after_second_insert",
        effect_type="words inserted",
        applied=True,
        requires_applied=False,
        modified="2012-02-22",
        affected_uri="/id/asp/2001/10",
        affected_class="ScottishAct",
        affected_year="2001",
        affected_number="10",
        affected_provisions="s. 109(6)",
        affecting_uri="/id/asp/2010/17",
        affecting_class="ScottishAct",
        affecting_year="2010",
        affecting_number="17",
        affecting_provisions="s. 155(c)(ii)",
        affecting_title="Test Amendment Act",
        in_force_dates=[{"date": "2012-02-22", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].payload is None
    assert ops[0].target.path == (("section", "109"), ("subsection", "6"))
    assert ops[0].text_patch is not None
    assert ops[0].text_patch.kind is TextPatchKindEnum.REPLACE
    assert ops[0].text_patch.selector.match_text == "section"
    assert ops[0].text_patch.selector.occurrence == 2
    assert ops[0].text_patch.replacement == "section 14A(9) or "


def test_compile_after_the_word_there_is_inserted_to_text_replace() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P3 xmlns="{_LEG_NS}" id="schedule-4-paragraph-7-6-a">
          <Pnumber>a</Pnumber>
          <Text>a after the word “possession” there is inserted “ or an eviction order ”.</Text>
        </P3>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_after_the_word_there_is_inserted",
        effect_type="words inserted",
        applied=True,
        requires_applied=False,
        modified="2017-12-01",
        affected_uri="/id/asp/2001/10",
        affected_class="ScottishAct",
        affected_year="2001",
        affected_number="10",
        affected_provisions="Sch. 6 para. 1",
        affecting_uri="/id/asp/2016/19",
        affecting_class="ScottishAct",
        affecting_year="2016",
        affecting_number="19",
        affecting_provisions="Sch. 4 para. 7(6)(a)",
        affecting_title="Test Amendment Act",
        in_force_dates=[{"date": "2017-12-01", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].payload is None
    assert ops[0].target.path == (("schedule", "6"), ("paragraph", "1"))
    assert ops[0].text_patch is not None
    assert ops[0].text_patch.selector.match_text == "possession"
    assert ops[0].text_patch.replacement == "possession or an eviction order "


def test_compile_for_insert_to_text_replace() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P2 xmlns="{_LEG_NS}" id="section-13-a">
          <Pnumber>a</Pnumber>
          <Text>in paragraph 2(2), for “6” insert “ 12 ”</Text>
        </P2>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_for_insert_text_replace",
        effect_type="word inserted",
        applied=True,
        requires_applied=False,
        modified="2015-01-01",
        affected_uri="/id/asp/2001/10",
        affected_class="ScottishAct",
        affected_year="2001",
        affected_number="10",
        affected_provisions="Sch. 3 para. 2(2)",
        affecting_uri="/id/asp/2014/14",
        affecting_class="ScottishAct",
        affecting_year="2014",
        affecting_number="14",
        affecting_provisions="s. 13(a)",
        affecting_title="Test Amendment Act",
        in_force_dates=[{"date": "2015-01-01", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].text_patch is not None
    assert ops[0].text_patch.selector.match_text == "6"
    assert ops[0].text_patch.replacement == "6 12"
    assert _fragment_substitution(ops[0]) == [
        {
            "original": "6",
            "replacement": "6 12",
        }
    ]
    assert "text_rewrite_rule:uk_effect_for_insert_text_insertion_patch" in ops[0].provenance_tags


def test_compile_substituted_for_words_expands_body_sibling_paragraph_targets() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P2 xmlns="{_LEG_NS}" id="section-57-2">
          <Pnumber>2</Pnumber>
          <Text>In subsection (4), for paragraphs (a) and (b) substitute "in the presence of a representative of the postal operator".</Text>
        </P2>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_substituted_for_words_body_siblings",
        effect_type="substituted for words",
        applied=True,
        requires_applied=False,
        modified="2010-04-08",
        affected_uri="/id/ukpga/2000/26",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="26",
        affected_provisions="s. 106(4)(a)(b)",
        affecting_uri="/id/ukpga/2010/13",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2010",
        affecting_number="13",
        affecting_provisions="s. 57(2)",
        affecting_title="Postal Services Act 2010",
        in_force_dates=[{"date": "2010-04-08", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 2
    assert all(op.action is StructuralAction.REPLACE for op in ops)
    assert [op.target.path for op in ops] == [
        (("section", "106"), ("subsection", "4"), ("paragraph", "a")),
        (("section", "106"), ("subsection", "4"), ("paragraph", "b")),
    ]


def test_compile_are_substituted_words_to_text_replace() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P2 xmlns="{_LEG_NS}" id="schedule-1-paragraph-135-4">
          <Pnumber>4</Pnumber>
          <P2para>
            <Text>
              In paragraph 23 of Schedule 1 for the words "The Post Office"
              are substituted the words "A universal service provider".
            </Text>
          </P2para>
        </P2>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_are_substituted_words",
        effect_type="",
        applied=True,
        requires_applied=False,
        modified="2001-03-26",
        affected_uri="/id/ukpga/2000/23",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="23",
        affected_provisions="Sch. 1 para. 23",
        affecting_uri="/id/uksi/2001/1149",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2001",
        affecting_number="1149",
        affecting_provisions="Sch. 1 para. 135(4)",
        affecting_title="Regulation of Investigatory Powers (Communications Data) Order 2001",
        in_force_dates=[{"date": "2001-03-26", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].payload is None
    assert ops[0].text_patch is not None
    assert ops[0].text_patch.selector.match_text == "The Post Office"
    assert ops[0].text_patch.replacement == "A universal service provider"
    assert ops[0].text_patch is not None


def test_compile_quote_only_words_omitted_payload_to_text_repeal() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P4 xmlns="{_LEG_NS}" id="regulation-46-3-a-iii">
          <Pnumber>iii</Pnumber>
          <Text>“public works contract”, and</Text>
        </P4>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_quote_only_words_omitted",
        effect_type="words omitted",
        applied=True,
        requires_applied=False,
        modified="2024-10-28",
        affected_uri="/id/asc/2023/1",
        affected_class="ActOfSeneddCymru",
        affected_year="2023",
        affected_number="1",
        affected_provisions="s. 45(1)",
        affecting_uri="/id/wsi/2024/782",
        affecting_class="WelshStatutoryInstrument",
        affecting_year="2024",
        affecting_number="782",
        affecting_provisions="reg. 46(3)(a)(iii)",
        affecting_title="Procurement (Wales) Regulations 2024",
        in_force_dates=[{"date": "2024-10-28", "prospective": "false"}],
    )
    lowering_rejections: list[dict[str, Any]] = []

    ops = compile_effect_to_ir_ops(
        effect,
        extracted_el,
        sequence=0,
        lowering_rejections_out=lowering_rejections,
    )

    assert lowering_rejections == []
    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPEAL
    assert ops[0].payload is None
    assert ops[0].target.path == (("section", "45"), ("subsection", "1"))
    assert ops[0].text_patch is not None
    assert ops[0].text_patch.kind is TextPatchKindEnum.DELETE
    assert ops[0].text_patch.selector.match_text == "public works contract"
    assert ops[0].text_patch.replacement is None
    assert _fragment_substitution(ops[0]) == [
        {"original": "public works contract", "replacement": ""}
    ]
    assert (
        f"{_NOTE_TEXT_REWRITE_RULE}uk_effect_quote_only_omission_payload_text_patch"
        in ops[0].provenance_tags
    )
    assert uk_replay_mod._uk_op_allowed_by_authority_mode(ops[0], "source_text_only") == (
        True,
        None,
    )


def test_compile_quote_only_definition_list_omission_uses_definition_selector() -> None:
    source_root = ET.fromstring(
        f"""
        <Legislation xmlns="{_LEG_NS}">
          <P2 id="regulation-46-3">
            <Pnumber>3</Pnumber>
            <P2para>
              <Text>In section 45 (interpretation of Part 3), in subsection (1)—</Text>
              <P3 id="regulation-46-3-a">
                <Pnumber>a</Pnumber>
                <P3para>
                  <Text>omit the definitions of—</Text>
                  <P4 id="regulation-46-3-a-iii">
                    <Pnumber>iii</Pnumber>
                    <P4para><Text>“public works contract”,</Text></P4para>
                  </P4>
                </P3para>
              </P3>
            </P2para>
          </P2>
        </Legislation>
        """
    )
    extracted_el = next(el for el in source_root.iter() if el.get("id") == "regulation-46-3-a-iii")
    effect = UKEffectRecord(
        effect_id="uk_test_quote_only_definition_list_omission",
        effect_type="words omitted",
        applied=True,
        requires_applied=False,
        modified="2025-03-25",
        affected_uri="/id/asc/2023/1",
        affected_class="ActOfSeneddCymru",
        affected_year="2023",
        affected_number="1",
        affected_provisions="s. 45(1)",
        affecting_uri="/id/wsi/2024/782",
        affecting_class="WelshStatutoryInstrument",
        affecting_year="2024",
        affecting_number="782",
        affecting_provisions="reg. 46(3)(a)(iii)",
        affecting_title="Procurement (Wales) Regulations 2024",
        in_force_dates=[{"date": "2025-02-24", "prospective": "false"}],
    )
    lowering_records: list[dict[str, Any]] = []

    ops = compile_effect_to_ir_ops(
        effect,
        extracted_el,
        sequence=0,
        lowering_rejections_out=lowering_records,
        source_root=source_root,
    )

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPEAL
    assert ops[0].text_patch is not None
    assert ops[0].text_patch.selector.match_text == "TEXT_DEFINITION_ENTRY_public works contract"
    assert _fragment_substitution(ops[0]) == [
        {"original": "TEXT_DEFINITION_ENTRY_public works contract", "replacement": ""}
    ]
    assert (
        f"{_NOTE_TEXT_REWRITE_RULE}uk_effect_quote_only_definition_list_omission_text_patch"
        in ops[0].provenance_tags
    )
    assert [record["rule_id"] for record in lowering_records] == [
        "uk_effect_quote_only_definition_list_omission_text_patch"
    ]
    assert lowering_records[0]["blocking"] is False
    assert lowering_records[0]["source_parent_id"] == "regulation-46-3-a"


def test_compile_quote_only_non_definition_list_omission_stays_bare_text_repeal() -> None:
    source_root = ET.fromstring(
        f"""
        <Legislation xmlns="{_LEG_NS}">
          <P2 id="regulation-1">
            <Pnumber>1</Pnumber>
            <P2para>
              <Text>omit the following words—</Text>
              <P3 id="regulation-1-a">
                <Pnumber>a</Pnumber>
                <P3para><Text>“public works contract”,</Text></P3para>
              </P3>
            </P2para>
          </P2>
        </Legislation>
        """
    )
    extracted_el = next(el for el in source_root.iter() if el.get("id") == "regulation-1-a")
    effect = UKEffectRecord(
        effect_id="uk_test_quote_only_non_definition_list_omission",
        effect_type="words omitted",
        applied=True,
        requires_applied=False,
        modified="2025-03-25",
        affected_uri="/id/asc/2023/1",
        affected_class="ActOfSeneddCymru",
        affected_year="2023",
        affected_number="1",
        affected_provisions="s. 45(1)",
        affecting_uri="/id/wsi/2024/782",
        affecting_class="WelshStatutoryInstrument",
        affecting_year="2024",
        affecting_number="782",
        affecting_provisions="reg. 1(a)",
        affecting_title="Procurement (Wales) Regulations 2024",
        in_force_dates=[{"date": "2025-02-24", "prospective": "false"}],
    )
    lowering_records: list[dict[str, Any]] = []

    ops = compile_effect_to_ir_ops(
        effect,
        extracted_el,
        sequence=0,
        lowering_rejections_out=lowering_records,
        source_root=source_root,
    )

    assert len(ops) == 1
    assert ops[0].text_patch is not None
    assert ops[0].text_patch.selector.match_text == "public works contract"
    assert all(
        record["rule_id"] != "uk_effect_quote_only_definition_list_omission_text_patch"
        for record in lowering_records
    )


def test_compile_word_range_substitution_collapses_target_subtree() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P2 xmlns="{_LEG_NS}" id="schedule-3-paragraph-51-3">
          <Pnumber>3</Pnumber>
          <Text>In subsection (1A) for the words from “means—” to “Wales,” substitute “ means ”.</Text>
        </P2>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_word_range_substitution",
        effect_type="word substituted",
        applied=True,
        requires_applied=False,
        modified="2012-01-15",
        affected_uri="/id/ukpga/2000/22",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="22",
        affected_provisions="s. 34(1A)",
        affecting_uri="/id/ukpga/2011/20",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2011",
        affecting_number="20",
        affecting_provisions="Sch. 3 para. 51(3)",
        affecting_title="Public Bodies Act 2011",
        in_force_dates=[{"date": "2012-01-15", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].text_patch is not None
    assert ops[0].text_patch.selector.match_text == "TEXT_FROM_means—_TO_Wales,"
    assert ops[0].text_patch.replacement == "means"
    assert ops[0].text_patch is not None

    base = IRStatute(
        statute_id="ukpga/2000/22",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="34",
                    text="Section 34",
                    attrs={"eId": "section-34"},
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1A",
                            text="means—",
                            attrs={"eId": "section-34-1A"},
                            children=(
                                IRNode(
                                    kind=IRNodeKind.PARAGRAPH,
                                    label="a",
                                    text="England, or a country or territory outside the United Kingdom,",
                                ),
                                IRNode(kind=IRNodeKind.PARAGRAPH, label="b", text="or Wales,"),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )

    replayed = replay_uk_ops(base, ops)
    subsection = replayed.body.children[0].children[0]
    assert subsection.text == "means"
    assert subsection.children == ()


def test_compile_inserted_subsection_preserves_intro_text_with_children() -> None:
    extracted_el = ET.fromstring(
        f"""
        <BlockAmendment xmlns="{_LEG_NS}">
          <P2 id="section-34-1A">
            <Pnumber>1A</Pnumber>
            <Text>In this section “ relevant form of executive ” means—</Text>
            <P3 id="section-34-1A-a">
              <Pnumber>a</Pnumber>
              <Text>in relation to England, an executive.</Text>
            </P3>
            <P3 id="section-34-1A-b">
              <Pnumber>b</Pnumber>
              <Text>in relation to Wales, executive arrangements.</Text>
            </P3>
          </P2>
        </BlockAmendment>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_inserted_subsection_intro_text",
        effect_type="inserted",
        applied=True,
        requires_applied=False,
        modified="2007-12-30",
        affected_uri="/id/ukpga/2000/22",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="22",
        affected_provisions="s. 34(1A)",
        affecting_uri="/id/ukpga/2007/28",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2007",
        affecting_number="28",
        affecting_provisions="s. 65(3)",
        affecting_title="Local Government and Public Involvement in Health Act 2007",
        in_force_dates=[{"date": "2007-12-30", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 1
    assert ops[0].payload is not None
    assert ops[0].payload.kind == IRNodeKind.SUBSECTION
    assert ops[0].payload.label == "1A"
    assert "means—" in ops[0].payload.text
    assert [child.label for child in ops[0].payload.children] == ["a", "b"]


def test_compile_word_range_to_end_substitution_collapses_target_subtree() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P2 xmlns="{_LEG_NS}" id="schedule-3-paragraph-14-3">
          <Pnumber>3</Pnumber>
          <Text>In subsection (1) for the words from “of” to the end substitute “ of a mayor and cabinet executive are to be discharged in accordance with this section ”.</Text>
        </P2>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_word_range_to_end_substitution",
        effect_type="words substituted",
        applied=True,
        requires_applied=False,
        modified="2012-05-04",
        affected_uri="/id/ukpga/2000/22",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="22",
        affected_provisions="s. 14(1)",
        affecting_uri="/id/ukpga/2011/20",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2011",
        affecting_number="20",
        affecting_provisions="Sch. 3 para. 14(3)",
        affecting_title="Public Bodies Act 2011",
        in_force_dates=[{"date": "2012-05-04", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].text_patch is not None
    assert ops[0].text_patch.selector.match_text == "TEXT_FROM_of_TO_END"
    assert ops[0].text_patch.replacement == "of a mayor and cabinet executive are to be discharged in accordance with this section"
    assert ops[0].text_patch is not None

    base = IRStatute(
        statute_id="ukpga/2000/22",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="14",
                    text="Section 14",
                    attrs={"eId": "section-14"},
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1",
                            text="Subject to any provision made under section 18, 19 or 20, any functions which, under executive arrangements, are the responsibility of—",
                            attrs={"eId": "section-14-1"},
                            children=(
                                IRNode(kind=IRNodeKind.PARAGRAPH, label="a", text="a mayor and cabinet executive, or"),
                                IRNode(
                                    kind=IRNodeKind.PARAGRAPH,
                                    label="b",
                                    text="a leader and cabinet executive (England), are to be discharged in accordance with this section.",
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )

    replayed = replay_uk_ops(base, ops)
    subsection = replayed.body.children[0].children[0]
    assert subsection.text.endswith(
        "of a mayor and cabinet executive are to be discharged in accordance with this section"
    )
    assert subsection.children == ()


def test_compile_word_range_to_end_there_is_substituted() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P2 xmlns="{_LEG_NS}" id="schedule-6-paragraph-9-7">
          <Pnumber>7</Pnumber>
          <Text>7 In section 14(5)(b), for the words from “member” to the end there is substituted “police member of the Agency”.</Text>
        </P2>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_word_range_to_end_there_is_substituted",
        effect_type="words substituted",
        applied=True,
        requires_applied=True,
        modified="2007-04-01",
        affected_uri="/id/asp/2000/11",
        affected_class="ScottishAct",
        affected_year="2000",
        affected_number="11",
        affected_provisions="s. 14(5)(b)",
        affecting_uri="/id/asp/2006/10",
        affecting_class="ScottishAct",
        affecting_year="2006",
        affecting_number="10",
        affecting_provisions="sch. 6 para. 9(7)",
        affecting_title="Test Act",
        in_force_dates=[{"date": "2007-04-01", "prospective": "false"}],
    )
    lowering_records: list[dict[str, Any]] = []

    ops = compile_effect_to_ir_ops(
        effect,
        extracted_el,
        sequence=0,
        lowering_rejections_out=lowering_records,
    )

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].target.path == (("section", "14"), ("subsection", "5"), ("paragraph", "b"))
    assert ops[0].text_patch is not None
    assert ops[0].text_patch.selector.match_text == "TEXT_FROM_member_TO_END"
    assert ops[0].text_patch.replacement == "police member of the Agency"
    assert (
        f"{_NOTE_TEXT_REWRITE_RULE}uk_effect_range_to_end_there_is_substituted_text_patch"
        in ops[0].provenance_tags
    )
    assert [
        record["rule_id"]
        for record in lowering_records
        if record["rule_id"] == "uk_effect_range_to_end_there_is_substituted_text_patch"
    ] == ["uk_effect_range_to_end_there_is_substituted_text_patch"]
    assert lowering_records[0]["strict_disposition"] == "record"


def test_compile_word_range_to_end_uses_ordinal_start_occurrence() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P4 xmlns="{_LEG_NS}" id="schedule-5-paragraph-6-a-i">
          <Pnumber>i</Pnumber>
          <Text>i in subsection (4), the words from “and” in the second place where it occurs to the end are repealed; and</Text>
        </P4>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_word_range_to_end_second_occurrence",
        effect_type="words repealed",
        applied=True,
        requires_applied=False,
        modified="2010-01-01",
        affected_uri="/id/asp/2000/7",
        affected_class="ScottishAct",
        affected_year="2000",
        affected_number="7",
        affected_provisions="s. 19(4)",
        affecting_uri="/id/asp/2010/1",
        affecting_class="ScottishAct",
        affecting_year="2010",
        affecting_number="1",
        affecting_provisions="Sch. 5 para. 6(a)(i)",
        affecting_title="Test Amendment Act",
        in_force_dates=[{"date": "2010-01-01", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPEAL
    assert ops[0].text_patch is not None
    assert ops[0].text_patch.selector.match_text == "TEXT_FROM_and_TO_END"
    assert ops[0].text_patch.selector.occurrence == 2

    base = IRStatute(
        statute_id="asp/2000/7",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="19",
                    text="",
                    attrs={"eId": "section-19"},
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="4",
                            text="first limb and still live and repealed tail",
                            attrs={"eId": "section-19-4"},
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )

    replayed = replay_uk_ops(base, ops)
    assert replayed.body.children[0].children[0].text == "first limb and still live"


def test_compile_word_range_repeal_uses_parenthesized_ordinal_start_occurrence() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P3 xmlns="{_LEG_NS}" id="section-51-8-a">
          <Pnumber>a</Pnumber>
          <Text>a the words from “in” (where first occurring) to “Act” are repealed;</Text>
        </P3>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_word_range_parenthesized_first_occurrence",
        effect_type="words repealed",
        applied=True,
        requires_applied=True,
        modified="2005-08-04",
        affected_uri="/id/asp/2001/2",
        affected_class="ScottishAct",
        affected_year="2001",
        affected_number="2",
        affected_provisions="s. 82(1)",
        affecting_uri="/id/asp/2005/12",
        affecting_class="ScottishAct",
        affecting_year="2005",
        affecting_number="12",
        affecting_provisions="s. 51(8)(a)",
        affecting_title="Test Amendment Act",
        in_force_dates=[{"date": "2005-08-04", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPEAL
    assert ops[0].target.path == (("section", "82"), ("subsection", "1"))
    assert ops[0].text_patch is not None
    assert ops[0].text_patch.selector.match_text == "TEXT_FROM_in_TO_Act"
    assert ops[0].text_patch.selector.occurrence == 1
    assert ops[0].text_patch.replacement is None
    assert (
        f"{_NOTE_TEXT_REWRITE_RULE}uk_effect_range_occurrence_repeal_text_patch"
        in ops[0].provenance_tags
    )


def test_replace_missing_alphanumeric_subsection_does_not_sequence_match_paragraph() -> None:
    base = IRStatute(
        statute_id="ukpga/2000/22",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="45",
                    text="",
                    attrs={"eId": "section-45"},
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="8",
                            text="Subsection 8",
                            attrs={"eId": "section-45-8"},
                            children=(
                                IRNode(
                                    kind=IRNodeKind.PARAGRAPH,
                                    label="a",
                                    text="Paragraph a",
                                    attrs={"eId": "section-45-8-a"},
                                ),
                                IRNode(
                                    kind=IRNodeKind.PARAGRAPH,
                                    label="b",
                                    text="Paragraph b",
                                    attrs={"eId": "section-45-8-b"},
                                ),
                            ),
                        ),
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="9",
                            text="Subsection 9",
                            attrs={"eId": "section-45-9"},
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    op = LegalOperation(
        op_id="uk_test_replace_missing_45_8a",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "45"), ("subsection", "8A"))),
        payload=IRNode(
            kind=IRNodeKind.SUBSECTION,
            label="8A",
            text="Inserted subsection 8A",
            children=(
                IRNode(kind=IRNodeKind.PARAGRAPH, label="a", text="Inserted paragraph a"),
                IRNode(kind=IRNodeKind.PARAGRAPH, label="b", text="Inserted paragraph b"),
            ),
        ),
        source=OperationSource(statute_id="ukpga/2000/41", title="Amending Act"),
    )

    replayed = replay_uk_ops(base, [op], eid_map={})

    section = replayed.body.children[0]
    assert [child.label for child in section.children] == ["8", "8A", "9"]
    assert [child.label for child in section.children[0].children] == ["a", "b"]
    assert [child.label for child in section.children[1].children] == ["a", "b"]


def test_compile_substituted_for_old_subsection_series_retargets_first_anchor_and_repeals_tail() -> None:
    extracted_el = ET.fromstring(
        f"""
        <BlockAmendment xmlns="{_LEG_NS}">
          <P2>
            <Pnumber>5A</Pnumber>
            <Text>In this Part “couple” means—</Text>
            <P3>
              <Pnumber>a</Pnumber>
              <Text>first paragraph</Text>
            </P3>
            <P3>
              <Pnumber>b</Pnumber>
              <Text>second paragraph</Text>
            </P3>
          </P2>
        </BlockAmendment>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_substituted_for_old_series_single_new_subsection",
        effect_type="substituted for s. 3(5)(6)",
        applied=True,
        requires_applied=False,
        modified="2005-12-05",
        affected_uri="/id/ukpga/2002/21",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2002",
        affected_number="21",
        affected_provisions="s. 3(5A)",
        affecting_uri="/id/ukpga/2004/33",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2004",
        affecting_number="33",
        affecting_provisions="Sch. 24 para. 144(3)",
        affecting_title="Pensions Act 2004",
        in_force_dates=[{"date": "2005-12-05", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 2
    assert [op.action.value for op in ops] == ["replace", "repeal"]
    assert [op.target.path for op in ops] == [
        (("section", "3"), ("subsection", "5a")),
        (("section", "3"), ("subsection", "6")),
    ]
    assert ops[0].payload is not None
    assert ops[0].payload.kind == IRNodeKind.SUBSECTION
    assert ops[0].payload.label is not None
    assert ops[0].payload.label.lower() == "5a"


def test_replay_substituted_for_old_subsection_series_replaces_first_anchor_and_removes_tail() -> None:
    extracted_el = ET.fromstring(
        f"""
        <BlockAmendment xmlns="{_LEG_NS}">
          <P2>
            <Pnumber>5A</Pnumber>
            <Text>Inserted subsection 5A.</Text>
            <P3>
              <Pnumber>a</Pnumber>
              <Text>Paragraph a</Text>
            </P3>
            <P3>
              <Pnumber>b</Pnumber>
              <Text>Paragraph b</Text>
            </P3>
          </P2>
        </BlockAmendment>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_replay_substituted_for_old_series",
        effect_type="substituted for s. 3(5)(6)",
        applied=True,
        requires_applied=False,
        modified="2005-12-05",
        affected_uri="/id/ukpga/2002/21",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2002",
        affected_number="21",
        affected_provisions="s. 3(5A)",
        affecting_uri="/id/ukpga/2004/33",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2004",
        affecting_number="33",
        affecting_provisions="Sch. 24 para. 144(3)",
        affecting_title="Pensions Act 2004",
        in_force_dates=[{"date": "2005-12-05", "prospective": "false"}],
    )
    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)
    base = IRStatute(
        statute_id="ukpga/2002/21",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="3",
                    text="",
                    children=(
                        IRNode(kind=IRNodeKind.SUBSECTION, label="5", text="Old subsection 5"),
                        IRNode(kind=IRNodeKind.SUBSECTION, label="6", text="Old subsection 6"),
                        IRNode(kind=IRNodeKind.SUBSECTION, label="7", text="Keep subsection 7"),
                    ),
                ),
            ),
        ),
        supplements=(),
    )

    replayed = replay_uk_ops(base, ops)

    section = replayed.body.children[0]
    assert [child.label for child in section.children] == ["5a", "7"]


def test_compile_substituted_for_single_schedule_item_with_new_sibling_lowers_insert() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P4 xmlns="{_LEG_NS}">
          <Pnumber>ii</Pnumber>
          <Text>for paragraph (d) substitute—</Text>
          <BlockAmendment>
            <P4>
              <Pnumber>d</Pnumber>
              <Text>may not be made after entitlement to a relevant lump sum, and</Text>
            </P4>
            <P4>
              <Pnumber>e</Pnumber>
              <Text>may not be made after the 31 October deadline.</Text>
            </P4>
          </BlockAmendment>
        </P4>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_substituted_for_single_schedule_item_new_sibling",
        effect_type="substituted for Sch. 9 para. 127(2)(d)",
        applied=True,
        requires_applied=True,
        modified="2024-11-18",
        affected_uri="/id/ukpga/2004/12",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2004",
        affected_number="12",
        affected_provisions="Sch. 9 para. 127(2)(d)(e)",
        affecting_uri="/id/uksi/2024/1012",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2024",
        affecting_number="1012",
        affecting_provisions="reg. 17(3)(b)(ii)",
        affecting_title="Test Regulations",
        in_force_dates=[{"date": "2024-11-18", "prospective": "false"}],
    )
    observations: list[dict[str, object]] = []

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0, lowering_rejections_out=observations)

    assert len(ops) == 2
    assert [op.action for op in ops] == [StructuralAction.REPLACE, StructuralAction.INSERT]
    assert [op.target.path for op in ops] == [
        (("schedule", "9"), ("paragraph", "127"), ("subparagraph", "2"), ("item", "d")),
        (("schedule", "9"), ("paragraph", "127"), ("subparagraph", "2"), ("item", "e")),
    ]
    assert ops[0].payload is not None
    assert ops[0].payload.label == "d"
    assert ops[1].payload is not None
    assert ops[1].payload.label == "e"
    assert any(
        record["rule_id"] == "uk_effect_substituted_series_new_sibling_insert_lowered"
        and record["blocking"] is False
        and record["target"] == "schedule:9/paragraph:127/subparagraph:2/item:e"
        for record in observations
    )


def test_replay_substituted_for_single_schedule_item_new_sibling_inserts_without_absent_gap() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P4 xmlns="{_LEG_NS}">
          <Pnumber>ii</Pnumber>
          <Text>for paragraph (d) substitute—</Text>
          <BlockAmendment>
            <P4>
              <Pnumber>d</Pnumber>
              <Text>new d text</Text>
            </P4>
            <P4>
              <Pnumber>e</Pnumber>
              <Text>new e text</Text>
            </P4>
          </BlockAmendment>
        </P4>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_replay_substituted_for_schedule_item_new_sibling",
        effect_type="substituted for Sch. 9 para. 127(2)(d)",
        applied=True,
        requires_applied=True,
        modified="2024-11-18",
        affected_uri="/id/ukpga/2004/12",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2004",
        affected_number="12",
        affected_provisions="Sch. 9 para. 127(2)(d)(e)",
        affecting_uri="/id/uksi/2024/1012",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2024",
        affecting_number="1012",
        affecting_provisions="reg. 17(3)(b)(ii)",
        affecting_title="Test Regulations",
        in_force_dates=[{"date": "2024-11-18", "prospective": "false"}],
    )
    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)
    base = IRStatute(
        statute_id="ukpga/2004/12",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=()),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="9",
                text="",
                children=(
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="127",
                        text="",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SUBPARAGRAPH,
                                label="2",
                                text="",
                                children=(
                                    IRNode(kind=IRNodeKind.ITEM, label="d", text="old d text"),
                                    IRNode(kind=IRNodeKind.ITEM, label="f", text="existing f text"),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    adjudications: list[CompileAdjudication] = []

    result = replay_uk_ops(base, ops, adjudications_out=adjudications)

    items = result.supplements[0].children[0].children[0].children
    assert [(item.label, item.text) for item in items] == [
        ("d", "new d text"),
        ("e", "new e text"),
        ("f", "existing f text"),
    ]
    assert [adjudication.kind for adjudication in adjudications] == []


def test_compile_retargets_instruction_paragraph_to_affected_section() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P1 xmlns="{_LEG_NS}">
          <Pnumber>27</Pnumber>
          <Text>Section 100 of the Act shall apply in respect of-</Text>
          <P2>
            <Pnumber>a</Pnumber>
            <Text>first paragraph</Text>
          </P2>
          <P2>
            <Pnumber>b</Pnumber>
            <Text>second paragraph</Text>
          </P2>
        </P1>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_instruction_retarget_section",
        effect_type="substituted",
        applied=True,
        requires_applied=False,
        modified="2011-01-01",
        affected_uri="/id/ukpga/2000/26",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="26",
        affected_provisions="s. 100",
        affecting_uri="/id/ukpga/2011/5",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2011",
        affecting_number="5",
        affecting_provisions="Sch. 12 para. 27",
        affecting_title="Postal Services Act 2011",
        in_force_dates=[{"date": "2011-10-01", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 1
    assert ops[0].payload is not None
    assert ops[0].payload.kind == IRNodeKind.SECTION
    assert ops[0].payload.label == "100"
    assert [child.label for child in ops[0].payload.children] == ["a", "b"]


def test_substituted_for_words_effect_is_admitted_as_structural() -> None:
    effect = UKEffectRecord(
        effect_id="uk_test_substituted_for_words",
        effect_type="substituted for words",
        applied=True,
        requires_applied=False,
        modified="2015-02-12",
        affected_uri="/id/ukpga/2003/31",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2003",
        affected_number="31",
        affected_provisions="s. 5(1)",
        affecting_uri="/id/ukpga/2015/9",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2015",
        affecting_number="9",
        affecting_provisions="s. 72(4)(a)",
        affecting_title="Serious Crime Act 2015",
        in_force_dates=[{"date": "2015-05-05", "prospective": "false"}],
    )

    assert effect.is_structural is True


def test_compile_substituted_for_words_falls_back_to_structural_replace_when_payload_is_structural() -> None:
    extracted_el = ET.fromstring(
        f"""
        <BlockAmendment xmlns="{_LEG_NS}">
          <P2>
            <Pnumber>1</Pnumber>
            <P2para>
              <Text>A person guilty of an offence under section 1, 2 or 3.</Text>
            </P2para>
          </P2>
        </BlockAmendment>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_substituted_for_words_payload",
        effect_type="substituted for words",
        applied=True,
        requires_applied=False,
        modified="2015-02-12",
        affected_uri="/id/ukpga/2003/31",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2003",
        affected_number="31",
        affected_provisions="s. 5(1)",
        affecting_uri="/id/ukpga/2015/9",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2015",
        affecting_number="9",
        affecting_provisions="s. 72(4)(a)",
        affecting_title="Serious Crime Act 2015",
        in_force_dates=[{"date": "2015-05-05", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.REPLACE
    assert ops[0].target.path == (("section", "5"), ("subsection", "1"))
    assert ops[0].payload is not None
    assert ops[0].payload.kind == IRNodeKind.SUBSECTION
    assert ops[0].payload.label == "1"


def test_executor_replace_inserts_missing_matching_leaf_target() -> None:
    base = IRStatute(
        statute_id="ukpga/2003/31",
        title="Female Genital Mutilation Act 2003",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="5",
                    attrs={"eId": "section-5"},
                    children=(
                        IRNode(kind=IRNodeKind.PARAGRAPH, label="a", attrs={"eId": "section-5-a"}, text="para a"),
                        IRNode(kind=IRNodeKind.PARAGRAPH, label="b", attrs={"eId": "section-5-b"}, text="para b"),
                        IRNode(
                            kind=IRNodeKind.SUBSECTION, label="2", attrs={"eId": "section-5-2"}, text="subsection 2"
                        ),
                    ),
                ),
            ),
        ),
        metadata={},
    )
    op = LegalOperation(
        op_id="uk_test_replace_missing_subsection_1",
        sequence=0,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "5"), ("subsection", "1"))),
        payload=IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="subsection 1"),
        source=OperationSource(statute_id="ukpga/2015/9", title="Serious Crime Act 2015"),
        provenance_tags=(),
    )

    replayed = replay_uk_ops(base, [op])
    section5 = replayed.body.children[0]

    assert any(child.kind == IRNodeKind.SUBSECTION and child.label == "1" for child in section5.children)


def test_compile_schedule_title_ref_is_skipped() -> None:
    extracted_el = ET.fromstring(
        f"""
        <InlineAmendment xmlns="{_LEG_NS}">
          <Text>Social Mobility Commission</Text>
        </InlineAmendment>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_schedule_title_skip",
        effect_type="substituted",
        applied=True,
        requires_applied=False,
        modified="2016-03-16",
        affected_uri="/id/ukpga/2010/9",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2010",
        affected_number="9",
        affected_provisions="Sch. 1 title",
        affecting_uri="/id/ukpga/2016/7",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2016",
        affecting_number="7",
        affecting_provisions="s. 6(2)",
        affecting_title="Welfare Reform and Work Act 2016",
        in_force_dates=[{"date": "2016-03-16", "prospective": "false"}],
    )

    assert compile_effect_to_ir_ops(effect, extracted_el, sequence=0) == []


def test_compile_coming_into_force_list_item_is_skipped() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P3 xmlns="{_LEG_NS}">
          <Pnumber>c</Pnumber>
          <P3para>section 63 (deduction of trade union subscriptions from wages in public sector);</P3para>
        </P3>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_commencement_list_item_skip",
        effect_type="coming into force",
        applied=True,
        requires_applied=False,
        modified="2025-01-01",
        affected_uri="/id/ukpga/2025/36",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2025",
        affected_number="36",
        affected_provisions="s. 63",
        affecting_uri="/id/uksi/2025/1",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2025",
        affecting_number="1",
        affecting_provisions="art. 2(c)",
        affecting_title="Test Commencement Order",
        in_force_dates=[{"date": "2025-01-01", "prospective": "false"}],
    )

    assert compile_effect_to_ir_ops(effect, extracted_el, sequence=0) == []


def test_compile_empty_effect_type_does_not_infer_range_from_word_fragments() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P3 xmlns="{_LEG_NS}">
          <Pnumber>c</Pnumber>
          <P3para>section 63 (deduction of trade union subscriptions from wages in public sector);</P3para>
        </P3>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_no_false_from_to_range_inference",
        effect_type="",
        applied=True,
        requires_applied=False,
        modified="2025-01-01",
        affected_uri="/id/ukpga/2025/36",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2025",
        affected_number="36",
        affected_provisions="s. 63",
        affecting_uri="/id/uksi/2025/1",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2025",
        affecting_number="1",
        affecting_provisions="art. 2(c)",
        affecting_title="Test Commencement Order",
        in_force_dates=[{"date": "2025-01-01", "prospective": "false"}],
    )

    assert compile_effect_to_ir_ops(effect, extracted_el, sequence=0) == []


def test_compile_empty_effect_type_records_no_supported_action_rejection() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P3 xmlns="{_LEG_NS}">
          <Pnumber>c</Pnumber>
          <P3para>section 63 (deduction of trade union subscriptions from wages in public sector);</P3para>
        </P3>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_no_supported_action_rejection",
        effect_type="",
        applied=True,
        requires_applied=False,
        modified="2025-01-01",
        affected_uri="/id/ukpga/2025/36",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2025",
        affected_number="36",
        affected_provisions="s. 63",
        affecting_uri="/id/uksi/2025/1",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2025",
        affecting_number="1",
        affecting_provisions="art. 2(c)",
        affecting_title="Test Commencement Order",
        in_force_dates=[{"date": "2025-01-01", "prospective": "false"}],
    )
    lowering_rejections: list[dict[str, Any]] = []

    assert (
        compile_effect_to_ir_ops(
            effect,
            extracted_el,
            sequence=0,
            lowering_rejections_out=lowering_rejections,
        )
        == []
    )
    assert len(lowering_rejections) == 1
    rejection = lowering_rejections[0]
    assert rejection["rule_id"] == "uk_effect_lowering_no_supported_action_rejected"
    assert rejection["family"] == "unsupported_or_unresolved_action"
    assert rejection["phase"] == "lowering"
    assert rejection["effect_id"] == "uk_test_no_supported_action_rejection"
    assert rejection["reason_code"] == "no_supported_action"
    assert rejection["blocking"] is True
    assert rejection["strict_disposition"] == "block"
    assert rejection["quirks_disposition"] == "record"
    assert rejection["extracted_tag"] == "P3"
    assert rejection["has_extracted_source"] is True
    assert "trade union subscriptions" in rejection["extracted_text_preview"]


def test_compile_structural_effect_records_no_targets_rejection() -> None:
    effect = UKEffectRecord(
        effect_id="uk_test_no_targets_rejection",
        effect_type="repealed",
        applied=True,
        requires_applied=False,
        modified="2025-01-01",
        affected_uri="/id/ukpga/2025/36",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2025",
        affected_number="36",
        affected_provisions="",
        affecting_uri="/id/uksi/2025/1",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2025",
        affecting_number="1",
        affecting_provisions="art. 2(c)",
        affecting_title="Test Commencement Order",
        in_force_dates=[{"date": "2025-01-01", "prospective": "false"}],
    )
    lowering_rejections: list[dict[str, Any]] = []

    assert (
        compile_effect_to_ir_ops(
            effect,
            None,
            sequence=0,
            lowering_rejections_out=lowering_rejections,
        )
        == []
    )
    assert len(lowering_rejections) == 1
    rejection = lowering_rejections[0]
    assert rejection["rule_id"] == "uk_effect_lowering_no_targets_rejected"
    assert rejection["family"] == "target_resolution_recovery"
    assert rejection["phase"] == "lowering"
    assert rejection["effect_id"] == "uk_test_no_targets_rejection"
    assert rejection["reason_code"] == "no_affected_targets"
    assert rejection["affected_provisions"] == ""
    assert rejection["blocking"] is True
    assert rejection["strict_disposition"] == "block"
    assert rejection["quirks_disposition"] == "record"
    assert rejection["has_extracted_source"] is False


def test_compile_malformed_overlap_substitution_records_unlowered_rejection() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P2 xmlns="{_LEG_NS}">
          <Pnumber>2</Pnumber>
          <Text>In subsection (1), the relevant words are changed.</Text>
        </P2>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_overlap_substitution_parse_failed",
        effect_type="words substituted",
        applied=True,
        requires_applied=False,
        modified="2025-01-01",
        affected_uri="/id/ukpga/2025/36",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2025",
        affected_number="36",
        affected_provisions="s. 63(1)",
        affecting_uri="/id/uksi/2025/1",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2025",
        affecting_number="1",
        affecting_provisions="art. 2",
        affecting_title="Test Amendment Order",
        in_force_dates=[{"date": "2025-01-01", "prospective": "false"}],
    )
    lowering_rejections: list[dict[str, Any]] = []

    assert (
        compile_effect_to_ir_ops(
            effect,
            extracted_el,
            sequence=0,
            lowering_rejections_out=lowering_rejections,
        )
        == []
    )
    assert len(lowering_rejections) == 1
    rejection = lowering_rejections[0]
    assert rejection["rule_id"] == "uk_effect_overlap_substitution_unlowered"
    assert rejection["family"] == "lowering_filter"
    assert rejection["reason_code"] == "overlap_substitution_parse_failed"
    assert rejection["effect_id"] == "uk_test_overlap_substitution_parse_failed"
    assert rejection["affected_provisions"] == "s. 63(1)"
    assert rejection["unlowered_target_candidates"] == ["s. 63(1)"]
    assert rejection["target_candidate_count"] == 1
    assert rejection["parser"] == "parse_fragment_substitution"
    assert rejection["blocking"] is True
    assert rejection["strict_disposition"] == "block"
    assert rejection["quirks_disposition"] == "record"
    assert "relevant words are changed" in rejection["extracted_text_preview"]


def test_compile_broad_table_entry_instruction_rejects_host_repeal() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P2 xmlns="{_LEG_NS}">
          <Pnumber>4</Pnumber>
          <Text>In Part 11 of Schedule 2 (Crown Office and Procurator Fiscal Service)\u2013
          (a) in column 1 of the table, in entry number 1, \u201cin respect of notional capital charging\u201d is omitted; and
          (b) for \u201c\u00a3500,000\u201d there is substituted \u201c\u00a3600,000\u201d.</Text>
        </P2>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_broad_table_entry_instruction",
        effect_type="",
        applied=True,
        requires_applied=False,
        modified="2025-01-01",
        affected_uri="/id/asp/2000/2",
        affected_class="ScottishAct",
        affected_year="2000",
        affected_number="2",
        affected_provisions="Sch. 2 Pt. 2",
        affecting_uri="/id/ssi/2001/68",
        affecting_class="ScottishStatutoryInstrument",
        affecting_year="2001",
        affecting_number="68",
        affecting_provisions="art. 2(4)",
        affecting_title="Test Amendment Order",
        in_force_dates=[{"date": "2025-01-01", "prospective": "false"}],
    )
    lowering_rejections: list[dict[str, Any]] = []

    assert (
        compile_effect_to_ir_ops(
            effect,
            extracted_el,
            sequence=0,
            lowering_rejections_out=lowering_rejections,
        )
        == []
    )

    assert len(lowering_rejections) == 1
    rejection = lowering_rejections[0]
    assert rejection["rule_id"] == "uk_effect_table_entry_instruction_rejected"
    assert rejection["family"] == "source_table_elaboration"
    assert rejection["reason_code"] == "table_entry_instruction_without_cell_target"
    assert rejection["affected_provisions"] == "Sch. 2 Pt. 2"
    assert rejection["target_ref"] == "Sch. 2 Pt. 2"
    assert rejection["entry_shape"] == "numbered_entry"
    assert rejection["blocking"] is True
    assert rejection["strict_disposition"] == "block"
    assert rejection["quirks_disposition"] == "record"


def test_compile_broad_schedule_column_instruction_rejects_host_text_patch() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P4 xmlns="{_LEG_NS}">
          <Pnumber>ii</Pnumber>
          <Text>in column 2, for \u201c\u00a32,149,014,000\u201d there is substituted \u201c \u00a32,398,612,000 \u201d ; and</Text>
        </P4>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_broad_schedule_column_instruction",
        effect_type="word substituted",
        applied=True,
        requires_applied=True,
        modified="2012-09-15",
        affected_uri="/id/asp/2001/4",
        affected_class="ScottishAct",
        affected_year="2001",
        affected_number="4",
        affected_provisions="sch. 1",
        affecting_uri="/id/asp/2003/6",
        affecting_class="ScottishAct",
        affecting_year="2003",
        affecting_number="6",
        affecting_provisions="s. 8(b)(ii)",
        affecting_title="Budget (Scotland) Act 2003",
        in_force_dates=[{"date": "2003-03-19", "prospective": "false"}],
    )
    lowering_rejections: list[dict[str, Any]] = []

    assert (
        compile_effect_to_ir_ops(
            effect,
            extracted_el,
            sequence=0,
            lowering_rejections_out=lowering_rejections,
        )
        == []
    )

    assert len(lowering_rejections) == 1
    table_rejection = lowering_rejections[0]
    assert table_rejection["rule_id"] == "uk_effect_table_entry_instruction_rejected"
    assert table_rejection["family"] == "source_table_elaboration"
    assert table_rejection["reason_code"] == "table_entry_instruction_without_cell_target"
    assert table_rejection["affected_provisions"] == "sch. 1"
    assert table_rejection["target_ref"] == "sch. 1"
    assert table_rejection["entry_shape"] == "column_instruction"
    assert table_rejection["blocking"] is True
    assert table_rejection["strict_disposition"] == "block"


def test_compile_structural_schedule_part_repeal_not_table_entry_rejected() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P2 xmlns="{_LEG_NS}">
          <Pnumber>4</Pnumber>
          <Text>Part 11 of Schedule 2 is omitted.</Text>
        </P2>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_schedule_part_omit",
        effect_type="",
        applied=True,
        requires_applied=False,
        modified="2025-01-01",
        affected_uri="/id/asp/2000/2",
        affected_class="ScottishAct",
        affected_year="2000",
        affected_number="2",
        affected_provisions="Sch. 2 Pt. 11",
        affecting_uri="/id/ssi/2001/68",
        affecting_class="ScottishStatutoryInstrument",
        affecting_year="2001",
        affecting_number="68",
        affecting_provisions="art. 2(4)",
        affecting_title="Test Amendment Order",
        in_force_dates=[{"date": "2025-01-01", "prospective": "false"}],
    )
    lowering_rejections: list[dict[str, Any]] = []

    ops = compile_effect_to_ir_ops(
        effect,
        extracted_el,
        sequence=0,
        lowering_rejections_out=lowering_rejections,
    )

    assert len(ops) == 1
    assert ops[0].action == StructuralAction.REPEAL
    assert str(ops[0].target) == "schedule:2/part:11"
    assert not [
        record
        for record in lowering_rejections
        if record["rule_id"] == "uk_effect_table_entry_instruction_rejected"
    ]


def test_compile_schedule_note_target_rejects_paragraph_coercion() -> None:
    extracted_el = ET.fromstring(
        f"""
        <BlockAmendment xmlns="{_LEG_NS}">
          <P2>
            <Pnumber>1</Pnumber>
            <Text>In the case of a conservation body, insert the year and number of the relevant statutory instrument.</Text>
          </P2>
        </BlockAmendment>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_schedule_note_target",
        effect_type="substituted",
        applied=True,
        requires_applied=False,
        modified="2025-01-01",
        affected_uri="/id/asp/2000/5",
        affected_class="ScottishAct",
        affected_year="2000",
        affected_number="5",
        affected_provisions="Sch. 8 Note 1",
        affecting_uri="/id/asp/2003/9",
        affecting_class="ScottishAct",
        affecting_year="2003",
        affecting_number="9",
        affecting_provisions="Sch. 13 para. 17",
        affecting_title="Test Amendment Act",
        in_force_dates=[{"date": "2025-01-01", "prospective": "false"}],
    )
    lowering_rejections: list[dict[str, Any]] = []

    assert (
        compile_effect_to_ir_ops(
            effect,
            extracted_el,
            sequence=0,
            lowering_rejections_out=lowering_rejections,
        )
        == []
    )

    assert len(lowering_rejections) == 1
    rejection = lowering_rejections[0]
    assert rejection["rule_id"] == "uk_effect_schedule_note_target_rejected"
    assert rejection["family"] == "unsupported_target_facet"
    assert rejection["reason_code"] == "schedule_note_target_unsupported"
    assert rejection["affected_provisions"] == "Sch. 8 Note 1"
    assert rejection["target_ref"] == "Sch. 8 Note 1"
    assert rejection["blocking"] is True
    assert rejection["strict_disposition"] == "block"


def test_compile_first_second_occurrence_substitution_preserves_bounded_occurrences() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P4 xmlns="{_LEG_NS}">
          <Pnumber>i</Pnumber>
          <Text>for \u201c retained \u201d (in the first and second places it appears) substitute \u201cassimilated\u201d ;</Text>
        </P4>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_first_second_occurrence_substitution",
        effect_type="word substituted",
        applied=True,
        requires_applied=True,
        modified="2024-10-04",
        affected_uri="/id/ukpga/2020/2",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2020",
        affected_number="2",
        affected_provisions="s. 2(6)",
        affecting_uri="/id/ukpga/2023/28",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2023",
        affecting_number="28",
        affecting_provisions="Sch. 2 para. 10(2)(d)(i)",
        affecting_title="Retained EU Law (Revocation and Reform) Act 2023",
        in_force_dates=[{"date": "2024-01-01", "prospective": "false"}],
    )
    lowering_rejections: list[dict[str, Any]] = []

    ops = compile_effect_to_ir_ops(
        effect,
        extracted_el,
        sequence=0,
        lowering_rejections_out=lowering_rejections,
    )

    assert lowering_rejections == []
    assert len(ops) == 2
    assert [op.action for op in ops] == [
        StructuralAction.TEXT_REPLACE,
        StructuralAction.TEXT_REPLACE,
    ]
    assert [op.text_patch.selector.occurrence for op in ops if op.text_patch is not None] == [2, 1]
    assert [op.text_patch.selector.match_text for op in ops if op.text_patch is not None] == [
        " retained ",
        " retained ",
    ]
    assert [op.text_patch.replacement for op in ops if op.text_patch is not None] == [
        "assimilated",
        "assimilated",
    ]
    assert all(
        f"{_NOTE_TEXT_REWRITE_RULE}uk_effect_first_second_occurrence_substitution_text_patch"
        in op.provenance_tags
        for op in ops
    )
    assert [_fragment_substitution(op)[0]["occurrence"] for op in ops] == ["2", "1"]


def test_compile_post_quoted_ordinal_substitution_preserves_bounded_occurrence() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P2 xmlns="{_LEG_NS}">
          <Pnumber>b</Pnumber>
          <Text>in paragraph (b) for \u201csix months\u201d in the first place it occurs substitute \u201cfour months\u201d,</Text>
        </P2>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_post_quoted_ordinal_substitution",
        effect_type="words substituted",
        applied=True,
        requires_applied=False,
        modified="2020-10-01",
        affected_uri="/id/ukpga/2020/7",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2020",
        affected_number="7",
        affected_provisions="Sch. 29 para. 7(b)",
        affecting_uri="/id/ukpga/2020/7",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2020",
        affecting_number="7",
        affecting_provisions="Sch. 29 para. 4",
        affecting_title="Coronavirus Act 2020",
        in_force_dates=[{"date": "2020-10-01", "prospective": "false"}],
    )
    lowering_rejections: list[dict[str, Any]] = []

    ops = compile_effect_to_ir_ops(
        effect,
        extracted_el,
        sequence=0,
        lowering_rejections_out=lowering_rejections,
    )

    assert lowering_rejections == []
    assert len(ops) == 1
    op = ops[0]
    assert op.action == StructuralAction.TEXT_REPLACE
    assert op.text_patch is not None
    assert op.text_patch.selector.match_text == "six months"
    assert op.text_patch.selector.occurrence == 1
    assert op.text_patch.replacement == "four months"
    assert (
        f"{_NOTE_TEXT_REWRITE_RULE}uk_effect_post_quoted_ordinal_substitution_text_patch"
        in op.provenance_tags
    )
    assert _fragment_substitution(op)[0]["occurrence"] == "1"


def test_compile_post_quoted_where_ordinal_substitution_preserves_bounded_occurrence() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P4 xmlns="{_LEG_NS}">
          <Pnumber>ii</Pnumber>
          <Text>for the words \u201cmedical practitioner\u201d, where they second occur, substitute \u201c person who issued the certificate \u201d ;</Text>
        </P4>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_post_quoted_where_ordinal_substitution",
        effect_type="words substituted",
        applied=True,
        requires_applied=True,
        modified="2017-04-24",
        affected_uri="/id/asp/2000/4",
        affected_class="ScottishAct",
        affected_year="2000",
        affected_number="4",
        affected_provisions="s. 50(4)",
        affecting_uri="/id/asp/2005/13",
        affecting_class="ScottishAct",
        affecting_year="2005",
        affecting_number="13",
        affecting_provisions="s. 35(4)(c)(ii)",
        affecting_title="Smoking, Health and Social Care (Scotland) Act 2005",
        in_force_dates=[{"date": "2005-12-19", "prospective": "false"}],
    )
    lowering_rejections: list[dict[str, Any]] = []

    ops = compile_effect_to_ir_ops(
        effect,
        extracted_el,
        sequence=0,
        lowering_rejections_out=lowering_rejections,
    )

    assert lowering_rejections == []
    assert len(ops) == 1
    op = ops[0]
    assert op.action == StructuralAction.TEXT_REPLACE
    assert op.text_patch is not None
    assert op.text_patch.selector.match_text == "medical practitioner"
    assert op.text_patch.selector.occurrence == 2
    assert op.text_patch.replacement == "person who issued the certificate"
    assert (
        f"{_NOTE_TEXT_REWRITE_RULE}uk_effect_post_quoted_where_ordinal_substitution_text_patch"
        in op.provenance_tags
    )
    assert _fragment_substitution(op)[0]["occurrence"] == "2"


def test_compile_parenthesized_nested_quote_substitution_lowers_to_text_patch() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P1 xmlns="{_LEG_NS}">
          <Pnumber>5</Pnumber>
          <Text>In section 293(2)(d), for \u201c(\u201ca progress report\u201d) substitute \u201c(a \u201cprogress report\u201d)\u201d.</Text>
        </P1>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_parenthesized_nested_quote_substitution",
        effect_type="words substituted",
        applied=True,
        requires_applied=True,
        modified="2025-04-25",
        affected_uri="/id/ukpga/2020/17",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2020",
        affected_number="17",
        affected_provisions="s. 293(2)(d)",
        affecting_uri="/id/ukpga/2022/32",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2022",
        affecting_number="32",
        affecting_provisions="Sch. 21 para. 5",
        affecting_title="Police, Crime, Sentencing and Courts Act 2022",
        in_force_dates=[{"date": "2022-06-28", "prospective": "false"}],
    )
    lowering_rejections: list[dict[str, Any]] = []

    ops = compile_effect_to_ir_ops(
        effect,
        extracted_el,
        sequence=0,
        lowering_rejections_out=lowering_rejections,
    )

    assert lowering_rejections == []
    assert len(ops) == 1
    op = ops[0]
    assert op.action == StructuralAction.TEXT_REPLACE
    assert op.text_patch is not None
    assert op.text_patch.selector.match_text == "(\u201ca progress report\u201d)"
    assert op.text_patch.replacement == "(a \u201cprogress report\u201d)"
    assert (
        f"{_NOTE_TEXT_REWRITE_RULE}uk_effect_parenthesized_nested_quote_substitution_text_patch"
        in op.provenance_tags
    )


def test_compile_after_anchor_ordinal_insert_preserves_bounded_occurrence() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P4 xmlns="{_LEG_NS}">
          <Pnumber>i</Pnumber>
          <Text>after \u201cSecretary of State\u201d, in the first place it occurs, insert \u201cand the Northern Ireland Department\u201d ;</Text>
        </P4>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_after_anchor_ordinal_insert",
        effect_type="words inserted",
        applied=True,
        requires_applied=False,
        modified="2021-11-17",
        affected_uri="/id/ukpga/2021/30",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2021",
        affected_number="30",
        affected_provisions="Sch. 1 para. 12(1)",
        affecting_uri="/id/ukpga/2021/30",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2021",
        affecting_number="30",
        affecting_provisions="Sch. 3 para. 29(7)(a)(i)",
        affecting_title="Environment Act 2021",
        in_force_dates=[{"date": "2021-11-17", "prospective": "false"}],
    )
    lowering_rejections: list[dict[str, Any]] = []

    ops = compile_effect_to_ir_ops(
        effect,
        extracted_el,
        sequence=0,
        lowering_rejections_out=lowering_rejections,
    )

    assert lowering_rejections == []
    assert len(ops) == 1
    op = ops[0]
    assert op.action == StructuralAction.TEXT_REPLACE
    assert op.text_patch is not None
    assert op.text_patch.selector.match_text == "Secretary of State"
    assert op.text_patch.selector.occurrence == 1
    assert op.text_patch.replacement == "Secretary of State and the Northern Ireland Department"
    assert (
        f"{_NOTE_TEXT_REWRITE_RULE}uk_effect_after_quoted_anchor_ordinal_insert_text_patch"
        in op.provenance_tags
    )
    assert _fragment_substitution(op)[0]["occurrence"] == "1"


def test_compile_after_prefixed_anchor_ordinal_insert_preserves_bounded_occurrence() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P3 xmlns="{_LEG_NS}">
          <Pnumber>b</Pnumber>
          <Text>in paragraph (b), after the second \u201corder\u201d insert \u201cand does not fall within paragraph (aa)\u201d .</Text>
        </P3>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_after_prefixed_anchor_ordinal_insert",
        effect_type="words inserted",
        applied=True,
        requires_applied=True,
        modified="2025-04-25",
        affected_uri="/id/ukpga/2020/17",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2020",
        affected_number="17",
        affected_provisions="Sch. 10 para. 1",
        affecting_uri="/id/ukpga/2022/32",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2022",
        affecting_number="32",
        affecting_provisions="Sch. 14 para. 12(2)(b)",
        affecting_title="Police, Crime, Sentencing and Courts Act 2022",
        in_force_dates=[{"date": "2022-06-28", "prospective": "false"}],
    )
    lowering_rejections: list[dict[str, Any]] = []

    ops = compile_effect_to_ir_ops(
        effect,
        extracted_el,
        sequence=0,
        lowering_rejections_out=lowering_rejections,
    )

    assert lowering_rejections == []
    assert len(ops) == 1
    op = ops[0]
    assert op.action == StructuralAction.TEXT_REPLACE
    assert op.text_patch is not None
    assert op.text_patch.selector.match_text == "order"
    assert op.text_patch.selector.occurrence == 2
    assert op.text_patch.replacement == "order and does not fall within paragraph (aa)"
    assert (
        f"{_NOTE_TEXT_REWRITE_RULE}uk_effect_after_prefixed_quoted_anchor_ordinal_insert_text_patch"
        in op.provenance_tags
    )
    assert _fragment_substitution(op)[0]["occurrence"] == "2"


def test_compile_final_quoted_word_omission_lowers_to_final_occurrence_repeal() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P3 xmlns="{_LEG_NS}">
          <Pnumber>a</Pnumber>
          <Text>in paragraph (a), omit the final \u201cand\u201d;</Text>
        </P3>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_final_quoted_word_omit",
        effect_type="word omitted",
        applied=True,
        requires_applied=True,
        modified="2025-04-25",
        affected_uri="/id/ukpga/2020/17",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2020",
        affected_number="17",
        affected_provisions="Sch. 16 para. 14(2)(a)",
        affecting_uri="/id/ukpga/2022/32",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2022",
        affecting_number="32",
        affecting_provisions="Sch. 14 para. 13(7)(a)",
        affecting_title="Police, Crime, Sentencing and Courts Act 2022",
        in_force_dates=[{"date": "2022-06-28", "prospective": "false"}],
    )
    lowering_rejections: list[dict[str, Any]] = []

    ops = compile_effect_to_ir_ops(
        effect,
        extracted_el,
        sequence=0,
        lowering_rejections_out=lowering_rejections,
    )

    assert lowering_rejections == []
    assert len(ops) == 1
    op = ops[0]
    assert op.action == StructuralAction.TEXT_REPEAL
    assert op.text_patch is not None
    assert op.text_patch.selector.match_text == "and"
    assert op.text_patch.selector.occurrence == -1
    assert op.text_patch.replacement is None
    assert f"{_NOTE_TEXT_REWRITE_RULE}uk_effect_final_quoted_word_omit_text_patch" in op.provenance_tags
    assert _fragment_substitution(op)[0]["occurrence"] == "-1"


def test_compile_multi_anchor_overlap_substitution_records_unlowered_rejection() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P2 xmlns="{_LEG_NS}">
          <Pnumber>2</Pnumber>
          <Text>In paragraphs (a) and (b), the overlapping words are changed.</Text>
        </P2>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_overlap_substitution_arity_failed",
        effect_type="words substituted",
        applied=True,
        requires_applied=False,
        modified="2025-01-01",
        affected_uri="/id/ukpga/2025/36",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2025",
        affected_number="36",
        affected_provisions="s. 63(1)(a)(b)",
        affecting_uri="/id/uksi/2025/1",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2025",
        affecting_number="1",
        affecting_provisions="art. 2",
        affecting_title="Test Amendment Order",
        in_force_dates=[{"date": "2025-01-01", "prospective": "false"}],
    )
    lowering_rejections: list[dict[str, Any]] = []

    assert (
        compile_effect_to_ir_ops(
            effect,
            extracted_el,
            sequence=0,
            lowering_rejections_out=lowering_rejections,
        )
        == []
    )
    assert len(lowering_rejections) == 1
    rejection = lowering_rejections[0]
    assert rejection["rule_id"] == "uk_effect_overlap_substitution_unlowered"
    assert rejection["reason_code"] == "overlap_substitution_arity_unsupported"
    assert rejection["effect_id"] == "uk_test_overlap_substitution_arity_failed"
    assert rejection["original_target_candidates"] == ["s. 63(1)(a)", "s. 63(1)(b)"]
    assert rejection["unlowered_target_candidates"] == ["s. 63(1)(a)", "s. 63(1)(b)"]
    assert rejection["target_candidate_count"] == 2
    assert rejection["strict_disposition"] == "block"
    assert rejection["quirks_disposition"] == "record"


def test_compile_word_omission_explicit_subsection_omit_reclassifies_to_repeal() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P3 xmlns="{_LEG_NS}">
          <Pnumber>b</Pnumber>
          <P3para>omit subsection (2).</P3para>
        </P3>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_word_omission_structural_subsection_repeal",
        effect_type="words omitted",
        applied=True,
        requires_applied=False,
        modified="2024-01-01",
        affected_uri="/id/ukpga/2023/29",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2023",
        affected_number="29",
        affected_provisions="s. 80(2)",
        affecting_uri="/id/uksi/2023/1424",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2023",
        affecting_number="1424",
        affecting_provisions="Sch. para. 107(9)(b)",
        affecting_title="Test Amendment Regulations",
        in_force_dates=[{"date": "2024-01-01", "prospective": "false"}],
    )
    lowering_observations: list[dict[str, Any]] = []

    ops = compile_effect_to_ir_ops(
        effect,
        extracted_el,
        sequence=0,
        lowering_rejections_out=lowering_observations,
    )

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.REPEAL
    assert ops[0].target.path == (("section", "80"), ("subsection", "2"))
    assert ops[0].payload is None
    assert ops[0].text_patch is None
    assert len(lowering_observations) == 1
    observation = lowering_observations[0]
    assert observation["rule_id"] == "uk_effect_word_omission_structural_subsection_repeal_reclassified"
    assert observation["family"] == "lowering_normalization"
    assert observation["reason_code"] == "word_level_feed_row_explicitly_omits_target_subsection"
    assert observation["blocking"] is False
    assert observation["strict_disposition"] == "record"
    assert observation["quirks_disposition"] == "record"
    assert observation["target_ref"] == "s. 80(2)"
    assert observation["source_target_kind"] == "subsection"
    assert observation["source_target_label"] == "2"


def test_compile_word_omission_structural_subsection_omit_does_not_hijack_mismatch() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P3 xmlns="{_LEG_NS}">
          <Pnumber>b</Pnumber>
          <P3para>omit subsection (2).</P3para>
        </P3>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_word_omission_structural_subsection_mismatch",
        effect_type="words omitted",
        applied=True,
        requires_applied=False,
        modified="2024-01-01",
        affected_uri="/id/ukpga/2023/29",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2023",
        affected_number="29",
        affected_provisions="s. 80(3)",
        affecting_uri="/id/uksi/2023/1424",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2023",
        affecting_number="1424",
        affecting_provisions="Sch. para. 107(9)(b)",
        affecting_title="Test Amendment Regulations",
        in_force_dates=[{"date": "2024-01-01", "prospective": "false"}],
    )
    lowering_rejections: list[dict[str, Any]] = []

    ops = compile_effect_to_ir_ops(
        effect,
        extracted_el,
        sequence=0,
        lowering_rejections_out=lowering_rejections,
    )

    assert ops == []
    assert [record["rule_id"] for record in lowering_rejections] == [
        "uk_effect_overlap_substitution_unlowered"
    ]
    assert lowering_rejections[0]["blocking"] is True
    assert lowering_rejections[0]["unlowered_target_candidates"] == ["s. 80(3)"]


def test_compile_plain_text_schedule_sibling_omission_expands_targets() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P2 xmlns="{_LEG_NS}">
          <Pnumber>11</Pnumber>
          <P2para>In Schedule 1, in paragraph 1(1), omit paragraphs (b) and (c).</P2para>
        </P2>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_plain_text_schedule_sibling_omission",
        effect_type="omitted",
        applied=True,
        requires_applied=False,
        modified="2016-03-16",
        affected_uri="/id/ukpga/2010/9",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2010",
        affected_number="9",
        affected_provisions="Sch. 1 para. 1(1)(b)(c)",
        affecting_uri="/id/ukpga/2016/7",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2016",
        affecting_number="7",
        affecting_provisions="s. 7(11)",
        affecting_title="Welfare Reform and Work Act 2016",
        in_force_dates=[{"date": "2016-03-16", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 2
    assert [op.target.path for op in ops] == [
        (("schedule", "1"), ("paragraph", "1"), ("subparagraph", "1"), ("item", "b")),
        (("schedule", "1"), ("paragraph", "1"), ("subparagraph", "1"), ("item", "c")),
    ]


def test_compile_plain_text_body_sibling_repeal_expands_targets() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P3 xmlns="{_LEG_NS}">
          <Pnumber>b</Pnumber>
          <P3para>subsections (1) and (2) are repealed.</P3para>
        </P3>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_plain_text_body_sibling_repeal",
        effect_type="repealed",
        applied=True,
        requires_applied=False,
        modified="2012-03-08",
        affected_uri="/id/ukpga/2010/9",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2010",
        affected_number="9",
        affected_provisions="s. 13(1) (2)",
        affecting_uri="/id/ukpga/2012/5",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2012",
        affecting_number="5",
        affecting_provisions="Sch. 13 para. 7(b)",
        affecting_title="Welfare Reform Act 2012",
        in_force_dates=[{"date": "2012-03-08", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 2
    assert [op.target.path for op in ops] == [
        (("section", "13"), ("subsection", "1")),
        (("section", "13"), ("subsection", "2")),
    ]


def test_compile_metadata_only_schedule_sibling_repeal_expands_targets() -> None:
    effect = UKEffectRecord(
        effect_id="uk_test_metadata_only_schedule_sibling_repeal",
        effect_type="repealed",
        applied=True,
        requires_applied=False,
        modified="2018-08-30",
        affected_uri="/id/ukpga/2000/23",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="23",
        affected_provisions="Sch. 4 para. 8(10)(11)",
        affecting_uri="/id/ukpga/2016/25",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2016",
        affecting_number="25",
        affecting_provisions="Sch. 10 Pt. 8",
        affecting_title="Investigatory Powers Act 2016",
        in_force_dates=[{"date": "2018-08-30", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, None, sequence=0)

    assert len(ops) == 2
    assert [op.target.path for op in ops] == [
        (("schedule", "4"), ("paragraph", "8"), ("subparagraph", "10")),
        (("schedule", "4"), ("paragraph", "8"), ("subparagraph", "11")),
    ]


def test_compile_metadata_only_body_trailing_sibling_paragraph_repeal_expands_targets() -> None:
    effect = UKEffectRecord(
        effect_id="uk_test_metadata_only_body_trailing_sibling_paragraph_repeal",
        effect_type="repealed",
        applied=True,
        requires_applied=False,
        modified="2020-12-31",
        affected_uri="/id/ukpga/2000/41",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="41",
        affected_provisions="s. 54(8)(b)(c)",
        affecting_uri="/id/uksi/2018/1310",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2018",
        affecting_number="1310",
        affecting_provisions="Sch. 1 Pt. 1",
        affecting_title="The Electoral Law Act (Northern Ireland) 1962 and Representation of the People Act 1983 (Amendment) Regulations 2018",
        in_force_dates=[{"date": "2020-12-31", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, None, sequence=0)

    assert len(ops) == 2
    assert [op.target.path for op in ops] == [
        (("section", "54"), ("subsection", "8"), ("paragraph", "b")),
        (("section", "54"), ("subsection", "8"), ("paragraph", "c")),
    ]


def test_compile_metadata_only_body_trailing_sibling_paragraph_repeal_with_lettered_subsection_expands_targets() -> (
    None
):
    effect = UKEffectRecord(
        effect_id="uk_test_metadata_only_body_trailing_sibling_paragraph_repeal_lettered_subsection",
        effect_type="repealed",
        applied=True,
        requires_applied=False,
        modified="2015-10-25",
        affected_uri="/id/ukpga/2003/30",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2003",
        affected_number="30",
        affected_provisions="s. 1(1A)(a)(b)(c)",
        affecting_uri="/id/ukpga/2008/32",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2008",
        affecting_number="32",
        affecting_provisions="s. 87(1)(b) Sch. 6",
        affecting_title="Climate Change Act 2008",
        in_force_dates=[{"date": "2009-01-26", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, None, sequence=0)

    assert len(ops) == 3
    assert [op.target.path for op in ops] == [
        (("section", "1"), ("subsection", "1a"), ("paragraph", "a")),
        (("section", "1"), ("subsection", "1a"), ("paragraph", "b")),
        (("section", "1"), ("subsection", "1a"), ("paragraph", "c")),
    ]


def test_compile_insert_backfills_missing_subsection_label_from_target() -> None:
    extracted_el = ET.fromstring(
        f"""
        <BlockAmendment xmlns="{_LEG_NS}">
          <Subsection>
            <Text>Inserted subsection text.</Text>
          </Subsection>
        </BlockAmendment>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_insert_backfills_missing_subsection_label",
        effect_type="inserted",
        applied=True,
        requires_applied=False,
        modified="2015-12-14",
        affected_uri="/id/ukpga/2001/11",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2001",
        affected_number="11",
        affected_provisions="s. 7(4A)",
        affecting_uri="/id/ukpga/2002/16",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2002",
        affecting_number="16",
        affecting_provisions="Sch. 2 para. 45(2)",
        affecting_title="State Pension Credit Act 2002",
        in_force_dates=[{"date": "2003-10-06", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.INSERT
    assert ops[0].target.path == (("section", "7"), ("subsection", "4a"))
    assert ops[0].payload is not None
    assert ops[0].payload.kind == IRNodeKind.SUBSECTION
    assert ops[0].payload.label == "4a"


def test_compile_insert_backfills_missing_paragraph_label_from_target() -> None:
    extracted_el = ET.fromstring(
        f"""
        <BlockAmendment xmlns="{_LEG_NS}">
          <P3>
            <P3para>state pension credit;</P3para>
          </P3>
        </BlockAmendment>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_insert_backfills_missing_paragraph_label",
        effect_type="inserted",
        applied=True,
        requires_applied=False,
        modified="2015-12-14",
        affected_uri="/id/ukpga/2001/11",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2001",
        affected_number="11",
        affected_provisions="s. 9(1)(bb)",
        affecting_uri="/id/ukpga/2002/16",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2002",
        affecting_number="16",
        affecting_provisions="Sch. 2 para. 46(2)",
        affecting_title="State Pension Credit Act 2002",
        in_force_dates=[{"date": "2003-10-06", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.INSERT
    assert ops[0].target.path == (("section", "9"), ("subsection", "1"), ("paragraph", "bb"))
    assert ops[0].payload is not None
    assert ops[0].payload.kind == IRNodeKind.PARAGRAPH
    assert ops[0].payload.label == "bb"


def test_compile_metadata_only_insert_uses_metadata_fallback_placeholder() -> None:
    effect = UKEffectRecord(
        effect_id="uk_test_metadata_only_insert_fallback",
        effect_type="inserted",
        applied=True,
        requires_applied=False,
        modified="2024-01-01",
        affected_uri="/id/ukpga/2000/10",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="10",
        affected_provisions="s. 57(2)",
        affecting_uri="/id/uksi/2000/2040",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2000",
        affecting_number="2040",
        affecting_provisions="Sch. 2 para. 7",
        affecting_title="Missing Affecting Act",
        in_force_dates=[{"date": "2024-01-01", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, None, sequence=0, fallback_for_missing_extracted_source=True)

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.INSERT
    assert ops[0].target.path == (("section", "57"), ("subsection", "2"))
    assert ops[0].payload is not None
    assert ops[0].payload.kind == IRNodeKind.SUBSECTION
    assert ops[0].payload.label == "2"
    assert ops[0].payload.text == "[inserted by metadata source only: uk_test_metadata_only_insert_fallback]"
    assert any(
        note.startswith(_NOTE_METADATA_SOURCE_FALLBACK + "uk_test_metadata_only_insert_fallback")
        for note in ops[0].provenance_tags
    )


def test_compile_metadata_only_replace_without_extracted_text_is_skipped() -> None:
    effect = UKEffectRecord(
        effect_id="uk_test_metadata_only_replace_fallback",
        effect_type="substituted",
        applied=True,
        requires_applied=False,
        modified="2024-01-01",
        affected_uri="/id/ukpga/2000/10",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="10",
        affected_provisions="s. 57(2)",
        affecting_uri="/id/uksi/2000/2040",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2000",
        affecting_number="2040",
        affecting_provisions="Sch. 2 para. 7",
        affecting_title="Missing Affecting Act",
        in_force_dates=[{"date": "2024-01-01", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(
        effect,
        None,
        sequence=0,
        fallback_for_missing_extracted_source=True,
    )

    assert ops == []


def test_compile_metadata_only_repeal_without_extracted_text_still_compiles() -> None:
    effect = UKEffectRecord(
        effect_id="uk_test_metadata_only_repeal_still_applied",
        effect_type="repealed",
        applied=True,
        requires_applied=False,
        modified="2024-01-01",
        affected_uri="/id/ukpga/2000/10",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="10",
        affected_provisions="s. 57(2)",
        affecting_uri="/id/uksi/2000/2040",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2000",
        affecting_number="2040",
        affecting_provisions="Sch. 2 para. 7",
        affecting_title="Missing Affecting Act",
        in_force_dates=[{"date": "2024-01-01", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(
        effect,
        None,
        sequence=0,
        fallback_for_missing_extracted_source=True,
    )

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.REPEAL
    assert ops[0].target.path == (("section", "57"), ("subsection", "2"))


def test_compile_metadata_only_schedule_part_insert_uses_metadata_fallback_placeholder() -> None:
    effect = UKEffectRecord(
        effect_id="uk_test_metadata_only_schedule_part_insert_fallback",
        effect_type="inserted",
        applied=True,
        requires_applied=False,
        modified="2024-01-01",
        affected_uri="/id/ukpga/2000/10",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="10",
        affected_provisions="Sch. 1 Pt. 3 para. 7",
        affecting_uri="/id/uksi/2000/2040",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2000",
        affecting_number="2040",
        affecting_provisions="Sch. 2 para. 7",
        affecting_title="Missing Affecting Act",
        in_force_dates=[{"date": "2024-01-01", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, None, sequence=0, fallback_for_missing_extracted_source=True)

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.INSERT
    assert ops[0].target.path == (("schedule", "1"), ("part", "3"), ("paragraph", "7"))
    assert ops[0].payload is not None
    assert ops[0].payload.kind == IRNodeKind.PARAGRAPH
    assert ops[0].payload.label == "7"
    assert (
        ops[0].payload.text == "[inserted by metadata source only: uk_test_metadata_only_schedule_part_insert_fallback]"
    )
    assert any(
        note.startswith(_NOTE_METADATA_SOURCE_FALLBACK + "uk_test_metadata_only_schedule_part_insert_fallback")
        for note in ops[0].provenance_tags
    )


def test_compile_metadata_only_schedule_trailing_sibling_items_expand_targets() -> None:
    effect = UKEffectRecord(
        effect_id="uk_test_metadata_only_schedule_trailing_sibling_items",
        effect_type="repealed",
        applied=True,
        requires_applied=False,
        modified="2016-03-16",
        affected_uri="/id/ukpga/2010/9",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2010",
        affected_number="9",
        affected_provisions="Sch. 1 para. 1(1)(b)(c)",
        affecting_uri="/id/ukpga/2016/7",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2016",
        affecting_number="7",
        affecting_provisions="s. 7(11)",
        affecting_title="Welfare Reform and Work Act 2016",
        in_force_dates=[{"date": "2016-03-16", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, None, sequence=0)

    assert len(ops) == 2
    assert [op.target.path for op in ops] == [
        (("schedule", "1"), ("paragraph", "1"), ("subparagraph", "1"), ("item", "b")),
        (("schedule", "1"), ("paragraph", "1"), ("subparagraph", "1"), ("item", "c")),
    ]


def test_compile_metadata_only_body_trailing_roman_sibling_subparagraphs_expand_targets() -> None:
    extracted_el = ET.fromstring(
        f"""
        <BlockAmendment xmlns="{_LEG_NS}">
          <P3>
            <Pnumber>vi</Pnumber>
            <P3para>petition officers in relation to recall petitions, and</P3para>
          </P3>
          <P3>
            <Pnumber>vii</Pnumber>
            <P3para>accredited campaigners within the meaning of Schedule 3 to the Recall of MPs Act 2015.</P3para>
          </P3>
        </BlockAmendment>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_metadata_only_body_trailing_roman_sibling_subparagraphs",
        effect_type="inserted",
        applied=True,
        requires_applied=False,
        modified="2016-03-04",
        affected_uri="/id/ukpga/2000/41",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="41",
        affected_provisions="s. 10(3)(a)(vi)(vii)",
        affecting_uri="/id/ukpga/2015/25",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2015",
        affecting_number="25",
        affecting_provisions="Sch. 6 para. 3(7)",
        affecting_title="Recall of MPs Act 2015",
        in_force_dates=[{"date": "2016-03-04", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 2
    assert [op.target.path for op in ops] == [
        (("section", "10"), ("subsection", "3"), ("paragraph", "a"), ("subparagraph", "vi")),
        (("section", "10"), ("subsection", "3"), ("paragraph", "a"), ("subparagraph", "vii")),
    ]
    payloads = [payload for op in ops if (payload := op.payload) is not None]
    assert [payload.kind for payload in payloads] == [
        IRNodeKind.PARAGRAPH,
        IRNodeKind.PARAGRAPH,
    ]


def test_compile_metadata_nested_roman_subparagraph_target_is_not_split_into_siblings() -> None:
    extracted_el = ET.fromstring(
        f"""
        <BlockAmendment xmlns="{_LEG_NS}">
          <P4>
            <Pnumber>i</Pnumber>
            <P4para>nested replacement text</P4para>
          </P4>
        </BlockAmendment>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_metadata_nested_roman_subparagraph_target",
        effect_type="substituted",
        applied=True,
        requires_applied=False,
        modified="2013-04-01",
        affected_uri="/id/ukpga/2001/11",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2001",
        affected_number="11",
        affected_provisions="s. 8(2)(b)(i)",
        affecting_uri="/id/ukpga/2012/5",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2012",
        affecting_number="5",
        affecting_provisions="Sch. 14 Pt. 1",
        affecting_title="Welfare Reform Act 2012",
        in_force_dates=[{"date": "2013-04-01", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 1
    assert ops[0].target.path == (
        ("section", "8"),
        ("subsection", "2"),
        ("paragraph", "b"),
        ("subparagraph", "i"),
    )


def test_compile_replace_paragraph_preserves_trailing_block_subparagraph_siblings() -> None:
    extracted_el = ET.fromstring(
        f"""
        <BlockAmendment xmlns="{_LEG_NS}">
          <P3>
            <Pnumber>b</Pnumber>
            <P3para>Paragraph b head text.</P3para>
          </P3>
          <P4>
            <Pnumber>i</Pnumber>
            <P4para>Subparagraph i.</P4para>
          </P4>
          <P4>
            <Pnumber>ii</Pnumber>
            <P4para>Subparagraph ii.</P4para>
          </P4>
          <P4>
            <Pnumber>iii</Pnumber>
            <P4para>Subparagraph iii.</P4para>
          </P4>
        </BlockAmendment>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_replace_paragraph_with_trailing_block_subparagraphs",
        effect_type="substituted",
        applied=True,
        requires_applied=False,
        modified="2013-04-01",
        affected_uri="/id/ukpga/2001/11",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2001",
        affected_number="11",
        affected_provisions="s. 7(9)(b)",
        affecting_uri="/id/ukpga/2012/5",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2012",
        affecting_number="5",
        affecting_provisions="Sch. 14 Pt. 1",
        affecting_title="Welfare Reform Act 2012",
        in_force_dates=[{"date": "2013-04-01", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 1
    assert ops[0].target.path == (
        ("section", "7"),
        ("subsection", "9"),
        ("paragraph", "b"),
    )
    assert ops[0].payload is not None
    assert ops[0].payload.kind == IRNodeKind.PARAGRAPH
    assert [child.kind for child in ops[0].payload.children] == [
        IRNodeKind.SUBPARAGRAPH,
        IRNodeKind.SUBPARAGRAPH,
        IRNodeKind.SUBPARAGRAPH,
    ]
    assert [child.label for child in ops[0].payload.children] == ["i", "ii", "iii"]


def test_compile_metadata_only_body_letter_range_repeal_expands_targets() -> None:
    effect = UKEffectRecord(
        effect_id="uk_test_metadata_only_body_letter_range_repeal",
        effect_type="repealed",
        applied=True,
        requires_applied=False,
        modified="2012-07-01",
        affected_uri="/id/ukpga/2000/22",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="22",
        affected_provisions="s. 49(6)(c)-(e)",
        affecting_uri="/id/ukpga/2011/20",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2011",
        affecting_number="20",
        affecting_provisions="Sch. 4 para. 8(6)(b) Sch. 25 Pt. 5",
        affecting_title="Police Reform and Social Responsibility Act 2011",
        in_force_dates=[{"date": "2012-07-01", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, None, sequence=0)

    assert len(ops) == 3
    assert [op.target.path for op in ops] == [
        (("section", "49"), ("subsection", "6"), ("paragraph", "c")),
        (("section", "49"), ("subsection", "6"), ("paragraph", "d")),
        (("section", "49"), ("subsection", "6"), ("paragraph", "e")),
    ]


def test_compile_metadata_only_body_alphanumeric_sibling_repeal_expands_targets() -> None:
    effect = UKEffectRecord(
        effect_id="uk_test_metadata_only_body_alphanumeric_sibling_repeal",
        effect_type="repealed",
        applied=True,
        requires_applied=False,
        modified="2012-07-01",
        affected_uri="/id/ukpga/2000/22",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="22",
        affected_provisions="s. 50(4C)(4D)",
        affecting_uri="/id/ukpga/2011/20",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2011",
        affecting_number="20",
        affecting_provisions="Sch. 4 para. 9(6) Sch. 25 Pt. 5",
        affecting_title="Police Reform and Social Responsibility Act 2011",
        in_force_dates=[{"date": "2012-07-01", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, None, sequence=0)

    assert len(ops) == 2
    assert [op.target.path for op in ops] == [
        (("section", "50"), ("subsection", "4c")),
        (("section", "50"), ("subsection", "4d")),
    ]


def test_compile_metadata_only_body_mixed_numeric_alphanumeric_sibling_repeal_expands_targets() -> None:
    effect = UKEffectRecord(
        effect_id="uk_test_metadata_only_body_mixed_numeric_alphanumeric_sibling_repeal",
        effect_type="repealed",
        applied=True,
        requires_applied=False,
        modified="2013-04-01",
        affected_uri="/id/ukpga/2001/11",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2001",
        affected_number="11",
        affected_provisions="s. 7(3)(4)(4B)(5)",
        affecting_uri="/id/ukpga/2012/5",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2012",
        affecting_number="5",
        affecting_provisions="Sch. 14 Pt. 1",
        affecting_title="Welfare Reform Act 2012",
        in_force_dates=[{"date": "2013-04-01", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, None, sequence=0)

    assert len(ops) == 4
    assert [op.target.path for op in ops] == [
        (("section", "7"), ("subsection", "3")),
        (("section", "7"), ("subsection", "4")),
        (("section", "7"), ("subsection", "4b")),
        (("section", "7"), ("subsection", "5")),
    ]


def test_compile_metadata_only_body_mixed_alpha_sibling_repeal_expands_targets() -> None:
    effect = UKEffectRecord(
        effect_id="uk_test_metadata_only_body_mixed_alpha_sibling_repeal",
        effect_type="repealed",
        applied=True,
        requires_applied=False,
        modified="2013-04-01",
        affected_uri="/id/ukpga/2001/11",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2001",
        affected_number="11",
        affected_provisions="s. 9(1)(a)(b)(bc)(c)(d)",
        affecting_uri="/id/ukpga/2012/5",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2012",
        affecting_number="5",
        affecting_provisions="Sch. 14 Pt. 1",
        affecting_title="Welfare Reform Act 2012",
        in_force_dates=[{"date": "2013-04-01", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, None, sequence=0)

    assert len(ops) == 5
    assert [op.target.path for op in ops] == [
        (("section", "9"), ("subsection", "1"), ("paragraph", "a")),
        (("section", "9"), ("subsection", "1"), ("paragraph", "b")),
        (("section", "9"), ("subsection", "1"), ("paragraph", "bc")),
        (("section", "9"), ("subsection", "1"), ("paragraph", "c")),
        (("section", "9"), ("subsection", "1"), ("paragraph", "d")),
    ]


def test_compile_stemmed_alnum_subsection_range_expands_targets() -> None:
    extracted_el = ET.fromstring(
        f"""
        <BlockAmendment xmlns="{_LEG_NS}">
          <P2><Pnumber>1ZA</Pnumber><Text>ZA text.</Text></P2>
          <P2><Pnumber>1ZB</Pnumber><Text>ZB text.</Text></P2>
          <P2><Pnumber>1ZC</Pnumber><Text>ZC text.</Text></P2>
        </BlockAmendment>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_stemmed_alnum_subsection_range",
        effect_type="inserted",
        applied=True,
        requires_applied=False,
        modified="2010-01-25",
        affected_uri="/id/ukpga/2000/23",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="23",
        affected_provisions="s. 33(1ZA)-(1ZC)",
        affecting_uri="/id/ukpga/2009/26",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2009",
        affecting_number="26",
        affecting_provisions="s. 9(3)",
        affecting_title="Policing and Crime Act 2009",
        in_force_dates=[{"date": "2010-01-25", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 3
    assert [op.target.path for op in ops] == [
        (("section", "33"), ("subsection", "1za")),
        (("section", "33"), ("subsection", "1zb")),
        (("section", "33"), ("subsection", "1zc")),
    ]


def test_compile_substituted_for_parenthesized_sibling_range_uses_extracted_children() -> None:
    extracted_el = ET.fromstring(
        f"""
        <BlockAmendment xmlns="{_LEG_NS}">
          <P3><Pnumber>g</Pnumber><Text>Paragraph g.</Text></P3>
          <P3><Pnumber>h</Pnumber><Text>Paragraph h.</Text></P3>
          <P3><Pnumber>ha</Pnumber><Text>Paragraph ha.</Text></P3>
        </BlockAmendment>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_substituted_for_parenthesized_range",
        effect_type="substituted for. s. 68(7)(g)(h)",
        applied=True,
        requires_applied=False,
        modified="2018-03-12",
        affected_uri="/id/ukpga/2000/23",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="23",
        affected_provisions="s. 68(7)(g)-(ha)",
        affecting_uri="/id/ukpga/2016/25",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2016",
        affecting_number="25",
        affecting_provisions="s. 243(5)(c)",
        affecting_title="Investigatory Powers Act 2016",
        in_force_dates=[{"date": "2018-03-12", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 3
    assert [op.action.value for op in ops] == ["replace", "replace", "replace"]
    assert [op.target.path for op in ops] == [
        (("section", "68"), ("subsection", "7"), ("paragraph", "g")),
        (("section", "68"), ("subsection", "7"), ("paragraph", "h")),
        (("section", "68"), ("subsection", "7"), ("paragraph", "ha")),
    ]
    payloads = [payload for op in ops if (payload := op.payload) is not None]
    assert [payload.label for payload in payloads] == ["g", "h", "ha"]


def test_pipeline_replays_nonstructural_multi_sibling_substituted_for_ops() -> None:
    extracted_el = ET.fromstring(
        f"""
        <BlockAmendment xmlns="{_LEG_NS}">
          <P3><Pnumber>g</Pnumber><Text>Paragraph g.</Text></P3>
          <P3><Pnumber>h</Pnumber><Text>Paragraph h.</Text></P3>
          <P3><Pnumber>ha</Pnumber><Text>Paragraph ha.</Text></P3>
        </BlockAmendment>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_pipeline_nonstructural_substituted_for",
        effect_type="substituted for. s. 68(7)(g)(h)",
        applied=True,
        requires_applied=False,
        modified="2018-03-12",
        affected_uri="/id/ukpga/2000/23",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="23",
        affected_provisions="s. 68(7)(g)-(ha)",
        affecting_uri="/id/ukpga/2016/25",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2016",
        affecting_number="25",
        affecting_provisions="s. 243(5)(c)",
        affecting_title="Investigatory Powers Act 2016",
        in_force_dates=[{"date": "2018-03-12", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert UKReplayPipeline._should_replay_nonstructural_ops(effect, ops)


def test_pipeline_replays_nonstructural_substituted_series_replace_plus_tail_repeal() -> None:
    extracted_el = ET.fromstring(
        f"""
        <BlockAmendment xmlns="{_LEG_NS}">
          <P2>
            <Pnumber>5A</Pnumber>
            <Text>Inserted subsection 5A.</Text>
            <P3>
              <Pnumber>a</Pnumber>
              <Text>Paragraph a</Text>
            </P3>
            <P3>
              <Pnumber>b</Pnumber>
              <Text>Paragraph b</Text>
            </P3>
          </P2>
        </BlockAmendment>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_pipeline_nonstructural_substituted_series_anchor",
        effect_type="substituted for s. 3(5)(6)",
        applied=True,
        requires_applied=False,
        modified="2005-12-05",
        affected_uri="/id/ukpga/2002/21",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2002",
        affected_number="21",
        affected_provisions="s. 3(5A)",
        affecting_uri="/id/ukpga/2004/33",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2004",
        affecting_number="33",
        affecting_provisions="Sch. 24 para. 144(3)",
        affecting_title="Pensions Act 2004",
        in_force_dates=[{"date": "2005-12-05", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert [op.action.value for op in ops] == ["replace", "repeal"]
    assert UKReplayPipeline._should_replay_nonstructural_ops(effect, ops)


def test_pipeline_skips_unapplied_nonstructural_substituted_series() -> None:
    extracted_el = ET.fromstring(
        f"""
        <BlockAmendment xmlns="{_LEG_NS}">
          <P2>
            <Pnumber>2</Pnumber>
            <Text>Subsection 2.</Text>
          </P2>
          <P2>
            <Pnumber>3</Pnumber>
            <Text>Subsection 3.</Text>
          </P2>
        </BlockAmendment>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_pipeline_skip_unapplied_nonstructural_substituted_series",
        effect_type="substituted for s. 35(2)",
        applied=False,
        requires_applied=False,
        modified="2012-01-01",
        affected_uri="/id/ukpga/2002/21",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2002",
        affected_number="21",
        affected_provisions="s. 35(2)(3)",
        affecting_uri="/id/ukpga/2012/5",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2012",
        affecting_number="5",
        affecting_provisions="s. 124",
        affecting_title="Welfare Reform Act 2012",
        in_force_dates=[],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert [op.action.value for op in ops] == ["replace", "insert"]
    assert UKReplayPipeline._should_replay_nonstructural_ops(effect, ops) is False


def test_pipeline_replays_nonstructural_revoked_repeal_ops() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P2 xmlns="{_LEG_NS}">
          <Pnumber>2</Pnumber>
          <Text>Subsections (2), (4), (5) and (6) are repealed.</Text>
        </P2>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_pipeline_nonstructural_revoked",
        effect_type="revoked",
        applied=True,
        requires_applied=False,
        modified="2022-02-15",
        affected_uri="/id/ukpga/2000/41",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="41",
        affected_provisions="s. 145(4)-(6)",
        affecting_uri="/id/ssi/2022/38",
        affecting_class="ScottishStatutoryInstrument",
        affecting_year="2022",
        affecting_number="38",
        affecting_provisions="art. 3(2)",
        affecting_title="Scottish Elections (Reform) Act 2020 (Consequential Provisions and Modifications) Order 2022",
        in_force_dates=[{"date": "2022-02-02", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert [op.action.value for op in ops] == ["repeal", "repeal", "repeal"]
    assert UKReplayPipeline._should_replay_nonstructural_ops(effect, ops)


def test_compile_ceases_to_have_effect_as_repeal() -> None:
    effect = UKEffectRecord(
        effect_id="uk_test_ceases_to_have_effect",
        effect_type="ceases to have effect",
        applied=True,
        requires_applied=False,
        modified="2017-04-05",
        affected_uri="/id/ukpga/2006/12",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2006",
        affected_number="12",
        affected_provisions="Sch. 4",
        affecting_uri="/id/ukpga/2006/12",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2006",
        affecting_number="12",
        affecting_provisions="s. 40(8)",
        affecting_title="London Olympic Games and Paralympic Games Act 2006",
        in_force_dates=[{"date": "2012-12-31", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, None, sequence=0)

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.REPEAL
    assert ops[0].target.path == (("schedule", "4"),)


def test_pipeline_replays_nonstructural_ceases_to_have_effect_repeal_ops() -> None:
    effect = UKEffectRecord(
        effect_id="uk_test_pipeline_nonstructural_ceases_to_have_effect",
        effect_type="ceases to have effect",
        applied=True,
        requires_applied=False,
        modified="2017-04-05",
        affected_uri="/id/ukpga/2006/12",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2006",
        affected_number="12",
        affected_provisions="Sch. 4",
        affecting_uri="/id/ukpga/2006/12",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2006",
        affecting_number="12",
        affecting_provisions="s. 40(8)",
        affecting_title="London Olympic Games and Paralympic Games Act 2006",
        in_force_dates=[{"date": "2012-12-31", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, None, sequence=0)

    assert [op.action.value for op in ops] == ["repeal"]
    assert UKReplayPipeline._should_replay_nonstructural_ops(effect, ops)


def test_compile_plain_text_body_sibling_omit_expands_targets() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P3 xmlns="{_LEG_NS}">
          <Pnumber>a</Pnumber>
          <P3para>omit subsections (1) and (2);</P3para>
        </P3>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_plain_text_body_sibling_omit",
        effect_type="omitted",
        applied=True,
        requires_applied=False,
        modified="2016-03-16",
        affected_uri="/id/ukpga/2010/9",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2010",
        affected_number="9",
        affected_provisions="s. 16(1)(2)",
        affecting_uri="/id/ukpga/2016/7",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2016",
        affecting_number="7",
        affecting_provisions="s. 7(5)(a)",
        affecting_title="Welfare Reform and Work Act 2016",
        in_force_dates=[{"date": "2016-03-16", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 2
    assert [op.target.path for op in ops] == [
        (("section", "16"), ("subsection", "1")),
        (("section", "16"), ("subsection", "2")),
    ]


def test_compile_plain_text_single_subsection_item_does_not_expand_siblings() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P3 xmlns="{_LEG_NS}">
          <Pnumber>c</Pnumber>
          <P3para>omit subsection (3)(a);</P3para>
        </P3>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_single_subsection_item_omit",
        effect_type="omitted",
        applied=True,
        requires_applied=False,
        modified="2016-03-16",
        affected_uri="/id/ukpga/2010/9",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2010",
        affected_number="9",
        affected_provisions="s. 13(3)(a)",
        affecting_uri="/id/ukpga/2016/7",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2016",
        affecting_number="7",
        affecting_provisions="s. 7(4)(c)",
        affecting_title="Welfare Reform and Work Act 2016",
        in_force_dates=[{"date": "2016-03-16", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 1
    assert ops[0].target.path == (("section", "13"), ("subsection", "3"), ("paragraph", "a"))


def test_compile_skips_sidenote_only_target() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P2 xmlns="{_LEG_NS}" id="section-199-2">
          <Pnumber>2</Pnumber>
          <Text>For the sidenote substitute " Decisions of case tribunals: Wales ".</Text>
        </P2>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_skip_sidenote_target",
        effect_type="substituted",
        applied=True,
        requires_applied=False,
        modified="2008-12-12",
        affected_uri="/id/ukpga/2000/22",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="22",
        affected_provisions="s. 79 sidenote",
        affecting_uri="/id/ukpga/2007/28",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2007",
        affecting_number="28",
        affecting_provisions="s. 199(2)",
        affecting_title="Local Government and Public Involvement in Health Act 2007",
        in_force_dates=[{"date": "2008-12-12", "prospective": "false"}],
    )

    lowering_rejections: list[dict[str, object]] = []
    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0, lowering_rejections_out=lowering_rejections)

    assert ops == []
    assert lowering_rejections
    assert lowering_rejections[0]["rule_id"] == "uk_effect_heading_only_ref_rejected"
    assert lowering_rejections[0]["reason_code"] == "heading_only_ref_unsupported"
    assert lowering_rejections[0]["strict_disposition"] == "block"


def test_compile_skips_non_substantive_structural_insert_payload() -> None:
    effect = UKEffectRecord(
        effect_id="uk_test_skip_dot_shell_insert",
        effect_type="inserted",
        applied=True,
        requires_applied=False,
        modified="2010-01-01",
        affected_uri="/id/ukpga/2000/10",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="10",
        affected_provisions="s. 2(4)",
        affecting_uri="/id/ukpga/2010/1",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2010",
        affecting_number="1",
        affecting_provisions="s. 77",
        affecting_title="Test Act",
        in_force_dates=[{"date": "2010-01-01", "prospective": "false"}],
    )

    missing_payload_rejections: list[dict[str, object]] = []
    ops = compile_effect_to_ir_ops(
        effect,
        None,
        sequence=0,
        lowering_rejections_out=missing_payload_rejections,
    )
    assert ops == []
    assert missing_payload_rejections
    assert missing_payload_rejections[0]["rule_id"] == "uk_effect_missing_structural_payload_rejected"
    assert missing_payload_rejections[0]["reason_code"] == "missing_extracted_payload"

    extracted_el = ET.fromstring(
        f"""
        <BlockAmendment xmlns="{_LEG_NS}">
          77 . . . . . . . . . . . . .
        </BlockAmendment>
        """
    )
    non_substantive_rejections: list[dict[str, object]] = []
    ops = compile_effect_to_ir_ops(
        effect,
        extracted_el,
        sequence=0,
        lowering_rejections_out=non_substantive_rejections,
    )

    assert ops == []
    assert non_substantive_rejections
    assert non_substantive_rejections[0]["rule_id"] == "uk_effect_non_substantive_payload_rejected"
    assert non_substantive_rejections[0]["reason_code"] == "non_substantive_structural_payload"


def test_parse_unlabelled_schedule_paragraph_target_keeps_schedule_root() -> None:
    target = _parse_affected_target("Sch. para. 9")

    assert target.path == (("schedule", ""), ("paragraph", "9"))


def test_parse_bare_schedule_paragraph_target_keeps_schedule_root() -> None:
    target = _parse_affected_target("Sch 4 para. 2")

    assert target.path == (("schedule", "4"), ("paragraph", "2"))


def test_parse_bare_schedule_paragraph_target_with_different_number() -> None:
    target = _parse_affected_target("Sch 4 para. 8")

    assert target.path == (("schedule", "4"), ("paragraph", "8"))


def test_parse_schedule_part_wrapper_paragraph_target() -> None:
    target = _parse_affected_target("Sch. 1 Pt. 3 para. wrapper1n1")

    assert target.path == (("schedule", "1"), ("part", "3"), ("paragraph", "wrapper1n1"))


def test_parse_schedule_paragraph_of_schedule_clause_keeps_schedule_root() -> None:
    target = _parse_affected_target("para. 2 of Sch. 4")

    assert target.path == (("schedule", "4"), ("paragraph", "2"))


def test_parse_body_fused_paragraph_label_splits_into_section_and_paragraph() -> None:
    target = _parse_affected_target("s. 90paragraph (b)")

    assert target.path == (("section", "90"), ("subsection", "b"))


def test_parse_body_fused_paragraph_label_splits_for_multiple_rows() -> None:
    first = _parse_affected_target("s. 68paragraph (h)")
    second = _parse_affected_target("s. 68paragraph (c)")

    assert first.path == (("section", "68"), ("subsection", "h"))
    assert second.path == (("section", "68"), ("subsection", "c"))


def test_parse_direct_section_paragraph_ref_does_not_create_alphabetic_subsection() -> None:
    target = _parse_affected_target("s. 48(a)")

    assert target.path == (("section", "48"), ("paragraph", "a"))


def test_parse_direct_section_paragraph_subparagraph_ref_preserves_granularity() -> None:
    target = _parse_affected_target("s. 48(a)(ii)")

    assert target.path == (("section", "48"), ("paragraph", "a"), ("subparagraph", "ii"))


def test_compile_direct_section_paragraph_ref_records_target_normalization() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P xmlns="{_LEG_NS}">
          <Text>after second “authority” there is inserted “ (i) ”</Text>
        </P>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_direct_section_paragraph_target",
        effect_type="word inserted",
        applied=True,
        requires_applied=False,
        modified="2005-10-10",
        affected_uri="/id/asp/2001/2/section/48/a",
        affected_class="ScottishAct",
        affected_year="2001",
        affected_number="2",
        affected_provisions="s. 48(a)",
        affecting_uri="/id/asp/2005/12",
        affecting_class="ScottishAct",
        affecting_year="2005",
        affecting_number="12",
        affecting_provisions="s. 51(2)(a)",
        affecting_title="Test Act",
        in_force_dates=[{"date": "2005-10-10", "prospective": "false"}],
    )
    lowering_records: list[dict[str, object]] = []

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0, lowering_rejections_out=lowering_records)

    assert len(ops) == 1
    assert ops[0].target == LegalAddress(path=(("section", "48"), ("paragraph", "a")))
    assert any(record["rule_id"] == "uk_effect_direct_section_paragraph_target_normalized" for record in lowering_records)


def test_executor_finds_schedule_descendant_when_schedule_number_is_omitted() -> None:
    statute = IRStatute(
        statute_id="ukpga/2010/9",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=()),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="1",
                text="",
                attrs={"eId": "schedule-1"},
                children=(
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="1",
                        text="The service is available on Saturdays.",
                    ),
                ),
            ),
        ),
    )
    executor: Any = UKReplayExecutor(statute)
    target = _parse_affected_target("Sch. para. 1")

    node, parent, idx = executor._find_node_by_target(target)

    assert getattr(node, "kind", None) == IRNodeKind.PARAGRAPH
    assert getattr(node, "label", None) == "1"
    assert getattr(parent, "kind", None) == IRNodeKind.SCHEDULE
    assert idx == 0


def test_parse_ref_ignores_of_connector_before_schedule() -> None:
    assert _parse_ref("para. 2 of Sch. 4") == (("paragraph", "2"), ("schedule", "4"))


def test_derive_target_eid_canonicalizes_schedule_alphanumeric_paragraph_aliases() -> None:
    statute = IRStatute(
        statute_id="ukpga/2023/3",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=()),
        supplements=(),
    )
    executor: Any = UKReplayExecutor(statute)

    assert (
        executor._derive_target_eid(LegalAddress(path=(("schedule", "13"), ("paragraph", "9a"))))
        == "schedule-13-paragraph-9A"
    )
    assert (
        executor._derive_target_eid(LegalAddress(path=(("schedule", "13"), ("paragraph", "116a"), ("item", "a"))))
        == "schedule-13-paragraph-116A-a"
    )
    assert (
        executor._derive_target_eid(LegalAddress(path=(("schedule", "13"), ("paragraph", "154a"))))
        == "schedule-13-paragraph-154A"
    )


def test_order_schedule_materialization_ops_prioritizes_structural_schedule_ops() -> None:
    source = OperationSource(statute_id="wsi/2024/1061", title="Test Source", effective="2024-11-03")
    ops = [
        LegalOperation(
            op_id="text",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("schedule", "13"), ("paragraph", "116a"))),
            source=source,
            text_patch=_replace_patch("old", "new"),
        ),
        LegalOperation(
            op_id="insert",
            sequence=2,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("schedule", "13"), ("paragraph", "106a"))),
            source=source,
        ),
        LegalOperation(
            op_id="body",
            sequence=3,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "45"), ("subsection", "1"))),
            source=source,
            text_patch=_replace_patch("old", "new"),
        ),
    ]

    ordered = _order_schedule_materialization_ops(ops)

    assert [op.op_id for op in ordered] == ["insert", "text", "body"]


def test_order_schedule_materialization_ops_keeps_heading_facet_patch_before_structural_insert() -> None:
    source = OperationSource(statute_id="ukpga/2021/26", title="Test Source", effective="2021-06-10")
    ops = [
        LegalOperation(
            op_id="insert",
            sequence=1,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("section", "1a"),)),
            source=source,
        ),
        LegalOperation(
            op_id="heading",
            sequence=2,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "1"),), special=FacetKind.HEADING),
            source=source,
            text_patch=_replace_patch("a temporary", "an initial temporary"),
        ),
        LegalOperation(
            op_id="body",
            sequence=3,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "1"), ("subsection", "1"))),
            source=source,
            text_patch=_replace_patch("temporary", "initial temporary"),
        ),
    ]

    ordered = _order_schedule_materialization_ops(ops)

    assert [op.op_id for op in ordered] == ["heading", "insert", "body"]


def test_order_schedule_materialization_ops_places_shape_creation_before_dependent_text_edit() -> None:
    statute = IRStatute(
        statute_id="ukpga/2010/10",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.PART,
                    label="6",
                    text="",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SECTION,
                            label="55",
                            text="",
                            children=(
                                IRNode(
                                    kind=IRNodeKind.SUBSECTION,
                                    label="2",
                                    text="",
                                    children=(
                                        IRNode(kind=IRNodeKind.PARAGRAPH, label="a", text="A"),
                                        IRNode(kind=IRNodeKind.PARAGRAPH, label="b", text="B"),
                                        IRNode(kind=IRNodeKind.PARAGRAPH, label="c", text="C"),
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    insert_source = OperationSource(
        statute_id="uksi/2019/628",
        title="Amending Act",
        effective="2019-03-22",
    )
    text_source = OperationSource(
        statute_id="ukpga/2020/1",
        title="Amending Act",
        effective="2020-01-31",
    )
    ops = [
        LegalOperation(
            op_id="text",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "55"), ("subsection", "2b"), ("paragraph", "d"))),
            source=text_source,
            text_patch=_replace_patch("landlord", "tenant"),
        ),
        LegalOperation(
            op_id="insert",
            sequence=1,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("section", "55"), ("subsection", "2"))),
            payload=IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="2b",
                text="",
                children=(IRNode(kind=IRNodeKind.PARAGRAPH, label="d", text="landlord"),),
            ),
            source=insert_source,
        ),
    ]

    ordered = _order_schedule_materialization_ops(ops)
    assert [op.op_id for op in ordered] == ["insert", "text"]

    executor: Any = UKReplayExecutor(statute)
    for op in ordered:
        executor.apply_op(op)

    paragraph = executor.statute.body.children[0].children[0].children[1].children[0]
    assert paragraph.text == "tenant"


def test_order_uk_effects_for_replay_uses_source_provision_order_within_same_act_date() -> None:
    later = _minimal_uk_effect(
        "key-2995bc9d550cefeee85f3c9f5211ef7c",
        affecting_provisions="reg. 17(7)(a)(iii)",
    )
    earlier = _minimal_uk_effect(
        "key-b356d431a25c04a6eff3a8e68a50dfb1",
        affecting_provisions="reg. 17(7)(a)(ii)",
    )
    diagnostics: list[dict[str, Any]] = []

    ordered = _order_uk_effects_for_replay([later, earlier], diagnostics_out=diagnostics)

    assert [effect.effect_id for effect in ordered] == [earlier.effect_id, later.effect_id]
    assert diagnostics
    assert diagnostics[0]["rule_id"] == "uk_effect_source_provision_order_normalized"
    assert diagnostics[0]["strict_disposition"] == "record"


def test_order_uk_text_patch_preimage_chains_orders_exact_same_target_chain() -> None:
    target = LegalAddress((("section", "4"), ("subsection", "1")))
    later = LegalOperation(
        op_id="key-322afc5623b43af35307b2fa18f1f8bf",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=target,
        text_patch=_replace_patch("£626,571,000", "£626,568,000"),
        source=OperationSource(statute_id="ssi/2001/68", title="Order", effective=""),
    )
    earlier = LegalOperation(
        op_id="key-4971bad3478b772a2c289b6532ed41ac",
        sequence=2,
        action=StructuralAction.TEXT_REPLACE,
        target=target,
        text_patch=_replace_patch("£589,278,000", "£626,571,000"),
        source=OperationSource(statute_id="ssi/2001/7", title="Order", effective=""),
    )
    observations: list[dict[str, Any]] = []

    ordered = _order_uk_text_patch_preimage_chains(
        [later, earlier],
        lowering_observations_out=observations,
    )

    assert [op.op_id for op in ordered] == [earlier.op_id, later.op_id]
    assert observations[0]["rule_id"] == "uk_effect_text_patch_preimage_chain_ordered"
    assert observations[0]["family"] == "temporal_recovery"
    assert observations[0]["strict_disposition"] == "record"


def test_order_uk_text_patch_preimage_chains_blocks_ambiguous_exact_chain() -> None:
    target = LegalAddress((("section", "4"), ("subsection", "1")))
    first = LegalOperation(
        op_id="op-first",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=target,
        text_patch=_replace_patch("old amount", "shared amount"),
        source=OperationSource(statute_id="ssi/2001/1", title="Order", effective=""),
    )
    second = LegalOperation(
        op_id="op-second",
        sequence=2,
        action=StructuralAction.TEXT_REPLACE,
        target=target,
        text_patch=_replace_patch("shared amount", "second amount"),
        source=OperationSource(statute_id="ssi/2001/2", title="Order", effective=""),
    )
    third = LegalOperation(
        op_id="op-third",
        sequence=3,
        action=StructuralAction.TEXT_REPLACE,
        target=target,
        text_patch=_replace_patch("shared amount", "third amount"),
        source=OperationSource(statute_id="ssi/2001/3", title="Order", effective=""),
    )
    observations: list[dict[str, Any]] = []

    ordered = _order_uk_text_patch_preimage_chains(
        [second, third, first],
        lowering_observations_out=observations,
    )

    assert [op.op_id for op in ordered] == [second.op_id, third.op_id, first.op_id]
    assert observations[0]["rule_id"] == "uk_effect_text_patch_preimage_chain_ambiguous"
    assert observations[0]["blocking"] is True
    assert observations[0]["strict_disposition"] == "block"


def test_uk_source_provision_order_treats_single_letters_as_alpha_not_roman() -> None:
    refs = ["reg. 17(7)(d)", "reg. 17(7)(c)", "reg. 17(7)(a)(ii)", "reg. 17(7)(a)(i)"]

    ordered = sorted(refs, key=_uk_source_provision_order_key)

    assert ordered == [
        "reg. 17(7)(a)(i)",
        "reg. 17(7)(a)(ii)",
        "reg. 17(7)(c)",
        "reg. 17(7)(d)",
    ]


def test_compile_metadata_descendant_renumber_lowers_typed_destination_op() -> None:
    effect = UKEffectRecord(
        effect_id="key-test-renumber-132",
        effect_type="Sch. 9 para. 132 renumbered as Sch. 9 para. 132(1)",
        applied=True,
        requires_applied=True,
        modified="2024-04-06",
        affected_uri="/id/ukpga/2024/3",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2024",
        affected_number="3",
        affected_provisions="Sch. 9 para. 132(1)",
        affecting_uri="/id/uksi/2024/356",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2024",
        affecting_number="356",
        affecting_provisions="reg. 4(23)(a)",
        affecting_title="Test Regulations",
        in_force_dates=[{"date": "2024-04-06", "prospective": "false"}],
    )
    lowering_records: list[dict[str, Any]] = []

    extracted_el = ET.fromstring("<P3>a the existing text becomes sub-paragraph (1);</P3>")

    ops = compile_effect_to_ir_ops(effect, extracted_el, lowering_rejections_out=lowering_records)

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.RENUMBER
    assert ops[0].target.path == (("schedule", "9"), ("paragraph", "132"))
    assert ops[0].destination is not None
    assert ops[0].destination.path == (
        ("schedule", "9"),
        ("paragraph", "132"),
        ("subparagraph", "1"),
    )
    assert ops[0].witness_rule_id == "uk_effect_metadata_renumber_lowered"
    assert any(str(tag).startswith(_NOTE_REWRITE_WITNESS) for tag in ops[0].provenance_tags)
    assert _uk_op_allowed_by_authority_mode(ops[0], "source_text_only") == (True, None)
    assert lowering_records[0]["rule_id"] == "uk_effect_metadata_renumber_lowered"
    assert lowering_records[0]["strict_disposition"] == "record"


def test_compile_metadata_sibling_renumber_lowers_typed_destination_op() -> None:
    effect = UKEffectRecord(
        effect_id="key-test-renumber-16-9",
        effect_type="s. 16(9) renumbered as s. 16(8)",
        applied=True,
        requires_applied=True,
        modified="2026-03-09",
        affected_uri="/id/asc/2024/6",
        affected_class="ActOfSeneddCymru",
        affected_year="2024",
        affected_number="6",
        affected_provisions="s. 16(8)",
        affecting_uri="/id/asc/2025/3",
        affecting_class="ActOfSeneddCymru",
        affecting_year="2025",
        affecting_number="3",
        affecting_provisions="Sch. 1 para. 62",
        affecting_title="Test Act",
        in_force_dates=[{"date": "2025-09-10", "prospective": "false"}],
    )
    lowering_records: list[dict[str, Any]] = []

    ops = compile_effect_to_ir_ops(effect, None, lowering_rejections_out=lowering_records)

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.RENUMBER
    assert ops[0].target.path == (("section", "16"), ("subsection", "9"))
    assert ops[0].destination is not None
    assert ops[0].destination.path == (("section", "16"), ("subsection", "8"))
    assert ops[0].witness_rule_id == "uk_effect_metadata_sibling_renumber_lowered"
    assert lowering_records[0]["rule_id"] == "uk_effect_metadata_sibling_renumber_lowered"
    assert lowering_records[0]["reason_code"] == "explicit_effect_metadata_same_parent_sibling_renumber"
    assert lowering_records[0]["blocking"] is False
    assert lowering_records[0]["strict_disposition"] == "record"


def test_replay_text_end_append_preserves_existing_target_text() -> None:
    statute = IRStatute(
        statute_id="ukpga/2024/3",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="1",
                    text="Base body",
                ),
            ),
        ),
        supplements=(),
    )
    source = OperationSource(statute_id="uksi/2024/1012", title="Test Regulations", effective="2024-11-18")
    op = LegalOperation(
        op_id="uk_test_text_end_append",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=(("section", "1"),)),
        source=source,
        text_patch=_append_patch("and tail"),
    )
    adjudications: list[CompileAdjudication] = []
    executor: Any = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(op)

    assert executor.statute.body.children[0].text == "Base body and tail"
    assert adjudications == []


def test_replay_text_end_append_preserves_subtree_children() -> None:
    statute = IRStatute(
        statute_id="ukpga/2024/3",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="1",
                    text="",
                    children=(
                        IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="First"),
                        IRNode(kind=IRNodeKind.SUBSECTION, label="2", text="Second"),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    source = OperationSource(statute_id="uksi/2024/1012", title="Test Regulations", effective="2024-11-18")
    op = LegalOperation(
        op_id="uk_test_subtree_text_end_append",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=(("section", "1"),)),
        source=source,
        text_patch=_append_patch(", appended"),
    )
    adjudications: list[CompileAdjudication] = []
    executor: Any = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(op)

    section = executor.statute.body.children[0]
    assert section.text == ""
    assert len(section.children) == 2
    assert section.children[0].text == "First"
    assert section.children[1].text == "Second, appended"
    assert adjudications == []


def test_parse_schedule_paragraph_of_part_of_schedule_clause_keeps_schedule_order() -> None:
    target = _parse_affected_target("para. 2 of Pt. 1 of Sch. 13")

    assert target.path == (("schedule", "13"), ("part", "1"), ("paragraph", "2"))


def test_parse_unlabelled_schedule_subparagraph_target_keeps_schedule_root() -> None:
    target = _parse_affected_target("Sch. para. 6(2)")

    assert target.path == (("schedule", ""), ("paragraph", "6"), ("subparagraph", "2"))


def test_parse_unlabelled_schedule_direct_item_target_uses_item_kind() -> None:
    target = _parse_affected_target("Sch. para. 4(f)")

    assert target.path == (("schedule", ""), ("paragraph", "4"), ("item", "f"))


def test_parse_unlabelled_schedule_deep_item_target_uses_typed_depth() -> None:
    target = _parse_affected_target("Sch. para. 2(2)(d)")

    assert target.path == (
        ("schedule", ""),
        ("paragraph", "2"),
        ("subparagraph", "2"),
        ("item", "d"),
    )


def test_parse_body_deep_subparagraph_target_preserves_intermediate_paragraph() -> None:
    target = _parse_affected_target("s. 12(4)(a)(i)")

    assert target.path == (
        ("section", "12"),
        ("subsection", "4"),
        ("paragraph", "a"),
        ("subparagraph", "i"),
    )


def test_executor_repeal_deep_schedule_item_target_removes_item() -> None:
    statute = IRStatute(
        statute_id="ukpga/2000/10",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=()),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="",
                text="Schedule",
                attrs={"eId": "schedule"},
                children=(
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="2",
                        text="",
                        attrs={"eId": "schedule-paragraph-2"},
                        children=(
                            IRNode(
                                kind=IRNodeKind.SUBPARAGRAPH,
                                label="2",
                                text="",
                                attrs={"eId": "schedule-paragraph-2-2"},
                                children=(
                                    IRNode(
                                        kind=IRNodeKind.ITEM,
                                        label="d",
                                        text="To repeal",
                                        attrs={"eId": "schedule-paragraph-2-2-d"},
                                    ),
                                    IRNode(
                                        kind=IRNodeKind.ITEM, label="e", text="Keep", attrs={"eId": "schedule-paragraph-2-2-e"}
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    executor: Any = UKReplayExecutor(statute)
    op = LegalOperation(
        op_id="uk_test_repeal_schedule_item",
        sequence=1,
        action=StructuralAction.REPEAL,
        target=LegalAddress(path=(("schedule", ""), ("paragraph", "2"), ("subparagraph", "2"), ("item", "d"))),
        payload=None,
        source=OperationSource(statute_id="ukpga/2010/1", title="Amending Act"),
    )

    executor.apply_op(op)

    subparagraph = executor.statute.supplements[0].children[0].children[0]
    assert [child.label for child in subparagraph.children] == ["e"]


def test_compile_section_target_prefers_direct_numbered_section_over_wrapper_group() -> None:
    extracted_el = ET.fromstring(
        f"""
        <BlockAmendment xmlns="{_LEG_NS}">
          <P1group>
            <Title>General interpretation</Title>
            <P1>
              <Pnumber>27</Pnumber>
              <P2>
                <Pnumber>1</Pnumber>
                <Text>Inserted subsection.</Text>
              </P2>
            </P1>
          </P1group>
        </BlockAmendment>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_replace_section_27",
        effect_type="substituted",
        applied=True,
        requires_applied=False,
        modified="2016-03-16",
        affected_uri="/id/ukpga/2010/9",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2010",
        affected_number="9",
        affected_provisions="s. 27",
        affecting_uri="/id/ukpga/2016/7",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2016",
        affecting_number="7",
        affecting_provisions="s. 7(8)",
        affecting_title="Welfare Reform and Work Act 2016",
        in_force_dates=[{"date": "2016-03-16", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 1
    assert ops[0].payload is not None
    assert ops[0].payload.kind == IRNodeKind.SECTION
    assert ops[0].payload.label == "27"


def test_compile_space_separated_section_list_with_cross_heading_suffix_splits_targets() -> None:
    extracted_el = ET.fromstring(
        f"""
        <BlockAmendment xmlns="{_LEG_NS}">
          <Pblock>
            <Title>Social Mobility Commission</Title>
            <P1group>
              <P1>
                <Pnumber>A1B</Pnumber>
                <Text>Section A1B.</Text>
              </P1>
            </P1group>
            <P1group>
              <Title>Promotion of social mobility, advice and reports</Title>
              <P1>
                <Pnumber>A1C</Pnumber>
                <Text>Section A1C.</Text>
              </P1>
            </P1group>
          </Pblock>
        </BlockAmendment>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_split_a1b_a1c_crossheading",
        effect_type="inserted",
        applied=True,
        requires_applied=False,
        modified="2016-03-16",
        affected_uri="/id/ukpga/2010/9",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2010",
        affected_number="9",
        affected_provisions="s. A1B A1C and cross-heading",
        affecting_uri="/id/ukpga/2016/7",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2016",
        affecting_number="7",
        affecting_provisions="s. 6(1)",
        affecting_title="Welfare Reform and Work Act 2016",
        in_force_dates=[{"date": "2016-03-16", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 3
    assert ops[0].payload is not None
    assert ops[0].payload.kind == IRNodeKind.CROSSHEADING
    assert ops[0].payload.text == "Social Mobility Commission"
    assert [op.target.path for op in ops[1:]] == [
        (("section", "a1b"),),
        (("section", "a1c"),),
    ]
    payloads = [payload for op in ops[1:] if (payload := op.payload) is not None]
    assert [payload.kind for payload in payloads] == [IRNodeKind.SECTION, IRNodeKind.SECTION]


def test_compile_structural_inserted_schedule_paragraph_stays_insert() -> None:
    extracted_el = ET.fromstring(
        f"""
        <BlockAmendment xmlns="{_LEG_NS}">
          <P1>
            <Pnumber>197</Pnumber>
            <Text>In paragraph 10, for "old" substitute "new".</Text>
          </P1>
        </BlockAmendment>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_inserted_schedule_para_197",
        effect_type="inserted",
        applied=True,
        requires_applied=False,
        modified="2024-01-01",
        affected_uri="/id/asc/2023/3",
        affected_class="ActOfSederunt",
        affected_year="2023",
        affected_number="3",
        affected_provisions="Sch. 13 para. 197",
        affecting_uri="/id/wsi/2024/1061",
        affecting_class="WelshStatutoryInstrument",
        affecting_year="2024",
        affecting_number="1061",
        affecting_provisions="reg. 2",
        affecting_title="Test Instrument",
        in_force_dates=[{"date": "2024-01-01", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.INSERT
    assert ops[0].target.path == (("schedule", "13"), ("paragraph", "197"))
    assert ops[0].payload is not None
    assert ops[0].payload.kind == IRNodeKind.PARAGRAPH
    assert ops[0].payload.label == "197"
    assert ops[0].text_patch is None


def test_compile_multiclause_schedule_paragraph_substitution_splits_to_items() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P1 xmlns="{_LEG_NS}" id="schedule-13-paragraph-117">
          <Pnumber>117</Pnumber>
          <Text>In sub-paragraph (a), for "28, 28B, 29, 44AC and 44D" substitute "28 and 44AC"; in sub-paragraph (b), for "x" substitute "y".</Text>
        </P1>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_schedule_para_117_multiclause",
        effect_type="substituted",
        applied=True,
        requires_applied=False,
        modified="2024-01-01",
        affected_uri="/id/asc/2023/3",
        affected_class="ActOfSederunt",
        affected_year="2023",
        affected_number="3",
        affected_provisions="Sch. 13 para. 117",
        affecting_uri="/id/wsi/2024/1061",
        affecting_class="WelshStatutoryInstrument",
        affecting_year="2024",
        affecting_number="1061",
        affecting_provisions="reg. 8",
        affecting_title="Test Instrument",
        in_force_dates=[{"date": "2024-01-01", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.REPLACE
    assert ops[0].target.path == (("schedule", "13"), ("paragraph", "117"))
    assert ops[0].text_patch is None
    assert ops[0].payload is not None
    assert ops[0].payload.kind == IRNodeKind.PARAGRAPH
    assert ops[0].payload.label == "117"
    assert "28, 28B, 29, 44AC and 44D" in ops[0].payload.text


def test_compile_multiclause_schedule_item_substitution_selects_matching_item_fragment() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P3 xmlns="{_LEG_NS}" id="schedule-13-paragraph-148-b">
          <Pnumber>b</Pnumber>
          <Text>In sub-paragraph (a), for "wrong" substitute "still wrong"; in sub-paragraph (b), for ", 28B, 29, 44AC or 44D" substitute "or 44AC".</Text>
        </P3>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_schedule_para_148b_multiclause",
        effect_type="words substituted",
        applied=True,
        requires_applied=False,
        modified="2024-01-01",
        affected_uri="/id/asc/2023/3",
        affected_class="ActOfSederunt",
        affected_year="2023",
        affected_number="3",
        affected_provisions="Sch. 13 para. 148(b)",
        affecting_uri="/id/wsi/2024/1061",
        affecting_class="WelshStatutoryInstrument",
        affecting_year="2024",
        affecting_number="1061",
        affecting_provisions="reg. 14(b)",
        affecting_title="Test Instrument",
        in_force_dates=[{"date": "2024-01-01", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].target.path == (("schedule", "13"), ("paragraph", "148"), ("item", "b"))
    assert ops[0].text_patch is not None
    assert ops[0].text_patch.selector.match_text == "wrong"
    assert ops[0].text_patch.replacement == "still wrong"
    assert ops[0].text_patch is not None
    assert any(note.startswith("fragment_substitution:") for note in ops[0].provenance_tags)


def test_split_metadata_preserves_heading_only_ref() -> None:
    assert _split_metadata_provisions("s. 13 heading") == ["s. 13 heading"]


def test_compile_skips_heading_only_ref_without_creating_section_replace() -> None:
    extracted_el = ET.fromstring(
        f"""
        <InlineAmendment xmlns="{_LEG_NS}">
          " Consultation: Scotland and Northern Ireland "
        </InlineAmendment>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_skip_heading_only_ref",
        effect_type="substituted",
        applied=True,
        requires_applied=False,
        modified="2012-03-08",
        affected_uri="/id/ukpga/2010/9",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2010",
        affected_number="9",
        affected_provisions="s. 13 heading",
        affecting_uri="/id/ukpga/2012/5",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2012",
        affecting_number="5",
        affecting_provisions="Sch. 13 para. 7(a)",
        affecting_title="Welfare Reform Act 2012",
        in_force_dates=[{"date": "2012-03-08", "prospective": "false"}],
    )

    lowering_rejections: list[dict[str, object]] = []
    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0, lowering_rejections_out=lowering_rejections)

    assert ops == []
    assert lowering_rejections
    assert lowering_rejections[0]["rule_id"] == "uk_effect_heading_only_ref_rejected"
    assert lowering_rejections[0]["reason_code"] == "heading_only_ref_unsupported"


def test_compile_heading_facet_word_substitution_targets_heading_special() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P xmlns="{_LEG_NS}">
          In the heading, for "Parliamentary sovereignty" substitute
          "Parliamentary sovereignty and devolution".
        </P>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_heading_facet_word_substitution",
        effect_type="words substituted",
        applied=True,
        requires_applied=False,
        modified="2024-02-20",
        affected_uri="/id/ukpga/2020/1",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2020",
        affected_number="1",
        affected_provisions="s. 38 heading",
        affecting_uri="/id/uksi/2024/164",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2024",
        affecting_number="164",
        affecting_provisions="reg. 2(2)(a)",
        affecting_title="Test Regulations",
        in_force_dates=[{"date": "2024-02-20", "prospective": "false"}],
    )

    lowering_records: list[dict[str, object]] = []
    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0, lowering_rejections_out=lowering_records)

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].target == LegalAddress(path=(("section", "38"),), special=FacetKind.HEADING)
    assert ops[0].text_patch == _replace_patch(
        "Parliamentary sovereignty",
        "Parliamentary sovereignty and devolution",
    )
    assert any(record["rule_id"] == "uk_effect_heading_facet_word_patch_lowered" for record in lowering_records)


def test_compile_title_facet_word_substitution_does_not_create_subsection_title_target() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P xmlns="{_LEG_NS}">
          In the title of section 6, for “development” substitute “improvement”.
        </P>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_title_facet_word_substitution",
        effect_type="word substituted",
        applied=True,
        requires_applied=False,
        modified="2017-08-01",
        affected_uri="/id/asp/2000/6/section/6/title",
        affected_class="ScottishAct",
        affected_year="2000",
        affected_number="6",
        affected_provisions="s. 6 title",
        affecting_uri="/id/asp/2016/8",
        affecting_class="ScottishAct",
        affecting_year="2016",
        affecting_number="8",
        affecting_provisions="s. 3(3)",
        affecting_title="Test Act",
        in_force_dates=[{"date": "2017-08-01", "prospective": "false"}],
    )
    lowering_records: list[dict[str, object]] = []

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0, lowering_rejections_out=lowering_records)

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].target == LegalAddress(path=(("section", "6"),), special=FacetKind.HEADING)
    assert ops[0].text_patch == _replace_patch("development", "improvement")
    assert any(record["rule_id"] == "uk_effect_heading_facet_word_patch_lowered" for record in lowering_records)


def test_compile_heading_facet_at_end_insert_targets_heading_append() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P xmlns="{_LEG_NS}">
          <Text>In the heading, at the end, insert “and the constitutional status of Northern Ireland”.</Text>
        </P>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_heading_facet_append",
        effect_type="words inserted",
        applied=True,
        requires_applied=False,
        modified="2020-12-31",
        affected_uri="/id/ukpga/2020/1/section/38/heading",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2020",
        affected_number="1",
        affected_provisions="s. 38 heading",
        affecting_uri="/id/ukpga/2020/27",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2020",
        affecting_number="27",
        affecting_provisions="Sch. 3 para. 1",
        affecting_title="Test Act",
        in_force_dates=[{"date": "2020-12-31", "prospective": "false"}],
    )
    lowering_records: list[dict[str, Any]] = []

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0, lowering_rejections_out=lowering_records)

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].payload is None
    assert ops[0].target == LegalAddress(path=(("section", "38"),), special=FacetKind.HEADING)
    assert ops[0].text_patch is not None
    assert ops[0].text_patch.kind is TextPatchKindEnum.APPEND
    assert ops[0].text_patch.selector.match_text == "TEXT_END"
    assert ops[0].text_patch.replacement == "and the constitutional status of Northern Ireland"
    assert any(record["rule_id"] == "uk_effect_heading_facet_append_lowered" for record in lowering_records)


def test_compile_heading_facet_words_following_anchor_to_tail_replace() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P2 xmlns="{_LEG_NS}">
          <Text>4 In the heading of Part 3, for the words following “Scotland” substitute “ or Northern Ireland.”</Text>
        </P2>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_heading_facet_words_following",
        effect_type="words substituted",
        applied=True,
        requires_applied=False,
        modified="2020-12-31",
        affected_uri="/id/ukpga/2020/17/schedule/14/part/3/heading",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2020",
        affected_number="17",
        affected_provisions="Sch. 14 Pt. 3 heading",
        affecting_uri="/id/ukpga/2020/17",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2020",
        affecting_number="17",
        affecting_provisions="Sch. 22 para. 95(4)",
        affecting_title="Test Act",
        in_force_dates=[{"date": "2020-12-31", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].target == LegalAddress(
        path=(("schedule", "14"), ("part", "3")),
        special=FacetKind.HEADING,
    )
    assert ops[0].text_patch is not None
    assert ops[0].text_patch.kind is TextPatchKindEnum.REPLACE
    assert ops[0].text_patch.selector.match_text == "TEXT_AFTER_Scotland_TO_END"
    assert ops[0].text_patch.replacement == "or Northern Ireland."


def test_compile_heading_facet_insert_without_append_stays_unsupported() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P xmlns="{_LEG_NS}">
          <Text>In the heading, after “Parliamentary” insert “and democratic”.</Text>
        </P>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_heading_facet_insert_after_unsupported",
        effect_type="words inserted",
        applied=True,
        requires_applied=False,
        modified="2020-12-31",
        affected_uri="/id/ukpga/2020/1/section/38/heading",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2020",
        affected_number="1",
        affected_provisions="s. 38 heading",
        affecting_uri="/id/ukpga/2020/27",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2020",
        affecting_number="27",
        affecting_provisions="Sch. 3 para. 1",
        affecting_title="Test Act",
        in_force_dates=[{"date": "2020-12-31", "prospective": "false"}],
    )
    lowering_records: list[dict[str, Any]] = []

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0, lowering_rejections_out=lowering_records)

    assert ops == []
    assert [record["rule_id"] for record in lowering_records] == ["uk_effect_heading_only_ref_rejected"]


def test_replay_heading_facet_word_substitution_mutates_unique_p1group_heading_only() -> None:
    base = IRStatute(
        statute_id="ukpga/2020/1",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.PART,
                    label="PART 5",
                    text="General and final provision",
                    children=(
                        IRNode(
                            kind=IRNodeKind.CROSSHEADING,
                            text="Parliamentary sovereignty",
                            children=(
                                IRNode(
                                    kind=IRNodeKind.P1GROUP,
                                    text="Parliamentary sovereignty",
                                    children=(
                                        IRNode(
                                            kind=IRNodeKind.SECTION,
                                            label="38",
                                            children=(
                                                IRNode(
                                                    kind=IRNodeKind.SUBSECTION,
                                                    label="1",
                                                    text="Body text must not be searched for heading replacements.",
                                                ),
                                            ),
                                        ),
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="uk_test_heading_facet_apply",
        sequence=0,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=(("section", "38"),), special=FacetKind.HEADING),
        source=OperationSource(statute_id="uksi/2024/164", title="Test Regulations", effective="2024-02-20"),
        text_patch=_replace_patch("Parliamentary sovereignty", "Parliamentary sovereignty and devolution"),
    )

    result = replay_uk_ops(base, [op])

    part = result.body.children[0]
    crossheading = part.children[0]
    group = crossheading.children[0]
    section = group.children[0]
    assert crossheading.text == "Parliamentary sovereignty"
    assert group.text == "Parliamentary sovereignty and devolution"
    assert section.children[0].text == "Body text must not be searched for heading replacements."


def test_replay_heading_facet_append_mutates_unique_p1group_heading_only() -> None:
    base = IRStatute(
        statute_id="ukpga/2020/1",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.P1GROUP,
                    label=None,
                    text="Parliamentary sovereignty",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SECTION,
                            label="38",
                            text="",
                            attrs={"eId": "section-38"},
                            children=(IRNode(kind=IRNodeKind.CONTENT, label=None, text="Body text."),),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    op = LegalOperation(
        op_id="uk_test_heading_facet_append_apply",
        sequence=0,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=(("section", "38"),), special=FacetKind.HEADING),
        text_patch=TextPatchSpec(
            kind=TextPatchKindEnum.APPEND,
            selector=TextSelector(match_text="TEXT_END"),
            replacement="and the constitutional status of Northern Ireland",
        ),
    )

    result = replay_uk_ops(base, [op])

    p1group = result.body.children[0]
    section = p1group.children[0]
    assert p1group.text == "Parliamentary sovereignty and the constitutional status of Northern Ireland"
    assert section.children[0].text == "Body text."


def test_replay_heading_facet_after_anchor_tail_replace_mutates_heading_only() -> None:
    base = IRStatute(
        statute_id="ukpga/2020/17",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(),
        ),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="14",
                text="",
                attrs={"eId": "schedule-14"},
                children=(
                    IRNode(
                        kind=IRNodeKind.PART,
                        label="3",
                        text="Scotland etc.",
                        attrs={"eId": "schedule-14-part-3"},
                        children=(IRNode(kind=IRNodeKind.PARAGRAPH, label="1", text="Body text."),),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="uk_test_heading_facet_after_anchor_apply",
        sequence=0,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=(("schedule", "14"), ("part", "3")), special=FacetKind.HEADING),
        text_patch=TextPatchSpec(
            kind=TextPatchKindEnum.REPLACE,
            selector=TextSelector(match_text="TEXT_AFTER_Scotland_TO_END"),
            replacement="or Northern Ireland.",
        ),
    )

    result = replay_uk_ops(base, [op])

    part = result.supplements[0].children[0]
    assert part.text == "Scotland or Northern Ireland."
    assert part.children[0].text == "Body text."


def test_replay_heading_facet_patch_uses_direct_section_heading_carrier() -> None:
    base = IRStatute(
        statute_id="ukpga/2020/17",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="16A",
                    text="Inserted body text.",
                    children=(
                        IRNode(
                            kind=IRNodeKind.HEADING,
                            label=None,
                            text="Committal for sentence of young offenders",
                            attrs={
                                "source_tag": "P1group",
                                "source_rule_id": "uk_inserted_section_p1group_heading_carrier",
                            },
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="uk_test_heading_facet_direct_child_apply",
        sequence=0,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress((("section", "16a"),), special=FacetKind.HEADING),
        text_patch=_replace_patch("young offenders", "terrorist offenders"),
        source=OperationSource(
            statute_id="ukpga/2021/11",
            raw_text="In the italic heading before section 16A...",
        ),
    )
    adjudications: list[CompileAdjudication] = []

    replayed = replay_uk_ops(base, [op], adjudications_out=adjudications)

    section = replayed.body.children[0]
    assert section.text == "Inserted body text."
    assert section.children[0].kind is IRNodeKind.HEADING
    assert section.children[0].text == "Committal for sentence of terrorist offenders"
    assert adjudications == []


def test_replay_text_patch_normalizes_compact_subsection_citation_spacing() -> None:
    statute = IRStatute(
        statute_id="ukpga/2021/3",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="44",
                    text="No provision may be made under section 2 (1) or 10 (1).",
                    attrs={"eId": "section-44"},
                ),
            ),
        ),
    )
    adjudications: list[CompileAdjudication] = []
    op = LegalOperation(
        op_id="uk_test_compact_subsection_citation_spacing",
        sequence=0,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress((("section", "44"),)),
        text_patch=_replace_patch("2(1)", "2(1), 7A(1)"),
        source=OperationSource(
            statute_id="ukpga/2022/31",
            raw_text='In section 44 after "2(1)" insert ", 7A(1)".',
        ),
    )

    replayed = replay_uk_ops(statute, [op], adjudications_out=adjudications)

    assert replayed.body.children[0].text == "No provision may be made under section 2(1), 7A(1) or 10 (1)."
    assert [adjudication.kind for adjudication in adjudications] == [
        "uk_replay_text_match_punctuation_space_normalized"
    ]
    assert adjudications[0].detail["strict_disposition"] == "record"


def test_replay_heading_facet_punctuation_recovery_mutates_heading_carrier_not_section_body() -> None:
    base = IRStatute(
        statute_id="ukpga/2020/17",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="16A",
                    text="Body says 2 (1) but is not the heading carrier.",
                    children=(
                        IRNode(
                            kind=IRNodeKind.HEADING,
                            label=None,
                            text="Power under section 2 (1)",
                        ),
                    ),
                ),
            ),
        ),
    )
    adjudications: list[CompileAdjudication] = []
    op = LegalOperation(
        op_id="uk_test_heading_facet_punctuation_recovery",
        sequence=0,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress((("section", "16a"),), special=FacetKind.HEADING),
        text_patch=_replace_patch("2(1)", "2(1), 7A(1)"),
        source=OperationSource(statute_id="ukpga/2022/31"),
    )

    replayed = replay_uk_ops(base, [op], adjudications_out=adjudications)

    section = replayed.body.children[0]
    assert section.text == "Body says 2 (1) but is not the heading carrier."
    assert section.children[0].text == "Power under section 2(1), 7A(1)"
    assert [adjudication.kind for adjudication in adjudications] == [
        "uk_replay_text_match_punctuation_space_normalized"
    ]


def test_replay_heading_facet_subsection_patch_uses_unique_pgroup_heading_carrier() -> None:
    base = IRStatute(
        statute_id="ukpga/2000/10",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="185",
                    attrs={"eId": "section-185"},
                    children=(
                        IRNode(
                            kind=IRNodeKind.PGROUP,
                            text="Electronic monitoring requirement",
                            attrs={
                                "source_tag": "P2group",
                                "source_rule_id": "uk_parse_subordinate_pgroup_heading_carrier",
                            },
                            children=(
                                IRNode(
                                    kind=IRNodeKind.SUBSECTION,
                                    label="4",
                                    text="An electronic monitoring requirement applies.",
                                    attrs={"eId": "section-185-4"},
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="uk_test_heading_facet_pgroup_apply",
        sequence=0,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(
            path=(("section", "185"), ("subsection", "4")),
            special=FacetKind.HEADING,
        ),
        text_patch=TextPatchSpec(
            kind=TextPatchKindEnum.REPLACE,
            selector=TextSelector(match_text="requirement"),
            replacement="requirements",
        ),
    )

    result = replay_uk_ops(base, [op], allow_oracle_alignment=False)

    group = result.body.children[0].children[0]
    subsection = group.children[0]
    assert group.text == "Electronic monitoring requirements"
    assert subsection.text == "An electronic monitoring requirement applies."


def test_replay_heading_facet_subsection_patch_uses_first_child_pgroup_heading_carrier() -> None:
    base = IRStatute(
        statute_id="ukpga/2000/10",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="231",
                    attrs={"eId": "section-231"},
                    children=(
                        IRNode(
                            kind=IRNodeKind.PGROUP,
                            text="Application to mandatory sentences",
                            attrs={
                                "source_tag": "P2group",
                                "source_rule_id": "uk_parse_subordinate_pgroup_heading_carrier",
                            },
                            children=(
                                IRNode(
                                    kind=IRNodeKind.SUBSECTION,
                                    label="3",
                                    text="Subsection body.",
                                    attrs={"eId": "section-231-3"},
                                ),
                                IRNode(
                                    kind=IRNodeKind.SUBSECTION,
                                    label="4",
                                    text="Later subsection body.",
                                    attrs={"eId": "section-231-4"},
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="uk_test_heading_facet_pgroup_first_child_apply",
        sequence=0,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(
            path=(("section", "231"), ("subsection", "3")),
            special=FacetKind.HEADING,
        ),
        text_patch=_replace_patch("mandatory sentences", "certain sentences"),
    )

    result = replay_uk_ops(base, [op], allow_oracle_alignment=False)

    group = result.body.children[0].children[0]
    assert group.text == "Application to certain sentences"
    assert [child.text for child in group.children] == ["Subsection body.", "Later subsection body."]


def test_replay_heading_facet_subsection_patch_blocks_non_first_pgroup_child() -> None:
    base = IRStatute(
        statute_id="ukpga/2000/10",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="231",
                    attrs={"eId": "section-231"},
                    children=(
                        IRNode(
                            kind=IRNodeKind.PGROUP,
                            text="Application to mandatory sentences",
                            attrs={
                                "source_tag": "P2group",
                                "source_rule_id": "uk_parse_subordinate_pgroup_heading_carrier",
                            },
                            children=(
                                IRNode(kind=IRNodeKind.SUBSECTION, label="3", text="Subsection body."),
                                IRNode(kind=IRNodeKind.SUBSECTION, label="4", text="Later subsection body."),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    adjudications: list[CompileAdjudication] = []
    op = LegalOperation(
        op_id="uk_test_heading_facet_pgroup_non_first_child",
        sequence=0,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(
            path=(("section", "231"), ("subsection", "4")),
            special=FacetKind.HEADING,
        ),
        text_patch=_replace_patch("mandatory sentences", "certain sentences"),
    )

    result = replay_uk_ops(base, [op], allow_oracle_alignment=False, adjudications_out=adjudications)

    assert result.body.children[0].children[0].text == "Application to mandatory sentences"
    assert [adjudication.kind for adjudication in adjudications] == ["uk_replay_heading_facet_target_gap"]


def test_replay_heading_facet_blocks_ambiguous_p1group_carrier() -> None:
    base = IRStatute(
        statute_id="ukpga/2020/1",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.P1GROUP,
                    text="Shared heading",
                    children=(
                        IRNode(kind=IRNodeKind.SECTION, label="1", text="old"),
                        IRNode(kind=IRNodeKind.SECTION, label="2", text="old"),
                    ),
                ),
            ),
        ),
    )
    adjudications: list[CompileAdjudication] = []
    op = LegalOperation(
        op_id="uk_test_heading_facet_ambiguous",
        sequence=0,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=(("section", "1"),), special=FacetKind.HEADING),
        source=OperationSource(statute_id="uksi/2024/164", title="Test Regulations", effective="2024-02-20"),
        text_patch=_replace_patch("Shared heading", "Changed heading"),
    )

    result = replay_uk_ops(base, [op], adjudications_out=adjudications)

    assert result.body.children[0].text == "Shared heading"
    assert [adjudication.kind for adjudication in adjudications] == ["uk_replay_heading_facet_target_gap"]


def test_compile_records_crossheading_replace_rejection() -> None:
    extracted_el = ET.fromstring(
        f"""
        <InlineAmendment xmlns="{_LEG_NS}">
          " New cross-heading "
        </InlineAmendment>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_crossheading_replace_rejected",
        effect_type="substituted",
        applied=True,
        requires_applied=False,
        modified="2012-03-08",
        affected_uri="/id/ukpga/2010/9",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2010",
        affected_number="9",
        affected_provisions="cross-heading",
        affecting_uri="/id/ukpga/2012/5",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2012",
        affecting_number="5",
        affecting_provisions="Sch. 13 para. 7(b)",
        affecting_title="Welfare Reform Act 2012",
        in_force_dates=[{"date": "2012-03-08", "prospective": "false"}],
    )

    lowering_rejections: list[dict[str, object]] = []
    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0, lowering_rejections_out=lowering_rejections)

    assert ops == []
    assert lowering_rejections
    assert lowering_rejections[0]["rule_id"] == "uk_effect_crossheading_replace_rejected"
    assert lowering_rejections[0]["reason_code"] == "crossheading_replace_unsupported"


def test_compile_crossheading_before_paragraph_replace_lowers_to_heading_patch() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P2 xmlns="{_LEG_NS}">
          <Pnumber>22</Pnumber>
          <Text>For the italic heading before paragraph 132 substitute—</Text>
          <BlockAmendment>
            <Text>Modifications of scheme rules</Text>
          </BlockAmendment>
        </P2>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_crossheading_before_anchor_replace",
        effect_type="substituted",
        applied=True,
        requires_applied=True,
        modified="2024-04-06",
        affected_uri="/id/ukpga/2004/12/schedule/9",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2004",
        affected_number="12",
        affected_provisions="Sch. 9 para. 132 cross-heading",
        affecting_uri="/id/uksi/2024/356",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2024",
        affecting_number="356",
        affecting_provisions="reg. 4(22)",
        affecting_title="Test Regulations",
        in_force_dates=[{"date": "2024-04-06", "prospective": "false"}],
    )

    observations: list[dict[str, object]] = []
    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0, lowering_rejections_out=observations)

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].target == LegalAddress(path=(("schedule", "9"), ("paragraph", "132")), special=FacetKind.HEADING)
    assert ops[0].text_patch is not None
    assert ops[0].text_patch.selector.match_text == "TEXT_ALL"
    assert ops[0].text_patch.replacement == "Modifications of scheme rules"
    assert any(
        note == "text_rewrite_rule:uk_effect_crossheading_before_anchor_replacement_text_patch"
        for note in ops[0].provenance_tags
    )
    assert observations
    assert observations[0]["rule_id"] == "uk_effect_crossheading_before_anchor_replacement_lowered"
    assert observations[0]["blocking"] is False


def test_replay_crossheading_before_anchor_replace_mutates_crossheading_parent_only() -> None:
    base = IRStatute(
        statute_id="ukpga/2004/12",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=()),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="9",
                text="",
                children=(
                    IRNode(
                        kind=IRNodeKind.CROSSHEADING,
                        label=None,
                        text="Old modifications heading",
                        children=(
                            IRNode(
                                kind=IRNodeKind.PARAGRAPH,
                                label="132",
                                text="Paragraph body must not be replaced.",
                                children=(),
                            ),
                            IRNode(kind=IRNodeKind.PARAGRAPH, label="133", text="Sibling body."),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="uk_test_crossheading_before_anchor_apply",
        sequence=0,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=(("schedule", "9"), ("paragraph", "132")), special=FacetKind.HEADING),
        source=OperationSource(statute_id="uksi/2024/356", title="Test Regulations", effective="2024-04-06"),
        text_patch=_replace_patch("TEXT_ALL", "Modifications of scheme rules"),
        provenance_tags=("text_rewrite_rule:uk_effect_crossheading_before_anchor_replacement_text_patch",),
    )

    result = replay_uk_ops(base, [op])

    schedule = result.supplements[0]
    crossheading = schedule.children[0]
    paragraph = crossheading.children[0]
    sibling = crossheading.children[1]
    assert crossheading.text == "Modifications of scheme rules"
    assert paragraph.text == "Paragraph body must not be replaced."
    assert sibling.text == "Sibling body."


def test_oracle_grounding_does_not_create_public_schedule_entry_eids() -> None:
    statute = IRStatute(
        statute_id="asp/2000/7",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=()),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="SCHEDULE 3",
                text="Devolved public bodies",
                attrs={"eId": "schedule-3"},
                children=(
                    IRNode(
                        kind=IRNodeKind.SCHEDULE_ENTRY,
                        label=None,
                        text="Scottish Legal Aid Board",
                        attrs={
                            "source_rule_id": "uk_schedule_list_entry_preserved",
                            "source_ordinal": "1",
                        },
                    ),
                ),
            ),
        ),
    )
    executor = UKReplayExecutor(
        statute,
        eid_map={
            "schedule-3": "schedule-3",
            "hash:6d1173ee0ce3": "schedule-3-p-legal-aid-board",
        },
        text_map={"schedule-3-p-legal-aid-board": "scottish legal aid board"},
    )

    executor.ground_ids()
    result = executor.statute.to_irstatute()
    schedule_entry = result.supplements[0].children[0]

    assert schedule_entry.kind is IRNodeKind.SCHEDULE_ENTRY
    assert schedule_entry.label is None
    assert "eId" not in schedule_entry.attrs
    assert "id" not in schedule_entry.attrs


def test_compile_schedule_list_entry_insert_lowers_to_typed_schedule_entry() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P3 xmlns="{_LEG_NS}">
          <Text>before the entry relating to “Scottish Children's Reporter Administration”
          insert— “ The Scottish Charity Regulator ” .</Text>
        </P3>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_schedule_list_entry_insert",
        effect_type="words inserted",
        applied=True,
        requires_applied=True,
        modified="2006-04-01",
        affected_uri="/id/asp/2000/7/schedule/3",
        affected_class="ScottishAct",
        affected_year="2000",
        affected_number="7",
        affected_provisions="Sch. 3",
        affecting_uri="/id/asp/2005/10",
        affecting_class="ScottishAct",
        affecting_year="2005",
        affecting_number="10",
        affecting_provisions="sch. 4 para. 12",
        affecting_title="Test Amendment Act",
        in_force_dates=[{"date": "2006-04-01", "prospective": "false"}],
    )
    observations: list[dict[str, object]] = []

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0, lowering_rejections_out=observations)

    assert len(ops) == 1
    op = ops[0]
    assert op.action is StructuralAction.INSERT
    assert op.target == LegalAddress(path=(("schedule", "3"),))
    assert op.payload is not None
    assert op.payload.kind is IRNodeKind.SCHEDULE_ENTRY
    assert op.payload.label is None
    assert op.payload.text == "The Scottish Charity Regulator"
    assert op.witness_rule_id == "uk_effect_schedule_list_entry_insert"
    assert any(note.startswith("schedule_list_entry_selector:") for note in op.provenance_tags)
    assert observations[0]["rule_id"] == "uk_effect_schedule_list_entry_insert"
    assert observations[0]["blocking"] is False


def test_compile_schedule_list_entry_insert_handles_there_is_inserted_form() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P3 xmlns="{_LEG_NS}">
          <Text>after the entry for the State Hospitals Board for Scotland,
          there is inserted the following entry— “ The Water Industry Commission for Scotland ” .</Text>
        </P3>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_schedule_list_entry_there_is_inserted",
        effect_type="words inserted",
        applied=True,
        requires_applied=True,
        modified="2005-07-01",
        affected_uri="/id/asp/2000/7/schedule/3",
        affected_class="ScottishAct",
        affected_year="2000",
        affected_number="7",
        affected_provisions="Sch. 3",
        affecting_uri="/id/asp/2005/3",
        affecting_class="ScottishAct",
        affecting_year="2005",
        affecting_number="3",
        affecting_provisions="sch. 5 para. 6(e)",
        affecting_title="Test Amendment Act",
        in_force_dates=[{"date": "2005-07-01", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 1
    assert ops[0].payload is not None
    assert ops[0].payload.text == "The Water Industry Commission for Scotland"
    selector_note = next(
        note for note in ops[0].provenance_tags if note.startswith("schedule_list_entry_selector:")
    )
    selector = json.loads(selector_note.removeprefix("schedule_list_entry_selector:"))
    assert selector["anchor_text"] == "the State Hospitals Board for Scotland"
    assert selector["direction"] == "after"


def test_compile_schedule_list_entry_insert_handles_insertion_of_form() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P2 xmlns="{_LEG_NS}">
          <Text>1 Schedule 3 is amended by the insertion, after the entry for
          “The Royal Commission on the Ancient and Historical Monuments of Scotland”
          of “The Cairngorms National Park Authority”.</Text>
        </P2>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_schedule_list_entry_insertion_of",
        effect_type="words inserted",
        applied=True,
        requires_applied=True,
        modified="2003-01-07",
        affected_uri="/id/asp/2000/7/schedule/3",
        affected_class="ScottishAct",
        affected_year="2000",
        affected_number="7",
        affected_provisions="Sch. 3",
        affecting_uri="/id/ssi/2003/1",
        affecting_class="ScottishStatutoryInstrument",
        affecting_year="2003",
        affecting_number="1",
        affecting_provisions="art. 15(1)",
        affecting_title="Test Order",
        in_force_dates=[{"date": "2003-01-07", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 1
    assert ops[0].payload is not None
    assert ops[0].payload.text == "The Cairngorms National Park Authority"
    selector_note = next(
        note for note in ops[0].provenance_tags if note.startswith("schedule_list_entry_selector:")
    )
    selector = json.loads(selector_note.removeprefix("schedule_list_entry_selector:"))
    assert selector["anchor_text"] == (
        "The Royal Commission on the Ancient and Historical Monuments of Scotland"
    )


def test_compile_schedule_list_entry_insert_handles_quoted_anchor_form() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P3 xmlns="{_LEG_NS}">
          <Text>insert before “The Board of Trustees of the Royal Botanic Garden, Edinburgh”–</Text>
          <BlockAmendment>
            <P3para><Text>Quality Meat Scotland</Text></P3para>
          </BlockAmendment>
        </P3>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_schedule_list_entry_quoted_anchor",
        effect_type="words inserted",
        applied=True,
        requires_applied=True,
        modified="2009-10-09",
        affected_uri="/id/asp/2000/7/schedule/3",
        affected_class="ScottishAct",
        affected_year="2000",
        affected_number="7",
        affected_provisions="Sch. 3",
        affecting_uri="/id/ssi/2009/286",
        affecting_class="ScottishStatutoryInstrument",
        affecting_year="2009",
        affecting_number="286",
        affecting_provisions="art. 2(2)(c)",
        affecting_title="Test Order",
        in_force_dates=[{"date": "2009-10-09", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 1
    assert ops[0].payload is not None
    assert ops[0].payload.kind is IRNodeKind.SCHEDULE_ENTRY
    assert ops[0].payload.text == "Quality Meat Scotland"
    selector_note = next(
        note for note in ops[0].provenance_tags if note.startswith("schedule_list_entry_selector:")
    )
    selector = json.loads(selector_note.removeprefix("schedule_list_entry_selector:"))
    assert selector["direction"] == "before"
    assert selector["anchor_text"] == "The Board of Trustees of the Royal Botanic Garden, Edinburgh"


def test_compile_schedule_list_entry_insert_handles_alphabetical_order_form() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P3 xmlns="{_LEG_NS}">
          <Text>at the appropriate place in alphabetical order insert—
          “ Historic Environment Scotland ” .</Text>
        </P3>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_schedule_list_entry_alphabetical",
        effect_type="words inserted",
        applied=True,
        requires_applied=True,
        modified="2015-02-27",
        affected_uri="/id/asp/2000/7/schedule/3",
        affected_class="ScottishAct",
        affected_year="2000",
        affected_number="7",
        affected_provisions="Sch. 3",
        affecting_uri="/id/asp/2014/19",
        affecting_class="ScottishAct",
        affecting_year="2014",
        affecting_number="19",
        affecting_provisions="sch. 6 para. 1(b)",
        affecting_title="Test Act",
        in_force_dates=[{"date": "2015-02-27", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 1
    assert ops[0].payload is not None
    assert ops[0].payload.kind is IRNodeKind.SCHEDULE_ENTRY
    assert ops[0].payload.text == "Historic Environment Scotland"
    selector_note = next(
        note for note in ops[0].provenance_tags if note.startswith("schedule_list_entry_selector:")
    )
    selector = json.loads(selector_note.removeprefix("schedule_list_entry_selector:"))
    assert selector["direction"] == "alphabetical"
    assert selector["anchor_text"] == ""


def test_compile_schedule_list_entry_insert_handles_entry_inserted_feed_type() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P3 xmlns="{_LEG_NS}">
          <Text>after the entry relating to the Scottish Environment Protection Agency
          there is inserted— “ The Scottish Further and Higher Education Funding Council ” ; and</Text>
        </P3>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_schedule_list_entry_feed_type",
        effect_type="entry inserted",
        applied=True,
        requires_applied=True,
        modified="2005-10-03",
        affected_uri="/id/asp/2000/7/schedule/3",
        affected_class="ScottishAct",
        affected_year="2000",
        affected_number="7",
        affected_provisions="Sch. 3",
        affecting_uri="/id/asp/2005/6",
        affecting_class="ScottishAct",
        affecting_year="2005",
        affecting_number="6",
        affecting_provisions="Sch. 3 para. 9(a)",
        affecting_title="Test Act",
        in_force_dates=[{"date": "2005-10-03", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 1
    assert ops[0].payload is not None
    assert ops[0].payload.kind is IRNodeKind.SCHEDULE_ENTRY
    assert ops[0].payload.text == "The Scottish Further and Higher Education Funding Council"


def test_replay_schedule_list_entry_insert_places_unlabeled_entry_before_anchor() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P3 xmlns="{_LEG_NS}">
          <Text>before the entry relating to “Scottish Children's Reporter Administration”
          insert— “ The Scottish Charity Regulator ” .</Text>
        </P3>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_schedule_list_entry_insert",
        effect_type="words inserted",
        applied=True,
        requires_applied=True,
        modified="2006-04-01",
        affected_uri="/id/asp/2000/7/schedule/3",
        affected_class="ScottishAct",
        affected_year="2000",
        affected_number="7",
        affected_provisions="Sch. 3",
        affecting_uri="/id/asp/2005/10",
        affecting_class="ScottishAct",
        affecting_year="2005",
        affecting_number="10",
        affecting_provisions="sch. 4 para. 12",
        affecting_title="Test Amendment Act",
        in_force_dates=[{"date": "2006-04-01", "prospective": "false"}],
    )
    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)
    base = IRStatute(
        statute_id="asp/2000/7",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=()),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="3",
                text="Devolved public bodies",
                children=(
                    IRNode(
                        kind=IRNodeKind.SCHEDULE_ENTRY,
                        label=None,
                        text="Scottish Children's Reporter Administration",
                        attrs={"source_rule_id": "uk_schedule_list_entry_preserved"},
                    ),
                    IRNode(
                        kind=IRNodeKind.SCHEDULE_ENTRY,
                        label=None,
                        text="Scottish Legal Aid Board",
                        attrs={"source_rule_id": "uk_schedule_list_entry_preserved"},
                    ),
                ),
            ),
        ),
    )

    replayed = replay_uk_ops(base, ops)
    schedule = replayed.supplements[0]

    assert [child.kind for child in schedule.children] == [
        IRNodeKind.SCHEDULE_ENTRY,
        IRNodeKind.SCHEDULE_ENTRY,
        IRNodeKind.SCHEDULE_ENTRY,
    ]
    assert [child.label for child in schedule.children] == [None, None, None]
    assert [child.text for child in schedule.children] == [
        "The Scottish Charity Regulator",
        "Scottish Children's Reporter Administration",
        "Scottish Legal Aid Board",
    ]
    assert "eId" not in schedule.children[0].attrs
    assert "id" not in schedule.children[0].attrs


def test_replay_schedule_list_entry_insert_records_unique_prefix_anchor() -> None:
    op = LegalOperation(
        op_id="uk_test_schedule_entry_prefix_anchor",
        sequence=0,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("schedule", "3"),)),
        payload=IRNode(
            kind=IRNodeKind.SCHEDULE_ENTRY,
            label=None,
            text="A community justice authority",
        ),
        provenance_tags=(
            'schedule_list_entry_selector:{"rule_id":"uk_effect_schedule_list_entry_insert",'
            '"direction":"after","anchor_text":"the Common Services Agency for the Scottish Health Service",'
            '"inserted_text":"A community justice authority"}',
        ),
    )
    base = IRStatute(
        statute_id="asp/2000/7",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=()),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="SCHEDULE 3",
                children=(
                    IRNode(
                        kind=IRNodeKind.SCHEDULE_ENTRY,
                        label=None,
                        text=(
                            "The Common Services Agency for the Scottish Health Service, "
                            "constituted under section 10 of the National Health Service "
                            "(Scotland) Act 1978"
                        ),
                    ),
                    IRNode(
                        kind=IRNodeKind.SCHEDULE_ENTRY,
                        label=None,
                        text="The State Hospitals Board for Scotland",
                    ),
                ),
            ),
        ),
    )
    adjudications: list[CompileAdjudication] = []

    replayed = replay_uk_ops(base, [op], adjudications_out=adjudications)

    assert [child.text for child in replayed.supplements[0].children] == [
        (
            "The Common Services Agency for the Scottish Health Service, "
            "constituted under section 10 of the National Health Service "
            "(Scotland) Act 1978"
        ),
        "A community justice authority",
        "The State Hospitals Board for Scotland",
    ]
    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_schedule_list_entry_anchor_prefix_normalized"
    assert adjudications[0].detail["blocking"] is False
    assert adjudications[0].detail["strict_disposition"] == "record"


def test_replay_schedule_list_entry_insert_records_unique_article_anchor() -> None:
    op = LegalOperation(
        op_id="uk_test_schedule_entry_article_anchor",
        sequence=0,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("schedule", "3"),)),
        payload=IRNode(
            kind=IRNodeKind.SCHEDULE_ENTRY,
            label=None,
            text="The Scottish Fire and Rescue Service",
        ),
        provenance_tags=(
            'schedule_list_entry_selector:{"rule_id":"uk_effect_schedule_list_entry_insert",'
            '"direction":"after","anchor_text":"Scottish Environment Protection Agency",'
            '"inserted_text":"The Scottish Fire and Rescue Service"}',
        ),
    )
    base = IRStatute(
        statute_id="asp/2000/7",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=()),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="SCHEDULE 3",
                children=(
                    IRNode(
                        kind=IRNodeKind.SCHEDULE_ENTRY,
                        label=None,
                        text="The Scottish Environment Protection Agency",
                    ),
                    IRNode(
                        kind=IRNodeKind.SCHEDULE_ENTRY,
                        label=None,
                        text="Scottish Legal Aid Board",
                    ),
                ),
            ),
        ),
    )
    adjudications: list[CompileAdjudication] = []

    replayed = replay_uk_ops(base, [op], adjudications_out=adjudications)

    assert [child.text for child in replayed.supplements[0].children] == [
        "The Scottish Environment Protection Agency",
        "The Scottish Fire and Rescue Service",
        "Scottish Legal Aid Board",
    ]
    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_schedule_list_entry_anchor_article_normalized"
    assert adjudications[0].detail["blocking"] is False
    assert adjudications[0].detail["strict_disposition"] == "record"


def test_replay_schedule_list_entry_insert_records_alphabetical_position() -> None:
    op = LegalOperation(
        op_id="uk_test_schedule_entry_alphabetical",
        sequence=0,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("schedule", "3"),)),
        payload=IRNode(
            kind=IRNodeKind.SCHEDULE_ENTRY,
            label=None,
            text="Historic Environment Scotland",
        ),
        provenance_tags=(
            'schedule_list_entry_selector:{"rule_id":"uk_effect_schedule_list_entry_insert",'
            '"direction":"alphabetical","anchor_text":"","inserted_text":"Historic Environment Scotland"}',
        ),
    )
    base = IRStatute(
        statute_id="asp/2000/7",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=()),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="SCHEDULE 3",
                children=(
                    IRNode(kind=IRNodeKind.SCHEDULE_ENTRY, label=None, text="Health Education Board for Scotland"),
                    IRNode(kind=IRNodeKind.SCHEDULE_ENTRY, label=None, text="Highlands and Islands Enterprise"),
                    IRNode(kind=IRNodeKind.SCHEDULE_ENTRY, label=None, text="The Mental Welfare Commission for Scotland"),
                ),
            ),
        ),
    )
    adjudications: list[CompileAdjudication] = []

    replayed = replay_uk_ops(base, [op], adjudications_out=adjudications)

    assert [child.text for child in replayed.supplements[0].children] == [
        "Health Education Board for Scotland",
        "Highlands and Islands Enterprise",
        "Historic Environment Scotland",
        "The Mental Welfare Commission for Scotland",
    ]
    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_schedule_list_entry_alphabetical_position_resolved"
    assert adjudications[0].detail["blocking"] is False
    assert adjudications[0].detail["strict_disposition"] == "record"


def test_replay_schedule_list_entry_insert_blocks_when_anchor_ambiguous() -> None:
    op = LegalOperation(
        op_id="uk_test_schedule_entry_ambiguous",
        sequence=0,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("schedule", "3"),)),
        payload=IRNode(kind=IRNodeKind.SCHEDULE_ENTRY, label=None, text="Inserted entry"),
        provenance_tags=(
            'schedule_list_entry_selector:{"rule_id":"uk_effect_schedule_list_entry_insert",'
            '"direction":"after","anchor_text":"Scottish Legal Aid Board","inserted_text":"Inserted entry"}',
        ),
    )
    base = IRStatute(
        statute_id="asp/2000/7",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=()),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="3",
                children=(
                    IRNode(kind=IRNodeKind.SCHEDULE_ENTRY, label=None, text="Scottish Legal Aid Board"),
                    IRNode(kind=IRNodeKind.SCHEDULE_ENTRY, label=None, text="Scottish Legal Aid Board"),
                ),
            ),
        ),
    )
    adjudications: list[CompileAdjudication] = []

    replayed = replay_uk_ops(base, [op], adjudications_out=adjudications)

    assert [child.text for child in replayed.supplements[0].children] == [
        "Scottish Legal Aid Board",
        "Scottish Legal Aid Board",
    ]
    assert len(adjudications) == 1
    assert adjudications
    assert adjudications[0].kind == "uk_replay_schedule_list_entry_anchor_unresolved"
    assert adjudications[0].detail["reason_code"] == "anchor_not_unique"
    assert adjudications[0].detail["strict_disposition"] == "block"


def test_replay_schedule_list_entry_insert_target_gap_does_not_emit_payload_mismatch() -> None:
    op = LegalOperation(
        op_id="uk_test_schedule_entry_missing_schedule",
        sequence=0,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("schedule", "5"),)),
        payload=IRNode(kind=IRNodeKind.SCHEDULE_ENTRY, label=None, text="Inserted entry"),
        provenance_tags=(
            'schedule_list_entry_selector:{"rule_id":"uk_effect_schedule_list_entry_insert",'
            '"direction":"after","anchor_text":"Scottish Legal Aid Board","inserted_text":"Inserted entry"}',
        ),
    )
    base = IRStatute(
        statute_id="asp/2010/8",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=()),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="SCHEDULE 3",
                children=(
                    IRNode(kind=IRNodeKind.SCHEDULE_ENTRY, label=None, text="Scottish Legal Aid Board"),
                ),
            ),
        ),
    )
    adjudications: list[CompileAdjudication] = []

    replayed = replay_uk_ops(base, [op], adjudications_out=adjudications)

    assert [schedule.label for schedule in replayed.supplements] == ["SCHEDULE 3"]
    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_schedule_list_entry_anchor_unresolved"
    assert adjudications[0].detail["reason_code"] == "schedule_target_unresolved"


def test_replay_schedule_list_entry_insert_after_schedule_repeal_reports_repealed_target() -> None:
    repeal_op = LegalOperation(
        op_id="uk_test_repeal_schedule_5",
        sequence=0,
        action=StructuralAction.REPEAL,
        target=LegalAddress(path=(("schedule", "5"),)),
    )
    insert_op = LegalOperation(
        op_id="uk_test_schedule_entry_after_repeal",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("schedule", "5"),)),
        payload=IRNode(kind=IRNodeKind.SCHEDULE_ENTRY, label=None, text="Inserted entry"),
        provenance_tags=(
            'schedule_list_entry_selector:{"rule_id":"uk_effect_schedule_list_entry_insert",'
            '"direction":"after","anchor_text":"Scottish Legal Aid Board","inserted_text":"Inserted entry"}',
        ),
    )
    base = IRStatute(
        statute_id="asp/2010/8",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=()),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="SCHEDULE 5",
                children=(
                    IRNode(kind=IRNodeKind.SCHEDULE_ENTRY, label=None, text="Scottish Legal Aid Board"),
                ),
            ),
        ),
    )
    adjudications: list[CompileAdjudication] = []

    replayed = replay_uk_ops(base, [repeal_op, insert_op], adjudications_out=adjudications)

    assert replayed.supplements == ()
    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_repealed_target_gap"
    assert adjudications[0].detail["reason_code"] == "schedule_target_previously_repealed"
    assert adjudications[0].detail["strict_disposition"] == "block"


def test_replay_schedule_list_entry_insert_resolves_anchor_inside_schedule_group() -> None:
    op = LegalOperation(
        op_id="uk_test_schedule_entry_grouped_anchor",
        sequence=0,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("schedule", "5"),)),
        payload=IRNode(kind=IRNodeKind.SCHEDULE_ENTRY, label=None, text="Scottish Fire and Rescue Service"),
        provenance_tags=(
            'schedule_list_entry_selector:{"rule_id":"uk_effect_schedule_list_entry_insert",'
            '"direction":"after","anchor_text":"Scottish Environment Protection Agency",'
            '"inserted_text":"Scottish Fire and Rescue Service"}',
        ),
    )
    base = IRStatute(
        statute_id="asp/2010/8",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=()),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="SCHEDULE 5",
                children=(
                    IRNode(
                        kind=IRNodeKind.P1GROUP,
                        label=None,
                        text="Scottish public authorities with mixed functions or no reserved functions",
                        children=(
                            IRNode(kind=IRNodeKind.SCHEDULE_ENTRY, label=None, text="Scottish Environment Protection Agency"),
                            IRNode(kind=IRNodeKind.SCHEDULE_ENTRY, label=None, text="Scottish Legal Aid Board"),
                        ),
                    ),
                    IRNode(
                        kind=IRNodeKind.P1GROUP,
                        label=None,
                        text="Cross-border public authorities",
                        children=(
                            IRNode(kind=IRNodeKind.SCHEDULE_ENTRY, label=None, text="Forestry Commissioners"),
                        ),
                    ),
                ),
            ),
        ),
    )
    adjudications: list[CompileAdjudication] = []

    replayed = replay_uk_ops(base, [op], adjudications_out=adjudications)

    first_group = replayed.supplements[0].children[0]
    second_group = replayed.supplements[0].children[1]
    assert [child.text for child in first_group.children] == [
        "Scottish Environment Protection Agency",
        "Scottish Fire and Rescue Service",
        "Scottish Legal Aid Board",
    ]
    assert [child.text for child in second_group.children] == ["Forestry Commissioners"]
    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_schedule_list_entry_group_anchor_resolved"
    assert adjudications[0].detail["group_text"] == (
        "Scottish public authorities with mixed functions or no reserved functions"
    )
    assert adjudications[0].detail["strict_disposition"] == "record"


def test_replay_schedule_list_entry_insert_blocks_ambiguous_grouped_anchor() -> None:
    op = LegalOperation(
        op_id="uk_test_schedule_entry_grouped_anchor_ambiguous",
        sequence=0,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("schedule", "5"),)),
        payload=IRNode(kind=IRNodeKind.SCHEDULE_ENTRY, label=None, text="Inserted entry"),
        provenance_tags=(
            'schedule_list_entry_selector:{"rule_id":"uk_effect_schedule_list_entry_insert",'
            '"direction":"after","anchor_text":"Shared Authority","inserted_text":"Inserted entry"}',
        ),
    )
    base = IRStatute(
        statute_id="asp/2010/8",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=()),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="SCHEDULE 5",
                children=(
                    IRNode(
                        kind=IRNodeKind.P1GROUP,
                        label=None,
                        text="First group",
                        children=(IRNode(kind=IRNodeKind.SCHEDULE_ENTRY, label=None, text="Shared Authority"),),
                    ),
                    IRNode(
                        kind=IRNodeKind.P1GROUP,
                        label=None,
                        text="Second group",
                        children=(IRNode(kind=IRNodeKind.SCHEDULE_ENTRY, label=None, text="Shared Authority"),),
                    ),
                ),
            ),
        ),
    )
    adjudications: list[CompileAdjudication] = []

    replayed = replay_uk_ops(base, [op], adjudications_out=adjudications)

    assert [child.text for group in replayed.supplements[0].children for child in group.children] == [
        "Shared Authority",
        "Shared Authority",
    ]
    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_schedule_list_entry_anchor_unresolved"
    assert adjudications[0].detail["anchor_match_count"] == 2
    assert adjudications[0].detail["grouped_entry_count"] == 2
    assert adjudications[0].detail["strict_disposition"] == "block"


def test_compile_schedule_list_entry_repeal_lowers_to_selector() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P3 xmlns="{_LEG_NS}">
          <Text>the entry relating to the Scottish Arts Council is repealed.</Text>
        </P3>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_schedule_list_entry_repeal",
        effect_type="entry repealed",
        applied=True,
        requires_applied=True,
        modified="2010-07-01",
        affected_uri="/id/asp/2000/7/schedule/3",
        affected_class="ScottishAct",
        affected_year="2000",
        affected_number="7",
        affected_provisions="Sch. 3",
        affecting_uri="/id/asp/2010/8",
        affecting_class="ScottishAct",
        affecting_year="2010",
        affecting_number="8",
        affecting_provisions="sch. 17 para. 2",
        affecting_title="Test Act",
        in_force_dates=[{"date": "2010-07-01", "prospective": "false"}],
    )
    observations: list[dict[str, object]] = []

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0, lowering_rejections_out=observations)

    assert len(ops) == 1
    op = ops[0]
    assert op.action is StructuralAction.REPEAL
    assert op.target == LegalAddress(path=(("schedule", "3"),))
    assert op.payload is None
    assert op.witness_rule_id == "uk_effect_schedule_list_entry_repeal"
    selector_note = next(
        note for note in op.provenance_tags if note.startswith("schedule_list_entry_repeal_selector:")
    )
    selector = json.loads(selector_note.removeprefix("schedule_list_entry_repeal_selector:"))
    assert selector["anchors"] == ["the Scottish Arts Council"]
    assert observations[0]["rule_id"] == "uk_effect_schedule_list_entry_repeal"
    assert observations[0]["blocking"] is False


def test_compile_schedule_list_entry_repeal_handles_multiple_anchors() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P3 xmlns="{_LEG_NS}">
          <Text>the entries relating to— i the Scottish Further Education Funding Council;
          and ii the Scottish Higher Education Funding Council, are repealed.</Text>
        </P3>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_schedule_list_entry_repeal_multiple",
        effect_type="entry repealed",
        applied=True,
        requires_applied=True,
        modified="2005-10-03",
        affected_uri="/id/asp/2000/7/schedule/3",
        affected_class="ScottishAct",
        affected_year="2000",
        affected_number="7",
        affected_provisions="Sch. 3",
        affecting_uri="/id/asp/2005/6",
        affecting_class="ScottishAct",
        affecting_year="2005",
        affecting_number="6",
        affecting_provisions="Sch. 3 para. 9(b)",
        affecting_title="Test Act",
        in_force_dates=[{"date": "2005-10-03", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    selector_note = next(
        note for note in ops[0].provenance_tags if note.startswith("schedule_list_entry_repeal_selector:")
    )
    selector = json.loads(selector_note.removeprefix("schedule_list_entry_repeal_selector:"))
    assert selector["anchors"] == [
        "the Scottish Further Education Funding Council",
        "the Scottish Higher Education Funding Council",
    ]


def test_compile_schedule_list_entry_repeal_splits_comma_and_final_and_list() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P3 xmlns="{_LEG_NS}">
          <Text>the entries for the East of Scotland Water Authority,
          the North of Scotland Water Authority and the West of Scotland
          Water Authority are repealed.</Text>
        </P3>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_schedule_list_entry_repeal_water",
        effect_type="entry repealed",
        applied=True,
        requires_applied=True,
        modified="2002-04-01",
        affected_uri="/id/asp/2000/7/schedule/3",
        affected_class="ScottishAct",
        affected_year="2000",
        affected_number="7",
        affected_provisions="Sch. 3",
        affecting_uri="/id/asp/2002/3",
        affecting_class="ScottishAct",
        affecting_year="2002",
        affecting_number="3",
        affecting_provisions="sch. 7 para. 2",
        affecting_title="Test Act",
        in_force_dates=[{"date": "2002-04-01", "prospective": "false"}],
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    selector_note = next(
        note for note in ops[0].provenance_tags if note.startswith("schedule_list_entry_repeal_selector:")
    )
    selector = json.loads(selector_note.removeprefix("schedule_list_entry_repeal_selector:"))
    assert selector["anchors"] == [
        "the East of Scotland Water Authority",
        "the North of Scotland Water Authority",
        "the West of Scotland Water Authority",
    ]


def test_replay_schedule_list_entry_repeal_deletes_only_matched_entry() -> None:
    op = LegalOperation(
        op_id="uk_test_schedule_entry_repeal",
        sequence=0,
        action=StructuralAction.REPEAL,
        target=LegalAddress(path=(("schedule", "3"),)),
        provenance_tags=(
            'schedule_list_entry_repeal_selector:{"rule_id":"uk_effect_schedule_list_entry_repeal",'
            '"anchors":["the Scottish Arts Council"]}',
        ),
    )
    base = IRStatute(
        statute_id="asp/2000/7",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=()),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="SCHEDULE 3",
                text="Devolved public bodies",
                children=(
                    IRNode(kind=IRNodeKind.SCHEDULE_ENTRY, label=None, text="The Scottish Arts Council"),
                    IRNode(kind=IRNodeKind.SCHEDULE_ENTRY, label=None, text="Scottish Enterprise"),
                ),
            ),
        ),
    )
    adjudications: list[CompileAdjudication] = []

    replayed = replay_uk_ops(base, [op], adjudications_out=adjudications)

    assert [child.text for child in replayed.supplements[0].children] == ["Scottish Enterprise"]
    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_schedule_list_entry_repeal_resolved"
    assert adjudications[0].detail["deleted_count"] == 1
    assert adjudications[0].detail["strict_disposition"] == "record"


def test_replay_schedule_list_entry_repeal_is_all_or_nothing_when_anchor_missing() -> None:
    op = LegalOperation(
        op_id="uk_test_schedule_entry_repeal_missing",
        sequence=0,
        action=StructuralAction.REPEAL,
        target=LegalAddress(path=(("schedule", "3"),)),
        provenance_tags=(
            'schedule_list_entry_repeal_selector:{"rule_id":"uk_effect_schedule_list_entry_repeal",'
            '"anchors":["The East of Scotland Water Authority","The Missing Water Authority"]}',
        ),
    )
    base = IRStatute(
        statute_id="asp/2000/7",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=()),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="SCHEDULE 3",
                text="Devolved public bodies",
                children=(
                    IRNode(kind=IRNodeKind.SCHEDULE_ENTRY, label=None, text="The East of Scotland Water Authority"),
                    IRNode(kind=IRNodeKind.SCHEDULE_ENTRY, label=None, text="Scottish Enterprise"),
                ),
            ),
        ),
    )
    adjudications: list[CompileAdjudication] = []

    replayed = replay_uk_ops(base, [op], adjudications_out=adjudications)

    assert [child.text for child in replayed.supplements[0].children] == [
        "The East of Scotland Water Authority",
        "Scottish Enterprise",
    ]
    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_schedule_list_entry_repeal_unresolved"
    assert adjudications[0].detail["anchor"] == "The Missing Water Authority"
    assert adjudications[0].detail["strict_disposition"] == "block"


def test_replay_schedule_list_entry_repeal_blocks_when_anchor_ambiguous() -> None:
    op = LegalOperation(
        op_id="uk_test_schedule_entry_repeal_ambiguous",
        sequence=0,
        action=StructuralAction.REPEAL,
        target=LegalAddress(path=(("schedule", "3"),)),
        provenance_tags=(
            'schedule_list_entry_repeal_selector:{"rule_id":"uk_effect_schedule_list_entry_repeal",'
            '"anchors":["Scottish Legal Aid Board"]}',
        ),
    )
    base = IRStatute(
        statute_id="asp/2000/7",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=()),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="SCHEDULE 3",
                text="Devolved public bodies",
                children=(
                    IRNode(kind=IRNodeKind.SCHEDULE_ENTRY, label=None, text="Scottish Legal Aid Board"),
                    IRNode(kind=IRNodeKind.SCHEDULE_ENTRY, label=None, text="Scottish Legal Aid Board"),
                ),
            ),
        ),
    )
    adjudications: list[CompileAdjudication] = []

    replayed = replay_uk_ops(base, [op], adjudications_out=adjudications)

    assert len(replayed.supplements[0].children) == 2
    assert adjudications[0].kind == "uk_replay_schedule_list_entry_repeal_unresolved"
    assert adjudications[0].detail["reason_code"] == "anchor_not_unique"


def test_replay_blocks_schedule_entry_repeal_widened_to_whole_schedule() -> None:
    op = LegalOperation(
        op_id="unsafe-schedule-entry-repeal",
        sequence=0,
        action=StructuralAction.REPEAL,
        target=LegalAddress(path=(("schedule", "3"),)),
        payload=IRNode(
            kind=IRNodeKind.SCHEDULE,
            label="3",
            text="the entries for the East of Scotland Water Authority are repealed",
        ),
        source=OperationSource(
            statute_id="asp/2002/3",
            title="Water Industry (Scotland) Act 2002",
            effective="",
            raw_text="the entries for the East of Scotland Water Authority are repealed",
        ),
    )
    base = IRStatute(
        statute_id="asp/2000/7",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=()),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="SCHEDULE 3",
                text="Devolved public bodies",
                children=(
                    IRNode(
                        kind=IRNodeKind.SCHEDULE_ENTRY,
                        label=None,
                        text="The East of Scotland Water Authority",
                    ),
                    IRNode(
                        kind=IRNodeKind.SCHEDULE_ENTRY,
                        label=None,
                        text="The Scottish Legal Aid Board",
                    ),
                ),
            ),
        ),
    )
    adjudications: list[CompileAdjudication] = []

    replayed = replay_uk_ops(base, [op], adjudications_out=adjudications)

    assert [schedule.label for schedule in replayed.supplements] == ["SCHEDULE 3"]
    assert [child.text for child in replayed.supplements[0].children] == [
        "The East of Scotland Water Authority",
        "The Scottish Legal Aid Board",
    ]
    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_schedule_entry_repeal_granularity_blocked"
    assert adjudications[0].detail["reason"] == "schedule_entry_repeal_widened_to_schedule"
    assert adjudications[0].detail["strict_disposition"] == "block"


def test_replay_blocks_singular_schedule_entry_repeal_widened_to_whole_schedule() -> None:
    op = LegalOperation(
        op_id="unsafe-singular-schedule-entry-repeal",
        sequence=0,
        action=StructuralAction.REPEAL,
        target=LegalAddress(path=(("schedule", "3"),)),
        payload=IRNode(
            kind=IRNodeKind.SCHEDULE,
            label="3",
            text="the entry relating to the Scottish Arts Council is repealed",
        ),
        source=OperationSource(
            statute_id="asp/2010/8",
            title="Public Services Reform (Scotland) Act 2010",
            effective="2010-07-01",
            raw_text="the entry relating to the Scottish Arts Council is repealed",
        ),
    )
    base = IRStatute(
        statute_id="asp/2000/7",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=()),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="SCHEDULE 3",
                text="Devolved public bodies",
                children=(
                    IRNode(kind=IRNodeKind.SCHEDULE_ENTRY, label=None, text="The Scottish Arts Council"),
                    IRNode(kind=IRNodeKind.SCHEDULE_ENTRY, label=None, text="Scottish Enterprise"),
                ),
            ),
        ),
    )
    adjudications: list[CompileAdjudication] = []

    replayed = replay_uk_ops(base, [op], adjudications_out=adjudications)

    assert [child.text for child in replayed.supplements[0].children] == [
        "The Scottish Arts Council",
        "Scottish Enterprise",
    ]
    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_schedule_entry_repeal_granularity_blocked"
    assert adjudications[0].detail["strict_disposition"] == "block"


def test_executor_replace_overwrites_existing_schedule_root() -> None:
    statute = IRStatute(
        statute_id="ukpga/2010/9",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=()),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="1",
                text="Old schedule",
                attrs={"eId": "schedule-1"},
                children=(IRNode(kind=IRNodeKind.PARAGRAPH, label="1", text="Old paragraph"),),
            ),
        ),
    )
    executor: Any = UKReplayExecutor(statute)
    op = LegalOperation(
        op_id="uk_test_replace_schedule_root",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("schedule", "1"),)),
        payload=IRNode(
            kind=IRNodeKind.SCHEDULE,
            label="1",
            text="New schedule",
            children=(IRNode(kind=IRNodeKind.PARAGRAPH, label="13", text="New paragraph"),),
        ),
        source=OperationSource(statute_id="ukpga/2012/5", title="Amending Act"),
    )

    executor.apply_op(op)

    assert len(executor.statute.supplements) == 1
    schedule = executor.statute.supplements[0]
    assert schedule.attrs["eId"] == "schedule-1"
    assert schedule.text == "New schedule"
    assert [child.label for child in schedule.children] == ["13"]


def test_executor_replace_overwrites_existing_subsection_with_leaf_payload() -> None:
    statute = IRStatute(
        statute_id="ukpga/2003/31",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="5C",
                    text="Guidance",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="5",
                            text="Old subsection",
                            attrs={"eId": "section-5C-5"},
                            children=(
                                IRNode(kind=IRNodeKind.PARAGRAPH, label="a", text="Old a"),
                                IRNode(kind=IRNodeKind.PARAGRAPH, label="b", text="Old b"),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor: Any = UKReplayExecutor(statute)
    op = LegalOperation(
        op_id="uk_test_replace_subsection_with_leaf",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "5C"), ("subsection", "5"))),
        payload=IRNode(
            kind=IRNodeKind.SUBSECTION,
            label="5",
            text="New subsection text",
            children=(),
        ),
        source=OperationSource(statute_id="ukpga/2015/9", title="Amending Act"),
    )

    executor.apply_op(op)

    section = executor.statute.body.children[0]
    subsection = section.children[0]
    assert subsection.attrs["eId"] == "section-5C-5"
    assert subsection.text == "New subsection text"
    assert subsection.children == []


def test_executor_text_replace_uses_fragment_substitution_fallback_when_primary_misses() -> None:
    statute = IRStatute(
        statute_id="ukpga/2010/9",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=()),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="1",
                text="",
                attrs={"eId": "schedule-1"},
                children=(
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="1",
                        text="The service is available on Saturdays.",
                    ),
                ),
            ),
        ),
    )
    executor: Any = UKReplayExecutor(statute)
    op = LegalOperation(
        op_id="uk_test_text_replace_fragment_fallback",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=(("schedule", "1"), ("paragraph", "1"))),
        payload=None,
        source=OperationSource(statute_id="ukpga/2012/5", title="Amending Act"),
        text_patch=_replace_patch("wrong token", "ignored"),
        provenance_tags=(
            "fragment_substitution:"
            + json.dumps(
                [
                    {"original": "wrong token", "replacement": "ignored"},
                    {"original": "Saturdays", "replacement": "weekends"},
                ],
                ensure_ascii=False,
            ),
        ),
    )

    executor.apply_op(op)

    assert executor.statute.supplements[0].children[0].text == "The service is available on weekends."
    assert not executor.adjudications_out


def test_executor_repeal_collapses_oracle_zombie_schedule_root() -> None:
    statute = IRStatute(
        statute_id="ukpga/2010/26",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=()),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="1",
                text="Amendments of Part 2",
                attrs={"eId": "schedule-1"},
                children=(IRNode(kind=IRNodeKind.PARAGRAPH, label="1", text="Old paragraph"),),
            ),
        ),
    )
    executor: Any = UKReplayExecutor(statute)
    op = LegalOperation(
        op_id="uk_test_repeal_schedule_zombie",
        sequence=1,
        action=StructuralAction.REPEAL,
        target=LegalAddress(path=(("schedule", "1"),)),
        payload=None,
        source=OperationSource(statute_id="ukpga/2011/10", title="Amending Act"),
        provenance_tags=("oracle_zombie_collapse:schedule-1",),
    )

    executor.apply_op(op)

    assert executor.statute.supplements == []


def test_executor_repeal_collapses_oracle_zombie_nested_part() -> None:
    zombie_part = IRNode(
        kind=IRNodeKind.PART,
        label="Part 2",
        text="Family proceedings",
        attrs={"eId": "schedule-3-part-2"},
        children=(IRNode(kind=IRNodeKind.PARAGRAPH, label="3", text="Old paragraph"),),
    )
    statute = IRStatute(
        statute_id="ukpga/2010/26",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=()),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="3",
                text="Schedule 3",
                attrs={"eId": "schedule-3"},
                children=(zombie_part,),
            ),
        ),
    )
    executor: Any = UKReplayExecutor(statute)
    op = LegalOperation(
        op_id="uk_test_repeal_nested_part_zombie",
        sequence=1,
        action=StructuralAction.REPEAL,
        target=LegalAddress(path=(("schedule", "3"), ("part", "2"))),
        payload=None,
        source=OperationSource(statute_id="ukpga/2011/10", title="Amending Act"),
        provenance_tags=("oracle_zombie_collapse:schedule-3-part-2",),
    )

    executor.apply_op(op)

    schedule = executor.statute.supplements[0]
    assert schedule.children == []


def test_replay_uk_ops_collapses_top_level_part_zombie() -> None:
    base = IRStatute(
        statute_id="ukpga/2010/26",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.PART,
                    label="Part 2",
                    text="Family proceedings",
                    attrs={"eId": "part-2"},
                    children=(IRNode(kind=IRNodeKind.SECTION, label="22", text="Old section"),),
                ),
            ),
        ),
        supplements=(),
    )
    op = LegalOperation(
        op_id="uk_test_repeal_top_level_part_zombie",
        sequence=1,
        action=StructuralAction.REPEAL,
        target=LegalAddress(path=(("part", "2"),)),
        payload=None,
        source=OperationSource(statute_id="ukpga/2011/10", title="Amending Act"),
    )

    replayed = replay_uk_ops(base, [op], eid_map={"part:2": "part-2"})

    assert replayed.body.children == ()


def test_replay_uk_ops_preserves_top_level_section_zombie_subtree_when_oracle_keeps_descendants() -> None:
    base = IRStatute(
        statute_id="ukpga/2000/44",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="3",
                    text="Section 3",
                    attrs={"eId": "section-3"},
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1",
                            text="Subsection 1",
                            attrs={"eId": "section-3-1"},
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    op = LegalOperation(
        op_id="uk_test_repeal_top_level_section_zombie_keep_subtree",
        sequence=1,
        action=StructuralAction.REPEAL,
        target=LegalAddress(path=(("section", "3"),)),
        payload=None,
        source=OperationSource(statute_id="ukpga/2003/42", title="Amending Act"),
    )

    replayed = replay_uk_ops(
        base,
        [op],
        eid_map={
            "section:3": "section-3",
            "section:3/subsection:1": "section-3-1",
        },
    )

    assert replayed.body.children == ()


def test_pipeline_apply_ops_skips_whole_act_repeal_like_replay_uk_ops() -> None:
    base = IRStatute(
        statute_id="ukpga/2000/44",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="1",
                    text="Section 1",
                    attrs={"eId": "section-1"},
                ),
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="2",
                    text="Section 2",
                    attrs={"eId": "section-2"},
                ),
            ),
        ),
        supplements=(),
    )
    op = LegalOperation(
        op_id="uk_test_pipeline_whole_act_repeal",
        sequence=1,
        action=StructuralAction.REPEAL,
        target=LegalAddress(path=(), special=None),
        payload=None,
        source=OperationSource(
            statute_id="ukpga/2000/44",
            title="Source-bad whole-act repeal row",
        ),
    )

    pipeline = UKReplayPipeline(Path("."))
    replayed = replay_uk_ops(base, [op], eid_map={"section:1": "section-1"})
    pipelined = pipeline.apply_ops(base, [op], eid_map={"section:1": "section-1"})

    assert [child.label for child in replayed.body.children] == ["1", "2"]
    assert [child.label for child in pipelined.body.children] == ["1", "2"]


def test_pipeline_compile_ops_skips_instruction_text_reused_as_payload_rows(monkeypatch) -> None:
    effect = UKEffectRecord(
        effect_id="uk_test_instruction_payload_skip",
        effect_type="substituted",
        applied=True,
        requires_applied=False,
        modified="2002-12-31",
        affected_uri="/id/ukpga/2001/11",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2001",
        affected_number="11",
        affected_provisions="s. 7 8 9",
        affecting_uri="/id/uksi/2001/4022",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2001",
        affecting_number="4022",
        affecting_provisions="reg. 20",
        affecting_title="Test Regulations",
        in_force_dates=[{"date": "2002-01-01", "prospective": "false"}],
    )
    extracted_el = ET.fromstring(
        f"""
        <P1 xmlns="{_LEG_NS}">
          <Text>Any restriction in section 6B, 7, 8 or 9 of the Act shall not apply.</Text>
        </P1>
        """
    )
    compiled = [
        LegalOperation(
            op_id="uk_test_instruction_payload_skip_0",
            sequence=0,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "7"),)),
            payload=IRNode(
                kind=IRNodeKind.SECTION, label="7", text="Any restriction in section 6B, 7, 8 or 9 of the Act shall not apply."
            ),
            source=OperationSource(statute_id="ukpga/2001/11", title="Test Regulations"),
        ),
        LegalOperation(
            op_id="uk_test_instruction_payload_skip_1",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "8"),)),
            payload=IRNode(
                kind=IRNodeKind.SECTION, label="8", text="Any restriction in section 6B, 7, 8 or 9 of the Act shall not apply."
            ),
            source=OperationSource(statute_id="ukpga/2001/11", title="Test Regulations"),
        ),
    ]

    monkeypatch.setattr(
        uk_replay_mod,
        "load_effects_for_statute_from_archive",
        lambda _sid, _archive: [effect],
    )
    monkeypatch.setattr(
        uk_replay_mod,
        "get_affecting_act_xml_from_archive",
        lambda _aid, _archive: _AVAILABLE_XML_BYTES,
    )
    monkeypatch.setattr(
        uk_replay_mod,
        "extract_provision_element_from_bytes",
        lambda _xml, _prov, **_kwargs: extracted_el,
    )
    monkeypatch.setattr(
        uk_replay_mod,
        "compile_effect_to_ir_ops",
        lambda _effect, _el, sequence=0, **_kwargs: compiled,
    )

    pipeline = UKReplayPipeline(Path("."))

    lowering_rejections: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []

    assert pipeline.compile_ops_for_statute(
        "ukpga/2001/11",
        archive=object(),
        lowering_rejections_out=lowering_rejections,
        effect_diagnostics_out=diagnostics,
    ) == []
    assert lowering_rejections == [
        {
            "rule_id": "uk_effect_instruction_text_payload_rejected",
            "family": "source_pathology_filter",
            "phase": "lowering",
            "effect_id": "uk_test_instruction_payload_skip",
            "affecting_act_id": "uksi/2001/4022",
            "affected_provisions": "s. 7 8 9",
            "affecting_provisions": "reg. 20",
            "effect_type": "substituted",
            "reason": "UK effect payload reused instruction text rather than source legal payload",
            "blocking": True,
            "strict_disposition": "block",
            "quirks_disposition": "record",
            "source_pathology": "instruction_text_reused_as_payload",
        }
    ]
    manual_rows = [
        row
        for row in diagnostics
        if row["rule_id"] == "uk_manual_compile_frontier_classified"
    ]
    assert manual_rows == [
        {
            "rule_id": "uk_manual_compile_frontier_classified",
            "family": "manual_compile_frontier",
            "phase": "lowering",
            "effect_id": "uk_test_instruction_payload_skip",
            "affecting_act_id": "uksi/2001/4022",
            "affected_provisions": "s. 7 8 9",
            "affecting_provisions": "reg. 20",
            "effect_type": "substituted",
            "manual_compile_status": "source_insufficient",
            "manual_compile_rule_id": "uk_manual_frontier_source_pathology_insufficient",
            "manual_compile_reason": (
                "The blocking row is dominated by source-shape pathology rather than "
                "an unambiguous manual compilation opportunity."
            ),
            "source_pathology": "instruction_text_reused_as_payload",
            "structural_for_replay": True,
            "replay_applicable": True,
            "compiled_op_count": 2,
            "blocking": False,
            "strict_disposition": "record",
            "quirks_disposition": "record",
        }
    ]


def test_pipeline_compile_ops_blocks_range_to_container_substitution_until_owned(monkeypatch) -> None:
    effect = UKEffectRecord(
        effect_id="uk_test_range_to_container_skip",
        effect_type="substituted for ss. 3-12 and cross-heading",
        applied=True,
        requires_applied=True,
        modified="2024-01-23",
        affected_uri="/id/asp/2001/2",
        affected_class="ScottishAct",
        affected_year="2001",
        affected_number="2",
        affected_provisions="Pt. 2 Ch. 1",
        affecting_uri="/id/asp/2019/17",
        affecting_class="ScottishAct",
        affecting_year="2019",
        affecting_number="17",
        affecting_provisions="s. 35(2)",
        affecting_title="Transport (Scotland) Act 2019",
        in_force_dates=[{"date": "2023-12-04", "prospective": "false"}],
    )
    extracted_el = ET.fromstring(
        f"""
        <P2 xmlns="{_LEG_NS}">
          <Text>For sections 3 to 12 (including the italic heading immediately preceding section 3), substitute—</Text>
          <BlockAmendment>
            <Chapter eId="part-2-chapter-1">
              <Number>Chapter 1</Number>
              <Title>Bus services improvement partnerships</Title>
            </Chapter>
          </BlockAmendment>
        </P2>
        """
    )
    compiled = [
        LegalOperation(
            op_id="uk_test_range_to_container_skip",
            sequence=0,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("part", "2"), ("chapter", "1"))),
            payload=IRNode(
                kind=IRNodeKind.CHAPTER,
                label="1",
                text="Bus services improvement partnerships",
                attrs={"eId": "part-2-chapter-1"},
            ),
            source=OperationSource(statute_id="asp/2001/2", title="Transport (Scotland) Act 2019"),
        )
    ]

    monkeypatch.setattr(
        uk_replay_mod,
        "load_effects_for_statute_from_archive",
        lambda _sid, _archive: [effect],
    )
    monkeypatch.setattr(
        uk_replay_mod,
        "get_affecting_act_xml_from_archive",
        lambda _aid, _archive: _AVAILABLE_XML_BYTES,
    )
    monkeypatch.setattr(
        uk_replay_mod,
        "extract_provision_element_from_bytes",
        lambda _xml, _prov, **_kwargs: extracted_el,
    )
    monkeypatch.setattr(
        uk_replay_mod,
        "compile_effect_to_ir_ops",
        lambda _effect, _el, sequence=0, **_kwargs: compiled,
    )

    lowering_rejections: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []

    assert UKReplayPipeline(Path(".")).compile_ops_for_statute(
        "asp/2001/2",
        archive=object(),
        lowering_rejections_out=lowering_rejections,
        effect_diagnostics_out=diagnostics,
    ) == []

    assert lowering_rejections[-1]["rule_id"] == "uk_effect_range_to_container_substitution_rejected"
    assert lowering_rejections[-1]["blocking"] is True
    assert lowering_rejections[-1]["source_pathology"] == "range_to_container_target_unsupported"
    manual_rows = [
        row
        for row in diagnostics
        if row["rule_id"] == "uk_manual_compile_frontier_classified"
    ]
    assert manual_rows[-1]["manual_compile_status"] == "manual_compile_candidate"
    assert manual_rows[-1]["manual_compile_rule_id"] == "uk_manual_frontier_range_to_container_candidate"


def test_pipeline_compile_ops_records_replay_applicability_filter_for_compiled_ops(monkeypatch) -> None:
    effect = UKEffectRecord(
        effect_id="uk_test_unapplied_compiled_filter",
        effect_type="inserted",
        applied=False,
        requires_applied=True,
        modified="2024-01-01",
        affected_uri="/id/ukpga/2000/10",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="10",
        affected_provisions="s. 57",
        affecting_uri="/id/uksi/2000/2040",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2000",
        affecting_number="2040",
        affecting_provisions="Sch. 2 para. 7",
        affecting_title="Unapplied Affecting Act",
        in_force_dates=[{"date": "2024-01-01", "prospective": "false"}],
    )
    compiled = [
        LegalOperation(
            op_id="uk_test_unapplied_compiled_filter_0",
            sequence=0,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("section", "57A"),)),
            payload=IRNode(kind=IRNodeKind.SECTION, label="57A", text="Inserted text."),
            source=OperationSource(statute_id="ukpga/2000/10", title="Unapplied Affecting Act"),
        )
    ]

    monkeypatch.setattr(
        uk_replay_mod,
        "load_effects_for_statute_from_archive",
        lambda _sid, _archive: [effect],
    )
    monkeypatch.setattr(
        uk_replay_mod,
        "compile_effect_to_ir_ops",
        lambda _effect, _el, sequence=0, **_kwargs: compiled,
    )

    diagnostics: list[dict[str, Any]] = []
    pipeline = UKReplayPipeline(Path("."))

    assert pipeline.compile_ops_for_statute(
        "ukpga/2000/10",
        archive=object(),
        effect_diagnostics_out=diagnostics,
    ) == []

    filter_rows = [
        row
        for row in diagnostics
        if row["rule_id"] == "uk_effect_replay_applicability_filter_rejected"
    ]
    assert filter_rows == [
        {
            "rule_id": "uk_effect_replay_applicability_filter_rejected",
            "family": "applicability_filter",
            "phase": "lowering",
            "effect_id": "uk_test_unapplied_compiled_filter",
            "affecting_act_id": "uksi/2000/2040",
            "affected_provisions": "s. 57",
            "affecting_provisions": "Sch. 2 para. 7",
            "effect_type": "inserted",
            "compiled_op_count": 1,
            "compiled_op_ids": ["uk_test_unapplied_compiled_filter_0"],
            "compiled_op_actions": ["insert"],
            "structural_for_replay": False,
            "replay_applicable": False,
            "nonstructural_replay_family": "",
            "reason": "UK effect compiled to operations but replay applicability excludes the effect",
            "blocking": False,
            "strict_disposition": "record",
            "quirks_disposition": "record",
        }
    ]


def test_pipeline_compile_ops_does_not_record_replay_applicability_filter_for_structural_ops(monkeypatch) -> None:
    effect = UKEffectRecord(
        effect_id="uk_test_applied_compiled",
        effect_type="inserted",
        applied=True,
        requires_applied=True,
        modified="2024-01-01",
        affected_uri="/id/ukpga/2000/10",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="10",
        affected_provisions="s. 57",
        affecting_uri="/id/uksi/2000/2040",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2000",
        affecting_number="2040",
        affecting_provisions="Sch. 2 para. 7",
        affecting_title="Applied Affecting Act",
        in_force_dates=[{"date": "2024-01-01", "prospective": "false"}],
    )
    compiled = [
        LegalOperation(
            op_id="uk_test_applied_compiled_0",
            sequence=0,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("section", "57A"),)),
            payload=IRNode(kind=IRNodeKind.SECTION, label="57A", text="Inserted text."),
            source=OperationSource(statute_id="ukpga/2000/10", title="Applied Affecting Act"),
        )
    ]

    monkeypatch.setattr(
        uk_replay_mod,
        "load_effects_for_statute_from_archive",
        lambda _sid, _archive: [effect],
    )
    monkeypatch.setattr(
        uk_replay_mod,
        "get_affecting_act_xml_from_archive",
        lambda _aid, _archive: _AVAILABLE_XML_BYTES,
    )
    monkeypatch.setattr(
        uk_replay_mod,
        "compile_effect_to_ir_ops",
        lambda _effect, _el, sequence=0, **_kwargs: compiled,
    )

    diagnostics: list[dict[str, Any]] = []
    pipeline = UKReplayPipeline(Path("."))

    assert pipeline.compile_ops_for_statute(
        "ukpga/2000/10",
        archive=object(),
        effect_diagnostics_out=diagnostics,
    ) == compiled
    assert not any(
        row["rule_id"] == "uk_effect_replay_applicability_filter_rejected"
        for row in diagnostics
    )


def test_pipeline_compile_ops_records_pit_date_filtered_effects(monkeypatch) -> None:
    future_effect = UKEffectRecord(
        effect_id="uk_test_future_effect",
        effect_type="inserted",
        applied=True,
        requires_applied=False,
        modified="2024-01-01",
        affected_uri="/id/ukpga/2000/10",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="10",
        affected_provisions="s. 57",
        affecting_uri="/id/uksi/2025/2040",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2025",
        affecting_number="2040",
        affecting_provisions="Sch. 2 para. 7",
        affecting_title="Future Affecting Act",
        in_force_dates=[{"date": "2025-01-01", "prospective": "false"}],
    )
    present_effect = UKEffectRecord(
        effect_id="uk_test_present_effect",
        effect_type="inserted",
        applied=True,
        requires_applied=False,
        modified="2024-01-01",
        affected_uri="/id/ukpga/2000/10",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="10",
        affected_provisions="s. 56",
        affecting_uri="/id/uksi/2024/2040",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2024",
        affecting_number="2040",
        affecting_provisions="Sch. 1 para. 1",
        affecting_title="Present Affecting Act",
        in_force_dates=[{"date": "2024-01-01", "prospective": "false"}],
    )

    monkeypatch.setattr(
        uk_replay_mod,
        "load_effects_for_statute_from_archive",
        lambda _sid, _archive: [future_effect, present_effect],
    )
    monkeypatch.setattr(
        uk_replay_mod,
        "get_affecting_act_xml_from_archive",
        lambda _aid, _archive: b"<Legislation/>",
    )
    monkeypatch.setattr(
        uk_replay_mod,
        "extract_provision_element_from_bytes",
        lambda _xml, _prov, **_kwargs: None,
    )
    monkeypatch.setattr(
        uk_replay_mod,
        "compile_effect_to_ir_ops",
        lambda _effect, _el, sequence=0, **_kwargs: [],
    )

    diagnostics: list[dict[str, Any]] = []
    pipeline = UKReplayPipeline(Path("."))

    assert pipeline.compile_ops_for_statute(
        "ukpga/2000/10",
        pit_date="2024-06-01",
        archive=object(),
        effect_diagnostics_out=diagnostics,
    ) == []

    filter_rows = [
        row for row in diagnostics if row["rule_id"] == "uk_effect_pit_date_filter_rejected"
    ]
    assert filter_rows == [
        {
            "rule_id": "uk_effect_pit_date_filter_rejected",
            "family": "temporal_filter",
            "phase": "lowering",
            "effect_id": "uk_test_future_effect",
            "affecting_act_id": "uksi/2025/2040",
            "affected_provisions": "s. 57",
            "affecting_provisions": "Sch. 2 para. 7",
            "effect_type": "inserted",
            "effective_date": "2025-01-01",
            "pit_date": "2024-06-01",
            "reason": "UK effect effective date is later than requested point-in-time date",
            "blocking": False,
            "strict_disposition": "record",
            "quirks_disposition": "record",
        }
    ]


def test_pipeline_compile_ops_does_not_record_pit_filter_for_included_effects(monkeypatch) -> None:
    effect = UKEffectRecord(
        effect_id="uk_test_present_effect",
        effect_type="inserted",
        applied=True,
        requires_applied=False,
        modified="2024-01-01",
        affected_uri="/id/ukpga/2000/10",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="10",
        affected_provisions="s. 56",
        affecting_uri="/id/uksi/2024/2040",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2024",
        affecting_number="2040",
        affecting_provisions="Sch. 1 para. 1",
        affecting_title="Present Affecting Act",
        in_force_dates=[{"date": "2024-01-01", "prospective": "false"}],
    )

    monkeypatch.setattr(
        uk_replay_mod,
        "load_effects_for_statute_from_archive",
        lambda _sid, _archive: [effect],
    )
    monkeypatch.setattr(
        uk_replay_mod,
        "get_affecting_act_xml_from_archive",
        lambda _aid, _archive: b"<Legislation/>",
    )
    monkeypatch.setattr(
        uk_replay_mod,
        "extract_provision_element_from_bytes",
        lambda _xml, _prov, **_kwargs: None,
    )
    monkeypatch.setattr(
        uk_replay_mod,
        "compile_effect_to_ir_ops",
        lambda _effect, _el, sequence=0, **_kwargs: [],
    )

    diagnostics: list[dict[str, Any]] = []
    pipeline = UKReplayPipeline(Path("."))

    assert pipeline.compile_ops_for_statute(
        "ukpga/2000/10",
        pit_date="2024-06-01",
        archive=object(),
        effect_diagnostics_out=diagnostics,
    ) == []
    assert not any(row["rule_id"] == "uk_effect_pit_date_filter_rejected" for row in diagnostics)


def test_pipeline_compile_ops_records_structural_effect_lowered_to_no_ops(monkeypatch) -> None:
    effect = UKEffectRecord(
        effect_id="uk_test_structural_no_ops",
        effect_type="inserted",
        applied=True,
        requires_applied=False,
        modified="2024-01-01",
        affected_uri="/id/ukpga/2000/10",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="10",
        affected_provisions="s. 57",
        affecting_uri="/id/uksi/2000/2040",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2000",
        affecting_number="2040",
        affecting_provisions="Sch. 2 para. 7",
        affecting_title="Missing Affecting Act",
        in_force_dates=[{"date": "2024-01-01", "prospective": "false"}],
    )
    second_effect = UKEffectRecord(
        effect_id="uk_test_structural_no_ops_second",
        effect_type="inserted",
        applied=True,
        requires_applied=False,
        modified="2024-01-02",
        affected_uri="/id/ukpga/2000/10",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="10",
        affected_provisions="s. 58",
        affecting_uri="/id/uksi/2000/2041",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2000",
        affecting_number="2041",
        affecting_provisions="Sch. 2 para. 8",
        affecting_title="Missing Affecting Act 2",
        in_force_dates=[{"date": "2024-01-02", "prospective": "false"}],
    )

    monkeypatch.setattr(
        uk_replay_mod,
        "load_effects_for_statute_from_archive",
        lambda _sid, _archive: [effect, second_effect],
    )
    monkeypatch.setattr(
        uk_replay_mod,
        "get_affecting_act_xml_from_archive",
        lambda _aid, _archive: _AVAILABLE_XML_BYTES,
    )
    monkeypatch.setattr(
        uk_replay_mod,
        "extract_provision_element_from_bytes",
        lambda _xml, _prov, **_kwargs: None,
    )
    monkeypatch.setattr(
        uk_replay_mod,
        "compile_effect_to_ir_ops",
        lambda _effect, _el, sequence=0, **_kwargs: [],
    )

    pipeline = UKReplayPipeline(Path("."))
    lowering_rejections: list[dict[str, Any]] = []

    assert pipeline.compile_ops_for_statute(
        "ukpga/2000/10",
        archive=object(),
        lowering_rejections_out=lowering_rejections,
    ) == []
    assert lowering_rejections == [
        {
            "rule_id": "uk_effect_lowering_no_ops_rejected",
            "family": "lowering_filter",
            "phase": "lowering",
            "effect_id": "uk_test_structural_no_ops",
            "affecting_act_id": "uksi/2000/2040",
            "affected_provisions": "s. 57",
            "affecting_provisions": "Sch. 2 para. 7",
            "effect_type": "inserted",
            "reason": "UK structural effect lowered to no replay operations",
            "blocking": True,
            "strict_disposition": "block",
            "quirks_disposition": "record",
        },
        {
            "rule_id": "uk_effect_lowering_no_ops_rejected",
            "family": "lowering_filter",
            "phase": "lowering",
            "effect_id": "uk_test_structural_no_ops_second",
            "affecting_act_id": "uksi/2000/2041",
            "affected_provisions": "s. 58",
            "affecting_provisions": "Sch. 2 para. 8",
            "effect_type": "inserted",
            "reason": "UK structural effect lowered to no replay operations",
            "blocking": True,
            "strict_disposition": "block",
            "quirks_disposition": "record",
        }
    ]


def test_pipeline_compile_ops_records_nonstructural_replay_candidates_lowered_to_no_ops(monkeypatch) -> None:
    revoked_effect = UKEffectRecord(
        effect_id="uk_test_nonstructural_revoked_no_ops",
        effect_type="revoked",
        applied=True,
        requires_applied=False,
        modified="2024-01-01",
        affected_uri="/id/ukpga/2000/10",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="10",
        affected_provisions="s. 57(2)",
        affecting_uri="/id/uksi/2000/2040",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2000",
        affecting_number="2040",
        affecting_provisions="Sch. 2 para. 7",
        affecting_title="Missing Affecting Act",
        in_force_dates=[{"date": "2024-01-01", "prospective": "false"}],
    )
    ceases_effect = UKEffectRecord(
        effect_id="uk_test_nonstructural_ceases_no_ops",
        effect_type="ceases to have effect",
        applied=True,
        requires_applied=False,
        modified="2024-01-02",
        affected_uri="/id/ukpga/2000/10",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="10",
        affected_provisions="Sch. 4",
        affecting_uri="/id/ukpga/2000/10",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2000",
        affecting_number="10",
        affecting_provisions="s. 40(8)",
        affecting_title="Missing Affecting Act",
        in_force_dates=[{"date": "2024-01-02", "prospective": "false"}],
    )

    monkeypatch.setattr(
        uk_replay_mod,
        "load_effects_for_statute_from_archive",
        lambda _sid, _archive: [revoked_effect, ceases_effect],
    )
    monkeypatch.setattr(
        uk_replay_mod,
        "get_affecting_act_xml_from_archive",
        lambda _aid, _archive: _AVAILABLE_XML_BYTES,
    )
    monkeypatch.setattr(
        uk_replay_mod,
        "extract_provision_element_from_bytes",
        lambda _xml, _prov, **_kwargs: None,
    )
    monkeypatch.setattr(
        uk_replay_mod,
        "compile_effect_to_ir_ops",
        lambda _effect, _el, sequence=0, **_kwargs: [],
    )

    pipeline = UKReplayPipeline(Path("."))
    lowering_rejections: list[dict[str, Any]] = []

    assert pipeline.compile_ops_for_statute(
        "ukpga/2000/10",
        archive=object(),
        lowering_rejections_out=lowering_rejections,
    ) == []
    assert lowering_rejections == [
        {
            "rule_id": "uk_effect_nonstructural_lowering_no_ops_rejected",
            "family": "lowering_filter",
            "phase": "lowering",
            "effect_id": "uk_test_nonstructural_revoked_no_ops",
            "affecting_act_id": "uksi/2000/2040",
            "affected_provisions": "s. 57(2)",
            "affecting_provisions": "Sch. 2 para. 7",
            "effect_type": "revoked",
            "reason": "UK nonstructural effect row may be replayable but lowered to no replay operations",
            "blocking": True,
            "strict_disposition": "block",
            "quirks_disposition": "record",
            "nonstructural_replay_candidate_family": "revoked_repeal",
        },
        {
            "rule_id": "uk_effect_nonstructural_lowering_no_ops_rejected",
            "family": "lowering_filter",
            "phase": "lowering",
            "effect_id": "uk_test_nonstructural_ceases_no_ops",
            "affecting_act_id": "ukpga/2000/10",
            "affected_provisions": "Sch. 4",
            "affecting_provisions": "s. 40(8)",
            "effect_type": "ceases to have effect",
            "reason": "UK nonstructural effect row may be replayable but lowered to no replay operations",
            "blocking": True,
            "strict_disposition": "block",
            "quirks_disposition": "record",
            "nonstructural_replay_candidate_family": "ceases_to_have_effect_repeal",
        },
    ]


def test_pipeline_compile_ops_records_unsupported_nonstructural_no_ops(monkeypatch) -> None:
    effect = UKEffectRecord(
        effect_id="uk_test_nonstructural_modified_no_ops",
        effect_type="modified",
        applied=True,
        requires_applied=False,
        modified="2024-01-01",
        affected_uri="/id/ukpga/2000/10",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="10",
        affected_provisions="s. 57",
        affecting_uri="/id/uksi/2000/2040",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2000",
        affecting_number="2040",
        affecting_provisions="Sch. 2 para. 7",
        affecting_title="Missing Affecting Act",
        in_force_dates=[{"date": "2024-01-01", "prospective": "false"}],
    )

    monkeypatch.setattr(
        uk_replay_mod,
        "load_effects_for_statute_from_archive",
        lambda _sid, _archive: [effect],
    )
    monkeypatch.setattr(
        uk_replay_mod,
        "get_affecting_act_xml_from_archive",
        lambda _aid, _archive: _AVAILABLE_XML_BYTES,
    )
    monkeypatch.setattr(
        uk_replay_mod,
        "extract_provision_element_from_bytes",
        lambda _xml, _prov, **_kwargs: None,
    )
    monkeypatch.setattr(
        uk_replay_mod,
        "compile_effect_to_ir_ops",
        lambda _effect, _el, sequence=0, **_kwargs: [],
    )

    pipeline = UKReplayPipeline(Path("."))
    lowering_rejections: list[dict[str, Any]] = []

    assert pipeline.compile_ops_for_statute(
        "ukpga/2000/10",
        archive=object(),
        lowering_rejections_out=lowering_rejections,
    ) == []
    assert lowering_rejections == [
        {
            "rule_id": "uk_effect_nonstructural_unsupported_no_ops_observed",
            "family": "nonstructural_replay_observation",
            "phase": "lowering",
            "effect_id": "uk_test_nonstructural_modified_no_ops",
            "affecting_act_id": "uksi/2000/2040",
            "affected_provisions": "s. 57",
            "affecting_provisions": "Sch. 2 para. 7",
            "effect_type": "modified",
            "reason": (
                "UK applicable nonstructural effect row is not replay-supported "
                "under the selected replay lens and lowered to no replay operations"
            ),
            "blocking": False,
            "strict_disposition": "record",
            "quirks_disposition": "record",
        }
    ]


def test_pipeline_compile_ops_does_not_record_commencement_no_ops_as_unsupported(monkeypatch) -> None:
    effect = UKEffectRecord(
        effect_id="uk_test_commencement_no_ops",
        effect_type="coming into force",
        applied=True,
        requires_applied=False,
        modified="2025-01-01",
        affected_uri="/id/ukpga/2025/36",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2025",
        affected_number="36",
        affected_provisions="s. 63",
        affecting_uri="/id/uksi/2025/1",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2025",
        affecting_number="1",
        affecting_provisions="art. 2(c)",
        affecting_title="Test Commencement Order",
        in_force_dates=[{"date": "2025-01-01", "prospective": "false"}],
    )

    monkeypatch.setattr(
        uk_replay_mod,
        "load_effects_for_statute_from_archive",
        lambda _sid, _archive: [effect],
    )
    monkeypatch.setattr(
        uk_replay_mod,
        "get_affecting_act_xml_from_archive",
        lambda _aid, _archive: _AVAILABLE_XML_BYTES,
    )
    monkeypatch.setattr(
        uk_replay_mod,
        "extract_provision_element_from_bytes",
        lambda _xml, _prov, **_kwargs: None,
    )
    monkeypatch.setattr(
        uk_replay_mod,
        "compile_effect_to_ir_ops",
        lambda _effect, _el, sequence=0, **_kwargs: [],
    )

    pipeline = UKReplayPipeline(Path("."))
    lowering_rejections: list[dict[str, Any]] = []

    assert pipeline.compile_ops_for_statute(
        "ukpga/2025/36",
        archive=object(),
        lowering_rejections_out=lowering_rejections,
    ) == []
    assert lowering_rejections == []


def test_pipeline_compile_ops_falls_back_to_metadata_for_missing_affecting_xml(monkeypatch) -> None:
    effect = UKEffectRecord(
        effect_id="uk_test_pipeline_metadata_only_insert_fallback",
        effect_type="inserted",
        applied=True,
        requires_applied=False,
        modified="2024-01-01",
        affected_uri="/id/ukpga/2000/10",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="10",
        affected_provisions="s. 57(2)",
        affecting_uri="/id/uksi/2000/2040",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2000",
        affecting_number="2040",
        affecting_provisions="Sch. 2 para. 7",
        affecting_title="Missing Affecting Act",
        in_force_dates=[{"date": "2024-01-01", "prospective": "false"}],
    )

    monkeypatch.setattr(
        uk_replay_mod,
        "load_effects_for_statute_from_archive",
        lambda _sid, _archive: [effect],
    )
    monkeypatch.setattr(
        uk_replay_mod,
        "get_affecting_act_xml_from_archive",
        lambda _aid, _archive: None,
    )

    pipeline = UKReplayPipeline(Path("."))
    compiled = pipeline.compile_ops_for_statute("ukpga/2000/10", archive=object())

    assert len(compiled) == 1
    assert compiled[0].action is StructuralAction.INSERT
    assert compiled[0].target.path == (("section", "57"), ("subsection", "2"))
    assert compiled[0].payload is not None
    assert (
        compiled[0].payload.text == "[inserted by metadata source only: uk_test_pipeline_metadata_only_insert_fallback]"
    )
    assert any(
        note.startswith(_NOTE_METADATA_SOURCE_FALLBACK + "uk_test_pipeline_metadata_only_insert_fallback")
        for note in compiled[0].provenance_tags
    )


def test_pipeline_compile_ops_records_metadata_only_selection_rejection(monkeypatch) -> None:
    effect = UKEffectRecord(
        effect_id="uk_test_pipeline_metadata_only_selection",
        effect_type="repealed",
        applied=False,
        requires_applied=False,
        metadata_only=True,
        modified="2024-01-01",
        affected_uri="/id/ukpga/2000/10",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="10",
        affected_provisions="s. 57",
        affecting_uri="/id/uksi/2000/2040",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2000",
        affecting_number="2040",
        affecting_provisions="Sch. 2 para. 7",
        affecting_title="Metadata Only Affecting Act",
        in_force_dates=[{"date": "2024-01-01", "prospective": "false"}],
    )

    monkeypatch.setattr(
        uk_replay_mod,
        "load_effects_for_statute_from_archive",
        lambda _sid, _archive: [effect],
    )

    pipeline = UKReplayPipeline(Path("."))
    lowering_rejections: list[dict[str, Any]] = []
    compiled = pipeline.compile_ops_for_statute(
        "ukpga/2000/10",
        archive=object(),
        allow_metadata_only_effects=False,
        lowering_rejections_out=lowering_rejections,
    )

    assert compiled == []
    assert lowering_rejections == [
        {
            "rule_id": "uk_effect_metadata_only_selection_rejected",
            "family": "applicability_filter",
            "phase": "lowering",
            "effect_id": "uk_test_pipeline_metadata_only_selection",
            "affecting_act_id": "uksi/2000/2040",
            "affected_provisions": "s. 57",
            "affecting_provisions": "Sch. 2 para. 7",
            "effect_type": "repealed",
            "metadata_only": True,
            "reason": "UK replay regime excludes metadata-only effect rows",
            "blocking": True,
            "strict_disposition": "block",
            "quirks_disposition": "record",
        }
    ]


def test_pipeline_compile_ops_records_authority_filter_rejections(monkeypatch) -> None:
    effect = UKEffectRecord(
        effect_id="uk_test_authority_filter",
        effect_type="inserted",
        applied=True,
        requires_applied=False,
        modified="2024-01-01",
        affected_uri="/id/ukpga/2000/10",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="10",
        affected_provisions="s. 57",
        affecting_uri="/id/uksi/2000/2040",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2000",
        affecting_number="2040",
        affecting_provisions="Sch. 2 para. 7",
        affecting_title="Missing Affecting Act",
        in_force_dates=[{"date": "2024-01-01", "prospective": "false"}],
    )
    op = LegalOperation(
        op_id="uk_test_authority_filter:1",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("section", "57"),)),
        payload=IRNode(kind=IRNodeKind.SECTION, label="57", text="Inserted text."),
        source=OperationSource(statute_id="uksi/2000/2040", title="Missing Affecting Act"),
    )

    monkeypatch.setattr(
        uk_replay_mod,
        "load_effects_for_statute_from_archive",
        lambda _sid, _archive: [effect],
    )
    monkeypatch.setattr(
        uk_replay_mod,
        "get_affecting_act_xml_from_archive",
        lambda _aid, _archive: _AVAILABLE_XML_BYTES,
    )
    monkeypatch.setattr(
        uk_replay_mod,
        "extract_provision_element_from_bytes",
        lambda _xml, _prov, **_kwargs: None,
    )
    monkeypatch.setattr(
        uk_replay_mod,
        "compile_effect_to_ir_ops",
        lambda _effect, _el, sequence=0, **_kwargs: [op],
    )

    pipeline = UKReplayPipeline(Path("."))
    authority_rejections: list[dict[str, Any]] = []

    assert (
        pipeline.compile_ops_for_statute(
            "ukpga/2000/10",
            archive=object(),
            authority_mode="source_text_only",
            authority_rejections_out=authority_rejections,
        )
        == []
    )
    assert authority_rejections == [
        {
            "rule_id": "uk_effect_authority_filter_rejected",
            "family": "authority_filter",
            "phase": "lowering",
            "effect_id": "uk_test_authority_filter",
            "affecting_act_id": "uksi/2000/2040",
            "affected_provisions": "s. 57",
            "affecting_provisions": "Sch. 2 para. 7",
            "effect_type": "inserted",
            "authority_mode": "source_text_only",
            "replay_applicable": True,
            "structural_for_replay": True,
            "applied": True,
            "requires_applied": False,
            "metadata_only": False,
            "rejected_op_count": 1,
            "kept_op_count": 0,
            "rejected_authority_layers": [],
            "rejected_reasons": ["extraction_authority"],
            "rejected_reason_counts": {"extraction_authority": 1},
            "reason": "UK source-text-only authority mode rejected non-source-text replay operations",
            "blocking": True,
            "strict_disposition": "block",
            "quirks_disposition": "record",
        }
    ]


def test_pipeline_compile_ops_records_non_applicable_authority_observation(monkeypatch) -> None:
    effect = UKEffectRecord(
        effect_id="uk_test_non_applicable_authority_filter",
        effect_type="omitted",
        applied=False,
        requires_applied=True,
        modified="2026-01-01",
        affected_uri="/id/ukpga/2000/10",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="10",
        affected_provisions="s. 57",
        affecting_uri="/id/uksi/2000/2040",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2000",
        affecting_number="2040",
        affecting_provisions="Sch. 2 para. 7",
        affecting_title="Missing Affecting Act",
        in_force_dates=[{"date": "2026-01-01", "prospective": "false"}],
    )
    op = LegalOperation(
        op_id="uk_test_non_applicable_authority_filter:1",
        sequence=1,
        action=StructuralAction.REPEAL,
        target=LegalAddress(path=(("section", "57"),)),
        source=OperationSource(statute_id="uksi/2000/2040", title="Missing Affecting Act"),
    )

    monkeypatch.setattr(
        uk_replay_mod,
        "load_effects_for_statute_from_archive",
        lambda _sid, _archive: [effect],
    )
    monkeypatch.setattr(
        uk_replay_mod,
        "get_affecting_act_xml_from_archive",
        lambda _aid, _archive: _AVAILABLE_XML_BYTES,
    )
    monkeypatch.setattr(
        uk_replay_mod,
        "extract_provision_element_from_bytes",
        lambda _xml, _prov, **_kwargs: None,
    )
    monkeypatch.setattr(
        uk_replay_mod,
        "compile_effect_to_ir_ops",
        lambda _effect, _el, sequence=0, **_kwargs: [op],
    )

    pipeline = UKReplayPipeline(Path("."))
    authority_observations: list[dict[str, Any]] = []

    assert (
        pipeline.compile_ops_for_statute(
            "ukpga/2000/10",
            archive=object(),
            authority_mode="source_text_only",
            authority_rejections_out=authority_observations,
        )
        == []
    )
    assert authority_observations == [
        {
            "rule_id": "uk_effect_authority_filter_non_applicable_observed",
            "family": "authority_filter",
            "phase": "lowering",
            "effect_id": "uk_test_non_applicable_authority_filter",
            "affecting_act_id": "uksi/2000/2040",
            "affected_provisions": "s. 57",
            "affecting_provisions": "Sch. 2 para. 7",
            "effect_type": "omitted",
            "authority_mode": "source_text_only",
            "replay_applicable": False,
            "structural_for_replay": False,
            "applied": False,
            "requires_applied": True,
            "metadata_only": False,
            "rejected_op_count": 1,
            "kept_op_count": 0,
            "rejected_authority_layers": [],
            "rejected_reasons": ["extraction_authority"],
            "rejected_reason_counts": {"extraction_authority": 1},
            "reason": (
                "UK source-text-only authority mode observed non-source-text operations "
                "on a non-replay-applicable effect"
            ),
            "blocking": False,
            "strict_disposition": "record",
            "quirks_disposition": "record",
        }
    ]


def test_pipeline_compile_ops_lowers_metadata_renumber_without_source_text(
    monkeypatch,
) -> None:
    effect = UKEffectRecord(
        effect_id="uk_test_metadata_renumber_lowered",
        effect_type="s. 49 renumbered as s. 49(1)",
        applied=True,
        requires_applied=True,
        modified="2021-05-01",
        affected_uri="/id/asc/2021/1",
        affected_class="WelshNationalAssemblyAct",
        affected_year="2021",
        affected_number="1",
        affected_provisions="s. 49(1)",
        affecting_uri="/id/wsi/2021/356",
        affecting_class="WelshStatutoryInstrument",
        affecting_year="2021",
        affecting_number="356",
        affecting_provisions="reg. 5(2)",
        affecting_title="Test Renumbering Regulations",
        in_force_dates=[{"date": "2021-05-01", "prospective": "false"}],
    )

    monkeypatch.setattr(
        uk_replay_mod,
        "load_effects_for_statute_from_archive",
        lambda *_args, **_kwargs: [effect],
    )
    monkeypatch.setattr(
        uk_replay_mod,
        "get_affecting_act_xml_from_archive",
        lambda *_args, **_kwargs: None,
    )

    pipeline = UKReplayPipeline(Path("."))
    lowering_rejections: list[dict[str, Any]] = []

    ops = pipeline.compile_ops_for_statute(
        "asc/2021/1",
        archive=object(),
        lowering_rejections_out=lowering_rejections,
    )

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.RENUMBER
    assert ops[0].target.path == (("section", "49"),)
    assert ops[0].destination is not None
    assert ops[0].destination.path == (("section", "49"), ("subsection", "1"))
    assert len(lowering_rejections) == 1
    observation = lowering_rejections[0]
    assert observation["rule_id"] == "uk_effect_metadata_renumber_lowered"
    assert observation["blocking"] is False
    assert observation["strict_disposition"] == "record"


def test_pipeline_compile_ops_records_malformed_affecting_act_xml_source_diagnostic(
    monkeypatch,
) -> None:
    effect = UKEffectRecord(
        effect_id="uk_test_malformed_affecting_xml",
        effect_type="inserted",
        applied=True,
        requires_applied=False,
        modified="2024-01-01",
        affected_uri="/id/ukpga/2000/10",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="10",
        affected_provisions="s. 57",
        affecting_uri="/id/ukpga/2024/13",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2024",
        affecting_number="13",
        affecting_provisions="s. 1",
        affecting_title="Malformed Affecting Act",
        in_force_dates=[{"date": "2024-01-01", "prospective": "false"}],
    )

    monkeypatch.setattr(
        uk_replay_mod,
        "load_effects_for_statute_from_archive",
        lambda *_args, **_kwargs: [effect],
    )
    monkeypatch.setattr(
        uk_replay_mod,
        "get_affecting_act_xml_from_archive",
        lambda _aid, _archive: b"<Legislation><P1>" + (b"x" * 128),
    )
    monkeypatch.setattr(
        uk_replay_mod,
        "compile_effect_to_ir_ops",
        lambda _effect, _el, sequence=0, **_kwargs: [],
    )

    diagnostics: list[dict[str, Any]] = []
    pipeline = UKReplayPipeline(Path("."))

    assert (
        pipeline.compile_ops_for_statute(
            "ukpga/2000/10",
            archive=object(),
            effect_diagnostics_out=diagnostics,
        )
        == []
    )

    parse_diagnostics = [
        row
        for row in diagnostics
        if row.get("rule_id") == "uk_affecting_act_xml_parse_rejected"
    ]
    assert len(parse_diagnostics) == 1
    parse_diagnostic = parse_diagnostics[0]
    assert parse_diagnostic["family"] == "source_pathology"
    assert parse_diagnostic["phase"] == "parse"
    assert parse_diagnostic["effect_id"] == "uk_test_malformed_affecting_xml"
    assert parse_diagnostic["affecting_act_id"] == "ukpga/2024/13"
    assert parse_diagnostic["locator"] == "https://www.legislation.gov.uk/ukpga/2024/13/data.xml"
    assert parse_diagnostic["exception_type"] == "ParseError"
    assert parse_diagnostic["blocking"] is True
    assert parse_diagnostic["strict_disposition"] == "block"
    assert parse_diagnostic["quirks_disposition"] == "record"
    assert any(
        row.get("rule_id") == "uk_effect_source_pathology_classified"
        and row.get("blocking") is False
        for row in diagnostics
    )


def test_pipeline_compile_ops_records_too_small_affecting_act_xml(
    monkeypatch,
) -> None:
    effect = UKEffectRecord(
        effect_id="uk_test_too_small_affecting_xml",
        effect_type="inserted",
        applied=True,
        requires_applied=False,
        modified="2024-01-01",
        affected_uri="/id/ukpga/2000/10",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="10",
        affected_provisions="s. 57",
        affecting_uri="/id/ukpga/2024/13",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2024",
        affecting_number="13",
        affecting_provisions="s. 1",
        affecting_title="Too Small Affecting Act",
        in_force_dates=[{"date": "2024-01-01", "prospective": "false"}],
    )

    monkeypatch.setattr(
        uk_replay_mod,
        "load_effects_for_statute_from_archive",
        lambda *_args, **_kwargs: [effect],
    )
    monkeypatch.setattr(
        uk_replay_mod,
        "get_affecting_act_xml_from_archive",
        lambda _aid, _archive: b"<short/>",
    )
    monkeypatch.setattr(
        uk_replay_mod,
        "compile_effect_to_ir_ops",
        lambda _effect, _el, sequence=0, **_kwargs: [],
    )

    diagnostics: list[dict[str, Any]] = []
    pipeline = UKReplayPipeline(Path("."))

    assert (
        pipeline.compile_ops_for_statute(
            "ukpga/2000/10",
            archive=object(),
            effect_diagnostics_out=diagnostics,
        )
        == []
    )

    too_small_diagnostics = [
        row
        for row in diagnostics
        if row.get("rule_id") == "uk_affecting_act_xml_too_small_rejected"
    ]
    assert len(too_small_diagnostics) == 1
    diagnostic = too_small_diagnostics[0]
    assert diagnostic["family"] == "source_pathology"
    assert diagnostic["phase"] == "acquisition"
    assert diagnostic["effect_id"] == "uk_test_too_small_affecting_xml"
    assert diagnostic["affecting_act_id"] == "ukpga/2024/13"
    assert diagnostic["source_size"] == len(b"<short/>")
    assert diagnostic["blocking"] is True
    assert diagnostic["strict_disposition"] == "block"
    assert not any(
        row.get("rule_id") == "uk_affecting_act_xml_parse_rejected"
        for row in diagnostics
    )


def test_pipeline_compile_ops_does_not_require_affecting_source_for_commencement(
    monkeypatch,
) -> None:
    effect = UKEffectRecord(
        effect_id="uk_test_commencement_no_source_required",
        effect_type="coming into force",
        applied=True,
        requires_applied=True,
        modified="2024-01-01",
        affected_uri="/id/ukpga/2000/10",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="10",
        affected_provisions="s. 57",
        affecting_uri="/id/uksi/2024/13",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2024",
        affecting_number="13",
        affecting_provisions="art. 2",
        affecting_title="Commencement Order",
        in_force_dates=[{"date": "2024-01-01", "prospective": "false"}],
    )

    def fail_if_fetched(_aid, _archive):  # noqa: ANN001
        raise AssertionError("commencement rows should not fetch affecting XML")

    monkeypatch.setattr(
        uk_replay_mod,
        "load_effects_for_statute_from_archive",
        lambda *_args, **_kwargs: [effect],
    )
    monkeypatch.setattr(uk_replay_mod, "get_affecting_act_xml_from_archive", fail_if_fetched)
    monkeypatch.setattr(
        uk_replay_mod,
        "compile_effect_to_ir_ops",
        lambda _effect, _el, sequence=0, **_kwargs: [],
    )

    diagnostics: list[dict[str, Any]] = []
    lowering_rejections: list[dict[str, Any]] = []
    pipeline = UKReplayPipeline(Path("."))

    assert (
        pipeline.compile_ops_for_statute(
            "ukpga/2000/10",
            archive=object(),
            effect_diagnostics_out=diagnostics,
            lowering_rejections_out=lowering_rejections,
        )
        == []
    )
    assert lowering_rejections == []
    assert all(
        row.get("rule_id") != "uk_affecting_act_xml_missing_rejected"
        for row in diagnostics
    )
    assert any(
        row.get("rule_id") == "uk_effect_source_pathology_classified"
        and row.get("source_pathology") == "nonstructural_root_gap"
        and row.get("structural_for_replay") is False
        for row in diagnostics
    )


def test_pipeline_compile_ops_reuses_parsed_affecting_act_xml_per_act(monkeypatch) -> None:
    effect_a = UKEffectRecord(
        effect_id="uk_test_cached_xml_a",
        effect_type="inserted",
        applied=True,
        requires_applied=False,
        modified="2024-01-01",
        affected_uri="/id/ukpga/2000/10",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="10",
        affected_provisions="s. 57(2)",
        affecting_uri="/id/ukpga/2024/13",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2024",
        affecting_number="13",
        affecting_provisions="s. 1",
        affecting_title="Cached Affecting Act",
        in_force_dates=[{"date": "2024-01-01", "prospective": "false"}],
    )
    effect_b = UKEffectRecord(
        effect_id="uk_test_cached_xml_b",
        effect_type="inserted",
        applied=True,
        requires_applied=False,
        modified="2024-01-02",
        affected_uri="/id/ukpga/2000/10",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="10",
        affected_provisions="s. 57(3)",
        affecting_uri="/id/ukpga/2024/13",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2024",
        affecting_number="13",
        affecting_provisions="s. 2",
        affecting_title="Cached Affecting Act",
        in_force_dates=[{"date": "2024-01-02", "prospective": "false"}],
    )

    xml_bytes = f"""
    <Legislation xmlns="{_LEG_NS}">
      <Body>
        <P1 id="section-1"><Pnumber>1</Pnumber><Text>One</Text></P1>
        <P1 id="section-2"><Pnumber>2</Pnumber><Text>Two</Text></P1>
      </Body>
    </Legislation>
    """.encode("utf-8")

    parse_calls = {"count": 0}

    original_fromstring = uk_replay_mod.ET.fromstring

    def counted_fromstring(data):
        parse_calls["count"] += 1
        return original_fromstring(data)

    monkeypatch.setattr(
        uk_replay_mod,
        "load_effects_for_statute_from_archive",
        lambda _sid, _archive: [effect_a, effect_b],
    )
    monkeypatch.setattr(
        uk_replay_mod,
        "get_affecting_act_xml_from_archive",
        lambda _aid, _archive: xml_bytes,
    )
    monkeypatch.setattr(uk_replay_mod.ET, "fromstring", counted_fromstring)
    monkeypatch.setattr(
        uk_replay_mod,
        "compile_effect_to_ir_ops",
        lambda effect, _el, sequence=0, **_kwargs: [
            LegalOperation(
                op_id=effect.effect_id,
                sequence=sequence,
                action=StructuralAction.INSERT,
                target=LegalAddress(path=(("section", "57"),)),
                payload=IRNode(kind=IRNodeKind.SECTION, label="57"),
                source=OperationSource(statute_id=effect.affecting_act_id, title=effect.affecting_title),
            )
        ],
    )

    pipeline = UKReplayPipeline(Path("."))
    compiled = pipeline.compile_ops_for_statute("ukpga/2000/10", archive=object())

    assert len(compiled) == 2
    assert parse_calls["count"] == 1


def test_pipeline_compile_ops_selects_enacted_source_when_current_source_is_shell(
    monkeypatch,
) -> None:
    effect = UKEffectRecord(
        effect_id="uk_test_enacted_source_fallback",
        effect_type="inserted",
        applied=True,
        requires_applied=False,
        modified="2024-01-01",
        affected_uri="/id/ukpga/2000/10",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="10",
        affected_provisions="s. 57",
        affecting_uri="/id/ukpga/2024/13",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2024",
        affecting_number="13",
        affecting_provisions="s. 1",
        affecting_title="Shell Current Source Act",
        in_force_dates=[{"date": "2024-01-01", "prospective": "false"}],
    )
    current_xml = f"""
    <Legislation xmlns="{_LEG_NS}">
      <Body>
        <P1 id="section-1"><Pnumber>1</Pnumber><Text>1 . . . . . .</Text></P1>
      </Body>
    </Legislation>
    """.encode("utf-8")
    enacted_xml = f"""
    <Legislation xmlns="{_LEG_NS}">
      <Body>
        <P1 id="section-1">
          <Pnumber>1</Pnumber>
          <Text>1 Inserted provision text from the enacted affecting source.</Text>
        </P1>
      </Body>
    </Legislation>
    """.encode("utf-8")
    compile_calls: list[dict[str, str]] = []

    def fake_compile(effect_arg, extracted_el, sequence=0, **kwargs):
        compile_calls.append(
            {
                "text": " ".join(" ".join(extracted_el.itertext()).split())
                if extracted_el is not None
                else "",
                "authority": kwargs.get("source_authority_layer", ""),
            }
        )
        return [
            LegalOperation(
                op_id=effect_arg.effect_id,
                sequence=sequence,
                action=StructuralAction.INSERT,
                target=LegalAddress(path=(("section", "57"),)),
                payload=IRNode(kind=IRNodeKind.SECTION, label="57"),
                source=OperationSource(statute_id=effect_arg.affecting_act_id),
            )
        ]

    monkeypatch.setattr(
        uk_replay_mod,
        "load_effects_for_statute_from_archive",
        lambda _sid, _archive: [effect],
    )
    monkeypatch.setattr(
        uk_replay_mod,
        "get_affecting_act_xml_from_archive",
        lambda _aid, _archive: current_xml,
    )
    monkeypatch.setattr(
        uk_replay_mod,
        "get_affecting_act_enacted_xml_from_archive",
        lambda _aid, _archive: enacted_xml,
    )
    monkeypatch.setattr(uk_replay_mod, "compile_effect_to_ir_ops", fake_compile)

    diagnostics: list[dict[str, Any]] = []
    compiled = UKReplayPipeline(Path(".")).compile_ops_for_statute(
        "ukpga/2000/10",
        archive=object(),
        effect_diagnostics_out=diagnostics,
    )

    assert len(compiled) == 1
    assert compile_calls == [
        {
            "text": "1 1 Inserted provision text from the enacted affecting source.",
            "authority": "AFFECTING_ACT_ENACTED_TEXT",
        }
    ]
    assert any(
        row.get("rule_id") == "uk_affecting_act_current_shell_enacted_source_selected"
        and row.get("current_text_preview") == "1 1 . . . . . ."
        and "Inserted provision text" in str(row.get("enacted_text_preview", ""))
        for row in diagnostics
    )


def test_pipeline_compile_ops_keeps_current_shell_when_enacted_same_ref_missing(
    monkeypatch,
) -> None:
    effect = UKEffectRecord(
        effect_id="uk_test_enacted_source_no_same_ref",
        effect_type="inserted",
        applied=True,
        requires_applied=False,
        modified="2024-01-01",
        affected_uri="/id/ukpga/2000/10",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="10",
        affected_provisions="s. 57",
        affecting_uri="/id/ukpga/2024/13",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2024",
        affecting_number="13",
        affecting_provisions="s. 1",
        affecting_title="Shell Current Source Act",
        in_force_dates=[{"date": "2024-01-01", "prospective": "false"}],
    )
    current_xml = f"""
    <Legislation xmlns="{_LEG_NS}">
      <Body>
        <P1 id="section-1"><Pnumber>1</Pnumber><Text>1 . . . . . .</Text></P1>
      </Body>
    </Legislation>
    """.encode("utf-8")
    enacted_xml = f"""
    <Legislation xmlns="{_LEG_NS}">
      <Body>
        <P1 id="section-2"><Pnumber>2</Pnumber><Text>Substantive but wrong provision.</Text></P1>
      </Body>
    </Legislation>
    """.encode("utf-8")
    compile_calls: list[dict[str, str]] = []

    def fake_compile(effect_arg, extracted_el, sequence=0, **kwargs):
        compile_calls.append(
            {
                "text": " ".join(" ".join(extracted_el.itertext()).split())
                if extracted_el is not None
                else "",
                "authority": kwargs.get("source_authority_layer", ""),
            }
        )
        return [
            LegalOperation(
                op_id=effect_arg.effect_id,
                sequence=sequence,
                action=StructuralAction.INSERT,
                target=LegalAddress(path=(("section", "57"),)),
                payload=IRNode(kind=IRNodeKind.SECTION, label="57"),
                source=OperationSource(statute_id=effect_arg.affecting_act_id),
            )
        ]

    monkeypatch.setattr(
        uk_replay_mod,
        "load_effects_for_statute_from_archive",
        lambda _sid, _archive: [effect],
    )
    monkeypatch.setattr(
        uk_replay_mod,
        "get_affecting_act_xml_from_archive",
        lambda _aid, _archive: current_xml,
    )
    monkeypatch.setattr(
        uk_replay_mod,
        "get_affecting_act_enacted_xml_from_archive",
        lambda _aid, _archive: enacted_xml,
    )
    monkeypatch.setattr(uk_replay_mod, "compile_effect_to_ir_ops", fake_compile)

    diagnostics: list[dict[str, Any]] = []
    compiled = UKReplayPipeline(Path(".")).compile_ops_for_statute(
        "ukpga/2000/10",
        archive=object(),
        effect_diagnostics_out=diagnostics,
    )

    assert len(compiled) == 1
    assert compile_calls == [
        {
            "text": "1 1 . . . . . .",
            "authority": "AFFECTING_ACT_TEXT",
        }
    ]
    assert all(
        row.get("rule_id") != "uk_affecting_act_current_shell_enacted_source_selected"
        for row in diagnostics
    )


def test_replay_uk_ops_grounds_inserted_subsection_via_parent_compound_key() -> None:
    base = IRStatute(
        statute_id="ukpga/2000/23",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.PART,
                    label="2",
                    text="Part 2",
                    attrs={"eId": "part-2"},
                    children=(
                        IRNode(
                            kind=IRNodeKind.SECTION,
                            label="29",
                            text="Section 29",
                            attrs={"eId": "section-29"},
                            children=(
                                IRNode(
                                    kind=IRNodeKind.SUBSECTION,
                                    label="6",
                                    text="Existing subsection",
                                    attrs={"eId": "section-29-6"},
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    op = LegalOperation(
        op_id="uk_test_insert_29_6a",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("section", "29"), ("subsection", "6A"))),
        payload=IRNode(kind=IRNodeKind.SUBSECTION, label="6A", text="Inserted subsection."),
        source=OperationSource(statute_id="ukpga/2017/3", title="Policing and Crime Act 2017"),
    )

    replayed = replay_uk_ops(
        base,
        [op],
        eid_map={
            "part-2": "part-2",
            "section-29": "section-29",
            "body:part-2:section-29:subsection-29-6a": "section-29-6A",
        },
    )

    section = replayed.body.children[0].children[0]
    inserted = next(child for child in section.children if child.label == "6A")
    assert inserted.attrs["eId"] == "section-29-6A"


def test_executor_insert_into_existing_unnumbered_schedule_root() -> None:
    statute = IRStatute(
        statute_id="ukpga/2000/10",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=()),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="",
                text="Further provision",
                attrs={"eId": "schedule"},
                children=(),
            ),
        ),
    )
    executor: Any = UKReplayExecutor(statute)
    op = LegalOperation(
        op_id="uk_test_insert_into_unnumbered_schedule",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("schedule", ""), ("paragraph", "9"))),
        payload=IRNode(
            kind=IRNodeKind.CROSSHEADING,
            label=None,
            text="Joint inspection of courts",
            children=(IRNode(kind=IRNodeKind.SECTION, label="9", text="Inserted payload"),),
        ),
        source=OperationSource(statute_id="ukpga/2024/13", title="Amending Act"),
    )

    executor.apply_op(op)

    assert executor.statute.body.children == []
    assert len(executor.statute.supplements) == 1
    schedule = executor.statute.supplements[0]
    assert [child.kind for child in schedule.children] == [IRNodeKind.CROSSHEADING]
    assert schedule.children[0].text == "Joint inspection of courts"


def test_executor_insert_top_level_schedule_does_not_nest_under_existing_schedule() -> None:
    statute = IRStatute(
        statute_id="ukpga/2000/41",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=()),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="7",
                text="Schedule 7",
                attrs={"eId": "schedule-7"},
                children=(
                    IRNode(
                        kind=IRNodeKind.PART,
                        label="I",
                        text="Part I",
                        attrs={"eId": "schedule-7-part-I"},
                        children=(
                            IRNode(
                                kind=IRNodeKind.CROSSHEADING,
                                label=None,
                                text="Operation and interpretation of Schedule",
                                attrs={
                                    "eId": ("schedule-7-part-I-crossheading-operation-and-interpretation-of-schedule")
                                },
                                children=(),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    executor: Any = UKReplayExecutor(statute)
    op = LegalOperation(
        op_id="uk_test_insert_top_level_schedule_7a",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("schedule", "7a"),)),
        payload=IRNode(
            kind=IRNodeKind.SCHEDULE,
            label="7A",
            text="Schedule 7A",
            attrs={"eId": "schedule-7A"},
            children=(IRNode(kind=IRNodeKind.PARAGRAPH, label="1", text="Inserted paragraph"),),
        ),
        source=OperationSource(statute_id="ukpga/2001/1", title="Amending Act"),
    )

    executor.apply_op(op)

    assert [schedule.label for schedule in executor.statute.supplements] == ["7", "7A"]
    schedule_7 = executor.statute.supplements[0]
    schedule_7a = executor.statute.supplements[1]
    assert schedule_7.children[0].kind == IRNodeKind.PART
    assert [child.kind for child in schedule_7.children[0].children] == [IRNodeKind.CROSSHEADING]
    assert schedule_7a.kind == IRNodeKind.SCHEDULE
    assert schedule_7a.label == "7A"
    assert [child.label for child in schedule_7a.children] == ["1"]


def test_executor_single_segment_body_insert_reuses_nested_predecessor_parent() -> None:
    statute = IRStatute(
        statute_id="ukpga/2006/12",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.CROSSHEADING,
                    label=None,
                    text="Removal of infringing articles",
                    children=(
                        IRNode(
                            kind=IRNodeKind.P1GROUP,
                            label=None,
                            text="",
                            children=(
                                IRNode(
                                    kind=IRNodeKind.SECTION,
                                    label="15",
                                    text="Section 15 text",
                                    attrs={"eId": "section-15"},
                                ),
                                IRNode(
                                    kind=IRNodeKind.SECTION,
                                    label="16",
                                    text="Section 16 text",
                                    attrs={"eId": "section-16"},
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor: Any = UKReplayExecutor(statute)

    for label in ("15A", "16A", "16B"):
        executor.apply_op(
            LegalOperation(
                op_id=f"uk_test_insert_nested_body_{label.lower()}",
                sequence=1,
                action=StructuralAction.INSERT,
                target=LegalAddress(path=(("section", label.lower()),)),
                payload=IRNode(kind=IRNodeKind.SECTION, label=label, text=f"Inserted {label}"),
                source=OperationSource(statute_id="ukpga/2006/12", title="Amending Act"),
            )
        )

    assert [child.kind for child in executor.statute.body.children] == [IRNodeKind.CROSSHEADING]
    group = executor.statute.body.children[0].children[0]
    assert group.kind == IRNodeKind.P1GROUP
    assert [child.label for child in group.children] == ["15", "15A", "16", "16A", "16B"]


def test_executor_alpha_suffix_paragraph_insert_sorts_after_base_letter() -> None:
    statute = IRStatute(
        statute_id="asc/2021/1",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="122",
                    text="Section 122",
                    attrs={"eId": "section-122"},
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1",
                            text="Subsection 1",
                            attrs={"eId": "section-122-1"},
                            children=(
                                IRNode(kind=IRNodeKind.PARAGRAPH, label="a", text="A"),
                                IRNode(kind=IRNodeKind.PARAGRAPH, label="b", text="B"),
                                IRNode(kind=IRNodeKind.PARAGRAPH, label="c", text="C"),
                                IRNode(kind=IRNodeKind.PARAGRAPH, label="g", text="G"),
                                IRNode(kind=IRNodeKind.PARAGRAPH, label="h", text="H"),
                                IRNode(kind=IRNodeKind.PARAGRAPH, label="i", text="I"),
                                IRNode(kind=IRNodeKind.PARAGRAPH, label="za", text="ZA"),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    adjudications: list[Any] = []
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)
    for label in ("ba", "ga"):
        executor.apply_op(
            LegalOperation(
                op_id=f"uk_test_insert_section_122_1_{label}",
                sequence=1,
                action=StructuralAction.INSERT,
                target=LegalAddress(path=(("section", "122"), ("subsection", "1"), ("paragraph", label))),
                payload=IRNode(kind=IRNodeKind.PARAGRAPH, label=label, text=label.upper()),
                source=OperationSource(statute_id="wsi/2021/1349", title="Amending Regulations"),
            )
        )

    section = executor.statute.body.children[0]
    subsection = section.children[0]
    assert [child.label for child in subsection.children] == ["a", "b", "ba", "c", "g", "ga", "h", "i", "za"]
    assert [adjudication.kind for adjudication in adjudications] == []


def test_executor_roman_suffix_paragraph_insert_sorts_after_base_roman() -> None:
    statute = IRStatute(
        statute_id="asp/2002/17",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="32",
                    text="Section 32",
                    attrs={"eId": "section-32"},
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="2",
                            text="Subsection 2",
                            attrs={"eId": "section-32-2"},
                            children=(
                                IRNode(
                                    kind=IRNodeKind.PARAGRAPH,
                                    label="a",
                                    text="Paragraph a",
                                    attrs={"eId": "section-32-2-a"},
                                    children=tuple(
                                        IRNode(kind=IRNodeKind.SUBPARAGRAPH, label=label, text=label)
                                        for label in ("i", "ii", "iii", "iv", "v", "vi")
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    adjudications: list[Any] = []
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)
    for label in ("iia", "iib", "iiia"):
        executor.apply_op(
            LegalOperation(
                op_id=f"uk_test_insert_section_32_2_a_{label}",
                sequence=1,
                action=StructuralAction.INSERT,
                target=LegalAddress(
                    path=(("section", "32"), ("subsection", "2"), ("paragraph", "a"), ("subparagraph", label))
                ),
                payload=IRNode(kind=IRNodeKind.SUBPARAGRAPH, label=label, text=label),
                source=OperationSource(statute_id="asp/2007/3", title="Amending Act"),
            )
        )

    paragraph = executor.statute.body.children[0].children[0].children[0]
    assert [child.label for child in paragraph.children] == [
        "i",
        "ii",
        "iia",
        "iib",
        "iii",
        "iiia",
        "iv",
        "v",
        "vi",
    ]
    assert [adjudication.kind for adjudication in adjudications] == []


def test_executor_alpha_suffix_schedule_item_batch_preserves_alphabetic_family_order() -> None:
    statute = IRStatute(
        statute_id="asp/2003/5",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=()),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="1",
                text="",
                attrs={"eId": "schedule-1"},
                children=(
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="2",
                        text="Paragraph 2",
                        attrs={"eId": "schedule-1-paragraph-2"},
                        children=tuple(
                            IRNode(kind=IRNodeKind.ITEM, label=label, text=label)
                            for label in ("a", "b", "c", "d", "e", "f")
                        ),
                    ),
                ),
            ),
        ),
    )
    adjudications: list[Any] = []
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)
    for label in ("da", "db", "dc", "dd", "de", "df", "dg", "dh", "di", "dj", "dk", "dl", "dm"):
        executor.apply_op(
            LegalOperation(
                op_id=f"uk_test_insert_schedule_1_2_{label}",
                sequence=1,
                action=StructuralAction.INSERT,
                target=LegalAddress(path=(("schedule", "1"), ("paragraph", "2"), ("item", label))),
                payload=IRNode(kind=IRNodeKind.ITEM, label=label, text=label),
                source=OperationSource(statute_id="asp/2009/9", title="Amending Act"),
            )
        )

    paragraph = executor.statute.supplements[0].children[0]
    assert [child.label for child in paragraph.children] == [
        "a",
        "b",
        "c",
        "d",
        "da",
        "db",
        "dc",
        "dd",
        "de",
        "df",
        "dg",
        "dh",
        "di",
        "dj",
        "dk",
        "dl",
        "dm",
        "e",
        "f",
    ]
    assert [adjudication.kind for adjudication in adjudications] == []


def test_ground_ids_schedule_nested_subparagraph_uses_plain_suffix() -> None:
    statute = IRStatute(
        statute_id="ukpga/2000/10",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=()),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="",
                text="Further provision",
                attrs={"eId": "schedule"},
                children=(
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="6",
                        text="Paragraph 6",
                        attrs={"eId": "schedule-paragraph-6"},
                        children=(IRNode(kind=IRNodeKind.PARAGRAPH, label="2", text="Inserted subparagraph"),),
                    ),
                ),
            ),
        ),
    )

    executor: Any = UKReplayExecutor(
        statute,
        eid_map={"keep_schedule": "schedule", "keep_para6": "schedule-paragraph-6"},
        text_map={},
    )
    executor.ground_ids()

    subparagraph = executor.statute.supplements[0].children[0].children[0]
    assert subparagraph.attrs["eId"] == "schedule-paragraph-6-2"


def test_ground_ids_schedule_nested_item_uses_plain_suffix() -> None:
    statute = IRStatute(
        statute_id="ukpga/2000/10",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=()),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="",
                text="Further provision",
                attrs={"eId": "schedule"},
                children=(
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="7",
                        text="Paragraph 7",
                        attrs={"eId": "schedule-paragraph-7"},
                        children=(
                            IRNode(
                                kind=IRNodeKind.PARAGRAPH,
                                label="3",
                                text="Subparagraph 3",
                                attrs={"eId": "schedule-paragraph-7-3"},
                                children=(IRNode(kind=IRNodeKind.ITEM, label="b", text="Inserted item"),),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )

    executor: Any = UKReplayExecutor(
        statute,
        eid_map={
            "keep_schedule": "schedule",
            "keep_para7": "schedule-paragraph-7",
            "keep_para73": "schedule-paragraph-7-3",
        },
        text_map={},
    )
    executor.ground_ids()

    item = executor.statute.supplements[0].children[0].children[0].children[0]
    assert item.attrs["eId"] == "schedule-paragraph-7-3-b"


def test_ground_ids_schedule_part_uses_kind_prefix_in_local_fallback() -> None:
    statute = IRStatute(
        statute_id="ukpga/2000/41",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=()),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="3",
                text="Schedule 3",
                attrs={"eId": "schedule-3"},
                children=(IRNode(kind=IRNodeKind.PART, label="II", text="Part II", children=()),),
            ),
        ),
    )

    executor: Any = UKReplayExecutor(
        statute,
        eid_map={"keep_schedule": "schedule-3"},
        text_map={},
    )
    executor.ground_ids()

    part = executor.statute.supplements[0].children[0]
    assert part.attrs["eId"] == "schedule-3-part-2"


def test_ground_ids_schedule_crossheading_does_not_steal_unrelated_bare_flat_id() -> None:
    statute = IRStatute(
        statute_id="ukpga/2000/41",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=()),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="3",
                text="Schedule 3",
                attrs={"eId": "schedule-3"},
                children=(
                    IRNode(
                        kind=IRNodeKind.PART,
                        label="II",
                        text="Part II",
                        children=(
                            IRNode(
                                kind=IRNodeKind.CROSSHEADING,
                                label=None,
                                text="Government of Wales Act 1998 (c. 38)",
                                children=(),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )

    executor: Any = UKReplayExecutor(
        statute,
        eid_map={
            "keep_schedule": "schedule-3",
            "crossheading-government-of-wales-act-1998-c-38": (
                "schedule-21-crossheading-government-of-wales-act-1998-c38"
            ),
        },
        text_map={},
    )
    executor.ground_ids()

    part = executor.statute.supplements[0].children[0]
    crossheading = part.children[0]
    assert part.attrs["eId"] == "schedule-3-part-2"
    assert "eId" not in crossheading.attrs


def test_ground_ids_section_roman_subparagraph_local_fallback_preserves_roman_suffix() -> None:
    statute = IRStatute(
        statute_id="ukpga/2000/41",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="88",
                    text="Section 88",
                    attrs={"eId": "section-88"},
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="3C",
                            text="Subsection 3C",
                            attrs={"eId": "section-88-3c"},
                            children=(
                                IRNode(
                                    kind=IRNodeKind.PARAGRAPH,
                                    label="b",
                                    text="Paragraph b",
                                    attrs={"eId": "section-88-3c-b"},
                                    children=(
                                        IRNode(
                                            kind=IRNodeKind.SUBPARAGRAPH,
                                            label="ii",
                                            text="Subparagraph ii",
                                            children=(),
                                        ),
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )

    executor: Any = UKReplayExecutor(
        statute,
        eid_map={
            "keep_section": "section-88",
            "keep_subsection": "section-88-3c",
            "keep_paragraph": "section-88-3c-b",
        },
        text_map={},
    )
    executor.ground_ids()

    subparagraph = executor.statute.body.children[0].children[0].children[0].children[0]
    assert subparagraph.attrs["eId"] == "section-88-3c-b-ii"


def test_ground_ids_schedule_roman_item_local_fallback_preserves_roman_suffix() -> None:
    statute = IRStatute(
        statute_id="ukpga/2000/41",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=()),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="7A",
                text="Schedule 7A",
                attrs={"eId": "schedule-7a"},
                children=(
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="9",
                        text="Paragraph 9",
                        attrs={"eId": "schedule-7a-paragraph-9"},
                        children=(
                            IRNode(
                                kind=IRNodeKind.PARAGRAPH,
                                label="2",
                                text="Subparagraph 2",
                                attrs={"eId": "schedule-7a-paragraph-9-2"},
                                children=(
                                    IRNode(
                                        kind=IRNodeKind.ITEM,
                                        label="b",
                                        text="Item b",
                                        attrs={"eId": "schedule-7a-paragraph-9-2-b"},
                                        children=(
                                            IRNode(
                                                kind=IRNodeKind.ITEM,
                                                label="ii",
                                                text="Item ii",
                                                children=(),
                                            ),
                                        ),
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )

    executor: Any = UKReplayExecutor(
        statute,
        eid_map={
            "keep_schedule": "schedule-7a",
            "keep_paragraph": "schedule-7a-paragraph-9",
            "keep_subparagraph": "schedule-7a-paragraph-9-2",
            "keep_item": "schedule-7a-paragraph-9-2-b",
        },
        text_map={},
    )
    executor.ground_ids()

    item = executor.statute.supplements[0].children[0].children[0].children[0].children[0]
    assert item.attrs["eId"] == "schedule-7a-paragraph-9-2-b-ii"


def test_executor_target_eid_preserves_lettered_schedule_item_suffix() -> None:
    statute = IRStatute(
        statute_id="asc/2021/1",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=()),
        supplements=(),
    )
    executor: Any = UKReplayExecutor(statute)

    assert (
        executor._derive_target_eid(
            LegalAddress(path=(("schedule", "5"), ("paragraph", "9"), ("item", "c")))
        )
        == "schedule-5-paragraph-9-c"
    )


def test_executor_target_eid_preserves_lettered_body_item_suffix() -> None:
    statute = IRStatute(
        statute_id="ukpga/2000/1",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=()),
        supplements=(),
    )
    executor: Any = UKReplayExecutor(statute)

    assert (
        executor._derive_target_eid(
            LegalAddress(path=(("section", "88"), ("subsection", "3C"), ("paragraph", "c")))
        )
        == "section-88-3c-c"
    )


def test_executor_replace_preserves_existing_id_attr_as_eid() -> None:
    statute = IRStatute(
        statute_id="asc/2021/1",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=()),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="5",
                text="",
                attrs={"id": "schedule-5"},
                children=(
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="9",
                        text="",
                        attrs={"id": "schedule-5-paragraph-9"},
                        children=(
                            IRNode(
                                kind=IRNodeKind.ITEM,
                                label="c",
                                text="old text",
                                attrs={"id": "schedule-5-paragraph-9-c"},
                                children=(),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="uk_test_replace_preserves_id",
        sequence=0,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("schedule", "5"), ("paragraph", "9"), ("item", "c"))),
        payload=IRNode(kind=IRNodeKind.ITEM, label="c", text="new text", children=()),
        source=OperationSource(statute_id="wsi/2021/1349"),
    )

    replayed = replay_uk_ops(statute, [op])

    replaced = replayed.supplements[0].children[0].children[0]
    assert replaced.text == "new text"
    assert replaced.attrs["eId"] == "schedule-5-paragraph-9-c"


def test_compile_synthesizes_local_eids_for_inserted_schedule_payload_descendants() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P1 xmlns="{_LEG_NS}">
          <BlockAmendment>
            <Schedule>
              <Number>10A</Number>
              <Title>Schedule 10A</Title>
              <ScheduleBody>
                <P1>
                  <Pnumber>1</Pnumber>
                  <Text>Paragraph 1</Text>
                  <P2>
                    <Pnumber>(a)</Pnumber>
                    <Text>Item a</Text>
                  </P2>
                </P1>
                <Pblock>
                  <Title>Unlabelled heading</Title>
                  <P1>
                    <Pnumber>2</Pnumber>
                    <Text>Paragraph 2</Text>
                  </P1>
                </Pblock>
              </ScheduleBody>
            </Schedule>
          </BlockAmendment>
        </P1>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_insert_schedule_10a",
        effect_type="inserted",
        applied=True,
        requires_applied=False,
        modified="2022-07-15",
        affected_uri="/id/asc/2021/1",
        affected_class="WelshPublicGeneralAct",
        affected_year="2021",
        affected_number="1",
        affected_provisions="Sch. 10A",
        affecting_uri="/id/wsi/2022/797",
        affecting_class="WelshStatutoryInstrument",
        affecting_year="2022",
        affecting_number="797",
        affecting_provisions="reg. 5",
        affecting_title="Amending Regulations",
    )
    lowering_records: list[dict[str, Any]] = []

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0, lowering_rejections_out=lowering_records)

    assert len(ops) == 1
    schedule = ops[0].payload
    assert schedule is not None
    paragraph = schedule.children[0]
    item = paragraph.children[0]
    crossheading = schedule.children[1]
    crossheading_paragraph = crossheading.children[0]
    assert schedule.attrs["eId"] == "schedule-10a"
    assert paragraph.attrs["eId"] == "schedule-10a-paragraph-1"
    assert item.attrs["eId"] == "schedule-10a-paragraph-1-a"
    assert "eId" not in crossheading.attrs
    assert crossheading_paragraph.attrs["eId"] == "schedule-10a-paragraph-2"
    assert lowering_records[0]["rule_id"] == "uk_whole_schedule_payload_descendant_eid_synthesis"
    assert lowering_records[0]["phase"] == "payload_normalization"
    assert lowering_records[0]["blocking"] is False
    assert lowering_records[0]["synthesized_count"] == 3


def test_compile_skips_duplicate_synthetic_schedule_descendant_eids() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P1 xmlns="{_LEG_NS}">
          <BlockAmendment>
            <Schedule>
              <Number>5A</Number>
              <Title>Schedule 5A</Title>
              <ScheduleBody>
                <P1>
                  <Pnumber>4</Pnumber>
                  <Text>Paragraph 4</Text>
                  <P2>
                    <Pnumber>(a)</Pnumber>
                    <Text>First field</Text>
                  </P2>
                  <P2>
                    <Pnumber>(b)</Pnumber>
                    <Text>Second field</Text>
                  </P2>
                  <P2>
                    <Pnumber>(a)</Pnumber>
                    <Text>Repeated form field</Text>
                  </P2>
                </P1>
              </ScheduleBody>
            </Schedule>
          </BlockAmendment>
        </P1>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_insert_schedule_5a_duplicate_labels",
        effect_type="inserted",
        applied=True,
        requires_applied=False,
        modified="2022-07-15",
        affected_uri="/id/asc/2021/1",
        affected_class="WelshPublicGeneralAct",
        affected_year="2021",
        affected_number="1",
        affected_provisions="Sch. 5A",
        affecting_uri="/id/wsi/2022/797",
        affecting_class="WelshStatutoryInstrument",
        affecting_year="2022",
        affecting_number="797",
        affecting_provisions="reg. 5",
        affecting_title="Amending Regulations",
    )
    lowering_records: list[dict[str, Any]] = []

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0, lowering_rejections_out=lowering_records)

    assert len(ops) == 1
    schedule = ops[0].payload
    assert schedule is not None
    paragraph = schedule.children[0]
    first_item, second_item, repeated_item = paragraph.children
    assert paragraph.attrs["eId"] == "schedule-5a-paragraph-4"
    assert first_item.attrs["eId"] == "schedule-5a-paragraph-4-a"
    assert second_item.attrs["eId"] == "schedule-5a-paragraph-4-b"
    assert "eId" not in repeated_item.attrs
    assert lowering_records[0]["rule_id"] == "uk_whole_schedule_payload_descendant_eid_synthesis"
    assert lowering_records[0]["synthesized_count"] == 3
    assert lowering_records[0]["skipped_duplicate_count"] == 1


def test_compile_preserves_source_eids_when_synthesizing_inserted_schedule_descendants() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P1 xmlns="{_LEG_NS}">
          <BlockAmendment>
            <Schedule>
              <Number>11</Number>
              <Title>Schedule 11</Title>
              <ScheduleBody>
                <P1 eId="source-schedule-11-paragraph-1">
                  <Pnumber>1</Pnumber>
                  <Text>Paragraph 1</Text>
                  <P2>
                    <Pnumber>(b)</Pnumber>
                    <Text>Item b</Text>
                  </P2>
                </P1>
              </ScheduleBody>
            </Schedule>
          </BlockAmendment>
        </P1>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_insert_schedule_11",
        effect_type="inserted",
        applied=True,
        requires_applied=False,
        modified="2022-07-15",
        affected_uri="/id/asc/2021/1",
        affected_class="WelshPublicGeneralAct",
        affected_year="2021",
        affected_number="1",
        affected_provisions="Sch. 11",
        affecting_uri="/id/wsi/2022/797",
        affecting_class="WelshStatutoryInstrument",
        affecting_year="2022",
        affecting_number="797",
        affecting_provisions="reg. 5",
        affecting_title="Amending Regulations",
    )
    lowering_records: list[dict[str, Any]] = []

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0, lowering_rejections_out=lowering_records)

    assert len(ops) == 1
    assert ops[0].payload is not None
    paragraph = ops[0].payload.children[0]
    item = paragraph.children[0]
    assert paragraph.attrs["eId"] == "source-schedule-11-paragraph-1"
    assert item.attrs["eId"] == "source-schedule-11-paragraph-1-b"
    assert lowering_records[0]["sample"] == [
        {
            "kind": "subparagraph",
            "label": "(b)",
            "parent_eid": "source-schedule-11-paragraph-1",
            "after_eid": "source-schedule-11-paragraph-1-b",
        }
    ]


def test_compile_can_strict_block_inserted_schedule_payload_descendant_eid_synthesis() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P1 xmlns="{_LEG_NS}">
          <BlockAmendment>
            <Schedule>
              <Number>12</Number>
              <Title>Schedule 12</Title>
              <ScheduleBody>
                <P1>
                  <Pnumber>1</Pnumber>
                  <Text>Paragraph 1</Text>
                </P1>
              </ScheduleBody>
            </Schedule>
          </BlockAmendment>
        </P1>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_insert_schedule_12",
        effect_type="inserted",
        applied=True,
        requires_applied=False,
        modified="2022-07-15",
        affected_uri="/id/asc/2021/1",
        affected_class="WelshPublicGeneralAct",
        affected_year="2021",
        affected_number="1",
        affected_provisions="Sch. 12",
        affecting_uri="/id/wsi/2022/797",
        affecting_class="WelshStatutoryInstrument",
        affecting_year="2022",
        affecting_number="797",
        affecting_provisions="reg. 5",
        affecting_title="Amending Regulations",
    )
    lowering_records: list[dict[str, Any]] = []

    ops = compile_effect_to_ir_ops(
        effect,
        extracted_el,
        sequence=0,
        lowering_rejections_out=lowering_records,
        allow_payload_identity_synthesis=False,
    )

    assert len(ops) == 1
    assert ops[0].payload is not None
    assert ops[0].payload.attrs["eId"] == "schedule-12"
    assert "eId" not in ops[0].payload.children[0].attrs
    assert lowering_records == [
        {
            "rule_id": "uk_whole_schedule_payload_descendant_eid_synthesis",
            "family": "payload_identity_normalization",
            "phase": "payload_normalization",
            "effect_id": "uk_test_insert_schedule_12",
            "affecting_act_id": "wsi/2022/797",
            "affected_provisions": "Sch. 12",
            "affecting_provisions": "reg. 5",
            "effect_type": "inserted",
            "target": "schedule:12",
            "reason": (
                "Whole-schedule payload has descendants without source EIDs; "
                "strict lowering did not synthesize local descendant identity"
            ),
            "blocking": True,
            "strict_disposition": "block",
            "quirks_disposition": "record",
        }
    ]


def test_compile_keeps_target_matching_structural_substitution_as_replace() -> None:
    extracted_el = ET.fromstring(
        f"""
        <P1 xmlns="{_LEG_NS}">
          <Pnumber>8</Pnumber>
          <P1para>
            <Text>For paragraph 117, substitute-</Text>
            <BlockAmendment>
              <P1>
                <Pnumber>117</Pnumber>
                <P1para>
                  <Text>In section 31-</Text>
                  <P3>
                    <Pnumber>a</Pnumber>
                    <P3para>
                      <Text>in subsection (2), for "28, 28B, 29, 44AC and 44D" substitute "28 and 44AC";</Text>
                    </P3para>
                  </P3>
                  <P3>
                    <Pnumber>b</Pnumber>
                    <P3para>
                      <Text>in subsection (4), for "sections 28 and 29" substitute "section 28".</Text>
                    </P3para>
                  </P3>
                </P1para>
              </P1>
            </BlockAmendment>
          </P1para>
        </P1>
        """
    )
    effect = UKEffectRecord(
        effect_id="uk_test_replace_schedule_paragraph_117",
        effect_type="substituted",
        applied=True,
        requires_applied=False,
        modified="2024-10-30",
        affected_uri="/id/asc/2023/3",
        affected_class="WelshPublicGeneralAct",
        affected_year="2023",
        affected_number="3",
        affected_provisions="Sch. 13 para. 117",
        affecting_uri="/id/wsi/2024/1061",
        affecting_class="WelshStatutoryInstrument",
        affecting_year="2024",
        affecting_number="1061",
        affecting_provisions="reg. 8",
        affecting_title="Amending Regulations",
    )

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 1
    assert ops[0].action == StructuralAction.REPLACE
    assert ops[0].text_patch is None
    assert ops[0].payload is not None
    assert ops[0].payload.kind == IRNodeKind.PARAGRAPH
    assert ops[0].payload.label == "117"
    assert ops[0].payload.text == "In section 31-"
    assert [child.label for child in ops[0].payload.children] == ["a", "b"]


def test_executor_inserts_subsections_in_label_order() -> None:
    statute = IRStatute(
        statute_id="ukpga/2000/26",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="105",
                    text="",
                    children=(
                        IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="one"),
                        IRNode(kind=IRNodeKind.SUBSECTION, label="2", text="two"),
                        IRNode(kind=IRNodeKind.SUBSECTION, label="3", text="three"),
                        IRNode(kind=IRNodeKind.SUBSECTION, label="4", text="four"),
                        IRNode(kind=IRNodeKind.SUBSECTION, label="5", text="five"),
                        IRNode(kind=IRNodeKind.SUBSECTION, label="6", text="six"),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor: Any = UKReplayExecutor(statute)

    for label in ("4A", "1A", "3A"):
        executor.apply_op(
            LegalOperation(
                op_id=f"uk_insert_{label}",
                sequence=1,
                action=StructuralAction.INSERT,
                target=LegalAddress(path=(("section", "105"), ("subsection", label.lower()))),
                payload=IRNode(kind=IRNodeKind.SUBSECTION, label=label, text=f"subsection {label}"),
                source=OperationSource(statute_id="ukpga/2011/5", title="Amending Act"),
            )
        )

    section = executor.statute.body.children[0]
    assert [child.label for child in section.children] == [
        "1",
        "1A",
        "2",
        "3",
        "3A",
        "4",
        "4A",
        "5",
        "6",
    ]


def test_executor_inserts_bare_chapter_before_numbered_chapters() -> None:
    statute = IRStatute(
        statute_id="ukpga/2000/26",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.PART,
                    label="6",
                    text="",
                    children=(
                        IRNode(kind=IRNodeKind.CHAPTER, label="CHAPTER 3", text="chapter 3"),
                        IRNode(kind=IRNodeKind.CHAPTER, label="CHAPTER 4", text="chapter 4"),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor: Any = UKReplayExecutor(statute)

    executor.apply_op(
        LegalOperation(
            op_id="uk_insert_bare_chapter",
            sequence=1,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("part", "6"), ("chapter", "chapter"))),
            payload=IRNode(kind=IRNodeKind.CHAPTER, label="CHAPTER", text="chapter"),
            source=OperationSource(statute_id="ukpga/2011/5", title="Amending Act"),
        )
    )

    part = executor.statute.body.children[0]
    assert [child.label for child in part.children] == [
        "CHAPTER",
        "CHAPTER 3",
        "CHAPTER 4",
    ]


def test_executor_matches_numeric_paragraph_as_malformed_subsection_target() -> None:
    statute = IRStatute(
        statute_id="ukpga/2000/23",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="45",
                    text="",
                    attrs={"eId": "section-45"},
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="2",
                            text="",
                            attrs={"eId": "section-45-2"},
                            children=(
                                IRNode(
                                    kind=IRNodeKind.PARAGRAPH,
                                    label="3",
                                    text="Where an authorisation was granted...",
                                    attrs={"eId": "section-45-2-3"},
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor: Any = UKReplayExecutor(statute)
    op = LegalOperation(
        op_id="uk_test_text_replace_malformed_subsection",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=(("section", "45"), ("subsection", "3"))),
        text_patch=_replace_patch("granted", "renewed"),
        source=OperationSource(statute_id="ukpga/2021/4", title="Amending Act"),
    )

    executor.apply_op(op)

    malformed = executor.statute.body.children[0].children[0].children[0]
    assert malformed.text == "Where an authorisation was renewed..."


def test_executor_matches_compound_subsection_against_8_then_paragraph_a() -> None:
    statute = IRStatute(
        statute_id="ukpga/2000/22",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="45",
                    text="",
                    attrs={"eId": "section-45"},
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="8",
                            text="",
                            attrs={"eId": "section-45-8"},
                            children=(
                                IRNode(
                                    kind=IRNodeKind.PARAGRAPH,
                                    label="a",
                                    text="The authority may recover costs.",
                                    attrs={"eId": "section-45-8-a"},
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor: Any = UKReplayExecutor(statute)
    op = LegalOperation(
        op_id="uk_test_text_replace_compound_subsection_45_8a",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=(("section", "45"), ("subsection", "8A"))),
        text_patch=_replace_patch("recover costs", "recover reasonable costs"),
        source=OperationSource(statute_id="ukpga/2011/20", title="Amending Act"),
    )

    executor.apply_op(op)

    malformed = executor.statute.body.children[0].children[0].children[0]
    assert malformed.text == "The authority may recover reasonable costs."


def test_executor_finds_unlabeled_schedule_root_for_schedule_1_target() -> None:
    statute = IRStatute(
        statute_id="ukpga/2010/14",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=()),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="",
                text="",
                attrs={"eId": "schedule"},
                children=(IRNode(kind=IRNodeKind.P1GROUP, label=None, text="Entry text", attrs={"eId": "schedule-1"}),),
            ),
        ),
    )
    executor: Any = UKReplayExecutor(statute)

    node, parent, idx = executor._find_node_by_target(
        LegalAddress(path=(("schedule", "1"),)),
    )

    assert node is not None
    assert node.kind == IRNodeKind.SCHEDULE
    assert parent is None
    assert idx == 0


def test_executor_finds_schedule_descendant_when_schedule_label_is_omitted() -> None:
    statute = IRStatute(
        statute_id="ukpga/2010/14",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=()),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="",
                text="",
                attrs={"eId": "schedule"},
                children=(
                    IRNode(
                        kind=IRNodeKind.P1GROUP, label=None, text="Visiting committees", attrs={"eId": "schedule-1"}
                    ),
                ),
            ),
        ),
    )
    executor: Any = UKReplayExecutor(statute)

    node, parent, idx = executor._find_node_by_target(
        LegalAddress(path=(("schedule", ""), ("paragraph", "1"))),
    )

    assert node is not None
    assert node.kind == IRNodeKind.P1GROUP
    assert node.text == "Visiting committees"
    assert parent is not None
    assert idx == 0


def test_executor_prefers_exact_schedule_eid_over_ordinal_p1group_fallback() -> None:
    statute = IRStatute(
        statute_id="asp/2001/10",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=()),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="3",
                text="",
                attrs={"eId": "schedule-3"},
                children=(
                    IRNode(
                        kind=IRNodeKind.P1GROUP,
                        label=None,
                        text="First wrapper",
                        children=(
                            IRNode(
                                kind=IRNodeKind.PARAGRAPH,
                                label="2",
                                text="",
                                attrs={"eId": "schedule-3-paragraph-2"},
                                children=(
                                    IRNode(
                                        kind=IRNodeKind.SUBPARAGRAPH,
                                        label="2",
                                        text="within 6 months",
                                        attrs={"eId": "schedule-3-paragraph-2-2"},
                                    ),
                                ),
                            ),
                        ),
                    ),
                    IRNode(
                        kind=IRNodeKind.P1GROUP,
                        label=None,
                        text="Order of succession",
                        children=(
                            IRNode(
                                kind=IRNodeKind.PARAGRAPH,
                                label="5",
                                text="",
                                attrs={"eId": "schedule-3-paragraph-5"},
                                children=(
                                    IRNode(
                                        kind=IRNodeKind.SUBPARAGRAPH,
                                        label="2",
                                        text="wrong ordinal fallback target",
                                        attrs={"eId": "schedule-3-paragraph-5-2"},
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    adjudications: list[Any] = []
    executor: Any = UKReplayExecutor(statute, adjudications_out=adjudications)
    op = LegalOperation(
        op_id="uk_test_schedule_exact_eid_before_ordinal_fallback",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=(("schedule", "3"), ("paragraph", "2"), ("subparagraph", "2"))),
        text_patch=_replace_patch("6", "6 12"),
        source=OperationSource(statute_id="asp/2014/14", title="Amending Act"),
    )

    executor.apply_op(op)

    correct = executor.statute.supplements[0].children[0].children[0].children[0]
    ordinal_fallback = executor.statute.supplements[0].children[1].children[0].children[0]
    assert correct.text == "within 6 12 months"
    assert ordinal_fallback.text == "wrong ordinal fallback target"
    assert [adjudication.kind for adjudication in adjudications] == []


def test_executor_matches_compound_subsection_item_carrier_against_2b_then_paragraph_d() -> None:
    statute = IRStatute(
        statute_id="ukpga/2000/22",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="55",
                    text="",
                    attrs={"eId": "section-55"},
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="2",
                            text="",
                            attrs={"eId": "section-55-2"},
                            children=(
                                IRNode(
                                    kind=IRNodeKind.ITEM,
                                    label="b",
                                    text="",
                                    attrs={"eId": "section-55-2-b"},
                                    children=(
                                        IRNode(
                                            kind=IRNodeKind.PARAGRAPH,
                                            label="d",
                                            text="The authority may recover costs.",
                                            attrs={"eId": "section-55-2-b-d"},
                                        ),
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor: Any = UKReplayExecutor(statute)
    op = LegalOperation(
        op_id="uk_test_text_replace_compound_subsection_2b_item_d",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=(("section", "55"), ("subsection", "2b"), ("paragraph", "d"))),
        text_patch=_replace_patch("recover costs", "recover reasonable costs"),
        source=OperationSource(statute_id="ukpga/2011/20", title="Amending Act"),
    )

    executor.apply_op(op)

    paragraph = executor.statute.body.children[0].children[0].children[0].children[0]
    assert paragraph.text == "The authority may recover reasonable costs."


def test_executor_matches_schedule_table_paragraph_against_table_kind() -> None:
    statute = IRStatute(
        statute_id="ukpga/2000/22",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=()),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="2",
                text="",
                attrs={"eId": "schedule-2"},
                children=(
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="table",
                        text="",
                        attrs={"eId": "schedule-2-table"},
                        children=(
                            IRNode(
                                kind=IRNodeKind.SUBPARAGRAPH,
                                label="2",
                                text="The authority may recover costs.",
                                attrs={"eId": "schedule-2-table-2"},
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    executor: Any = UKReplayExecutor(statute)
    op = LegalOperation(
        op_id="uk_test_text_replace_schedule_table_2",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=(("schedule", "2"), ("paragraph", "table"), ("subparagraph", "2"))),
        text_patch=_replace_patch("recover costs", "recover reasonable costs"),
        source=OperationSource(statute_id="ukpga/2011/20", title="Amending Act"),
    )

    executor.apply_op(op)

    table_row = executor.statute.supplements[0].children[0].children[0]
    assert table_row.text == "The authority may recover reasonable costs."


def test_executor_reports_empty_schedule_shape_gap_instead_of_target_not_found() -> None:
    statute = IRStatute(
        statute_id="ukpga/2000/22",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=()),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="1",
                text="",
                attrs={"eId": "schedule-1"},
                children=(),
            ),
        ),
    )
    executor: Any = UKReplayExecutor(statute, adjudications_out=[])
    op = LegalOperation(
        op_id="uk_test_empty_schedule_shape_gap",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=(("schedule", "1"), ("paragraph", "table"))),
        text_patch=_replace_patch("landlord", "tenant"),
        source=OperationSource(statute_id="ukpga/2011/20", title="Amending Act"),
    )

    executor.apply_op(op)

    assert executor.adjudications_out is not None
    assert len(executor.adjudications_out) == 1
    assert executor.adjudications_out[0].kind == "uk_replay_empty_schedule_shape_gap"


def test_executor_does_not_report_preexisting_invariant_as_replay_violation() -> None:
    statute = IRStatute(
        statute_id="asp/2010/3",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="14",
                    text="",
                    attrs={"eId": "section-14"},
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="4",
                            text="",
                            attrs={"eId": "section-14-4"},
                            children=(
                                IRNode(
                                    kind=IRNodeKind.PARAGRAPH,
                                    label="a",
                                    text="First paragraph text.",
                                    attrs={"eId": "section-14-4-a-1"},
                                ),
                                IRNode(
                                    kind=IRNodeKind.PARAGRAPH,
                                    label="a",
                                    text="Second paragraph text.",
                                    attrs={"eId": "section-14-4-a-2"},
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    adjudications: list = []
    executor: Any = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_preexisting_invariant_not_reblamed",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "14"), ("subsection", "4"), ("paragraph", "a"))),
            text_patch=_replace_patch("First paragraph", "Updated paragraph"),
            source=OperationSource(statute_id="uk_test", title="Test Source"),
        )
    )

    assert [adj.kind for adj in adjudications] == []


def test_executor_sequence_match_does_not_alias_section_1_1_to_section_11() -> None:
    statute = IRStatute(
        statute_id="ukpga/2000/22",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="1",
                    text="",
                    attrs={"eId": "section-1"},
                    children=(),
                ),
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="11",
                    text="",
                    attrs={"eId": "section-11"},
                    children=(),
                ),
            ),
        ),
        supplements=(),
    )
    executor: Any = UKReplayExecutor(statute)
    executor.apply_op(
        LegalOperation(
            op_id="uk_test_insert_section_1_1_a_vi",
            sequence=1,
            action=StructuralAction.INSERT,
            target=LegalAddress(
                path=(
                    ("section", "1"),
                    ("subsection", "1"),
                    ("paragraph", "a"),
                    ("subparagraph", "vi"),
                )
            ),
            payload=IRNode(kind=IRNodeKind.PARAGRAPH, label="a", text="vi inserted text"),
            source=OperationSource(statute_id="ukpga/2000/22", title="Amending Act"),
        )
    )

    section_1, section_11 = executor.statute.body.children
    assert section_1.children == []
    assert section_11.children == []


def test_executor_refuses_impossible_body_root_fallback_for_descendant_insert() -> None:
    statute = IRStatute(
        statute_id="ukpga/2000/21",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=()),
        supplements=(),
    )
    executor: Any = UKReplayExecutor(statute)

    executor.apply_op(
        LegalOperation(
            op_id="uk_missing_parent_item_fallback",
            sequence=1,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("section", "113a"), ("subsection", "9"), ("paragraph", "f"))),
            payload=IRNode(kind=IRNodeKind.ITEM, label="2", text="Inserted item"),
            source=OperationSource(statute_id="ukpga/2005/18", title="Amending Act"),
        )
    )

    assert executor.statute.body.children == []

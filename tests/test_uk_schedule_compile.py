from __future__ import annotations

import json
from pathlib import Path
import xml.etree.ElementTree as ET
from typing import Any

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
from lawvm.core.semantic_types import IRNodeKind, TextPatchKindEnum
from lawvm.uk_legislation.uk_amendment_replay import (
    UKEffectRecord,
    UKReplayPipeline,
    UKReplayExecutor,
    _order_schedule_materialization_ops,
    _NOTE_METADATA_SOURCE_FALLBACK,
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
    parse_effects_from_metadata,
)


def replay_uk_ops(base, ops, **kwargs):
    return UKReplayPipeline(Path(".")).apply_ops(base, ops, **kwargs)


def _replace_patch(match: str, replacement: str, occurrence: int = 0) -> TextPatchSpec:
    return TextPatchSpec(
        kind=TextPatchKindEnum.REPLACE,
        selector=TextSelector(match_text=match, occurrence=occurrence),
        replacement=replacement,
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

    ops = compile_effect_to_ir_ops(effect, extracted_el, sequence=0)

    assert len(ops) == 1
    assert ops[0].payload is not None
    assert ops[0].payload.kind == IRNodeKind.SCHEDULE
    assert ops[0].payload.label == "2"
    assert [child.kind for child in ops[0].payload.children] == [IRNodeKind.PARAGRAPH]


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
    assert ops[0].text_patch.selector.match_text == "TEXT_FROM__TO_END"
    assert ops[0].text_patch.replacement == "(subject to section 33A)"
    assert ops[0].text_patch is not None


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

    assert [op.action.value for op in ops] == ["replace", "replace"]
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
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].target.path == (("schedule", "13"), ("paragraph", "117"))
    assert ops[0].text_patch is not None
    assert ops[0].text_patch.selector.match_text == "28, 28B, 29, 44AC and 44D"
    assert ops[0].text_patch.replacement == "28 and 44AC"
    assert ops[0].text_patch is not None
    assert any(note.startswith("fragment_substitution:") for note in ops[0].provenance_tags)


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
        effect_type="substituted",
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
        lambda _aid, _archive: b"<xml/>",
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

    assert pipeline.compile_ops_for_statute(
        "ukpga/2001/11",
        archive=object(),
        lowering_rejections_out=lowering_rejections,
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

    monkeypatch.setattr(
        uk_replay_mod,
        "load_effects_for_statute_from_archive",
        lambda _sid, _archive: [effect],
    )
    monkeypatch.setattr(
        uk_replay_mod,
        "get_affecting_act_xml_from_archive",
        lambda _aid, _archive: b"<xml/>",
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
        lambda _aid, _archive: b"<xml/>",
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
        lambda _aid, _archive: b"<xml/>",
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
            "rule_id": "uk_effect_nonstructural_unsupported_no_ops_rejected",
            "family": "lowering_filter",
            "phase": "lowering",
            "effect_id": "uk_test_nonstructural_modified_no_ops",
            "affecting_act_id": "uksi/2000/2040",
            "affected_provisions": "s. 57",
            "affecting_provisions": "Sch. 2 para. 7",
            "effect_type": "modified",
            "reason": "UK applicable nonstructural effect row is not replay-supported and lowered to no replay operations",
            "blocking": True,
            "strict_disposition": "block",
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
        lambda _aid, _archive: b"<xml/>",
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
        lambda _aid, _archive: b"<xml/>",
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
            "authority_mode": "source_text_only",
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

from __future__ import annotations

from lawvm.uk_legislation.source_state import (
    UKStatuteXmlContentStatus,
    UKSourceStatus,
    classify_uk_statute_xml_content,
    classify_uk_source_blob,
    uk_multiple_choice_candidate_data_urls,
    is_uk_affecting_act_xml_source_diagnostic,
    is_uk_affecting_act_xml_source_observation,
    uk_affecting_act_article_schedule_payload_source_extracted,
    uk_affecting_act_block_amendment_payload_descendant_ref_rejection,
    uk_affecting_act_compound_payload_only_block_amendment_selected,
    uk_affecting_act_current_shell_enacted_source_selected,
    uk_affecting_act_enacted_schedule_table_row_source_extracted,
    uk_affecting_act_missing_current_enacted_source_selected,
    uk_affecting_act_single_amendment_child_source_selected,
    uk_affecting_act_single_unnumbered_schedule_context_ignored,
    uk_affecting_act_xml_too_small_rejection,
    uk_enacted_blob_replay_base_usability,
    uk_root_is_metadata_only_stub,
    uk_source_state_wire_tuple,
)
from lawvm.core.xml_parse import parse_corpus_xml


def test_uk_source_state_classifies_absent_too_small_and_available() -> None:
    absent = classify_uk_source_blob(None)
    assert absent.source_state_status is UKSourceStatus.ABSENT
    assert absent.size == 0
    assert absent.missing is True
    assert absent.available is False

    too_small = classify_uk_source_blob(b"<short/>")
    assert too_small.source_state_status is UKSourceStatus.TOO_SMALL
    assert too_small.size == len(b"<short/>")
    assert too_small.missing is True

    available = classify_uk_source_blob(b"x" * 100)
    assert available.source_state_status is UKSourceStatus.AVAILABLE
    assert available.size == 100
    assert available.available is True
    assert available.missing is False


def test_uk_source_state_legacy_tuple_preserves_cli_wire_values() -> None:
    assert uk_source_state_wire_tuple(None) == ("absent", 0)
    assert uk_source_state_wire_tuple(b"") == ("too_small", 0)
    assert uk_source_state_wire_tuple(b"HTTP 300 Multiple Choices") == (
        "multiple_choices",
        25,
    )
    assert uk_source_state_wire_tuple(b"x" * 100) == ("available", 100)


def test_uk_statute_xml_content_classifies_multiple_choices_html() -> None:
    blob = b"""<div xmlns="http://www.w3.org/1999/xhtml" id="layout2">
  <div id="title"><h1 id="pageTitle">Multiple Choices</h1></div>
  <div id="content">
    <p>The link that you've followed could mean either of the following:</p>
    <ul>
      <li><a href="/ukpga/Eliz2/3-4/18/enacted">Army Act 1955 (repealed)</a></li>
      <li><a href="/ukpga/Eliz2/4-5/18/enacted">Aliens' Employment Act 1955</a></li>
    </ul>
  </div>
</div>"""

    state = classify_uk_statute_xml_content(blob)

    assert state.xml_content_status is UKStatuteXmlContentStatus.MULTIPLE_CHOICES
    assert state.usable_as_replay_base is False
    assert state.to_dict()["multiple_choice_candidates"] == [
        {
            "href": "/ukpga/Eliz2/3-4/18/enacted",
            "title": "Army Act 1955 (repealed)",
        },
        {
            "href": "/ukpga/Eliz2/4-5/18/enacted",
            "title": "Aliens' Employment Act 1955",
        },
    ]


def test_uk_statute_xml_content_classifies_multiple_choices_any_variant() -> None:
    blob = b"""<html><body>
    <h1>Multiple Choices</h1>
    <p>The link that you've followed could mean any of the following:</p>
    <a href="/ukpga/Geo5/12-13/3">Consolidated Fund (No. 2) Act 1922</a>
    <a href="/ukpga/Geo5/13/3">Appropriation Act 1922</a>
    <a href="/ukpga/Geo5Sess2/13/3">Appropriation (Session 2) Act 1922</a>
    </body></html>"""

    state = classify_uk_statute_xml_content(blob)

    assert state.xml_content_status is UKStatuteXmlContentStatus.MULTIPLE_CHOICES
    assert uk_multiple_choice_candidate_data_urls(blob) == (
        "https://www.legislation.gov.uk/ukpga/Geo5/12-13/3/data.xml",
        "https://www.legislation.gov.uk/ukpga/Geo5/12-13/3/enacted/data.xml",
        "https://www.legislation.gov.uk/ukpga/Geo5/13/3/data.xml",
        "https://www.legislation.gov.uk/ukpga/Geo5/13/3/enacted/data.xml",
        "https://www.legislation.gov.uk/ukpga/Geo5Sess2/13/3/data.xml",
        "https://www.legislation.gov.uk/ukpga/Geo5Sess2/13/3/enacted/data.xml",
    )


def test_uk_source_blob_classifies_multiple_choices_after_long_html_head() -> None:
    blob = (
        b"<!DOCTYPE html><html><head>"
        + (b"<meta name='x' content='y'/>" * 400)
        + b"</head><body><h1>Multiple Choices</h1>"
        + b"<p>The link that you've followed could mean either of the following:</p>"
        + b"<a href='/ukpga/Eliz2/3-4/18/enacted'>Army Act 1955</a>"
        + b"</body></html>"
    )

    assert classify_uk_source_blob(blob).source_state_status is UKSourceStatus.MULTIPLE_CHOICES


def test_uk_multiple_choice_candidate_data_urls_filters_non_candidate_links() -> None:
    blob = b"""<div id="content">
    <h1>Multiple Choices</h1>
    <p>The link that you've followed could mean either of the following:</p>
    <a href="/search">Advanced Search</a>
    <a href="/ukpga/2025-03-20T17:32:43Z/1851-08-01T00:00:00Z">Bad timestamp link</a>
    <a href="/ukpga/Eliz2/3-4/18/enacted">Army Act 1955 (repealed)</a>
    <a href="https://www.legislation.gov.uk/ukpga/Eliz2/4-5/18/enacted">Aliens</a>
    </div>"""

    assert uk_multiple_choice_candidate_data_urls(blob) == (
        "https://www.legislation.gov.uk/ukpga/Eliz2/3-4/18/enacted/data.xml",
        "https://www.legislation.gov.uk/ukpga/Eliz2/3-4/18/data.xml",
        "https://www.legislation.gov.uk/ukpga/Eliz2/4-5/18/enacted/data.xml",
        "https://www.legislation.gov.uk/ukpga/Eliz2/4-5/18/data.xml",
    )


def test_uk_statute_xml_content_classifies_metadata_only_enacted_envelope() -> None:
    blob = b"""<?xml version="1.0"?>
<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation"
    NumberOfProvisions="0">
  <ukm:Metadata xmlns:ukm="http://www.legislation.gov.uk/namespaces/metadata"/>
</Legislation>"""

    state = classify_uk_statute_xml_content(blob)

    assert state.xml_content_status is UKStatuteXmlContentStatus.METADATA_ONLY
    assert state.number_of_provisions == "0"
    assert state.has_body is False
    assert state.has_schedules is False
    assert state.usable_as_replay_base is False
    assert state.to_dict()["xml_content_status"] == "metadata_only"


def test_uk_enacted_blob_replay_base_usability_rejects_metadata_only() -> None:
    metadata_only = b"""<?xml version="1.0"?>
<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation"
    NumberOfProvisions="0">
  <ukm:Metadata xmlns:ukm="http://www.legislation.gov.uk/namespaces/metadata"/>
</Legislation>"""

    usable, status = uk_enacted_blob_replay_base_usability(metadata_only)

    # A metadata-only enacted envelope is well over the size floor (so the
    # size/multiple-choices wire gate calls it "available") but must NOT be used
    # as a replay base: it parses into an empty IR tree that manufactures
    # spurious self-consistency findings.
    assert uk_source_state_wire_tuple(metadata_only)[0] == "available"
    assert usable is False
    assert status == "metadata_only"


def test_uk_root_metadata_only_stub_detected() -> None:
    # A PDF-only affecting act's /data.xml is a NumberOfProvisions="0" metadata
    # envelope: well over the size floor (so the wire gate calls it "available")
    # but structure-aware detection on the parsed root must flag it as a
    # metadata-only stub so the missing-payload lowering gate can TYPE the gap.
    stub = b"""<?xml version="1.0"?>
<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation"
    NumberOfProvisions="0">
  <ukm:Metadata xmlns:ukm="http://www.legislation.gov.uk/namespaces/metadata"/>
</Legislation>"""
    assert uk_source_state_wire_tuple(stub)[0] == "available"
    assert uk_root_is_metadata_only_stub(parse_corpus_xml(stub)) is True


def test_uk_root_metadata_only_stub_false_for_body() -> None:
    with_body = b"""<?xml version="1.0"?>
<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation"
    NumberOfProvisions="1">
  <Body><P1 id="section-1"><Pnumber>1</Pnumber><P1para>Text.</P1para></P1></Body>
</Legislation>"""
    # A real body is not a stub; absent/too-small carry their own upstream typing.
    assert uk_root_is_metadata_only_stub(parse_corpus_xml(with_body)) is False


def test_uk_enacted_blob_replay_base_usability_accepts_body() -> None:
    with_body = b"""<?xml version="1.0"?>
<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation"
    NumberOfProvisions="1">
  <Body><P1 id="section-1"><Pnumber>1</Pnumber><P1para>Text.</P1para></P1></Body>
</Legislation>"""

    usable, status = uk_enacted_blob_replay_base_usability(with_body)

    assert usable is True
    assert status == "available"


def test_uk_statute_xml_content_available_when_body_present() -> None:
    blob = b"""<?xml version="1.0"?>
<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation"
    NumberOfProvisions="1">
  <Body><P1 id="section-1"><Pnumber>1</Pnumber><P1para>Text.</P1para></P1></Body>
</Legislation>"""

    state = classify_uk_statute_xml_content(blob)

    assert state.xml_content_status is UKStatuteXmlContentStatus.AVAILABLE
    assert state.number_of_provisions == "1"
    assert state.has_body is True
    assert state.usable_as_replay_base is True


def test_uk_statute_xml_content_ignores_comments_when_scanning_shape() -> None:
    blob = b"""<?xml version="1.0"?>
<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation"
    NumberOfProvisions="1">
  <!-- publisher comment -->
  <Body><P1 id="section-1"><Pnumber>1</Pnumber><P1para>Text.</P1para></P1></Body>
</Legislation>"""

    state = classify_uk_statute_xml_content(blob)

    assert state.xml_content_status is UKStatuteXmlContentStatus.AVAILABLE
    assert state.has_body is True


def test_uk_statute_xml_content_records_parse_error() -> None:
    state = classify_uk_statute_xml_content(b"<Legislation>" + b"x" * 100)

    assert state.xml_content_status is UKStatuteXmlContentStatus.PARSE_ERROR
    assert state.usable_as_replay_base is False
    assert state.parse_error


def test_affecting_act_xml_too_small_rejection_is_typed_source_diagnostic() -> None:
    rejection = uk_affecting_act_xml_too_small_rejection(
        effect_id="eff-1",
        affecting_act_id="ukpga/2025/1",
        locator="https://www.legislation.gov.uk/ukpga/2025/1/data.xml",
        source_size=8,
    )

    assert rejection["rule_id"] == "uk_affecting_act_xml_too_small_rejected"
    assert rejection["family"] == "source_pathology"
    assert rejection["phase"] == "acquisition"
    assert rejection["source_size"] == 8
    assert rejection["blocking"] is True
    assert rejection["strict_disposition"] == "block"
    assert is_uk_affecting_act_xml_source_observation(rejection) is True
    assert is_uk_affecting_act_xml_source_diagnostic(rejection) is True


def test_affecting_act_xml_source_observation_includes_nonblocking_records() -> None:
    observation = {
        "rule_id": "uk_affecting_act_xml_cached_recorded",
        "phase": "acquisition",
        "blocking": False,
        "strict_disposition": "record",
    }

    assert is_uk_affecting_act_xml_source_observation(observation) is True
    assert is_uk_affecting_act_xml_source_diagnostic(observation) is True


def test_block_amendment_payload_descendant_rejection_is_typed_source_diagnostic() -> None:
    rejection = uk_affecting_act_block_amendment_payload_descendant_ref_rejection(
        effect_id="eff-1",
        affecting_act_id="ukpga/2022/32",
        affecting_provisions="s. 175(2)(b)",
        locator="https://www.legislation.gov.uk/ukpga/2022/32/data.xml",
        authority_layer="AFFECTING_ACT_TEXT",
        extracted_tag="P3",
        extracted_label="b",
        extracted_text_preview="b require the offender to do anything described in the order.",
        amendment_container_tag="BlockAmendment",
        source_instruction_ancestor_tag="P3",
        source_instruction_ancestor_id="section-175-2-a",
        source_instruction_ancestor_label="a",
        source_instruction_ancestor_text_preview="a for subsection (1) substitute...",
    )

    assert rejection["rule_id"] == "uk_affecting_act_block_amendment_payload_descendant_ref_rejected"
    assert rejection["family"] == "source_pathology"
    assert rejection["phase"] == "extraction"
    assert rejection["source_instruction_ancestor_tag"] == "P3"
    assert rejection["source_instruction_ancestor_id"] == "section-175-2-a"
    assert rejection["source_instruction_ancestor_label"] == "a"
    assert rejection["blocking"] is True
    assert rejection["strict_disposition"] == "block"
    assert is_uk_affecting_act_xml_source_observation(rejection) is True
    assert is_uk_affecting_act_xml_source_diagnostic(rejection) is True


def test_article_schedule_payload_source_observation_is_typed_source_diagnostic() -> None:
    observation = uk_affecting_act_article_schedule_payload_source_extracted(
        effect_id="eff-1",
        affecting_act_id="uksi/2003/3076",
        affecting_provisions="art. 2 Sch.",
        locator="https://www.legislation.gov.uk/uksi/2003/3076/data.xml",
        authority_layer="AFFECTING_ACT_TEXT",
        article_ref="art. 2",
        article_element_id="article-2",
        schedule_element_id="schedule",
        article_text_preview="For Part 1 of Schedule 3A, substitute the text set out in the Schedule.",
    )

    assert observation["rule_id"] == "uk_affecting_act_article_schedule_payload_source_extracted"
    assert observation["family"] == "source_lane_selection"
    assert observation["phase"] == "extraction"
    assert observation["blocking"] is False
    assert observation["strict_disposition"] == "record"
    assert observation["selected_source_lane"] == "attached_schedule_payload"
    assert observation["selected_source_locator"] == (
        "https://www.legislation.gov.uk/uksi/2003/3076/data.xml#schedule"
    )
    assert observation["source_lane_attempts"] == (
        {
            "lane": "article_source_context",
            "lane_attempt_status": "context_selected_not_payload",
            "locator": "https://www.legislation.gov.uk/uksi/2003/3076/data.xml#article-2",
            "article_ref": "art. 2",
            "article_text_preview": "For Part 1 of Schedule 3A, substitute the text set out in the Schedule.",
        },
        {
            "lane": "attached_schedule_payload",
            "lane_attempt_status": "selected",
            "locator": "https://www.legislation.gov.uk/uksi/2003/3076/data.xml#schedule",
            "schedule_element_id": "schedule",
        },
    )
    assert is_uk_affecting_act_xml_source_observation(observation) is True
    assert is_uk_affecting_act_xml_source_diagnostic(observation) is True


def test_single_unnumbered_schedule_context_observation_is_typed_source_diagnostic() -> None:
    observation = uk_affecting_act_single_unnumbered_schedule_context_ignored(
        effect_id="eff-1",
        affecting_act_id="ssi/2006/536",
        affecting_provisions="Sch. 1 para. 8",
        locator="https://www.legislation.gov.uk/ssi/2006/536/data.xml",
        authority_layer="AFFECTING_ACT_TEXT",
        requested_schedule_label="1",
        normalized_affecting_provisions="Sch. para. 8",
        schedule_element_id="schedule",
        source_instruction_id="schedule-paragraph-8",
        extracted_element_id="",
    )

    assert observation["rule_id"] == "uk_affecting_act_single_unnumbered_schedule_context_ignored"
    assert observation["family"] == "target_resolution_recovery"
    assert observation["phase"] == "extraction"
    assert observation["blocking"] is False
    assert observation["strict_disposition"] == "record"
    assert observation["requested_schedule_label"] == "1"
    assert observation["normalized_affecting_provisions"] == "Sch. para. 8"
    assert observation["source_instruction_id"] == "schedule-paragraph-8"
    target_resolution = observation["target_resolution"]
    assert target_resolution["target_resolution_status"] == "recovered"
    assert target_resolution["source_target"] == "Sch. 1 para. 8"
    assert target_resolution["selected_target"] == "Sch. para. 8"
    assert target_resolution["scope_confidence"] == "explicit_source_with_context"
    assert is_uk_affecting_act_xml_source_observation(observation) is True
    assert is_uk_affecting_act_xml_source_diagnostic(observation) is True


def test_single_amendment_child_source_selection_uses_shared_source_lane_evidence() -> None:
    observation = uk_affecting_act_single_amendment_child_source_selected(
        effect_id="eff-1",
        affecting_act_id="uksi/2003/3076",
        affecting_provisions="art. 2",
        locator="https://www.legislation.gov.uk/uksi/2003/3076/enacted/data.xml",
        authority_layer="AFFECTING_ACT_ENACTED_TEXT",
        source_container_id="article-2",
        selected_child_id="article-2-2",
        selected_child_label="2",
        selected_child_text_preview="except in Scotland",
    )

    assert observation["rule_id"] == "uk_affecting_act_single_amendment_child_source_selected"
    assert observation["selected_source_lane"] == "single_amendment_child_payload"
    assert observation["selected_source_locator"] == (
        "https://www.legislation.gov.uk/uksi/2003/3076/enacted/data.xml#article-2-2"
    )
    assert [attempt["lane_attempt_status"] for attempt in observation["source_lane_attempts"]] == [
        "context_selected_not_payload",
        "selected",
    ]
    assert observation["source_container_id"] == "article-2"
    assert observation["selected_child_id"] == "article-2-2"


def test_enacted_schedule_table_row_source_selection_uses_shared_source_lane_evidence() -> None:
    observation = uk_affecting_act_enacted_schedule_table_row_source_extracted(
        effect_id="eff-1",
        affecting_act_id="asp/2004/3",
        affected_provisions="sch. 1 para. 32B",
        affecting_provisions="Sch. 1",
        locator="https://www.legislation.gov.uk/asp/2004/3/enacted/data.xml",
        authority_layer="AFFECTING_ACT_ENACTED_TEXT",
        schedule_label="1",
        part_label="4",
        target_label="32b",
        source_row_text="32B NHS Health Scotland",
    )

    assert observation["rule_id"] == "uk_affecting_act_enacted_schedule_table_row_source_extracted"
    assert observation["selected_source_lane"] == "enacted_schedule_table_row_payload"
    assert observation["source_lane_attempts"][0]["lane_attempt_status"] == "selected"
    assert observation["source_lane_attempts"][0]["target_label"] == "32b"
    assert observation["part_label"] == "4"
    assert observation["source_row_text"] == "32B NHS Health Scotland"


def test_compound_payload_only_source_selection_uses_shared_source_lane_evidence() -> None:
    observation = uk_affecting_act_compound_payload_only_block_amendment_selected(
        effect_id="eff-1",
        affecting_act_id="ukpga/2023/1",
        affecting_provisions="Sch. 2 Pt. 1 para. 1(2)(a)",
        locator="https://www.legislation.gov.uk/ukpga/2023/1/data.xml",
        authority_layer="AFFECTING_ACT_TEXT",
        source_row_tag="P3",
        source_row_id="schedule-2-paragraph-1-2-a",
        source_row_label="a",
        payload_container_tag="BlockAmendment",
        payload_text_preview="the 1996 Act means...",
    )

    assert observation["rule_id"] == "uk_affecting_act_compound_payload_only_block_amendment_selected"
    assert observation["selected_source_lane"] == "block_amendment_payload_container"
    assert [attempt["lane"] for attempt in observation["source_lane_attempts"]] == [
        "numbered_source_row_context",
        "block_amendment_payload_container",
    ]
    assert observation["payload_container_tag"] == "BlockAmendment"
    assert observation["source_row_id"] == "schedule-2-paragraph-1-2-a"


def test_current_shell_enacted_source_selection_uses_shared_source_lane_evidence() -> None:
    observation = uk_affecting_act_current_shell_enacted_source_selected(
        effect_id="eff-1",
        affecting_act_id="ukpga/2022/32",
        affecting_provisions="s. 175(2)(b)",
        current_locator="current.xml",
        enacted_locator="enacted.xml",
        current_source_size=123,
        enacted_source_size=456,
        current_text_preview="...",
        enacted_text_preview="substantive amendment text",
    )

    assert observation["rule_id"] == "uk_affecting_act_current_shell_enacted_source_selected"
    assert observation["family"] == "source_lane_selection"
    assert observation["selected_source_lane"] == "enacted_xml"
    assert observation["selected_source_locator"] == "enacted.xml"
    assert observation["source_lane_attempts"] == (
        {
            "lane": "current_xml",
            "lane_attempt_status": "rejected_non_substantive_shell",
            "locator": "current.xml",
            "source_size": 123,
            "text_preview": "...",
        },
        {
            "lane": "enacted_xml",
            "lane_attempt_status": "selected",
            "locator": "enacted.xml",
            "source_size": 456,
            "text_preview": "substantive amendment text",
        },
    )
    assert observation["current_locator"] == "current.xml"
    assert observation["enacted_locator"] == "enacted.xml"
    assert observation["blocking"] is False


def test_missing_current_enacted_source_selection_uses_shared_source_lane_evidence() -> None:
    observation = uk_affecting_act_missing_current_enacted_source_selected(
        effect_id="eff-1",
        affecting_act_id="ukpga/2022/32",
        affecting_provisions="s. 175(2)(b)",
        current_locator="current.xml",
        enacted_locator="enacted.xml",
        current_source_size=0,
        enacted_source_size=456,
        enacted_text_preview="substantive amendment text",
    )

    assert observation["rule_id"] == "uk_affecting_act_missing_current_enacted_source_selected"
    assert observation["family"] == "source_lane_selection"
    assert observation["selected_source_lane"] == "enacted_xml"
    assert observation["source_lane_attempts"][0]["lane_attempt_status"] == "missing_same_provision_source"
    assert observation["source_lane_attempts"][1]["lane_attempt_status"] == "selected"
    assert observation["blocking"] is False

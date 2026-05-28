from __future__ import annotations

from lawvm.uk_legislation.source_state import (
    UKSourceStatus,
    classify_uk_source_blob,
    classify_uk_source_blob_legacy,
    is_uk_affecting_act_xml_source_diagnostic,
    is_uk_affecting_act_xml_source_observation,
    uk_affecting_act_article_schedule_payload_source_extracted,
    uk_affecting_act_block_amendment_payload_descendant_ref_rejection,
    uk_affecting_act_compound_payload_only_block_amendment_selected,
    uk_affecting_act_current_shell_enacted_source_selected,
    uk_affecting_act_enacted_schedule_table_row_source_extracted,
    uk_affecting_act_missing_current_enacted_source_selected,
    uk_affecting_act_single_amendment_child_source_selected,
    uk_affecting_act_xml_too_small_rejection,
    uk_source_state_wire_tuple,
)


def test_uk_source_state_classifies_absent_too_small_and_available() -> None:
    absent = classify_uk_source_blob(None)
    assert absent.status is UKSourceStatus.ABSENT
    assert absent.size == 0
    assert absent.missing is True
    assert absent.available is False

    too_small = classify_uk_source_blob(b"<short/>")
    assert too_small.status is UKSourceStatus.TOO_SMALL
    assert too_small.size == len(b"<short/>")
    assert too_small.missing is True

    available = classify_uk_source_blob(b"x" * 100)
    assert available.status is UKSourceStatus.AVAILABLE
    assert available.size == 100
    assert available.available is True
    assert available.missing is False


def test_uk_source_state_legacy_tuple_preserves_cli_wire_values() -> None:
    assert uk_source_state_wire_tuple(None) == ("absent", 0)
    assert uk_source_state_wire_tuple(b"") == ("too_small", 0)
    assert uk_source_state_wire_tuple(b"x" * 100) == ("available", 100)
    assert classify_uk_source_blob_legacy(None) == ("absent", 0)
    assert classify_uk_source_blob_legacy(b"") == ("too_small", 0)
    assert classify_uk_source_blob_legacy(b"x" * 100) == ("available", 100)


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
            "status": "context_selected_not_payload",
            "locator": "https://www.legislation.gov.uk/uksi/2003/3076/data.xml#article-2",
            "article_ref": "art. 2",
            "article_text_preview": "For Part 1 of Schedule 3A, substitute the text set out in the Schedule.",
        },
        {
            "lane": "attached_schedule_payload",
            "status": "selected",
            "locator": "https://www.legislation.gov.uk/uksi/2003/3076/data.xml#schedule",
            "schedule_element_id": "schedule",
        },
    )
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
    assert [attempt["status"] for attempt in observation["source_lane_attempts"]] == [
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
    assert observation["source_lane_attempts"][0]["status"] == "selected"
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
            "status": "rejected_non_substantive_shell",
            "locator": "current.xml",
            "source_size": 123,
            "text_preview": "...",
        },
        {
            "lane": "enacted_xml",
            "status": "selected",
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
    assert observation["source_lane_attempts"][0]["status"] == "missing_same_provision_source"
    assert observation["source_lane_attempts"][1]["status"] == "selected"
    assert observation["blocking"] is False

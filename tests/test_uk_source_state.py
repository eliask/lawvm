from __future__ import annotations

from lawvm.uk_legislation.source_state import (
    UKSourceStatus,
    classify_uk_source_blob,
    classify_uk_source_blob_legacy,
    is_uk_affecting_act_xml_source_diagnostic,
    is_uk_affecting_act_xml_source_observation,
    uk_affecting_act_block_amendment_payload_descendant_ref_rejection,
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
    )

    assert rejection["rule_id"] == "uk_affecting_act_block_amendment_payload_descendant_ref_rejected"
    assert rejection["family"] == "source_pathology"
    assert rejection["phase"] == "extraction"
    assert rejection["blocking"] is True
    assert rejection["strict_disposition"] == "block"
    assert is_uk_affecting_act_xml_source_observation(rejection) is True
    assert is_uk_affecting_act_xml_source_diagnostic(rejection) is True

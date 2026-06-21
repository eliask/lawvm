from __future__ import annotations

from lawvm.core.compile_records import (
    BlockingDisposition,
    CompileRecord,
    is_blocking_compile_record,
)


def test_compile_record_carrier_defaults_to_blocking() -> None:
    record = CompileRecord()
    assert record.is_blocking is True
    assert record.disposition is BlockingDisposition.BLOCK
    assert is_blocking_compile_record(record) is True


def test_compile_record_explicit_blocking_wins_over_disposition() -> None:
    blocking = CompileRecord(blocking=True, strict_disposition="record")
    nonblocking = CompileRecord(blocking=False, strict_disposition="block")
    assert blocking.is_blocking is True
    assert nonblocking.is_blocking is False
    assert is_blocking_compile_record(blocking) is True
    assert is_blocking_compile_record(nonblocking) is False


def test_compile_record_record_disposition_opts_out_when_blocking_unspecified() -> None:
    record_disposition = CompileRecord(strict_disposition="record")
    block_disposition = CompileRecord(strict_disposition="block")
    assert record_disposition.is_blocking is False
    assert record_disposition.disposition is BlockingDisposition.RECORD
    assert block_disposition.is_blocking is True
    assert block_disposition.disposition is BlockingDisposition.BLOCK


def test_from_mapping_matches_legacy_dict_predicate() -> None:
    rows: tuple[dict[str, object], ...] = (
        {"rule_id": "legacy_rejection"},
        {"rule_id": "observation", "blocking": False},
        {"rule_id": "rejection", "blocking": True},
        {"rule_id": "typed_observation", "strict_disposition": "record"},
        {"rule_id": "typed_rejection", "strict_disposition": "block"},
        {"rule_id": "mixed", "blocking": False, "strict_disposition": "block"},
    )
    for row in rows:
        carrier = CompileRecord.from_mapping(row)
        # Typed carrier and back-compat mapping path agree on the authority verdict.
        assert is_blocking_compile_record(carrier) is is_blocking_compile_record(row)


def test_from_mapping_preserves_payload_in_extra_without_losing_authority_fields() -> None:
    row = {
        "rule_id": "uk_effect_repeal_table_structural_repeal",
        "blocking": False,
        "strict_disposition": "record",
        "target_ref": "schedule:1/paragraph:2",
    }
    carrier = CompileRecord.from_mapping(row)
    assert carrier.blocking is False
    assert carrier.strict_disposition == "record"
    assert carrier.extra is not None
    assert carrier.extra["rule_id"] == "uk_effect_repeal_table_structural_repeal"
    assert carrier.extra["target_ref"] == "schedule:1/paragraph:2"
    # Authority-relevant fields are not duplicated into extra.
    assert "blocking" not in carrier.extra
    assert "strict_disposition" not in carrier.extra


def test_from_mapping_absent_blocking_is_unspecified() -> None:
    carrier = CompileRecord.from_mapping({"rule_id": "legacy"})
    assert carrier.blocking is None
    assert carrier.strict_disposition is None
    assert carrier.extra is not None
    assert carrier.extra["rule_id"] == "legacy"

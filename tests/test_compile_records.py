from __future__ import annotations

from lawvm.core.compile_records import is_blocking_compile_record


def test_is_blocking_compile_record_defaults_legacy_records_to_blocking() -> None:
    assert is_blocking_compile_record({"rule_id": "legacy_rejection"}) is True


def test_is_blocking_compile_record_respects_explicit_blocking() -> None:
    assert is_blocking_compile_record({"rule_id": "observation", "blocking": False}) is False
    assert is_blocking_compile_record({"rule_id": "rejection", "blocking": True}) is True


def test_is_blocking_compile_record_treats_record_disposition_as_nonblocking() -> None:
    assert (
        is_blocking_compile_record(
            {"rule_id": "typed_observation", "strict_disposition": "record"}
        )
        is False
    )
    assert (
        is_blocking_compile_record(
            {"rule_id": "typed_rejection", "strict_disposition": "block"}
        )
        is True
    )


def test_is_blocking_compile_record_explicit_blocking_wins_over_disposition() -> None:
    assert (
        is_blocking_compile_record(
            {
                "rule_id": "explicit_rejection",
                "blocking": True,
                "strict_disposition": "record",
            }
        )
        is True
    )
    assert (
        is_blocking_compile_record(
            {
                "rule_id": "explicit_observation",
                "blocking": False,
                "strict_disposition": "block",
            }
        )
        is False
    )

from __future__ import annotations

from lawvm.tools.uk_misses import (
    _blocking_compile_records,
    _rejection_rule_histogram,
)


def test_uk_misses_splits_blocking_compile_records() -> None:
    rows = [
        {
            "rule_id": "uk_effect_lowering_no_supported_action_rejected",
            "blocking": True,
            "affected_provisions": "s. 1",
            "effect_type": "inserted",
        },
        {
            "rule_id": "uk_effect_undated_applied_si_commencement_date",
            "blocking": False,
            "affected_provisions": "s. 2",
            "effect_type": "repealed",
        },
        {
            "rule_id": "legacy_blocking_without_flag",
            "affected_provisions": "s. 3",
            "effect_type": "substituted",
        },
    ]

    all_histogram = _rejection_rule_histogram(rows)
    blocking_histogram = _rejection_rule_histogram(_blocking_compile_records(rows))

    assert {rule_id: count for rule_id, count, _ in all_histogram} == {
        "legacy_blocking_without_flag": 1,
        "uk_effect_lowering_no_supported_action_rejected": 1,
        "uk_effect_undated_applied_si_commencement_date": 1,
    }
    assert {rule_id: count for rule_id, count, _ in blocking_histogram} == {
        "legacy_blocking_without_flag": 1,
        "uk_effect_lowering_no_supported_action_rejected": 1,
    }

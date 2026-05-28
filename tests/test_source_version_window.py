from __future__ import annotations

from dataclasses import dataclass

from lawvm.core.source_version_window import (
    SOURCE_VERSION_CHANGE_WINDOW_TRUTH_CLAIM,
    SOURCE_VERSION_DATE_WINDOW_TRUTH_CLAIM,
    iso_date_prefix,
    select_source_version_change_window,
    select_source_version_date_window,
)


@dataclass(frozen=True)
class _Witness:
    witness_id: str
    date: str


def test_source_version_date_window_brackets_requested_date_without_replay_claim() -> None:
    witnesses = (
        _Witness("preferred-same-day", "2025-04-05B"),
        _Witness("older", "2024-01-01"),
        _Witness("newer", "2026-04-05"),
        _Witness("ignored", "not-a-date"),
    )

    window = select_source_version_date_window(
        witnesses,
        requested_version_date="2025-06-01T00:00:00Z",
        version_date=lambda witness: witness.date,
    )

    assert window.truth_claim == SOURCE_VERSION_DATE_WINDOW_TRUTH_CLAIM
    assert window.replay_claims is False
    assert window.requested_version_date == "2025-06-01"
    assert window.on_or_before == witnesses[0]
    assert window.on_or_after == witnesses[2]


def test_source_version_change_window_uses_strict_before_witness() -> None:
    witnesses = (
        _Witness("same-day", "2025-06-01"),
        _Witness("before", "2025-04-05"),
        _Witness("after", "2025-08-27"),
    )

    window = select_source_version_change_window(
        witnesses,
        requested_version_date="2025-06-01",
        version_date=lambda witness: witness.date,
    )

    assert window.truth_claim == SOURCE_VERSION_CHANGE_WINDOW_TRUTH_CLAIM
    assert window.replay_claims is False
    assert window.before == witnesses[1]
    assert window.on_or_after == witnesses[0]


def test_source_version_window_ties_preserve_candidate_order() -> None:
    witnesses = (
        _Witness("preferred", "2025-06-01"),
        _Witness("same-date-later-in-input", "2025-06-01"),
    )

    date_window = select_source_version_date_window(
        witnesses,
        requested_version_date="2025-06-01",
        version_date=lambda witness: witness.date,
    )
    change_window = select_source_version_change_window(
        witnesses,
        requested_version_date="2025-06-01",
        version_date=lambda witness: witness.date,
    )

    assert date_window.on_or_before == witnesses[0]
    assert date_window.on_or_after == witnesses[0]
    assert change_window.before is None
    assert change_window.on_or_after == witnesses[0]


def test_iso_date_prefix_rejects_non_iso_prefixes() -> None:
    assert iso_date_prefix("2025-06-01Z") == "2025-06-01"
    assert iso_date_prefix("not-2025-06-01") == ""

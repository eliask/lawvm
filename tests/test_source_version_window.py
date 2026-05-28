from __future__ import annotations

from dataclasses import dataclass

from lawvm.core.source_version_window import (
    SOURCE_VERSION_CHANGE_WINDOW_RULE_ID,
    SOURCE_VERSION_CHANGE_WINDOW_TRUTH_CLAIM,
    SOURCE_VERSION_DATE_WINDOW_RULE_ID,
    SOURCE_VERSION_DATE_WINDOW_TRUTH_CLAIM,
    iso_date_prefix,
    select_source_version_change_window,
    select_source_version_date_window,
    source_version_change_window_diagnostic_detail,
    source_version_date_window_diagnostic_detail,
)


@dataclass(frozen=True)
class _Witness:
    version_id: str
    version_date: str
    locator: str = ""


def _date(witness: _Witness) -> str:
    return witness.version_date


def _detail(witness: _Witness) -> dict[str, str]:
    return {
        "version_id": witness.version_id,
        "version_date": witness.version_date,
        "locator": witness.locator,
    }


def test_iso_date_prefix_accepts_iso_prefix_only() -> None:
    assert iso_date_prefix("2026-05-29T12:00:00Z") == "2026-05-29"
    assert iso_date_prefix(" 2026-05-29 ") == "2026-05-29"
    assert iso_date_prefix("29.05.2026") == ""


def test_date_window_selects_exact_match_as_both_sides() -> None:
    exact = _Witness("v2", "2026-05-29", "exact.xml")
    window = select_source_version_date_window(
        (
            _Witness("v1", "2026-01-01", "before.xml"),
            exact,
            _Witness("v3", "2026-12-31", "after.xml"),
        ),
        requested_version_date="2026-05-29",
        version_date=_date,
    )

    assert window.on_or_before is exact
    assert window.on_or_after is exact
    assert window.rule_id == SOURCE_VERSION_DATE_WINDOW_RULE_ID
    assert window.truth_claim == SOURCE_VERSION_DATE_WINDOW_TRUTH_CLAIM
    assert window.replay_claims is False


def test_date_window_brackets_between_versions_without_effectivity_claim() -> None:
    before = _Witness("v1", "2026-01-01", "before.xml")
    after = _Witness("v2", "2026-12-31", "after.xml")
    window = select_source_version_date_window(
        (after, before),
        requested_version_date="2026-05-29",
        version_date=_date,
    )

    assert window.on_or_before is before
    assert window.on_or_after is after
    detail = source_version_date_window_diagnostic_detail(
        window,
        witness_detail=_detail,
        phase="test_source_window",
    )
    assert detail["rule_id"] == SOURCE_VERSION_DATE_WINDOW_RULE_ID
    assert detail["family"] == "source_version_window"
    assert detail["truth_claim"] == SOURCE_VERSION_DATE_WINDOW_TRUTH_CLAIM
    assert detail["replay_claims"] is False
    assert detail["on_or_before"]["version_id"] == "v1"
    assert detail["on_or_after"]["version_id"] == "v2"


def test_change_window_uses_strict_before_and_on_or_after() -> None:
    before = _Witness("before", "2026-01-01")
    exact = _Witness("exact", "2026-05-29")
    after = _Witness("after", "2026-12-31")
    window = select_source_version_change_window(
        (after, exact, before),
        requested_version_date="2026-05-29",
        version_date=_date,
    )

    assert window.before is before
    assert window.on_or_after is exact
    assert window.rule_id == SOURCE_VERSION_CHANGE_WINDOW_RULE_ID
    assert window.truth_claim == SOURCE_VERSION_CHANGE_WINDOW_TRUTH_CLAIM
    assert window.replay_claims is False
    detail = source_version_change_window_diagnostic_detail(
        window,
        witness_detail=_detail,
        phase="test_source_window",
    )
    assert detail["before"]["version_id"] == "before"
    assert detail["on_or_after"]["version_id"] == "exact"


def test_source_version_window_ignores_undated_candidates_and_preserves_duplicate_tie_order() -> None:
    first_duplicate = _Witness("first", "2026-05-29")
    second_duplicate = _Witness("second", "2026-05-29")
    window = select_source_version_date_window(
        (
            _Witness("undated", "not-a-date"),
            first_duplicate,
            second_duplicate,
        ),
        requested_version_date="2026-05-29",
        version_date=_date,
    )

    assert window.on_or_before is first_duplicate
    assert window.on_or_after is first_duplicate


def test_source_version_window_reports_missing_witnesses_as_null_details() -> None:
    window = select_source_version_date_window(
        (),
        requested_version_date="2026-05-29",
        version_date=_date,
    )

    detail = source_version_date_window_diagnostic_detail(
        window,
        witness_detail=_detail,
    )
    assert detail["requested_version_date"] == "2026-05-29"
    assert detail["on_or_before"] is None
    assert detail["on_or_after"] is None

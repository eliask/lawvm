"""Shared source-version bracketing helpers.

These helpers select archived source witnesses around a requested source
version date. They deliberately do not interpret commencement, legal effect, or
oracle authority.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Generic, Sequence, TypeVar

T = TypeVar("T")

SOURCE_VERSION_DATE_WINDOW_RULE_ID = "source_version_date_window_source_only"
SOURCE_VERSION_CHANGE_WINDOW_RULE_ID = "source_version_change_window_source_only"
SOURCE_VERSION_DATE_WINDOW_TRUTH_CLAIM = "source_version_date_window_not_effective_date"
SOURCE_VERSION_CHANGE_WINDOW_TRUTH_CLAIM = "source_change_window_not_effective_date"


@dataclass(frozen=True)
class SourceVersionDateWindow(Generic[T]):
    requested_version_date: str
    on_or_before: T | None
    on_or_after: T | None
    rule_id: str = SOURCE_VERSION_DATE_WINDOW_RULE_ID
    truth_claim: str = SOURCE_VERSION_DATE_WINDOW_TRUTH_CLAIM
    replay_claims: bool = False


@dataclass(frozen=True)
class SourceVersionChangeWindow(Generic[T]):
    requested_version_date: str
    before: T | None
    on_or_after: T | None
    rule_id: str = SOURCE_VERSION_CHANGE_WINDOW_RULE_ID
    truth_claim: str = SOURCE_VERSION_CHANGE_WINDOW_TRUTH_CLAIM
    replay_claims: bool = False


def select_source_version_date_window(
    candidates: Sequence[T],
    *,
    requested_version_date: str,
    version_date: Callable[[T], str],
) -> SourceVersionDateWindow[T]:
    """Return latest on-or-before and earliest on-or-after source witnesses."""

    requested = iso_date_prefix(requested_version_date)
    dated = _dated_candidates(candidates, version_date)
    before_date = max((date for date, _candidate in dated if date <= requested), default="")
    after_date = min((date for date, _candidate in dated if date >= requested), default="")
    on_or_before = next((candidate for date, candidate in dated if date == before_date), None)
    on_or_after = next((candidate for date, candidate in dated if date == after_date), None)
    return SourceVersionDateWindow(
        requested_version_date=requested,
        on_or_before=on_or_before,
        on_or_after=on_or_after,
    )


def select_source_version_change_window(
    candidates: Sequence[T],
    *,
    requested_version_date: str,
    version_date: Callable[[T], str],
) -> SourceVersionChangeWindow[T]:
    """Return strict-before and earliest on-or-after source witnesses."""

    requested = iso_date_prefix(requested_version_date)
    dated = _dated_candidates(candidates, version_date)
    before_date = max((date for date, _candidate in dated if date < requested), default="")
    after_date = min((date for date, _candidate in dated if date >= requested), default="")
    before = next((candidate for date, candidate in dated if date == before_date), None)
    on_or_after = next((candidate for date, candidate in dated if date == after_date), None)
    return SourceVersionChangeWindow(
        requested_version_date=requested,
        before=before,
        on_or_after=on_or_after,
    )


def iso_date_prefix(value: str) -> str:
    match = _ISO_DATE_PREFIX_RE.match(value.strip())
    return match.group(1) if match else ""


def _dated_candidates(
    candidates: Sequence[T],
    version_date: Callable[[T], str],
) -> tuple[tuple[str, T], ...]:
    return tuple(
        (date_prefix, candidate)
        for candidate in candidates
        if (date_prefix := iso_date_prefix(version_date(candidate)))
    )


_ISO_DATE_PREFIX_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")

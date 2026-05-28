"""Shared source-version bracketing helpers.

These helpers select archived source witnesses around a requested source
version date. They deliberately do not interpret commencement, legal effect, or
oracle authority.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Generic, Mapping, Protocol, Sequence, TypeVar

from lawvm.core.diagnostic_records import diagnostic_detail

T = TypeVar("T")

SOURCE_VERSION_DATE_WINDOW_RULE_ID = "source_version_date_window_source_only"
SOURCE_VERSION_CHANGE_WINDOW_RULE_ID = "source_version_change_window_source_only"
SOURCE_VERSION_DATE_WINDOW_TRUTH_CLAIM = "source_version_date_window_not_effective_date"
SOURCE_VERSION_CHANGE_WINDOW_TRUTH_CLAIM = "source_change_window_not_effective_date"
SOURCE_VERSION_WINDOW_FAMILY = "source_version_window"


@dataclass(frozen=True)
class SourceVersionDateWindow(Generic[T]):
    requested_version_date: str
    on_or_before: T | None
    on_or_after: T | None
    rule_id: str = SOURCE_VERSION_DATE_WINDOW_RULE_ID
    truth_claim: str = SOURCE_VERSION_DATE_WINDOW_TRUTH_CLAIM
    replay_claims: bool = False

    def __post_init__(self) -> None:
        _validate_source_version_window_contract(
            self,
            expected_truth_claim=SOURCE_VERSION_DATE_WINDOW_TRUTH_CLAIM,
            subject="SourceVersionDateWindow",
        )


@dataclass(frozen=True)
class SourceVersionChangeWindow(Generic[T]):
    requested_version_date: str
    before: T | None
    on_or_after: T | None
    rule_id: str = SOURCE_VERSION_CHANGE_WINDOW_RULE_ID
    truth_claim: str = SOURCE_VERSION_CHANGE_WINDOW_TRUTH_CLAIM
    replay_claims: bool = False

    def __post_init__(self) -> None:
        _validate_source_version_window_contract(
            self,
            expected_truth_claim=SOURCE_VERSION_CHANGE_WINDOW_TRUTH_CLAIM,
            subject="SourceVersionChangeWindow",
        )


class SourceVersionDateWindowLike(Protocol[T]):
    requested_version_date: str
    on_or_before: T | None
    on_or_after: T | None
    rule_id: str
    truth_claim: str
    replay_claims: bool


class SourceVersionChangeWindowLike(Protocol[T]):
    requested_version_date: str
    before: T | None
    on_or_after: T | None
    rule_id: str
    truth_claim: str
    replay_claims: bool


def select_source_version_date_window(
    candidates: Sequence[T],
    *,
    requested_version_date: str,
    version_date: Callable[[T], str],
) -> SourceVersionDateWindow[T]:
    """Return latest on-or-before and earliest on-or-after source witnesses."""

    requested = _requested_source_version_date(requested_version_date)
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

    requested = _requested_source_version_date(requested_version_date)
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


def source_version_date_window_diagnostic_detail(
    window: SourceVersionDateWindowLike[T],
    *,
    witness_detail: Callable[[T], Mapping[str, Any]],
    phase: str = "source_version_window",
    reason: str = "source_version_date_window_source_only",
) -> dict[str, Any]:
    """Project a source-version date window into a shared evidence shape.

    This is intentionally source-only evidence. It does not assert legal
    commencement, effectivity, oracle authority, or replay agreement.
    """

    _validate_source_version_window_contract(
        window,
        expected_truth_claim=SOURCE_VERSION_DATE_WINDOW_TRUTH_CLAIM,
        subject="source version date window diagnostic",
    )
    return diagnostic_detail(
        rule_id=window.rule_id,
        phase=phase,
        family=SOURCE_VERSION_WINDOW_FAMILY,
        reason=reason,
        blocking=False,
        strict_disposition="record",
        quirks_disposition="record",
        requested_version_date=window.requested_version_date,
        truth_claim=window.truth_claim,
        replay_claims=window.replay_claims,
        on_or_before=_source_version_witness_detail(window.on_or_before, witness_detail),
        on_or_after=_source_version_witness_detail(window.on_or_after, witness_detail),
    )


def source_version_change_window_diagnostic_detail(
    window: SourceVersionChangeWindowLike[T],
    *,
    witness_detail: Callable[[T], Mapping[str, Any]],
    phase: str = "source_version_window",
    reason: str = "source_version_change_window_source_only",
) -> dict[str, Any]:
    """Project a strict-before/on-or-after source-change window as evidence."""

    _validate_source_version_window_contract(
        window,
        expected_truth_claim=SOURCE_VERSION_CHANGE_WINDOW_TRUTH_CLAIM,
        subject="source version change window diagnostic",
    )
    return diagnostic_detail(
        rule_id=window.rule_id,
        phase=phase,
        family=SOURCE_VERSION_WINDOW_FAMILY,
        reason=reason,
        blocking=False,
        strict_disposition="record",
        quirks_disposition="record",
        requested_version_date=window.requested_version_date,
        truth_claim=window.truth_claim,
        replay_claims=window.replay_claims,
        before=_source_version_witness_detail(window.before, witness_detail),
        on_or_after=_source_version_witness_detail(window.on_or_after, witness_detail),
    )


def _source_version_witness_detail(
    witness: T | None,
    witness_detail: Callable[[T], Mapping[str, Any]],
) -> dict[str, Any] | None:
    return dict(witness_detail(witness)) if witness is not None else None


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


def _requested_source_version_date(value: str) -> str:
    requested = iso_date_prefix(value)
    if not requested:
        raise ValueError("requested_version_date must start with an ISO YYYY-MM-DD date")
    return requested


def _validate_source_version_window_contract(
    window: SourceVersionDateWindowLike[Any] | SourceVersionChangeWindowLike[Any],
    *,
    expected_truth_claim: str,
    subject: str,
) -> None:
    requested = str(window.requested_version_date or "").strip()
    if iso_date_prefix(requested) != requested:
        raise ValueError(f"{subject}.requested_version_date must be a normalized ISO YYYY-MM-DD date")
    if not str(window.rule_id or "").strip():
        raise ValueError(f"{subject}.rule_id must be non-empty")
    if window.truth_claim != expected_truth_claim:
        raise ValueError(f"{subject}.truth_claim must be {expected_truth_claim!r}")
    if window.replay_claims is not False:
        raise ValueError(f"{subject}.replay_claims must be False")


_ISO_DATE_PREFIX_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")

"""Shared source-lane selection evidence records.

These records describe which source acquisition lane was selected and which
candidate lanes were considered. They do not decide jurisdiction-specific
authority or fallback policy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from lawvm.core.diagnostic_records import diagnostic_detail


@dataclass(frozen=True)
class SourceLaneAttempt:
    lane: str
    locator: str = ""
    status: str = ""
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.lane or "").strip():
            raise ValueError("SourceLaneAttempt.lane must be non-empty")
        if not str(self.status or "").strip():
            raise ValueError("SourceLaneAttempt.status must be non-empty")
        _reject_source_lane_overrides("SourceLaneAttempt.detail", self.detail, {"lane", "locator", "status"})

    def to_dict(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "lane": str(self.lane),
            "status": str(self.status),
        }
        if self.locator:
            row["locator"] = str(self.locator)
        row.update(dict(self.detail))
        return row


@dataclass(frozen=True)
class SourceLaneSelectionEvidence:
    rule_id: str
    phase: str
    reason: str
    selected_lane: str
    attempts: tuple[SourceLaneAttempt, ...]
    selected_locator: str = ""
    blocking: bool = False
    strict_disposition: str = "record"
    quirks_disposition: str = "record"
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.rule_id or "").strip():
            raise ValueError("SourceLaneSelectionEvidence.rule_id must be non-empty")
        if not str(self.phase or "").strip():
            raise ValueError("SourceLaneSelectionEvidence.phase must be non-empty")
        if not str(self.reason or "").strip():
            raise ValueError("SourceLaneSelectionEvidence.reason must be non-empty")
        if not str(self.selected_lane or "").strip():
            raise ValueError("SourceLaneSelectionEvidence.selected_lane must be non-empty")
        if not self.attempts:
            raise ValueError("SourceLaneSelectionEvidence.attempts must be non-empty")
        attempt_lanes = {attempt.lane for attempt in self.attempts}
        has_selected_attempt = any(
            str(attempt.status).startswith("selected") for attempt in self.attempts
        )
        if (
            self.selected_lane not in attempt_lanes
            and not has_selected_attempt
            and not self.selected_lane.startswith("no_source_lane_selected_")
        ):
            raise ValueError(
                "SourceLaneSelectionEvidence.selected_lane must match an attempted lane, "
                "have a selected attempt, or use no_source_lane_selected_*"
            )
        _reject_source_lane_overrides(
            "SourceLaneSelectionEvidence.detail",
            self.detail,
            {"selected_source_lane", "selected_source_locator", "source_lane_attempts"},
        )

    def to_diagnostic_detail(self) -> dict[str, Any]:
        return diagnostic_detail(
            rule_id=self.rule_id,
            family="source_lane_selection",
            phase=self.phase,
            reason=self.reason,
            blocking=self.blocking,
            strict_disposition=self.strict_disposition,
            quirks_disposition=self.quirks_disposition,
            selected_source_lane=self.selected_lane,
            selected_source_locator=self.selected_locator,
            source_lane_attempts=tuple(attempt.to_dict() for attempt in self.attempts),
            detail=self.detail,
        )


def source_lane_attempt_from_mapping(row: Mapping[str, Any]) -> SourceLaneAttempt:
    detail = {key: value for key, value in row.items() if key not in {"lane", "url", "locator", "status"}}
    return SourceLaneAttempt(
        lane=str(row.get("lane") or ""),
        locator=str(row.get("locator") or row.get("url") or ""),
        status=str(row.get("status") or ""),
        detail=detail,
    )


def _reject_source_lane_overrides(source: str, values: Mapping[str, Any], reserved: set[str]) -> None:
    overlaps = sorted(reserved.intersection(values.keys()))
    if overlaps:
        joined = ", ".join(overlaps)
        raise ValueError(f"{source} must not override source-lane keys: {joined}")

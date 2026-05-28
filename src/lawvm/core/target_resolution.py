"""Shared target-resolution evidence projection.

This module does not resolve legal targets. Frontends own source grammar,
candidate discovery, and local fallback policy. Core only provides a stable
diagnostic shape for the point where a frontend records how a source target
was resolved, rejected, or left ambiguous.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping

from lawvm.core.diagnostic_records import diagnostic_detail
from lawvm.core.frozen_values import FrozenDict, freeze_mapping


TargetResolutionStatus = Literal[
    "resolved",
    "unresolved",
    "ambiguous",
    "fallback_resolved",
    "recovered",
    "rejected",
]

TARGET_RESOLUTION_FAMILY = "target_resolution"
TARGET_RESOLVED: TargetResolutionStatus = "resolved"
TARGET_UNRESOLVED: TargetResolutionStatus = "unresolved"
TARGET_AMBIGUOUS: TargetResolutionStatus = "ambiguous"
TARGET_FALLBACK_RESOLVED: TargetResolutionStatus = "fallback_resolved"
TARGET_RECOVERED: TargetResolutionStatus = "recovered"
TARGET_REJECTED: TargetResolutionStatus = "rejected"

SCOPE_CONFIDENCE_EXPLICIT_SOURCE = "explicit_source"
SCOPE_CONFIDENCE_EXPLICIT_SOURCE_WITH_CONTEXT = "explicit_source_with_context"
SCOPE_CONFIDENCE_INFERRED_FROM_GROUP = "inferred_from_group"
SCOPE_CONFIDENCE_INFERRED_FROM_PAYLOAD = "inferred_from_payload"
SCOPE_CONFIDENCE_INFERRED_FROM_LIVE_UNIQUE = "inferred_from_live_unique"
SCOPE_CONFIDENCE_FALLBACK = "fallback"

_VALID_TARGET_RESOLUTION_STATUSES = frozenset(
    {
        TARGET_RESOLVED,
        TARGET_UNRESOLVED,
        TARGET_AMBIGUOUS,
        TARGET_FALLBACK_RESOLVED,
        TARGET_RECOVERED,
        TARGET_REJECTED,
    }
)
_VALID_SCOPE_CONFIDENCES = frozenset(
    {
        SCOPE_CONFIDENCE_EXPLICIT_SOURCE,
        SCOPE_CONFIDENCE_EXPLICIT_SOURCE_WITH_CONTEXT,
        SCOPE_CONFIDENCE_INFERRED_FROM_GROUP,
        SCOPE_CONFIDENCE_INFERRED_FROM_PAYLOAD,
        SCOPE_CONFIDENCE_INFERRED_FROM_LIVE_UNIQUE,
        SCOPE_CONFIDENCE_FALLBACK,
    }
)
_RESERVED_TARGET_RESOLUTION_KEYS = frozenset(
    {
        "target_resolution_status",
        "source_target",
        "candidate_count",
        "target_candidates",
        "selected_target",
        "scope_confidence",
        "selected_target_differs_from_source",
    }
)
_RESERVED_TARGET_CANDIDATE_KEYS = frozenset({"target", "reason"})


@dataclass(frozen=True)
class TargetResolutionCandidate:
    """One frontend-owned candidate considered during target resolution."""

    target: str
    reason: str = ""
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.target or "").strip():
            raise ValueError("TargetResolutionCandidate.target must be non-empty")
        object.__setattr__(
            self,
            "detail",
            _frozen_target_resolution_detail("TargetResolutionCandidate.detail", self.detail),
        )
        _reject_target_candidate_overrides(self.detail)
        _reject_target_resolution_overrides(self.detail)

    def to_dict(self) -> dict[str, Any]:
        row: dict[str, Any] = {"target": str(self.target)}
        if self.reason:
            row["reason"] = str(self.reason)
        row.update(dict(self.detail))
        return row


@dataclass(frozen=True)
class TargetResolutionCertificate:
    """Evidence envelope for frontend-owned target/slot selection decisions."""

    rule_id: str
    phase: str
    reason: str
    status: TargetResolutionStatus
    source_target: str
    candidate_count: int = 0
    candidates: tuple[TargetResolutionCandidate, ...] = ()
    selected_target: str = ""
    scope_confidence: str = ""
    blocking: bool = False
    strict_disposition: str = ""
    quirks_disposition: str = "record"
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.rule_id or "").strip():
            raise ValueError("TargetResolutionCertificate.rule_id must be non-empty")
        if not str(self.phase or "").strip():
            raise ValueError("TargetResolutionCertificate.phase must be non-empty")
        if not str(self.reason or "").strip():
            raise ValueError("TargetResolutionCertificate.reason must be non-empty")
        if not str(self.status or "").strip():
            raise ValueError("TargetResolutionCertificate.status must be non-empty")
        if self.status not in _VALID_TARGET_RESOLUTION_STATUSES:
            raise ValueError(
                f"TargetResolutionCertificate.status must be one of "
                f"{sorted(_VALID_TARGET_RESOLUTION_STATUSES)}"
            )
        if not str(self.source_target or "").strip():
            raise ValueError("TargetResolutionCertificate.source_target must be non-empty")
        candidates = tuple(self.candidates)
        if not all(isinstance(candidate, TargetResolutionCandidate) for candidate in candidates):
            raise ValueError(
                "TargetResolutionCertificate.candidates must contain TargetResolutionCandidate records"
            )
        object.__setattr__(self, "candidates", candidates)
        object.__setattr__(
            self,
            "detail",
            _frozen_target_resolution_detail("TargetResolutionCertificate.detail", self.detail),
        )
        if self.candidate_count < 0:
            raise ValueError("TargetResolutionCertificate.candidate_count must be non-negative")
        if self.candidate_count < len(self.candidates):
            raise ValueError(
                "TargetResolutionCertificate.candidate_count must cover listed candidates"
            )
        if self.status in {TARGET_RESOLVED, TARGET_FALLBACK_RESOLVED, TARGET_RECOVERED}:
            if not self.selected_target:
                raise ValueError(
                    f"TargetResolutionCertificate(status={self.status!r}) requires selected_target"
                )
            if self.candidate_count < 1:
                raise ValueError(
                    f"TargetResolutionCertificate(status={self.status!r}) requires candidate_count >= 1"
                )
        if self.scope_confidence and self.scope_confidence not in _VALID_SCOPE_CONFIDENCES:
            raise ValueError(
                f"TargetResolutionCertificate.scope_confidence must be one of "
                f"{sorted(_VALID_SCOPE_CONFIDENCES)}"
            )
        _reject_target_resolution_overrides(self.detail)

    def to_diagnostic_detail(self) -> dict[str, Any]:
        target_fields: dict[str, Any] = {
            "target_resolution_status": self.status,
            "source_target": self.source_target,
            "candidate_count": self.candidate_count,
        }
        if self.candidates:
            target_fields["target_candidates"] = tuple(candidate.to_dict() for candidate in self.candidates)
        if self.selected_target:
            target_fields["selected_target"] = self.selected_target
            target_fields["selected_target_differs_from_source"] = (
                self.selected_target != self.source_target
            )
        if self.scope_confidence:
            target_fields["scope_confidence"] = self.scope_confidence
        return diagnostic_detail(
            rule_id=self.rule_id,
            family=TARGET_RESOLUTION_FAMILY,
            phase=self.phase,
            reason=self.reason,
            blocking=self.blocking,
            strict_disposition=self.strict_disposition or ("block" if self.blocking else "record"),
            quirks_disposition=self.quirks_disposition,
            **target_fields,
            detail=self.detail,
        )


def target_resolution_candidate_from_mapping(row: Mapping[str, Any]) -> TargetResolutionCandidate:
    """Build a candidate while preserving frontend-local fields."""

    detail = {key: value for key, value in row.items() if key not in {"target", "reason"}}
    return TargetResolutionCandidate(
        target=str(row.get("target") or ""),
        reason=str(row.get("reason") or ""),
        detail=detail,
    )


def _reject_target_resolution_overrides(values: Mapping[str, Any]) -> None:
    overlaps = sorted(_RESERVED_TARGET_RESOLUTION_KEYS.intersection(values.keys()))
    if overlaps:
        joined = ", ".join(overlaps)
        raise ValueError(
            f"target resolution detail must not override target-resolution keys: {joined}"
        )


def _reject_target_candidate_overrides(values: Mapping[str, Any]) -> None:
    overlaps = sorted(_RESERVED_TARGET_CANDIDATE_KEYS.intersection(values.keys()))
    if overlaps:
        joined = ", ".join(overlaps)
        raise ValueError(f"target resolution candidate detail must not override candidate keys: {joined}")


def _frozen_target_resolution_detail(source: str, values: Mapping[str, Any]) -> FrozenDict:
    if not isinstance(values, Mapping):
        raise ValueError(f"{source} must be a mapping")
    return freeze_mapping(values)

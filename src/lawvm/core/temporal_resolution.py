"""Shared temporal-resolution evidence projection.

This module does not parse commencement language or decide legal temporal
policy. Frontends own those rules. Core only provides a stable diagnostic
shape for the point where a frontend has resolved, deferred, or rejected a
temporal fact.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping

from lawvm.core.diagnostic_records import diagnostic_detail


TemporalResolutionStatus = Literal[
    "fixed_date",
    "immediate",
    "source_backed_override",
    "certified_untriggered",
    "unresolved_contingent",
    "unknown_effective_date",
    "future_effective_date",
]

TEMPORAL_RESOLUTION_FAMILY = "temporal_resolution"
TEMPORAL_RECOVERY_FAMILY = "temporal_recovery"
TEMPORAL_FIXED_DATE: TemporalResolutionStatus = "fixed_date"
TEMPORAL_IMMEDIATE: TemporalResolutionStatus = "immediate"
TEMPORAL_SOURCE_BACKED_OVERRIDE: TemporalResolutionStatus = "source_backed_override"
TEMPORAL_CERTIFIED_UNTRIGGERED: TemporalResolutionStatus = "certified_untriggered"
TEMPORAL_UNRESOLVED_CONTINGENT: TemporalResolutionStatus = "unresolved_contingent"
TEMPORAL_UNKNOWN_EFFECTIVE_DATE: TemporalResolutionStatus = "unknown_effective_date"
TEMPORAL_FUTURE_EFFECTIVE_DATE: TemporalResolutionStatus = "future_effective_date"

_RESERVED_TEMPORAL_KEYS = frozenset(
    {
        "temporal_resolution_status",
        "effective_date",
        "as_of",
        "source_locator",
        "authority_layer",
    }
)


@dataclass(frozen=True)
class TemporalResolutionEvidence:
    """Evidence envelope for frontend-owned temporal resolution decisions."""

    rule_id: str
    phase: str
    reason: str
    status: TemporalResolutionStatus
    blocking: bool = False
    family: str = TEMPORAL_RESOLUTION_FAMILY
    effective_date: str = ""
    as_of: str = ""
    source_locator: str = ""
    authority_layer: str = ""
    strict_disposition: str = ""
    quirks_disposition: str = "record"
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.rule_id or "").strip():
            raise ValueError("TemporalResolutionEvidence.rule_id must be non-empty")
        if not str(self.phase or "").strip():
            raise ValueError("TemporalResolutionEvidence.phase must be non-empty")
        if not str(self.reason or "").strip():
            raise ValueError("TemporalResolutionEvidence.reason must be non-empty")
        if not str(self.status or "").strip():
            raise ValueError("TemporalResolutionEvidence.status must be non-empty")
        if self.status in {
            TEMPORAL_FIXED_DATE,
            TEMPORAL_SOURCE_BACKED_OVERRIDE,
            TEMPORAL_FUTURE_EFFECTIVE_DATE,
        } and not self.effective_date:
            raise ValueError(
                f"TemporalResolutionEvidence(status={self.status!r}) requires effective_date"
            )
        _reject_temporal_overrides(self.detail)

    def to_diagnostic_detail(self) -> dict[str, Any]:
        temporal_fields: dict[str, Any] = {"temporal_resolution_status": self.status}
        if self.effective_date:
            temporal_fields["effective_date"] = self.effective_date
        if self.as_of:
            temporal_fields["as_of"] = self.as_of
        if self.source_locator:
            temporal_fields["source_locator"] = self.source_locator
        if self.authority_layer:
            temporal_fields["authority_layer"] = self.authority_layer
        return diagnostic_detail(
            rule_id=self.rule_id,
            family=self.family,
            phase=self.phase,
            reason=self.reason,
            blocking=self.blocking,
            strict_disposition=self.strict_disposition or ("block" if self.blocking else "record"),
            quirks_disposition=self.quirks_disposition,
            **temporal_fields,
            detail=self.detail,
        )


def _reject_temporal_overrides(values: Mapping[str, Any]) -> None:
    overlaps = sorted(_RESERVED_TEMPORAL_KEYS.intersection(values.keys()))
    if overlaps:
        joined = ", ".join(overlaps)
        raise ValueError(f"TemporalResolutionEvidence.detail must not override temporal keys: {joined}")

"""Shared verification/reporting contracts.

These dataclasses are shared wire/reporting shapes for verifier-style tools.
Frontends may retain local issue types internally, but machine-readable
verifier output should project into these contracts rather than serializing
ad hoc text.

API tier
--------
Stable reporting contract. Keep additive where possible.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Mapping

from lawvm.core.frozen_values import freeze_mapping


VerifySeverity = Literal["error", "warning", "info"]
VERIFY_SEVERITIES = frozenset({"error", "warning", "info"})


@dataclass(frozen=True)
class VerifyIssue:
    """Shared verification issue shape."""

    code: str
    message: str
    stage: str = ""
    severity: VerifySeverity = "error"
    context: str = ""
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_field(self.code, "VerifyIssue.code")
        _require_field(self.message, "VerifyIssue.message")
        if self.severity not in VERIFY_SEVERITIES:
            raise ValueError(f"VerifyIssue.severity must be one of {sorted(VERIFY_SEVERITIES)}")
        if not isinstance(self.detail, Mapping):
            raise ValueError("VerifyIssue.detail must be a mapping")
        object.__setattr__(self, "detail", freeze_mapping(self.detail))

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["detail"] = dict(self.detail)
        return data


@dataclass(frozen=True)
class DivergenceRecord:
    """Shared divergence row shape for replay-vs-oracle style comparisons."""

    address: str
    kind: str
    replay_text: str = ""
    oracle_text: str = ""
    score: float | None = None
    touched: bool | None = None
    source_signal: str = ""
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_field(self.address, "DivergenceRecord.address")
        _require_field(self.kind, "DivergenceRecord.kind")
        if self.score is not None and not 0 <= self.score <= 1:
            raise ValueError("DivergenceRecord.score must be between 0 and 1")
        if not isinstance(self.detail, Mapping):
            raise ValueError("DivergenceRecord.detail must be a mapping")
        object.__setattr__(self, "detail", freeze_mapping(self.detail))

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["detail"] = dict(self.detail)
        return data


@dataclass(frozen=True)
class FilteredDivergenceRecord:
    """A divergence intentionally removed from the primary mismatch lane."""

    divergence: Any
    rule_id: str
    reason: str

    def __post_init__(self) -> None:
        _require_field(self.rule_id, "FilteredDivergenceRecord.rule_id")
        _require_field(self.reason, "FilteredDivergenceRecord.reason")


@dataclass(frozen=True)
class DivergencePartition:
    """Primary divergences plus filtered divergences with explicit rule IDs."""

    primary: tuple[Any, ...]
    filtered: tuple[FilteredDivergenceRecord, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "primary", tuple(self.primary))
        filtered = tuple(self.filtered)
        if not all(isinstance(record, FilteredDivergenceRecord) for record in filtered):
            raise ValueError("DivergencePartition.filtered must contain FilteredDivergenceRecord records")
        object.__setattr__(self, "filtered", filtered)


@dataclass(frozen=True)
class CoverageAttribution:
    """Shared summary of touched/untouched divergence attribution."""

    touched_path_count: int = 0
    touched_source_count: int = 0
    touched_op_count: int = 0
    touched_divergence_count: int = 0
    untouched_divergence_count: int = 0
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in (
            "touched_path_count",
            "touched_source_count",
            "touched_op_count",
            "touched_divergence_count",
            "untouched_divergence_count",
        ):
            if getattr(self, field_name) < 0:
                raise ValueError(f"CoverageAttribution.{field_name} must be non-negative")
        if not isinstance(self.detail, Mapping):
            raise ValueError("CoverageAttribution.detail must be a mapping")
        object.__setattr__(self, "detail", freeze_mapping(self.detail))

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["detail"] = dict(self.detail)
        return data


@dataclass(frozen=True)
class VerifySummary:
    """Shared top-level verification result shape."""

    jurisdiction: str
    base_id: str
    as_of: str = ""
    status: str = "ok"
    error: str | None = None
    consistent: bool | None = None
    issue_count: int = 0
    divergence_count: int = 0
    op_count: int = 0
    source_signal: str = ""
    issues: tuple[VerifyIssue, ...] = ()
    divergences: tuple[DivergenceRecord, ...] = ()
    coverage: CoverageAttribution | None = None
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_field(self.jurisdiction, "VerifySummary.jurisdiction")
        _require_field(self.base_id, "VerifySummary.base_id")
        _require_field(self.status, "VerifySummary.status")
        for field_name in ("issue_count", "divergence_count", "op_count"):
            if getattr(self, field_name) < 0:
                raise ValueError(f"VerifySummary.{field_name} must be non-negative")
        issues = tuple(self.issues)
        if not all(isinstance(issue, VerifyIssue) for issue in issues):
            raise ValueError("VerifySummary.issues must contain VerifyIssue records")
        object.__setattr__(self, "issues", issues)
        divergences = tuple(self.divergences)
        if not all(isinstance(divergence, DivergenceRecord) for divergence in divergences):
            raise ValueError("VerifySummary.divergences must contain DivergenceRecord records")
        object.__setattr__(self, "divergences", divergences)
        if self.coverage is not None and not isinstance(self.coverage, CoverageAttribution):
            raise ValueError("VerifySummary.coverage must be a CoverageAttribution")
        if not isinstance(self.detail, Mapping):
            raise ValueError("VerifySummary.detail must be a mapping")
        object.__setattr__(self, "detail", freeze_mapping(self.detail))

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["issues"] = [issue.to_dict() for issue in self.issues]
        data["divergences"] = [divergence.to_dict() for divergence in self.divergences]
        data["coverage"] = self.coverage.to_dict() if self.coverage is not None else None
        data["detail"] = dict(self.detail)
        return data


def _require_field(value: str, name: str) -> None:
    if not str(value or "").strip():
        raise ValueError(f"{name} must be non-empty")

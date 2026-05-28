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


VerifySeverity = Literal["error", "warning", "info"]


@dataclass(frozen=True)
class VerifyIssue:
    """Shared verification issue shape."""

    code: str
    message: str
    stage: str = ""
    severity: VerifySeverity = "error"
    context: str = ""
    detail: Mapping[str, Any] = field(default_factory=dict)

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


@dataclass(frozen=True)
class DivergencePartition:
    """Primary divergences plus filtered divergences with explicit rule IDs."""

    primary: list[Any]
    filtered: list[FilteredDivergenceRecord]


@dataclass(frozen=True)
class CoverageAttribution:
    """Shared summary of touched/untouched divergence attribution."""

    touched_path_count: int = 0
    touched_source_count: int = 0
    touched_op_count: int = 0
    touched_divergence_count: int = 0
    untouched_divergence_count: int = 0
    detail: Mapping[str, Any] = field(default_factory=dict)

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

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["issues"] = [issue.to_dict() for issue in self.issues]
        data["divergences"] = [divergence.to_dict() for divergence in self.divergences]
        data["coverage"] = self.coverage.to_dict() if self.coverage is not None else None
        data["detail"] = dict(self.detail)
        return data

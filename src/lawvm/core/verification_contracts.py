"""Reserved verification/reporting contracts.

These dataclasses are candidate shared wire/reporting shapes, but they are not
yet the repo's live verification surface. There are currently no production
importers under ``src/lawvm/``; the only direct consumer is the shared-contract
shape test.

API tier
--------
Reserved contract header. Stable enough to retain, but not a currently
adopted cross-cutting runtime surface.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Mapping


VerifySeverity = Literal["error", "warning", "info"]


@dataclass(frozen=True)
class VerifyIssue:
    """Reserved verification issue shape."""

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
    """Reserved divergence row shape for replay-vs-oracle style comparisons."""

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
class CoverageAttribution:
    """Reserved summary of touched/untouched divergence attribution."""

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
    """Reserved top-level verification result shape."""

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

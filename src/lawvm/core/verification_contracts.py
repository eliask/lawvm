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

CurrentTextGateStatus = Literal[
    "yes",
    "no",
    "unknown",
    "not_applicable",
    "requires_public_html_review",
]
CURRENT_TEXT_GATE_STATUSES = frozenset(
    {
        "yes",
        "no",
        "unknown",
        "not_applicable",
        "requires_public_html_review",
    }
)
CURRENT_TEXT_GATE_FIELDS = (
    "current_body_text_contains_target_phrase",
    "current_status_page_check",
    "source_explicitly_omits_or_repeals_same_text",
    "commencement_in_force",
    "same_territorial_extent",
    "no_later_reinsertion_revival_or_replacement_found",
    "target_phrase_in_operative_text_not_commentary",
)


@dataclass(frozen=True)
class CurrentTextVerificationMatrix:
    """A-G gate for source-backed current-text review packets.

    This is a reporting/adjudication contract only. It does not authorize replay
    and does not classify an official current representation as wrong by itself.
    """

    current_body_text_contains_target_phrase: CurrentTextGateStatus
    current_status_page_check: CurrentTextGateStatus
    source_explicitly_omits_or_repeals_same_text: CurrentTextGateStatus
    commencement_in_force: CurrentTextGateStatus
    same_territorial_extent: CurrentTextGateStatus
    no_later_reinsertion_revival_or_replacement_found: CurrentTextGateStatus
    target_phrase_in_operative_text_not_commentary: CurrentTextGateStatus
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in CURRENT_TEXT_GATE_FIELDS:
            object.__setattr__(
                self,
                field_name,
                _current_text_gate_status(field_name, getattr(self, field_name)),
            )
        if not isinstance(self.detail, Mapping):
            raise ValueError("CurrentTextVerificationMatrix.detail must be a mapping")
        object.__setattr__(self, "detail", freeze_mapping(self.detail))

    @property
    def blocking_gate_names(self) -> tuple[str, ...]:
        """Gate names that block an email-safe/public-proof candidate."""

        blocked: list[str] = []
        for field_name in CURRENT_TEXT_GATE_FIELDS:
            status = getattr(self, field_name)
            if field_name == "commencement_in_force":
                if status not in {"yes", "not_applicable"}:
                    blocked.append(field_name)
                continue
            if status != "yes":
                blocked.append(field_name)
        return tuple(blocked)

    @property
    def is_email_safe(self) -> bool:
        return not self.blocking_gate_names

    def to_dict(self) -> dict[str, Any]:
        data = {field_name: getattr(self, field_name) for field_name in CURRENT_TEXT_GATE_FIELDS}
        data["blocking_gate_names"] = list(self.blocking_gate_names)
        data["is_email_safe"] = self.is_email_safe
        data["detail"] = dict(self.detail)
        return data


def current_text_verification_matrix_from_mapping(
    matrix: Mapping[str, Any],
) -> CurrentTextVerificationMatrix:
    """Build a typed current-text verification matrix from report rows."""

    return CurrentTextVerificationMatrix(
        current_body_text_contains_target_phrase=matrix.get(
            "current_body_text_contains_target_phrase",
            "unknown",
        ),
        current_status_page_check=matrix.get("current_status_page_check", "unknown"),
        source_explicitly_omits_or_repeals_same_text=matrix.get(
            "source_explicitly_omits_or_repeals_same_text",
            "unknown",
        ),
        commencement_in_force=matrix.get("commencement_in_force", "unknown"),
        same_territorial_extent=matrix.get("same_territorial_extent", "unknown"),
        no_later_reinsertion_revival_or_replacement_found=matrix.get(
            "no_later_reinsertion_revival_or_replacement_found",
            "unknown",
        ),
        target_phrase_in_operative_text_not_commentary=matrix.get(
            "target_phrase_in_operative_text_not_commentary",
            "unknown",
        ),
        detail=matrix.get("detail", {}) if isinstance(matrix.get("detail", {}), Mapping) else {},
    )


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
        count_fields = (
            ("touched_path_count", self.touched_path_count),
            ("touched_source_count", self.touched_source_count),
            ("touched_op_count", self.touched_op_count),
            ("touched_divergence_count", self.touched_divergence_count),
            ("untouched_divergence_count", self.untouched_divergence_count),
        )
        for field_name, value in count_fields:
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"CoverageAttribution.{field_name} must be an integer")
            if value < 0:
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
        for field_name, value in (
            ("issue_count", self.issue_count),
            ("divergence_count", self.divergence_count),
            ("op_count", self.op_count),
        ):
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"VerifySummary.{field_name} must be an integer")
            if value < 0:
                raise ValueError(f"VerifySummary.{field_name} must be non-negative")
        if self.error is not None and not isinstance(self.error, str):
            raise ValueError("VerifySummary.error must be a string or None")
        if self.consistent is not None and not isinstance(self.consistent, bool):
            raise ValueError("VerifySummary.consistent must be a bool or None")
        issues = tuple(self.issues)
        if not all(isinstance(issue, VerifyIssue) for issue in issues):
            raise ValueError("VerifySummary.issues must contain VerifyIssue records")
        object.__setattr__(self, "issues", issues)
        divergences = tuple(self.divergences)
        if not all(isinstance(divergence, DivergenceRecord) for divergence in divergences):
            raise ValueError("VerifySummary.divergences must contain DivergenceRecord records")
        object.__setattr__(self, "divergences", divergences)
        if self.issue_count and self.issue_count != len(issues):
            raise ValueError("VerifySummary.issue_count must match emitted issues when non-zero")
        if self.divergence_count and self.divergence_count != len(divergences):
            raise ValueError(
                "VerifySummary.divergence_count must match emitted divergences when non-zero"
            )
        if self.consistent is True and divergences:
            raise ValueError("VerifySummary.consistent=True cannot carry divergences")
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


def _current_text_gate_status(field_name: str, value: Any) -> CurrentTextGateStatus:
    text = str(value or "").strip()
    if text == "n/a":
        text = "not_applicable"
    if text not in CURRENT_TEXT_GATE_STATUSES:
        raise ValueError(
            f"CurrentTextVerificationMatrix.{field_name} must be one of "
            f"{sorted(CURRENT_TEXT_GATE_STATUSES)}"
        )
    return text  # ty:ignore[invalid-return-type]

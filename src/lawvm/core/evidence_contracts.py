"""Shared evidence/proof artifact contracts.

These dataclasses and validators are the minimal shared wire/reporting
contracts for cross-frontend corpus evidence export. They intentionally validate
only the common row envelope: frontend-local ``detail`` and ``evidence`` payloads
remain additive and uninterpreted by core.

API tier
--------
Shared evidence contract header. Stable enough for report/query consumers, but
still deliberately small; frontends own their local semantics.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Mapping

from lawvm.core.diagnostic_records import validate_blocking_disposition
from lawvm.core.frozen_values import freeze_mapping


@dataclass(frozen=True)
class EvidenceSummary:
    """Reserved evidence/proof bundle summary shape."""

    jurisdiction: str
    base_id: str
    primary_tier: str = ""
    status: str = "ok"
    error: str | None = None
    claim_count: int = 0
    divergence_count: int = 0
    actionable_count: int = 0
    unresolved_count: int = 0
    tiers: tuple[str, ...] = ()
    claim_kinds: tuple[str, ...] = ()
    trigger_sources: tuple[str, ...] = ()
    artifact_families: tuple[str, ...] = ()
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_dataclass_string(self.jurisdiction, "EvidenceSummary.jurisdiction")
        _require_dataclass_string(self.base_id, "EvidenceSummary.base_id")
        for field_name in ("claim_count", "divergence_count", "actionable_count", "unresolved_count"):
            if getattr(self, field_name) < 0:
                raise ValueError(f"EvidenceSummary.{field_name} must be non-negative")
        for field_name in ("tiers", "claim_kinds", "trigger_sources", "artifact_families"):
            object.__setattr__(self, field_name, tuple(str(value) for value in getattr(self, field_name)))
        if not isinstance(self.detail, Mapping):
            raise ValueError("EvidenceSummary.detail must be a mapping")
        object.__setattr__(self, "detail", freeze_mapping(self.detail))

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["detail"] = dict(self.detail)
        return data


class CorpusRowStatus(Enum):
    """Cross-frontend corpus operation/effect row disposition."""

    ACCEPTED = "accepted"
    REJECTED = "rejected"
    SKIPPED = "skipped"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"
    MATCHED = "matched"
    DIVERGED = "diverged"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class CorpusOperationEvidenceRow:
    """Minimal shared operation/effect row for corpus evidence exports."""

    row_id: str
    frontend_id: str
    source_artifact_id: str
    source_unit_id: str = ""
    source_locator: str = ""
    effect_family: str = ""
    canonical_family: str = ""
    original_target: str = ""
    resolved_target: str = ""
    status: CorpusRowStatus = CorpusRowStatus.ACCEPTED
    blocking: bool = False
    strict_disposition: str = ""
    quirks_disposition: str = ""
    finding_ids: tuple[str, ...] = ()
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.status, CorpusRowStatus):
            raise ValueError("CorpusOperationEvidenceRow.status must be a CorpusRowStatus")
        if not isinstance(self.detail, Mapping):
            raise ValueError("CorpusOperationEvidenceRow.detail must be a mapping")
        object.__setattr__(self, "finding_ids", tuple(str(value) for value in self.finding_ids))
        object.__setattr__(self, "detail", freeze_mapping(self.detail))
        _raise_if_issues(
            validate_corpus_operation_evidence_row(self.to_dict()),
            subject="CorpusOperationEvidenceRow",
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        data["detail"] = dict(self.detail)
        return data


@dataclass(frozen=True)
class CorpusFindingEvidenceRow:
    """Minimal shared finding row for corpus evidence exports."""

    finding_id: str
    frontend_id: str
    family: str
    rule_id: str
    phase: str
    message: str
    source_artifact_id: str = ""
    source_unit_id: str = ""
    related_row_ids: tuple[str, ...] = ()
    blocking: bool = False
    strict_disposition: str = ""
    quirks_disposition: str = ""
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.evidence, Mapping):
            raise ValueError("CorpusFindingEvidenceRow.evidence must be a mapping")
        object.__setattr__(
            self,
            "related_row_ids",
            tuple(str(value) for value in self.related_row_ids),
        )
        object.__setattr__(self, "evidence", freeze_mapping(self.evidence))
        _raise_if_issues(
            validate_corpus_finding_evidence_row(self.to_dict()),
            subject="CorpusFindingEvidenceRow",
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["evidence"] = dict(self.evidence)
        return data


_NON_CLAIM_STATUSES = frozenset({
    CorpusRowStatus.REJECTED.value,
    CorpusRowStatus.SKIPPED.value,
    CorpusRowStatus.UNSUPPORTED.value,
    CorpusRowStatus.FAILED.value,
})


def validate_corpus_operation_evidence_row(row: Mapping[str, Any]) -> tuple[str, ...]:
    """Return validation issues for a shared corpus operation/effect row dict."""

    issues: list[str] = []
    _require_non_empty_string(row, "row_id", issues)
    _require_non_empty_string(row, "frontend_id", issues)
    _require_non_empty_string(row, "source_artifact_id", issues)
    _require_non_empty_string(row, "strict_disposition", issues)
    _require_non_empty_string(row, "quirks_disposition", issues)
    status = row.get("status")
    valid_statuses = {status.value for status in CorpusRowStatus}
    if not isinstance(status, str) or not status:
        issues.append("status is required")
    elif status not in valid_statuses:
        issues.append(f"status has unsupported value: {status}")
    finding_ids = row.get("finding_ids", ())
    if finding_ids is None:
        finding_ids = ()
    if not isinstance(finding_ids, (list, tuple)):
        issues.append("finding_ids must be a list or tuple")
        finding_ids = ()
    detail = row.get("detail", {})
    if detail is None:
        detail = {}
    if not isinstance(detail, Mapping):
        issues.append("detail must be a mapping")
        detail = {}
    if isinstance(status, str) and status in _NON_CLAIM_STATUSES and not finding_ids and not _has_reason_detail(detail):
        issues.append(f"{status} row must carry finding_ids or reason-bearing detail")
    issues.extend(validate_blocking_disposition(row, subject="row"))
    blocking = row.get("blocking", False)
    if status == CorpusRowStatus.MATCHED.value and blocking and not detail.get("blocking_justification"):
        issues.append("matched row cannot be blocking without blocking_justification detail")
    return tuple(issues)


def validate_corpus_finding_evidence_row(row: Mapping[str, Any]) -> tuple[str, ...]:
    """Return validation issues for a shared corpus finding row dict."""

    issues: list[str] = []
    _require_non_empty_string(row, "finding_id", issues)
    _require_non_empty_string(row, "frontend_id", issues)
    _require_non_empty_string(row, "rule_id", issues)
    _require_non_empty_string(row, "phase", issues)
    _require_non_empty_string(row, "message", issues)
    _require_non_empty_string(row, "strict_disposition", issues)
    _require_non_empty_string(row, "quirks_disposition", issues)
    evidence = row.get("evidence", {})
    if evidence is not None and not isinstance(evidence, Mapping):
        issues.append("evidence must be a mapping")
    related_row_ids = row.get("related_row_ids", ())
    if related_row_ids is not None and not isinstance(related_row_ids, (list, tuple)):
        issues.append("related_row_ids must be a list or tuple")
    issues.extend(validate_blocking_disposition(row, subject="finding"))
    return tuple(issues)


def evidence_rule_ids(row: Mapping[str, Any]) -> set[str]:
    """Extract stable rule ids referenced by a shared evidence-row dict."""

    values = {_scalar(row.get("rule_id"))}
    finding_ids = row.get("finding_ids", ())
    if isinstance(finding_ids, (list, tuple)):
        values.update(str(value) for value in finding_ids)
    for evidence in _evidence_maps(row):
        for key in _RULE_DETAIL_KEYS:
            value = evidence.get(key)
            if isinstance(value, str) and (key != "reason" or _looks_like_rule_id(value)):
                values.add(value)
        for key in _RULE_LIST_DETAIL_KEYS:
            value = evidence.get(key)
            if isinstance(value, (list, tuple)):
                values.update(str(item) for item in value if str(item))
    return {value for value in values if value}


def evidence_row_kind(row: Mapping[str, Any]) -> str:
    """Classify a shared evidence row as a finding or operation row."""

    if "finding_id" in row or "rule_id" in row:
        return "finding"
    return "operation"


def _require_non_empty_string(row: Mapping[str, Any], key: str, issues: list[str]) -> None:
    value = row.get(key)
    if not isinstance(value, str) or not value:
        issues.append(f"{key} is required")


def _require_dataclass_string(value: str, name: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be non-empty")


def _raise_if_issues(issues: tuple[str, ...], *, subject: str) -> None:
    if issues:
        joined = "; ".join(issues)
        raise ValueError(f"{subject} invalid: {joined}")


def _has_reason_detail(detail: Mapping[str, Any]) -> bool:
    return any(detail.get(key) for key in ("reason", "error", "message", "finding", "status"))


def _evidence_maps(row: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    return tuple(value for key in ("detail", "evidence") if isinstance((value := row.get(key)), Mapping))


def _scalar(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


_RULE_DETAIL_KEYS = (
    "reason",
    "blocking_rule_id",
    "candidate_blocking_rule_id",
    "batch_blocking_rule_id",
    "preflight_blocking_rule_id",
    "replay_blocking_rule_id",
    "effect_blocking_rule_id",
    "operation_target_blocking_rule_id",
    "oracle_agreement_blocking_rule_id",
    "candidate_witness_rule_id",
    "instruction_semantic_rule_id",
    "instruction_subfamily_rule_id",
    "payload_structural_subfamily_rule_id",
    "latest_oracle_text_rule_id",
    "latest_oracle_target_resolution_rule_id",
    "repeal_payload_corroboration_rule_id",
)


_RULE_LIST_DETAIL_KEYS = (
    "declared_recovery_rule_ids",
    "declared_migration_rule_ids",
    "matched_allowance_rule_ids",
)


def _looks_like_rule_id(value: str) -> bool:
    if not value or " " in value:
        return False
    return "_" in value or ":" in value or "." in value

"""Shared report envelope for evidence surfaces."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from lawvm.core.frozen_values import freeze_mapping


@dataclass(frozen=True, slots=True)
class EvidenceSurfaceReport:
    """Envelope that states what an evidence report does and does not claim."""

    jurisdiction: str
    report_kind: str
    schema: str
    truth_claim: str
    replay_claims: bool
    canonical_effect_claims: bool
    candidate_effect_claims: bool
    dry_run_claims: bool
    agreement_claims: bool
    summary: Mapping[str, Any] = field(default_factory=dict)
    filters: Mapping[str, Any] = field(default_factory=dict)
    filtered_summary: Mapping[str, Any] = field(default_factory=dict)
    rows: tuple[Mapping[str, Any], ...] = ()
    rows_truncated: bool = False
    evidence_jsonl: Mapping[str, Any] = field(default_factory=dict)
    written_paths: tuple[str, ...] = ()
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        jurisdiction = _required_string("jurisdiction", self.jurisdiction)
        report_kind = _required_string("report_kind", self.report_kind)
        schema = _required_string("schema", self.schema)
        truth_claim = _required_string("truth_claim", self.truth_claim)
        object.__setattr__(self, "jurisdiction", jurisdiction)
        object.__setattr__(self, "report_kind", report_kind)
        object.__setattr__(self, "schema", schema)
        object.__setattr__(self, "truth_claim", truth_claim)
        _require_bool("replay_claims", self.replay_claims)
        _require_bool("canonical_effect_claims", self.canonical_effect_claims)
        _require_bool("candidate_effect_claims", self.candidate_effect_claims)
        _require_bool("dry_run_claims", self.dry_run_claims)
        _require_bool("agreement_claims", self.agreement_claims)
        _require_bool("rows_truncated", self.rows_truncated)
        _require_mapping("summary", self.summary)
        _require_mapping("filters", self.filters)
        _require_mapping("filtered_summary", self.filtered_summary)
        _require_mapping("evidence_jsonl", self.evidence_jsonl)
        _require_mapping("detail", self.detail)
        rows = tuple(self.rows)
        if not all(isinstance(row, Mapping) for row in rows):
            raise ValueError("EvidenceSurfaceReport.rows must contain mappings")
        object.__setattr__(self, "rows", rows)
        object.__setattr__(self, "summary", freeze_mapping(self.summary))
        object.__setattr__(self, "filters", freeze_mapping(self.filters))
        object.__setattr__(
            self,
            "filtered_summary",
            freeze_mapping(self.filtered_summary),
        )
        object.__setattr__(self, "evidence_jsonl", freeze_mapping(self.evidence_jsonl))
        object.__setattr__(self, "detail", freeze_mapping(self.detail))
        object.__setattr__(
            self,
            "written_paths",
            tuple(str(item) for item in self.written_paths if str(item)),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = _plain_jsonable(self.detail)
        payload.update(
            {
                "jurisdiction": self.jurisdiction,
                "report_kind": self.report_kind,
                "schema": self.schema,
                "truth_claim": self.truth_claim,
                "replay_claims": self.replay_claims,
                "canonical_effect_claims": self.canonical_effect_claims,
                "candidate_effect_claims": self.candidate_effect_claims,
                "dry_run_claims": self.dry_run_claims,
                "agreement_claims": self.agreement_claims,
                "summary": _plain_jsonable(self.summary),
                "filters": _plain_jsonable(self.filters),
                "filtered_summary": _plain_jsonable(self.filtered_summary),
                "rows": [_plain_jsonable(row) for row in self.rows],
                "rows_truncated": self.rows_truncated,
                "evidence_jsonl": _plain_jsonable(self.evidence_jsonl),
                "written_paths": list(self.written_paths),
            }
        )
        return payload


def _required_string(field_name: str, value: Any) -> str:
    text = str(value or "")
    if not text:
        raise ValueError(f"EvidenceSurfaceReport.{field_name} is required")
    return text


def _require_bool(field_name: str, value: Any) -> None:
    if not isinstance(value, bool):
        raise ValueError(f"EvidenceSurfaceReport.{field_name} must be boolean")


def _require_mapping(field_name: str, value: Any) -> None:
    if not isinstance(value, Mapping):
        raise ValueError(f"EvidenceSurfaceReport.{field_name} must be a mapping")


def _plain_jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain_jsonable(inner) for key, inner in value.items()}
    if isinstance(value, list | tuple):
        return [_plain_jsonable(inner) for inner in value]
    if isinstance(value, set | frozenset):
        return sorted((_plain_jsonable(inner) for inner in value), key=repr)
    return value

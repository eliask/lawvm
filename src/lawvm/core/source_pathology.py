"""Shared source-pathology proof-surface projections."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping, cast

from lawvm.core.compile_result import SourcePathology
from lawvm.core.evidence_surface_report import EvidenceSurfaceReport
from lawvm.core.frozen_values import freeze_mapping


_SOURCE_PATHOLOGY_FORBIDDEN_SHORTCUTS: tuple[str, ...] = (
    "source_pathology_as_source_truth",
    "source_pathology_as_replay_authorization",
    "source_pathology_as_mutation_boundary_proof",
    "source_pathology_as_target_widening",
)


@dataclass(frozen=True, slots=True)
class SourcePathologyProjection:
    """Read-model row for a source pathology.

    This object classifies and carries evidence for a source pathology. It does
    not repair source text, authorize replay, or decide the manual/frontier lane
    by itself.
    """

    pathology_id: str
    jurisdiction: str
    source_artifact_id: str
    pathology_kind: str
    affected_phase: str
    blocks_execution: bool
    suggested_lane: str
    message: str = ""
    target_unit_kind: str = ""
    target_label: str = ""
    evidence_refs: tuple[str, ...] = ()
    safe_default: str = "preserve_uncertainty_and_do_not_promote_pathology_to_replay"
    forbidden_shortcuts: tuple[str, ...] = _SOURCE_PATHOLOGY_FORBIDDEN_SHORTCUTS
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name, value in (
            ("pathology_id", self.pathology_id),
            ("jurisdiction", self.jurisdiction),
            ("source_artifact_id", self.source_artifact_id),
            ("pathology_kind", self.pathology_kind),
            ("affected_phase", self.affected_phase),
            ("suggested_lane", self.suggested_lane),
            ("safe_default", self.safe_default),
        ):
            object.__setattr__(
                self,
                field_name,
                _required_string(f"SourcePathologyProjection.{field_name}", value),
            )
        if not isinstance(self.blocks_execution, bool):
            raise ValueError("SourcePathologyProjection.blocks_execution must be boolean")
        object.__setattr__(self, "message", str(self.message or ""))
        object.__setattr__(self, "target_unit_kind", str(self.target_unit_kind or ""))
        object.__setattr__(self, "target_label", str(self.target_label or ""))
        object.__setattr__(
            self,
            "evidence_refs",
            _string_tuple("SourcePathologyProjection.evidence_refs", self.evidence_refs),
        )
        object.__setattr__(
            self,
            "forbidden_shortcuts",
            _string_tuple("SourcePathologyProjection.forbidden_shortcuts", self.forbidden_shortcuts),
        )
        if not self.forbidden_shortcuts:
            raise ValueError("SourcePathologyProjection.forbidden_shortcuts is required")
        if not isinstance(self.detail, Mapping):
            raise ValueError("SourcePathologyProjection.detail must be a mapping")
        object.__setattr__(self, "detail", freeze_mapping(self.detail))

    def to_dict(self) -> dict[str, Any]:
        return {
            "pathology_id": self.pathology_id,
            "jurisdiction": self.jurisdiction,
            "source_artifact_id": self.source_artifact_id,
            "pathology_kind": self.pathology_kind,
            "affected_phase": self.affected_phase,
            "blocks_execution": self.blocks_execution,
            "suggested_lane": self.suggested_lane,
            "message": self.message,
            "target_unit_kind": self.target_unit_kind,
            "target_label": self.target_label,
            "evidence_refs": list(self.evidence_refs),
            "safe_default": self.safe_default,
            "forbidden_shortcuts": list(self.forbidden_shortcuts),
            "detail": _plain_jsonable(self.detail),
        }


def source_pathology_projection(
    pathology: SourcePathology | Mapping[str, Any],
    *,
    jurisdiction: str,
    affected_phase: str,
    suggested_lane: str,
    blocks_execution: bool,
    pathology_id: str = "",
    evidence_refs: tuple[str, ...] = (),
) -> SourcePathologyProjection:
    """Project an existing source-pathology carrier into the shared row shape."""

    row = _pathology_row(pathology)
    return SourcePathologyProjection(
        pathology_id=pathology_id or _stable_pathology_id(row, jurisdiction=jurisdiction),
        jurisdiction=jurisdiction,
        source_artifact_id=str(row.get("source_statute") or "unknown"),
        pathology_kind=str(row.get("code") or "UNKNOWN_SOURCE_PATHOLOGY"),
        affected_phase=affected_phase,
        blocks_execution=blocks_execution,
        suggested_lane=suggested_lane,
        message=str(row.get("message") or ""),
        target_unit_kind=str(row.get("target_unit_kind") or ""),
        target_label=str(row.get("target_label") or ""),
        evidence_refs=evidence_refs,
        detail={
            "source_pathology": row,
        },
    )


def source_pathology_evidence_report(
    pathologies: (
        SourcePathologyProjection
        | SourcePathology
        | Mapping[str, Any]
        | tuple[SourcePathologyProjection | SourcePathology | Mapping[str, Any], ...]
    ),
    *,
    jurisdiction: str,
    affected_phase: str = "unknown",
    suggested_lane: str = "source_pathology",
    blocks_execution: bool = True,
    report_kind: str = "source_pathology",
) -> EvidenceSurfaceReport:
    """Project source pathologies into a shared passive evidence report."""

    projections = tuple(
        _projection(
            item,
            jurisdiction=jurisdiction,
            affected_phase=affected_phase,
            suggested_lane=suggested_lane,
            blocks_execution=blocks_execution,
        )
        for item in _pathology_sequence(pathologies)
    )
    rows = tuple(_source_pathology_report_row(projection) for projection in projections)
    kind_counts = _counts(projection.pathology_kind for projection in projections)
    phase_counts = _counts(projection.affected_phase for projection in projections)
    lane_counts = _counts(projection.suggested_lane for projection in projections)
    blocking_count = sum(1 for projection in projections if projection.blocks_execution)
    summary = {
        "source_pathology_count": len(projections),
        "blocking_source_pathology_count": blocking_count,
        "nonblocking_source_pathology_count": len(projections) - blocking_count,
        "pathology_kind_counts": kind_counts,
        "affected_phase_counts": phase_counts,
        "suggested_lane_counts": lane_counts,
        "claim_flags": {
            "replay_claims": False,
            "canonical_effect_claims": False,
            "candidate_effect_claims": False,
            "dry_run_claims": False,
            "agreement_claims": False,
        },
    }
    return EvidenceSurfaceReport(
        jurisdiction=jurisdiction,
        report_kind=report_kind,
        schema="lawvm.source_pathology_report.v1",
        truth_claim="source pathology projection only",
        replay_claims=False,
        canonical_effect_claims=False,
        candidate_effect_claims=False,
        dry_run_claims=False,
        agreement_claims=False,
        summary=summary,
        filters={
            "affected_phase": affected_phase,
            "suggested_lane": suggested_lane,
        },
        filtered_summary=summary,
        rows=rows,
        rows_truncated=False,
        detail={
            "safe_default": "record_source_pathology_without_repair_or_replay_authority",
            "forbidden_shortcuts": _SOURCE_PATHOLOGY_FORBIDDEN_SHORTCUTS,
            "included_surfaces": ("source_pathology",),
        },
    )


def _projection(
    item: SourcePathologyProjection | SourcePathology | Mapping[str, Any],
    *,
    jurisdiction: str,
    affected_phase: str,
    suggested_lane: str,
    blocks_execution: bool,
) -> SourcePathologyProjection:
    if isinstance(item, SourcePathologyProjection):
        return item
    return source_pathology_projection(
        item,
        jurisdiction=jurisdiction,
        affected_phase=affected_phase,
        suggested_lane=suggested_lane,
        blocks_execution=blocks_execution,
    )


def _source_pathology_report_row(projection: SourcePathologyProjection) -> dict[str, Any]:
    data = projection.to_dict()
    return {
        "surface": "source_pathology",
        "row_id": data["pathology_id"],
        "subject_id": data["source_artifact_id"],
        "row_status": "blocking" if data["blocks_execution"] else "reported",
        "replay_authorized": False,
        **data,
    }


def _pathology_sequence(
    value: (
        SourcePathologyProjection
        | SourcePathology
        | Mapping[str, Any]
        | tuple[SourcePathologyProjection | SourcePathology | Mapping[str, Any], ...]
    ),
) -> tuple[SourcePathologyProjection | SourcePathology | Mapping[str, Any], ...]:
    if isinstance(value, SourcePathologyProjection | SourcePathology) or isinstance(value, Mapping):
        return (cast(SourcePathologyProjection | SourcePathology | Mapping[str, Any], value),)
    return tuple(value)


def _pathology_row(pathology: SourcePathology | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(pathology, SourcePathology):
        return {
            "code": pathology.code,
            "message": pathology.message,
            "source_statute": pathology.source_statute,
            "target_unit_kind": str(pathology.target_unit_kind or ""),
            "target_label": pathology.target_label,
            "detail": _plain_jsonable(pathology.detail),
        }
    detail = pathology.get("detail", {})
    return {
        "code": str(pathology.get("code") or ""),
        "message": str(pathology.get("message") or ""),
        "source_statute": str(pathology.get("source_statute") or ""),
        "target_unit_kind": str(pathology.get("target_unit_kind") or ""),
        "target_label": str(pathology.get("target_label") or ""),
        "detail": _plain_jsonable(detail) if isinstance(detail, Mapping) else {},
    }


def _stable_pathology_id(row: Mapping[str, Any], *, jurisdiction: str) -> str:
    payload = {
        "jurisdiction": jurisdiction,
        "code": str(row.get("code") or ""),
        "source_statute": str(row.get("source_statute") or ""),
        "target_unit_kind": str(row.get("target_unit_kind") or ""),
        "target_label": str(row.get("target_label") or ""),
        "detail": _plain_jsonable(row.get("detail")),
    }
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=True, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    source = str(row.get("source_statute") or "unknown").replace("/", "_")
    code = str(row.get("code") or "unknown").lower()
    return f"{jurisdiction}:source-pathology:{source}:{code}:{digest}"


def _required_string(field_name: str, value: Any) -> str:
    text = str(value or "")
    if not text:
        raise ValueError(f"{field_name} is required")
    return text


def _string_tuple(field_name: str, values: tuple[str, ...]) -> tuple[str, ...]:
    if isinstance(values, str):
        raise ValueError(f"{field_name} must be a tuple of strings, not a string")
    normalized = tuple(str(item) for item in values)
    if not all(normalized):
        raise ValueError(f"{field_name} must contain non-empty strings")
    return normalized


def _counts(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value or "__blank__")
        counts[key] = counts.get(key, 0) + 1
    return counts


def _plain_jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain_jsonable(inner) for key, inner in value.items()}
    if isinstance(value, list | tuple):
        return tuple(_plain_jsonable(inner) for inner in value)
    if isinstance(value, set | frozenset):
        return tuple(sorted((_plain_jsonable(inner) for inner in value), key=repr))
    return value

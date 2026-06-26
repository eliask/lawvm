"""Shared passive source-unit coverage accounting surface."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, cast

from lawvm.core.evidence_surface_report import EvidenceSurfaceReport
from lawvm.core.frozen_values import freeze_mapping


SourceUnitCoverageStatus = Literal[
    "enumerated",
    "lineage_witnessed",
    "frontier_witnessed",
    "unclassified",
    "blocked",
]

SOURCE_UNIT_ENUMERATED: SourceUnitCoverageStatus = "enumerated"
SOURCE_UNIT_LINEAGE_WITNESSED: SourceUnitCoverageStatus = "lineage_witnessed"
SOURCE_UNIT_FRONTIER_WITNESSED: SourceUnitCoverageStatus = "frontier_witnessed"
SOURCE_UNIT_UNCLASSIFIED: SourceUnitCoverageStatus = "unclassified"
SOURCE_UNIT_BLOCKED: SourceUnitCoverageStatus = "blocked"

_VALID_STATUSES = frozenset(
    {
        SOURCE_UNIT_ENUMERATED,
        SOURCE_UNIT_LINEAGE_WITNESSED,
        SOURCE_UNIT_FRONTIER_WITNESSED,
        SOURCE_UNIT_UNCLASSIFIED,
        SOURCE_UNIT_BLOCKED,
    }
)
_SOURCE_UNIT_COVERAGE_FORBIDDEN_SHORTCUTS: tuple[str, ...] = (
    "source_unit_coverage_as_replay_authorization",
    "source_unit_coverage_as_complete_source_enumeration",
    "lineage_witness_as_source_unit_exhaustiveness_proof",
)


@dataclass(frozen=True, slots=True)
class SourceUnitCoverage:
    """Passive row for one source unit in a declared coverage scope."""

    coverage_id: str
    jurisdiction: str
    source_artifact_id: str
    source_unit_id: str
    owner_phase: str
    coverage_status: SourceUnitCoverageStatus
    unit_family: str
    source_role: str = ""
    source_lane: str = ""
    refs: tuple[str, ...] = ()
    required_proofs: tuple[str, ...] = ()
    safe_default: str = ""
    forbidden_shortcuts: tuple[str, ...] = _SOURCE_UNIT_COVERAGE_FORBIDDEN_SHORTCUTS
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name, value in (
            ("coverage_id", self.coverage_id),
            ("jurisdiction", self.jurisdiction),
            ("source_artifact_id", self.source_artifact_id),
            ("source_unit_id", self.source_unit_id),
            ("owner_phase", self.owner_phase),
            ("coverage_status", self.coverage_status),
            ("unit_family", self.unit_family),
        ):
            object.__setattr__(
                self,
                field_name,
                _required_string(field_name, value),
            )
        if self.coverage_status not in _VALID_STATUSES:
            raise ValueError(
                "SourceUnitCoverage.coverage_status must be one of "
                f"{sorted(_VALID_STATUSES)}"
            )
        object.__setattr__(self, "source_role", str(self.source_role or ""))
        object.__setattr__(self, "source_lane", str(self.source_lane or ""))
        object.__setattr__(self, "refs", _string_tuple("refs", self.refs))
        object.__setattr__(
            self,
            "required_proofs",
            _string_tuple("required_proofs", self.required_proofs),
        )
        if not self.safe_default:
            raise ValueError("SourceUnitCoverage.safe_default is required")
        object.__setattr__(
            self,
            "forbidden_shortcuts",
            _string_tuple("forbidden_shortcuts", self.forbidden_shortcuts),
        )
        if not self.forbidden_shortcuts:
            raise ValueError("SourceUnitCoverage.forbidden_shortcuts is required")
        if not isinstance(self.detail, Mapping):
            raise ValueError("SourceUnitCoverage.detail must be a mapping")
        object.__setattr__(self, "detail", freeze_mapping(self.detail))

    def to_dict(self) -> dict[str, Any]:
        return {
            "coverage_id": self.coverage_id,
            "jurisdiction": self.jurisdiction,
            "source_artifact_id": self.source_artifact_id,
            "source_unit_id": self.source_unit_id,
            "owner_phase": self.owner_phase,
            "coverage_status": self.coverage_status,
            "unit_family": self.unit_family,
            "source_role": self.source_role,
            "source_lane": self.source_lane,
            "refs": list(self.refs),
            "required_proofs": list(self.required_proofs),
            "safe_default": self.safe_default,
            "forbidden_shortcuts": list(self.forbidden_shortcuts),
            "detail": _plain_jsonable(self.detail),
        }


def source_unit_coverage_evidence_report(
    coverage_rows: (
        SourceUnitCoverage
        | Mapping[str, Any]
        | tuple[SourceUnitCoverage | Mapping[str, Any], ...]
    ),
    *,
    jurisdiction: str,
    report_kind: str = "source_unit_coverage",
) -> EvidenceSurfaceReport:
    """Project source-unit coverage rows into a passive evidence report."""

    rows = tuple(
        _source_unit_coverage_mapping(row)
        for row in _coverage_sequence(coverage_rows)
    )
    status_counts = _counts(str(row.get("coverage_status") or "") for row in rows)
    family_counts = _counts(str(row.get("unit_family") or "") for row in rows)
    phase_counts = _counts(str(row.get("owner_phase") or "") for row in rows)
    summary = {
        "source_unit_coverage_count": len(rows),
        "coverage_status_counts": status_counts,
        "unit_family_counts": family_counts,
        "owner_phase_counts": phase_counts,
        "enumerated_count": status_counts.get(SOURCE_UNIT_ENUMERATED, 0),
        "lineage_witnessed_count": status_counts.get(SOURCE_UNIT_LINEAGE_WITNESSED, 0),
        "frontier_witnessed_count": status_counts.get(SOURCE_UNIT_FRONTIER_WITNESSED, 0),
        "unclassified_count": status_counts.get(SOURCE_UNIT_UNCLASSIFIED, 0),
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
        schema="lawvm.source_unit_coverage.v1",
        truth_claim="declared source-unit coverage rows",
        replay_claims=False,
        canonical_effect_claims=False,
        candidate_effect_claims=False,
        dry_run_claims=False,
        agreement_claims=False,
        summary=summary,
        filters={"report_kind": report_kind},
        filtered_summary=summary,
        rows=tuple(_source_unit_coverage_report_row(row) for row in rows),
        detail={
            "safe_default": "treat_source_unit_coverage_as_accounting_not_replay_authority",
            "forbidden_shortcuts": _SOURCE_UNIT_COVERAGE_FORBIDDEN_SHORTCUTS,
            "included_surfaces": ("source_unit_coverage",),
        },
    )


def _coverage_sequence(
    value: (
        SourceUnitCoverage
        | Mapping[str, Any]
        | tuple[SourceUnitCoverage | Mapping[str, Any], ...]
    ),
) -> tuple[SourceUnitCoverage | Mapping[str, Any], ...]:
    if isinstance(value, SourceUnitCoverage) or isinstance(value, Mapping):
        return (cast(SourceUnitCoverage | Mapping[str, Any], value),)
    return tuple(value)


def _source_unit_coverage_mapping(
    value: SourceUnitCoverage | Mapping[str, Any],
) -> Mapping[str, Any]:
    if isinstance(value, SourceUnitCoverage):
        return value.to_dict()
    return SourceUnitCoverage(
        coverage_id=str(value.get("coverage_id") or ""),
        jurisdiction=str(value.get("jurisdiction") or ""),
        source_artifact_id=str(value.get("source_artifact_id") or ""),
        source_unit_id=str(value.get("source_unit_id") or ""),
        owner_phase=str(value.get("owner_phase") or ""),
        coverage_status=cast(SourceUnitCoverageStatus, str(value.get("coverage_status") or "")),
        unit_family=str(value.get("unit_family") or ""),
        source_role=str(value.get("source_role") or ""),
        source_lane=str(value.get("source_lane") or ""),
        refs=_string_tuple("refs", value.get("refs", ())),
        required_proofs=_string_tuple("required_proofs", value.get("required_proofs", ())),
        safe_default=str(value.get("safe_default") or ""),
        forbidden_shortcuts=_string_tuple(
            "forbidden_shortcuts",
            value.get("forbidden_shortcuts", _SOURCE_UNIT_COVERAGE_FORBIDDEN_SHORTCUTS),
        ),
        detail=_mapping_or_empty(value.get("detail")),
    ).to_dict()


def _source_unit_coverage_report_row(row: Mapping[str, Any]) -> dict[str, Any]:
    coverage_id = str(row.get("coverage_id") or "")
    return {
        **dict(row),
        "surface": "source_unit_coverage",
        "row_id": coverage_id,
        "subject_id": str(row.get("source_unit_id") or coverage_id),
        "row_status": str(row.get("coverage_status") or ""),
        "forbidden_shortcuts": list(row.get("forbidden_shortcuts") or ()),
    }


def _required_string(field_name: str, value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"SourceUnitCoverage.{field_name} is required")
    return text


def _string_tuple(field_name: str, value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,) if value else ()
    try:
        return tuple(str(item) for item in value if str(item))
    except TypeError as exc:
        raise ValueError(f"SourceUnitCoverage.{field_name} must be iterable") from exc


def _mapping_or_empty(value: Any) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("SourceUnitCoverage.detail must be a mapping")
    return value


def _counts(values: tuple[str, ...] | Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value or "__blank__")
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _plain_jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain_jsonable(inner) for key, inner in value.items()}
    if isinstance(value, list | tuple):
        return [_plain_jsonable(inner) for inner in value]
    if isinstance(value, set | frozenset):
        return sorted((_plain_jsonable(inner) for inner in value), key=repr)
    return value

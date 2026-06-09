"""Shared agreement residual projection contract."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, cast

from lawvm.core.evidence_surface_report import EvidenceSurfaceReport
from lawvm.core.frozen_values import freeze_mapping


AgreementResidualFamily = Literal[
    "agreement",
    "accepted_non_executable_frontier",
    "error",
    "extent_branch_mismatch",
    "non_commensurable_surface",
    "oracle_editorial_pathology",
    "replay_bug",
    "source_footing_gap",
    "source_pathology",
    "target_recovery_mismatch",
    "temporal_mismatch",
    "topology_granularity_mismatch",
    "unknown",
]

AgreementResidualStatus = Literal[
    "agrees",
    "blocked",
    "frontier",
    "residual",
    "error",
]

MaterializationKind = Literal[
    "legal_text_state",
    "official_consolidation_view",
    "editorial_display_view",
    "proposed_future_branch",
    "source_as_enacted",
    "unknown",
]

_VALID_FAMILIES = frozenset(AgreementResidualFamily.__args__)
_VALID_STATUSES = frozenset(AgreementResidualStatus.__args__)
_VALID_MATERIALIZATION_KINDS = frozenset(MaterializationKind.__args__)


@dataclass(frozen=True, slots=True)
class AgreementResidual:
    """Classify replay/materialization disagreement with an agreement surface.

    This is an adjudication/reporting object. It never authorizes replay and
    never turns an oracle surface into source truth.
    """

    residual_id: str
    jurisdiction: str
    agreement_surface: str
    family: AgreementResidualFamily
    status: AgreementResidualStatus
    owner_phase: str
    rule_id: str
    source_artifact_id: str = ""
    replay_count: int = 0
    oracle_count: int = 0
    missing_proofs: tuple[str, ...] = ()
    safe_default: str = ""
    forbidden_shortcuts: tuple[str, ...] = ()
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "residual_id",
            _required_string("residual_id", self.residual_id),
        )
        object.__setattr__(
            self,
            "jurisdiction",
            _required_string("jurisdiction", self.jurisdiction),
        )
        object.__setattr__(
            self,
            "agreement_surface",
            _required_string("agreement_surface", self.agreement_surface),
        )
        family = _required_string("family", self.family)
        if family not in _VALID_FAMILIES:
            raise ValueError(
                "AgreementResidual.family must be one of "
                f"{sorted(_VALID_FAMILIES)}"
            )
        status = _required_string("status", self.status)
        if status not in _VALID_STATUSES:
            raise ValueError(
                "AgreementResidual.status must be one of "
                f"{sorted(_VALID_STATUSES)}"
            )
        object.__setattr__(self, "family", family)
        object.__setattr__(self, "status", status)
        object.__setattr__(
            self,
            "owner_phase",
            _required_string("owner_phase", self.owner_phase),
        )
        object.__setattr__(self, "rule_id", _required_string("rule_id", self.rule_id))
        object.__setattr__(self, "source_artifact_id", str(self.source_artifact_id or ""))
        _require_nonnegative_int("replay_count", self.replay_count)
        _require_nonnegative_int("oracle_count", self.oracle_count)
        object.__setattr__(
            self,
            "missing_proofs",
            _string_tuple("missing_proofs", self.missing_proofs),
        )
        object.__setattr__(self, "safe_default", str(self.safe_default or ""))
        if not self.safe_default:
            raise ValueError("AgreementResidual.safe_default is required")
        object.__setattr__(
            self,
            "forbidden_shortcuts",
            _string_tuple("forbidden_shortcuts", self.forbidden_shortcuts),
        )
        if not self.forbidden_shortcuts:
            raise ValueError("AgreementResidual.forbidden_shortcuts is required")
        if not isinstance(self.detail, Mapping):
            raise ValueError("AgreementResidual.detail must be a mapping")
        object.__setattr__(self, "detail", freeze_mapping(self.detail))

    def to_dict(self) -> dict[str, Any]:
        return {
            "residual_id": self.residual_id,
            "jurisdiction": self.jurisdiction,
            "agreement_surface": self.agreement_surface,
            "family": self.family,
            "status": self.status,
            "owner_phase": self.owner_phase,
            "rule_id": self.rule_id,
            "source_artifact_id": self.source_artifact_id,
            "replay_count": self.replay_count,
            "oracle_count": self.oracle_count,
            "missing_proofs": list(self.missing_proofs),
            "safe_default": self.safe_default,
            "forbidden_shortcuts": list(self.forbidden_shortcuts),
            "detail": _plain_jsonable(self.detail),
        }


@dataclass(frozen=True, slots=True)
class AgreementSurface:
    """Comparison surface over replay/materialization and an external witness."""

    surface_id: str
    jurisdiction: str
    agreement_surface: str
    materialization_id: str
    comparison_target_id: str
    comparison_kind: str
    materialization_kind: MaterializationKind = "legal_text_state"
    comparison_materialization_kind: MaterializationKind = "unknown"
    profile_id: str = ""
    exact_ratio: float | None = None
    residuals: tuple[AgreementResidual, ...] = ()

    def __post_init__(self) -> None:
        for field_name in (
            "surface_id",
            "jurisdiction",
            "agreement_surface",
            "materialization_id",
            "comparison_target_id",
            "comparison_kind",
        ):
            object.__setattr__(
                self,
                field_name,
                _required_string(field_name, getattr(self, field_name)),
            )
        materialization_kind = _materialization_kind(
            "materialization_kind",
            self.materialization_kind,
        )
        comparison_materialization_kind = _materialization_kind(
            "comparison_materialization_kind",
            self.comparison_materialization_kind,
        )
        object.__setattr__(self, "materialization_kind", materialization_kind)
        object.__setattr__(
            self,
            "comparison_materialization_kind",
            comparison_materialization_kind,
        )
        object.__setattr__(self, "profile_id", str(self.profile_id or ""))
        if self.exact_ratio is not None:
            if not isinstance(self.exact_ratio, int | float) or isinstance(self.exact_ratio, bool):
                raise ValueError("AgreementSurface.exact_ratio must be numeric when present")
            if self.exact_ratio < 0 or self.exact_ratio > 1:
                raise ValueError("AgreementSurface.exact_ratio must be between 0 and 1")
            object.__setattr__(self, "exact_ratio", float(self.exact_ratio))
        residuals = tuple(self.residuals)
        if not all(isinstance(residual, AgreementResidual) for residual in residuals):
            raise ValueError("AgreementSurface.residuals must contain AgreementResidual objects")
        object.__setattr__(self, "residuals", residuals)

    def to_dict(self) -> dict[str, Any]:
        return {
            "surface_id": self.surface_id,
            "jurisdiction": self.jurisdiction,
            "agreement_surface": self.agreement_surface,
            "materialization_id": self.materialization_id,
            "comparison_target_id": self.comparison_target_id,
            "comparison_kind": self.comparison_kind,
            "materialization_kind": self.materialization_kind,
            "comparison_materialization_kind": self.comparison_materialization_kind,
            "profile_id": self.profile_id,
            "exact_ratio": self.exact_ratio,
            "residuals": [residual.to_dict() for residual in self.residuals],
        }


def agreement_surface_from_residuals(
    residuals: tuple[AgreementResidual | Mapping[str, Any], ...],
    *,
    jurisdiction: str,
    agreement_surface: str,
    materialization_id: str,
    comparison_target_id: str,
    comparison_kind: str,
    materialization_kind: MaterializationKind = "legal_text_state",
    comparison_materialization_kind: MaterializationKind = "unknown",
    surface_id: str = "",
    profile_id: str = "",
    exact_ratio: float | None = None,
) -> AgreementSurface:
    """Build a typed agreement surface from already-classified residual rows."""

    typed_residuals = tuple(_residual(row) for row in residuals)
    default_surface_id = (
        f"{jurisdiction}:agreement:{agreement_surface}:"
        f"{materialization_id}:{comparison_target_id}"
    )
    return AgreementSurface(
        surface_id=surface_id or default_surface_id,
        jurisdiction=jurisdiction,
        agreement_surface=agreement_surface,
        materialization_id=materialization_id,
        comparison_target_id=comparison_target_id,
        comparison_kind=comparison_kind,
        materialization_kind=materialization_kind,
        comparison_materialization_kind=comparison_materialization_kind,
        profile_id=profile_id,
        exact_ratio=exact_ratio,
        residuals=typed_residuals,
    )


def agreement_surface_evidence_report(
    surface: AgreementSurface | Mapping[str, Any],
    *,
    report_kind: str = "agreement_surface",
) -> EvidenceSurfaceReport:
    """Project an agreement surface into the shared evidence-report envelope."""

    typed_surface = _agreement_surface(surface)
    rows = tuple(_agreement_residual_report_row(residual) for residual in typed_surface.residuals)
    summary = {
        "agreement_surface_count": 1,
        "agreement_residual_count": len(typed_surface.residuals),
        "residual_family_counts": _counts(residual.family for residual in typed_surface.residuals),
        "residual_status_counts": _counts(residual.status for residual in typed_surface.residuals),
        "agreement_surface": typed_surface.agreement_surface,
        "comparison_kind": typed_surface.comparison_kind,
        "materialization_kind": typed_surface.materialization_kind,
        "comparison_materialization_kind": typed_surface.comparison_materialization_kind,
        "exact_ratio": typed_surface.exact_ratio,
        "claim_flags": {
            "replay_claims": False,
            "canonical_effect_claims": False,
            "candidate_effect_claims": False,
            "dry_run_claims": False,
            "agreement_claims": True,
        },
    }
    return EvidenceSurfaceReport(
        jurisdiction=typed_surface.jurisdiction,
        report_kind=report_kind,
        schema="lawvm.agreement_surface_report.v1",
        truth_claim="agreement residual classification",
        replay_claims=False,
        canonical_effect_claims=False,
        candidate_effect_claims=False,
        dry_run_claims=False,
        agreement_claims=True,
        summary=summary,
        filters={
            "agreement_surface": typed_surface.agreement_surface,
            "comparison_kind": typed_surface.comparison_kind,
            "materialization_kind": typed_surface.materialization_kind,
            "comparison_materialization_kind": typed_surface.comparison_materialization_kind,
            "profile": typed_surface.profile_id,
        },
        filtered_summary=summary,
        rows=rows,
        rows_truncated=False,
        detail={
            "safe_default": "classify_agreement_without_rewriting_materialization_or_oracle",
            "forbidden_shortcuts": (
                "agreement_surface_as_replay_authorization",
                "agreement_residual_as_mutation_instruction",
                "oracle_score_as_source_truth",
            ),
            "agreement_surface": typed_surface.to_dict(),
        },
    )


def _agreement_surface(surface: AgreementSurface | Mapping[str, Any]) -> AgreementSurface:
    if isinstance(surface, AgreementSurface):
        return surface
    residual_rows = surface.get("residuals", ())
    if not isinstance(residual_rows, tuple | list):
        residual_rows = ()
    return agreement_surface_from_residuals(
        tuple(row for row in residual_rows if isinstance(row, AgreementResidual | Mapping)),
        jurisdiction=str(surface.get("jurisdiction") or ""),
        agreement_surface=str(surface.get("agreement_surface") or ""),
        materialization_id=str(surface.get("materialization_id") or ""),
        comparison_target_id=str(surface.get("comparison_target_id") or ""),
        comparison_kind=str(surface.get("comparison_kind") or ""),
        materialization_kind=cast(MaterializationKind, str(surface.get("materialization_kind") or "legal_text_state")),
        comparison_materialization_kind=cast(MaterializationKind, str(surface.get("comparison_materialization_kind") or "unknown")),
        surface_id=str(surface.get("surface_id") or ""),
        profile_id=str(surface.get("profile_id") or ""),
        exact_ratio=surface.get("exact_ratio") if surface.get("exact_ratio") is not None else None,
    )


def _residual(row: AgreementResidual | Mapping[str, Any]) -> AgreementResidual:
    if isinstance(row, AgreementResidual):
        return row
    return AgreementResidual(
        residual_id=str(row.get("residual_id") or ""),
        jurisdiction=str(row.get("jurisdiction") or ""),
        agreement_surface=str(row.get("agreement_surface") or ""),
        family=cast(AgreementResidualFamily, str(row.get("family") or "")),
        status=cast(AgreementResidualStatus, str(row.get("status") or "")),
        owner_phase=str(row.get("owner_phase") or ""),
        rule_id=str(row.get("rule_id") or ""),
        source_artifact_id=str(row.get("source_artifact_id") or ""),
        replay_count=int(row.get("replay_count") or 0),
        oracle_count=int(row.get("oracle_count") or 0),
        missing_proofs=tuple(str(item) for item in _sequence(row.get("missing_proofs"))),
        safe_default=str(row.get("safe_default") or ""),
        forbidden_shortcuts=tuple(str(item) for item in _sequence(row.get("forbidden_shortcuts"))),
        detail=dict(row.get("detail") or {}) if isinstance(row.get("detail"), Mapping) else {},
    )


def _agreement_residual_report_row(residual: AgreementResidual) -> dict[str, Any]:
    data = residual.to_dict()
    return {
        "surface": "agreement_residual",
        "row_id": residual.residual_id,
        "subject_id": residual.source_artifact_id or residual.residual_id,
        "status": residual.status,
        "replay_authorized": False,
        **data,
    }


def _required_string(field_name: str, value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"AgreementResidual.{field_name} is required")
    return text


def _materialization_kind(field_name: str, value: Any) -> MaterializationKind:
    text = _required_string(field_name, value)
    if text not in _VALID_MATERIALIZATION_KINDS:
        raise ValueError(
            "AgreementSurface.materialization kind must be one of "
            f"{sorted(_VALID_MATERIALIZATION_KINDS)}"
        )
    return cast(MaterializationKind, text)


def _require_nonnegative_int(field_name: str, value: Any) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"AgreementResidual.{field_name} must be an integer")
    if value < 0:
        raise ValueError(f"AgreementResidual.{field_name} must be non-negative")


def _string_tuple(field_name: str, values: Any) -> tuple[str, ...]:
    if isinstance(values, str) or not isinstance(values, tuple):
        raise ValueError(f"AgreementResidual.{field_name} must be a tuple")
    return tuple(str(value) for value in values if str(value))


def _sequence(value: Any) -> tuple[Any, ...]:
    if isinstance(value, str):
        return (value,) if value else ()
    if isinstance(value, list | tuple):
        return tuple(value)
    return ()


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
        return [_plain_jsonable(inner) for inner in value]
    if isinstance(value, set | frozenset):
        return sorted((_plain_jsonable(inner) for inner in value), key=repr)
    return value

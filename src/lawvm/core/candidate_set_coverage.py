"""Shared evidence contract for bounded candidate sets."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, cast

from lawvm.core.evidence_surface_report import EvidenceSurfaceReport
from lawvm.core.frozen_values import freeze_mapping


CandidateSetCompletenessStatus = Literal[
    "complete",
    "partial",
    "truncated",
    "unavailable",
    "rejected",
]

CANDIDATE_SET_COMPLETE: CandidateSetCompletenessStatus = "complete"
CANDIDATE_SET_PARTIAL: CandidateSetCompletenessStatus = "partial"
CANDIDATE_SET_TRUNCATED: CandidateSetCompletenessStatus = "truncated"
CANDIDATE_SET_UNAVAILABLE: CandidateSetCompletenessStatus = "unavailable"
CANDIDATE_SET_REJECTED: CandidateSetCompletenessStatus = "rejected"

_VALID_COMPLETENESS_STATUSES = frozenset(
    {
        CANDIDATE_SET_COMPLETE,
        CANDIDATE_SET_PARTIAL,
        CANDIDATE_SET_TRUNCATED,
        CANDIDATE_SET_UNAVAILABLE,
        CANDIDATE_SET_REJECTED,
    }
)
_CANDIDATE_SET_REPORT_FORBIDDEN_SHORTCUTS: tuple[str, ...] = (
    "candidate_set_coverage_as_replay_authorization",
    "candidate_set_coverage_as_source_cue_exhaustiveness_proof_without_declared_scope",
    "candidate_set_completeness_as_target_uniqueness_proof",
)
_RESERVED_DETAIL_KEYS = frozenset(
    {
        "scope_id",
        "candidate_set_kind",
        "phase",
        "rule_id",
        "reason",
        "completeness_status",
        "candidate_count",
        "candidate_ids",
        "missing_candidate_count",
        "selected_candidate_ids",
        "blocker_counts",
        "blocker_families",
        "next_promotion_allowed",
        "next_promotion_requires",
    }
)


@dataclass(frozen=True, slots=True)
class CandidateSetCoverage:
    """Evidence envelope for a bounded candidate set.

    The certificate describes completeness for a declared scope. It does not
    make candidates executable and does not authorize replay.
    """

    scope_id: str
    candidate_set_kind: str
    phase: str
    rule_id: str
    reason: str
    completeness_status: CandidateSetCompletenessStatus
    candidate_count: int
    candidate_ids: tuple[str, ...] = ()
    missing_candidate_count: int = 0
    selected_candidate_ids: tuple[str, ...] = ()
    blocker_counts: Mapping[str, int] = field(default_factory=dict)
    blocker_families: tuple[str, ...] = ()
    next_promotion_allowed: bool = False
    next_promotion_requires: tuple[str, ...] = ()
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        scope_id = _required_string("scope_id", self.scope_id)
        candidate_set_kind = _required_string("candidate_set_kind", self.candidate_set_kind)
        phase = _required_string("phase", self.phase)
        rule_id = _required_string("rule_id", self.rule_id)
        reason = _required_string("reason", self.reason)
        status = _required_string("completeness_status", self.completeness_status)
        if status not in _VALID_COMPLETENESS_STATUSES:
            raise ValueError(
                "CandidateSetCoverage.completeness_status must be one of "
                f"{sorted(_VALID_COMPLETENESS_STATUSES)}"
            )
        object.__setattr__(self, "scope_id", scope_id)
        object.__setattr__(self, "candidate_set_kind", candidate_set_kind)
        object.__setattr__(self, "phase", phase)
        object.__setattr__(self, "rule_id", rule_id)
        object.__setattr__(self, "reason", reason)
        object.__setattr__(self, "completeness_status", status)
        _require_nonnegative_int("candidate_count", self.candidate_count)
        _require_nonnegative_int("missing_candidate_count", self.missing_candidate_count)
        if not isinstance(self.next_promotion_allowed, bool):
            raise ValueError("CandidateSetCoverage.next_promotion_allowed must be boolean")
        candidate_ids = _string_tuple("candidate_ids", self.candidate_ids)
        selected_ids = _string_tuple("selected_candidate_ids", self.selected_candidate_ids)
        blocker_families = _string_tuple("blocker_families", self.blocker_families)
        next_requires = _string_tuple("next_promotion_requires", self.next_promotion_requires)
        if self.candidate_count < len(candidate_ids):
            raise ValueError("CandidateSetCoverage.candidate_count must cover candidate_ids")
        if candidate_ids and not set(selected_ids).issubset(set(candidate_ids)):
            raise ValueError(
                "CandidateSetCoverage.selected_candidate_ids must be a subset of candidate_ids"
            )
        if status == CANDIDATE_SET_COMPLETE and self.missing_candidate_count != 0:
            raise ValueError(
                "CandidateSetCoverage(status='complete') requires missing_candidate_count=0"
            )
        if self.next_promotion_allowed and status != CANDIDATE_SET_COMPLETE:
            raise ValueError(
                "CandidateSetCoverage.next_promotion_allowed requires complete status"
            )
        blocker_counts = _int_mapping("blocker_counts", self.blocker_counts)
        _reject_reserved_detail_keys(self.detail)
        object.__setattr__(self, "candidate_ids", candidate_ids)
        object.__setattr__(self, "selected_candidate_ids", selected_ids)
        object.__setattr__(self, "blocker_counts", blocker_counts)
        object.__setattr__(self, "blocker_families", blocker_families)
        object.__setattr__(self, "next_promotion_requires", next_requires)
        object.__setattr__(self, "detail", freeze_mapping(self.detail))

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "scope_id": self.scope_id,
            "candidate_set_kind": self.candidate_set_kind,
            "phase": self.phase,
            "rule_id": self.rule_id,
            "reason": self.reason,
            "completeness_status": self.completeness_status,
            "candidate_count": self.candidate_count,
            "candidate_ids": list(self.candidate_ids),
            "missing_candidate_count": self.missing_candidate_count,
            "selected_candidate_ids": list(self.selected_candidate_ids),
            "blocker_counts": dict(self.blocker_counts),
            "blocker_families": list(self.blocker_families),
            "next_promotion_allowed": self.next_promotion_allowed,
            "next_promotion_requires": list(self.next_promotion_requires),
        }
        payload.update(_plain_jsonable(self.detail))
        return payload


def candidate_set_evidence_report(
    certificates: (
        CandidateSetCoverage
        | Mapping[str, Any]
        | tuple[CandidateSetCoverage | Mapping[str, Any], ...]
    ),
    *,
    jurisdiction: str,
    report_kind: str = "candidate_set_coverage",
) -> EvidenceSurfaceReport:
    """Project candidate-set certificates into a shared passive report."""

    rows = tuple(_candidate_set_mapping(row) for row in _candidate_set_sequence(certificates))
    report_rows = tuple(_candidate_set_report_row(row) for row in rows)
    status_counts = _counts(str(row.get("completeness_status") or "") for row in rows)
    kind_counts = _counts(str(row.get("candidate_set_kind") or "") for row in rows)
    phase_counts = _counts(str(row.get("phase") or "") for row in rows)
    blocker_family_counts = _counts(
        str(family)
        for row in rows
        for family in _sequence(row.get("blocker_families"))
    )
    summary = {
        "candidate_set_coverage_count": len(rows),
        "candidate_set_status_counts": status_counts,
        "candidate_set_kind_counts": kind_counts,
        "phase_counts": phase_counts,
        "blocker_family_counts": blocker_family_counts,
        "complete_count": status_counts.get(CANDIDATE_SET_COMPLETE, 0),
        "incomplete_count": len(rows) - status_counts.get(CANDIDATE_SET_COMPLETE, 0),
        "candidate_count": sum(int(row.get("candidate_count") or 0) for row in rows),
        "missing_candidate_count": sum(
            int(row.get("missing_candidate_count") or 0)
            for row in rows
        ),
        "next_promotion_allowed_count": sum(
            1 for row in rows if bool(row.get("next_promotion_allowed"))
        ),
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
        schema="lawvm.candidate_set_report.v1",
        truth_claim="bounded candidate-set completeness projections",
        replay_claims=False,
        canonical_effect_claims=False,
        candidate_effect_claims=False,
        dry_run_claims=False,
        agreement_claims=False,
        summary=summary,
        filters={"report_kind": report_kind},
        filtered_summary=summary,
        rows=report_rows,
        detail={
            "safe_default": "treat_candidate_sets_as_completeness_evidence_not_replay_authority",
            "forbidden_shortcuts": _CANDIDATE_SET_REPORT_FORBIDDEN_SHORTCUTS,
            "included_surfaces": ("candidate_set_coverage",),
        },
    )


def _candidate_set_sequence(
    value: (
        CandidateSetCoverage
        | Mapping[str, Any]
        | tuple[CandidateSetCoverage | Mapping[str, Any], ...]
    ),
) -> tuple[CandidateSetCoverage | Mapping[str, Any], ...]:
    if isinstance(value, CandidateSetCoverage) or isinstance(value, Mapping):
        return (cast(CandidateSetCoverage | Mapping[str, Any], value),)
    return tuple(value)


def _candidate_set_mapping(value: CandidateSetCoverage | Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(value, CandidateSetCoverage):
        return value.to_dict()
    # Rehydrate through the dataclass so mapping rows get the same validation.
    return CandidateSetCoverage(
        scope_id=str(value.get("scope_id") or ""),
        candidate_set_kind=str(value.get("candidate_set_kind") or ""),
        phase=str(value.get("phase") or ""),
        rule_id=str(value.get("rule_id") or ""),
        reason=str(value.get("reason") or ""),
        completeness_status=cast(CandidateSetCompletenessStatus, value.get("completeness_status")),
        candidate_count=_required_nonnegative_int_value("candidate_count", value.get("candidate_count")),
        candidate_ids=tuple(str(item) for item in _sequence(value.get("candidate_ids"))),
        missing_candidate_count=_required_nonnegative_int_value(
            "missing_candidate_count",
            value.get("missing_candidate_count", 0),
        ),
        selected_candidate_ids=tuple(
            str(item) for item in _sequence(value.get("selected_candidate_ids"))
        ),
        blocker_counts=_mapping_str_int(value.get("blocker_counts")),
        blocker_families=tuple(str(item) for item in _sequence(value.get("blocker_families"))),
        next_promotion_allowed=bool(value.get("next_promotion_allowed")),
        next_promotion_requires=tuple(
            str(item) for item in _sequence(value.get("next_promotion_requires"))
        ),
        detail=_mapping_detail_without_certificate_keys(value),
    ).to_dict()


def _candidate_set_report_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **dict(row),
        "surface": "candidate_set_coverage",
        "row_id": str(row.get("scope_id") or ""),
        "subject_id": str(row.get("scope_id") or ""),
        "row_status": str(row.get("completeness_status") or ""),
        "proof_ref": str(row.get("rule_id") or ""),
        "forbidden_shortcuts": _CANDIDATE_SET_REPORT_FORBIDDEN_SHORTCUTS,
    }


def _required_string(field_name: str, value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"CandidateSetCoverage.{field_name} is required")
    return text


def _require_nonnegative_int(field_name: str, value: Any) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"CandidateSetCoverage.{field_name} must be an integer")
    if value < 0:
        raise ValueError(f"CandidateSetCoverage.{field_name} must be non-negative")


def _string_tuple(field_name: str, values: Any) -> tuple[str, ...]:
    if isinstance(values, str) or not isinstance(values, tuple):
        raise ValueError(f"CandidateSetCoverage.{field_name} must be a tuple")
    return tuple(str(value) for value in values if str(value))


def _int_mapping(field_name: str, value: Any) -> Mapping[str, int]:
    if not isinstance(value, Mapping):
        raise ValueError(f"CandidateSetCoverage.{field_name} must be a mapping")
    normalized: dict[str, int] = {}
    for key, count in value.items():
        if not isinstance(count, int) or isinstance(count, bool):
            raise ValueError(f"CandidateSetCoverage.{field_name} values must be integers")
        if count < 0:
            raise ValueError(f"CandidateSetCoverage.{field_name} values must be non-negative")
        normalized[str(key)] = count
    return freeze_mapping(normalized)


def _mapping_str_int(value: Any) -> Mapping[str, int]:
    if not isinstance(value, Mapping):
        return {}
    return {
        str(key): _required_nonnegative_int_value(f"blocker_counts.{key}", count)
        for key, count in value.items()
    }


def _mapping_detail_without_certificate_keys(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return {
        str(key): item
        for key, item in value.items()
        if str(key) not in _RESERVED_DETAIL_KEYS
    }


def _required_nonnegative_int_value(field_name: str, value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"CandidateSetCoverage.{field_name} must be an integer")
    if value < 0:
        raise ValueError(f"CandidateSetCoverage.{field_name} must be non-negative")
    return value


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


def _reject_reserved_detail_keys(values: Mapping[str, Any]) -> None:
    if not isinstance(values, Mapping):
        raise ValueError("CandidateSetCoverage.detail must be a mapping")
    overlaps = sorted(_RESERVED_DETAIL_KEYS.intersection(values.keys()))
    if overlaps:
        joined = ", ".join(overlaps)
        raise ValueError(f"candidate set detail must not override certificate keys: {joined}")


def _plain_jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain_jsonable(inner) for key, inner in value.items()}
    if isinstance(value, list | tuple):
        return [_plain_jsonable(inner) for inner in value]
    if isinstance(value, set | frozenset):
        return sorted((_plain_jsonable(inner) for inner in value), key=repr)
    return value

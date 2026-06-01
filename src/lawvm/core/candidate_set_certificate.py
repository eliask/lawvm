"""Shared evidence contract for bounded candidate sets."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping

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
class CandidateSetCertificate:
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
                "CandidateSetCertificate.completeness_status must be one of "
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
            raise ValueError("CandidateSetCertificate.next_promotion_allowed must be boolean")
        candidate_ids = _string_tuple("candidate_ids", self.candidate_ids)
        selected_ids = _string_tuple("selected_candidate_ids", self.selected_candidate_ids)
        blocker_families = _string_tuple("blocker_families", self.blocker_families)
        next_requires = _string_tuple("next_promotion_requires", self.next_promotion_requires)
        if self.candidate_count < len(candidate_ids):
            raise ValueError("CandidateSetCertificate.candidate_count must cover candidate_ids")
        if candidate_ids and not set(selected_ids).issubset(set(candidate_ids)):
            raise ValueError(
                "CandidateSetCertificate.selected_candidate_ids must be a subset of candidate_ids"
            )
        if status == CANDIDATE_SET_COMPLETE and self.missing_candidate_count != 0:
            raise ValueError(
                "CandidateSetCertificate(status='complete') requires missing_candidate_count=0"
            )
        if self.next_promotion_allowed and status != CANDIDATE_SET_COMPLETE:
            raise ValueError(
                "CandidateSetCertificate.next_promotion_allowed requires complete status"
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


def _required_string(field_name: str, value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"CandidateSetCertificate.{field_name} is required")
    return text


def _require_nonnegative_int(field_name: str, value: Any) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"CandidateSetCertificate.{field_name} must be an integer")
    if value < 0:
        raise ValueError(f"CandidateSetCertificate.{field_name} must be non-negative")


def _string_tuple(field_name: str, values: Any) -> tuple[str, ...]:
    if isinstance(values, str) or not isinstance(values, tuple):
        raise ValueError(f"CandidateSetCertificate.{field_name} must be a tuple")
    return tuple(str(value) for value in values if str(value))


def _int_mapping(field_name: str, value: Any) -> Mapping[str, int]:
    if not isinstance(value, Mapping):
        raise ValueError(f"CandidateSetCertificate.{field_name} must be a mapping")
    normalized: dict[str, int] = {}
    for key, count in value.items():
        if not isinstance(count, int) or isinstance(count, bool):
            raise ValueError(f"CandidateSetCertificate.{field_name} values must be integers")
        if count < 0:
            raise ValueError(f"CandidateSetCertificate.{field_name} values must be non-negative")
        normalized[str(key)] = count
    return freeze_mapping(normalized)


def _reject_reserved_detail_keys(values: Mapping[str, Any]) -> None:
    if not isinstance(values, Mapping):
        raise ValueError("CandidateSetCertificate.detail must be a mapping")
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

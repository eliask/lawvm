"""Shared evidence contract for proof obligations at promotion boundaries."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping

from lawvm.core.frozen_values import freeze_mapping


ProofObligationStatus = Literal["complete", "blocked", "unavailable"]

PROOF_OBLIGATION_COMPLETE: ProofObligationStatus = "complete"
PROOF_OBLIGATION_BLOCKED: ProofObligationStatus = "blocked"
PROOF_OBLIGATION_UNAVAILABLE: ProofObligationStatus = "unavailable"

_VALID_STATUSES = frozenset(
    {
        PROOF_OBLIGATION_COMPLETE,
        PROOF_OBLIGATION_BLOCKED,
        PROOF_OBLIGATION_UNAVAILABLE,
    }
)
_RESERVED_DETAIL_KEYS = frozenset(
    {
        "scope_id",
        "phase",
        "rule_id",
        "reason",
        "proof_status",
        "proved_proofs",
        "missing_proofs",
        "blocker_counts",
        "next_promotion_allowed",
        "next_promotion_requires",
    }
)


@dataclass(frozen=True, slots=True)
class ProofObligationCoverage:
    """Evidence envelope for a bounded promotion proof boundary.

    The certificate separates proofs already discharged from proofs still
    missing before a row may be promoted to execution or replay.
    """

    scope_id: str
    phase: str
    rule_id: str
    reason: str
    proof_status: ProofObligationStatus
    proved_proofs: tuple[str, ...] = ()
    missing_proofs: tuple[str, ...] = ()
    blocker_counts: Mapping[str, int] = field(default_factory=dict)
    next_promotion_allowed: bool = False
    next_promotion_requires: tuple[str, ...] = ()
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        scope_id = _required_string("scope_id", self.scope_id)
        phase = _required_string("phase", self.phase)
        rule_id = _required_string("rule_id", self.rule_id)
        reason = _required_string("reason", self.reason)
        status = _required_string("proof_status", self.proof_status)
        if status not in _VALID_STATUSES:
            raise ValueError(
                "ProofObligationCoverage.proof_status must be one of "
                f"{sorted(_VALID_STATUSES)}"
            )
        if not isinstance(self.next_promotion_allowed, bool):
            raise ValueError(
                "ProofObligationCoverage.next_promotion_allowed must be boolean"
            )
        proved = _string_tuple("proved_proofs", self.proved_proofs)
        missing = _string_tuple("missing_proofs", self.missing_proofs)
        next_requires = _string_tuple(
            "next_promotion_requires",
            self.next_promotion_requires,
        )
        if status == PROOF_OBLIGATION_COMPLETE and missing:
            raise ValueError(
                "ProofObligationCoverage(status='complete') requires no missing_proofs"
            )
        if self.next_promotion_allowed and status != PROOF_OBLIGATION_COMPLETE:
            raise ValueError(
                "ProofObligationCoverage.next_promotion_allowed requires complete status"
            )
        blockers = _int_mapping("blocker_counts", self.blocker_counts)
        _reject_reserved_detail_keys(self.detail)
        object.__setattr__(self, "scope_id", scope_id)
        object.__setattr__(self, "phase", phase)
        object.__setattr__(self, "rule_id", rule_id)
        object.__setattr__(self, "reason", reason)
        object.__setattr__(self, "proof_status", status)
        object.__setattr__(self, "proved_proofs", proved)
        object.__setattr__(self, "missing_proofs", missing)
        object.__setattr__(self, "blocker_counts", blockers)
        object.__setattr__(self, "next_promotion_requires", next_requires)
        object.__setattr__(self, "detail", freeze_mapping(self.detail))

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "scope_id": self.scope_id,
            "phase": self.phase,
            "rule_id": self.rule_id,
            "reason": self.reason,
            "proof_status": self.proof_status,
            "proved_proofs": list(self.proved_proofs),
            "missing_proofs": list(self.missing_proofs),
            "blocker_counts": dict(self.blocker_counts),
            "next_promotion_allowed": self.next_promotion_allowed,
            "next_promotion_requires": list(self.next_promotion_requires),
        }
        payload.update(_plain_jsonable(self.detail))
        return payload


def _required_string(field_name: str, value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"ProofObligationCoverage.{field_name} is required")
    return text


def _string_tuple(field_name: str, values: Any) -> tuple[str, ...]:
    if isinstance(values, str) or not isinstance(values, tuple):
        raise ValueError(f"ProofObligationCoverage.{field_name} must be a tuple")
    return tuple(str(value) for value in values if str(value))


def _int_mapping(field_name: str, value: Any) -> Mapping[str, int]:
    if not isinstance(value, Mapping):
        raise ValueError(f"ProofObligationCoverage.{field_name} must be a mapping")
    normalized: dict[str, int] = {}
    for key, count in value.items():
        if not isinstance(count, int) or isinstance(count, bool):
            raise ValueError(
                f"ProofObligationCoverage.{field_name} values must be integers"
            )
        if count < 0:
            raise ValueError(
                f"ProofObligationCoverage.{field_name} values must be non-negative"
            )
        normalized[str(key)] = count
    return freeze_mapping(normalized)


def _reject_reserved_detail_keys(values: Mapping[str, Any]) -> None:
    if not isinstance(values, Mapping):
        raise ValueError("ProofObligationCoverage.detail must be a mapping")
    overlaps = sorted(_RESERVED_DETAIL_KEYS.intersection(values.keys()))
    if overlaps:
        joined = ", ".join(overlaps)
        raise ValueError(
            f"proof obligation detail must not override certificate keys: {joined}"
        )


def _plain_jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain_jsonable(inner) for key, inner in value.items()}
    if isinstance(value, list | tuple):
        return [_plain_jsonable(inner) for inner in value]
    if isinstance(value, set | frozenset):
        return sorted((_plain_jsonable(inner) for inner in value), key=repr)
    return value

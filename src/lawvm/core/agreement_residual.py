"""Shared agreement residual projection contract."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping

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

_VALID_FAMILIES = frozenset(AgreementResidualFamily.__args__)
_VALID_STATUSES = frozenset(AgreementResidualStatus.__args__)


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


def _required_string(field_name: str, value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"AgreementResidual.{field_name} is required")
    return text


def _require_nonnegative_int(field_name: str, value: Any) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"AgreementResidual.{field_name} must be an integer")
    if value < 0:
        raise ValueError(f"AgreementResidual.{field_name} must be non-negative")


def _string_tuple(field_name: str, values: Any) -> tuple[str, ...]:
    if isinstance(values, str) or not isinstance(values, tuple):
        raise ValueError(f"AgreementResidual.{field_name} must be a tuple")
    return tuple(str(value) for value in values if str(value))


def _plain_jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain_jsonable(inner) for key, inner in value.items()}
    if isinstance(value, list | tuple):
        return [_plain_jsonable(inner) for inner in value]
    if isinstance(value, set | frozenset):
        return sorted((_plain_jsonable(inner) for inner in value), key=repr)
    return value

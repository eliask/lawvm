"""Shared execution/replay authorization projection contract."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from lawvm.core.frozen_values import freeze_mapping


@dataclass(frozen=True)
class ExecutionAuthorization:
    """Answer whether a diagnostic/frontier row may mutate legal state.

    This is a reporting/evidence contract.  It does not grant authority by
    itself; phase-local compilers and validators still own the semantics.
    """

    executable: bool
    replay_authorized: bool
    authorization_status: str
    authorization_rule_id: str
    owner_phase: str
    strict_disposition: str
    quirks_disposition: str = "record"
    validator_status: str = ""
    required_proofs: tuple[str, ...] = ()
    safe_default: str = ""
    forbidden_shortcuts: tuple[str, ...] = ()
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "authorization_status", str(self.authorization_status or ""))
        object.__setattr__(self, "authorization_rule_id", str(self.authorization_rule_id or ""))
        object.__setattr__(self, "owner_phase", str(self.owner_phase or ""))
        object.__setattr__(self, "strict_disposition", str(self.strict_disposition or ""))
        object.__setattr__(self, "quirks_disposition", str(self.quirks_disposition or "record"))
        object.__setattr__(self, "validator_status", str(self.validator_status or ""))
        object.__setattr__(self, "required_proofs", tuple(str(item) for item in self.required_proofs))
        object.__setattr__(self, "forbidden_shortcuts", tuple(str(item) for item in self.forbidden_shortcuts))
        if not isinstance(self.detail, Mapping):
            raise ValueError("ExecutionAuthorization.detail must be a mapping")
        object.__setattr__(self, "detail", freeze_mapping(self.detail))
        issues = validate_execution_authorization(self.to_dict())
        if issues:
            raise ValueError("; ".join(issues))

    def to_dict(self) -> dict[str, Any]:
        return {
            "executable": self.executable,
            "replay_authorized": self.replay_authorized,
            "authorization_status": self.authorization_status,
            "authorization_rule_id": self.authorization_rule_id,
            "owner_phase": self.owner_phase,
            "strict_disposition": self.strict_disposition,
            "quirks_disposition": self.quirks_disposition,
            "validator_status": self.validator_status,
            "required_proofs": list(self.required_proofs),
            "safe_default": self.safe_default,
            "forbidden_shortcuts": list(self.forbidden_shortcuts),
            "detail": dict(self.detail),
        }


def validate_execution_authorization(row: Mapping[str, Any]) -> tuple[str, ...]:
    """Validate the shared execution authorization projection."""
    issues: list[str] = []
    for key in (
        "authorization_status",
        "authorization_rule_id",
        "owner_phase",
        "strict_disposition",
        "quirks_disposition",
    ):
        value = row.get(key)
        if not isinstance(value, str) or not value:
            issues.append(f"{key} is required")
    executable = row.get("executable")
    replay_authorized = row.get("replay_authorized")
    if not isinstance(executable, bool):
        issues.append("executable must be a boolean")
    if not isinstance(replay_authorized, bool):
        issues.append("replay_authorized must be a boolean")
    if replay_authorized is True and executable is not True:
        issues.append("replay_authorized requires executable")
    required_proofs = row.get("required_proofs", ())
    if not isinstance(required_proofs, (list, tuple)):
        issues.append("required_proofs must be a sequence")
    elif replay_authorized is False and not required_proofs:
        issues.append("non-authorized row must list required_proofs")
    forbidden_shortcuts = row.get("forbidden_shortcuts", ())
    if not isinstance(forbidden_shortcuts, (list, tuple)):
        issues.append("forbidden_shortcuts must be a sequence")
    if not row.get("safe_default"):
        issues.append("safe_default is required")
    detail = row.get("detail", {})
    if detail is not None and not isinstance(detail, Mapping):
        issues.append("detail must be a mapping when present")
    return tuple(issues)

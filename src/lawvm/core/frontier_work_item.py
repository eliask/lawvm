"""Shared non-executable frontier work-item projection contract."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from lawvm.core.frozen_values import freeze_mapping


@dataclass(frozen=True)
class FrontierWorkItem:
    """Describe useful non-executable work without promoting it to replay."""

    work_item_id: str
    jurisdiction: str
    source_artifact_id: str
    source_unit_id: str
    owner_phase: str
    frontier_family: str
    frontier_status: str
    required_claim_kind: str
    safe_default: str
    source_witness: Mapping[str, Any] = field(default_factory=dict)
    candidate_operation_family: str = ""
    candidate_targets: tuple[str, ...] = ()
    guidance_refs: tuple[str, ...] = ()
    required_validator_checks: tuple[str, ...] = ()
    required_proofs: tuple[str, ...] = ()
    forbidden_shortcuts: tuple[str, ...] = ()
    executable: bool = False
    replay_authorized: bool = False
    authorization_status: str = ""
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in (
            "work_item_id",
            "jurisdiction",
            "source_artifact_id",
            "source_unit_id",
            "owner_phase",
            "frontier_family",
            "frontier_status",
            "required_claim_kind",
            "safe_default",
            "candidate_operation_family",
            "authorization_status",
        ):
            object.__setattr__(
                self,
                field_name,
                str(getattr(self, field_name) or ""),
            )
        object.__setattr__(
            self,
            "candidate_targets",
            tuple(str(item) for item in self.candidate_targets if str(item)),
        )
        object.__setattr__(
            self,
            "guidance_refs",
            tuple(str(item) for item in self.guidance_refs if str(item)),
        )
        object.__setattr__(
            self,
            "required_validator_checks",
            tuple(str(item) for item in self.required_validator_checks if str(item)),
        )
        object.__setattr__(
            self,
            "required_proofs",
            tuple(str(item) for item in self.required_proofs if str(item)),
        )
        object.__setattr__(
            self,
            "forbidden_shortcuts",
            tuple(str(item) for item in self.forbidden_shortcuts if str(item)),
        )
        if not isinstance(self.source_witness, Mapping):
            raise ValueError("FrontierWorkItem.source_witness must be a mapping")
        if not isinstance(self.detail, Mapping):
            raise ValueError("FrontierWorkItem.detail must be a mapping")
        object.__setattr__(self, "source_witness", freeze_mapping(self.source_witness))
        object.__setattr__(self, "detail", freeze_mapping(self.detail))
        issues = validate_frontier_work_item(self.to_dict())
        if issues:
            raise ValueError("; ".join(issues))

    def to_dict(self) -> dict[str, Any]:
        return {
            "work_item_id": self.work_item_id,
            "jurisdiction": self.jurisdiction,
            "source_artifact_id": self.source_artifact_id,
            "source_unit_id": self.source_unit_id,
            "source_witness": dict(self.source_witness),
            "owner_phase": self.owner_phase,
            "frontier_family": self.frontier_family,
            "frontier_status": self.frontier_status,
            "candidate_operation_family": self.candidate_operation_family,
            "candidate_targets": list(self.candidate_targets),
            "guidance_refs": list(self.guidance_refs),
            "required_claim_kind": self.required_claim_kind,
            "required_validator_checks": list(self.required_validator_checks),
            "required_proofs": list(self.required_proofs),
            "safe_default": self.safe_default,
            "forbidden_shortcuts": list(self.forbidden_shortcuts),
            "executable": self.executable,
            "replay_authorized": self.replay_authorized,
            "authorization_status": self.authorization_status,
            "detail": dict(self.detail),
        }


def validate_frontier_work_item(row: Mapping[str, Any]) -> tuple[str, ...]:
    """Validate the shared non-executable frontier work-item projection."""
    issues: list[str] = []
    for key in (
        "work_item_id",
        "jurisdiction",
        "source_artifact_id",
        "source_unit_id",
        "owner_phase",
        "frontier_family",
        "frontier_status",
        "required_claim_kind",
        "safe_default",
    ):
        value = row.get(key)
        if not isinstance(value, str) or not value:
            issues.append(f"{key} is required")
    if row.get("executable") is not False:
        issues.append("frontier work items must be non-executable")
    if row.get("replay_authorized") is not False:
        issues.append("frontier work items must not be replay-authorized")
    if not isinstance(row.get("source_witness", {}), Mapping):
        issues.append("source_witness must be a mapping")
    for key in (
        "candidate_targets",
        "guidance_refs",
        "required_validator_checks",
        "required_proofs",
        "forbidden_shortcuts",
    ):
        if not isinstance(row.get(key, ()), (list, tuple)):
            issues.append(f"{key} must be a sequence")
    if not row.get("authorization_status"):
        issues.append("authorization_status is required")
    if not row.get("required_proofs"):
        issues.append("required_proofs is required")
    if not row.get("forbidden_shortcuts"):
        issues.append("forbidden_shortcuts is required")
    if not isinstance(row.get("detail", {}), Mapping):
        issues.append("detail must be a mapping")
    return tuple(issues)

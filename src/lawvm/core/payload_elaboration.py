"""Shared report-facing payload elaboration contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from lawvm.core.frozen_values import freeze_mapping


@dataclass(frozen=True, slots=True)
class SlotBinding:
    """Projection of one source payload slot to one target/effective slot."""

    binding_id: str
    source_slot_id: str
    target_slot_id: str
    status: str
    operation_id: str = ""
    binding_rule_id: str = ""
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in ("binding_id", "source_slot_id", "target_slot_id", "status"):
            object.__setattr__(
                self,
                field_name,
                _required_string(f"SlotBinding.{field_name}", getattr(self, field_name)),
            )
        object.__setattr__(self, "operation_id", str(self.operation_id or ""))
        object.__setattr__(self, "binding_rule_id", str(self.binding_rule_id or ""))
        if not isinstance(self.detail, Mapping):
            raise ValueError("SlotBinding.detail must be a mapping")
        object.__setattr__(self, "detail", freeze_mapping(self.detail))

    def to_dict(self) -> dict[str, Any]:
        return {
            "binding_id": self.binding_id,
            "source_slot_id": self.source_slot_id,
            "target_slot_id": self.target_slot_id,
            "status": self.status,
            "operation_id": self.operation_id,
            "binding_rule_id": self.binding_rule_id,
            "detail": _plain_jsonable(self.detail),
        }


@dataclass(frozen=True, slots=True)
class SlotBindingReport:
    """Report-facing slot-binding summary for payload elaboration."""

    subject_id: str
    jurisdiction: str
    owner_phase: str
    status: str
    completeness_kind: str
    bindings: tuple[SlotBinding, ...] = ()
    unassigned_source_slots: tuple[str, ...] = ()
    unfilled_target_slots: tuple[str, ...] = ()
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in ("subject_id", "jurisdiction", "owner_phase", "status", "completeness_kind"):
            object.__setattr__(
                self,
                field_name,
                _required_string(f"SlotBindingReport.{field_name}", getattr(self, field_name)),
            )
        bindings = tuple(self.bindings)
        if not all(isinstance(binding, SlotBinding) for binding in bindings):
            raise ValueError("SlotBindingReport.bindings must contain SlotBinding objects")
        object.__setattr__(self, "bindings", bindings)
        object.__setattr__(
            self,
            "unassigned_source_slots",
            _string_tuple("SlotBindingReport.unassigned_source_slots", self.unassigned_source_slots),
        )
        object.__setattr__(
            self,
            "unfilled_target_slots",
            _string_tuple("SlotBindingReport.unfilled_target_slots", self.unfilled_target_slots),
        )
        if not isinstance(self.detail, Mapping):
            raise ValueError("SlotBindingReport.detail must be a mapping")
        object.__setattr__(self, "detail", freeze_mapping(self.detail))

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject_id": self.subject_id,
            "jurisdiction": self.jurisdiction,
            "owner_phase": self.owner_phase,
            "status": self.status,
            "completeness_kind": self.completeness_kind,
            "binding_count": len(self.bindings),
            "bindings": [binding.to_dict() for binding in self.bindings],
            "unassigned_source_slots": list(self.unassigned_source_slots),
            "unfilled_target_slots": list(self.unfilled_target_slots),
            "detail": _plain_jsonable(self.detail),
        }


@dataclass(frozen=True, slots=True)
class PayloadElaborationResult:
    """Report-facing payload elaboration projection.

    This object does not authorize replay. Frontends may use it to summarize
    payload completeness, slot binding, rejected ops, source pathologies, and
    observations at the extraction/elaboration boundary.
    """

    result_id: str
    jurisdiction: str
    owner_phase: str
    status: str
    payload_surface_kind: str
    completeness_kind: str
    elaborated_op_count: int = 0
    rejected_op_count: int = 0
    source_pathology_count: int = 0
    observation_count: int = 0
    slot_binding_report: SlotBindingReport | None = None
    replay_authorized: bool = False
    authorization_status: str = "projection_only_not_replay_authority"
    safe_default: str = "record_without_replay_authority"
    forbidden_shortcuts: tuple[str, ...] = ("treat_payload_projection_as_replay_authorization",)
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in (
            "result_id",
            "jurisdiction",
            "owner_phase",
            "status",
            "payload_surface_kind",
            "completeness_kind",
            "authorization_status",
            "safe_default",
        ):
            object.__setattr__(
                self,
                field_name,
                _required_string(f"PayloadElaborationResult.{field_name}", getattr(self, field_name)),
            )
        for field_name in (
            "elaborated_op_count",
            "rejected_op_count",
            "source_pathology_count",
            "observation_count",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, int) or value < 0:
                raise ValueError(f"PayloadElaborationResult.{field_name} must be a non-negative integer")
        if self.slot_binding_report is not None and not isinstance(self.slot_binding_report, SlotBindingReport):
            raise ValueError("PayloadElaborationResult.slot_binding_report must be a SlotBindingReport")
        if not isinstance(self.replay_authorized, bool):
            raise ValueError("PayloadElaborationResult.replay_authorized must be boolean")
        if self.replay_authorized:
            raise ValueError("PayloadElaborationResult projection must not be replay_authorized")
        object.__setattr__(
            self,
            "forbidden_shortcuts",
            _string_tuple("PayloadElaborationResult.forbidden_shortcuts", self.forbidden_shortcuts),
        )
        if not self.forbidden_shortcuts:
            raise ValueError("PayloadElaborationResult.forbidden_shortcuts is required")
        if not isinstance(self.detail, Mapping):
            raise ValueError("PayloadElaborationResult.detail must be a mapping")
        object.__setattr__(self, "detail", freeze_mapping(self.detail))

    def to_dict(self) -> dict[str, Any]:
        return {
            "result_id": self.result_id,
            "jurisdiction": self.jurisdiction,
            "owner_phase": self.owner_phase,
            "status": self.status,
            "payload_surface_kind": self.payload_surface_kind,
            "completeness_kind": self.completeness_kind,
            "elaborated_op_count": self.elaborated_op_count,
            "rejected_op_count": self.rejected_op_count,
            "source_pathology_count": self.source_pathology_count,
            "observation_count": self.observation_count,
            "slot_binding_report": (
                self.slot_binding_report.to_dict() if self.slot_binding_report is not None else None
            ),
            "replay_authorized": self.replay_authorized,
            "authorization_status": self.authorization_status,
            "safe_default": self.safe_default,
            "forbidden_shortcuts": list(self.forbidden_shortcuts),
            "detail": _plain_jsonable(self.detail),
        }


def _required_string(field_name: str, value: Any) -> str:
    text = str(value or "")
    if not text:
        raise ValueError(f"{field_name} is required")
    return text


def _string_tuple(field_name: str, values: tuple[str, ...]) -> tuple[str, ...]:
    if isinstance(values, str):
        raise ValueError(f"{field_name} must be a tuple, not a string")
    try:
        return tuple(str(value) for value in values)
    except TypeError as exc:
        raise ValueError(f"{field_name} must be iterable") from exc


def _plain_jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain_jsonable(inner) for key, inner in value.items()}
    if isinstance(value, list | tuple):
        return [_plain_jsonable(inner) for inner in value]
    if isinstance(value, set | frozenset):
        return sorted((_plain_jsonable(inner) for inner in value), key=repr)
    return value

"""Shared report-facing payload elaboration contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from lawvm.core.evidence_surface_report import EvidenceSurfaceReport
from lawvm.core.frozen_values import freeze_mapping


_PAYLOAD_ELABORATION_FORBIDDEN_SHORTCUTS: tuple[str, ...] = (
    "payload_elaboration_report_as_replay_authorization",
    "slot_binding_report_as_target_uniqueness_proof",
    "payload_completeness_as_mutation_boundary_proof",
    "elaborated_op_count_as_canonical_effect_proof",
)


@dataclass(frozen=True, slots=True)
class PayloadCompletenessWitness:
    """Payload ownership/completeness assessment emitted before replay apply."""

    kind: str
    reasons: tuple[str, ...] = ()
    tail_policy: str = "preserve_unstated_tail"
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", _required_string("PayloadCompletenessWitness.kind", self.kind))
        object.__setattr__(
            self,
            "reasons",
            _string_tuple("PayloadCompletenessWitness.reasons", self.reasons),
        )
        object.__setattr__(
            self,
            "tail_policy",
            _required_string("PayloadCompletenessWitness.tail_policy", self.tail_policy),
        )
        if not isinstance(self.detail, Mapping):
            raise ValueError("PayloadCompletenessWitness.detail must be a mapping")
        object.__setattr__(self, "detail", freeze_mapping(self.detail))

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "reasons": list(self.reasons),
            "tail_policy": self.tail_policy,
            "detail": _plain_jsonable(self.detail),
        }


@dataclass(frozen=True, slots=True)
class SlotBinding:
    """Projection of one source payload slot to one target/effective slot."""

    binding_id: str
    source_slot_id: str
    target_slot_id: str
    binding_status: str
    operation_id: str = ""
    binding_rule_id: str = ""
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "binding_id", _required_string("SlotBinding.binding_id", self.binding_id))
        object.__setattr__(
            self,
            "source_slot_id",
            _required_string("SlotBinding.source_slot_id", self.source_slot_id),
        )
        object.__setattr__(
            self,
            "target_slot_id",
            _required_string("SlotBinding.target_slot_id", self.target_slot_id),
        )
        object.__setattr__(self, "binding_status", _required_string("SlotBinding.binding_status", self.binding_status))
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
            "binding_status": self.binding_status,
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
    binding_status: str
    completeness_kind: str
    bindings: tuple[SlotBinding, ...] = ()
    unassigned_source_slots: tuple[str, ...] = ()
    unfilled_target_slots: tuple[str, ...] = ()
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "subject_id",
            _required_string("SlotBindingReport.subject_id", self.subject_id),
        )
        object.__setattr__(
            self,
            "jurisdiction",
            _required_string("SlotBindingReport.jurisdiction", self.jurisdiction),
        )
        object.__setattr__(
            self,
            "owner_phase",
            _required_string("SlotBindingReport.owner_phase", self.owner_phase),
        )
        object.__setattr__(self, "binding_status", _required_string("SlotBindingReport.binding_status", self.binding_status))
        object.__setattr__(
            self,
            "completeness_kind",
            _required_string("SlotBindingReport.completeness_kind", self.completeness_kind),
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
            "binding_status": self.binding_status,
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
    elaboration_status: str
    payload_surface_kind: str
    completeness_kind: str
    elaborated_op_count: int = 0
    rejected_op_count: int = 0
    source_pathology_count: int = 0
    observation_count: int = 0
    payload_completeness: PayloadCompletenessWitness | None = None
    slot_binding_report: SlotBindingReport | None = None
    replay_authorized: bool = False
    authorization_status: str = "projection_only_not_replay_authority"
    safe_default: str = "record_without_replay_authority"
    forbidden_shortcuts: tuple[str, ...] = ("treat_payload_projection_as_replay_authorization",)
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "result_id",
            _required_string("PayloadElaborationResult.result_id", self.result_id),
        )
        object.__setattr__(
            self,
            "jurisdiction",
            _required_string("PayloadElaborationResult.jurisdiction", self.jurisdiction),
        )
        object.__setattr__(
            self,
            "owner_phase",
            _required_string("PayloadElaborationResult.owner_phase", self.owner_phase),
        )
        object.__setattr__(self, "elaboration_status", _required_string("PayloadElaborationResult.elaboration_status", self.elaboration_status))
        object.__setattr__(
            self,
            "payload_surface_kind",
            _required_string("PayloadElaborationResult.payload_surface_kind", self.payload_surface_kind),
        )
        object.__setattr__(
            self,
            "completeness_kind",
            _required_string("PayloadElaborationResult.completeness_kind", self.completeness_kind),
        )
        object.__setattr__(
            self,
            "authorization_status",
            _required_string("PayloadElaborationResult.authorization_status", self.authorization_status),
        )
        object.__setattr__(
            self,
            "safe_default",
            _required_string("PayloadElaborationResult.safe_default", self.safe_default),
        )
        for field_name, value in (
            ("elaborated_op_count", self.elaborated_op_count),
            ("rejected_op_count", self.rejected_op_count),
            ("source_pathology_count", self.source_pathology_count),
            ("observation_count", self.observation_count),
        ):
            if not isinstance(value, int) or value < 0:
                raise ValueError(f"PayloadElaborationResult.{field_name} must be a non-negative integer")
        if self.slot_binding_report is not None and not isinstance(self.slot_binding_report, SlotBindingReport):
            raise ValueError("PayloadElaborationResult.slot_binding_report must be a SlotBindingReport")
        if self.payload_completeness is not None and not isinstance(
            self.payload_completeness,
            PayloadCompletenessWitness,
        ):
            raise ValueError("PayloadElaborationResult.payload_completeness must be a PayloadCompletenessWitness")
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
            "elaboration_status": self.elaboration_status,
            "payload_surface_kind": self.payload_surface_kind,
            "completeness_kind": self.completeness_kind,
            "elaborated_op_count": self.elaborated_op_count,
            "rejected_op_count": self.rejected_op_count,
            "source_pathology_count": self.source_pathology_count,
            "observation_count": self.observation_count,
            "payload_completeness": (
                self.payload_completeness.to_dict()
                if self.payload_completeness is not None
                else None
            ),
            "slot_binding_report": (
                self.slot_binding_report.to_dict() if self.slot_binding_report is not None else None
            ),
            "replay_authorized": self.replay_authorized,
            "authorization_status": self.authorization_status,
            "safe_default": self.safe_default,
            "forbidden_shortcuts": list(self.forbidden_shortcuts),
            "detail": _plain_jsonable(self.detail),
        }


def payload_elaboration_evidence_report(
    result: PayloadElaborationResult | Mapping[str, Any],
    *,
    report_kind: str = "payload_elaboration",
) -> EvidenceSurfaceReport:
    """Project payload elaboration output into the shared report envelope.

    The projection is evidence/read-model only. It makes payload completeness,
    slot bindings, rejected-operation counts, and pathology counts visible at
    the extraction/elaboration boundary without promoting them into replay
    authority or target-uniqueness proof.
    """

    data = result.to_dict() if isinstance(result, PayloadElaborationResult) else dict(result)
    payload_completeness = _mapping(data.get("payload_completeness"))
    slot_report = _mapping(data.get("slot_binding_report"))
    slot_bindings = _mapping_rows(slot_report.get("bindings"))
    rows = (
        _payload_result_report_row(data),
        *(_payload_completeness_report_row(payload_completeness, data=data) if payload_completeness else ()),
        *(_slot_report_rows(slot_report, data=data) if slot_report else ()),
        *(_slot_binding_report_row(row, data=data, index=index) for index, row in enumerate(slot_bindings)),
    )
    summary = {
        "result_count": 1,
        "payload_completeness_witness_count": 1 if payload_completeness else 0,
        "slot_binding_report_count": 1 if slot_report else 0,
        "slot_binding_count": len(slot_bindings),
        "elaborated_op_count": _nonnegative_int(data.get("elaborated_op_count")),
        "rejected_op_count": _nonnegative_int(data.get("rejected_op_count")),
        "source_pathology_count": _nonnegative_int(data.get("source_pathology_count")),
        "observation_count": _nonnegative_int(data.get("observation_count")),
        "completeness_kind": str(data.get("completeness_kind") or ""),
        "claim_flags": {
            "replay_claims": False,
            "canonical_effect_claims": False,
            "candidate_effect_claims": False,
            "dry_run_claims": False,
            "agreement_claims": False,
        },
    }
    return EvidenceSurfaceReport(
        jurisdiction=str(data.get("jurisdiction") or ""),
        report_kind=report_kind,
        schema="lawvm.payload_elaboration_report.v1",
        truth_claim="payload elaboration projection only",
        replay_claims=False,
        canonical_effect_claims=False,
        candidate_effect_claims=False,
        dry_run_claims=False,
        agreement_claims=False,
        summary=summary,
        filters={
            "owner_phase": str(data.get("owner_phase") or ""),
            "result_id": str(data.get("result_id") or ""),
            "payload_surface_kind": str(data.get("payload_surface_kind") or ""),
        },
        filtered_summary=summary,
        rows=rows,
        rows_truncated=False,
        detail={
            "safe_default": "treat_payload_elaboration_report_as_projection_not_replay_authority",
            "forbidden_shortcuts": _PAYLOAD_ELABORATION_FORBIDDEN_SHORTCUTS,
            "included_surfaces": (
                "payload_elaboration_result",
                "payload_completeness_witness",
                "slot_binding_report",
                "slot_binding",
            ),
            "payload_elaboration_detail": _mapping(data.get("detail")),
        },
    )


def _payload_result_report_row(data: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "surface": "payload_elaboration_result",
        "row_id": str(data.get("result_id") or ""),
        "subject_id": str(data.get("result_id") or ""),
        "elaboration_status": str(data.get("elaboration_status") or "reported"),
        "jurisdiction": str(data.get("jurisdiction") or ""),
        "owner_phase": str(data.get("owner_phase") or ""),
        "payload_surface_kind": str(data.get("payload_surface_kind") or ""),
        "completeness_kind": str(data.get("completeness_kind") or ""),
        "elaborated_op_count": _nonnegative_int(data.get("elaborated_op_count")),
        "rejected_op_count": _nonnegative_int(data.get("rejected_op_count")),
        "source_pathology_count": _nonnegative_int(data.get("source_pathology_count")),
        "observation_count": _nonnegative_int(data.get("observation_count")),
        "replay_authorized": False,
        "authorization_status": str(data.get("authorization_status") or ""),
        "safe_default": str(data.get("safe_default") or "record_without_replay_authority"),
        "forbidden_shortcuts": tuple(
            dict.fromkeys(
                (
                    *tuple(str(item) for item in _sequence(data.get("forbidden_shortcuts"))),
                    *_PAYLOAD_ELABORATION_FORBIDDEN_SHORTCUTS,
                )
            )
        ),
        "detail": _mapping(data.get("detail")),
    }


def _payload_completeness_report_row(
    witness: Mapping[str, Any],
    *,
    data: Mapping[str, Any],
) -> tuple[dict[str, Any], ...]:
    row = {
        "surface": "payload_completeness_witness",
        "row_id": f"{data.get('result_id')}:payload_completeness",
        "subject_id": str(data.get("result_id") or ""),
        "row_status": str(witness.get("kind") or "reported"),
        "jurisdiction": str(data.get("jurisdiction") or ""),
        "owner_phase": str(data.get("owner_phase") or ""),
        "completeness_kind": str(witness.get("kind") or ""),
        "reasons": tuple(str(item) for item in _sequence(witness.get("reasons"))),
        "tail_policy": str(witness.get("tail_policy") or ""),
        "replay_authorized": False,
        "forbidden_shortcuts": _PAYLOAD_ELABORATION_FORBIDDEN_SHORTCUTS,
        "detail": _mapping(witness.get("detail")),
    }
    return (row,)


def _slot_report_rows(
    slot_report: Mapping[str, Any],
    *,
    data: Mapping[str, Any],
) -> tuple[dict[str, Any], ...]:
    row = {
        "surface": "slot_binding_report",
        "row_id": _slot_report_id(slot_report, data=data),
        "subject_id": str(slot_report.get("subject_id") or data.get("result_id") or ""),
        "binding_status": str(slot_report.get("binding_status") or "reported"),
        "jurisdiction": str(slot_report.get("jurisdiction") or data.get("jurisdiction") or ""),
        "owner_phase": str(slot_report.get("owner_phase") or data.get("owner_phase") or ""),
        "completeness_kind": str(slot_report.get("completeness_kind") or data.get("completeness_kind") or ""),
        "binding_count": _nonnegative_int(slot_report.get("binding_count")),
        "unassigned_source_slots": tuple(str(item) for item in _sequence(slot_report.get("unassigned_source_slots"))),
        "unfilled_target_slots": tuple(str(item) for item in _sequence(slot_report.get("unfilled_target_slots"))),
        "replay_authorized": False,
        "forbidden_shortcuts": _PAYLOAD_ELABORATION_FORBIDDEN_SHORTCUTS,
        "detail": _mapping(slot_report.get("detail")),
    }
    return (row,)


def _slot_binding_report_row(
    row: Mapping[str, Any],
    *,
    data: Mapping[str, Any],
    index: int,
) -> dict[str, Any]:
    return {
        "surface": "slot_binding",
        "row_id": str(row.get("binding_id") or f"{data.get('result_id')}:slot:{index}"),
        "subject_id": str(data.get("result_id") or ""),
        "binding_status": str(row.get("binding_status") or "reported"),
        "jurisdiction": str(data.get("jurisdiction") or ""),
        "owner_phase": str(data.get("owner_phase") or "payload_elaboration"),
        "source_slot_id": str(row.get("source_slot_id") or ""),
        "target_slot_id": str(row.get("target_slot_id") or ""),
        "operation_id": str(row.get("operation_id") or ""),
        "binding_rule_id": str(row.get("binding_rule_id") or ""),
        "replay_authorized": False,
        "forbidden_shortcuts": _PAYLOAD_ELABORATION_FORBIDDEN_SHORTCUTS,
        "detail": _mapping(row.get("detail")),
    }


def _slot_report_id(slot_report: Mapping[str, Any], *, data: Mapping[str, Any]) -> str:
    subject_id = str(slot_report.get("subject_id") or data.get("result_id") or "payload")
    return f"{subject_id}:slot_binding_report"


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


def _mapping_rows(value: Any) -> tuple[Mapping[str, Any], ...]:
    if isinstance(value, list | tuple):
        return tuple(item for item in value if isinstance(item, Mapping))
    return ()


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> tuple[Any, ...]:
    if isinstance(value, str):
        return (value,) if value else ()
    if isinstance(value, list | tuple):
        return tuple(value)
    return ()


def _nonnegative_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(value, 0)
    return 0


def _plain_jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain_jsonable(inner) for key, inner in value.items()}
    if isinstance(value, list | tuple):
        return [_plain_jsonable(inner) for inner in value]
    if isinstance(value, set | frozenset):
        return sorted((_plain_jsonable(inner) for inner in value), key=repr)
    return value

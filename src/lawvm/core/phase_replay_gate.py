"""Typed phase-local replay gate.

This module represents the final proof boundary between an otherwise accepted
manual/evidence claim and replay authority.  It is intentionally separate from
claim validation and evidence-policy authorization: those surfaces may prove
that a claim is well-formed and reviewed, but they do not by themselves prove
that a legal-state mutation is safe to execute.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from lawvm.core.execution_authorization import ExecutionAuthorization
from lawvm.core.frozen_values import freeze_mapping
from lawvm.core.quirks_disposition import QuirksDisposition


_PHASE_REPLAY_GATE_FORBIDDEN_SHORTCUTS: tuple[str, ...] = (
    "manual_claim_as_phase_replay_gate",
    "evidence_policy_result_as_phase_replay_gate",
    "frontier_closure_as_phase_replay_gate",
)


@dataclass(frozen=True, slots=True)
class PhaseReplayGateEvaluation:
    """Result of checking a phase-local gate against one claim/frontier."""

    replay_authorized: bool
    reason_code: str
    missing_proofs: tuple[str, ...] = ()
    blocked_proofs: tuple[str, ...] = ()
    forbidden_present: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PhaseLocalReplayGate:
    """A phase-owned proof that one matched claim may be replay-authorizing.

    The gate is not a general permission token.  It binds to exactly one claim
    id, claim kind, and frontier item, then records which promotion proofs were
    required and satisfied by the phase-local compiler/validator.
    """

    gate_id: str
    jurisdiction: str
    claim_id: str
    claim_kind: str
    frontier_ref: str
    owner_phase: str
    authorization_rule_id: str
    required_proofs: tuple[str, ...]
    satisfied_proofs: tuple[str, ...]
    candidate_operation_family: str = ""
    candidate_targets: tuple[str, ...] = ()
    blocked_proofs: tuple[str, ...] = ()
    forbidden_present: tuple[str, ...] = ()
    executable: bool = True
    strict_disposition: str = "record"
    quirks_disposition: QuirksDisposition = QuirksDisposition.RECORD
    validator_status: str = "phase_gate_validated"
    safe_default: str = "block_until_phase_local_replay_gate_is_satisfied"
    forbidden_shortcuts: tuple[str, ...] = _PHASE_REPLAY_GATE_FORBIDDEN_SHORTCUTS
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "gate_id", _required_string("gate_id", self.gate_id))
        object.__setattr__(self, "jurisdiction", _required_string("jurisdiction", self.jurisdiction))
        object.__setattr__(self, "claim_id", _required_string("claim_id", self.claim_id))
        object.__setattr__(self, "claim_kind", _required_string("claim_kind", self.claim_kind))
        object.__setattr__(self, "frontier_ref", _required_string("frontier_ref", self.frontier_ref))
        object.__setattr__(self, "owner_phase", _required_string("owner_phase", self.owner_phase))
        object.__setattr__(
            self,
            "authorization_rule_id",
            _required_string("authorization_rule_id", self.authorization_rule_id),
        )
        object.__setattr__(self, "required_proofs", _string_tuple("required_proofs", self.required_proofs))
        object.__setattr__(self, "satisfied_proofs", _string_tuple("satisfied_proofs", self.satisfied_proofs))
        object.__setattr__(
            self,
            "candidate_targets",
            _string_tuple("candidate_targets", self.candidate_targets, allow_empty=True),
        )
        object.__setattr__(
            self,
            "blocked_proofs",
            _string_tuple("blocked_proofs", self.blocked_proofs, allow_empty=True),
        )
        object.__setattr__(
            self,
            "forbidden_present",
            _string_tuple("forbidden_present", self.forbidden_present, allow_empty=True),
        )
        if not self.required_proofs:
            raise ValueError("PhaseLocalReplayGate.required_proofs is required")
        if not isinstance(self.executable, bool):
            raise ValueError("PhaseLocalReplayGate.executable must be boolean")
        object.__setattr__(self, "strict_disposition", _required_string("strict_disposition", self.strict_disposition))
        object.__setattr__(self, "quirks_disposition", _required_string("quirks_disposition", self.quirks_disposition))
        object.__setattr__(self, "validator_status", _required_string("validator_status", self.validator_status))
        object.__setattr__(self, "safe_default", _required_string("safe_default", self.safe_default))
        object.__setattr__(
            self,
            "forbidden_shortcuts",
            _string_tuple("forbidden_shortcuts", self.forbidden_shortcuts),
        )
        if not isinstance(self.detail, Mapping):
            raise ValueError("PhaseLocalReplayGate.detail must be a mapping")
        object.__setattr__(self, "detail", freeze_mapping(self.detail))

    @property
    def missing_proofs(self) -> tuple[str, ...]:
        satisfied = frozenset(self.satisfied_proofs)
        return tuple(proof for proof in self.required_proofs if proof not in satisfied)

    @property
    def replay_authorized(self) -> bool:
        return (
            self.executable
            and not self.missing_proofs
            and not self.blocked_proofs
            and not self.forbidden_present
        )

    def evaluate_for_claim(
        self,
        *,
        claim_id: str,
        claim_kind: str,
        frontier_ref: str,
    ) -> PhaseReplayGateEvaluation:
        """Return whether this gate authorizes replay for exactly this claim."""

        if claim_id != self.claim_id:
            return PhaseReplayGateEvaluation(False, "rejected_phase_replay_gate_claim_mismatch")
        if claim_kind != self.claim_kind:
            return PhaseReplayGateEvaluation(False, "rejected_phase_replay_gate_kind_mismatch")
        if frontier_ref != self.frontier_ref:
            return PhaseReplayGateEvaluation(False, "rejected_phase_replay_gate_frontier_mismatch")
        if not self.executable:
            return PhaseReplayGateEvaluation(False, "rejected_phase_replay_gate_non_executable")
        if self.forbidden_present:
            return PhaseReplayGateEvaluation(
                False,
                "rejected_phase_replay_gate_forbidden_present",
                forbidden_present=self.forbidden_present,
            )
        if self.blocked_proofs:
            return PhaseReplayGateEvaluation(
                False,
                "rejected_phase_replay_gate_blocked_proofs",
                blocked_proofs=self.blocked_proofs,
            )
        missing = self.missing_proofs
        if missing:
            return PhaseReplayGateEvaluation(
                False,
                "rejected_phase_replay_gate_missing_proofs",
                missing_proofs=missing,
            )
        return PhaseReplayGateEvaluation(True, "phase_replay_gate_authorized")

    def to_execution_authorization(self) -> ExecutionAuthorization:
        """Project the gate into the shared execution-authorization surface."""

        replay_authorized = self.replay_authorized
        blocking_proofs = (
            self.missing_proofs
            or self.blocked_proofs
            or tuple(f"forbidden_present:{item}" for item in self.forbidden_present)
            or self.required_proofs
        )
        return ExecutionAuthorization(
            executable=self.executable,
            replay_authorized=replay_authorized,
            authorization_status=(
                "replay_authorized" if replay_authorized else "phase_replay_gate_blocked"
            ),
            authorization_rule_id=self.authorization_rule_id,
            owner_phase=self.owner_phase,
            strict_disposition=self.strict_disposition,
            quirks_disposition=self.quirks_disposition,
            validator_status=self.validator_status,
            required_proofs=() if replay_authorized else blocking_proofs,
            safe_default=(
                "execute_only_after_phase_local_replay_gate"
                if replay_authorized
                else self.safe_default
            ),
            forbidden_shortcuts=self.forbidden_shortcuts,
            detail={
                **dict(self.detail),
                "phase_local_replay_gate": self.to_dict(),
            },
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "lawvm.phase_local_replay_gate.v1",
            "gate_id": self.gate_id,
            "jurisdiction": self.jurisdiction,
            "claim_id": self.claim_id,
            "claim_kind": self.claim_kind,
            "frontier_ref": self.frontier_ref,
            "owner_phase": self.owner_phase,
            "authorization_rule_id": self.authorization_rule_id,
            "required_proofs": list(self.required_proofs),
            "satisfied_proofs": list(self.satisfied_proofs),
            "missing_proofs": list(self.missing_proofs),
            "blocked_proofs": list(self.blocked_proofs),
            "forbidden_present": list(self.forbidden_present),
            "candidate_operation_family": self.candidate_operation_family,
            "candidate_targets": list(self.candidate_targets),
            "executable": self.executable,
            "replay_authorized": self.replay_authorized,
            "strict_disposition": self.strict_disposition,
            "quirks_disposition": self.quirks_disposition,
            "validator_status": self.validator_status,
            "safe_default": self.safe_default,
            "forbidden_shortcuts": list(self.forbidden_shortcuts),
            "detail": _plain_jsonable(self.detail),
        }


def _required_string(field_name: str, value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"PhaseLocalReplayGate.{field_name} is required")
    return text


def _string_tuple(field_name: str, values: Any, *, allow_empty: bool = False) -> tuple[str, ...]:
    if isinstance(values, str) or not isinstance(values, tuple):
        raise ValueError(f"PhaseLocalReplayGate.{field_name} must be a tuple")
    normalized = tuple(str(value) for value in values if str(value))
    if not allow_empty and not normalized:
        raise ValueError(f"PhaseLocalReplayGate.{field_name} is required")
    return normalized


def _plain_jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain_jsonable(inner) for key, inner in value.items()}
    if isinstance(value, list | tuple):
        return [_plain_jsonable(inner) for inner in value]
    if isinstance(value, set | frozenset):
        return sorted((_plain_jsonable(inner) for inner in value), key=repr)
    return value

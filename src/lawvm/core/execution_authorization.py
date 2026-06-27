"""Shared execution/replay authorization projection contract."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from typing import Any, Mapping, cast

from lawvm.core.evidence_surface_report import EvidenceSurfaceReport
from lawvm.core.frozen_values import freeze_mapping
from lawvm.core.quirks_disposition import QuirksDisposition

if TYPE_CHECKING:
    from lawvm.core.evidence_kernel import AuthorizationResult


_EXECUTION_AUTHORIZATION_REPORT_FORBIDDEN_SHORTCUTS: tuple[str, ...] = (
    "authorization_report_as_operation_payload",
    "authorization_report_as_mutation_boundary_proof",
    "evidence_policy_result_as_replay_authority_without_phase_gate",
)


@dataclass(frozen=True, slots=True)
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
    quirks_disposition: QuirksDisposition = QuirksDisposition.RECORD
    validator_status: str = ""
    required_proofs: tuple[str, ...] = ()
    safe_default: str = ""
    forbidden_shortcuts: tuple[str, ...] = ()
    # Read-as-witness-only. ``detail`` carries free-form evidence (e.g. the
    # projected EvidenceKernel result) for human/report consumption. It drives
    # NO control flow: the executable/replay_authorized two-flag promotion plus
    # ``forbidden_shortcuts`` are the canonical authority waist, and nothing in
    # this module branches on ``detail`` contents. ``Mapping[str, Any]`` is the
    # intended type here precisely because no caller may treat it as authority.
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


def execution_authorization_from_kernel_result(
    result: "AuthorizationResult",
    *,
    executable: bool,
    owner_phase: str,
    authorization_rule_id: str = "",
    strict_disposition: str = "",
    quirks_disposition: QuirksDisposition = QuirksDisposition.RECORD,
    validator_status: str = "",
    replay_authorized_when_policy_satisfied: bool = False,
    required_proofs: tuple[str, ...] = (),
    safe_default: str = "",
    forbidden_shortcuts: tuple[str, ...] = (),
    detail: Mapping[str, Any] | None = None,
) -> ExecutionAuthorization:
    """Project EvidenceKernel output into the shared replay-authorization shape.

    ``AuthorizationResult.authorized`` means a declarative evidence policy was
    satisfied.  It is intentionally not replay authority by itself.  Callers
    that want a satisfied policy to authorize replay must also set
    ``executable=True`` and ``replay_authorized_when_policy_satisfied=True``.
    """
    replay_authorized = (
        bool(result.authorized)
        and bool(executable)
        and bool(replay_authorized_when_policy_satisfied)
    )
    status = _kernel_authorization_status(
        result_authorized=result.authorized,
        executable=executable,
        replay_authorized=replay_authorized,
    )
    blocked_proofs = required_proofs or _kernel_required_proofs(result)
    default_safe = safe_default or _kernel_safe_default(
        result_authorized=result.authorized,
        replay_authorized=replay_authorized,
    )
    shortcuts = forbidden_shortcuts or (
        "treat_evidence_policy_satisfaction_as_replay_authority",
    )
    return ExecutionAuthorization(
        executable=bool(executable),
        replay_authorized=replay_authorized,
        authorization_status=status,
        authorization_rule_id=authorization_rule_id or result.policy_id,
        owner_phase=owner_phase,
        strict_disposition=strict_disposition or ("record" if replay_authorized else "block"),
        quirks_disposition=quirks_disposition,
        validator_status=validator_status,
        required_proofs=() if replay_authorized else blocked_proofs,
        safe_default=default_safe,
        forbidden_shortcuts=shortcuts,
        detail={
            **dict(detail or {}),
            "evidence_kernel": {
                "subject": {
                    "artifact_type": result.subject.artifact_type,
                    "artifact_id": result.subject.artifact_id,
                    "content_hash": result.subject.content_hash,
                },
                "policy_id": result.policy_id,
                "profile_name": result.profile_name,
                "authorized": result.authorized,
                "satisfied_clauses": list(result.satisfied_clauses),
                "unsatisfied_clauses": list(result.unsatisfied_clauses),
                "forbidden_present": list(result.forbidden_present),
                "evidence_bundle_hash": result.evidence_bundle_hash,
            },
        },
        )


def execution_authorization_evidence_report(
    authorizations: (
        ExecutionAuthorization
        | Mapping[str, Any]
        | tuple[ExecutionAuthorization | Mapping[str, Any], ...]
    ),
    *,
    jurisdiction: str,
    report_kind: str = "execution_authorization",
) -> EvidenceSurfaceReport:
    """Project execution authorization rows into a shared report envelope.

    This adapter is a read-model bridge for already-computed authorization
    objects. It does not evaluate evidence policy and does not synthesize replay
    permission. If any row is explicitly replay-authorized, the report's
    ``replay_claims`` flag becomes true so downstream readers do not miss that
    the envelope contains authority-bearing rows.
    """

    rows = tuple(
        _authorization_mapping(row)
        for row in _authorization_sequence(authorizations)
    )
    report_rows = tuple(
        _authorization_report_row(row, index=index)
        for index, row in enumerate(rows, start=1)
    )
    replay_authorized_count = sum(1 for row in rows if bool(row.get("replay_authorized")))
    executable_count = sum(1 for row in rows if bool(row.get("executable")))
    status_counts = _counts(str(row.get("authorization_status") or "") for row in rows)
    owner_phase_counts = _counts(str(row.get("owner_phase") or "") for row in rows)
    strict_disposition_counts = _counts(
        str(row.get("strict_disposition") or "") for row in rows
    )
    quirks_disposition_counts = _counts(
        str(row.get("quirks_disposition") or "") for row in rows
    )
    validator_status_counts = _counts_nonblank(
        str(row.get("validator_status") or "") for row in rows
    )
    required_proof_counts = _counts(
        str(proof)
        for row in rows
        for proof in _sequence(row.get("required_proofs"))
        if str(proof)
    )
    summary = {
        "authorization_count": len(rows),
        "executable_count": executable_count,
        "replay_authorized_count": replay_authorized_count,
        "non_authorized_count": len(rows) - replay_authorized_count,
        "strict_blocked_count": strict_disposition_counts.get("block", 0),
        "authorization_status_counts": status_counts,
        "owner_phase_counts": owner_phase_counts,
        "strict_disposition_counts": strict_disposition_counts,
        "quirks_disposition_counts": quirks_disposition_counts,
        "validator_status_counts": validator_status_counts,
        "required_proof_counts": required_proof_counts,
        "claim_flags": {
            "replay_claims": replay_authorized_count > 0,
            "canonical_effect_claims": False,
            "candidate_effect_claims": False,
            "dry_run_claims": False,
            "agreement_claims": False,
        },
    }
    return EvidenceSurfaceReport(
        jurisdiction=jurisdiction,
        report_kind=report_kind,
        schema="lawvm.execution_authorization_report.v1",
        truth_claim="execution authorization projections",
        replay_claims=replay_authorized_count > 0,
        canonical_effect_claims=False,
        candidate_effect_claims=False,
        dry_run_claims=False,
        agreement_claims=False,
        summary=summary,
        filters={"report_kind": report_kind},
        filtered_summary=summary,
        rows=report_rows,
        rows_truncated=False,
        detail={
            "safe_default": "read_authorization_rows_but_do_not_infer_missing_authority",
            "forbidden_shortcuts": _EXECUTION_AUTHORIZATION_REPORT_FORBIDDEN_SHORTCUTS,
            "included_surfaces": ("execution_authorization",),
        },
    )


def _kernel_authorization_status(
    *,
    result_authorized: bool,
    executable: bool,
    replay_authorized: bool,
) -> str:
    if replay_authorized:
        return "replay_authorized"
    if not result_authorized:
        return "evidence_policy_unsatisfied"
    if not executable:
        return "evidence_policy_satisfied_non_executable"
    return "evidence_policy_satisfied_replay_gate_required"


def _kernel_required_proofs(result: "AuthorizationResult") -> tuple[str, ...]:
    proofs = [
        f"evidence_policy_clause:{clause}"
        for clause in result.unsatisfied_clauses
    ]
    proofs.extend(
        f"forbidden_evidence_absence:{clause}"
        for clause in result.forbidden_present
    )
    if not proofs:
        proofs.append("phase_local_replay_authorization")
    return tuple(proofs)


def _kernel_safe_default(
    *,
    result_authorized: bool,
    replay_authorized: bool,
) -> str:
    if replay_authorized:
        return "execute_only_after_evidence_policy_and_phase_local_gate"
    if result_authorized:
        return "record_evidence_policy_result_without_promoting_to_replay"
    return "block_until_evidence_policy_is_satisfied"


def _authorization_sequence(
    value: (
        ExecutionAuthorization
        | Mapping[str, Any]
        | tuple[ExecutionAuthorization | Mapping[str, Any], ...]
    ),
) -> tuple[ExecutionAuthorization | Mapping[str, Any], ...]:
    if isinstance(value, ExecutionAuthorization) or isinstance(value, Mapping):
        return (cast(ExecutionAuthorization | Mapping[str, Any], value),)
    return tuple(value)


def _authorization_mapping(value: ExecutionAuthorization | Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(value, ExecutionAuthorization):
        return value.to_dict()
    row = dict(value)
    issues = validate_execution_authorization(row)
    if issues:
        raise ValueError("; ".join(issues))
    return row


def _authorization_report_row(row: Mapping[str, Any], *, index: int) -> dict[str, Any]:
    detail_value = row.get("detail")
    detail: Mapping[str, Any] = detail_value if isinstance(detail_value, Mapping) else {}
    subject_id = _authorization_subject_id(row, detail=detail, index=index)
    row_id = str(row.get("row_id") or "") or _authorization_row_id(
        row,
        subject_id=subject_id,
    )
    return {
        "surface": "execution_authorization",
        "row_id": row_id,
        "subject_id": subject_id,
        "row_status": str(row.get("authorization_status") or "reported"),
        "authorization_ref": str(row.get("authorization_rule_id") or ""),
        "authorization_rule_id": str(row.get("authorization_rule_id") or ""),
        "owner_phase": str(row.get("owner_phase") or ""),
        "executable": bool(row.get("executable", False)),
        "replay_authorized": bool(row.get("replay_authorized", False)),
        "strict_disposition": str(row.get("strict_disposition") or ""),
        "quirks_disposition": str(row.get("quirks_disposition") or ""),
        "validator_status": str(row.get("validator_status") or ""),
        "required_proofs": tuple(str(item) for item in _sequence(row.get("required_proofs"))),
        "safe_default": str(row.get("safe_default") or ""),
        "forbidden_shortcuts": tuple(
            dict.fromkeys(
                (
                    *tuple(str(item) for item in _sequence(row.get("forbidden_shortcuts"))),
                    *_EXECUTION_AUTHORIZATION_REPORT_FORBIDDEN_SHORTCUTS,
                )
            )
        ),
        "detail": detail,
    }


def _authorization_subject_id(
    row: Mapping[str, Any],
    *,
    detail: Mapping[str, Any],
    index: int,
) -> str:
    explicit_subject_id = str(row.get("subject_id") or "")
    if explicit_subject_id:
        return explicit_subject_id
    evidence_kernel = detail.get("evidence_kernel")
    subject = evidence_kernel.get("subject") if isinstance(evidence_kernel, Mapping) else None
    if isinstance(subject, Mapping):
        subject_id = str(subject.get("artifact_id") or "")
        if subject_id:
            return subject_id
    return str(row.get("authorization_rule_id") or f"authorization:{index}")


def _authorization_row_id(row: Mapping[str, Any], *, subject_id: str) -> str:
    authorization_rule_id = str(row.get("authorization_rule_id") or "authorization")
    digest = _authorization_row_digest(row)
    return f"{subject_id}:{authorization_rule_id}:{digest}"


def _authorization_row_digest(row: Mapping[str, Any]) -> str:
    payload = json.dumps(
        _plain_jsonable(row),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


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


def _counts_nonblank(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value or "")
        if not key:
            continue
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

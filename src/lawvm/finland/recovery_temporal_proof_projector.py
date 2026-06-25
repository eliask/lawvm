"""Project Finland temporal/recovery strict-report rows into proof surfaces."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from lawvm.core.execution_authorization import ExecutionAuthorization
from lawvm.core.source_completeness import source_completeness_status_from_mapping
from lawvm.core.temporal_resolution import (
    TEMPORAL_RECOVERY_FAMILY,
    TEMPORAL_UNRESOLVED_CONTINGENT,
    TEMPORAL_UNKNOWN_EFFECTIVE_DATE,
    TemporalResolutionEvidence,
    TemporalResolutionStatus,
)
from lawvm.finland.proof_surface_row_helpers import kind_slug
from lawvm.finland.recovery_authorization_registry import recovery_authorization_rule

def source_completeness_status_row(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Project strict-report source-completeness counts into a passive row."""
    status = source_completeness_status_from_mapping(payload, jurisdiction="fi")
    if status is None:
        return {}
    return status.to_dict()


def temporal_resolution_evidence_rows_from_projection_rows(
    projection_rows: tuple[Mapping[str, Any], ...],
    *,
    strict_fail_reasons: tuple[str, ...] = (),
) -> tuple[dict[str, Any], ...]:
    """Project existing Finland TIME.* findings into shared temporal evidence rows."""
    fail_reason_set = {str(reason) for reason in strict_fail_reasons}
    rows: list[dict[str, Any]] = []
    for row in projection_rows:
        kind = str(row.get("kind") or "")
        if kind not in {
            "TIME.ESTIMATED_EFFECTIVE_DATE",
            "TIME.CONTINGENT_EFFECTIVE_DATE",
        }:
            continue
        detail_raw = row.get("detail")
        detail: Mapping[str, Any] = detail_raw if isinstance(detail_raw, Mapping) else {}
        rows.append(
            TemporalResolutionEvidence(
                rule_id=temporal_resolution_rule_id(kind),
                phase="temporal_elaboration",
                reason=str(row.get("message") or temporal_resolution_reason(kind)),
                status=temporal_resolution_status(kind),
                family=TEMPORAL_RECOVERY_FAMILY,
                blocking=kind in fail_reason_set,
                source_locator=str(row.get("source") or ""),
                strict_disposition="block" if kind in fail_reason_set else "record",
                quirks_disposition="record",
                detail={
                    "finding_kind": kind,
                    "step": str(detail.get("step") or ""),
                    "source_statute": str(row.get("source") or ""),
                },
            ).to_diagnostic_detail()
        )
    return tuple(rows)


def recovery_execution_authorization_rows_from_projection_rows(
    projection_rows: tuple[Mapping[str, Any], ...],
    *,
    strict_fail_reasons: tuple[str, ...] = (),
    statute_id: str = "",
) -> tuple[dict[str, Any], ...]:
    """Project Finland recovery/strictness findings into non-executable authorizations."""

    fail_reason_set = {str(reason) for reason in strict_fail_reasons}
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for index, row in enumerate(projection_rows, start=1):
        kind = str(row.get("kind") or "")
        rule = recovery_authorization_rule(kind)
        if rule is None:
            continue
        detail_raw = row.get("detail")
        detail: Mapping[str, Any] = detail_raw if isinstance(detail_raw, Mapping) else {}
        source_statute = str(row.get("source") or detail.get("source_statute") or detail.get("amendment_id") or "")
        message = str(row.get("message") or "")
        key = (kind, source_statute, message)
        if key in seen:
            continue
        seen.add(key)
        blocking = kind in fail_reason_set or rule.blocks_in_strict()
        authorization = ExecutionAuthorization(
            executable=False,
            replay_authorized=False,
            authorization_status="strict_recovery_blocked" if blocking else "recovery_projection_not_replay_authority",
            authorization_rule_id=f"fi_recovery_{kind_slug(kind)}",
            owner_phase=rule.owner_phase,
            strict_disposition="block" if blocking else rule.strict_disposition,
            quirks_disposition=rule.quirks_disposition,
            validator_status=rule.validator_status,
            required_proofs=rule.required_proofs,
            safe_default=rule.safe_default,
            forbidden_shortcuts=rule.forbidden_shortcuts,
            detail={
                "jurisdiction": "fi",
                "statute_id": statute_id,
                "finding_kind": kind,
                "family": rule.family,
                "source_statute": source_statute,
                "message": message,
                "strict_fail_reason_present": kind in fail_reason_set,
                "projection_only": True,
                "projection_detail": dict(detail),
            },
        ).to_dict()
        authorization["row_id"] = recovery_authorization_row_id(
            statute_id=statute_id,
            index=index,
            kind=kind,
            source_statute=source_statute,
            message=message,
        )
        authorization["finding_kind"] = kind
        authorization["family"] = rule.family
        if source_statute:
            authorization["source_artifact_id"] = source_statute
        rows.append(authorization)
    return tuple(rows)


def temporal_resolution_rule_id(kind: str) -> str:
    if kind == "TIME.CONTINGENT_EFFECTIVE_DATE":
        return "fi_time_contingent_effective_date"
    return "fi_time_estimated_effective_date"


def temporal_resolution_status(kind: str) -> TemporalResolutionStatus:
    if kind == "TIME.CONTINGENT_EFFECTIVE_DATE":
        return TEMPORAL_UNRESOLVED_CONTINGENT
    return TEMPORAL_UNKNOWN_EFFECTIVE_DATE


def temporal_resolution_reason(kind: str) -> str:
    if kind == "TIME.CONTINGENT_EFFECTIVE_DATE":
        return "effective date is contingent or decree-set"
    return "effective date was estimated or substituted from publication metadata"


def recovery_authorization_row_id(
    *,
    statute_id: str,
    index: int,
    kind: str,
    source_statute: str,
    message: str,
) -> str:
    payload = {
        "statute_id": statute_id,
        "index": index,
        "kind": kind,
        "source_statute": source_statute,
        "message": message,
    }
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=True, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    return f"fi:{statute_id or 'unknown'}:recovery-auth:{kind_slug(kind)}:{digest}"

__all__ = [
    "recovery_execution_authorization_rows_from_projection_rows",
    "source_completeness_status_row",
    "temporal_resolution_evidence_rows_from_projection_rows",
]

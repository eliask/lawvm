"""Shared builders for diagnostic detail envelopes.

This module standardizes the outward detail fields used by replay, lowering,
source-pathology, and audit records. It deliberately does not replace
frontend-local finding/adjudication classes.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional


BLOCKING_STRICT_DISPOSITIONS = frozenset({"block", "reject", "fail", "hard_fail", "strict_block"})


def diagnostic_detail(
    *,
    rule_id: str,
    phase: str,
    blocking: bool,
    family: str = "",
    reason: str = "",
    message: str = "",
    strict_disposition: Optional[str] = None,
    quirks_disposition: str = "record",
    detail: Optional[Mapping[str, Any]] = None,
    **extra: Any,
) -> dict[str, Any]:
    """Build a standard diagnostic detail dictionary.

    The envelope is a projection contract only. Source-pathology, replay, and
    compile adjudication classes remain phase-local owners of their semantics.
    """
    if not rule_id:
        raise ValueError("diagnostic_detail requires a non-empty rule_id")
    if not phase:
        raise ValueError("diagnostic_detail requires a non-empty phase")
    payload: dict[str, Any] = {
        "rule_id": str(rule_id),
        "phase": str(phase),
        "blocking": bool(blocking),
        "strict_disposition": strict_disposition or ("block" if blocking else "record"),
        "quirks_disposition": str(quirks_disposition or "record"),
    }
    if family:
        payload["family"] = str(family)
    if reason:
        payload["reason"] = str(reason)
    if message:
        payload["message"] = str(message)
    if detail:
        payload.update(dict(detail))
    payload.update(extra)
    return payload


def validate_diagnostic_detail(row: Mapping[str, Any]) -> tuple[str, ...]:
    """Return validation issues for the shared diagnostic detail envelope.

    This validates only the common projection fields. Frontend-local payload
    fields remain additive and phase-owned.
    """
    issues: list[str] = []
    _require_non_empty_string(row, "rule_id", issues)
    _require_non_empty_string(row, "phase", issues)
    _require_non_empty_string(row, "strict_disposition", issues)
    _require_non_empty_string(row, "quirks_disposition", issues)
    family = row.get("family", "")
    if family is not None and family != "" and not isinstance(family, str):
        issues.append("family must be a string when present")
    reason = row.get("reason", "")
    if reason is not None and reason != "" and not isinstance(reason, str):
        issues.append("reason must be a string when present")
    message = row.get("message", "")
    if message is not None and message != "" and not isinstance(message, str):
        issues.append("message must be a string when present")
    issues.extend(validate_blocking_disposition(row, subject="diagnostic"))
    return tuple(issues)


def validate_blocking_disposition(row: Mapping[str, Any], *, subject: str) -> tuple[str, ...]:
    """Validate the shared blocking/strict-disposition invariant."""

    blocking = row.get("blocking")
    if not isinstance(blocking, bool):
        return ("blocking must be a boolean",)
    if blocking and row.get("strict_disposition") not in BLOCKING_STRICT_DISPOSITIONS:
        return (f"blocking {subject} must have blocking strict_disposition",)
    return ()


def _require_non_empty_string(row: Mapping[str, Any], key: str, issues: list[str]) -> None:
    value = row.get(key)
    if not isinstance(value, str) or not value:
        issues.append(f"{key} is required")

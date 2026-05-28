"""Shared builders for diagnostic detail envelopes.

This module standardizes the outward detail fields used by replay, lowering,
source-pathology, and audit records. It deliberately does not replace
frontend-local finding/adjudication classes.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional


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

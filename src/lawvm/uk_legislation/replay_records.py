"""UK replay adjudication record helpers."""

from __future__ import annotations

from typing import Any, Optional

from lawvm.core.ir import LegalOperation
from lawvm.core.phase_result import Finding
from lawvm.replay_adjudication import CompileAdjudication


def _build_uk_replay_adjudication(
    *,
    kind: str,
    message: str,
    op: LegalOperation,
    detail: Optional[dict[str, Any]] = None,
) -> CompileAdjudication:
    """Build a typed UK replay adjudication without requiring an output sink."""
    detail_payload: dict[str, Any] = dict(detail or {})
    detail_payload.setdefault("rule_id", str(kind))
    detail_payload.setdefault("phase", "replay")
    if kind == "uk_replay_unsupported_action":
        detail_payload.setdefault("family", "unsupported_or_unresolved_action")
        detail_payload.setdefault("blocking", True)
        detail_payload.setdefault("strict_disposition", "block")
        detail_payload.setdefault("quirks_disposition", "record")
    return CompileAdjudication(
        kind=str(kind),
        message=message,
        source_statute=op.source.statute_id if op.source else "",
        op_id=op.op_id,
        detail=detail_payload,
    )


def _append_uk_replay_adjudication(
    adjudications_out: Optional[list[CompileAdjudication]],
    *,
    kind: str,
    message: str,
    op: LegalOperation,
    detail: Optional[dict[str, Any]] = None,
) -> CompileAdjudication:
    """Append a UK replay adjudication when a sink list is available."""
    adjudication = _build_uk_replay_adjudication(
        kind=kind,
        message=message,
        op=op,
        detail=detail,
    )
    if adjudications_out is not None:
        adjudications_out.append(adjudication)
    return adjudication


def _uk_adjudication_from_finding(finding: Finding) -> CompileAdjudication:
    """Project replay-lint findings into the UK replay compatibility bag."""
    detail = dict(finding.detail)
    message = str(detail.pop("message", "") or "")
    blocking = bool(finding.blocking)
    detail.setdefault("blocking", blocking)
    detail.setdefault("strict_disposition", "block" if blocking else "record")
    detail.setdefault("quirks_disposition", "record")
    return CompileAdjudication(
        kind=str(finding.kind or ""),
        message=message,
        source_statute=str(finding.source_statute or ""),
        detail=detail,
    )

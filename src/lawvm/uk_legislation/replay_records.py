"""UK replay adjudication record helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from dataclasses import replace as dc_replace
from typing import Any, Optional

from lawvm.core.ir import IRStatute, LegalOperation
from lawvm.core.phase_result import Finding
from lawvm.core.replay_lints import build_text_duplication_findings
from lawvm.replay_adjudication import CompileAdjudication


@dataclass(frozen=True)
class UKReplayPrepareResult:
    accepted_ops: tuple[LegalOperation, ...]
    rejected_adjudications: tuple[CompileAdjudication, ...]


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


def append_replay_fold_text_duplication_adjudications(
    adjudications_out: list[CompileAdjudication],
    *,
    frozen_statute: IRStatute,
    source_statute: str,
) -> None:
    """Project replay-fold text-duplication findings into UK adjudications."""
    duplicate_findings = [
        dc_replace(finding, detail={**finding.detail, "root": "body"})
        for finding in build_text_duplication_findings(
            frozen_statute.body,
            phase="replay_fold",
            source_statute=source_statute,
        )
    ]
    for schedule in frozen_statute.supplements:
        schedule_findings = build_text_duplication_findings(
            schedule,
            phase="replay_fold",
            source_statute=source_statute,
        )
        duplicate_findings.extend(
            dc_replace(
                finding,
                detail={**finding.detail, "root": f"schedule:{schedule.label or '?'}"},
            )
            for finding in schedule_findings
        )

    seen_duplicate_keys = {
        (
            adjudication.kind,
            adjudication.message,
            adjudication.source_statute,
            json.dumps(adjudication.detail, sort_keys=True, ensure_ascii=False),
        )
        for adjudication in adjudications_out
    }
    for finding in duplicate_findings:
        adjudication = _uk_adjudication_from_finding(finding)
        key = (
            adjudication.kind,
            adjudication.message,
            adjudication.source_statute,
            json.dumps(adjudication.detail, sort_keys=True, ensure_ascii=False),
        )
        if key in seen_duplicate_keys:
            continue
        adjudications_out.append(adjudication)
        seen_duplicate_keys.add(key)

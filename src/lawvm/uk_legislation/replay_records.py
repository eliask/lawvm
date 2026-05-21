"""UK replay adjudication record helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from dataclasses import replace as dc_replace
from typing import Any, Optional

from lawvm.core.ir import IRStatute, LegalAddress, LegalOperation
from lawvm.core.phase_result import Finding
from lawvm.core.replay_lints import build_text_duplication_findings
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.uk_legislation.addressing import _action_name


UK_REPLAY_SCHEDULE_ENTRY_REPEAL_GRANULARITY_BLOCKED_RULE_ID = (
    "uk_replay_schedule_entry_repeal_granularity_blocked"
)


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


def uk_replay_action_target_detail(
    op: LegalOperation,
    target: LegalAddress,
    *,
    blocking: bool,
    **extra: Any,
) -> dict[str, Any]:
    """Build the standard action/target detail payload for replay adjudications."""
    detail: dict[str, Any] = {
        "action": _action_name(op.action),
        "target": str(target),
        "blocking": bool(blocking),
        "strict_disposition": "block" if blocking else "record",
        "quirks_disposition": "record",
    }
    detail.update(extra)
    return detail


def uk_replay_blocking_action_target_detail(
    op: LegalOperation,
    target: LegalAddress,
    **extra: Any,
) -> dict[str, Any]:
    """Build the standard strict-blocking detail payload for replay skips."""
    return uk_replay_action_target_detail(op, target, blocking=True, **extra)


def uk_replay_recovery_action_target_detail(
    op: LegalOperation,
    target: LegalAddress,
    *,
    family: str,
    **extra: Any,
) -> dict[str, Any]:
    """Build the standard detail payload for recoveries applied only in quirks mode."""
    detail = uk_replay_action_target_detail(
        op,
        target,
        blocking=False,
        family=family,
        **extra,
    )
    detail["strict_disposition"] = "block"
    detail["quirks_disposition"] = "apply"
    return detail


def append_schedule_entry_repeal_granularity_blocked_adjudication(
    adjudications_out: Optional[list[CompileAdjudication]],
    *,
    op: LegalOperation,
) -> CompileAdjudication:
    return _append_uk_replay_adjudication(
        adjudications_out,
        kind=UK_REPLAY_SCHEDULE_ENTRY_REPEAL_GRANULARITY_BLOCKED_RULE_ID,
        message=(
            "UK replay prepare step skipped a schedule-root repeal "
            "whose source text only claims entry-level repeal."
        ),
        op=op,
        detail={
            "action": _action_name(op.action),
            "target": str(op.target),
            "reason": "schedule_entry_repeal_widened_to_schedule",
            "family": "source_schedule_list_entry_elaboration",
            "blocking": True,
            "strict_disposition": "block",
            "quirks_disposition": "record",
        },
    )


def append_same_source_text_patch_overlap_disjoint_adjudication(
    adjudications_out: Optional[list[CompileAdjudication]],
    *,
    op: LegalOperation,
    ordered_before_op_ids: tuple[str, ...],
) -> CompileAdjudication:
    match_text = op.text_patch.selector.match_text if op.text_patch is not None else ""
    occurrence = op.text_patch.selector.occurrence if op.text_patch is not None else 0
    return _append_uk_replay_adjudication(
        adjudications_out,
        kind="uk_replay_same_source_text_patch_overlap_disjoint",
        message=(
            "UK replay allowed an ordinal text patch whose selector text appears "
            "inside broader same-source patches because the claimed occurrence is "
            "disjoint in the base target text."
        ),
        op=op,
        detail={
            "action": _action_name(op.action),
            "target": str(op.target),
            "match_text": match_text,
            "occurrence": occurrence,
            "ordered_before_op_ids": ordered_before_op_ids,
            "reason": "base_target_occurrence_disjoint_from_broader_same_source_patch",
            "family": "text_patch_overlap_resolution",
            "blocking": False,
            "strict_disposition": "record",
            "quirks_disposition": "record",
        },
    )


def append_same_source_text_patch_overlap_blocked_adjudication(
    adjudications_out: Optional[list[CompileAdjudication]],
    *,
    op: LegalOperation,
) -> CompileAdjudication:
    match_text = op.text_patch.selector.match_text if op.text_patch is not None else ""
    occurrence = op.text_patch.selector.occurrence if op.text_patch is not None else 0
    return _append_uk_replay_adjudication(
        adjudications_out,
        kind="uk_replay_same_source_text_patch_overlap_blocked",
        message=(
            "UK replay prepare step skipped an ordinal text patch whose selector overlaps "
            "a broader same-source text patch on the same target."
        ),
        op=op,
        detail={
            "action": _action_name(op.action),
            "target": str(op.target),
            "match_text": match_text,
            "occurrence": occurrence,
            "reason": "same_source_same_target_overlapping_text_patch",
            "family": "text_patch_overlap_resolution",
            "blocking": True,
            "strict_disposition": "block",
            "quirks_disposition": "record",
        },
    )


def append_unsupported_whole_act_prepare_filter_adjudication(
    adjudications_out: Optional[list[CompileAdjudication]],
    *,
    op: LegalOperation,
) -> CompileAdjudication:
    return _append_uk_replay_adjudication(
        adjudications_out,
        kind="uk_replay_unsupported_action",
        message="UK replay prepare step skipped unsupported whole-act target before replay apply.",
        op=op,
        detail={
            "action": _action_name(op.action),
            "target": str(op.target),
            "reason": "whole_act_prepare_filter",
        },
    )


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

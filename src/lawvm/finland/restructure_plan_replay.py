"""Replay boundary for executing Finland structural-transform plans."""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Optional

from lawvm.core.ir import IRNode, LegalAddress, LegalOperation, OperationSource
from lawvm.core.phase_result import Finding
from lawvm.core.provenance import MigrationEvent
from lawvm.core.semantic_types import StructuralAction
from lawvm.finland.migration_ledger import MigrationLedger
from lawvm.finland.replay_notices import replay_print
from lawvm.finland.restructure_plan import (
    StructuralTransformPlan,
    deferred_plan_op_finding,
    execute_restructure_plan,
    move_skip_finding,
    relabel_skip_finding,
    relabel_skip_source_pathology_finding,
)
from lawvm.finland.statute import ReplayState

logger = logging.getLogger(__name__)

FI_RESTRUCTURE_RENUMBER_TIMELINE_RULE_ID = "fi.restructure.renumber_timeline"
FI_RESTRUCTURE_CHAPTER_PART_MOVE_TIMELINE_RULE_ID = (
    "fi.restructure.chapter_part_move_timeline"
)


@dataclass(frozen=True, slots=True)
class ChapterPartMoveTimelineRequest:
    amendment_id: str
    chapter_label: str
    old_part_label: str
    new_part_label: str
    payload: IRNode
    source: OperationSource


@dataclass(frozen=True, slots=True)
class ChapterPartMoveTimelineOps:
    repeal: LegalOperation
    insert: LegalOperation


def build_chapter_part_move_timeline_ops(
    request: ChapterPartMoveTimelineRequest,
) -> ChapterPartMoveTimelineOps:
    old_chapter_path = (
        ("part", request.old_part_label),
        ("chapter", request.chapter_label),
    )
    new_chapter_path = (
        ("part", request.new_part_label),
        ("chapter", request.chapter_label),
    )
    return ChapterPartMoveTimelineOps(
        repeal=LegalOperation(
            op_id=f"chapter_part_move_repeal_{request.chapter_label}_{request.amendment_id}",
            sequence=0,
            action=StructuralAction.REPEAL,
            target=LegalAddress(path=old_chapter_path),
            source=request.source,
            group_id=f"finland-johto:{request.amendment_id}",
            witness_rule_id=FI_RESTRUCTURE_CHAPTER_PART_MOVE_TIMELINE_RULE_ID,
        ),
        insert=LegalOperation(
            op_id=f"chapter_part_move_insert_{request.chapter_label}_{request.amendment_id}",
            sequence=0,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=new_chapter_path),
            payload=request.payload,
            source=request.source,
            group_id=f"finland-johto:{request.amendment_id}",
            witness_rule_id=FI_RESTRUCTURE_CHAPTER_PART_MOVE_TIMELINE_RULE_ID,
        ),
    )


@dataclass(frozen=True, slots=True)
class ExecuteRestructurePlanRequest:
    """Inputs for executing a pending StructuralTransformPlan inside replay."""

    state: ReplayState
    plan: StructuralTransformPlan
    amendment_id: str
    source_title: str
    amendment_issue_date: Optional[dt.date]
    amendment_effective_date: Optional[dt.date]
    migration_ledger: Optional[MigrationLedger]
    log_label: str


@dataclass(frozen=True, slots=True)
class ExecuteRestructurePlanSinks:
    """Mutable evidence/artifact channels for restructure-plan execution."""

    lo_ops_out: Optional[list[LegalOperation]] = None
    findings_out: Optional[list[Finding]] = None


@dataclass(frozen=True, slots=True)
class ExecuteRestructurePlanResult:
    """Result of executing a StructuralTransformPlan inside replay."""

    state: ReplayState
    executed: bool


def emit_restructure_plan_renumber_legal_operations(
    *,
    lo_ops_out: Optional[list[LegalOperation]],
    migration_events: tuple[MigrationEvent, ...],
    amendment_id: str,
    source_title: str,
    amendment_issue_date: Optional[dt.date],
    amendment_effective_date: Optional[dt.date],
) -> int:
    """Emit explicit RENUMBER LOs for restructure-plan migration events.

    The restructure-plan executor records migration events, but
    ``compile_timelines()`` only tombstones the source lineage when it also
    sees an executable ``RENUMBER`` operation. Emit those bounded LOs here so
    scope-changing relabels do not leave their source timeline alive.
    """
    if lo_ops_out is None or not migration_events:
        return 0

    source = OperationSource(
        statute_id=amendment_id,
        title=source_title,
        enacted=amendment_issue_date.isoformat() if amendment_issue_date else "",
        effective=amendment_effective_date.isoformat() if amendment_effective_date else "",
        raw_text="",
    )
    emitted = 0
    for index, event in enumerate(migration_events, start=1):
        if event.kind != "renumber":
            continue
        lo_ops_out.append(
            LegalOperation(
                op_id=f"restructure_renumber_{amendment_id}_{index}",
                sequence=0,
                action=StructuralAction.RENUMBER,
                target=event.from_address,
                destination=event.to_address,
                source=source,
                group_id=f"finland-restructure:{amendment_id}",
                witness_rule_id=FI_RESTRUCTURE_RENUMBER_TIMELINE_RULE_ID,
            )
        )
        emitted += 1
    return emitted


def emit_restructure_skip_findings(
    exec_ops: Iterable[object],
    findings_out: Optional[list[Finding]],
    amendment_id: str,
) -> None:
    """Emit skip findings for restructure-plan ops that did not execute."""
    if findings_out is None:
        return
    for exec_op in exec_ops:
        for builder in (
            relabel_skip_finding,
            relabel_skip_source_pathology_finding,
            move_skip_finding,
            deferred_plan_op_finding,
        ):
            finding = builder(exec_op, source_statute=amendment_id)
            if finding is not None:
                findings_out.append(finding)


def execute_restructure_plan_with_evidence(
    request: ExecuteRestructurePlanRequest,
    sinks: ExecuteRestructurePlanSinks,
) -> ExecuteRestructurePlanResult:
    """Execute one restructure plan and emit its replay evidence."""
    migration_events_before = (
        len(request.migration_ledger)
        if request.migration_ledger is not None
        else 0
    )
    new_ir, exec_ops = execute_restructure_plan(
        request.plan,
        request.state.ir,
        migration_ledger=request.migration_ledger,
        effective_date=(
            request.amendment_effective_date.isoformat()
            if request.amendment_effective_date
            else ""
        ),
    )
    if not exec_ops:
        return ExecuteRestructurePlanResult(state=request.state, executed=False)

    executed_labels = [exec_op.note for exec_op in exec_ops if exec_op.success]
    skipped_labels = [exec_op.note for exec_op in exec_ops if not exec_op.success]
    state = request.state
    if executed_labels:
        state = state.with_ir(new_ir)
        if request.migration_ledger is not None:
            emit_restructure_plan_renumber_legal_operations(
                lo_ops_out=sinks.lo_ops_out,
                migration_events=request.migration_ledger.events[migration_events_before:],
                amendment_id=request.amendment_id,
                source_title=request.source_title,
                amendment_issue_date=request.amendment_issue_date,
                amendment_effective_date=request.amendment_effective_date,
            )
        replay_print(
            f"  [{request.amendment_id}] {request.log_label} executed: "
            f"{len(executed_labels)} ops"
        )
    if skipped_labels:
        logger.debug(
            "  [%s] %s skipped ops: %s",
            request.amendment_id,
            request.log_label,
            skipped_labels,
        )
        emit_restructure_skip_findings(
            exec_ops,
            sinks.findings_out,
            request.amendment_id,
        )
    return ExecuteRestructurePlanResult(
        state=state,
        executed=bool(executed_labels),
    )

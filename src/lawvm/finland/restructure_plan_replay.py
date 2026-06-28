"""Replay boundary for executing Finland structural-transform plans."""

from __future__ import annotations

import copy
import datetime as dt
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Optional

from lawvm.core.ir import IRNode, LegalAddress, LegalOperation, OperationSource
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.core.phase_result import Finding
from lawvm.core.provenance import MigrationEvent
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.core import tree_ops as _tops
from lawvm.finland.apply_runtime_support import _stamp_exact_section_snapshot_payload
from lawvm.finland.helpers import _norm_num_token
from lawvm.finland.migration_ledger import MigrationLedger
from lawvm.finland.replay_notices import replay_print
from lawvm.finland.restructure_plan import (
    ExecutedOp,
    StructuralTransformPlan,
    TransformOpKind,
    _RelabelLookupCache,
    _find_path_by_suffix,
    _resolve_live_section_snapshot_path,
    _resolve_section_node_at_live_path,
    _strip_hcontainer_from_path,
    deferred_plan_op_finding,
    execute_restructure_plan,
    move_skip_finding,
    relabel_migration_ledger_lookup_finding,
    relabel_structural_label_alias_lookup_finding,
    relabel_skip_finding,
    relabel_skip_source_pathology_finding,
)
from lawvm.finland.source_model import AmendmentSourceModel
from lawvm.finland.statute import ReplayState

logger = logging.getLogger(__name__)

FI_RESTRUCTURE_RENUMBER_TIMELINE_RULE_ID = "fi.restructure.renumber_timeline"
FI_RESTRUCTURE_RELABEL_SECTION_SNAPSHOT_RULE_ID = (
    "fi.restructure.relabel_section_snapshot"
)
FI_RESTRUCTURE_RELABEL_SECTION_SNAPSHOT_ATTR = "lawvm_restructure_relabel_section_snapshot"
FI_RESTRUCTURE_CHAPTER_PART_MOVE_TIMELINE_RULE_ID = (
    "fi.restructure.chapter_part_move_timeline"
)
FI_RESTRUCTURE_CHAPTER_PART_MOVE_LABEL_REUSE_GUARD_RULE_ID = (
    "fi.restructure.chapter_part_move_timeline.label_reuse_guard"
)
CHAPTER_PART_MOVE_LABEL_REUSE_SKIP_REASON = (
    "chapter_label_reuse_old_part_still_hosts_chapter"
)


def _mark_restructure_relabel_section_snapshot(payload: IRNode) -> IRNode:
    """Mark section snapshots emitted only to bridge restructure relabel lineage."""
    if payload.kind is not IRNodeKind.SECTION:
        return payload
    attrs = dict(payload.attrs)
    attrs[FI_RESTRUCTURE_RELABEL_SECTION_SNAPSHOT_ATTR] = "1"
    return IRNode(
        kind=payload.kind,
        label=payload.label,
        text=payload.text,
        attrs=attrs,
        children=payload.children,
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


def chapter_part_move_label_reuse_guard_finding(
    *,
    source_statute: str,
    chapter_label: str,
    old_part_label: str,
    new_part_label: str,
) -> Finding:
    """Record suppression of an inferred chapter part-move when labels collide."""
    return Finding(
        kind="APPLY.MOVE_SKIP",
        role="observation",
        stage="apply",
        blocking=False,
        source_statute=source_statute,
        detail={
            "message": (
                "Inferred chapter part-move suppressed because the same chapter "
                "label still lives under its pre-amendment part."
            ),
            "reason_code": CHAPTER_PART_MOVE_LABEL_REUSE_SKIP_REASON,
            "witness_rule_id": FI_RESTRUCTURE_CHAPTER_PART_MOVE_LABEL_REUSE_GUARD_RULE_ID,
            "chapter_label": chapter_label,
            "old_part_label": old_part_label,
            "new_part_label": new_part_label,
        },
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
    source_model: AmendmentSourceModel | None = None


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


def _restructure_lineage_date(
    *,
    amendment_issue_date: Optional[dt.date],
    amendment_effective_date: Optional[dt.date],
) -> str:
    """Date used to anchor restructure migration lineage.

    Some historical Finland amendment streams carry an owned issue date but no
    parsed commencement for the restructure-plan lane. Leaving relabel
    migration events undated makes them timeless and can project a later native
    rebirth through an older occupant's renumber. The issue date is the
    conservative lineage lower bound when no explicit effective date is
    available; temporal uncertainty remains visible through the existing
    temporal findings.
    """
    if amendment_effective_date is not None:
        return amendment_effective_date.isoformat()
    if amendment_issue_date is not None:
        return amendment_issue_date.isoformat()
    return ""


def _is_substantive_section_payload(payload: IRNode | None) -> bool:
    if payload is None or payload.kind is not IRNodeKind.SECTION:
        return False
    return any(
        child.kind
        not in {
            IRNodeKind.NUM,
            IRNodeKind.HEADING,
            IRNodeKind.OMISSION,
        }
        and bool(irnode_to_text(child).strip())
        for child in payload.children
    )


def source_destination_relabel_snapshot_payload(
    source_model: AmendmentSourceModel | None,
    live_path: tuple[tuple[str, str], ...],
) -> IRNode | None:
    """Return amendment-body payload for a section relabel destination.

    In forms such as ``muutettu 27 f § ... siirtyy 27 g §:ksi`` the source
    body prints the changed continuing provision under the destination label.
    The relabel executor only has the pre-change live node, so the snapshot
    bridge prefers the source model's typed destination payload when present.
    """
    if source_model is None or not live_path or live_path[-1][0] != "section":
        return None

    by_kind = {kind: label for kind, label in live_path}
    section = _norm_num_token(by_kind.get("section", ""))
    if not section:
        return None

    query_scopes = (
        (
            _norm_num_token(by_kind.get("chapter", "")) or None,
            _norm_num_token(by_kind.get("part", "")) or None,
        ),
        (None, None),
    )
    seen: set[tuple[str | None, str | None]] = set()
    for chapter, part in query_scopes:
        key = (chapter, part)
        if key in seen:
            continue
        seen.add(key)
        lookup = source_model.lookup_payload_ir("section", section, chapter, part)
        payload = lookup.payload_ir
        if (
            payload is not None
            and payload.kind is IRNodeKind.SECTION
            and _norm_num_token(payload.label or "") == section
            and _is_substantive_section_payload(payload)
        ):
            return payload
    return None


def _apply_source_destination_relabel_payloads(
    state_ir: IRNode,
    executed_ops: Iterable[ExecutedOp],
    source_model: AmendmentSourceModel | None,
) -> IRNode:
    if source_model is None:
        return state_ir

    updated = state_ir
    lookup_cache = _RelabelLookupCache()
    for exec_op in executed_ops:
        if (
            not exec_op.success
            or exec_op.op.kind is not TransformOpKind.RELABEL
            or exec_op.applied_path is None
            or exec_op.applied_path[-1][0] != "section"
        ):
            continue
        tree_path = _resolve_live_section_tree_path(
            updated,
            exec_op.applied_path,
            lookup_cache=lookup_cache,
        )
        if tree_path is None:
            continue
        payload = source_destination_relabel_snapshot_payload(source_model, tree_path)
        if payload is None:
            continue
        updated = _tops.replace_at(updated, tree_path, copy.deepcopy(payload))
        lookup_cache = _RelabelLookupCache()
    return updated


def _resolve_live_section_tree_path(
    tree: IRNode,
    applied_path: tuple[tuple[str, str], ...],
    *,
    lookup_cache: _RelabelLookupCache | None = None,
) -> tuple[tuple[str, str], ...] | None:
    """Map a relabel-time section path to the actual IR path, wrappers included."""
    stripped = _strip_hcontainer_from_path(applied_path)
    if not stripped or stripped[-1][0] != "section":
        return None
    if _tops.resolve(tree, stripped) is not None:
        return stripped
    internal_prefixes = (("hcontainer", ""), ("hcontainer", "statuteProvisionsWrapper"))
    for prefix in internal_prefixes:
        prefixed = (prefix,) + stripped
        if _tops.resolve(tree, prefixed) is not None:
            return prefixed
    found = _find_path_by_suffix(tree, list(stripped), lookup_cache=lookup_cache)
    if found is not None:
        return found
    if len(stripped) >= 3 and stripped[0][0] == "part":
        found = _find_path_by_suffix(tree, list(stripped[1:]), lookup_cache=lookup_cache)
        if found is not None:
            return found
    return None


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

    source_enacted = amendment_issue_date.isoformat() if amendment_issue_date else ""
    source_effective = _restructure_lineage_date(
        amendment_issue_date=amendment_issue_date,
        amendment_effective_date=amendment_effective_date,
    )
    emitted = 0
    for index, event in enumerate(migration_events, start=1):
        if event.kind != "renumber":
            continue
        if event.from_address.path == event.to_address.path:
            continue
        source = OperationSource(
            statute_id=amendment_id,
            title=source_title,
            enacted=source_enacted,
            effective=event.effective or source_effective,
            raw_text="",
        )
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


def emit_restructure_plan_section_snapshot_legal_operations(
    *,
    lo_ops_out: Optional[list[LegalOperation]],
    state_ir: IRNode,
    executed_ops: Iterable[ExecutedOp],
    amendment_id: str,
    source_title: str,
    amendment_issue_date: Optional[dt.date],
    amendment_effective_date: Optional[dt.date],
    source_model: AmendmentSourceModel | None = None,
) -> int:
    """Emit payload snapshots for successful section relabels at their live paths.

    Restructure relabels can land sections on addresses that differ from the
    amendment-frame migration ``to_address``. Timeline compilation needs a
    payload snapshot at the live post-relabel path, not only the paired
    payload-less ``RENUMBER`` LO.
    """
    if lo_ops_out is None:
        return 0

    source_effective = _restructure_lineage_date(
        amendment_issue_date=amendment_issue_date,
        amendment_effective_date=amendment_effective_date,
    )
    source = OperationSource(
        statute_id=amendment_id,
        title=source_title,
        enacted=amendment_issue_date.isoformat() if amendment_issue_date else "",
        effective=source_effective,
        raw_text="",
    )
    existing_op_ids = {op.op_id for op in lo_ops_out}
    existing_payload_targets = {
        op.target
        for op in lo_ops_out
        if op.payload is not None
        and op.source is not None
        and op.source.statute_id == amendment_id
    }
    lookup_cache = _RelabelLookupCache()
    emitted = 0
    for exec_op in executed_ops:
        if (
            not exec_op.success
            or exec_op.op.kind is not TransformOpKind.RELABEL
            or exec_op.applied_path is None
            or exec_op.applied_path[-1][0] != "section"
        ):
            continue
        live_path = _resolve_live_section_snapshot_path(
            state_ir,
            exec_op.applied_path,
            lookup_cache=lookup_cache,
        )
        if live_path is None:
            continue
        address = LegalAddress(path=live_path)
        if address in existing_payload_targets:
            continue
        section_label = live_path[-1][1]
        op_id = f"snapshot_section_{section_label}_restructure_{amendment_id}"
        if op_id in existing_op_ids:
            continue
        payload = source_destination_relabel_snapshot_payload(source_model, live_path)
        if payload is None:
            payload = exec_op.snapshot_payload
        if payload is None:
            payload = _resolve_section_node_at_live_path(state_ir, live_path)
        if payload is None or payload.kind is not IRNodeKind.SECTION:
            continue
        lo_ops_out.append(
            LegalOperation(
                op_id=op_id,
                sequence=0,
                action=StructuralAction.INSERT,
                target=address,
                payload=_stamp_exact_section_snapshot_payload(
                    _mark_restructure_relabel_section_snapshot(
                        copy.deepcopy(payload)
                    )
                ),
                source=source,
                group_id=f"finland-restructure:{amendment_id}",
                witness_rule_id=FI_RESTRUCTURE_RELABEL_SECTION_SNAPSHOT_RULE_ID,
            )
        )
        existing_op_ids.add(op_id)
        existing_payload_targets.add(address)
        emitted += 1
    return emitted


def emit_restructure_skip_findings(
    exec_ops: Iterable[ExecutedOp],
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
            relabel_migration_ledger_lookup_finding,
            relabel_structural_label_alias_lookup_finding,
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
        effective_date=_restructure_lineage_date(
            amendment_issue_date=request.amendment_issue_date,
            amendment_effective_date=request.amendment_effective_date,
        ),
    )
    if not exec_ops:
        return ExecuteRestructurePlanResult(state=request.state, executed=False)

    executed_labels = [exec_op.note for exec_op in exec_ops if exec_op.success]
    skipped_labels = [exec_op.note for exec_op in exec_ops if not exec_op.success]
    state = request.state
    if executed_labels:
        new_ir = _apply_source_destination_relabel_payloads(
            new_ir,
            exec_ops,
            request.source_model,
        )
        state = state.with_ir(new_ir)
        if request.migration_ledger is not None:
            wave_events = request.migration_ledger.events[migration_events_before:]
            emit_restructure_plan_renumber_legal_operations(
                lo_ops_out=sinks.lo_ops_out,
                migration_events=wave_events,
                amendment_id=request.amendment_id,
                source_title=request.source_title,
                amendment_issue_date=request.amendment_issue_date,
                amendment_effective_date=request.amendment_effective_date,
            )
        emit_restructure_plan_section_snapshot_legal_operations(
            lo_ops_out=sinks.lo_ops_out,
            state_ir=state.ir,
            executed_ops=exec_ops,
            amendment_id=request.amendment_id,
            source_title=request.source_title,
            amendment_issue_date=request.amendment_issue_date,
            amendment_effective_date=request.amendment_effective_date,
            source_model=request.source_model,
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

"""Supplemental recovery tail for Finland resolved-op replay apply."""
from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from typing import Dict, List, Literal, Optional, Set

from lawvm.core import tree_ops as _tops
from lawvm.core.compile_result import SourcePathology, StrictProfile
from lawvm.core.ir import IRNode, LegalOperation, OperationSource
from lawvm.core.mutation_accounting import MutationAccountingResult
from lawvm.core.observed_write_audit import ObservedWriteAudit
from lawvm.core.phase_result import Finding
from lawvm.core.write_receipt import WriteReceipt
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.statute_validity import expires_on_from_valid_until
from lawvm.finland.amendment_chapter_precreate import (
    ChapterRef,
)
from lawvm.finland.apply_events import ApplyMutationEvent
from lawvm.finland.apply_resolved_op import (
    ApplyResolvedOpRequest,
    ApplyResolvedOpSinks,
    apply_resolved_op_with_audit,
)
from lawvm.finland.apply_runtime_support import _emit_section_snapshot
from lawvm.finland.future_repeal import RepealTargetRef
from lawvm.finland.uncovered_body_recovery import (
    UncoveredBodyRecoveryRequest,
    UncoveredBodyRecoverySinks,
    recover_uncovered_body_ops,
)
from lawvm.finland.uncovered_recovery_findings import (
    _strict_rejected_uncovered_body_finding,
)
from lawvm.finland.uncovered_chapter_scaffold import (
    UncoveredChapterScaffoldDraft,
    build_uncovered_chapter_scaffold_lo,
)
from lawvm.finland.uncovered_kumotaan_recovery import (
    KumotaanRecoveryRequest,
    KumotaanRecoverySinks,
    _apply_uncovered_kumotaan_typed,
)
from lawvm.finland.group_ops import append_compiled_group_ops as _append_compiled_group_ops
from lawvm.finland.migration_ledger import MigrationLedger
from lawvm.finland.ops import AmendmentOp, FailedOp
from lawvm.finland.replay_notices import replay_print as _replay_print
from lawvm.finland.restructure_plan import StructuralTransformPlan
from lawvm.finland.restructure_plan_replay import (
    ChapterPartMoveTimelineRequest as _ChapterPartMoveTimelineRequest,
    ExecuteRestructurePlanRequest as _ExecuteRestructurePlanRequest,
    ExecuteRestructurePlanSinks as _ExecuteRestructurePlanSinks,
    build_chapter_part_move_timeline_ops as _build_chapter_part_move_timeline_ops,
    chapter_part_move_label_reuse_guard_finding as _chapter_part_move_label_reuse_guard_finding,
    execute_restructure_plan_with_evidence as _execute_restructure_plan_with_evidence,
)
from lawvm.finland.standalone_targets import StandaloneSectionTarget
from lawvm.finland.source_model import AmendmentSourceModel
from lawvm.finland.statute import ReplayState, StatuteContext

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ApplySupplementalRecoveryRequest:
    """Semantic inputs for post-fold supplemental replay recovery."""

    state: ReplayState
    ctx: StatuteContext
    ops: List[AmendmentOp]
    source_model: AmendmentSourceModel
    johto: str
    amendment_id: str
    source_title: str
    amendment_issue_date: Optional[dt.date]
    amendment_effective_date: Optional[dt.date]
    amendment_expiry_date: Optional[dt.date]
    replay_mode: Literal["official_consolidation", "legal_pit"]
    strict_profile: Optional[StrictProfile]
    vts_ops_enrich_done: bool
    future_repeals: Optional[Set[RepealTargetRef]]
    base_ir: IRNode
    pre_real_chapter_refs: tuple[ChapterRef, ...]
    pre_pseudo_chapter_refs: tuple[ChapterRef, ...]
    ch_to_part_before: Dict[str, str]
    parts_before: Set[str]
    executed_restructure_plan_ids: Set[str]
    standalone_section_targets: frozenset[StandaloneSectionTarget]
    migration_ledger: Optional[MigrationLedger]


@dataclass(frozen=True, slots=True)
class ApplySupplementalRecoverySinks:
    """Mutable evidence/artifact channels for supplemental replay recovery."""

    compiled_ops_out: Optional[List[Dict[str, object]]] = None
    lo_ops_out: Optional[List[LegalOperation]] = None
    failed_ops_out: Optional[List[FailedOp]] = None
    source_pathologies_out: Optional[List[SourcePathology]] = None
    mutation_events_out: Optional[List[ApplyMutationEvent]] = None
    restructure_plans_out: Optional[List[StructuralTransformPlan]] = None
    observations_out: Optional[List[Dict[str, object]]] = None
    findings_out: Optional[List[Finding]] = None
    observed_touch_results_out: Optional[List[MutationAccountingResult]] = None
    write_audits_out: Optional[List[ObservedWriteAudit]] = None
    write_receipts_out: Optional[List[WriteReceipt]] = None


@dataclass(frozen=True, slots=True)
class ApplySupplementalRecoveryResult:
    state: ReplayState
    executed_restructure_plan_ids: frozenset[str]


def _operation_source_for_uncovered_recovery(
    request: ApplySupplementalRecoveryRequest,
    *,
    lo_ops_out: Optional[List[LegalOperation]],
) -> Optional[OperationSource]:
    if lo_ops_out is None:
        return None
    return OperationSource(
        statute_id=request.amendment_id,
        title=request.source_title,
        effective=request.amendment_effective_date.isoformat()
        if request.amendment_effective_date
        else "",
        enacted=request.amendment_issue_date.isoformat()
        if request.amendment_issue_date
        else "",
        # amendment_expiry_date is the prose-inclusive last in-force day; the
        # kernel `expires` field is an exclusive cutoff.
        expires=(
            expires_on_from_valid_until(request.amendment_expiry_date).isoformat()
            if request.amendment_expiry_date
            else ""
        ),
    )


def _emit_chapter_scaffold_ops(
    *,
    state: ReplayState,
    chapter_refs: List[ChapterRef] | tuple[ChapterRef, ...],
    op_id_prefix: str,
    source: OperationSource,
    amendment_id: str,
    lo_ops_out: List[LegalOperation],
    log_label: str,
) -> None:
    for chapter_ref in chapter_refs:
        part_label = chapter_ref.part_label
        chapter_label = chapter_ref.chapter_label
        if part_label:
            part_path = state.find("part", part_label)
            part_node = _tops.resolve(state.ir, part_path) if part_path is not None else None
            local_ch_path = (
                _tops.find(part_node, "chapter", chapter_label)
                if part_node is not None
                else None
            )
            ch_path = (
                part_path + local_ch_path
                if part_path is not None and local_ch_path is not None
                else None
            )
        else:
            ch_path = state.find("chapter", chapter_label)
        ch_node = _tops.resolve(state.ir, ch_path) if ch_path else None
        if ch_path is None or ch_node is None:
            continue
        ch_tl_path = tuple((kind, label) for kind, label in ch_path if label)
        lo_ops_out.append(
            build_uncovered_chapter_scaffold_lo(
                UncoveredChapterScaffoldDraft(
                    op_id=f"{op_id_prefix}_{part_label or 'root'}_{chapter_label}",
                    path=ch_tl_path,
                    payload=ch_node,
                    source=source,
                    amendment_id=amendment_id,
                )
            )
        )
        logger.debug(
            "  [%s] %s %s/%s (path=%s)",
            amendment_id,
            log_label,
            part_label or "-",
            chapter_label,
            ch_tl_path,
        )


def run_apply_supplemental_recovery(
    request: ApplySupplementalRecoveryRequest,
    sinks: ApplySupplementalRecoverySinks,
) -> ApplySupplementalRecoveryResult:
    """Run uncovered-body, kumotaan, and chapter-move recovery after apply fold."""
    state = request.state
    executed_restructure_plan_ids = set(request.executed_restructure_plan_ids)
    lo_ops_out = sinks.lo_ops_out
    ops = request.ops

    if not ops and lo_ops_out is None:
        return ApplySupplementalRecoveryResult(
            state=state,
            executed_restructure_plan_ids=frozenset(executed_restructure_plan_ids),
        )

    uncov_src = _operation_source_for_uncovered_recovery(
        request,
        lo_ops_out=lo_ops_out,
    )
    uncov_allowed = not request.vts_ops_enrich_done and (
        request.strict_profile is None
        or request.strict_profile.allows_uncovered_body_recovery
    )

    if ops and uncov_allowed:
        new_chapter_refs = list(request.pre_real_chapter_refs)
        late_new_chapters = request.source_model.pre_create_amendment_chapters(
            state,
            request.amendment_id,
        )
        if late_new_chapters is not None:
            state = late_new_chapters.state
            new_chapter_refs = list(
                dict.fromkeys(
                    (*request.pre_real_chapter_refs, *late_new_chapters.created_refs)
                )
            )
            if lo_ops_out is not None and uncov_src is not None and new_chapter_refs:
                _emit_chapter_scaffold_ops(
                    state=state,
                    chapter_refs=new_chapter_refs,
                    op_id_prefix="uncov_chapter_create",
                    source=uncov_src,
                    amendment_id=request.amendment_id,
                    lo_ops_out=lo_ops_out,
                    log_label="uncovered chapter LO INSERT",
                )
            if (
                lo_ops_out is not None
                and uncov_src is not None
                and request.pre_pseudo_chapter_refs
            ):
                _emit_chapter_scaffold_ops(
                    state=state,
                    chapter_refs=request.pre_pseudo_chapter_refs,
                    op_id_prefix="pseudo_chapter_create",
                    source=uncov_src,
                    amendment_id=request.amendment_id,
                    lo_ops_out=lo_ops_out,
                    log_label="pseudo-chapter LO INSERT",
                )

        new_chapter_labels = [ref.chapter_label for ref in new_chapter_refs]
        pre_pseudo_chapter_labels = [
            ref.chapter_label for ref in request.pre_pseudo_chapter_refs
        ]
        uncov_recovery = recover_uncovered_body_ops(
            UncoveredBodyRecoveryRequest(
                state=state,
                ctx=request.ctx,
                ops=ops,
                source_model=request.source_model,
                amendment_id=request.amendment_id,
                future_repeals=request.future_repeals,
                op_source=uncov_src,
                new_chapter_labels=set(new_chapter_labels)
                | set(pre_pseudo_chapter_labels),
            ),
            UncoveredBodyRecoverySinks(
                failed_ops_out=sinks.failed_ops_out,
                restructure_plans_out=sinks.restructure_plans_out,
                observations_out=sinks.observations_out,
                findings_out=sinks.findings_out,
            ),
        )
        uncov_rops = list(uncov_recovery.recovered_ops)
        if sinks.observations_out is not None:
            sinks.observations_out.extend(
                audit.to_observation(source_statute=request.amendment_id)
                for audit in uncov_recovery.candidate_audits
            )
        _append_compiled_group_ops(sinks.compiled_ops_out, uncov_rops)

        if sinks.restructure_plans_out:
            for plan in sinks.restructure_plans_out:
                if (
                    plan.amendment_id == request.amendment_id
                    and plan.has_unexecuted_ops
                    and plan.amendment_id not in executed_restructure_plan_ids
                ):
                    restructure_result = _execute_restructure_plan_with_evidence(
                        _ExecuteRestructurePlanRequest(
                            state=state,
                            plan=plan,
                            amendment_id=request.amendment_id,
                            source_title=request.source_title,
                            amendment_issue_date=request.amendment_issue_date,
                            amendment_effective_date=request.amendment_effective_date,
                            migration_ledger=request.migration_ledger,
                            log_label="restructure_plan",
                            source_model=request.source_model,
                        ),
                        _ExecuteRestructurePlanSinks(
                            lo_ops_out=lo_ops_out,
                            findings_out=sinks.findings_out,
                        ),
                    )
                    state = restructure_result.state
                    if restructure_result.executed:
                        executed_restructure_plan_ids.add(plan.amendment_id)

        replaced_labels: List[str] = []
        inserted_labels: List[str] = []
        for rop in uncov_rops:
            apply_result = apply_resolved_op_with_audit(
                ApplyResolvedOpRequest(
                    state=state,
                    ctx=request.ctx,
                    rop=rop,
                    amendment_id=request.amendment_id,
                    replay_mode=request.replay_mode,
                    migration_ledger=request.migration_ledger,
                    strict_profile=request.strict_profile,
                    error_prefix="uncovered rop",
                    force_apply_pass=True,
                ),
                ApplyResolvedOpSinks(
                    write_receipts_out=(
                        sinks.write_receipts_out
                        if sinks.write_receipts_out is not None
                        else []
                    ),
                    write_audits_out=(
                        sinks.write_audits_out
                        if sinks.write_audits_out is not None
                        else []
                    ),
                    lo_ops_out=lo_ops_out,
                    failed_ops_out=sinks.failed_ops_out,
                    source_pathologies_out=sinks.source_pathologies_out,
                    mutation_events_out=sinks.mutation_events_out,
                    findings_out=sinks.findings_out,
                    observed_touch_results_out=sinks.observed_touch_results_out,
                ),
            )
            state = apply_result.state
            if sinks.observations_out is not None:
                sinks.observations_out.append(apply_result.audit.to_observation())
            if apply_result.disposition == "APPLY_FAILED":
                continue
            if lo_ops_out is not None:
                snapshot_group = rop.resolved_group_key_view
                _emit_section_snapshot(
                    state,
                    snapshot_group.unit_kind,
                    snapshot_group.target_norm,
                    snapshot_group.target_chapter,
                    snapshot_group.target_part,
                    [rop],
                    lo_ops_out,
                    request.amendment_id,
                    request.source_title,
                    request.amendment_issue_date,
                    request.amendment_effective_date,
                    base_ir=request.base_ir,
                    migration_ledger=request.migration_ledger,
                    standalone_section_targets=request.standalone_section_targets,
                )
            if rop.is_replace_action:
                replaced_labels.append(rop.target_norm)
            else:
                inserted_labels.append(rop.target_norm)
        if replaced_labels:
            _replay_print(
                f"  [{request.amendment_id}] uncovered section replaces: {replaced_labels}"
            )
        if inserted_labels:
            _replay_print(
                f"  [{request.amendment_id}] uncovered section inserts: {inserted_labels}"
            )
    elif ops and not request.vts_ops_enrich_done and not uncov_allowed:
        finding = _strict_rejected_uncovered_body_finding(
            source_statute=request.amendment_id,
            stage="apply",
        )
        if sinks.findings_out is not None:
            sinks.findings_out.append(finding)

    if uncov_allowed or request.vts_ops_enrich_done:
        kumotaan_recovery = _apply_uncovered_kumotaan_typed(
            KumotaanRecoveryRequest(
                state=state,
                ctx=request.ctx,
                ops=ops,
                johto=request.johto,
                amendment_id=request.amendment_id,
                op_source=uncov_src,
            ),
            KumotaanRecoverySinks(
                lo_ops_out=lo_ops_out,
                findings_out=sinks.findings_out,
                source_pathologies_out=sinks.source_pathologies_out,
            ),
        )
        state = kumotaan_recovery.state

    if lo_ops_out is not None and uncov_src is not None and request.ch_to_part_before:
        pp_after = _tops.find_provisions_parent(state.ir)
        pp_after_node = _tops.resolve(state.ir, pp_after) if pp_after else state.ir
        if pp_after_node is not None:
            parts_after: set[str] = {
                part.label
                for part in pp_after_node.children
                if part.kind is IRNodeKind.PART and part.label
            }
            for part_node in pp_after_node.children:
                if part_node.kind is not IRNodeKind.PART or not part_node.label:
                    continue
                for chapter_node in part_node.children:
                    if (
                        chapter_node.kind is not IRNodeKind.CHAPTER
                        or not chapter_node.label
                    ):
                        continue
                    old_part = request.ch_to_part_before.get(chapter_node.label)
                    if old_part is None or old_part == part_node.label:
                        continue
                    if part_node.label in request.parts_before:
                        continue
                    if old_part not in parts_after:
                        continue
                    # Same chapter label under a different part is common when a
                    # genuinely new part is inserted (e.g. 1929/234 part V
                    # rebirth). Only emit part-move timeline LOs when the
                    # chapter no longer lives under its pre-amendment part —
                    # otherwise we silently repeal unrelated legal state.
                    old_part_path = _tops.find(state.ir, "part", old_part)
                    if old_part_path is not None:
                        old_part_node = _tops.resolve(state.ir, old_part_path)
                        if old_part_node is not None:
                            old_ch_local = _tops.find(
                                old_part_node,
                                "chapter",
                                chapter_node.label,
                            )
                            if old_ch_local is not None:
                                if sinks.findings_out is not None:
                                    sinks.findings_out.append(
                                        _chapter_part_move_label_reuse_guard_finding(
                                            source_statute=request.amendment_id,
                                            chapter_label=chapter_node.label,
                                            old_part_label=old_part,
                                            new_part_label=part_node.label,
                                        )
                                    )
                                continue
                    part_move_ops = _build_chapter_part_move_timeline_ops(
                        _ChapterPartMoveTimelineRequest(
                            amendment_id=request.amendment_id,
                            chapter_label=chapter_node.label,
                            old_part_label=old_part,
                            new_part_label=part_node.label,
                            payload=chapter_node,
                            source=uncov_src,
                        )
                    )
                    lo_ops_out.append(part_move_ops.repeal)
                    lo_ops_out.append(part_move_ops.insert)
                    logger.debug(
                        "  [%s] chapter part-move LO: ch:%s part:%s -> part:%s",
                        request.amendment_id,
                        chapter_node.label,
                        old_part,
                        part_node.label,
                    )

    return ApplySupplementalRecoveryResult(
        state=state,
        executed_restructure_plan_ids=frozenset(executed_restructure_plan_ids),
    )

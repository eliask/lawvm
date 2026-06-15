"""Typed uncovered-body recovery coordinator for Finland amendment replay."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, List, Optional, Set, Tuple

import lxml.etree as etree

from lawvm.core.ir import OperationSource
from lawvm.core.phase_result import Finding
from lawvm.finland.body_coverage import analyze_coverage
from lawvm.finland.body_pairing import assign_body_units_subtree_aware
from lawvm.finland.future_repeal import RepealTargetRef
from lawvm.finland.johto_scope_mentions import (
    collect_johto_mentioned_section_labels as _collect_johto_mentioned_section_labels_impl,
    collect_johto_mentioned_section_labels_frozenset as _collect_johto_mentioned_section_labels_frozenset_impl,
    expand_johto_section_label_range as _expand_johto_section_label_range_impl,
)
from lawvm.finland.ops import AmendmentOp, FailedOp, ResolvedOp
from lawvm.finland.restructure_plan import StructuralTransformPlan
from lawvm.finland.uncovered_recovery_iteration import (
    peg_owned_section_targets,
    run_uncovered_candidate_iteration,
)
from lawvm.finland.uncovered_recovery_prepare import (
    UncoveredRecoveryPreparationRequest,
    prepare_uncovered_body_recovery,
)
from lawvm.finland.uncovered_recovery_runner import UncoveredRecoveryRun
from lawvm.finland.uncovered_recovery_state import RecoveryState, UncoveredCandidateAudit

if TYPE_CHECKING:
    from lawvm.finland.statute import ReplayState, StatuteContext


@dataclass(frozen=True, slots=True)
class UncoveredBodyRecoveryRequest:
    """Semantic inputs for uncovered-body recovery over one amendment body."""

    state: "ReplayState"
    ctx: "StatuteContext"
    ops: List[AmendmentOp]
    muutos_tree: etree._Element
    amendment_id: str
    future_repeals: Optional[Set[RepealTargetRef]] = None
    op_source: Optional[OperationSource] = None
    new_chapter_labels: Optional[Set[str]] = None


@dataclass(frozen=True, slots=True)
class UncoveredBodyRecoverySinks:
    """Mutable evidence/output channels for uncovered-body recovery."""

    failed_ops_out: Optional[List[FailedOp]] = None
    restructure_plans_out: Optional[List[StructuralTransformPlan]] = None
    observations_out: Optional[List[Dict[str, object]]] = None
    findings_out: Optional[List[Finding]] = None


@dataclass(frozen=True, slots=True)
class UncoveredBodyRecoveryResult:
    """Recovered operations plus the per-candidate audit trail that produced them."""

    recovered_ops: Tuple[ResolvedOp, ...]
    candidate_audits: Tuple[UncoveredCandidateAudit, ...]


def _expand_johto_section_label_range(start: str, end: str) -> tuple[str, ...]:
    return _expand_johto_section_label_range_impl(start, end)


def _collect_johto_mentioned_section_labels(johto_text: str) -> set[str]:
    return _collect_johto_mentioned_section_labels_impl(johto_text)


def _collect_johto_mentioned_section_labels_frozenset(johto_text: str) -> frozenset[str]:
    return _collect_johto_mentioned_section_labels_frozenset_impl(johto_text)


def recover_uncovered_body_ops(
    request: UncoveredBodyRecoveryRequest,
    sinks: Optional[UncoveredBodyRecoverySinks] = None,
) -> UncoveredBodyRecoveryResult:
    """Collect body-driven ResolvedOps for sections not covered by parsed ops."""
    sinks = sinks or UncoveredBodyRecoverySinks()
    preparation = prepare_uncovered_body_recovery(
        UncoveredRecoveryPreparationRequest(
            statute_id=request.ctx.id,
            amendment_id=request.amendment_id,
            ops=request.ops,
            muutos_tree=request.muutos_tree,
            failed_ops_out=sinks.failed_ops_out,
            new_chapter_labels=request.new_chapter_labels,
            restructure_plans_out=sinks.restructure_plans_out,
            observations_out=sinks.observations_out,
            findings_out=sinks.findings_out,
            analyze_coverage_fn=analyze_coverage,
            assign_body_units_fn=assign_body_units_subtree_aware,
        )
    )
    if not preparation.has_body:
        return UncoveredBodyRecoveryResult(recovered_ops=(), candidate_audits=())

    rstate = RecoveryState(
        amendment_id=request.amendment_id,
        op_source=request.op_source,
        findings_out=sinks.findings_out,
        guards=preparation.recovery_guards,
    )
    recovery_run = UncoveredRecoveryRun(
        state=request.state,
        ops=request.ops,
        amendment_id=request.amendment_id,
        future_repeals=request.future_repeals,
        new_chapter_labels=request.new_chapter_labels,
        has_content_ops=preparation.has_content_ops,
        rstate=rstate,
        recovery_guards=preparation.recovery_guards,
        bp_assignments=preparation.body_pairing_assignments,
        johto_mentioned_labels=set(preparation.context.johto_mentioned_labels),
        johto_mentioned_replaced_chapters=set(
            preparation.context.johto_mentioned_replaced_chapters
        ),
        moved_section_destinations=preparation.context.moved_section_destinations,
        owned_chapter_labels=set(preparation.context.owned_chapter_labels),
    )

    run_uncovered_candidate_iteration(
        supplemental_candidates=preparation.cov_report.supplemental_candidates,
        peg_owned_targets=peg_owned_section_targets(request.ops),
        processor=recovery_run,
    )
    rstate.emit_chapter_payload_mixed_findings()

    return UncoveredBodyRecoveryResult(
        recovered_ops=tuple(rstate.result),
        candidate_audits=tuple(rstate.audits),
    )

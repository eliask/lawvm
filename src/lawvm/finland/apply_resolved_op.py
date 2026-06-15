"""Typed resolved-op apply boundary with replay evidence."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal, Optional

from lawvm.core.compile_result import SourcePathology, StrictProfile
from lawvm.core.ir import LegalOperation
from lawvm.core.mutation_accounting import (
    MutationAccountingResult,
    observed_vs_declared_cross_check,
)
from lawvm.core.mutation_boundary import diff_ir_paths_identity_pruned
from lawvm.core.observed_write_audit import ObservedWriteAudit
from lawvm.core.phase_result import Finding
from lawvm.finland.apply import apply_op
from lawvm.finland.apply_events import ApplyMutationEvent
from lawvm.finland.migration_ledger import MigrationLedger
from lawvm.finland.ops import FailedOp, ResolvedOp
from lawvm.finland.replay_notices import replay_print as _replay_print
from lawvm.finland.standalone_targets import StandaloneSectionTarget
from lawvm.finland.statute import ReplayState, StatuteContext

logger = logging.getLogger(__name__)

# Stage-0 passive observed-vs-declared tree cross-check. Defaults ON: after each
# applied op the replay fold computes an identity-pruned structural diff of the
# before/after IR and verifies every observed changed path is explained by the
# op's declared mutation-event paths. It only records findings; it never alters
# replay behavior, so it is safe to leave enabled.
OBSERVED_MUTATION_CROSS_CHECK_ENABLED = True

ApplyDisposition = Literal["APPLIED", "APPLY_FAILED", "NO_APPLY_PASS"]
APPLY_RESOLVED_OP_AUDIT_KIND = "APPLY.RESOLVED_OP_AUDIT"
FI_APPLY_RESOLVED_OP_RULE_ID = "fi.apply.resolved_op"


@dataclass(frozen=True, slots=True)
class ApplyResolvedOpAudit:
    """One visible audit record for a resolved op considered by apply."""

    source_statute: str
    op_id: str
    description: str
    target_unit_kind: str
    target_norm: str
    target_chapter: str
    target_part: str
    disposition: ApplyDisposition

    def to_observation(self) -> dict[str, object]:
        return {
            "kind": APPLY_RESOLVED_OP_AUDIT_KIND,
            "source_statute": self.source_statute,
            "detail": {
                "rule_id": FI_APPLY_RESOLVED_OP_RULE_ID,
                "op_id": self.op_id,
                "description": self.description,
                "target_unit_kind": self.target_unit_kind,
                "target_norm": self.target_norm,
                "target_chapter": self.target_chapter,
                "target_part": self.target_part,
                "disposition": self.disposition,
            },
        }


@dataclass(frozen=True, slots=True)
class ApplyResolvedOpRequest:
    """Semantic inputs for one ResolvedOp apply pass."""

    state: ReplayState
    ctx: StatuteContext
    rop: ResolvedOp
    amendment_id: str
    replay_mode: Literal["official_consolidation", "legal_pit"]
    path_hint: tuple[tuple[str, str], ...] | None = None
    standalone_section_targets: frozenset[StandaloneSectionTarget] = frozenset()
    migration_ledger: Optional[MigrationLedger] = None
    strict_profile: Optional[StrictProfile] = None
    error_prefix: str = ""
    force_apply_pass: bool = False


@dataclass(frozen=True, slots=True)
class ApplyResolvedOpSinks:
    """Mutable evidence/artifact channels for one ResolvedOp apply pass."""

    lo_ops_out: Optional[list[LegalOperation]] = None
    failed_ops_out: Optional[list[FailedOp]] = None
    source_pathologies_out: Optional[list[SourcePathology]] = None
    mutation_events_out: Optional[list[ApplyMutationEvent]] = None
    findings_out: Optional[list[Finding]] = None
    observed_touch_results_out: Optional[list[MutationAccountingResult]] = None
    write_audits_out: Optional[list[ObservedWriteAudit]] = None


@dataclass(frozen=True, slots=True)
class ApplyResolvedOpResult:
    """Result of one ResolvedOp apply pass."""

    state: ReplayState
    disposition: ApplyDisposition
    audit: ApplyResolvedOpAudit


def apply_resolved_op_with_audit(
    request: ApplyResolvedOpRequest,
    sinks: ApplyResolvedOpSinks,
) -> ApplyResolvedOpResult:
    """Apply one ResolvedOp and cross-check declared mutation evidence."""
    state = request.state
    rop = request.rop
    disposition: ApplyDisposition = "NO_APPLY_PASS"
    if request.force_apply_pass or rop.replay_requires_apply_pass:
        state, disposition = _apply_required_resolved_op(request, sinks)
    return ApplyResolvedOpResult(
        state=state,
        disposition=disposition,
        audit=_audit_for_rop(request, disposition),
    )


def _apply_required_resolved_op(
    request: ApplyResolvedOpRequest,
    sinks: ApplyResolvedOpSinks,
) -> tuple[ReplayState, ApplyDisposition]:
    state = request.state
    rop = request.rop
    disposition: ApplyDisposition = "APPLIED"
    try:
        prev_state = state
        event_cursor = (
            len(sinks.mutation_events_out)
            if sinks.mutation_events_out is not None
            else 0
        )
        failed_cursor = (
            len(sinks.failed_ops_out)
            if sinks.failed_ops_out is not None
            else 0
        )
        state = apply_op(
            state,
            None,
            request.ctx,
            None,
            replay_mode=request.replay_mode,
            failed_ops_out=sinks.failed_ops_out,
            source_pathologies_out=sinks.source_pathologies_out,
            mutation_events_out=sinks.mutation_events_out,
            findings_out=sinks.findings_out,
            path_hint=request.path_hint,
            rop=rop,
            replay_history_ops=sinks.lo_ops_out,
            standalone_section_targets=request.standalone_section_targets,
            migration_ledger=request.migration_ledger,
            strict_profile=request.strict_profile,
            write_audits_out=sinks.write_audits_out,
        )
        if (
            sinks.failed_ops_out is not None
            and len(sinks.failed_ops_out) > failed_cursor
        ):
            disposition = "APPLY_FAILED"
        if sinks.mutation_events_out is not None:
            cross_check_observed_vs_declared(
                prev_state,
                state,
                rop.op_id or "",
                sinks.mutation_events_out[event_cursor:],
                sinks.observed_touch_results_out,
            )
    except (NameError, TypeError, AttributeError):
        raise
    except Exception as e:
        disposition = "APPLY_FAILED"
        prefix = request.error_prefix
        if prefix:
            logger.debug(
                "  [%s] %s %s -> ERROR",
                request.amendment_id,
                prefix,
                rop.description(),
                exc_info=True,
            )
            _replay_print(
                f"  [{request.amendment_id}] {prefix} {rop.description()} -> ERROR: {e}"
            )
        else:
            logger.debug(
                "  [%s] %s -> ERROR",
                request.amendment_id,
                rop.description(),
                exc_info=True,
            )
            _replay_print(f"  [{request.amendment_id}] {rop.description()} -> ERROR: {e}")
    return state, disposition


def cross_check_observed_vs_declared(
    prev_state: ReplayState,
    new_state: ReplayState,
    op_id: str,
    events: list[ApplyMutationEvent],
    observed_touch_results_out: Optional[list[MutationAccountingResult]],
) -> None:
    """Passively verify the op's observed tree diff against its declared events."""
    if observed_touch_results_out is None or not OBSERVED_MUTATION_CROSS_CHECK_ENABLED:
        return
    if prev_state.ir is new_state.ir:
        return
    observed_paths = diff_ir_paths_identity_pruned(prev_state.ir, new_state.ir)
    if not observed_paths:
        return
    helper = events[-1].helper if events else ""
    result = observed_vs_declared_cross_check(op_id, helper, observed_paths, events)
    if result is not None:
        observed_touch_results_out.append(result)


def _audit_for_rop(
    request: ApplyResolvedOpRequest,
    disposition: ApplyDisposition,
) -> ApplyResolvedOpAudit:
    group = request.rop.resolved_group_key_view
    return ApplyResolvedOpAudit(
        source_statute=request.amendment_id,
        op_id=request.rop.op_id or "",
        description=request.rop.description(),
        target_unit_kind=str(group.unit_kind or ""),
        target_norm=str(group.target_norm or ""),
        target_chapter=str(group.target_chapter or ""),
        target_part=str(group.target_part or ""),
        disposition=disposition,
    )

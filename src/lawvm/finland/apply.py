"""Finland apply entrypoint and public compatibility shell.

Dispatch selection lives in :mod:`lawvm.finland.apply_intent_facade`; granularity
slices remain in the named ``apply_*`` modules catalogued there.

No grafter.py imports.  Depends only on:
  - Python stdlib
  - lawvm.core.ir
  - lawvm.finland.ops

XMLStatute is referenced only under TYPE_CHECKING to avoid circular imports.
grafter.py re-exports every public symbol from here for backward compatibility.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Literal, Optional

from lawvm.core.compile_result import SourcePathology
from lawvm.core.ir import IRNode
from lawvm.core.ir import LegalOperation as _LegalOperation
from lawvm.core.observed_write_audit import ObservedWriteAudit
from lawvm.core.phase_result import Finding
from lawvm.core.tree_ops import Path
from lawvm.finland.apply_events import ApplyMutationEvent
from lawvm.finland.apply_intent_facade import dispatch_apply_intent
from lawvm.finland.migration_ledger import MigrationLedger
from lawvm.finland.ops import AmendmentOp, FailedOp, ResolvedOp, StrictProfile
from lawvm.finland.standalone_targets import StandaloneSectionTargetsInput

if TYPE_CHECKING:
    from lawvm.finland.payload_normalize import SubsectionSlotAssignmentResult
    from lawvm.finland.statute import ReplayState, StatuteContext

__all__ = [
    "ApplyMutationEvent",
    "apply_op",
]


def apply_op(
    state: ReplayState,
    op: Optional[AmendmentOp],
    ctx: StatuteContext,
    muutos_ir: Optional[IRNode],
    cross_ir: Optional[IRNode] = None,
    amend_sub_ir: Optional[IRNode] = None,
    slot_assignment: "SubsectionSlotAssignmentResult | None" = None,
    replay_mode: Literal["official_consolidation", "legal_pit"] = "official_consolidation",
    failed_ops_out: Optional[List[FailedOp]] = None,
    source_pathologies_out: Optional[List[SourcePathology]] = None,
    mutation_events_out: Optional[List[ApplyMutationEvent]] = None,
    findings_out: Optional[List[Finding]] = None,
    path_hint: Optional[Path] = None,
    rop: Optional[ResolvedOp] = None,
    standalone_section_targets: StandaloneSectionTargetsInput = None,
    migration_ledger: Optional[MigrationLedger] = None,
    replay_history_ops: Optional[List[_LegalOperation]] = None,
    strict_profile: Optional[StrictProfile] = None,
    write_audits_out: Optional[List[ObservedWriteAudit]] = None,
) -> ReplayState:
    """Apply one amendment operation. Pure: state in → state out, no mutation.

    Routes through :func:`lawvm.finland.apply_intent_facade.dispatch_apply_intent`.
    """
    return dispatch_apply_intent(
        state,
        op,
        ctx,
        muutos_ir,
        cross_ir=cross_ir,
        amend_sub_ir=amend_sub_ir,
        slot_assignment=slot_assignment,
        replay_mode=replay_mode,
        failed_ops_out=failed_ops_out,
        source_pathologies_out=source_pathologies_out,
        mutation_events_out=mutation_events_out,
        findings_out=findings_out,
        path_hint=path_hint,
        rop=rop,
        standalone_section_targets=standalone_section_targets,
        migration_ledger=migration_ledger,
        replay_history_ops=replay_history_ops,
        strict_profile=strict_profile,
        write_audits_out=write_audits_out,
    )

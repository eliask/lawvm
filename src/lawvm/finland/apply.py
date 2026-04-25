"""Finland apply entrypoint and public compatibility shell.

The live executor owners now live in dedicated modules:
- ``apply_typed_dispatch.py`` for CanonicalIntent routing
- ``apply_legacy_dispatch.py`` for the legacy field-based fallback
- ``apply_events.py`` for mutation-event types/emission

This file now intentionally keeps only the public ``apply_op`` entrypoint plus
the small compatibility surface that still matters to in-repo callers/tests.

No grafter.py imports.  Depends only on:
  - Python stdlib
  - lawvm.core.ir
  - lawvm.finland.ops

XMLStatute is referenced only under TYPE_CHECKING to avoid circular imports.
grafter.py re-exports every public symbol from here for backward compatibility.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING, FrozenSet, List, Literal, Optional

from lawvm.core.compile_result import SourcePathology
from lawvm.core.ir import IRNode
from lawvm.core.ir import LegalOperation as _LegalOperation
from lawvm.core.tree_ops import Path
from lawvm.finland.ops import (
    AmendmentOp,
    FailedOp,
    ResolvedOp,
    StrictProfile,
    intent_required_for_apply,
    typed_intent_action_mismatch,
    get_replay_profile,
)
from lawvm.finland.apply_typed_dispatch import _apply_canonical_intent
from lawvm.finland.apply_events import (
    ApplyMutationEvent,
    _emit_legacy_dispatch_fallback_event,
)
from lawvm.finland.apply_legacy_dispatch import _apply_legacy_dispatch
from lawvm.finland.apply_runtime_support import _legacy_dispatch_shell_for_rop
from lawvm.finland.migration_ledger import MigrationLedger

if TYPE_CHECKING:
    from lawvm.finland.statute import StatuteContext, ReplayState
    from lawvm.finland.payload_normalize import (
        SubsectionSlotAssignmentResult,
    )

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public API
#
# apply_op   — pure (ReplayState, AmendmentOp, StatuteContext, ...) → ReplayState
#
# All other symbols are implementation-internal (_-prefixed) but are
# imported by grafter.py for backward-compat re-export.  They are NOT
# part of the stable public API and may be renamed without notice.
# ---------------------------------------------------------------------------
__all__ = [
    "ApplyMutationEvent",
    "apply_op",
]


@dataclass(frozen=True)
class _PreparedLegacyApplyInputs:
    shell_op: AmendmentOp
    muutos_ir: Optional[IRNode]
    cross_ir: Optional[IRNode]
    amend_sub_ir: Optional[IRNode]
    slot_assignment: "SubsectionSlotAssignmentResult | None"


def _prepare_legacy_apply_inputs(
    *,
    op: Optional[AmendmentOp],
    muutos_ir: Optional[IRNode],
    cross_ir: Optional[IRNode],
    amend_sub_ir: Optional[IRNode],
    slot_assignment: "SubsectionSlotAssignmentResult | None",
    rop: Optional[ResolvedOp],
) -> _PreparedLegacyApplyInputs:
    shell_op = op
    prepared_muutos_ir = muutos_ir
    prepared_cross_ir = cross_ir
    prepared_amend_sub_ir = amend_sub_ir
    prepared_slot_assignment = slot_assignment
    if rop is not None:
        shell_op = _legacy_dispatch_shell_for_rop(rop)
        if prepared_muutos_ir is None:
            prepared_muutos_ir = rop.muutos_ir
        if prepared_cross_ir is None:
            prepared_cross_ir = rop.cross_ir
        if prepared_amend_sub_ir is None:
            prepared_amend_sub_ir = rop.resolved_amend_sub_ir()
        if prepared_slot_assignment is None:
            prepared_slot_assignment = rop.slot_assignment
    assert shell_op is not None
    return _PreparedLegacyApplyInputs(
        shell_op=shell_op,
        muutos_ir=prepared_muutos_ir,
        cross_ir=prepared_cross_ir,
        amend_sub_ir=prepared_amend_sub_ir,
        slot_assignment=prepared_slot_assignment,
    )


# ---------------------------------------------------------------------------
# Public API: pure (ReplayState, StatuteContext) → ReplayState
# ---------------------------------------------------------------------------


def apply_op(
    state: ReplayState,
    op: Optional[AmendmentOp],
    ctx: StatuteContext,
    muutos_ir: Optional[IRNode],
    cross_ir: Optional[IRNode] = None,
    amend_sub_ir: Optional[IRNode] = None,
    slot_assignment: "SubsectionSlotAssignmentResult | None" = None,
    replay_mode: Literal["finlex_oracle", "legal_pit"] = "finlex_oracle",
    failed_ops_out: Optional[List[FailedOp]] = None,
    source_pathologies_out: Optional[List[SourcePathology]] = None,
    mutation_events_out: Optional[List[ApplyMutationEvent]] = None,
    path_hint: Optional[Path] = None,
    rop: Optional[ResolvedOp] = None,
    standalone_section_targets: Optional[FrozenSet] = None,
    migration_ledger: Optional[MigrationLedger] = None,
    replay_history_ops: Optional[List[_LegalOperation]] = None,
    strict_profile: Optional[StrictProfile] = None,
) -> ReplayState:
    """Apply one amendment operation. Pure: state in → state out, no mutation.

    Returns the updated ReplayState.  The input state is never modified.

    If ``rop`` is provided, the typed and legacy dispatch owners project any
    missing compatibility inputs from that late-waist carrier themselves.
    If ``rop.intent`` is not None, dispatches through
    the typed canonical-intent path (``_apply_canonical_intent``).  Otherwise
    falls back to the legacy field-based dispatch (``_apply_legacy_dispatch``).

    ``ctx`` provides read-only access to the original base statute (id, title,
    base_ir) — currently unused here but threaded for future use and API
    consistency.
    """
    if op is None and rop is None:
        raise RuntimeError("FI_APPLY_INPUT_REQUIRED: apply_op needs AmendmentOp or ResolvedOp")
    rop_description = rop.description() if rop is not None else (op.description() if op is not None else "")

    # --- Typed intent dispatch (Step 2 of canonical intent migration) ---
    if rop is not None and rop.intent is not None:
        mismatch = typed_intent_action_mismatch(rop)
        if mismatch is not None:
            raise RuntimeError(
                "FI_TYPED_INTENT_ACTION_MISMATCH: apply received contradictory "
                f"typed intent for {rop_description} (op_id={rop.op_id or '<missing-op-id>'}): {mismatch}"
            )
        profile = get_replay_profile(replay_mode, strict_profile=strict_profile)
        ctx_label = f"[{rop.resolved_source_statute}] {rop_description}"
        logger.debug("  %s → typed intent dispatch (%s)", ctx_label, type(rop.intent).__name__)
        return _apply_canonical_intent(
            state,
            rop,
            rop_description,
            rop.intent,
            ctx,
            profile,
            ctx_label,
            cross_ir=cross_ir,
            failed_ops_out=failed_ops_out,
            source_pathologies_out=source_pathologies_out,
            mutation_events_out=mutation_events_out,
            path_hint=path_hint,
            replay_history_ops=replay_history_ops,
            standalone_section_targets=standalone_section_targets,
            migration_ledger=migration_ledger,
            strict_profile=strict_profile,
        )

    if rop is not None and intent_required_for_apply(rop):
        raise RuntimeError(
            "FI_TYPED_INTENT_REQUIRED: apply received ResolvedOp without "
            f"CanonicalIntent for {rop.resolved_action_type} {rop_description} "
            f"(op_id={rop.op_id or '<missing-op-id>'})"
        )
    # Only non-required op types (e.g. MOVE) reach the legacy path; REPLACE/INSERT/
    # REPEAL/RENUMBER without typed intent are blocked by the check above.

    # --- Legacy field-based dispatch ---
    # DEBUG: no accepted FI no-destination RENUMBER waiver remains. If a
    # relabel reaches this legacy path, the producer/lowering side failed to
    # construct CanonicalIntent and should be fixed upstream instead of adding
    # new apply-side accommodation here.
    if rop is not None:
        logger.debug(
            "LEGACY_DISPATCH_FALLBACK: %s %s — intent is None, using legacy field dispatch",
            rop.resolved_action_type,
            rop.target_norm,
        )
        _emit_legacy_dispatch_fallback_event(
            mutation_events_out,
            rop=rop,
            helper="apply_op",
            reason_tag="missing_canonical_intent",
            failure_reason="ResolvedOp reached apply without CanonicalIntent",
            reason_code="missing_canonical_intent",
            path_hint=path_hint,
        )
    legacy_inputs = _prepare_legacy_apply_inputs(
        op=op,
        muutos_ir=muutos_ir,
        cross_ir=cross_ir,
        amend_sub_ir=amend_sub_ir,
        slot_assignment=slot_assignment,
        rop=rop,
    )
    return _apply_legacy_dispatch(
        state,
        legacy_inputs.shell_op,
        rop_description,
        ctx,
        legacy_inputs.muutos_ir,
        cross_ir=legacy_inputs.cross_ir,
        amend_sub_ir=legacy_inputs.amend_sub_ir,
        slot_assignment=legacy_inputs.slot_assignment,
        replay_mode=replay_mode,
        failed_ops_out=failed_ops_out,
        source_pathologies_out=source_pathologies_out,
        mutation_events_out=mutation_events_out,
        path_hint=path_hint,
        replay_history_ops=replay_history_ops,
        standalone_section_targets=standalone_section_targets,
        rop=rop,
        migration_ledger=migration_ledger,
        inputs_prepared=True,
        strict_profile=strict_profile,
    )

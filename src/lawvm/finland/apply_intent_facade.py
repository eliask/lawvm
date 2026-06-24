"""Finland apply intent facade: typed canonical dispatch + legacy strict-only branch.

Bench census (690 statutes): zero ``APPLY.LEGACY_DISPATCH_FALLBACK`` on the hot
path. REPLACE/INSERT/REPEAL/RENUMBER require ``CanonicalIntent``; legacy field
dispatch remains a named strict-only branch for MOVE-ish residuals and emits
``APPLY.LEGACY_DISPATCH_FALLBACK`` mutation events when entered.

Granularity slices (item / subsection / section / container / group-replay) live
in dedicated ``apply_*`` modules catalogued by :data:`APPLY_INTENT_LANES`; this
facade owns lane *selection* only, not the slice implementations.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Literal, Optional

from lawvm.core.compile_result import SourcePathology
from lawvm.core.ir import IRNode
from lawvm.core.ir import LegalOperation as _LegalOperation
from lawvm.core.observed_write_audit import ObservedWriteAudit
from lawvm.core.phase_result import Finding
from lawvm.core.tree_ops import Path
from lawvm.finland.apply_events import (
    ApplyMutationEvent,
    _emit_legacy_dispatch_fallback_event,
)
from lawvm.finland.apply_legacy_dispatch import _apply_legacy_dispatch
from lawvm.finland.apply_runtime_support import _legacy_dispatch_shell_for_rop
from lawvm.finland.apply_typed_dispatch import _apply_canonical_intent
from lawvm.finland.migration_ledger import MigrationLedger
from lawvm.finland.ops import (
    AmendmentOp,
    FailedOp,
    ResolvedOp,
    StrictProfile,
    get_replay_profile,
    intent_required_for_apply,
    typed_intent_action_mismatch,
)
from lawvm.finland.standalone_targets import StandaloneSectionTargetsInput

if TYPE_CHECKING:
    from lawvm.finland.payload_normalize import SubsectionSlotAssignmentResult
    from lawvm.finland.statute import ReplayState, StatuteContext

logger = logging.getLogger(__name__)

ApplyDispatchLane = Literal["typed_canonical", "legacy_strict_only"]
ApplyGranularity = Literal[
    "dispatch",
    "legacy",
    "events",
    "policy",
    "structure",
    "subsection_dispatch",
    "subsection",
    "item",
    "ir",
    "payload",
    "group_replay",
    "supplemental_recovery",
    "runtime",
    "resolved_op",
    "executor",
    "boundary",
    "loop_state",
]

LEGACY_DISPATCH_FALLBACK_KIND = "APPLY.LEGACY_DISPATCH_FALLBACK"

TYPED_INTENT_HELPERS: tuple[str, ...] = (
    "_apply_canonical_intent",
    "_apply_intent_replace",
    "_apply_intent_insert",
    "_apply_intent_repeal",
    "_apply_intent_relabel",
    "_apply_intent_move",
    "_apply_intent_section_level",
    "_apply_intent_container",
)


@dataclass(frozen=True, slots=True)
class ApplyIntentLane:
    """One periodic-table row for a Finland apply granularity slice."""

    lane_id: str
    module: str
    granularity: ApplyGranularity
    symbol: str
    notes: str


APPLY_INTENT_LANES: tuple[ApplyIntentLane, ...] = (
    ApplyIntentLane(
        lane_id="typed_dispatch",
        module="lawvm.finland.apply_typed_dispatch",
        granularity="dispatch",
        symbol="_apply_canonical_intent",
        notes="CanonicalIntent router; action-family dispatch to intent helpers.",
    ),
    ApplyIntentLane(
        lane_id="legacy_dispatch",
        module="lawvm.finland.apply_legacy_dispatch",
        granularity="legacy",
        symbol="_apply_legacy_dispatch",
        notes="Strict-only legacy field dispatch; emits LEGACY_DISPATCH_FALLBACK witness.",
    ),
    ApplyIntentLane(
        lane_id="mutation_events",
        module="lawvm.finland.apply_events",
        granularity="events",
        symbol="ApplyMutationEvent",
        notes="Apply mutation-event carriers and receipt emission.",
    ),
    ApplyIntentLane(
        lane_id="apply_policy",
        module="lawvm.finland.apply_policy",
        granularity="policy",
        symbol="section_resolver_binding",
        notes="Section ladder resolution and occupancy policy observations.",
    ),
    ApplyIntentLane(
        lane_id="structure_ops",
        module="lawvm.finland.apply_structure_ops",
        granularity="structure",
        symbol="_apply_whole_section_op",
        notes="Section and chapter/part container apply slices.",
    ),
    ApplyIntentLane(
        lane_id="subsection_dispatch",
        module="lawvm.finland.apply_subsection_dispatch",
        granularity="subsection_dispatch",
        symbol="_apply_deterministic_subsection_op",
        notes="Subsection dispatch normalization and landed-path recovery.",
    ),
    ApplyIntentLane(
        lane_id="subsection_ops",
        module="lawvm.finland.apply_subsection_ops",
        granularity="subsection",
        symbol="_apply_subsection_replace",
        notes="Subsection-level IR mutations.",
    ),
    ApplyIntentLane(
        lane_id="item_ops",
        module="lawvm.finland.apply_item_ops",
        granularity="item",
        symbol="_apply_item_op",
        notes="Item/kohta-level IR mutations.",
    ),
    ApplyIntentLane(
        lane_id="ir_ops",
        module="lawvm.finland.apply_ir_ops",
        granularity="ir",
        symbol="_relabel_section_ir",
        notes="IR rebuild and relabel helpers shared across apply slices.",
    ),
    ApplyIntentLane(
        lane_id="payload_ops",
        module="lawvm.finland.apply_payload_ops",
        granularity="payload",
        symbol="_collapse_intro_list_amend_subsection_ir",
        notes="Payload normalization hooks on apply path.",
    ),
    ApplyIntentLane(
        lane_id="group_replay",
        module="lawvm.finland.apply_group_replay",
        granularity="group_replay",
        symbol="_emit_section_snapshot",
        notes="Group replay snapshot emission after apply fold.",
    ),
    ApplyIntentLane(
        lane_id="supplemental_recovery",
        module="lawvm.finland.apply_supplemental_recovery",
        granularity="supplemental_recovery",
        symbol="run_apply_supplemental_recovery",
        notes="Named post-apply recovery lanes (uncovered body, part-move timeline).",
    ),
    ApplyIntentLane(
        lane_id="runtime_support",
        module="lawvm.finland.apply_runtime_support",
        granularity="runtime",
        symbol="_legacy_dispatch_shell_for_rop",
        notes="Legacy shell projection and insert-parent path helpers.",
    ),
    ApplyIntentLane(
        lane_id="resolved_op",
        module="lawvm.finland.apply_resolved_op",
        granularity="resolved_op",
        symbol="apply_resolved_op_with_audit",
        notes="Single ResolvedOp wrapper over dispatch_apply_intent.",
    ),
    ApplyIntentLane(
        lane_id="ops_executor",
        module="lawvm.finland.apply_ops_executor",
        granularity="executor",
        symbol="_apply_ops_to_tree_typed",
        notes="Batch fold of resolved ops through apply_op.",
    ),
    ApplyIntentLane(
        lane_id="ops_boundary",
        module="lawvm.finland.apply_ops_boundary",
        granularity="boundary",
        symbol="ApplyOpsRequest",
        notes="Typed request/sink boundary for batch apply.",
    ),
    ApplyIntentLane(
        lane_id="loop_state",
        module="lawvm.finland.apply_loop_state",
        granularity="loop_state",
        symbol="ApplyGroupState",
        notes="Per-amendment apply loop accumulator (ops trail, not tree owner).",
    ),
)


@dataclass(frozen=True, slots=True)
class PreparedLegacyApplyInputs:
    """Legacy dispatch inputs projected from AmendmentOp and/or ResolvedOp."""

    shell_op: AmendmentOp
    muutos_ir: Optional[IRNode]
    cross_ir: Optional[IRNode]
    amend_sub_ir: Optional[IRNode]
    slot_assignment: "SubsectionSlotAssignmentResult | None"


def classify_apply_dispatch_lane(rop: ResolvedOp | None) -> ApplyDispatchLane | None:
    """Return the dispatch lane for *rop*, or None when inputs are insufficient."""
    if rop is None:
        return "legacy_strict_only"
    if rop.intent is not None:
        return "typed_canonical"
    if intent_required_for_apply(rop):
        return None
    return "legacy_strict_only"


def apply_intent_lane_summary() -> dict[str, object]:
    """Machine-readable catalog of Finland apply granularity lanes."""
    by_granularity: dict[str, list[dict[str, str]]] = {}
    for lane in APPLY_INTENT_LANES:
        by_granularity.setdefault(lane.granularity, []).append(
            {
                "lane_id": lane.lane_id,
                "module": lane.module,
                "symbol": lane.symbol,
            }
        )
    return {
        "catalog_kind": "finland_apply_intent_lanes",
        "lane_count": len(APPLY_INTENT_LANES),
        "typed_intent_helpers": list(TYPED_INTENT_HELPERS),
        "legacy_dispatch_fallback_kind": LEGACY_DISPATCH_FALLBACK_KIND,
        "lanes_by_granularity": by_granularity,
    }


def prepare_legacy_apply_inputs(
    *,
    op: Optional[AmendmentOp],
    muutos_ir: Optional[IRNode],
    cross_ir: Optional[IRNode],
    amend_sub_ir: Optional[IRNode],
    slot_assignment: "SubsectionSlotAssignmentResult | None",
    rop: Optional[ResolvedOp],
) -> PreparedLegacyApplyInputs:
    """Project legacy dispatch inputs from AmendmentOp and/or ResolvedOp."""
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
    return PreparedLegacyApplyInputs(
        shell_op=shell_op,
        muutos_ir=prepared_muutos_ir,
        cross_ir=prepared_cross_ir,
        amend_sub_ir=prepared_amend_sub_ir,
        slot_assignment=prepared_slot_assignment,
    )


def dispatch_apply_intent(
    state: "ReplayState",
    op: Optional[AmendmentOp],
    ctx: "StatuteContext",
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
) -> "ReplayState":
    """Single apply entry: typed CanonicalIntent router or legacy strict-only branch."""
    if op is None and rop is None:
        raise RuntimeError("FI_APPLY_INPUT_REQUIRED: apply_op needs AmendmentOp or ResolvedOp")
    rop_description = rop.description() if rop is not None else (op.description() if op is not None else "")

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
            findings_out=findings_out,
            path_hint=path_hint,
            replay_history_ops=replay_history_ops,
            standalone_section_targets=standalone_section_targets,
            migration_ledger=migration_ledger,
            strict_profile=strict_profile,
            write_audits_out=write_audits_out,
        )

    if rop is not None and intent_required_for_apply(rop):
        raise RuntimeError(
            "FI_TYPED_INTENT_REQUIRED: apply received ResolvedOp without "
            f"CanonicalIntent for {rop.resolved_action_type} {rop_description} "
            f"(op_id={rop.op_id or '<missing-op-id>'})"
        )

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
    legacy_inputs = prepare_legacy_apply_inputs(
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
        findings_out=findings_out,
        path_hint=path_hint,
        replay_history_ops=replay_history_ops,
        standalone_section_targets=standalone_section_targets,
        rop=rop,
        migration_ledger=migration_ledger,
        inputs_prepared=True,
        strict_profile=strict_profile,
    )


__all__ = [
    "APPLY_INTENT_LANES",
    "ApplyDispatchLane",
    "ApplyGranularity",
    "ApplyIntentLane",
    "LEGACY_DISPATCH_FALLBACK_KIND",
    "PreparedLegacyApplyInputs",
    "TYPED_INTENT_HELPERS",
    "apply_intent_lane_summary",
    "classify_apply_dispatch_lane",
    "dispatch_apply_intent",
    "prepare_legacy_apply_inputs",
]

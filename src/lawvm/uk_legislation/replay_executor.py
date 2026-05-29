"""UK replay executor and public replay API."""

from __future__ import annotations

import time
from typing import Any, List, Optional

from lawvm.core.ir import IRStatute, LegalOperation
from lawvm.core.mutation_events import MutationEvent
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.uk_legislation.addressing import _action_name
from lawvm.uk_legislation.mutable_ir import UKMutableNode, UKMutableStatute
from lawvm.uk_legislation.replay_grounding import UKReplayGroundingMixin
from lawvm.uk_legislation.replay_heading_apply import UKReplayHeadingApplyMixin
from lawvm.uk_legislation.replay_insert_apply import UKReplayInsertApplyMixin
from lawvm.uk_legislation.replay_invariant_diagnostics import UKReplayInvariantDiagnosticsMixin
from lawvm.uk_legislation.replay_prepare import prepare_replay_uk_ops
from lawvm.uk_legislation.replay_records import (
    UKReplayPrepareResult,
    append_replay_fold_text_duplication_adjudications,
    _append_uk_replay_adjudication,
    uk_replay_blocking_action_target_detail,
)
from lawvm.uk_legislation.replay_renumber_apply import UKReplayRenumberApplyMixin
from lawvm.uk_legislation.replay_repeal_apply import UKReplayRepealApplyMixin
from lawvm.uk_legislation.replay_replace_apply import UKReplayReplaceApplyMixin
from lawvm.uk_legislation.replay_schedule_list_apply import UKReplayScheduleListApplyMixin
from lawvm.uk_legislation.replay_state import UKReplayStateMixin
from lawvm.uk_legislation.replay_table_apply import UKReplayTableApplyMixin
from lawvm.uk_legislation.replay_target_diagnostics import UKReplayTargetDiagnosticsMixin
from lawvm.uk_legislation.replay_target_lookup import UKReplayTargetLookupMixin
from lawvm.uk_legislation.replay_text_action_apply import UKReplayTextActionApplyMixin
from lawvm.uk_legislation.replay_text_apply import UKReplayTextApplyMixin


class UKReplayExecutor(
    UKReplayTableApplyMixin,
    UKReplayTextActionApplyMixin,
    UKReplayTextApplyMixin,
    UKReplayInvariantDiagnosticsMixin,
    UKReplayScheduleListApplyMixin,
    UKReplayGroundingMixin,
    UKReplayTargetDiagnosticsMixin,
    UKReplayTargetLookupMixin,
    UKReplayInsertApplyMixin,
    UKReplayStateMixin,
    UKReplayRenumberApplyMixin,
    UKReplayHeadingApplyMixin,
    UKReplayRepealApplyMixin,
    UKReplayReplaceApplyMixin,
):
    def __init__(
        self,
        statute: IRStatute,
        eid_map: Optional[dict[str, str]] = None,
        text_map: Optional[dict[str, str]] = None,
        verbose: bool = False,
        lo_ops_out: Optional[List[LegalOperation]] = None,
        adjudications_out: Optional[List[CompileAdjudication]] = None,
        mutation_events_out: Optional[list[MutationEvent]] = None,
    ):
        self.statute = UKMutableStatute.from_irstatute(statute)
        self.eid_map = eid_map or {}
        self.text_map = text_map or {}
        self.verbose = bool(verbose)
        self.lo_ops_out = lo_ops_out  # None = don't collect snapshots
        self.mutation_events_out = mutation_events_out
        self._current_mutation_op: Optional[LegalOperation] = None
        self.adjudications_out = adjudications_out if adjudications_out is not None else []
        self._seen_invariant_violations = self._collect_invariant_violations()
        self._repealed_target_prefixes: set[str] = set()
        self._applied_text_patch_targets: dict[str, list[str]] = {}
        self.oracle_alignment_events: list[dict[str, Any]] = []
        self._structure_mutation_serial = 0
        self._last_invariant_structure_serial = 0
        self._eid_lookup_index = None
        self._eid_lookup_ambiguous: set[str] = set()
        self._eid_suffix_lookup_index = None
        self._eid_suffix_lookup_ambiguous: set[tuple[str, str]] = set()
        self._eid_search_cache = {}
        self._target_lookup_cache = {}
        self._recursive_match_cache = {}
        self._recursive_match_all_cache = {}

    def _log(self, message: str) -> None:
        if self.verbose:
            print(message)

    def _record_whole_act_repeal_mutation_event(self, op: LegalOperation) -> None:
        if self.mutation_events_out is None:
            return
        source = op.source
        self.mutation_events_out.append(
            MutationEvent(
                op_id=op.op_id,
                source_statute=source.statute_id if source is not None else "",
                action=_action_name(op.action),
                helper="_apply_op_with_context",
                outcome="whole_act_repealed",
                resolved_target_path=(),
                parent_path=(),
                removed_paths=((),),
            )
        )

    def apply_op(self, op: LegalOperation):
        previous_mutation_op = self._current_mutation_op
        self._current_mutation_op = op
        try:
            self._apply_op_with_context(op)
        finally:
            self._current_mutation_op = previous_mutation_op

    def _apply_op_with_context(self, op: LegalOperation) -> None:
        target = op.target
        # Keep legacy warnings visible during replay runs while also recording
        # structured adjudications for downstream analyses.

        if str(target.special or "") == "whole_act":
            if _action_name(op.action) == "repeal":
                self._log("  EXECUTOR: repealing WHOLE ACT")
                self.statute.body.children = []
                self.statute.supplements = []
                self._clear_eid_lookup_index()
                self._note_structure_mutation()
                self._record_whole_act_repeal_mutation_event(op)
                self._record_invariant_violations(op)
            else:
                self._log(
                    f"  EXECUTOR: WARN whole_act target with unhandled action {op.action!r} — skipping {op.op_id}"
                )
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_unsupported_action",
                    message="UK replay skipped unsupported whole-act action.",
                    op=op,
                    detail=uk_replay_blocking_action_target_detail(op, target),
                )
            return

        target_eid = self._derive_target_eid(target)
        node: Optional[UKMutableNode]
        parent: Optional[UKMutableNode]
        idx: Optional[int]
        node, parent, idx = None, None, None
        if target_eid:
            node, parent, idx = self._find_node_and_parent_statute(
                target_eid,
                allow_sequence_match=False,
            )
            if node is not None and not self._eid_candidate_matches_target_leaf(node, target):
                node, parent, idx = None, None, None

        if not node:
            allow_compound_subsection_alias = _action_name(op.action) in ("text_replace", "text_repeal")
            node, parent, idx = self._find_node_by_target(
                target,
                allow_compound_subsection_alias=allow_compound_subsection_alias,
                allow_recursive_match=_action_name(op.action) != "insert",
                target_resolution_op=op,
            )
        insert_existing_target_resolution = ""
        if not node:
            node, parent, idx, insert_existing_target_resolution = (
                self._find_existing_insert_target_by_explicit_parent_leaf(target, op)
            )
        if not node and _action_name(op.action) in {"replace", "repeal"}:
            node, parent, idx = self._find_unique_schedule_item_for_source_parent_substitution_range_target(
                target,
                op,
            )
        target_found = node is not None
        if not target_found and self._empty_schedule_root_shape_gap(target):
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind="uk_replay_empty_schedule_shape_gap",
                message="UK replay skipped text-based op: empty schedule root has no descendant target shape.",
                op=op,
                detail=uk_replay_blocking_action_target_detail(
                    op,
                    target,
                    source_shape="empty_schedule_root",
                ),
            )
            return

        if _action_name(op.action) == "repeal":
            self._apply_repeal_op(op, target, node, parent, idx)
            return
        elif _action_name(op.action) == "replace":
            self._apply_replace_op(op, target, node, parent, idx, target_found)
            return
        elif _action_name(op.action) in ("text_replace", "text_repeal"):
            self._apply_text_action_op(op, target, node, parent)
            return
        elif _action_name(op.action) == "insert":
            self._apply_insert_op(op, target, node, insert_existing_target_resolution)
            return
        elif _action_name(op.action) == "renumber":
            self._apply_renumber_op(op, target)
            return
        elif _action_name(op.action) == "unknown":
            self._log(f"  EXECUTOR: unknown action — skipping {op.op_id}")
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind="uk_replay_unsupported_action",
                message="UK replay skipped unsupported action.",
                op=op,
                detail=uk_replay_blocking_action_target_detail(op, target),
            )
        else:
            raise ValueError(
                f"UKReplayExecutor.apply_op: unhandled action {op.action!r} "
                f"on op {op.op_id}. This is a programming error — every action "
                f"type must be explicitly handled (even if only to skip+warn)."
            )


# ---------------------------------------------------------------------------
# Commencement-aware EID filtering
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Public replay API
# ---------------------------------------------------------------------------


def _prepare_replay_uk_ops(
    ops: list[LegalOperation],
    *,
    base_ir: Optional[IRStatute] = None,
    verbose: bool = False,
    adjudications_out: Optional[list[CompileAdjudication]] = None,
) -> UKReplayPrepareResult:
    """Normalize replay ops so every entry point applies the same semantics."""
    base_executor: Optional[UKReplayExecutor] = UKReplayExecutor(base_ir) if base_ir is not None else None
    return prepare_replay_uk_ops(
        ops,
        base_executor=base_executor,
        verbose=verbose,
        adjudications_out=adjudications_out,
    )


def replay_uk_ops(
    base: IRStatute,
    ops: list[LegalOperation],
    *,
    eid_map: Optional[dict[str, str]] = None,
    text_map: Optional[dict[str, str]] = None,
    allow_oracle_alignment: bool = True,
    verbose: bool = False,
    lo_ops_out: Optional[List[LegalOperation]] = None,
    adjudications_out: Optional[List[CompileAdjudication]] = None,
    mutation_events_out: Optional[list[MutationEvent]] = None,
    replay_phase_timings_out: Optional[dict[str, float]] = None,
) -> IRStatute:
    """Apply compiled UK legal operations to enacted base, return amended statute.

    This is the primary public entry point for the UK replay engine.  It wraps
    UKReplayExecutor with a clean function signature so callers do not need to
    instantiate the executor directly.

    Args:
        base:       Enacted (base) IRStatute produced by parse_uk_statute_ir().
        ops:        Compiled LegalOperation list from compile_effect_to_ir_ops()
                    or UKReplayPipeline.compile_ops_for_statute().
        eid_map:    Optional oracle EID map for grounding (key → oracle EID).
        text_map:   Optional oracle text map for fuzzy-text grounding.
        allow_oracle_alignment:
                    When True, replay-time oracle adapter behavior is enabled:
                    oracle-zombie collapse preparation plus post-apply EID grounding.
                    When False, replay runs without ORACLE_ALIGNMENT_ONLY mutation help.
        verbose:    If True, executor prints each applied op to stdout.
        lo_ops_out: Optional list to collect top-section snapshots after each
                    structural op.  Pass an empty list; it will be populated with
                    legal operations suitable for replay timelines.
        adjudications_out: Optional list to collect replay skip/no-op adjudications.
                    Entries are `CompileAdjudication` with one of the `uk_replay_*`
                    kinds defined by this executor.
        mutation_events_out:
                    Optional list to collect core mutation events at UK replay
                    mutation sites. This is a debug/evidence stream, not a replay
                    control path.
        replay_phase_timings_out:
                    Optional accumulator for replay preparation, per-action
                    apply, and replay finalization timing diagnostics.

    Returns:
        A new IRStatute with all ops applied (deep copy — base is not mutated).

    Op ordering:
        Ops are applied in the order supplied.  Callers should pre-sort by
        (effective_date, sequence) before passing.  UKReplayPipeline already
        does this in compile_ops_for_statute().
    """
    if verbose:
        print(f"  replay_uk_ops: applying {len(ops)} ops to {base.statute_id}")
    replay_phase_t0 = time.perf_counter()

    def _mark_replay_phase(name: str) -> None:
        nonlocal replay_phase_t0
        if replay_phase_timings_out is None:
            return
        now = time.perf_counter()
        replay_phase_timings_out[name] = replay_phase_timings_out.get(name, 0.0) + (
            now - replay_phase_t0
        )
        replay_phase_t0 = now

    prepared_ops = _prepare_replay_uk_ops(
        ops,
        base_ir=base,
        verbose=verbose,
        adjudications_out=adjudications_out,
    )
    _mark_replay_phase("replay_prepare")

    executor = UKReplayExecutor(
        base,
        eid_map=(eid_map or {}) if allow_oracle_alignment else {},
        text_map=(text_map or {}) if allow_oracle_alignment else {},
        verbose=verbose,
        lo_ops_out=lo_ops_out,
        adjudications_out=adjudications_out,
        mutation_events_out=mutation_events_out,
    )
    _mark_replay_phase("replay_executor_init")
    if replay_phase_timings_out is None:
        for op in prepared_ops.accepted_ops:
            executor.apply_op(op)
    else:
        for op in prepared_ops.accepted_ops:
            op_t0 = time.perf_counter()
            executor.apply_op(op)
            action_name = _action_name(op.action)
            key = f"replay_apply_{action_name}"
            replay_phase_timings_out[key] = replay_phase_timings_out.get(key, 0.0) + (
                time.perf_counter() - op_t0
            )
        replay_phase_t0 = time.perf_counter()

    if adjudications_out is not None:
        append_replay_fold_text_duplication_adjudications(
            adjudications_out,
            frozen_statute=executor.statute.to_irstatute(),
            source_statute=base.statute_id,
        )
        _mark_replay_phase("replay_fold_text_duplication")

    replayed = executor.statute.to_irstatute()
    _mark_replay_phase("replay_to_ir")
    return replayed

"""UK replay executor and public replay API."""

from __future__ import annotations
from typing_extensions import override

import time
from dataclasses import replace as dc_replace
from typing import Any, List, Optional

from lawvm.core.apply_seam import (
    AppliedOp,
    ApplyProfile,
    MaterializeResult,
)
from lawvm.core.apply_seam import (
    apply_op as core_apply_op,
)
from lawvm.core.ir import IRNode, IRStatute, LegalOperation
from lawvm.core.mutation_boundary import TreePath
from lawvm.core.mutation_events import MutationEvent
from lawvm.core.phase_result import Finding
from lawvm.core.write_receipt import WriteReceipt
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.uk_legislation.addressing import _action_name
from lawvm.uk_legislation.execution_authorization import _uk_execution_authorization
from lawvm.uk_legislation.uk_write_receipts import (
    UK_SECTION_RENUMBER_RELABEL_RULE_ID,
    emit_uk_op_receipt,
)
from lawvm.uk_legislation.mutation_boundary_per_op_probe import (
    drain_seam_boundary_observations as _uk_drain_seam_boundary_observations,
)
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
    # Sub-PR C+D (mutable_ir Wave N3d, now complete): explicit class-level
    # annotation so ty narrows ``UKReplayExecutor.statute`` to ``IRStatute``.
    # The historical ``UKMutableStatute`` annotation on sibling apply mixins
    # was widened to ``IRNode``/``IRStatute`` when mutable_ir Wave N3d Sub-PR B
    # completed and the ``mutable_ir.py`` shadow module was deleted.
    statute: IRStatute
    def __init__(
        self,
        statute: IRStatute,
        eid_map: Optional[dict[str, str]] = None,
        text_map: Optional[dict[str, str]] = None,
        verbose: bool = False,
        lo_ops_out: Optional[List[LegalOperation]] = None,
        adjudications_out: Optional[List[CompileAdjudication]] = None,
        mutation_events_out: Optional[list[MutationEvent]] = None,
        write_receipts_out: Optional[list[WriteReceipt]] = None,
    ):
        # Sub-PR C (mutable_ir Wave N3d): store the input ``IRStatute`` directly.
        # All downstream mutation now goes through
        # ``self.statute = dataclasses.replace(self.statute, body=..., supplements=...)``
        # because ``IRStatute`` is a frozen dataclass — no in-place writes
        # survive past the boundary. Sub-PR B-completion widened the previously
        # ``UKMutableNode``-typed fields in apply modules to ``IRNode``; the
        # ``mutable_ir.py`` shadow module (including ``UKMutableNode``,
        # ``UKMutableStatute``) is now deleted.
        self.statute: IRStatute = statute
        self.eid_map = eid_map or {}
        self.text_map = text_map or {}
        self.verbose = bool(verbose)
        self.lo_ops_out = lo_ops_out  # None = don't collect snapshots
        self.mutation_events_out = mutation_events_out
        # §2.3 per-op WriteReceipt collection sink. None (the default) = don't
        # collect — ``apply_op`` is then byte-identical to its
        # pre-instrumentation behaviour (no body snapshot, no diff). A list opts
        # the caller into additive per-op receipts (the §2.7 grounding-neutral
        # debug stream shape, mirroring ``mutation_events_out`` / ``lo_ops_out``).
        self.write_receipts_out = write_receipts_out
        self._current_mutation_op: Optional[LegalOperation] = None
        # §2.9 per-op carrier: when a recovery lane INTENTIONALLY retargets the
        # write to a node outside the op's nominal storage boundary (e.g. a
        # missing-leaf REPLACE recovered as an INSERT under the resolved parent /
        # body root), the recovered write-parent tree path is appended here so the
        # per-op mutation-boundary audit (the seam observer AND the in-fold probe)
        # can declare it as an authorized ``declared_recovery`` boundary
        # extension rather than reading the retargeted write as an undeclared
        # escape. Reset per op in ``_uk_materialize_one`` / ``apply_op``; stays
        # empty (and is ignored) when no recovery retarget fired. This records the
        # SPECIFIC authorized recovery parent — never a blanket boundary widening.
        self._uk_declared_recovery_paths: list[TreePath] = []
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
        self._node_tree_path_index = None

    @override
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
        # Reset the per-op declared-recovery carrier so a recovery retarget from a
        # prior op never leaks into this op's boundary. The recovery apply branches
        # (e.g. the missing-leaf REPLACE→INSERT lane) append the resolved write
        # parent path here during the dispatch below.
        self._uk_declared_recovery_paths = []
        # §2.9 per-op mutation-boundary observation: the in-fold env-probe is
        # RETIRED. The seam's always-on observer (``core/apply_seam.apply_op``,
        # reached via ``seam_apply_op``→``_uk_materialize_one``) runs the IDENTICAL
        # core ``audit_op_mutation_boundary`` — reading the SAME
        # ``_uk_declared_recovery_paths`` via the ``MaterializeResult`` — and routes
        # the witness to ``AppliedOp.observations``; ``replay_uk_ops`` drains that
        # observation into the env-gated adjudication. No per-op snapshot is taken
        # here for the boundary; the §2.3 receipt sink keeps its own before-snapshot.
        # §2.3 receipt collection captures the immutable body reference BEFORE the
        # apply mutates it. ``IRStatute`` is frozen so this is a cheap reference,
        # not a deep copy (§2.7). The snapshot is only taken when the receipt sink
        # is opted in; with it off ``apply_op`` does no extra work (byte-stable).
        receipts_on = self.write_receipts_out is not None
        before_snapshot = self.statute.body if receipts_on else None
        try:
            self._apply_op_with_context(op)
        finally:
            self._current_mutation_op = previous_mutation_op
        if self.write_receipts_out is not None and before_snapshot is not None:
            after_snapshot = self.statute.body
            receipt = emit_uk_op_receipt(before_snapshot, after_snapshot, op)
            if receipt is not None:
                self.write_receipts_out.append(receipt)

    # ── UK materializer (Wave 5, design §3.1/§3.5). ──────────────────────────
    # The per-op tree dispatch — UK's executor ``apply_op`` (the whole
    # ``_apply_op_with_context`` dispatch over the action mixins, the warm-EID
    # CoW in ``replay_state``, the ``MutationEvent`` stream, the env-gated
    # mutation-boundary probe, and the optional ``write_receipts_out`` sink) IS
    # the UK :class:`~lawvm.core.apply_seam.Materializer`. It is a thin closure
    # over the verbatim ``self.apply_op(op)`` call: NO semantic rewrite, the body
    # mutation is the same warm-EID CoW the prior inline fold ran, and the
    # side channels (``lo_ops_out`` section snapshots, ``mutation_events_out``,
    # ``adjudications_out``, ``write_receipts_out``) stay UK-specific and remain
    # the single producers of their output — exactly like EE's ``_ee_apply_op``
    # closure (``estonia/grafter.py``).
    #
    # UK-SPECIFIC vs SEAM-OWNED. The executor mutates its OWN ``self.statute`` in
    # place across calls (it is a stateful object, unlike NO/SE/EE/EU's pure
    # ``(body, op) -> body`` folds), so the materializer ignores its
    # ``before_body`` argument — the executor already holds the live body — and
    # returns ``self.statute.body`` after the dispatch. The seam owns ONLY the
    # ``applied`` derivation (``new_state is not base_state`` — UK's body-identity
    # signal). The boundary gate is ``off`` and receipt/coverage emission is
    # disabled on the bare lane (see ``_uk_seam_apply_profile``) so the
    # seam-routed bare fold is byte-identical to the pre-cutover loop: UK keeps
    # its own per-op probe + receipt sink inside ``apply_op`` as the single
    # producers.
    def _uk_materialize_one(
        self, before_body: IRNode, op: LegalOperation
    ) -> MaterializeResult[IRNode]:
        # ``before_body`` is the seam's view of the body at loop entry; the
        # executor holds the identical reference on ``self.statute.body``. The
        # verbatim dispatch (warm-EID CoW + MutationEvent + probe + receipt sink)
        # runs exactly as the pre-cutover ``executor.apply_op(op)`` call did.
        # ``apply_op`` resets ``_uk_declared_recovery_paths`` at entry and the
        # recovery apply branches append to it during the dispatch, so after it
        # returns the carrier holds exactly this op's authorized recovery-retarget
        # parent paths. They are surfaced on the :class:`MaterializeResult` so the
        # seam's always-on LS-01 observer declares the retargeted write as
        # in-boundary (mirrors NO/EE) instead of reading it as an undeclared
        # escape. Production materialization output is unchanged — this only
        # DECLARES the already-happening authorized recovery write.
        self.apply_op(op)
        return MaterializeResult(
            new_state=self.statute.body,
            declared_recovery_prefixes=tuple(self._uk_declared_recovery_paths),
        )

    def _uk_seam_apply_profile(self) -> ApplyProfile[IRNode]:
        # ``boundary_mode="off"``: the seam's always-on observer is the SINGLE
        # LS-01 producer; the in-fold probe is retired and ``replay_uk_ops`` drains
        # the seam observation into the env-gated
        # ``uk_replay_mutation_boundary_per_op_violation_observed`` adjudication, so
        # the env-flag-ON output is byte-identical to the pre-cutover fold (mirrors
        # NO/SE). ``emit_receipts``/``emit_coverage`` are False on the bare
        # lane: UK's bare ``replay_uk_ops`` result (the materialized
        # ``IRStatute`` + the ``write_receipts_out`` / ``lo_ops_out`` /
        # ``mutation_events_out`` side channels produced INSIDE ``apply_op``)
        # stays byte-identical (no new artifacts). The additive conserved +
        # seam-synthesized-receipt lane is the dedicated
        # ``replay_uk_ops_conserved`` / ``uk_replay_write_receipts`` callers.
        # ``receipt_helper_prefix="UKReplayExecutor.apply_op"`` +
        # ``renumber_migration_rule_ids=(uk_section_renumber_relabel,)`` make the
        # seam-synthesized receipt byte-identical to UK's existing
        # ``emit_uk_op_receipt`` if a caller routes receipts through the seam.
        # ``authorization_resolver=_uk_execution_authorization`` (EV-05): UK is now
        # a MINTING frontend. The resolver reads a proof already on the op's
        # ``execution_authorization`` carrier, else mints one from the op's
        # affecting-act identity (``op.source.statute_id``). A mutating op with a
        # known affecting act goes QUIET on the EV-05 observe gate; an op with no
        # affecting-act identity (unknown authority) emits the non-blocking
        # ``EVID.REPLAY_AUTHORIZATION_PROOF_OBSERVED`` witness to
        # ``AppliedOp.observations`` — never to ``findings`` (byte-identity: the
        # gate's verdict drives no production output; the witness is drained only
        # via the opt-in ``seam_observations_out``). No proof is fabricated for an
        # unknown-authority op (§2.10). UK carries no typed Parsed-vs-Recovered
        # signal on its ops (no ``LegalOperation.scope_confidence`` rider, no
        # ``scope_confidence:`` provenance-tag rung), so AM-01 is NOT wired here.
        return ApplyProfile(
            jurisdiction="uk",
            materializer=self._uk_materialize_one,
            boundary_mode="off",
            emit_receipts=False,
            emit_coverage=False,
            renumber_migration_rule_ids=(UK_SECTION_RENUMBER_RELABEL_RULE_ID,),
            receipt_helper_prefix="UKReplayExecutor.apply_op",
            authorization_resolver=_uk_execution_authorization,
        )

    def seam_apply_op(self, op: LegalOperation) -> AppliedOp[IRNode]:
        """Apply one op through the unified core apply seam (Wave 5).

        Routes UK's per-op dispatch through ``core/apply_seam.apply_op`` instead
        of calling ``self.apply_op`` directly. The materializer carries the
        verbatim executor dispatch (warm-EID CoW + MutationEvent + probe +
        receipt sink); the seam owns the ``applied`` derivation. Returns the
        :class:`~lawvm.core.apply_seam.AppliedOp` so a conserved wrapper can read
        ``applied`` for its accepted/rejected partition. The executor's
        ``self.statute`` is mutated in place by the materializer (UK's stateful
        contract), so the seam's returned ``new_state`` and ``self.statute.body``
        are the same reference.
        """
        return core_apply_op(
            self.statute.body,
            op,
            provenance=op.source,
            profile=self._uk_seam_apply_profile(),
            source_statute=self.statute.statute_id,
        )

    def _apply_op_with_context(self, op: LegalOperation) -> None:
        target = op.target
        # Keep legacy warnings visible during replay runs while also recording
        # structured adjudications for downstream analyses.

        if str(target.special or "") == "whole_act":
            if _action_name(op.action) == "repeal":
                self._log("  EXECUTOR: repealing WHOLE ACT")
                # Sub-PR C+D (audit XJUR-02 / AGENTS.md §2.3): copy-on-write
                # whole-act repeal. ``IRStatute`` is frozen, so the body
                # rebuild assigns a NEW IRNode whose children tuple is empty
                # (no in-place list mutation), then ``self.statute`` itself
                # is rebuilt via ``dc_replace`` to thread the new body/supplements
                # into the immutable statute.
                self.statute = dc_replace(
                    self.statute,
                    body=dc_replace(self.statute.body, children=()),
                    supplements=(),
                )
                self._clear_eid_lookup_index()
                self._note_structure_mutation()
                self._record_whole_act_repeal_mutation_event(op)
                self._record_invariant_violations(op)
            elif _action_name(op.action) == "text_replace":
                self._apply_whole_act_text_patch_op(op, target)
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
        node: Optional[IRNode]
        parent: Optional[IRNode]
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
    write_receipts_out: Optional[list[WriteReceipt]] = None,
    replay_phase_timings_out: Optional[dict[str, float]] = None,
    applied_op_ids_out: Optional[set[str]] = None,
    seam_observations_out: Optional[list[Finding]] = None,
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
        write_receipts_out:
                    Optional list to collect per-op ``WriteReceipt`` records
                    (AGENTS.md §2.3 receipt contract). One receipt per APPLIED
                    op (skipped ops emit none — the adjudication carries the
                    witness). Additive evidence: passing the sink does NOT change
                    the replayed statute (the §2.7 grounding-neutral invariant).
        replay_phase_timings_out:
                    Optional accumulator for replay preparation, per-action
                    apply, and replay finalization timing diagnostics.
        applied_op_ids_out:
                    Optional set to collect the ``op_id`` of every PREPARED op
                    whose seam apply LANDED a write (``AppliedOp.applied`` True).
                    The §1.8 conserved wrapper (:func:`replay_uk_ops_conserved`)
                    reads this to partition prepared ops into accepted (landed)
                    vs apply-skipped (prepared but no body change) — a robust
                    per-op signal sourced from the seam, not from enumerating UK's
                    ~70 adjudication kinds. Additive: passing the sink does NOT
                    change the replayed statute (the §2.7 grounding-neutral
                    invariant); with it absent ``replay_uk_ops`` is byte-identical.

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
        write_receipts_out=write_receipts_out,
    )
    _mark_replay_phase("replay_executor_init")
    # ── Seam loop (design §3.1, Wave 5): apply each prepared op through the
    # unified per-op kernel. ``executor.seam_apply_op`` wraps UK's verbatim
    # executor dispatch behind the core seam's :class:`Materializer`; the seam
    # owns the ``applied`` derivation and (here-disabled) receipt/coverage
    # outputs, while UK's warm-EID CoW, MutationEvent stream, ``lo_ops_out``
    # snapshots, and ``write_receipts_out`` sink stay the single producers inside
    # ``apply_op`` (byte-identical to the pre-cutover loop). The per-op boundary
    # adjudication is now the seam-observation DRAIN below (the in-fold probe is
    # retired). Op input order is preserved exactly (UK ordering is settled
    # upstream in ``prepared_ops.accepted_ops``; the seam loop does not re-order).
    if replay_phase_timings_out is None:
        for op in prepared_ops.accepted_ops:
            applied = executor.seam_apply_op(op)
            if applied_op_ids_out is not None and applied.applied:
                applied_op_ids_out.add(op.op_id)
            # ── B-enforcement (LS-01): drain the seam's OBSERVE lane. The seam's
            # always-on per-op mutation-boundary audit routes any
            # ``APPLY.MUTATION_BOUNDARY_FINDING_AT_OP`` escape witness to
            # ``AppliedOp.observations`` (NEVER ``findings`` while
            # ``boundary_mode="off"`` — production output is byte-identical). When
            # ``seam_observations_out`` is provided (the corpus boundary-clean
            # MEASUREMENT + block-promotion decision), they are appended verbatim.
            # Default ``None`` is a pure no-op (byte-identical).
            if seam_observations_out is not None and applied.observations:
                seam_observations_out.extend(applied.observations)
            # ── LS-01 cleanup: drain the boundary observation into the env-gated
            # UK adjudication (the retired in-fold probe's surface). The seam ran
            # the IDENTICAL core audit with the SAME ``declared_recovery_prefixes``
            # the probe used; default (env-off or ``adjudications_out is None``) is
            # a pure no-op → byte-identical production.
            _uk_drain_seam_boundary_observations(
                applied.observations,
                adjudications_out=adjudications_out,
                source_statute=base.statute_id,
                op_id=op.op_id,
            )
    else:
        for op in prepared_ops.accepted_ops:
            op_t0 = time.perf_counter()
            applied = executor.seam_apply_op(op)
            if applied_op_ids_out is not None and applied.applied:
                applied_op_ids_out.add(op.op_id)
            if seam_observations_out is not None and applied.observations:
                seam_observations_out.extend(applied.observations)
            _uk_drain_seam_boundary_observations(
                applied.observations,
                adjudications_out=adjudications_out,
                source_statute=base.statute_id,
                op_id=op.op_id,
            )
            action_name = _action_name(op.action)
            key = f"replay_apply_{action_name}"
            replay_phase_timings_out[key] = replay_phase_timings_out.get(key, 0.0) + (
                time.perf_counter() - op_t0
            )
        replay_phase_t0 = time.perf_counter()

    if adjudications_out is not None:
        append_replay_fold_text_duplication_adjudications(
            adjudications_out,
            frozen_statute=executor.statute,
            source_statute=base.statute_id,
        )
        _mark_replay_phase("replay_fold_text_duplication")

    replayed = executor.statute
    _mark_replay_phase("replay_to_ir")
    return replayed

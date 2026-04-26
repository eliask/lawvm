"""Legacy Finland apply fallback.

This module owns the original field-based replay dispatcher. It remains the
compatibility safety net for operations that reach apply without a typed
CanonicalIntent.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING, FrozenSet, List, Literal, Optional

from lawvm.core.compile_result import SourcePathology
from lawvm.core.ir import IRNode
from lawvm.core.ir import LegalOperation as _LegalOperation
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core import tree_ops as _tops
from lawvm.core.tree_ops import Path
from lawvm.core.compile_result import StrictProfile
from lawvm.finland.ops import AmendmentOp, FailedOp, ResolvedOp, TargetUnitKind, get_replay_profile
from lawvm.finland.replay_notices import replay_print
from lawvm.finland.apply_runtime_support import _legacy_dispatch_shell_for_rop, _valid_target_path_hint
from lawvm.finland.apply_policy import _observe_occupancy_transition
from lawvm.finland.apply_structure_ops import (
    _structure_apply_view_for_op,
    _apply_container_op,
    _apply_whole_section_op,
    _apply_materialization,
)
from lawvm.finland.apply_subsection_dispatch import (
    _apply_deterministic_subsection_op,
    _normalize_subsection_dispatch_inputs,
)
from lawvm.finland.apply_events import (
    ApplyMutationEvent,
    DeclaredMutationAllowance,
    _emit_apply_mutation_event,
    _emit_apply_mutation_event_for_rop,
    _path_to_tuple,
    _resolved_target_path_for_event,
    _resolved_target_path_for_rop_event,
    _target_address_path_for_rop_event,
)
from lawvm.finland.migration_ledger import MigrationLedger

if TYPE_CHECKING:
    from lawvm.finland.statute import ReplayState, StatuteContext
    from lawvm.finland.payload_normalize import SubsectionSlotAssignmentResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _FailureContext:
    amendment_id: str
    description: str
    target_section: str
    target_chapter: Optional[str]
    target_part: Optional[str]
    target_unit_kind: TargetUnitKind


def _failure_context(dispatch_op: AmendmentOp, rop: ResolvedOp | None, rop_description: str | None) -> _FailureContext:
    if rop is not None:
        return _FailureContext(
            amendment_id=rop.resolved_source_statute,
            description=rop_description or dispatch_op.description(),
            target_section=rop.resolved_target_label,
            target_chapter=rop.resolved_target_scope_chapter_label,
            target_part=rop.resolved_target_scope_part_label,
            target_unit_kind=rop.target_unit_kind,
        )
    return _FailureContext(
        amendment_id=dispatch_op.source_statute or "",
        description=dispatch_op.description(),
        target_section=dispatch_op.target_section or "",
        target_chapter=dispatch_op.target_chapter,
        target_part=dispatch_op.target_part,
        target_unit_kind=dispatch_op.target_unit_kind,
    )


def _apply_legacy_dispatch(
    state: "ReplayState",
    op: AmendmentOp,
    op_description: str,
    ctx: "StatuteContext",
    muutos_ir: Optional[IRNode],
    cross_ir: Optional[IRNode] = None,
    amend_sub_ir: Optional[IRNode] = None,
    slot_assignment: "SubsectionSlotAssignmentResult | None" = None,
    replay_mode: Literal["finlex_oracle", "legal_pit"] = "finlex_oracle",
    failed_ops_out: Optional[List[FailedOp]] = None,
    source_pathologies_out: Optional[List[SourcePathology]] = None,
    mutation_events_out: Optional[List[ApplyMutationEvent]] = None,
    path_hint: Path | None = None,
    standalone_section_targets: Optional[FrozenSet] = None,
    rop: Optional[ResolvedOp] = None,
    replay_history_ops: Optional[List[_LegalOperation]] = None,
    migration_ledger: Optional[MigrationLedger] = None,
    inputs_prepared: bool = False,
    strict_profile: Optional[StrictProfile] = None,
) -> "ReplayState":
    """Legacy field-based apply dispatch.

    Direct callers should prefer `apply_op()` so the typed-intent waiver is
    handled once at the public apply boundary.
    """
    if rop is not None:
        if not inputs_prepared:
            if muutos_ir is None:
                muutos_ir = rop.muutos_ir
            if cross_ir is None:
                cross_ir = rop.cross_ir
            if amend_sub_ir is None:
                amend_sub_ir = rop.resolved_amend_sub_ir()
            if slot_assignment is None:
                slot_assignment = rop.slot_assignment
    raw_path_hint = path_hint
    if raw_path_hint is not None:
        path_hint = _tops._as_path(raw_path_hint)
    dispatch_op: AmendmentOp = op if inputs_prepared or rop is None else _legacy_dispatch_shell_for_rop(rop)

    profile = get_replay_profile(replay_mode)
    base_ir = ctx.base_ir
    # Keep typed structure witnesses from the late-waist op when available.
    # The legacy dispatch shell is only a compatibility carrier for field-based
    # helpers; building the structure view from it would silently drop
    # ResolvedOp-only witnesses such as payload completeness.
    structure_view = _structure_apply_view_for_op(rop if rop is not None else dispatch_op)
    failure_context = _failure_context(dispatch_op, rop, op_description)
    ctx_label = f"[{failure_context.amendment_id}] {failure_context.description}"
    used_fallback_tags: tuple[str, ...] = ()

    def _emit(
        *,
        helper: str,
        outcome: str,
        resolved_target_path: tuple[tuple[str, str], ...] | None = None,
        failure_reason: str = "",
        reason_code: str = "",
        declared_allowances: tuple[DeclaredMutationAllowance, ...] = (),
        consumed_paths: tuple[tuple[tuple[str, str], ...], ...] = (),
        created_paths: tuple[tuple[tuple[str, str], ...], ...] = (),
        removed_paths: tuple[tuple[tuple[str, str], ...], ...] = (),
        replaced_paths: tuple[tuple[tuple[str, str], ...], ...] = (),
    ) -> None:
        if rop is not None:
            _emit_apply_mutation_event_for_rop(
                mutation_events_out,
                rop=rop,
                helper=helper,
                outcome=outcome,
                resolved_target_path=resolved_target_path,
                used_fallback_tags=used_fallback_tags,
                failure_reason=failure_reason,
                reason_code=reason_code,
                declared_allowances=declared_allowances,
                consumed_paths=consumed_paths,
                created_paths=created_paths,
                removed_paths=removed_paths,
                replaced_paths=replaced_paths,
            )
            return
        _emit_apply_mutation_event(
            mutation_events_out,
            op=op,
            helper=helper,
            outcome=outcome,
            resolved_target_path=resolved_target_path,
            used_fallback_tags=used_fallback_tags,
            failure_reason=failure_reason,
            reason_code=reason_code,
            declared_allowances=declared_allowances,
            consumed_paths=consumed_paths,
            created_paths=created_paths,
            removed_paths=removed_paths,
            replaced_paths=replaced_paths,
        )

    def _fail(reason: str, *, reason_code: str = "") -> None:
        replay_print(f"  {ctx_label} -> FAILED ({reason})")
        if failed_ops_out is not None:
            failed_ops_out.append(
                FailedOp.from_scope(
                    amendment_id=failure_context.amendment_id,
                    description=failure_context.description,
                    reason=reason,
                    reason_code=reason_code,
                    target_section=failure_context.target_section,
                    target_chapter=failure_context.target_chapter,
                    target_part=failure_context.target_part,
                    target_unit_kind=failure_context.target_unit_kind,
                )
            )
        _emit(
            helper="apply_op",
            outcome="failed",
            resolved_target_path=(
                _target_address_path_for_rop_event(rop, path_hint) if rop is not None else _path_to_tuple(path_hint)
            ),
            failure_reason=reason,
            reason_code=reason_code,
        )

    mixed_sparse_insert = (
        slot_assignment is not None
        and structure_view.target_paragraph is None
        and structure_view.target_item is None
        and structure_view.target_special is None
        and any(binding.op_type == "INSERT" for binding in slot_assignment.sparse_slot_bindings)
    )
    container_result = _apply_container_op(
        state,
        structure_view,
        muutos_ir,
        profile,
        ctx_label,
        base_ir=base_ir,
        standalone_section_targets=standalone_section_targets,
        mixed_sparse_insert=mixed_sparse_insert,
        source_pathologies_out=source_pathologies_out,
        migration_ledger=migration_ledger,
    )
    if container_result is not None:
        _emit(
            helper="_apply_container_op",
            outcome="applied" if container_result is not state else "failed",
            resolved_target_path=(
                _target_address_path_for_rop_event(rop, path_hint) if rop is not None else _path_to_tuple(path_hint)
            ),
        )
        return container_result

    sec_path = _valid_target_path_hint(
        state,
        target_unit_kind=dispatch_op.target_unit_kind,
        target_norm=dispatch_op.target_section or "",
        target_chapter=dispatch_op.target_chapter,
        target_part=dispatch_op.target_part,
        path_hint=path_hint,
    )
    if sec_path is None:
        sec_path = state.find_section_path(
            dispatch_op.target_section or "",
            dispatch_op.target_chapter,
            dispatch_op.target_part,
        )
    sec_path = _tops._as_path(sec_path) if sec_path is not None else None
    if sec_path is not None and dispatch_op.target_chapter:
        resolved_chapter = next((label for kind, label in sec_path if kind == "chapter"), None)
        if resolved_chapter != dispatch_op.target_chapter:
            logger.debug(
                "  %s -> rejected resolved path outside stated chapter (%s != %s)",
                ctx_label,
                resolved_chapter,
                dispatch_op.target_chapter,
            )
            sec_path = None

    _observe_occupancy_transition(dispatch_op, sec_path, state, ctx_label)

    existing_global_section_count = 0
    if dispatch_op.target_section:
        existing_global_section_count = len(
            state.provision_index.get(("section", _tops._norm(dispatch_op.target_section)), [])
        )

    blocked_scoped_whole_section_replace_recovery = (
        sec_path is None
        and dispatch_op.target_chapter
        and dispatch_op.op_type == "REPLACE"
        and structure_view.target_unit_kind == "section"
        and structure_view.target_paragraph is None
        and structure_view.target_item is None
        and structure_view.target_special is None
        and existing_global_section_count > 0
        and dispatch_op.move_clause_target_unit_kind != "chapter"
    )

    whole_result = None
    migration_rebased_target_path = None
    migration_rebase_source_path = None
    if (
        dispatch_op.op_type in {"INSERT", "REPLACE"}
        and migration_ledger is not None
        and dispatch_op.lo is not None
    ):
        migrated = migration_ledger.current_address_with_prefix_migrations(dispatch_op.lo.target)
        if migrated != dispatch_op.lo.target and migrated.path and migrated.path[-1][0] == "section":
            migration_rebased_target_path = _path_to_tuple(migrated.path)
            source_labels = {kind: label for kind, label in dispatch_op.lo.target.path}
            source_section = source_labels.get("section")
            if source_section:
                migration_rebase_source_path = _path_to_tuple(
                    state.find_section_path(
                        source_section,
                        source_labels.get("chapter"),
                        source_labels.get("part"),
                    )
                )
            sec_path = None
    if not blocked_scoped_whole_section_replace_recovery:
        whole_result = _apply_whole_section_op(
            state,
            structure_view,
            sec_path,
            muutos_ir,
            cross_ir,
            profile,
            ctx_label,
            base_ir=base_ir,
            replay_history_ops=replay_history_ops,
            source_pathologies_out=source_pathologies_out,
            mixed_sparse_insert=mixed_sparse_insert,
            migration_ledger=migration_ledger,
        )
    if whole_result is not None:
        resolved_target_path = (
            _resolved_target_path_for_rop_event(rop, sec_path)
            if rop is not None
            else _resolved_target_path_for_event(dispatch_op, sec_path)
        )
        if migration_rebased_target_path is not None:
            resolved_target_path = migration_rebased_target_path
        created_paths = ()
        replaced_paths = ()
        removed_paths = ()
        declared_allowances = ()
        if migration_rebase_source_path is not None:
            declared_allowances = (
                DeclaredMutationAllowance(
                    kind="migration_path",
                    paths=(migration_rebase_source_path,),
                    rule_id="pending_source_chain_insert_rebase",
                ),
            )
            removed_paths = (migration_rebase_source_path,)
        if resolved_target_path is not None:
            if dispatch_op.op_type == "INSERT":
                created_paths = (resolved_target_path,)
            elif dispatch_op.op_type == "REPLACE":
                if sec_path is None:
                    created_paths = (resolved_target_path,)
                else:
                    replaced_paths = (resolved_target_path,)
        _emit(
            helper="_apply_whole_section_op",
            outcome="applied" if whole_result is not state else "failed",
            resolved_target_path=resolved_target_path,
            declared_allowances=declared_allowances,
            created_paths=created_paths,
            replaced_paths=replaced_paths,
            removed_paths=removed_paths,
        )
        return whole_result

    mat_result = None
    if not blocked_scoped_whole_section_replace_recovery:
        mat_result = _apply_materialization(
            state,
            structure_view,
            muutos_ir,
            ctx_label,
            migration_ledger=migration_ledger,
            source_pathologies_out=source_pathologies_out,
        )
    if mat_result is not None:
        _emit(
            helper="_apply_materialization",
            outcome="applied" if mat_result is not state else "failed",
            resolved_target_path=(
                _resolved_target_path_for_rop_event(rop, sec_path)
                if rop is not None
                else _resolved_target_path_for_event(dispatch_op, sec_path)
            ),
        )
        return mat_result

    if sec_path is None:
        _fail(
            f"master §{dispatch_op.target_section} not found",
            reason_code="section_not_found",
        )
        return state

    sec_node = _tops.resolve(state.ir, sec_path)
    assert sec_node is not None, f"resolve failed for {sec_path}"
    master_subsecs_ir = [c for c in sec_node.children if c.kind == IRNodeKind.SUBSECTION]
    if rop is not None:
        resolved_amend_sub_ir = rop.resolved_amend_sub_ir()
        dispatch_slot_assignment = None
        subsection_rop: ResolvedOp | None = rop
        subsection_dispatch_op = dispatch_op
    else:
        resolved_amend_sub_ir = (
            slot_assignment.resolve_apply_subsection_ir(dispatch_op, amend_sub_ir)
            if slot_assignment is not None
            else amend_sub_ir
        )
        dispatch_slot_assignment = slot_assignment
        subsection_rop = None
        subsection_dispatch_op = dispatch_op
    subsection_dispatch_op, subsection_rop = _normalize_subsection_dispatch_inputs(
        dispatch_op=subsection_dispatch_op,
        rop=subsection_rop,
        master_subsecs=master_subsecs_ir,
        amend_sub_ir=resolved_amend_sub_ir,
        ctx_label=ctx_label,
        source_pathologies_out=source_pathologies_out,
        strict_profile=strict_profile,
    )

    subsection_result = _apply_deterministic_subsection_op(
        state,
        subsection_dispatch_op,
        sec_path,
        muutos_ir,
        resolved_amend_sub_ir,
        dispatch_slot_assignment,
        profile,
        ctx_label,
        source_pathologies_out,
        strict_profile=strict_profile,
        cross_ir=cross_ir,
        rop=subsection_rop,
        replay_history_ops=replay_history_ops,
        base_ir=base_ir,
    )
    if subsection_result is not None:
        resolved_target_path = (
            _resolved_target_path_for_rop_event(rop, sec_path)
            if rop is not None
            else _resolved_target_path_for_event(dispatch_op, sec_path)
        )
        consumed_paths = ()
        created_paths = ()
        replaced_paths = ()
        if subsection_result is not state and resolved_target_path is not None:
            action = dispatch_op.op_type.lower()
            if action == "insert":
                created_paths = (resolved_target_path,)
            elif action == "replace":
                replaced_paths = (resolved_target_path,)
            else:
                consumed_paths = (resolved_target_path,)
        _emit(
            helper="_apply_deterministic_subsection_op",
            outcome="applied" if subsection_result is not state else "failed",
            resolved_target_path=resolved_target_path,
            consumed_paths=consumed_paths,
            created_paths=created_paths,
            replaced_paths=replaced_paths,
        )
        return subsection_result

    _fail("no deterministic path", reason_code="no_deterministic_path")
    return state

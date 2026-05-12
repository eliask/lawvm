"""Typed dispatch layer for Finland apply.

This module owns the CanonicalIntent-driven section/container dispatch and the
top-level typed action routing. `apply.py` keeps the public `apply_op` entry
point plus the legacy fallback path for now.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, FrozenSet, List, Optional, cast

from lawvm.core.compile_result import SourcePathology, StrictProfile
from lawvm.core.ir import IRNode, LegalAddress
from lawvm.core.ir import LegalOperation as _LegalOperation
from lawvm.core.phase_result import Finding
from lawvm.core.semantic_types import FacetKind, IRNodeKind
from lawvm.core import tree_ops as _tops
from lawvm.core.tree_ops import Path
from lawvm.finland.ops import FailedOp, ReplayProfile, ResolvedOp, _assert_intent_compat
from lawvm.finland.apply_policy import _resolve_section_path_with_fallbacks, _check_occupancy_policy
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
    _emit_apply_mutation_event_for_rop,
    _path_to_tuple,
    _resolved_target_path_for_rop_event,
    _target_address_path_for_rop_event,
)
from lawvm.finland.apply_ir_ops import (
    _rebuild_section_with_subsections_ir,
    _relabel_chapter_ir,
    _relabel_section_ir,
    _relabel_subsection_ir,
)
from lawvm.finland.apply_runtime_support import _find_insert_parent_path
from lawvm.finland.migration_ledger import MigrationLedger
from lawvm.finland.replay_notices import replay_print

if TYPE_CHECKING:  # pragma: no cover
    from lawvm.core.canonical_intent import CanonicalIntent, Insert, Repeal, Relabel, Replace
    from lawvm.core.canonical_intent import Move
    from lawvm.finland.statute import ReplayState, StatuteContext

logger = logging.getLogger(__name__)


_MOVE_SKIP_REASON_CODES = {
    "source_address_empty": "source_address_empty",
    "source_not_found": "source_not_found",
    "source_resolved_none": "source_resolved_none",
    "destination_parent_not_found": "destination_parent_not_found",
    "destination_exists": "destination_exists",
}


def _address_leaf_kind(address) -> str:
    return address.leaf_kind() if address is not None else ""


def _is_container_facet_replace(intent: "Replace") -> bool:
    from lawvm.core.canonical_intent import FacetTarget

    return isinstance(intent.target, FacetTarget) and intent.target.facet in {FacetKind.HEADING, FacetKind.INTRO} and (
        _address_leaf_kind(intent.target.host) in {"chapter", "part"}
    )


def _relabel_source_unit_kind(intent: "Relabel") -> str:
    return _address_leaf_kind(intent.source.address)


def _materialization_root_move_allowances(
    state: "ReplayState",
    rop: ResolvedOp,
    muutos_ir: Optional[IRNode],
    sec_path: Path | None,
) -> tuple[DeclaredMutationAllowance, ...]:
    root_move_paths = _materialization_root_move_paths(state, rop, muutos_ir, sec_path)
    if not root_move_paths:
        return ()
    return (
        DeclaredMutationAllowance(
            kind="recovery_path",
            paths=root_move_paths,
            rule_id="section_materialization_root_move_destination_rebind",
        ),
        DeclaredMutationAllowance(
            kind="migration_path",
            paths=root_move_paths,
            rule_id="section_materialization_root_move_destination_rebind",
        ),
    )


def _materialization_root_move_paths(
    state: "ReplayState",
    rop: ResolvedOp,
    muutos_ir: Optional[IRNode],
    sec_path: Path | None,
) -> tuple[tuple[tuple[str, str], ...], ...]:
    if sec_path is not None or muutos_ir is None or muutos_ir.kind is not IRNodeKind.SECTION:
        return ()
    target_chapter = rop.resolved_target_scope_chapter_label
    target_norm = str(rop.resolved_target_label or "").strip()
    payload_label = str(muutos_ir.label or "").strip()
    if not target_chapter or not target_norm or not payload_label:
        return ()
    if _tops._norm(payload_label) != _tops._norm(target_norm):
        return ()
    matches = state.provision_index.get(("section", _tops._norm(target_norm)), [])
    root_matches = [
        _tops._as_path(path)
        for path in matches
        if not any(kind == "chapter" for kind, _label in _tops._as_path(path))
    ]
    if len(root_matches) != 1:
        return ()
    return (root_matches[0],)


def _whole_section_move_rebind_paths(
    state: "ReplayState",
    rop: ResolvedOp,
    muutos_ir: Optional[IRNode],
    sec_path: Path | None,
) -> tuple[tuple[tuple[str, str], ...], ...]:
    if sec_path is not None or muutos_ir is None or muutos_ir.kind is not IRNodeKind.SECTION:
        return ()
    target_chapter = rop.resolved_target_scope_chapter_label
    target_norm = str(rop.resolved_target_label or "").strip()
    payload_label = str(muutos_ir.label or "").strip()
    if not target_chapter or not target_norm or not payload_label:
        return ()
    if _tops._norm(payload_label) != _tops._norm(target_norm):
        return ()
    matches = state.provision_index.get(("section", _tops._norm(target_norm)), [])
    root_matches = [
        _tops._as_path(path)
        for path in matches
        if not any(kind == "chapter" for kind, _label in _tops._as_path(path))
    ]
    candidate_paths = root_matches if len(root_matches) == 1 else ([_tops._as_path(matches[0])] if len(matches) == 1 else [])
    if not candidate_paths:
        return ()
    existing_path = candidate_paths[0]
    existing_chapter = next((label for kind, label in existing_path if kind == "chapter"), None)
    if rop.resolved_action_type == "REPLACE":
        if not existing_chapter or existing_chapter != target_chapter:
            return (existing_path,)
        return ()
    if rop.resolved_action_type == "INSERT":
        if not existing_chapter:
            return (existing_path,)
        existing_node = _tops.resolve(state.ir, existing_path)
        is_placeholder = existing_node is not None and existing_node.attrs.get("lawvm_repeal_placeholder") == "1"
        if is_placeholder:
            return (existing_path,)
        if re.fullmatch(rf"{re.escape(existing_chapter)}[a-z]+", target_chapter, re.I) is not None:
            return (existing_path,)
    return ()


def _whole_section_move_rebind_allowances(
    state: "ReplayState",
    rop: ResolvedOp,
    muutos_ir: Optional[IRNode],
    sec_path: Path | None,
) -> tuple[DeclaredMutationAllowance, ...]:
    rebind_paths = _whole_section_move_rebind_paths(state, rop, muutos_ir, sec_path)
    if not rebind_paths:
        return ()
    rule_id = ""
    if rop.resolved_action_type == "REPLACE":
        rule_id = "section_move_replace_destination_rebind"
    elif rop.resolved_action_type == "INSERT":
        rule_id = "section_move_insert_destination_rebind"
    if not rule_id:
        return ()
    return (
        DeclaredMutationAllowance(
            kind="recovery_path",
            paths=rebind_paths,
            rule_id=rule_id,
        ),
        DeclaredMutationAllowance(
            kind="migration_path",
            paths=rebind_paths,
            rule_id=rule_id,
        ),
    )


def _intent_targets_section(intent: "CanonicalIntent") -> bool:
    from lawvm.core.canonical_intent import FacetTarget, Insert, NodeTarget, Relabel, Repeal, Replace

    match intent:
        case Replace(target=NodeTarget(address=addr)) if _address_leaf_kind(addr) == "section":
            return True
        case Replace(target=FacetTarget(host=host)) if _address_leaf_kind(host) == "section":
            return True
        case Insert(target=NodeTarget(address=addr)) if _address_leaf_kind(addr) == "section":
            return True
        case Repeal(target=NodeTarget(address=addr)) if _address_leaf_kind(addr) == "section":
            return True
        case Relabel(source=source) if _address_leaf_kind(source.address) == "section":
            return True
        case _:
            return False


def _parent_path(path: tuple[tuple[str, str], ...] | None) -> tuple[tuple[str, str], ...] | None:
    if path is None or not path:
        return None
    return path[:-1]


def _find_scoped_section_insert_parent_path(
    ir: IRNode,
    *,
    chapter_label: str | None,
    part_label: str | None,
) -> tuple[tuple[str, str], ...]:
    """Resolve a section parent path without dropping part scope.

    Bare chapter-label lookup is unsafe when multiple parts contain the same
    chapter label, as in `2017/320 <- 2019/371`. Prefer the explicitly scoped
    part/chapter parent when available.
    """
    if part_label:
        part_path = _tops.find(ir, "part", part_label)
        if part_path is not None:
            part_node = _tops.resolve(ir, part_path)
            if part_node is not None and chapter_label:
                chapter_in_part = _tops.find(part_node, "chapter", chapter_label)
                if chapter_in_part is not None:
                    return _tops._as_path(part_path + chapter_in_part)
            return _tops._as_path(part_path)
    return _find_insert_parent_path(ir, chapter_label)


def _apply_intent_section_level(
    state: "ReplayState",
    rop: ResolvedOp,
    rop_description: str,
    ctx: "StatuteContext",
    profile: ReplayProfile,
    ctx_label: str,
    muutos_ir: Optional[IRNode],
    *,
    cross_ir: Optional[IRNode] = None,
    failed_ops_out: Optional[List[FailedOp]] = None,
    source_pathologies_out: Optional[List[SourcePathology]] = None,
    mutation_events_out: Optional[List[ApplyMutationEvent]] = None,
    strict_profile: Optional[StrictProfile] = None,
    path_hint: Optional[Path] = None,
    replay_history_ops: Optional[List[_LegalOperation]] = None,
    migration_ledger: Optional[MigrationLedger] = None,
) -> "ReplayState":
    used_fallback_tags: tuple[str, ...] = ()
    base_ir = ctx.base_ir

    def _fail(reason: str, *, reason_code: str = "") -> None:
        replay_print(f"  {ctx_label} → FAILED ({reason})")
        if failed_ops_out is not None:
            failed_ops_out.append(
                FailedOp.from_scope(
                    amendment_id=rop.resolved_source_statute,
                    description=rop_description,
                    reason=reason,
                    reason_code=reason_code,
                    target_section=rop.resolved_target_label,
                    target_chapter=rop.resolved_target_scope_chapter_label,
                    target_part=rop.resolved_target_scope_part_label,
                    target_unit_kind=rop.target_unit_kind,
                )
            )
        _emit_apply_mutation_event_for_rop(
            mutation_events_out,
            rop=rop,
            helper="apply_op",
            outcome="failed",
            resolved_target_path=_target_address_path_for_rop_event(rop, path_hint),
            used_fallback_tags=used_fallback_tags,
            failure_reason=reason,
            reason_code=reason_code,
        )

    section_resolution = _resolve_section_path_with_fallbacks(
        state,
        rop,
        muutos_ir,
        path_hint,
        ctx_label,
        migration_ledger=migration_ledger,
    )
    sec_path = section_resolution.path
    if section_resolution.used_live_unique_global_fallback:
        used_fallback_tags = (
            "APPLY.SCOPE_CONFIDENCE_GLOBAL_FALLBACK",
            section_resolution.reason_code or "live_unique_global_fallback",
        )
    elif section_resolution.reason_code == "follow_same_wave_migration":
        used_fallback_tags = (
            "APPLY.SAME_WAVE_MIGRATION_REBASE",
            "follow_same_wave_migration",
        )
    mixed_sparse_insert = (
        rop.slot_assignment is not None
        and rop.effective_target_paragraph is None
        and rop.effective_target_item_label is None
        and rop.effective_target_special is None
        and any(binding.op_type == "INSERT" for binding in rop.slot_assignment.sparse_slot_bindings)
    )
    migration_rebased_target_path: tuple[tuple[str, str], ...] | None = None
    migration_rebase_source_path: tuple[tuple[str, str], ...] | None = None
    if rop.resolved_action_type in {"INSERT", "REPLACE"} and migration_ledger is not None:
        rop_lo = getattr(rop, "lo", None)
        source_address = rop.resolved_target_address or (rop_lo.target if rop_lo is not None else None)
        if source_address is not None:
            migrated = migration_ledger.current_address_with_prefix_migrations(source_address)
            if migrated != source_address and migrated.path and migrated.path[-1][0] == "section":
                migration_rebased_target_path = _path_to_tuple(migrated.path)
                source_labels = {kind: label for kind, label in source_address.path}
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

    whole_result = _apply_whole_section_op(
        state,
        rop,
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
        resolved_target_path = _resolved_target_path_for_rop_event(rop, sec_path)
        if migration_rebased_target_path is not None:
            resolved_target_path = migration_rebased_target_path
        parent_path = _parent_path(resolved_target_path)
        rebind_paths = _whole_section_move_rebind_paths(state, rop, muutos_ir, sec_path)
        declared_allowances = _whole_section_move_rebind_allowances(state, rop, muutos_ir, sec_path)
        if migration_rebase_source_path is not None:
            declared_allowances = declared_allowances + (
                DeclaredMutationAllowance(
                    kind="migration_path",
                    paths=(migration_rebase_source_path,),
                    rule_id="pending_source_chain_insert_rebase",
                ),
            )
        created_paths: tuple[tuple[tuple[str, str], ...], ...] = ()
        replaced_paths: tuple[tuple[tuple[str, str], ...], ...] = ()
        removed_paths: tuple[tuple[tuple[str, str], ...], ...] = ()
        placeholder_created_paths: tuple[tuple[tuple[str, str], ...], ...] = ()
        if rop.resolved_action_type == "INSERT":
            if resolved_target_path is not None:
                created_paths = (resolved_target_path,)
            if rebind_paths:
                removed_paths = rebind_paths
            if migration_rebase_source_path is not None:
                removed_paths = tuple(dict.fromkeys((*removed_paths, migration_rebase_source_path)))
        elif rop.resolved_action_type == "REPLACE":
            if resolved_target_path is not None:
                if sec_path is None:
                    created_paths = (resolved_target_path,)
                else:
                    replaced_paths = (resolved_target_path,)
                if rebind_paths:
                    removed_paths = rebind_paths
                if migration_rebase_source_path is not None:
                    removed_paths = tuple(dict.fromkeys((*removed_paths, migration_rebase_source_path)))
        elif rop.resolved_action_type == "REPEAL":
            if profile.synthesize_repeal_placeholders:
                if resolved_target_path is not None:
                    placeholder_created_paths = (resolved_target_path,)
            else:
                if resolved_target_path is not None:
                    removed_paths = (resolved_target_path,)
        _emit_apply_mutation_event_for_rop(
            mutation_events_out,
            rop=rop,
            helper="_apply_whole_section_op",
            outcome="applied" if whole_result is not state else "failed",
            resolved_target_path=resolved_target_path,
            parent_path=parent_path,
            declared_allowances=declared_allowances,
            created_paths=created_paths,
            replaced_paths=replaced_paths,
            removed_paths=removed_paths,
            placeholder_created_paths=placeholder_created_paths,
            used_fallback_tags=used_fallback_tags,
        )
        return whole_result

    if sec_path is None:
        mat_result = _apply_materialization(
            state,
            rop,
            muutos_ir,
            ctx_label,
            migration_ledger=migration_ledger,
            source_pathologies_out=source_pathologies_out,
        )
        if mat_result is not None:
            resolved_target_path = _resolved_target_path_for_rop_event(rop, sec_path)
            root_move_paths = _materialization_root_move_paths(state, rop, muutos_ir, sec_path)
            _emit_apply_mutation_event_for_rop(
                mutation_events_out,
                rop=rop,
                helper="_apply_materialization",
                outcome="applied" if mat_result is not state else "failed",
                resolved_target_path=resolved_target_path,
                parent_path=_parent_path(resolved_target_path),
                declared_allowances=_materialization_root_move_allowances(state, rop, muutos_ir, sec_path),
                created_paths=(resolved_target_path,) if resolved_target_path is not None else (),
                removed_paths=root_move_paths,
                used_fallback_tags=used_fallback_tags,
            )
            return mat_result

    if sec_path is None:
        if rop.is_repeal_action and rop.targets_subsection_only():
            logger.debug(
                "  %s → subsection repeal skipped (parent section §%s already absent)",
                ctx_label,
                rop.resolved_target_label,
            )
            _emit_apply_mutation_event_for_rop(
                mutation_events_out,
                rop=rop,
                helper="apply_op",
                outcome="skipped",
                resolved_target_path=_target_address_path_for_rop_event(rop),
                used_fallback_tags=used_fallback_tags,
                failure_reason="parent section already absent (idempotent repeal)",
            )
            return state
        _fail(
            f"master §{rop.resolved_target_label} not found",
            reason_code="section_not_found",
        )
        return state

    sec_node = _tops.resolve(state.ir, sec_path)
    assert sec_node is not None, f"resolve failed for {sec_path}"
    master_subsecs_ir = [c for c in sec_node.children if c.kind == IRNodeKind.SUBSECTION]
    resolved_amend_sub_ir = rop.resolved_amend_sub_ir()
    subsection_dispatch_op, subsection_rop = _normalize_subsection_dispatch_inputs(
        dispatch_op=rop,
        rop=rop,
        master_subsecs=master_subsecs_ir,
        amend_sub_ir=resolved_amend_sub_ir,
        ctx_label=ctx_label,
        source_pathologies_out=source_pathologies_out,
        strict_profile=strict_profile,
    )
    assert subsection_rop is not None, "typed subsection normalization must preserve the late-waist op"

    subsection_result = _apply_deterministic_subsection_op(
        state,
        subsection_dispatch_op,
        sec_path,
        muutos_ir,
        resolved_amend_sub_ir,
        rop.slot_assignment,
        profile,
        ctx_label,
        source_pathologies_out,
        strict_profile=strict_profile,
        cross_ir=cross_ir,
        rop=subsection_rop,
        replay_history_ops=replay_history_ops,
        base_ir=base_ir,
        migration_ledger=migration_ledger,
    )
    if subsection_result is not None:
        resolved_target_path = _resolved_target_path_for_rop_event(rop, sec_path)
        consumed_paths = ()
        created_paths = ()
        replaced_paths = ()
        if subsection_result is not state and resolved_target_path is not None:
            action = rop.resolved_action_type.lower()
            if action == "insert":
                created_paths = (resolved_target_path,)
            elif action == "replace":
                replaced_paths = (resolved_target_path,)
            else:
                consumed_paths = (resolved_target_path,)
        _emit_apply_mutation_event_for_rop(
            mutation_events_out,
            rop=rop,
            helper="_apply_deterministic_subsection_op",
            outcome="applied" if subsection_result is not state else "failed",
            resolved_target_path=resolved_target_path,
            parent_path=_parent_path(resolved_target_path),
            used_fallback_tags=used_fallback_tags,
            consumed_paths=consumed_paths,
            created_paths=created_paths,
            replaced_paths=replaced_paths,
        )
        return subsection_result

    _fail("no deterministic path", reason_code="no_deterministic_path")
    return state


def _record_unhandled_typed_target_failed_op(
    failed_ops_out: Optional[List[FailedOp]],
    *,
    rop: ResolvedOp,
    rop_description: str,
    reason: str,
    reason_code: str,
) -> None:
    if failed_ops_out is None:
        return
    failed_ops_out.append(
        FailedOp.from_scope(
            amendment_id=rop.resolved_source_statute,
            description=rop_description,
            reason=reason,
            reason_code=reason_code,
            target_section=rop.resolved_target_label,
            target_chapter=rop.resolved_target_scope_chapter_label,
            target_part=rop.resolved_target_scope_part_label,
            target_unit_kind=rop.target_unit_kind,
        )
    )


def _apply_intent_container(
    state: "ReplayState",
    rop: ResolvedOp,
    rop_description: str,
    ctx: "StatuteContext",
    profile: ReplayProfile,
    ctx_label: str,
    muutos_ir: Optional[IRNode],
    *,
    mutation_events_out: Optional[List[ApplyMutationEvent]] = None,
    source_pathologies_out: Optional[List[SourcePathology]] = None,
    path_hint: Optional[Path] = None,
    standalone_section_targets: Optional[FrozenSet] = None,
    migration_ledger: Optional[MigrationLedger] = None,
) -> "ReplayState":
    base_ir = ctx.base_ir
    structure_view = _structure_apply_view_for_op(rop)
    mixed_sparse_insert = (
        rop.slot_assignment is not None
        and structure_view.target_paragraph is None
        and structure_view.target_item is None
        and structure_view.target_special is None
        and any(binding.op_type == "INSERT" for binding in rop.slot_assignment.sparse_slot_bindings)
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
        resolved_target_path = _target_address_path_for_rop_event(rop, path_hint)
        _emit_apply_mutation_event_for_rop(
            mutation_events_out,
            rop=rop,
            helper="_apply_container_op",
            outcome="applied" if container_result is not state else "failed",
            resolved_target_path=resolved_target_path,
            parent_path=_parent_path(resolved_target_path),
            created_paths=(resolved_target_path,) if rop.resolved_action_type == "INSERT" and resolved_target_path is not None else (),
            removed_paths=(resolved_target_path,) if rop.resolved_action_type == "REPEAL" and resolved_target_path is not None and not profile.synthesize_repeal_placeholders else (),
            replaced_paths=(resolved_target_path,) if rop.resolved_action_type == "REPLACE" and resolved_target_path is not None else (),
            placeholder_created_paths=(
                (resolved_target_path,) if rop.resolved_action_type == "REPEAL" and profile.synthesize_repeal_placeholders and resolved_target_path is not None else ()
            ),
        )
        return container_result

    logger.warning("  %s → container intent dispatch: _apply_container_op returned None", ctx_label)
    _emit_apply_mutation_event_for_rop(
        mutation_events_out,
        rop=rop,
        helper="_apply_intent_container",
        outcome="skipped",
        resolved_target_path=_target_address_path_for_rop_event(rop, path_hint),
        failure_reason="_apply_container_op returned None for container intent",
        reason_code="container_op_returned_none",
    )
    return state


def _apply_intent_replace(
    state: "ReplayState",
    rop: ResolvedOp,
    rop_description: str,
    intent: "Replace",
    ctx: "StatuteContext",
    profile: ReplayProfile,
    ctx_label: str,
    *,
    cross_ir: Optional[IRNode] = None,
    failed_ops_out: Optional[List[FailedOp]] = None,
    source_pathologies_out: Optional[List[SourcePathology]] = None,
    mutation_events_out: Optional[List[ApplyMutationEvent]] = None,
    strict_profile: Optional[StrictProfile] = None,
    path_hint: Optional[Path] = None,
    standalone_section_targets: Optional[FrozenSet] = None,
    replay_history_ops: Optional[List[_LegalOperation]] = None,
    migration_ledger: Optional[MigrationLedger] = None,
) -> "ReplayState":
    from lawvm.core.canonical_intent import FacetTarget, NodeTarget

    muutos_ir = rop.muutos_ir
    match intent.target:
        case FacetTarget(facet=FacetKind.HEADING | FacetKind.INTRO) if _is_container_facet_replace(intent):
            return _apply_intent_container(
                state,
                rop,
                rop_description,
                ctx,
                profile,
                ctx_label,
                muutos_ir,
                mutation_events_out=mutation_events_out,
                path_hint=path_hint,
                standalone_section_targets=standalone_section_targets,
                migration_ledger=migration_ledger,
            )
        case FacetTarget(facet=FacetKind.HEADING | FacetKind.INTRO):
            return _apply_intent_section_level(
                state,
                rop,
                rop_description,
                ctx,
                profile,
                ctx_label,
                muutos_ir,
                cross_ir=cross_ir,
                failed_ops_out=failed_ops_out,
                source_pathologies_out=source_pathologies_out,
                mutation_events_out=mutation_events_out,
                strict_profile=strict_profile,
                path_hint=path_hint,
                replay_history_ops=replay_history_ops,
                migration_ledger=migration_ledger,
            )
        case NodeTarget(address=addr) if _address_leaf_kind(addr) in {"item", "row", "subsection"}:
            return _apply_intent_section_level(
                state,
                rop,
                rop_description,
                ctx,
                profile,
                ctx_label,
                muutos_ir,
                cross_ir=cross_ir,
                failed_ops_out=failed_ops_out,
                source_pathologies_out=source_pathologies_out,
                mutation_events_out=mutation_events_out,
                strict_profile=strict_profile,
                path_hint=path_hint,
                replay_history_ops=replay_history_ops,
                migration_ledger=migration_ledger,
            )
        case NodeTarget(address=addr) if _address_leaf_kind(addr) == "section":
            return _apply_intent_section_level(
                state,
                rop,
                rop_description,
                ctx,
                profile,
                ctx_label,
                muutos_ir,
                cross_ir=cross_ir,
                failed_ops_out=failed_ops_out,
                source_pathologies_out=source_pathologies_out,
                mutation_events_out=mutation_events_out,
                strict_profile=strict_profile,
                path_hint=path_hint,
                replay_history_ops=replay_history_ops,
                migration_ledger=migration_ledger,
            )
        case NodeTarget(address=addr) if _address_leaf_kind(addr) in {"chapter", "part"}:
            return _apply_intent_container(
                state,
                rop,
                rop_description,
                ctx,
                profile,
                ctx_label,
                muutos_ir,
                mutation_events_out=mutation_events_out,
                path_hint=path_hint,
                standalone_section_targets=standalone_section_targets,
                migration_ledger=migration_ledger,
            )
        case _:
            reason = f"unhandled Replace target: {type(intent.target).__name__}"
            reason_code = "unhandled_replace_target"
            logger.warning(
                "UNHANDLED_TYPED_TARGET: %s %s — Replace target %r unsupported in Finland apply",
                ctx_label,
                rop.target_norm,
                intent.target,
            )
            _record_unhandled_typed_target_failed_op(
                failed_ops_out,
                rop=rop,
                rop_description=rop_description,
                reason=reason,
                reason_code=reason_code,
            )
            _emit_apply_mutation_event_for_rop(
                mutation_events_out,
                rop=rop,
                helper="_apply_intent_replace",
                outcome="skipped",
                resolved_target_path=_target_address_path_for_rop_event(rop, path_hint),
                failure_reason=reason,
                reason_code=reason_code,
            )
            return state


def _apply_intent_insert(
    state: "ReplayState",
    rop: ResolvedOp,
    rop_description: str,
    intent: "Insert",
    ctx: "StatuteContext",
    profile: ReplayProfile,
    ctx_label: str,
    *,
    cross_ir: Optional[IRNode] = None,
    failed_ops_out: Optional[List[FailedOp]] = None,
    source_pathologies_out: Optional[List[SourcePathology]] = None,
    mutation_events_out: Optional[List[ApplyMutationEvent]] = None,
    strict_profile: Optional[StrictProfile] = None,
    path_hint: Optional[Path] = None,
    standalone_section_targets: Optional[FrozenSet] = None,
    replay_history_ops: Optional[List[_LegalOperation]] = None,
    migration_ledger: Optional[MigrationLedger] = None,
) -> "ReplayState":
    from lawvm.core.canonical_intent import NodeTarget

    muutos_ir = rop.muutos_ir
    match intent.target:
        case NodeTarget(address=addr) if _address_leaf_kind(addr) in {"item", "row", "subsection"}:
            return _apply_intent_section_level(
                state,
                rop,
                rop_description,
                ctx,
                profile,
                ctx_label,
                muutos_ir,
                cross_ir=cross_ir,
                failed_ops_out=failed_ops_out,
                source_pathologies_out=source_pathologies_out,
                mutation_events_out=mutation_events_out,
                strict_profile=strict_profile,
                path_hint=path_hint,
                replay_history_ops=replay_history_ops,
                migration_ledger=migration_ledger,
            )
        case NodeTarget(address=addr) if _address_leaf_kind(addr) == "section":
            return _apply_intent_section_level(
                state,
                rop,
                rop_description,
                ctx,
                profile,
                ctx_label,
                muutos_ir,
                cross_ir=cross_ir,
                failed_ops_out=failed_ops_out,
                source_pathologies_out=source_pathologies_out,
                mutation_events_out=mutation_events_out,
                strict_profile=strict_profile,
                path_hint=path_hint,
                replay_history_ops=replay_history_ops,
                migration_ledger=migration_ledger,
            )
        case NodeTarget(address=addr) if _address_leaf_kind(addr) in {"chapter", "part"}:
            return _apply_intent_container(
                state,
                rop,
                rop_description,
                ctx,
                profile,
                ctx_label,
                muutos_ir,
                mutation_events_out=mutation_events_out,
                path_hint=path_hint,
                standalone_section_targets=standalone_section_targets,
            )
        case _:
            reason = f"unhandled Insert target: {type(intent.target).__name__}"
            reason_code = "unhandled_insert_target"
            logger.warning(
                "UNHANDLED_TYPED_TARGET: %s %s — Insert target %r unsupported in Finland apply",
                ctx_label,
                rop.target_norm,
                intent.target,
            )
            _record_unhandled_typed_target_failed_op(
                failed_ops_out,
                rop=rop,
                rop_description=rop_description,
                reason=reason,
                reason_code=reason_code,
            )
            _emit_apply_mutation_event_for_rop(
                mutation_events_out,
                rop=rop,
                helper="_apply_intent_insert",
                outcome="skipped",
                resolved_target_path=_target_address_path_for_rop_event(rop, path_hint),
                failure_reason=reason,
                reason_code=reason_code,
            )
            return state


def _apply_intent_repeal(
    state: "ReplayState",
    rop: ResolvedOp,
    rop_description: str,
    intent: "Repeal",
    ctx: "StatuteContext",
    profile: ReplayProfile,
    ctx_label: str,
    *,
    failed_ops_out: Optional[List[FailedOp]] = None,
    source_pathologies_out: Optional[List[SourcePathology]] = None,
    mutation_events_out: Optional[List[ApplyMutationEvent]] = None,
    strict_profile: Optional[StrictProfile] = None,
    path_hint: Optional[Path] = None,
    replay_history_ops: Optional[List[_LegalOperation]] = None,
    migration_ledger: Optional[MigrationLedger] = None,
) -> "ReplayState":
    from lawvm.core.canonical_intent import NodeTarget

    muutos_ir = rop.muutos_ir
    match intent.target:
        case NodeTarget(address=addr) if _address_leaf_kind(addr) in {"item", "row", "subsection"}:
            return _apply_intent_section_level(
                state,
                rop,
                rop_description,
                ctx,
                profile,
                ctx_label,
                muutos_ir,
                failed_ops_out=failed_ops_out,
                source_pathologies_out=source_pathologies_out,
                mutation_events_out=mutation_events_out,
                strict_profile=strict_profile,
                path_hint=path_hint,
                replay_history_ops=replay_history_ops,
                migration_ledger=migration_ledger,
            )
        case NodeTarget(address=addr) if _address_leaf_kind(addr) == "section":
            return _apply_intent_section_level(
                state,
                rop,
                rop_description,
                ctx,
                profile,
                ctx_label,
                muutos_ir,
                failed_ops_out=failed_ops_out,
                source_pathologies_out=source_pathologies_out,
                mutation_events_out=mutation_events_out,
                strict_profile=strict_profile,
                path_hint=path_hint,
                replay_history_ops=replay_history_ops,
                migration_ledger=migration_ledger,
            )
        case NodeTarget(address=addr) if _address_leaf_kind(addr) in {"chapter", "part"}:
            return _apply_intent_container(
                state,
                rop,
                rop_description,
                ctx,
                profile,
                ctx_label,
                muutos_ir,
                mutation_events_out=mutation_events_out,
                path_hint=path_hint,
                migration_ledger=migration_ledger,
            )
        case _:
            reason = f"unhandled Repeal target: {type(intent.target).__name__}"
            reason_code = "unhandled_repeal_target"
            logger.warning(
                "UNHANDLED_TYPED_TARGET: %s %s — Repeal target %r unsupported in Finland apply",
                ctx_label,
                rop.target_norm,
                intent.target,
            )
            _record_unhandled_typed_target_failed_op(
                failed_ops_out,
                rop=rop,
                rop_description=rop_description,
                reason=reason,
                reason_code=reason_code,
            )
            _emit_apply_mutation_event_for_rop(
                mutation_events_out,
                rop=rop,
                helper="_apply_intent_repeal",
                outcome="skipped",
                resolved_target_path=_target_address_path_for_rop_event(rop, path_hint),
                failure_reason=reason,
                reason_code=reason_code,
            )
            return state


def _apply_intent_relabel(
    state: "ReplayState",
    rop: ResolvedOp,
    rop_description: str,
    intent: "Relabel",
    ctx: "StatuteContext",
    profile: ReplayProfile,
    ctx_label: str,
    *,
    failed_ops_out: Optional[List[FailedOp]] = None,
    mutation_events_out: Optional[List[ApplyMutationEvent]] = None,
    path_hint: Optional[Path] = None,
    migration_ledger: Optional[MigrationLedger] = None,
) -> "ReplayState":
    def _emit_relabel_skip(
        *,
        reason_tag: str,
        failure_reason: str,
        resolved_target_path: tuple[tuple[str, str], ...] | None,
    ) -> None:
        _emit_apply_mutation_event_for_rop(
            mutation_events_out,
            rop=rop,
            helper="_apply_intent_relabel",
            outcome="skipped",
            resolved_target_path=resolved_target_path,
            used_fallback_tags=("APPLY.RELABEL_SKIPPED", reason_tag),
            failure_reason=failure_reason,
            reason_code=reason_tag,
        )

    dest_label = (
        intent.destination.address.leaf_label()
        if intent.destination is not None and intent.destination.address.path
        else None
    )

    source_unit_kind = _relabel_source_unit_kind(intent)

    if dest_label and source_unit_kind in {"chapter", "part"}:
        kind = source_unit_kind
        src_path = None
        scoped_prefix: Path | None = None
        if source_unit_kind == "chapter":
            part_label = rop.resolved_target_scope_part_label
            if part_label:
                scoped_prefix = (("part", part_label),)
            if rop.target_norm:
                scoped_prefix = (scoped_prefix or ()) + (("chapter", rop.target_norm),)
        elif source_unit_kind == "part" and rop.target_norm:
            scoped_prefix = (("part", rop.target_norm),)
        if scoped_prefix is not None and _tops.resolve(state.ir, scoped_prefix) is not None:
            src_path = scoped_prefix
        for candidate in (
            rop.resolved_target_address.path if rop.resolved_target_address is not None else None,
            path_hint,
        ):
            if not candidate:
                continue
            prefix: Path = ()
            for step_kind, step_label in candidate:
                prefix = prefix + ((step_kind, step_label),)
                if step_kind != kind:
                    continue
                if _tops._norm(step_label) != _tops._norm(rop.target_norm):
                    continue
                if _tops.resolve(state.ir, prefix) is not None:
                    src_path = prefix
                    break
            if src_path is not None:
                break
        if src_path is None:
            src_path = state.find(kind, rop.target_norm)
        if src_path is not None:
            node = _tops.resolve(state.ir, src_path)
            if node is not None:
                renamed = (
                    _relabel_chapter_ir(node, dest_label)
                    if source_unit_kind == "chapter"
                    else IRNode(
                        kind=node.kind,
                        label=dest_label,
                        text=node.text,
                        attrs=dict(node.attrs),
                        children=tuple(node.children),
                    )
                )
                logger.debug("  %s → Relabel container %s → %s", ctx_label, rop.target_norm, dest_label)
                _emit_apply_mutation_event_for_rop(
                    mutation_events_out,
                    rop=rop,
                    helper="_apply_intent_relabel",
                    outcome="applied",
                    resolved_target_path=cast(tuple[tuple[str, str], ...], _path_to_tuple(src_path)),
                    parent_path=cast(tuple[tuple[str, str], ...], _path_to_tuple(src_path[:-1])),
                    renumbered_paths=(
                        (
                            cast(tuple[tuple[str, str], ...], _path_to_tuple(src_path)),
                            cast(
                                tuple[tuple[str, str], ...],
                                _path_to_tuple(src_path[:-1] + ((source_unit_kind, dest_label),)),
                            ),
                        ),
                    ),
                )
                if migration_ledger is not None:
                    from_addr = intent.source.address
                    to_addr = intent.destination.address
                    source = rop.resolved_op_source
                    effective = source.effective if source is not None else ""
                    migration_ledger.record_renumber(
                        from_addr,
                        to_addr,
                        effective=effective,
                        source_statute=rop.resolved_source_statute,
                    )
                return state.with_ir(_tops.replace_at(state.ir, src_path, renamed))
        logger.debug(
            "  %s → Relabel container %s not found (absent — may have been renamed already)", ctx_label, rop.target_norm
        )
        _emit_apply_mutation_event_for_rop(
            mutation_events_out,
            rop=rop,
            helper="_apply_intent_relabel",
            outcome="skipped",
            resolved_target_path=_target_address_path_for_rop_event(rop, path_hint),
            used_fallback_tags=("APPLY.RELABEL_SKIPPED", "source_container_missing"),
            failure_reason=f"source container {source_unit_kind}:{rop.target_norm} not found",
            reason_code="source_container_missing",
        )
        return state

    if dest_label and source_unit_kind == "section":
        dest_path = intent.destination.address.path if intent.destination is not None else ()
        source_target_norm, source_target_chapter, source_target_part = rop.resolved_section_lookup_scope
        dest_chapter = next((lbl for kind, lbl in dest_path if kind == "chapter"), None) or rop.resolved_target_scope_chapter_label
        dest_part = next((lbl for kind, lbl in dest_path if kind == "part"), None) or source_target_part
        src_path = state.find_section_path(
            source_target_norm,
            source_target_chapter,
            source_target_part,
        )
        if src_path is not None:
            node = _tops.resolve(state.ir, src_path)
            if node is not None:
                without_source = _tops.remove_at(state.ir, src_path)
                parent_path = (
                    src_path[:-1]
                    if dest_chapter == source_target_chapter and dest_part == source_target_part
                    else _find_scoped_section_insert_parent_path(
                        without_source,
                        chapter_label=dest_chapter,
                        part_label=dest_part,
                    )
                )
                parent_node = _tops.resolve(without_source, parent_path)
                existing_dest = _tops.find(parent_node, "section", dest_label) if parent_node is not None else None
                if existing_dest is not None:
                    logger.debug(
                        "  %s → Relabel section %s -> %s skipped (destination already exists)",
                        ctx_label,
                        rop.target_norm,
                        dest_label,
                    )
                    _emit_relabel_skip(
                        reason_tag="destination_exists",
                        failure_reason=f"destination section {dest_label} already exists",
                        resolved_target_path=cast(tuple[tuple[str, str], ...], _path_to_tuple(src_path)),
                    )
                    return state
                relabelled = _relabel_section_ir(node, dest_label)
                logger.debug(
                    "  %s → Relabel section %s -> %s%s",
                    ctx_label,
                    rop.target_norm,
                    dest_label,
                    f" in chapter {dest_chapter}" if dest_chapter else "",
                )
                _emit_apply_mutation_event_for_rop(
                    mutation_events_out,
                    rop=rop,
                    helper="_apply_intent_relabel",
                    outcome="applied",
                    resolved_target_path=cast(tuple[tuple[str, str], ...], _path_to_tuple(src_path)),
                    parent_path=cast(tuple[tuple[str, str], ...], _path_to_tuple(parent_path)),
                    renumbered_paths=(
                        (
                            cast(tuple[tuple[str, str], ...], _path_to_tuple(src_path)),
                            cast(
                                tuple[tuple[str, str], ...],
                                _path_to_tuple(parent_path + (("section", dest_label),)),
                            ),
                        ),
                    ),
                )
                if migration_ledger is not None:
                    from_addr = intent.source.address
                    to_addr = intent.destination.address
                    source = rop.resolved_op_source
                    effective = source.effective if source is not None else ""
                    migration_ledger.record_renumber(
                        from_addr,
                        to_addr,
                        effective=effective,
                        source_statute=rop.resolved_source_statute,
                    )
                return state.with_ir(_tops.insert_sorted(without_source, parent_path, relabelled))
        logger.debug(
            "  %s → Relabel section %s not found (absent — may have been renamed already)", ctx_label, rop.target_norm
        )
        _emit_relabel_skip(
            reason_tag="source_section_missing",
            failure_reason=f"source section {rop.target_norm} not found",
            resolved_target_path=_target_address_path_for_rop_event(rop, path_hint),
        )
        return state

    if dest_label and source_unit_kind == "subsection":
        source_path = intent.source.address.path
        source_section = next((lbl for kind, lbl in source_path if kind == "section"), None)
        source_chapter = next((lbl for kind, lbl in source_path if kind == "chapter"), None)
        source_part = next((lbl for kind, lbl in source_path if kind == "part"), None)
        source_subsection = intent.source.address.leaf_label()

        dest_path = intent.destination.address.path if intent.destination is not None else ()
        dest_section = next((lbl for kind, lbl in dest_path if kind == "section"), None) or source_section
        dest_chapter = next((lbl for kind, lbl in dest_path if kind == "chapter"), None) or source_chapter
        dest_part = next((lbl for kind, lbl in dest_path if kind == "part"), None) or source_part

        if source_section is None or dest_section != source_section or dest_chapter != source_chapter or dest_part != source_part:
            logger.warning(
                "RELABEL_UNHANDLED: %s %s — subsection relabel across parent boundaries not yet implemented",
                rop.resolved_action_type,
                rop.target_norm,
            )
            _emit_relabel_skip(
                reason_tag="cross_parent_unimplemented",
                failure_reason="subsection relabel across parent boundaries not yet implemented",
                resolved_target_path=_target_address_path_for_rop_event(rop, path_hint),
            )
            return state

        section_path = state.find_section_path(source_section, source_chapter, source_part)
        if section_path is None:
            _emit_relabel_skip(
                reason_tag="source_section_missing",
                failure_reason=f"source section {source_section} not found for subsection relabel",
                resolved_target_path=_target_address_path_for_rop_event(rop, path_hint),
            )
            return state
        section_node = _tops.resolve(state.ir, section_path)
        if section_node is None:
            return state

        subsections = [child for child in section_node.children if child.kind is IRNodeKind.SUBSECTION]
        source_idx = next((idx for idx, child in enumerate(subsections) if child.label == source_subsection), None)
        if source_idx is None:
            _emit_relabel_skip(
                reason_tag="source_subsection_missing",
                failure_reason=f"source subsection {source_subsection} not found",
                resolved_target_path=cast(tuple[tuple[str, str], ...], _path_to_tuple(section_path)),
            )
            return state
        if any(child.label == dest_label for idx, child in enumerate(subsections) if idx != source_idx):
            _emit_relabel_skip(
                reason_tag="destination_exists",
                failure_reason=f"destination subsection {dest_label} already exists",
                resolved_target_path=cast(tuple[tuple[str, str], ...], _path_to_tuple(section_path)),
            )
            return state

        rebuilt_subsections = list(subsections)
        rebuilt_subsections[source_idx] = _relabel_subsection_ir(rebuilt_subsections[source_idx], dest_label)
        rebuilt_subsections.sort(key=lambda child: _tops._default_sort_key(child.label))
        rebuilt_section = _rebuild_section_with_subsections_ir(section_node, rebuilt_subsections)

        _emit_apply_mutation_event_for_rop(
            mutation_events_out,
            rop=rop,
            helper="_apply_intent_relabel",
            outcome="applied",
            resolved_target_path=cast(tuple[tuple[str, str], ...], _path_to_tuple(section_path)),
            parent_path=cast(tuple[tuple[str, str], ...], _path_to_tuple(section_path)),
            renumbered_paths=(
                (
                    cast(
                        tuple[tuple[str, str], ...],
                        _path_to_tuple(section_path + (("subsection", source_subsection),)),
                    ),
                    cast(
                        tuple[tuple[str, str], ...],
                        _path_to_tuple(section_path + (("subsection", dest_label),)),
                    ),
                ),
            ),
        )
        if migration_ledger is not None:
            from_addr = intent.source.address
            to_addr = intent.destination.address
            source = rop.resolved_op_source
            effective = source.effective if source is not None else ""
            migration_ledger.record_renumber(
                from_addr,
                to_addr,
                effective=effective,
                source_statute=rop.resolved_source_statute,
            )
        return state.with_ir(_tops.replace_at(state.ir, section_path, rebuilt_section))

    logger.warning(
        "RELABEL_UNHANDLED: %s %s — Relabel target kind %r not yet implemented",
        rop.resolved_action_type,
        rop.target_norm,
        source_unit_kind,
    )
    _emit_relabel_skip(
        reason_tag="target_kind_unimplemented",
        failure_reason=f"Relabel target kind {source_unit_kind!r} not yet implemented",
        resolved_target_path=_target_address_path_for_rop_event(rop, path_hint),
    )
    return state


def _apply_intent_move(
    state: "ReplayState",
    rop: ResolvedOp,
    rop_description: str,
    intent: "Move",
    ctx: "StatuteContext",
    profile: ReplayProfile,
    ctx_label: str,
    *,
    failed_ops_out: Optional[List[FailedOp]] = None,
    mutation_events_out: Optional[List[ApplyMutationEvent]] = None,
    path_hint: Optional[Path] = None,
    migration_ledger: Optional[MigrationLedger] = None,
) -> "ReplayState":
    source_addr = intent.source.address
    dest_parent_path = intent.destination_parent.path

    source_leaf_kind = source_addr.leaf_kind()
    source_leaf_label = source_addr.leaf_label()
    if not source_leaf_kind or not source_leaf_label:
        _emit_apply_mutation_event_for_rop(
            mutation_events_out,
            rop=rop,
            helper="_apply_intent_move",
            outcome="skipped",
            resolved_target_path=_target_address_path_for_rop_event(rop, path_hint),
            failure_reason="Move source address is empty",
            reason_code=_MOVE_SKIP_REASON_CODES["source_address_empty"],
        )
        return state

    source_part = next((lbl for kind, lbl in source_addr.path if kind == "part"), None)
    source_chapter = next((lbl for kind, lbl in source_addr.path if kind == "chapter"), None)
    if source_leaf_kind == "section":
        src_path = state.find_section_path(source_leaf_label, source_chapter, source_part)
    else:
        parent = source_addr.parent()
        scope_kind = parent.leaf_kind() if parent is not None else None
        scope_label = parent.leaf_label() if parent is not None else None
        src_path = state.find(
            source_leaf_kind,
            source_leaf_label,
            scope_kind=scope_kind,
            scope_label=scope_label,
        )

    if src_path is None:
        _emit_apply_mutation_event_for_rop(
            mutation_events_out,
            rop=rop,
            helper="_apply_intent_move",
            outcome="skipped",
            resolved_target_path=_target_address_path_for_rop_event(rop, path_hint),
            failure_reason=f"source {source_leaf_kind}:{source_leaf_label} not found",
            reason_code=_MOVE_SKIP_REASON_CODES["source_not_found"],
        )
        return state

    node = _tops.resolve(state.ir, src_path)
    if node is None:
        _emit_apply_mutation_event_for_rop(
            mutation_events_out,
            rop=rop,
            helper="_apply_intent_move",
            outcome="skipped",
            resolved_target_path=cast(tuple[tuple[str, str], ...], _path_to_tuple(src_path)),
            failure_reason=f"source {source_leaf_kind}:{source_leaf_label} could not be resolved",
            reason_code=_MOVE_SKIP_REASON_CODES["source_resolved_none"],
        )
        return state

    destination_node = _tops.resolve(state.ir, dest_parent_path)
    if destination_node is None:
        _emit_apply_mutation_event_for_rop(
            mutation_events_out,
            rop=rop,
            helper="_apply_intent_move",
            outcome="skipped",
            resolved_target_path=cast(tuple[tuple[str, str], ...], _path_to_tuple(src_path)),
            failure_reason=f"destination parent {'/'.join(f'{k}:{v}' for k, v in dest_parent_path) or '<root>'} not found",
            reason_code=_MOVE_SKIP_REASON_CODES["destination_parent_not_found"],
        )
        return state

    if any(child.kind == node.kind and child.label == node.label for child in destination_node.children):
        _emit_apply_mutation_event_for_rop(
            mutation_events_out,
            rop=rop,
            helper="_apply_intent_move",
            outcome="skipped",
            resolved_target_path=cast(tuple[tuple[str, str], ...], _path_to_tuple(src_path)),
            failure_reason=f"destination already contains {node.kind.value}:{node.label}",
            reason_code=_MOVE_SKIP_REASON_CODES["destination_exists"],
        )
        return state

    moved_ir = _tops.remove_at(state.ir, src_path)
    moved_ir = _tops.insert_sorted(moved_ir, dest_parent_path, node)

    if migration_ledger is not None:
        source = rop.resolved_op_source
        effective = source.effective if source is not None else ""
        destination_address = LegalAddress(
            path=dest_parent_path + ((source_leaf_kind, source_leaf_label),),
            special=source_addr.special,
        )
        migration_ledger.record_move(
            source_addr,
            destination_address,
            effective=effective,
            source_statute=rop.resolved_source_statute,
        )

    _emit_apply_mutation_event_for_rop(
        mutation_events_out,
        rop=rop,
        helper="_apply_intent_move",
        outcome="applied",
        resolved_target_path=cast(tuple[tuple[str, str], ...], _path_to_tuple(src_path)),
        parent_path=cast(tuple[tuple[str, str], ...], _path_to_tuple(dest_parent_path)),
        renumbered_paths=(
            (
                cast(tuple[tuple[str, str], ...], _path_to_tuple(src_path)),
                cast(
                    tuple[tuple[str, str], ...],
                    _path_to_tuple(dest_parent_path + ((source_leaf_kind, source_leaf_label),)),
                ),
            ),
        ),
    )
    return state.with_ir(moved_ir)


def _apply_canonical_intent(
    state: "ReplayState",
    rop: ResolvedOp,
    rop_description: str,
    intent: "CanonicalIntent",
    ctx: "StatuteContext",
    profile: ReplayProfile,
    ctx_label: str,
    *,
    cross_ir: Optional[IRNode] = None,
    failed_ops_out: Optional[List[FailedOp]] = None,
    source_pathologies_out: Optional[List[SourcePathology]] = None,
    mutation_events_out: Optional[List[ApplyMutationEvent]] = None,
    findings_out: Optional[List[Finding]] = None,
    path_hint: Optional[Path] = None,
    standalone_section_targets: Optional[FrozenSet] = None,
    migration_ledger: Optional[MigrationLedger] = None,
    replay_history_ops: Optional[List[_LegalOperation]] = None,
    strict_profile: Optional[StrictProfile] = None,
) -> "ReplayState":
    from lawvm.core.canonical_intent import Replace, Insert, Repeal, Relabel, Move, TextPatch

    def _fail(reason: str, *, reason_code: str = "") -> None:
        replay_print(f"  {ctx_label} → FAILED ({reason})")
        if failed_ops_out is not None:
            failed_ops_out.append(
                FailedOp.from_scope(
                    amendment_id=rop.resolved_source_statute,
                    description=rop_description,
                    reason=reason,
                    reason_code=reason_code,
                    target_section=rop.resolved_target_label,
                    target_chapter=rop.resolved_target_scope_chapter_label,
                    target_part=rop.resolved_target_scope_part_label,
                    target_unit_kind=rop.target_unit_kind,
                )
            )
        _emit_apply_mutation_event_for_rop(
            mutation_events_out,
            rop=rop,
            helper="_apply_canonical_intent",
            outcome="failed",
            resolved_target_path=_target_address_path_for_rop_event(rop, path_hint),
            failure_reason=reason,
            reason_code=reason_code,
        )

    if cross_ir is None:
        cross_ir = rop.cross_ir
    target_norm, target_chapter, target_part = rop.resolved_section_lookup_scope
    sec_path = (
        state.find_section_path(target_norm, target_chapter, target_part)
        if _intent_targets_section(intent)
        else None
    )
    _check_occupancy_policy(state, rop, intent, sec_path, ctx_label, findings_out=findings_out)
    _assert_intent_compat(rop, intent, ctx_label, findings_out=findings_out)

    match intent:
        case Replace() as it:
            logger.debug("  %s → canonical dispatch: Replace(%s)", ctx_label, type(it.target).__name__)
            return _apply_intent_replace(
                state,
                rop,
                rop_description,
                it,
                ctx,
                profile,
                ctx_label,
                cross_ir=cross_ir,
                failed_ops_out=failed_ops_out,
                source_pathologies_out=source_pathologies_out,
                mutation_events_out=mutation_events_out,
                path_hint=path_hint,
                standalone_section_targets=standalone_section_targets,
                replay_history_ops=replay_history_ops,
                migration_ledger=migration_ledger,
                strict_profile=strict_profile,
            )
        case Insert() as it:
            logger.debug("  %s → canonical dispatch: Insert(%s)", ctx_label, type(it.target).__name__)
            return _apply_intent_insert(
                state,
                rop,
                rop_description,
                it,
                ctx,
                profile,
                ctx_label,
                cross_ir=cross_ir,
                failed_ops_out=failed_ops_out,
                source_pathologies_out=source_pathologies_out,
                mutation_events_out=mutation_events_out,
                path_hint=path_hint,
                standalone_section_targets=standalone_section_targets,
                replay_history_ops=replay_history_ops,
                migration_ledger=migration_ledger,
                strict_profile=strict_profile,
            )
        case Repeal() as it:
            logger.debug("  %s → canonical dispatch: Repeal(%s)", ctx_label, type(it.target).__name__)
            return _apply_intent_repeal(
                state,
                rop,
                rop_description,
                it,
                ctx,
                profile,
                ctx_label,
                failed_ops_out=failed_ops_out,
                source_pathologies_out=source_pathologies_out,
                mutation_events_out=mutation_events_out,
                strict_profile=strict_profile,
                path_hint=path_hint,
                replay_history_ops=replay_history_ops,
                migration_ledger=migration_ledger,
            )
        case Relabel() as it:
            logger.debug("  %s → canonical dispatch: Relabel(%s)", ctx_label, type(it.source).__name__)
            return _apply_intent_relabel(
                state,
                rop,
                rop_description,
                it,
                ctx,
                profile,
                ctx_label,
                failed_ops_out=failed_ops_out,
                mutation_events_out=mutation_events_out,
                path_hint=path_hint,
                migration_ledger=migration_ledger,
            )
        case Move() as move_intent:
            logger.debug(
                "  %s → canonical dispatch: Move(%s -> %s)",
                ctx_label,
                type(move_intent.source).__name__,
                type(move_intent.destination_parent).__name__,
            )
            return _apply_intent_move(
                state,
                rop,
                rop_description,
                move_intent,
                ctx,
                profile,
                ctx_label,
                failed_ops_out=failed_ops_out,
                mutation_events_out=mutation_events_out,
                path_hint=path_hint,
                migration_ledger=migration_ledger,
            )
        case TextPatch():
            logger.warning(
                "TEXTPATCH_UNSUPPORTED: %s %s — TextPatch is UK-only; failing closed in Finland apply",
                rop.resolved_action_type,
                rop.target_norm,
            )
            _fail(
                "TextPatch is UK-only and unsupported in Finland apply",
                reason_code="textpatch_unsupported",
            )
            return state
        case _:
            logger.warning(
                "UNKNOWN_TYPED_INTENT: %s %s — unknown intent type %r, failing closed",
                rop.resolved_action_type,
                rop.target_norm,
                type(intent).__name__,
            )
            _fail(
                f"unhandled intent type: {type(intent).__name__}",
                reason_code="unhandled_intent_type",
            )
            return state

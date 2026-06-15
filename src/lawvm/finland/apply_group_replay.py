"""Apply-group snapshot and path-hint replay helpers for Finland."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Optional

from lawvm.core import tree_ops as _tops
from lawvm.core.ir import IRNode, LegalAddress, LegalOperation
from lawvm.core.elaboration_context import TargetUnitKind
from lawvm.core.tree_ops import normalized_label_key
from lawvm.finland.apply_loop_state import ApplyGroupState
from lawvm.finland.apply_runtime_support import (
    _emit_section_snapshot,
    _prefer_unique_substantive_section_path_over_placeholder,
    _resolved_destination_path_for_rop,
    _valid_target_group_path_hint,
)
from lawvm.finland.migration_ledger import MigrationLedger, migration_lower_bound_for_op
from lawvm.finland.ops import ResolvedOp
from lawvm.finland.replay_history_emission import emit_granular_subsection_timeline_ops
from lawvm.finland.standalone_targets import StandaloneSectionTarget
from lawvm.finland.statute import ReplayState


@dataclass(frozen=True, slots=True)
class ApplyGroupSnapshotRequest:
    """Replay state needed to decide whether an apply group emits a timeline snapshot."""

    state: ReplayState
    group: ApplyGroupState
    amendment_id: str
    source_title: str
    amendment_issue_date: Optional[dt.date]
    amendment_effective_date: Optional[dt.date]
    base_ir: IRNode
    migration_ledger: Optional[MigrationLedger]
    standalone_section_targets: frozenset[StandaloneSectionTarget]


@dataclass(frozen=True, slots=True)
class ApplyGroupSnapshotSinks:
    """Mutable output channels for apply-group snapshot emission."""

    lo_ops_out: Optional[list[LegalOperation]] = None


def emit_apply_group_snapshot_if_allowed(
    request: ApplyGroupSnapshotRequest,
    sinks: Optional[ApplyGroupSnapshotSinks] = None,
) -> None:
    """Emit a timeline snapshot for the current apply group when replay permits it."""
    state = request.state
    group = request.group
    lo_ops_out = sinks.lo_ops_out if sinks is not None else None
    amendment_id = request.amendment_id
    source_title = request.source_title
    amendment_issue_date = request.amendment_issue_date
    amendment_effective_date = request.amendment_effective_date
    base_ir = request.base_ir
    migration_ledger = request.migration_ledger
    standalone_section_targets = request.standalone_section_targets

    if not group.group_rops or lo_ops_out is None:
        return
    if group.group_had_failed_apply:
        return
    first_rop = group.group_rops[0]
    group_key = first_rop.resolved_group_key_view
    if not emit_granular_subsection_timeline_ops(
        state,
        group.group_rops,
        lo_ops_out,
        amendment_id,
        source_title,
        amendment_issue_date,
        amendment_effective_date,
        base_ir,
        path_hint=group.group_path_hint,
    ):
        _emit_section_snapshot(
            state,
            group_key.unit_kind,
            group_key.target_norm,
            group_key.target_chapter,
            group_key.target_part,
            group.group_rops,
            lo_ops_out,
            amendment_id,
            source_title,
            amendment_issue_date,
            amendment_effective_date,
            base_ir=base_ir,
            path_hint=group.group_path_hint,
            migration_ledger=migration_ledger,
            standalone_section_targets=standalone_section_targets,
        )


@dataclass(frozen=True, slots=True)
class RefreshGroupPathHintRequest:
    """Inputs for recomputing the live target path of the current apply group."""

    state: ReplayState
    target_unit_kind: TargetUnitKind
    target_norm: str
    target_chapter: Optional[str]
    target_part: Optional[str]
    path_hint: tuple[tuple[str, str], ...] | None
    rop: Optional[ResolvedOp]
    migration_ledger: Optional[MigrationLedger]


def refresh_group_path_hint(
    request: RefreshGroupPathHintRequest,
) -> tuple[tuple[str, str], ...] | None:
    """Refresh the apply group's live target path after a possible mutation."""
    state = request.state
    target_unit_kind = request.target_unit_kind
    target_norm = request.target_norm
    target_chapter = request.target_chapter
    target_part = request.target_part
    path_hint = request.path_hint
    rop = request.rop
    migration_ledger = request.migration_ledger

    valid_hint = _valid_target_group_path_hint(
        state,
        target_unit_kind,
        target_norm,
        target_chapter,
        target_part,
        path_hint,
    )
    if valid_hint is not None:
        return tuple(valid_hint)
    hint_op_effective = migration_lower_bound_for_op(rop) if rop is not None else ""
    if rop is not None:
        dest_path = _resolved_destination_path_for_rop(rop)
        if dest_path is not None:
            dest_path_tuple = tuple(dest_path)
            if migration_ledger is not None:
                migrated = migration_ledger.current_address_with_prefix_migrations(
                    LegalAddress(path=dest_path_tuple),
                    not_before=hint_op_effective,
                )
                migrated_path = migrated.path
                if _tops.resolve(state.ir, migrated_path) is not None:
                    return migrated_path
            if _tops.resolve(state.ir, dest_path_tuple) is not None:
                return dest_path_tuple
    if target_unit_kind == "part":
        return state.find("part", target_norm)
    if target_unit_kind == "chapter":
        return state.find("chapter", target_norm)
    if target_unit_kind == "section":
        raw_path = state.find_section_path(target_norm, target_chapter, target_part)
        raw_path = _prefer_unique_substantive_section_path_over_placeholder(
            state,
            target_norm=target_norm,
            target_chapter=target_chapter,
            target_part=target_part,
            raw_path=raw_path,
        )
        if raw_path is None and target_chapter is None and target_part is None:
            raw_path = _unique_global_section_path(state, target_norm)
        if raw_path is None and target_chapter is not None and target_part is None:
            # Cross-chapter/root-level unique global fallback for non-INSERT ops.
            # Finnish amendments sometimes group sections under a chapter heading
            # that differs from where the section lives in the live statute.
            is_non_insert = rop is None or rop.resolved_action_type != "INSERT"
            if is_non_insert:
                raw_path = _unique_global_section_path(state, target_norm)
        if raw_path is not None and migration_ledger is not None:
            migrated = migration_ledger.current_address_with_prefix_migrations(
                LegalAddress(path=tuple(raw_path)),
                not_before=hint_op_effective,
            )
            migrated_path = migrated.path
            if _tops.resolve(state.ir, migrated_path) is not None:
                return migrated_path
        return raw_path
    return None


def _unique_global_section_path(
    state: ReplayState,
    label: str,
) -> tuple[tuple[str, str], ...] | None:
    """Return a globally unique live section path for ``label``, if one exists."""
    idx = state.provision_index
    raw_path = _tops.find(state.ir, "section", label, label_index=idx)
    if raw_path is None:
        return None
    label_norm = normalized_label_key(label)
    if len(idx.get(("section", label_norm), [])) != 1:
        return None
    return raw_path

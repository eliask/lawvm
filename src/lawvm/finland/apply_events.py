"""Mutation-event helpers for Finland apply.

This module isolates the apply-time observability surface from the execution
helpers so `apply.py` can shrink without changing the public compatibility
surface.  The helpers here are pure formatting/recording utilities: they do
not read live replay state or mutate IR.
"""

from __future__ import annotations

from typing import List, Optional

from lawvm.core.mutation_accounting import (
    MutationAccountingResult as ApplyMutationAccountingResult,
    MutationInvariantReport as ApplyMutationInvariantReport,
    analyze_mutation_accounting as analyze_apply_mutation_accounting,
    analyze_mutation_invariant_reports as analyze_apply_mutation_invariant_reports,
    build_mutation_invariant_reports as build_apply_mutation_invariant_reports,
    check_mutation_accounting as check_apply_mutation_accounting,
    check_mutation_invariant_reports as check_apply_mutation_invariant_reports,
)
from lawvm.core.mutation_boundary import RenumberedTreePaths, TreePath, TreePaths
from lawvm.core.mutation_events import (
    DeclaredMutationAllowance,
    MutationEvent as ApplyMutationEvent,
)
from lawvm.core.tree_ops import Path
from lawvm.finland.ops import AmendmentOp, ResolvedOp


def _path_to_tuple(path: Path | None) -> TreePath | None:
    if path is None:
        return None
    return tuple((str(kind), str(label)) for kind, label in path)


def _resolved_target_path_for_event(
    op: AmendmentOp,
    sec_path: Path | None,
) -> TreePath | None:
    if sec_path is None:
        return None
    resolved: TreePath = tuple(sec_path)
    if op.target_paragraph is not None:
        resolved = resolved + (("subsection", str(op.target_paragraph)),)
    if op.target_item is not None:
        resolved = resolved + (("paragraph", str(op.target_item)),)
    if op.target_special is not None:
        resolved = resolved + (("special", str(op.target_special)),)
    return _path_to_tuple(resolved)


def _resolved_target_path_for_rop_event(
    rop: ResolvedOp,
    sec_path: Path | None,
) -> TreePath | None:
    """Resolve mutation-event target identity from late-waist fields.

    When the resolver bound a concrete section node (``sec_path``), derive the
    event's declared path from that *resolved* location rather than the op's
    nominal compiled address.  The nominal address can carry a stale/unqualified
    chapter (flat-numbered statutes, renumber, hoist) while the bound node lives
    under a different chapter; preferring ``sec_path`` keeps the declared path
    fully rooted at the node the write actually landed on.  Any subsection/item/
    special suffix the nominal address declares below the section is grafted onto
    the resolved section path so the event path stays fully chapter-qualified.
    """
    if sec_path is not None:
        return _rooted_target_path_from_resolved_section(rop, sec_path)
    return _target_address_path_for_rop_event(rop)


def _rooted_target_path_from_resolved_section(
    rop: ResolvedOp,
    sec_path: Path,
) -> TreePath | None:
    """Graft the nominal address' below-section suffix onto the resolved section path."""
    resolved = _path_to_tuple(sec_path)
    if resolved is None:
        return None
    return resolved + _below_section_suffix_for_rop(rop)


def _below_section_suffix_for_rop(rop: ResolvedOp) -> TreePath:
    """Return the subsection/item/special steps below the section level.

    Sourced from the op's resolved target address so the suffix matches what the
    helper resolved against, but stripped of everything up to and including the
    section step (the section identity comes from the resolved ``sec_path``).
    """
    address = rop.resolved_target_address
    if address is None or not address.path:
        return ()
    path = tuple((str(kind), str(label)) for kind, label in address.path)
    section_indices = [index for index, (kind, _label) in enumerate(path) if kind == "section"]
    if not section_indices:
        return ()
    return path[section_indices[-1] + 1 :]


def _target_address_path_for_rop_event(
    rop: ResolvedOp,
    path_hint: Path | None = None,
) -> TreePath | None:
    """Resolve mutation-event identity from the effective ResolvedOp target address."""
    address = rop.resolved_target_address
    if address is not None and address.path:
        return _path_to_tuple(address.path)
    return _path_to_tuple(path_hint)


def _emit_apply_mutation_event(
    mutation_events_out: Optional[List[ApplyMutationEvent]],
    *,
    op: AmendmentOp,
    helper: str,
    outcome: str,
    resolved_target_path: TreePath | None = None,
    parent_path: TreePath | None = None,
    declared_allowances: tuple[DeclaredMutationAllowance, ...] = (),
    consumed_paths: TreePaths = (),
    created_paths: TreePaths = (),
    removed_paths: TreePaths = (),
    replaced_paths: TreePaths = (),
    renumbered_paths: RenumberedTreePaths = (),
    placeholder_created_paths: TreePaths = (),
    placeholder_consumed_paths: TreePaths = (),
    used_fallback_tags: tuple[str, ...] = (),
    failure_reason: str = "",
    reason_code: str = "",
) -> None:
    if mutation_events_out is None:
        return
    mutation_events_out.append(
        ApplyMutationEvent(
            op_id=op.op_id,
            source_statute=op.source_statute,
            action=op.op_type.lower(),
            helper=helper,
            outcome=outcome,
            resolved_target_path=resolved_target_path,
            parent_path=parent_path,
            declared_allowances=declared_allowances,
            consumed_paths=consumed_paths,
            created_paths=created_paths,
            removed_paths=removed_paths,
            replaced_paths=replaced_paths,
            renumbered_paths=renumbered_paths,
            placeholder_created_paths=placeholder_created_paths,
            placeholder_consumed_paths=placeholder_consumed_paths,
            used_fallback_tags=used_fallback_tags,
            failure_reason=failure_reason,
            reason_code=reason_code,
        )
    )


def _emit_apply_mutation_event_for_rop(
    mutation_events_out: Optional[List[ApplyMutationEvent]],
    *,
    rop: ResolvedOp,
    helper: str,
    outcome: str,
    resolved_target_path: TreePath | None = None,
    parent_path: TreePath | None = None,
    declared_allowances: tuple[DeclaredMutationAllowance, ...] = (),
    consumed_paths: TreePaths = (),
    created_paths: TreePaths = (),
    removed_paths: TreePaths = (),
    replaced_paths: TreePaths = (),
    renumbered_paths: RenumberedTreePaths = (),
    placeholder_created_paths: TreePaths = (),
    placeholder_consumed_paths: TreePaths = (),
    used_fallback_tags: tuple[str, ...] = (),
    failure_reason: str = "",
    reason_code: str = "",
) -> None:
    """Emit a mutation event from late-waist fields without consulting AmendmentOp."""
    if mutation_events_out is None:
        return
    effective_declared_allowances = declared_allowances
    if not effective_declared_allowances and rop.uses_uncovered_body_recovery:
        allowed_paths = tuple(
            path
            for path in (resolved_target_path, parent_path)
            if path
        )
        effective_declared_allowances = (
            DeclaredMutationAllowance(
                kind="recovery",
                paths=allowed_paths,
                rule_id="uncovered_body_recovery",
            ),
        )
    mutation_events_out.append(
        ApplyMutationEvent(
            op_id=rop.op_id or "",
            source_statute=rop.resolved_source_statute,
            action=rop.resolved_action_type.lower(),
            helper=helper,
            outcome=outcome,
            resolved_target_path=resolved_target_path,
            parent_path=parent_path,
            declared_allowances=effective_declared_allowances,
            consumed_paths=consumed_paths,
            created_paths=created_paths,
            removed_paths=removed_paths,
            replaced_paths=replaced_paths,
            renumbered_paths=renumbered_paths,
            placeholder_created_paths=placeholder_created_paths,
            placeholder_consumed_paths=placeholder_consumed_paths,
            used_fallback_tags=used_fallback_tags,
            failure_reason=failure_reason,
            reason_code=reason_code,
        )
    )


def _emit_legacy_dispatch_fallback_event(
    mutation_events_out: Optional[List[ApplyMutationEvent]],
    *,
    rop: ResolvedOp,
    helper: str,
    reason_tag: str,
    failure_reason: str,
    reason_code: str = "",
    path_hint: Path | None = None,
) -> None:
    """Record that typed apply fell back to legacy field-based dispatch."""
    _emit_apply_mutation_event_for_rop(
        mutation_events_out,
        rop=rop,
        helper=helper,
        outcome="skipped",
        resolved_target_path=_target_address_path_for_rop_event(rop, path_hint),
        used_fallback_tags=("APPLY.LEGACY_DISPATCH_FALLBACK", reason_tag),
        failure_reason=failure_reason,
        reason_code=reason_code,
    )


__all__ = [
    "ApplyMutationEvent",
    "ApplyMutationAccountingResult",
    "ApplyMutationInvariantReport",
    "DeclaredMutationAllowance",
    "TreePath",
    "TreePaths",
    "RenumberedTreePaths",
    "build_apply_mutation_invariant_reports",
    "analyze_apply_mutation_invariant_reports",
    "analyze_apply_mutation_accounting",
    "check_apply_mutation_invariant_reports",
    "check_apply_mutation_accounting",
    "_path_to_tuple",
    "_resolved_target_path_for_event",
    "_resolved_target_path_for_rop_event",
    "_rooted_target_path_from_resolved_section",
    "_below_section_suffix_for_rop",
    "_target_address_path_for_rop_event",
    "_emit_apply_mutation_event",
    "_emit_apply_mutation_event_for_rop",
    "_emit_legacy_dispatch_fallback_event",
]

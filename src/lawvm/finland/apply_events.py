"""Mutation-event helpers for Finland apply.

This module isolates the apply-time observability surface from the execution
helpers so `apply.py` can shrink without changing the public compatibility
surface.  The helpers here are pure formatting/recording utilities: they do
not read live replay state or mutate IR.
"""

from __future__ import annotations

from typing import Any, List, Optional

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
from lawvm.core.write_receipt import WriteReceipt
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
    if op.target_cols.target_paragraph is not None:
        resolved = resolved + (("subsection", str(op.target_cols.target_paragraph)),)
    if op.target_cols.target_item is not None:
        resolved = resolved + (("paragraph", str(op.target_cols.target_item)),)
    if op.target_cols.target_special is not None:
        resolved = resolved + (("special", str(op.target_cols.target_special)),)
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


def landed_section_event_path(
    result_state,
    *,
    section_label: str | None,
    chapter_label: str | None,
    part_label: str | None,
) -> TreePath | None:
    """Resolve a section in the post-apply tree, for mutation-event declaration.

    The nominal compiled address can disagree with the live tree about
    container shape: a "2 luku 69a §" citation carries no part step, and an
    address enriched from the consolidated base can carry a part step the live
    tree does not have (yet). Mutation events must declare the path the write
    actually landed on, so resolve in the result tree, progressively dropping
    scope when the scoped lookup finds nothing (a part/chapter label that does
    not exist in the live tree). A lookup that binds a different same-labeled
    node than the write touched cannot mask anything: the observed-vs-declared
    cross-check still fires on the unexplained observed path.
    """
    if not section_label:
        return None
    scopes: list[tuple[str | None, str | None]] = [(chapter_label, part_label)]
    if part_label:
        scopes.append((chapter_label, None))
    if chapter_label:
        scopes.append((None, None))
    for chapter_scope, part_scope in scopes:
        path = result_state.find_section_path(section_label, chapter_scope, part_scope)
        if path is not None:
            return _path_to_tuple(path)
    return None


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


def _emit_apply_mutation_event_from_receipt(
    mutation_events_out: Optional[List[ApplyMutationEvent]],
    *,
    receipt: WriteReceipt,
    outcome: str,
    rop: ResolvedOp | None = None,
    op: AmendmentOp | None = None,
    used_fallback_tags: tuple[str, ...] = (),
) -> None:
    """Derive the op's mutation event from its WriteReceipt.

    Contract §4: ApplyMutationEvent rows are DERIVED from the receipt, not
    assembled independently — one producer, many projections. The declared
    paths therefore reflect the landed footprint the helper recorded at the
    write, and every named recovery/migration rule on the receipt becomes a
    declared allowance covering that footprint.
    """
    if rop is None and op is None:
        raise ValueError("receipt-derived mutation event requires rop or op identity")
    footprint = receipt.declared_footprint
    declared_allowances = tuple(
        DeclaredMutationAllowance(kind="recovery_path", paths=footprint, rule_id=rule_id)
        for rule_id in receipt.recovery_rule_ids
    ) + tuple(
        DeclaredMutationAllowance(kind="migration_path", paths=footprint, rule_id=rule_id)
        for rule_id in receipt.migration_rule_ids
    )
    landed = receipt.landed_primary_path
    shared_fields: dict[str, Any] = dict(
        helper=receipt.helper,
        outcome=outcome,
        resolved_target_path=landed,
        parent_path=(landed[:-1] if landed else None),
        declared_allowances=declared_allowances,
        consumed_paths=receipt.consumed_paths,
        created_paths=receipt.created_paths,
        removed_paths=receipt.removed_paths,
        replaced_paths=receipt.replaced_paths,
        renumbered_paths=receipt.renumbered_paths,
        placeholder_created_paths=receipt.placeholder_created_paths,
        placeholder_consumed_paths=receipt.placeholder_consumed_paths,
        used_fallback_tags=used_fallback_tags,
    )
    if rop is not None:
        _emit_apply_mutation_event_for_rop(mutation_events_out, rop=rop, **shared_fields)
        return
    assert op is not None
    _emit_apply_mutation_event(mutation_events_out, op=op, **shared_fields)


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
    "_emit_apply_mutation_event_from_receipt",
]

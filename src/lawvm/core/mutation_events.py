"""Shared mutation-event carriers.

Mutation events are replay/apply observations recorded at mutation sites. They
are not themselves legal operations; they describe what a frontend helper
touched so boundary checks and evidence code can reason about target ownership.
"""

from __future__ import annotations

from dataclasses import dataclass

from lawvm.core.mutation_boundary import (
    RenumberedTreePaths,
    TreePath,
    TreePaths,
    dedupe_tree_paths,
    partition_changed_paths,
    path_has_prefix,
)


@dataclass(frozen=True)
class DeclaredMutationAllowance:
    kind: str
    paths: TreePaths = ()
    rule_id: str = ""
    note: str = ""


@dataclass(frozen=True)
class MutationEvent:
    op_id: str
    source_statute: str
    action: str
    helper: str
    outcome: str
    resolved_target_path: TreePath | None = None
    parent_path: TreePath | None = None
    declared_allowances: tuple[DeclaredMutationAllowance, ...] = ()
    consumed_paths: TreePaths = ()
    created_paths: TreePaths = ()
    removed_paths: TreePaths = ()
    replaced_paths: TreePaths = ()
    renumbered_paths: RenumberedTreePaths = ()
    placeholder_created_paths: TreePaths = ()
    placeholder_consumed_paths: TreePaths = ()
    used_fallback_tags: tuple[str, ...] = ()
    failure_reason: str = ""
    reason_code: str = ""


@dataclass(frozen=True)
class MutationEventPathSetReport:
    """Path-set accounting for one mutation event against declared regions."""

    op_id: str
    helper: str
    outcome: str
    touched_paths: TreePaths = ()
    changed_paths: TreePaths = ()
    allowed_effect_region_paths: TreePaths = ()
    declared_allowance_paths: TreePaths = ()
    declared_recovery_paths: TreePaths = ()
    declared_recovery_rule_ids: tuple[str, ...] = ()
    declared_migration_paths: TreePaths = ()
    declared_migration_rule_ids: tuple[str, ...] = ()
    permitted_paths: TreePaths = ()
    covered_changed_paths: TreePaths = ()
    unexplained_changed_paths: TreePaths = ()
    allowed_non_target_paths: TreePaths = ()
    matched_allowance_rule_ids: tuple[str, ...] = ()
    path_set_invariant_holds: bool = True


def mutation_event_touched_paths(event: MutationEvent) -> TreePaths:
    """Return all paths touched by one mutation event in first-seen order."""
    touched: list[TreePath] = []
    touched.extend(event.consumed_paths)
    touched.extend(event.created_paths)
    touched.extend(event.removed_paths)
    touched.extend(event.replaced_paths)
    touched.extend(event.placeholder_created_paths)
    touched.extend(event.placeholder_consumed_paths)
    for old_path, new_path in event.renumbered_paths:
        touched.append(old_path)
        touched.append(new_path)
    return dedupe_tree_paths(touched)


def mutation_event_declared_allowance_paths(event: MutationEvent) -> TreePaths:
    """Return all declared non-target allowance paths for one event."""
    return dedupe_tree_paths(path for allowance in event.declared_allowances for path in allowance.paths if path)


def mutation_event_allowance_paths_by_kind(
    event: MutationEvent,
    *kinds: str,
) -> TreePaths:
    """Return declared allowance paths matching any of the given kind labels."""

    wanted = set(kinds)
    return dedupe_tree_paths(
        path
        for allowance in event.declared_allowances
        if allowance.kind in wanted
        for path in allowance.paths
        if path
    )


def mutation_event_allowance_rule_ids_by_kind(
    event: MutationEvent,
    *kinds: str,
) -> tuple[str, ...]:
    """Return first-seen declared allowance rule IDs matching kind labels."""

    wanted = set(kinds)
    rule_ids: list[str] = []
    seen: set[str] = set()
    for allowance in event.declared_allowances:
        if allowance.kind not in wanted:
            continue
        rule_id = str(allowance.rule_id or "").strip()
        if not rule_id or rule_id in seen:
            continue
        seen.add(rule_id)
        rule_ids.append(rule_id)
    return tuple(rule_ids)


def mutation_event_matching_allowance_rule_ids(
    event: MutationEvent,
    path: TreePath,
) -> tuple[str, ...]:
    """Return rule IDs whose declared allowance covers *path*."""

    matched: list[str] = []
    seen: set[str] = set()
    for allowance in event.declared_allowances:
        rule_id = str(allowance.rule_id or "").strip()
        if not rule_id or rule_id in seen:
            continue
        if any(path_has_prefix(path, (allowed_path,)) for allowed_path in allowance.paths if allowed_path):
            seen.add(rule_id)
            matched.append(rule_id)
    return tuple(matched)


def build_mutation_event_path_set_report(
    event: MutationEvent,
    allowed_effect_region_paths: TreePaths,
) -> MutationEventPathSetReport:
    """Partition one event's touched paths through target, recovery, and migration regions."""

    touched_paths = mutation_event_touched_paths(event)
    allowed_roots = dedupe_tree_paths(allowed_effect_region_paths)
    declared_allowance_paths = mutation_event_declared_allowance_paths(event)
    declared_recovery_paths = mutation_event_allowance_paths_by_kind(event, "recovery", "recovery_path")
    declared_recovery_rule_ids = mutation_event_allowance_rule_ids_by_kind(event, "recovery", "recovery_path")
    declared_migration_paths = mutation_event_allowance_paths_by_kind(event, "migration", "migration_path")
    declared_migration_rule_ids = mutation_event_allowance_rule_ids_by_kind(event, "migration", "migration_path")
    permitted_paths = dedupe_tree_paths(
        (
            *allowed_roots,
            *declared_recovery_paths,
            *declared_migration_paths,
        )
    )
    partition = partition_changed_paths(touched_paths, permitted_paths)
    allowed_non_target_paths = tuple(
        path
        for path in partition.covered_changed_paths
        if not path_has_prefix(path, allowed_roots)
    )
    matched_rule_ids: list[str] = []
    seen_rule_ids: set[str] = set()
    for path in allowed_non_target_paths:
        for rule_id in mutation_event_matching_allowance_rule_ids(event, path):
            if rule_id in seen_rule_ids:
                continue
            seen_rule_ids.add(rule_id)
            matched_rule_ids.append(rule_id)
    return MutationEventPathSetReport(
        op_id=event.op_id,
        helper=event.helper,
        outcome=event.outcome,
        touched_paths=touched_paths,
        changed_paths=touched_paths,
        allowed_effect_region_paths=allowed_roots,
        declared_allowance_paths=declared_allowance_paths,
        declared_recovery_paths=declared_recovery_paths,
        declared_recovery_rule_ids=declared_recovery_rule_ids,
        declared_migration_paths=declared_migration_paths,
        declared_migration_rule_ids=declared_migration_rule_ids,
        permitted_paths=permitted_paths,
        covered_changed_paths=partition.covered_changed_paths,
        unexplained_changed_paths=partition.unexplained_changed_paths,
        allowed_non_target_paths=allowed_non_target_paths,
        matched_allowance_rule_ids=tuple(matched_rule_ids),
        path_set_invariant_holds=not partition.unexplained_changed_paths,
    )

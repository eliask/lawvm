"""Shared mutation-event carriers.

Mutation events are replay/apply observations recorded at mutation sites. They
are not themselves legal operations; they describe what a frontend helper
touched so boundary checks and evidence code can reason about target ownership.
"""

from __future__ import annotations

from dataclasses import dataclass

from lawvm.core.mutation_boundary import TreePath, dedupe_tree_paths


@dataclass(frozen=True)
class DeclaredMutationAllowance:
    kind: str
    paths: tuple[TreePath, ...] = ()
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
    consumed_paths: tuple[TreePath, ...] = ()
    created_paths: tuple[TreePath, ...] = ()
    removed_paths: tuple[TreePath, ...] = ()
    replaced_paths: tuple[TreePath, ...] = ()
    renumbered_paths: tuple[tuple[TreePath, TreePath], ...] = ()
    placeholder_created_paths: tuple[TreePath, ...] = ()
    placeholder_consumed_paths: tuple[TreePath, ...] = ()
    used_fallback_tags: tuple[str, ...] = ()
    failure_reason: str = ""
    reason_code: str = ""


def mutation_event_touched_paths(event: MutationEvent) -> tuple[TreePath, ...]:
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


def mutation_event_declared_allowance_paths(event: MutationEvent) -> tuple[TreePath, ...]:
    """Return all declared non-target allowance paths for one event."""
    return dedupe_tree_paths(path for allowance in event.declared_allowances for path in allowance.paths if path)

"""Reusable invariant profiles for replay/product surfaces."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from lawvm.core.tree_ops import TreeInvariantKind, TreeInvariantNode, TreeInvariantViolation
from lawvm.core.tree_ops import iter_tree_invariant_violations


@dataclass(frozen=True, slots=True)
class TreeInvariantProfile:
    """Named tree-invariant family selection for one diagnostic surface."""

    surface: str
    families: tuple[TreeInvariantKind, ...]

    def __post_init__(self) -> None:
        if not self.surface:
            raise ValueError("TreeInvariantProfile.surface must be non-empty")
        if not self.families:
            raise ValueError("TreeInvariantProfile.families must be non-empty")


def collect_tree_invariant_violations(
    tree: TreeInvariantNode,
    profile: TreeInvariantProfile,
) -> tuple[TreeInvariantViolation, ...]:
    """Collect typed tree invariant violations selected by *profile*."""
    return tuple(iter_tree_invariant_violations(tree, families=profile.families))


def collect_tree_invariant_messages(
    tree: TreeInvariantNode,
    profile: TreeInvariantProfile,
) -> tuple[str, ...]:
    """Collect legacy-prefixed invariant messages for one product surface."""
    return tuple(
        f"{profile.surface}:{violation.message}"
        for violation in collect_tree_invariant_violations(tree, profile)
    )


def collect_tree_invariant_dicts(
    tree: TreeInvariantNode,
    profile: TreeInvariantProfile,
) -> tuple[dict[str, object], ...]:
    """Collect typed invariant dictionaries for JSON/report surfaces."""
    return tuple(violation.to_dict() for violation in collect_tree_invariant_violations(tree, profile))


def collect_tree_invariant_messages_for_profiles(
    tree: TreeInvariantNode,
    profiles: Sequence[TreeInvariantProfile],
) -> tuple[str, ...]:
    """Collect legacy-prefixed invariant messages for multiple surfaces."""
    messages: list[str] = []
    for profile in profiles:
        messages.extend(collect_tree_invariant_messages(tree, profile))
    return tuple(messages)


__all__ = [
    "TreeInvariantProfile",
    "collect_tree_invariant_dicts",
    "collect_tree_invariant_messages",
    "collect_tree_invariant_messages_for_profiles",
    "collect_tree_invariant_violations",
]

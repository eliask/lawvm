"""Reusable invariant profiles for replay/product surfaces."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from lawvm.core.tree_ops import TreeInvariantKind, TreeInvariantNode, TreeInvariantViolation
from lawvm.core.tree_ops import iter_tree_invariant_violations

CORE_STRUCTURAL_PRODUCT_STRICT_FAMILIES: tuple[TreeInvariantKind, ...] = (
    "duplicate_label",
    "unexpected_child_kind",
)
CORE_STRUCTURAL_PRODUCT_HIERARCHICAL_FAMILIES: tuple[TreeInvariantKind, ...] = (
    *CORE_STRUCTURAL_PRODUCT_STRICT_FAMILIES,
    "mixed_hierarchy_child",
)
CORE_REPLAY_DELTA_MINIMAL_FAMILIES: tuple[TreeInvariantKind, ...] = (
    "duplicate_label",
    "sort_order",
)


@dataclass(frozen=True, slots=True)
class TreeInvariantProfile:
    """Named tree-invariant family selection for one diagnostic surface."""

    surface: str
    families: tuple[TreeInvariantKind, ...]
    profile_id: str = "custom"

    def __post_init__(self) -> None:
        if not self.surface:
            raise ValueError("TreeInvariantProfile.surface must be non-empty")
        if not self.families:
            raise ValueError("TreeInvariantProfile.families must be non-empty")
        if not self.profile_id:
            raise ValueError("TreeInvariantProfile.profile_id must be non-empty")


def structural_product_strict_profile(surface: str) -> TreeInvariantProfile:
    """Return the core product-tree profile for structural hard errors."""
    return TreeInvariantProfile(
        surface=surface,
        families=CORE_STRUCTURAL_PRODUCT_STRICT_FAMILIES,
        profile_id="core_structural_product_strict",
    )


def structural_product_hierarchical_profile(surface: str) -> TreeInvariantProfile:
    """Return the core product-tree profile that also flags mixed hierarchy."""
    return TreeInvariantProfile(
        surface=surface,
        families=CORE_STRUCTURAL_PRODUCT_HIERARCHICAL_FAMILIES,
        profile_id="core_structural_product_hierarchical",
    )


def replay_delta_minimal_profile(surface: str) -> TreeInvariantProfile:
    """Return the core replay-delta profile for duplicate/order drift."""
    return TreeInvariantProfile(
        surface=surface,
        families=CORE_REPLAY_DELTA_MINIMAL_FAMILIES,
        profile_id="core_replay_delta_minimal",
    )


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
    rows: list[dict[str, object]] = []
    for violation in collect_tree_invariant_violations(tree, profile):
        row = violation.to_dict()
        row["surface"] = profile.surface
        row["profile_id"] = profile.profile_id
        rows.append(row)
    return tuple(rows)


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
    "CORE_REPLAY_DELTA_MINIMAL_FAMILIES",
    "CORE_STRUCTURAL_PRODUCT_HIERARCHICAL_FAMILIES",
    "CORE_STRUCTURAL_PRODUCT_STRICT_FAMILIES",
    "TreeInvariantProfile",
    "collect_tree_invariant_dicts",
    "collect_tree_invariant_messages",
    "collect_tree_invariant_messages_for_profiles",
    "collect_tree_invariant_violations",
    "replay_delta_minimal_profile",
    "structural_product_hierarchical_profile",
    "structural_product_strict_profile",
]

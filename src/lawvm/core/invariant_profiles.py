"""Reusable invariant profiles for replay/product surfaces."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

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
CORE_STRUCTURAL_TREE_ALL_FAMILIES: tuple[TreeInvariantKind, ...] = (
    "duplicate_label",
    "normalized_duplicate_label",
    "sort_order",
    "unexpected_child_kind",
    "mixed_hierarchy_child",
)
MutationAccountingMode = Literal["off", "passive", "warning", "hard"]
ReplayTransitionDetectorName = Literal[
    "descendant_sibling_loss",
    "same_source_descendant_snapshot_shadow",
]
TimelineInvariantFamily = Literal[
    "temporal_overlap",
    "temporary_overlay",
    "expiry_chain",
    "replay_timeline",
]
ReplayWarningFamily = Literal[
    "text_duplication",
    "flattened_sublist_family",
    "label_sequence_gap",
]
LocalPolicyMode = Literal["none", "frontend_required"]

_VALID_MUTATION_ACCOUNTING_MODES = frozenset(MutationAccountingMode.__args__)
_VALID_TRANSITION_DETECTORS = frozenset(ReplayTransitionDetectorName.__args__)
_VALID_TIMELINE_INVARIANTS = frozenset(TimelineInvariantFamily.__args__)
_VALID_WARNING_FAMILIES = frozenset(ReplayWarningFamily.__args__)
_VALID_LOCAL_POLICY_MODES = frozenset(LocalPolicyMode.__args__)


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


@dataclass(frozen=True, slots=True)
class ReplayInvariantProfile:
    """Named opt-in invariant/lint family set for a replay diagnostic surface.

    The profile is declarative only: it selects shared families and records
    whether local allowance/classification policy is required. It does not make
    those families authoritative or execute them by itself.
    """

    profile_id: str
    tree_profiles: tuple[TreeInvariantProfile, ...] = ()
    mutation_accounting: MutationAccountingMode = "off"
    transition_detectors: tuple[ReplayTransitionDetectorName, ...] = ()
    timeline_invariants: tuple[TimelineInvariantFamily, ...] = ()
    warnings: tuple[ReplayWarningFamily, ...] = ()
    local_allowance_policy: LocalPolicyMode = "none"
    local_classifier_policy: LocalPolicyMode = "none"
    safe_default: str = "profile_is_declarative_not_replay_authorization"

    def __post_init__(self) -> None:
        if not self.profile_id:
            raise ValueError("ReplayInvariantProfile.profile_id must be non-empty")
        if not all(isinstance(profile, TreeInvariantProfile) for profile in self.tree_profiles):
            raise ValueError("ReplayInvariantProfile.tree_profiles must contain TreeInvariantProfile records")
        if self.mutation_accounting not in _VALID_MUTATION_ACCOUNTING_MODES:
            raise ValueError("ReplayInvariantProfile.mutation_accounting is invalid")
        _validate_literal_tuple(
            "ReplayInvariantProfile.transition_detectors",
            self.transition_detectors,
            _VALID_TRANSITION_DETECTORS,
        )
        _validate_literal_tuple(
            "ReplayInvariantProfile.timeline_invariants",
            self.timeline_invariants,
            _VALID_TIMELINE_INVARIANTS,
        )
        _validate_literal_tuple(
            "ReplayInvariantProfile.warnings",
            self.warnings,
            _VALID_WARNING_FAMILIES,
        )
        if self.local_allowance_policy not in _VALID_LOCAL_POLICY_MODES:
            raise ValueError("ReplayInvariantProfile.local_allowance_policy is invalid")
        if self.local_classifier_policy not in _VALID_LOCAL_POLICY_MODES:
            raise ValueError("ReplayInvariantProfile.local_classifier_policy is invalid")
        if not self.safe_default:
            raise ValueError("ReplayInvariantProfile.safe_default must be non-empty")

    def to_dict(self) -> dict[str, object]:
        """Project the profile to a stable report/config row."""
        return {
            "profile_id": self.profile_id,
            "tree_profiles": tuple(
                {
                    "surface": profile.surface,
                    "profile_id": profile.profile_id,
                    "families": profile.families,
                }
                for profile in self.tree_profiles
            ),
            "mutation_accounting": self.mutation_accounting,
            "transition_detectors": self.transition_detectors,
            "timeline_invariants": self.timeline_invariants,
            "warnings": self.warnings,
            "local_allowance_policy": self.local_allowance_policy,
            "local_classifier_policy": self.local_classifier_policy,
            "safe_default": self.safe_default,
            "replay_authorization_claims": False,
        }


def _validate_literal_tuple(field_name: str, values: tuple[str, ...], allowed: frozenset[str]) -> None:
    if not isinstance(values, tuple):
        raise ValueError(f"{field_name} must be a tuple")
    bad = tuple(value for value in values if value not in allowed)
    if bad:
        raise ValueError(f"{field_name} contains unsupported values: {bad!r}")


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


def structural_tree_all_profile(surface: str) -> TreeInvariantProfile:
    """Return the core profile matching the default tree-invariant scanner."""
    return TreeInvariantProfile(
        surface=surface,
        families=CORE_STRUCTURAL_TREE_ALL_FAMILIES,
        profile_id="core_structural_tree_all",
    )


def replay_invariant_profile(
    *,
    profile_id: str,
    tree_profiles: Sequence[TreeInvariantProfile] = (),
    mutation_accounting: MutationAccountingMode = "off",
    transition_detectors: Sequence[ReplayTransitionDetectorName] = (),
    timeline_invariants: Sequence[TimelineInvariantFamily] = (),
    warnings: Sequence[ReplayWarningFamily] = (),
    local_allowance_policy: LocalPolicyMode = "none",
    local_classifier_policy: LocalPolicyMode = "none",
    safe_default: str = "profile_is_declarative_not_replay_authorization",
) -> ReplayInvariantProfile:
    """Build a replay invariant profile from sequence inputs."""
    return ReplayInvariantProfile(
        profile_id=profile_id,
        tree_profiles=tuple(tree_profiles),
        mutation_accounting=mutation_accounting,
        transition_detectors=tuple(transition_detectors),
        timeline_invariants=tuple(timeline_invariants),
        warnings=tuple(warnings),
        local_allowance_policy=local_allowance_policy,
        local_classifier_policy=local_classifier_policy,
        safe_default=safe_default,
    )


def core_replay_strict_profile(surface: str) -> ReplayInvariantProfile:
    """Return the shared strict replay profile as an opt-in declaration."""
    return replay_invariant_profile(
        profile_id="core_replay_strict_v1",
        tree_profiles=(replay_delta_minimal_profile(surface),),
        mutation_accounting="hard",
        transition_detectors=(
            "descendant_sibling_loss",
            "same_source_descendant_snapshot_shadow",
        ),
        timeline_invariants=(
            "temporal_overlap",
            "temporary_overlay",
            "expiry_chain",
            "replay_timeline",
        ),
        warnings=("text_duplication", "flattened_sublist_family", "label_sequence_gap"),
        local_allowance_policy="frontend_required",
        local_classifier_policy="frontend_required",
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


def project_tree_invariant_dicts(
    violations: Sequence[TreeInvariantViolation],
    profile: TreeInvariantProfile,
) -> tuple[dict[str, object], ...]:
    """Project typed invariant violations with profile/surface metadata."""
    rows: list[dict[str, object]] = []
    for violation in violations:
        row = violation.to_dict()
        row["surface"] = profile.surface
        row["profile_id"] = profile.profile_id
        rows.append(row)
    return tuple(rows)


def collect_tree_invariant_dicts(
    tree: TreeInvariantNode,
    profile: TreeInvariantProfile,
) -> tuple[dict[str, object], ...]:
    """Collect typed invariant dictionaries for JSON/report surfaces."""
    return project_tree_invariant_dicts(
        collect_tree_invariant_violations(tree, profile),
        profile,
    )


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
    "CORE_STRUCTURAL_TREE_ALL_FAMILIES",
    "LocalPolicyMode",
    "MutationAccountingMode",
    "ReplayInvariantProfile",
    "ReplayTransitionDetectorName",
    "ReplayWarningFamily",
    "TimelineInvariantFamily",
    "TreeInvariantProfile",
    "collect_tree_invariant_dicts",
    "collect_tree_invariant_messages",
    "collect_tree_invariant_messages_for_profiles",
    "collect_tree_invariant_violations",
    "core_replay_strict_profile",
    "project_tree_invariant_dicts",
    "replay_invariant_profile",
    "replay_delta_minimal_profile",
    "structural_product_hierarchical_profile",
    "structural_product_strict_profile",
    "structural_tree_all_profile",
]

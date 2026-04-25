"""Finland jurisdiction profile for LawVM.

Unified bundle exposing the Finnish legal ontology, label algebra,
temporal activation defaults, and identity/renumber policies through
one cohesive API.

This is a thin integration layer over:
- ``lawvm.finland.ontology`` -- unit ontology, hierarchy, facets
- ``lawvm.finland.labels`` -- label parsing, rendering, sorting, validation
- ``lawvm.core.temporal`` -- activation rules and temporal status

Design invariants
-----------------
- Frozen dataclasses throughout.  No mutation.
- One canonical ``FINLAND_PROFILE`` instance for downstream consumption.
- Does NOT modify ontology.py or labels.py -- only imports from them.

Usage
-----
    from lawvm.finland.profile import FINLAND_PROFILE, FinlandProfile

    profile = FINLAND_PROFILE
    assert profile.default_activation.kind == "immediate"
    assert profile.identity_policy("section").preserves_identity_on_renumber
    assert profile.is_well_formed_amendment_target("section", "5")
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from lawvm.core.compile_result import ActivationRule
from lawvm.finland.labels import (
    AlphaSequence,
    AnyFinlandLabel,
    FinlandLabel,
    ImplicitOrdinal,
    InsertableArabic,
    RomanOrdinal,
    SymbolicLabel,
    is_valid_label_for_kind,
    label_sort_key,
    normalize_raw_label,
    parse_label,
    render_label,
)
from lawvm.finland.ontology import (
    ALL_FACET_KINDS,
    ALL_UNIT_KINDS,
    HIERARCHY_ORDER,
    UNIT_ONTOLOGY,
    UnitOntologyEntry,
    allowed_label_series,
    can_carry_facet,
    hierarchy_depth,
    is_amendable,
    is_legal_unit,
    parent_kinds,
)


# ---------------------------------------------------------------------------
# Identity policy: which unit types preserve identity across renumber
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class IdentityPolicy:
    """Identity and renumber policy for a Finnish legal unit kind.

    Parameters
    ----------
    kind
        The unit kind this policy applies to.
    identity_class
        How the unit is identified: "stable_label" (explicit printed label)
        or "implicit_ordinal" (positional, no printed label).
    preserves_identity_on_renumber
        True if the unit keeps its legal identity when the container is
        renumbered.  ``stable_label`` units preserve identity (the label IS
        the identity); ``implicit_ordinal`` units do NOT (inserting before
        them changes their ordinal, hence their identity).
    insertion_creates_suffix
        True if inserting a new sibling creates a suffixed label (e.g. "5 a")
        rather than shifting existing ordinals.  This is True for
        ``stable_label`` units with ``suffix`` insertion policy.
    insertion_shifts_later
        True if inserting a new sibling shifts later siblings' ordinals.
        This is True only for ``implicit_ordinal`` units (momentti/subsection).
    """

    kind: str
    identity_class: str
    preserves_identity_on_renumber: bool
    insertion_creates_suffix: bool
    insertion_shifts_later: bool


def _build_identity_policy(entry: UnitOntologyEntry) -> IdentityPolicy:
    """Derive identity policy from an ontology entry."""
    is_stable = entry.identity_class == "stable_label"
    return IdentityPolicy(
        kind=entry.kind,
        identity_class=entry.identity_class,
        preserves_identity_on_renumber=is_stable,
        insertion_creates_suffix=(entry.insertion_policy == "suffix"),
        insertion_shifts_later=(entry.insertion_policy == "shift_ordinal"),
    )


_IDENTITY_POLICIES: dict[str, IdentityPolicy] = {
    kind: _build_identity_policy(entry)
    for kind, entry in UNIT_ONTOLOGY.items()
}


# ---------------------------------------------------------------------------
# Default temporal activation for Finland
# ---------------------------------------------------------------------------

# Finnish statutes default to immediate commencement unless the
# voimaantulosäännös (commencement provision) specifies otherwise.
FINLAND_DEFAULT_ACTIVATION = ActivationRule(kind="immediate")


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def is_well_formed_amendment_target(unit_kind: str, raw_label: str) -> bool:
    """Return True if *unit_kind* + *raw_label* form a valid Finnish amendment target.

    Checks:
    1. The unit kind is a known Finnish legal unit.
    2. The unit kind is amendable.
    3. The raw label parses successfully for that unit kind.
    4. The parsed label is in the expected series for that unit kind.

    Parameters
    ----------
    unit_kind
        E.g. ``"section"``, ``"chapter"``, ``"item"``.
    raw_label
        Raw label text, e.g. ``"5 §"``, ``"3 luku"``, ``"a)"``.

    Returns
    -------
    bool
        True if the target is well-formed.
    """
    if not is_legal_unit(unit_kind):
        return False
    if not is_amendable(unit_kind):
        return False
    try:
        label = parse_label(raw_label, unit_kind)
    except ValueError:
        return False
    return is_valid_label_for_kind(label, unit_kind)


def label_matches_series(unit_kind: str, label: AnyFinlandLabel) -> bool:
    """Return True if *label* is in the expected label series for *unit_kind*.

    This is a convenience wrapper around ``labels.is_valid_label_for_kind``
    that also checks the unit kind is known.
    """
    if not is_legal_unit(unit_kind):
        return False
    return is_valid_label_for_kind(label, unit_kind)


def validate_parent_child(parent_kind: str, child_kind: str) -> bool:
    """Return True if *parent_kind* is an allowed parent of *child_kind*.

    Both kinds must be known Finnish legal units.
    """
    if not is_legal_unit(parent_kind) or not is_legal_unit(child_kind):
        return False
    return parent_kind in parent_kinds(child_kind)


# ---------------------------------------------------------------------------
# FinlandProfile: the unified frozen bundle
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FinlandProfile:
    """Frozen jurisdiction profile bundling Finland's legal ontology,
    label algebra, temporal defaults, and identity policies.

    This is the single entry point for downstream consumers that need
    to query Finnish legal structure semantics.

    Attributes
    ----------
    jurisdiction
        ISO country code.
    unit_ontology
        The canonical unit ontology table.
    hierarchy_order
        Canonical depth-ordered tuple of unit kinds.
    all_unit_kinds
        All known unit kind strings.
    all_facet_kinds
        All known facet kind strings.
    default_activation
        The default temporal activation rule (immediate for Finland).
    identity_policies
        Mapping from unit kind to identity/renumber policy.
    """

    jurisdiction: str
    unit_ontology: dict[str, UnitOntologyEntry]
    hierarchy_order: Tuple[str, ...]
    all_unit_kinds: Tuple[str, ...]
    all_facet_kinds: Tuple[str, ...]
    default_activation: ActivationRule
    identity_policies: dict[str, IdentityPolicy]

    # -- Ontology queries (delegated to ontology module) --

    def is_legal_unit(self, kind: str) -> bool:
        """Return True if *kind* is a known Finnish legal unit."""
        return is_legal_unit(kind)

    def is_amendable(self, kind: str) -> bool:
        """Return True if *kind* is an amendment target."""
        return is_amendable(kind)

    def parent_kinds(self, kind: str) -> Tuple[str, ...]:
        """Return allowed parent kinds for *kind*."""
        return parent_kinds(kind)

    def hierarchy_depth(self, kind: str) -> int:
        """Return canonical hierarchy depth for *kind*, or -1 if unknown."""
        return hierarchy_depth(kind)

    def can_carry_facet(self, unit_kind: str, facet_kind: str) -> bool:
        """Return True if *unit_kind* may carry *facet_kind*."""
        return can_carry_facet(unit_kind, facet_kind)

    def allowed_label_series(self, kind: str) -> Tuple[str, ...]:
        """Return allowed label series names for *kind*."""
        return allowed_label_series(kind)

    # -- Label operations (delegated to labels module) --

    def parse_label(self, raw: str, unit_kind: str) -> AnyFinlandLabel:
        """Parse a raw Finnish label string into a typed label object."""
        return parse_label(raw, unit_kind)

    def render_label(self, label: AnyFinlandLabel, unit_kind: str) -> str:
        """Render a typed label as canonical Finnish display text."""
        return render_label(label, unit_kind)

    def label_sort_key(self, label: AnyFinlandLabel) -> Tuple[int, int, str]:
        """Return a sort key for ordering labels."""
        return label_sort_key(label)

    def normalize_raw_label(self, raw: str, tag: str) -> str:
        """Normalize a raw label string for a given unit kind."""
        return normalize_raw_label(raw, tag)

    # -- Identity policy queries --

    def identity_policy(self, kind: str) -> IdentityPolicy:
        """Return the identity/renumber policy for *kind*.

        Raises
        ------
        KeyError
            If *kind* is not a known Finnish legal unit.
        """
        return self.identity_policies[kind]

    def preserves_identity_on_renumber(self, kind: str) -> bool:
        """Return True if *kind* preserves identity when renumbered.

        Returns False for unknown kinds.
        """
        policy = self.identity_policies.get(kind)
        return policy.preserves_identity_on_renumber if policy is not None else False

    # -- Validation helpers --

    def is_well_formed_amendment_target(
        self, unit_kind: str, raw_label: str
    ) -> bool:
        """Return True if *unit_kind* + *raw_label* is a valid amendment target."""
        return is_well_formed_amendment_target(unit_kind, raw_label)

    def label_matches_series(
        self, unit_kind: str, label: AnyFinlandLabel
    ) -> bool:
        """Return True if *label* is in the expected series for *unit_kind*."""
        return label_matches_series(unit_kind, label)

    def validate_parent_child(
        self, parent_kind: str, child_kind: str
    ) -> bool:
        """Return True if *parent_kind* is an allowed parent of *child_kind*."""
        return validate_parent_child(parent_kind, child_kind)


# ---------------------------------------------------------------------------
# Canonical singleton instance
# ---------------------------------------------------------------------------

FINLAND_PROFILE = FinlandProfile(
    jurisdiction="FI",
    unit_ontology=UNIT_ONTOLOGY,
    hierarchy_order=HIERARCHY_ORDER,
    all_unit_kinds=ALL_UNIT_KINDS,
    all_facet_kinds=ALL_FACET_KINDS,
    default_activation=FINLAND_DEFAULT_ACTIVATION,
    identity_policies=_IDENTITY_POLICIES,
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # Profile
    "FinlandProfile",
    "FINLAND_PROFILE",
    # Identity policy
    "IdentityPolicy",
    # Default activation
    "FINLAND_DEFAULT_ACTIVATION",
    # Standalone validation helpers
    "is_well_formed_amendment_target",
    "label_matches_series",
    "validate_parent_child",
    # Re-exports from ontology
    "UNIT_ONTOLOGY",
    "HIERARCHY_ORDER",
    "ALL_UNIT_KINDS",
    "ALL_FACET_KINDS",
    "UnitOntologyEntry",
    "is_legal_unit",
    "is_amendable",
    "parent_kinds",
    "hierarchy_depth",
    "can_carry_facet",
    "allowed_label_series",
    # Re-exports from labels
    "InsertableArabic",
    "RomanOrdinal",
    "AlphaSequence",
    "ImplicitOrdinal",
    "SymbolicLabel",
    "FinlandLabel",
    "AnyFinlandLabel",
    "parse_label",
    "render_label",
    "label_sort_key",
    "normalize_raw_label",
    "is_valid_label_for_kind",
    # Re-export from core temporal
    "ActivationRule",
]

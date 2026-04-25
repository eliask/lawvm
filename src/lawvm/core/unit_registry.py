"""Unit kind and facet validation registry for CanonicalIntent targets.

A UnitRegistry maps jurisdiction-specific unit_kind strings projected from
LegalAddress leaf kinds, and typed facet values (as used in FacetTarget), to
their validation spec.  It is consulted at lowering time to catch early
miscategorisation — unknown unit_kinds or facets indicate either a parser bug
or a jurisdiction gap that needs addressing.

Validation is strict at the shared-core boundary: invalid target typing is a
programming error in lowering or registry publication, not something core
should silently tolerate.

Usage
-----
    from jurisdiction_frontend.unit_registry import FRONTEND_REGISTRY
    from lawvm.core.unit_registry import validate_intent_target
    validate_intent_target(target, FRONTEND_REGISTRY)

The function raises ``IntentTargetValidationError`` on any unknown unit_kind
or facet mismatch. Callers that want advisory behavior should catch that
explicitly outside core.

API tier
--------
Stable shared validation/registry surface for intent-target typing.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import FrozenSet, Literal, TYPE_CHECKING

from lawvm.core.semantic_types import FacetKind

if TYPE_CHECKING:
    from lawvm.core.canonical_intent import CanonicalTarget

_log = logging.getLogger("lawvm.core.unit_registry")


class IntentTargetValidationError(ValueError):
    """Raised when a CanonicalTarget does not satisfy a registry contract."""


# ---------------------------------------------------------------------------
# UnitSpec and UnitRegistry
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class UnitSpec:
    """Specification for a single structural unit kind.

    unit_kind
        The host leaf kind projected from LegalAddress.path for NodeTarget
        and FacetTarget validation. E.g. "section", "subsection", "item".

    display_name
        Human-readable label for use in diagnostics and logging.

    can_have_heading
        Whether this unit kind may carry a "heading" facet.

    can_have_intro
        Whether this unit kind may carry an "intro" (johd) facet.

    identity_class
        How the unit's identity is established and maintained:
        - "stable_label": explicit label (e.g. "5 §", "3 luku"); insertions use
          a-suffix ("1 a §"); repeal leaves a gap, never compacts siblings.
          Applies to: osa, luku, pykälä, kohta, alakohta, liite.
        - "implicit_ordinal": ordinal position is the identity, not a printed
          label (e.g. momentti is typically unlabelled in text but cited by
          ordinal "3 momentti"). Insertion CAN shift later ordinals; repeal does
          NOT compact later ordinals.
          Applies to: momentti, rivi.
        - "facet": identity inherited from host unit; no independent label
          lifecycle.
          Applies to: heading, intro, wrapUp, document_title.

    insertion_policy
        How new siblings are inserted:
        - "suffix": new unit inserted as a-labelled unit ("1 a §") without
          shifting existing labels.
        - "shift_ordinal": insertion before an existing unit shifts later
          ordinals upward.
        - "inherit_host": no independent insertion; identity is part of host.

    repeal_compacts
        Whether repealing a unit causes later siblings to be renumbered
        (compacted) into the gap.  Repeal does not auto-compact later
        siblings at this layer.

    """
    unit_kind: str
    display_name: str
    can_have_heading: bool = False
    can_have_intro: bool = False
    identity_class: Literal["stable_label", "implicit_ordinal", "facet"] = "stable_label"
    insertion_policy: Literal["suffix", "shift_ordinal", "inherit_host"] = "suffix"
    repeal_compacts: bool = False


@dataclass(frozen=True)
class UnitRegistry:
    """Registry of valid unit_kind strings and typed facet values for a jurisdiction.

    unit_specs
        Mapping from projected unit_kind string → UnitSpec.

    valid_facets
        The complete set of valid facet strings for FacetTarget.facet.
        Callers still pass typed ``FacetKind`` values; the registry stores the
        jurisdiction vocabulary as strings.

    jurisdiction
        Optional short jurisdiction tag for diagnostics.  The built-in
        registry is a shared default for core tests/examples; other
        jurisdictions should provide their own registry instances.
    """
    unit_specs: dict[str, UnitSpec] = field(default_factory=dict)
    valid_facets: FrozenSet[str] = frozenset()
    jurisdiction: str = ""

    def is_valid_unit_kind(self, unit_kind: str) -> bool:
        return unit_kind in self.unit_specs

    def is_valid_facet(self, facet: "FacetKind") -> bool:
        """Return True when a typed FacetKind is accepted by the registry."""
        return facet.value in self.valid_facets

    def get_identity_class(self, unit_kind: str) -> str:
        """Return the identity_class for unit_kind, or 'stable_label' if unknown."""
        spec = self.unit_specs.get(unit_kind)
        return spec.identity_class if spec is not None else "stable_label"

    def allows_suffix_insertion(self, unit_kind: str) -> bool:
        """True if new siblings are inserted as a-labelled units (no shift)."""
        spec = self.unit_specs.get(unit_kind)
        return spec is not None and spec.insertion_policy == "suffix"

    def allows_ordinal_shift(self, unit_kind: str) -> bool:
        """True if inserting before an existing unit shifts later ordinals."""
        spec = self.unit_specs.get(unit_kind)
        return spec is not None and spec.insertion_policy == "shift_ordinal"

    def repeal_compacts_siblings(self, unit_kind: str) -> bool:
        """True if repealing a unit causes later siblings to be renumbered.

        In the shared core registry this is always False for every registered
        unit kind.
        """
        spec = self.unit_specs.get(unit_kind)
        return spec is not None and spec.repeal_compacts


# ---------------------------------------------------------------------------
# Concrete registries belong in jurisdiction frontends
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# validate_intent_target
# ---------------------------------------------------------------------------

def validate_intent_target(target: "CanonicalTarget", registry: UnitRegistry) -> None:
    """Validate the unit_kind and/or typed facet of a CanonicalTarget.

    Raises ``IntentTargetValidationError`` when the target violates the
    registry contract.
    """
    # Import here to avoid circular imports at module load time.
    from lawvm.core.canonical_intent import NodeTarget, FacetTarget  # noqa: PLC0415

    if isinstance(target, NodeTarget):
        target_kind = target.address.leaf_kind()
        if not registry.is_valid_unit_kind(target_kind):
            message = (
                f"Unknown unit_kind {target_kind!r} in NodeTarget "
                f"(address={target.address}, registry={registry.jurisdiction or '<unspecified>'})"
            )
            _log.error(message)
            raise IntentTargetValidationError(message)
        return None

    if isinstance(target, FacetTarget):
        host_kind = target.host.leaf_kind()
        host_spec = registry.unit_specs.get(host_kind)
        if host_spec is None:
            message = (
                f"Unknown host unit_kind {host_kind!r} in FacetTarget "
                f"(host={target.host}, registry={registry.jurisdiction or '<unspecified>'})"
            )
            _log.error(message)
            raise IntentTargetValidationError(message)
        if not registry.is_valid_facet(target.facet):
            message = (
                f"Unknown facet {target.facet.value!r} in FacetTarget "
                f"(host={target.host}, registry={registry.jurisdiction or '<unspecified>'}); "
                f"valid facets are {sorted(registry.valid_facets)!r}"
            )
            _log.error(message)
            raise IntentTargetValidationError(message)
        if target.facet.value == "heading" and not host_spec.can_have_heading:
            message = (
                f"Facet {target.facet.value!r} is not valid for host unit_kind "
                f"{host_kind!r} (host={target.host}, registry={registry.jurisdiction or '<unspecified>'})"
            )
            _log.error(message)
            raise IntentTargetValidationError(message)
        if target.facet.value == "intro" and not host_spec.can_have_intro:
            message = (
                f"Facet {target.facet.value!r} is not valid for host unit_kind "
                f"{host_kind!r} (host={target.host}, registry={registry.jurisdiction or '<unspecified>'})"
            )
            _log.error(message)
            raise IntentTargetValidationError(message)
        return None

    # TextTarget has no unit_kind or facet — nothing to validate.
    return None

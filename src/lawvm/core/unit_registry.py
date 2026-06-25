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
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import FrozenSet, Literal, TYPE_CHECKING

from lawvm.core.frozen_values import FrozenDict
from lawvm.core.semantic_types import FacetKind

if TYPE_CHECKING:
    from lawvm.core.canonical_intent import CanonicalTarget

_log = logging.getLogger("lawvm.core.unit_registry")


class IntentTargetValidationError(ValueError):
    """Raised when a CanonicalTarget does not satisfy a registry contract."""


# ---------------------------------------------------------------------------
# UnitSpec and UnitRegistry
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
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
          Applies to: part (osa), chapter (luku), section (pykälä), paragraph
          (kohta), subparagraph (alakohta), annex (liite).
        - "implicit_ordinal": ordinal position is the identity, not a printed
          label (e.g. subsection (momentti) is typically unlabelled in text but
          cited by ordinal "3 momentti"). Insertion CAN shift later ordinals;
          repeal does NOT compact later ordinals.
          Applies to: subsection (momentti), line (rivi).
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

    def __post_init__(self) -> None:
        if not isinstance(self.unit_kind, str) or not self.unit_kind:
            raise ValueError("UnitSpec.unit_kind must be a non-empty string")
        if not isinstance(self.display_name, str) or not self.display_name:
            raise ValueError("UnitSpec.display_name must be a non-empty string")
        bool_fields = (
            ("can_have_heading", self.can_have_heading),
            ("can_have_intro", self.can_have_intro),
            ("repeal_compacts", self.repeal_compacts),
        )
        for attr, value in bool_fields:
            if not isinstance(value, bool):
                raise TypeError(f"UnitSpec.{attr} must be a bool")
        if self.identity_class not in {"stable_label", "implicit_ordinal", "facet"}:
            raise ValueError("UnitSpec.identity_class is not a supported value")
        if self.insertion_policy not in {"suffix", "shift_ordinal", "inherit_host"}:
            raise ValueError("UnitSpec.insertion_policy is not a supported value")


@dataclass(frozen=True, slots=True)
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
    unit_specs: Mapping[str, UnitSpec] = field(default_factory=FrozenDict)
    valid_facets: FrozenSet[str] = frozenset()
    jurisdiction: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.unit_specs, Mapping):
            raise TypeError("UnitRegistry.unit_specs must be a mapping")
        normalized_specs: dict[str, UnitSpec] = {}
        for unit_kind, spec in self.unit_specs.items():
            if not isinstance(unit_kind, str) or not unit_kind:
                raise ValueError("UnitRegistry.unit_specs keys must be non-empty strings")
            if not isinstance(spec, UnitSpec):
                raise TypeError("UnitRegistry.unit_specs values must be UnitSpec")
            if spec.unit_kind != unit_kind:
                raise ValueError("UnitRegistry.unit_specs keys must match UnitSpec.unit_kind")
            normalized_specs[unit_kind] = spec
        object.__setattr__(self, "unit_specs", FrozenDict(normalized_specs))

        normalized_facets = frozenset(self.valid_facets)
        if any(not isinstance(facet, str) or not facet for facet in normalized_facets):
            raise ValueError("UnitRegistry.valid_facets must contain non-empty strings")
        object.__setattr__(self, "valid_facets", normalized_facets)
        if not isinstance(self.jurisdiction, str):
            raise TypeError("UnitRegistry.jurisdiction must be a string")

    def is_valid_unit_kind(self, unit_kind: str) -> bool:
        return unit_kind in self.unit_specs

    def is_valid_facet(self, facet: "FacetKind") -> bool:
        """Return True when a typed FacetKind is accepted by the registry."""
        return facet.value in self.valid_facets

    def _require_spec(self, unit_kind: str, accessor: str) -> UnitSpec:
        """Return the UnitSpec for unit_kind or fail loud.

        The insertion/repeal/identity accessors below drive structural apply
        policy. An unregistered ``unit_kind`` previously fell through to a
        *guessed* default (identity_class="stable_label", insertion/repeal
        False) -- silently applying a possibly-wrong lifecycle policy to a kind
        the registry never declared. That is exactly the implicit-default seam
        this registry exists to catch, so resolve it loud and self-evidencing
        (the message embeds the offending unit_kind) instead.
        """
        spec = self.unit_specs.get(unit_kind)
        if spec is None:
            message = (
                f"Unregistered unit_kind {unit_kind!r} in {accessor} "
                f"(registry={self.jurisdiction or '<unspecified>'}); refusing to "
                f"apply a guessed default lifecycle policy. Known unit_kinds: "
                f"{sorted(self.unit_specs)!r}"
            )
            _log.error(message)
            raise IntentTargetValidationError(message)
        return spec

    def get_identity_class(self, unit_kind: str) -> str:
        """Return the identity_class for unit_kind; fail loud if unregistered."""
        return self._require_spec(unit_kind, "get_identity_class").identity_class

    def allows_suffix_insertion(self, unit_kind: str) -> bool:
        """True if new siblings are inserted as a-labelled units (no shift)."""
        return self._require_spec(unit_kind, "allows_suffix_insertion").insertion_policy == "suffix"

    def allows_ordinal_shift(self, unit_kind: str) -> bool:
        """True if inserting before an existing unit shifts later ordinals."""
        return self._require_spec(unit_kind, "allows_ordinal_shift").insertion_policy == "shift_ordinal"

    def repeal_compacts_siblings(self, unit_kind: str) -> bool:
        """True if repealing a unit causes later siblings to be renumbered.

        In the shared core registry this is always False for every registered
        unit kind. An unregistered unit_kind fails loud rather than defaulting.
        """
        return self._require_spec(unit_kind, "repeal_compacts_siblings").repeal_compacts


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
    from lawvm.core.canonical_intent import NodeTarget, FacetTarget

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

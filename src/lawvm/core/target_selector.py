"""TargetSelector — pre-resolution amendment target descriptor (cross-jurisdiction).

A ``TargetSelector`` describes *what an amendment source says it targets* before
the resolver has bound that description to a concrete tree node. It is therefore
distinct from :class:`lawvm.core.ir.LegalAddress`, which is the *resolved* node
identity.

Selector vs resolved address (Pro ruling):
- A selector carries a ``relative_path`` (the focus, e.g. ``section:5`` or
  ``section:5 / subsection:1 / item:4``) plus a :class:`TargetScope` describing
  what the source declared about the enclosing scope (chapter/part), including
  whether the source declared a scope *at all*.
- Only when the scope is sufficiently determined can the selector be projected
  to a ``LegalAddress`` via :meth:`TargetSelector.to_legal_address_if_complete`.

These types are generic and live in core. Jurisdiction-specific encodings (such
as the Finland legacy ``AmendmentOp`` ``target_*`` columns) belong in a codec at
the jurisdiction boundary, never here.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from lawvm.core.ir import LegalAddress
from lawvm.core.semantic_types import FacetKind


class ScopeStatus(StrEnum):
    """How the enclosing (parent) scope of a target was determined."""

    UNSPECIFIED = "unspecified"
    """The source did not declare any parent scope (chapter/part)."""

    EXPLICIT_ROOT = "explicit_root"
    """The source explicitly placed the target at root/body level."""

    EXPLICIT_SCOPE = "explicit_scope"
    """The source named a concrete parent scope (carried in ``path``)."""

    INFERRED_SCOPE = "inferred_scope"
    """A resolver inferred the parent scope; ``rule_id`` records the inference."""


@dataclass(frozen=True, slots=True)
class AddressSegment:
    """One ``(kind, label)`` step of a structural address path."""

    kind: str
    label: str


@dataclass(frozen=True, slots=True)
class TargetScope:
    """The declared (or inferred) enclosing scope of a target.

    Invariants (enforced in ``__post_init__``):
    - ``EXPLICIT_SCOPE`` requires a non-empty ``path``.
    - ``EXPLICIT_ROOT`` forbids a ``path``.
    - ``INFERRED_SCOPE`` requires a ``rule_id``.
    - ``UNSPECIFIED`` forbids a ``path`` (nothing was declared).
    """

    status: ScopeStatus
    path: tuple[AddressSegment, ...] = ()
    rule_id: str | None = None

    def __post_init__(self) -> None:
        if self.status == ScopeStatus.EXPLICIT_SCOPE and not self.path:
            raise ValueError("TargetScope.EXPLICIT_SCOPE requires a non-empty path")
        if self.status == ScopeStatus.EXPLICIT_ROOT and self.path:
            raise ValueError("TargetScope.EXPLICIT_ROOT forbids a path")
        if self.status == ScopeStatus.UNSPECIFIED and self.path:
            raise ValueError("TargetScope.UNSPECIFIED forbids a path")
        if self.status == ScopeStatus.INFERRED_SCOPE and not self.rule_id:
            raise ValueError("TargetScope.INFERRED_SCOPE requires a rule_id")


# Address segment kinds, deepest-first, that identify the "section family" focus.
_SECTION_FAMILY_KINDS = ("section", "subsection", "item", "subitem")

# Major-kind precedence for the major_kind property (most significant first).
_MAJOR_KIND_PRECEDENCE = ("section", "chapter", "part")


@dataclass(frozen=True, slots=True)
class TargetSelector:
    """A pre-resolution description of an amendment's target.

    ``relative_path`` is the focus of the amendment relative to its scope (e.g.
    ``section:5`` or ``section:5 / subsection:1 / item:4``). ``scope`` records
    what the source said about the enclosing chapter/part.
    """

    relative_path: tuple[AddressSegment, ...]
    scope: TargetScope
    special: FacetKind | None = None
    source_rule_id: str | None = None
    # Jurisdiction-specific facet token as the *source* phrased it, when the
    # coarse ``special`` (a cross-jurisdiction ``FacetKind``) cannot distinguish
    # finer variants. Example: Finland's ``otsikko`` (heading) vs
    # ``otsikko_edella`` (heading-before/edellä) both map to ``FacetKind.HEADING``,
    # so the exact source token is preserved here for lossless legacy round-trip.
    # The resolved ``LegalAddress`` deliberately uses only the coarse ``special``.
    special_raw: str | None = None

    def __post_init__(self) -> None:
        if not self.relative_path:
            raise ValueError("TargetSelector.relative_path must be non-empty")

    @property
    def major_kind(self) -> str:
        """The address-family of the focus.

        A section/subsection/item/subitem target is still a ``"section"`` target.
        Otherwise the most-significant present kind (chapter, then part) wins; if
        none of those appear, fall back to the deepest segment's kind.
        """
        kinds = {segment.kind for segment in self.relative_path}
        if kinds & set(_SECTION_FAMILY_KINDS):
            return "section"
        for kind in _MAJOR_KIND_PRECEDENCE:
            if kind in kinds:
                return kind
        return self.relative_path[-1].kind

    def to_legal_address_if_complete(self) -> LegalAddress | None:
        """Project to a resolved :class:`LegalAddress`, or ``None`` if not complete.

        Returns ``None`` when the scope is :attr:`ScopeStatus.UNSPECIFIED` (the
        source never declared a scope, so the resolved identity is unknown).
        For :attr:`ScopeStatus.EXPLICIT_ROOT` the address is ``relative_path``
        alone; otherwise the scope path is prepended.
        """
        if self.scope.status == ScopeStatus.UNSPECIFIED:
            return None
        if self.scope.status == ScopeStatus.EXPLICIT_ROOT:
            full_segments: tuple[AddressSegment, ...] = self.relative_path
        else:
            scope_path = self.scope.path
            # A part-focus selector may carry a redundant enclosing ``part`` scope
            # segment equal to the focus (the legacy ``target_part``-mirrors-
            # ``target_section`` encoding round-tripped losslessly by the FI codec).
            # Collapse that exact duplicate so the resolved address stays a single
            # ``part:<x>`` and never produces ``part:<x>/part:<x>``.
            if (
                scope_path
                and scope_path[-1] == self.relative_path[0]
                and self.relative_path[0].kind == "part"
            ):
                scope_path = scope_path[:-1]
            full_segments = scope_path + self.relative_path
        path = tuple((segment.kind, segment.label) for segment in full_segments)
        return LegalAddress(path=path, special=self.special)

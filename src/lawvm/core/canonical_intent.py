"""Canonical three-axis intent type system for legal operation dispatch.

This module defines the CanonicalIntent sum type and its supporting types along
three orthogonal axes:

  Axis 1 — Action family (Replace | Insert | Repeal | TextPatch | Relabel | Move)
    What kind of structural change the operation performs.  Each variant carries
    variant-specific fields (payload for structural rewrites, patch for text-level
    ops, source/destination for Move/Relabel) plus a target and an execution
    contract.

  Axis 2 — Target family (NodeTarget | FacetTarget | TextTarget)
    What structural element is being addressed.  NodeTarget covers whole nodes
    (sections, subsections, items, …); FacetTarget covers named facets of a node
    (heading, intro, wording); TextTarget supports word-level selectors for
    jurisdictions like the UK.

  Axis 3 — Execution contract (ExecutionContract)
    How apply must execute the operation: occupancy expectations, coverage mode,
    insertion ordering, and placeholder behaviour.  This bundles OccupancyPolicy
    (what occupancy class is expected, allowed, and produced), CoverageMode,
    InsertOrder, and PlaceholderPolicy.

Constitutional rule
-------------------
apply may dispatch ONLY on typed intent and typed contract — never by
re-inferring from old fields (op_type, target_paragraph, target_special,
target_item) or from resolution_hint strings. Debug-oriented hint text may
still be present on some adapters, but it has zero semantic weight for
execution and is not part of the live runtime lane.

Shared-kernel execution flows through ``LegalOperation`` while keeping
``LegalOperation`` move-free and ``CanonicalIntent`` as the semantic endpoint
for MOVE semantics.

API tier
--------
Stable shared intent algebra for lowering/validation. Temporal execution is
intentionally outside this module, and a few interop helpers remain.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import FrozenSet, Literal, Optional, Protocol, Union

import icontract

from lawvm.core.ir import LegalAddress
from lawvm.core.occupancy import OccupancyClass
from lawvm.core.semantic_types import FacetKind


# ---------------------------------------------------------------------------
# Axis 3 supporting enums
# ---------------------------------------------------------------------------

class CoverageMode(StrEnum):
    """How apply treats child nodes of the target that are not mentioned in the payload.

    EXACT
        The payload fully replaces the target.  Any existing children not
        present in the payload are discarded.

    PRESERVE_UNSPECIFIED_WITHIN_TARGET
        Children of the target that are not addressed by this operation are
        preserved.  Used for sparse subsection replacements where only some
        sub-items are specified.
    """
    EXACT = "exact"
    PRESERVE_UNSPECIFIED_WITHIN_TARGET = "preserve_unspecified_within_target"


class InsertOrder(StrEnum):
    """Where a new node is placed among its siblings.

    SORTED_FAMILY
        Insert in canonical label order within the sibling family (e.g. 14a
        sorts between 14 and 14b).

    BEFORE_ANCHOR
        Insert immediately before the anchor node specified on the Insert intent.

    AFTER_ANCHOR
        Insert immediately after the anchor node specified on the Insert intent.
    """
    SORTED_FAMILY = "sorted_family"
    BEFORE_ANCHOR = "before_anchor"
    AFTER_ANCHOR = "after_anchor"


class PlaceholderPolicy(StrEnum):
    """What to do when the target slot is absent or a tombstone.

    NONE
        Do not create any placeholder.  The operation fails if the slot is
        absent and the occupancy policy does not permit it.

    MATERIALIZE
        Create a scaffold/placeholder node so the address is resolvable before
        the full content arrives.

    TOMBSTONE_ONLY
        After the operation the slot holds a tombstone even if no substantive
        content was ever placed.
    """
    NONE = "none"
    MATERIALIZE = "materialize"
    TOMBSTONE_ONLY = "tombstone_only"


# ---------------------------------------------------------------------------
# Small protocol-style local contracts for payloads
# ---------------------------------------------------------------------------

class _TextSelector(Protocol):
    """Minimal selector contract shared by text patch variants."""

    match_text: str
    occurrence: int


class _IRNodeLike(Protocol):
    """Minimal structural contract for tree payloads carried by Replace/Insert."""

    kind: object
    children: tuple["_IRNodeLike", ...]
    label: str | None
    text: str


class _TextPatchLike(Protocol):
    """Minimal contract for text-level patch payloads."""

    kind: object
    selector: _TextSelector
    replacement: str | None


# ---------------------------------------------------------------------------
# Axis 3 — OccupancyPolicy (richer than OccupancyClass alone)
# ---------------------------------------------------------------------------

@icontract.invariant(
    lambda self: self.primary_expected_from <= self.allowed_from,
    "primary_expected_from must be a subset of allowed_from",
)
@dataclass(frozen=True)
class OccupancyPolicy:
    """Typed occupancy contract for a single legal operation.

    Describes what apply must check and enforce around slot occupancy:

    primary_expected_from
        The occupancy classes that the caller considers the "normal" case.
        If the actual slot occupancy is in this set, apply proceeds without
        any fallback.

    allowed_from
        The full set of occupancy classes from which the operation is
        permitted to proceed.  Must be a superset of primary_expected_from.
        Any actual occupancy outside this set is a hard conflict — apply
        must surface a FailedOp rather than silently proceeding.

    result
        The occupancy class of the slot after the operation completes
        successfully.
    """
    primary_expected_from: FrozenSet[OccupancyClass]
    allowed_from: FrozenSet[OccupancyClass]
    result: OccupancyClass

    # ------------------------------------------------------------------
    # Convenience constructors
    # ------------------------------------------------------------------

    @staticmethod
    def fresh_insert() -> OccupancyPolicy:
        """New node inserted into a slot that has never existed."""
        return OccupancyPolicy(
            primary_expected_from=frozenset({OccupancyClass.ABSENT}),
            allowed_from=frozenset({OccupancyClass.ABSENT}),
            result=OccupancyClass.SUBSTANTIVE,
        )

    @staticmethod
    def reenact_insert() -> OccupancyPolicy:
        """Reinstatement of a previously repealed (tombstone) node.

        The primary expectation is a tombstone, but scaffold and absent are
        also permitted to accommodate slightly imprecise source XML.
        """
        return OccupancyPolicy(
            primary_expected_from=frozenset({OccupancyClass.TOMBSTONE}),
            allowed_from=frozenset({
                OccupancyClass.ABSENT,
                OccupancyClass.TOMBSTONE,
                OccupancyClass.SCAFFOLD,
            }),
            result=OccupancyClass.SUBSTANTIVE,
        )

    @staticmethod
    def same_slot_replace() -> OccupancyPolicy:
        """Ordinary content update of a live node."""
        return OccupancyPolicy(
            primary_expected_from=frozenset({OccupancyClass.SUBSTANTIVE}),
            allowed_from=frozenset({OccupancyClass.SUBSTANTIVE}),
            result=OccupancyClass.SUBSTANTIVE,
        )

    @staticmethod
    def repeal_to_tombstone() -> OccupancyPolicy:
        """Repeal with placeholder: slot becomes a tombstone.

        Allows repealing a node that is already a tombstone (idempotent repeal
        of a previously repealed provision).
        """
        return OccupancyPolicy(
            primary_expected_from=frozenset({OccupancyClass.SUBSTANTIVE}),
            allowed_from=frozenset({
                OccupancyClass.SUBSTANTIVE,
                OccupancyClass.TOMBSTONE,
            }),
            result=OccupancyClass.TOMBSTONE,
        )

# ---------------------------------------------------------------------------
# Axis 3 — ExecutionContract
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ExecutionContract:
    """Full execution contract for a legal operation.

    Bundles all apply-time behavioural parameters that are orthogonal to the
    action family and target.  apply reads ONLY this contract (plus typed
    intent) — never old op fields.

    occupancy
        What occupancy class is expected, what is tolerated, and what results.

    coverage
        How to handle children of the target that are not mentioned in the
        payload.  Defaults to EXACT (full replacement).

    insert_order
        Where to place a newly inserted node among its siblings.  Required
        for Insert intents; should be None for Replace/Repeal.

    placeholder
        What to do when the target slot is absent.  None means "follow
        occupancy policy strictly" (most common).
    """
    occupancy: OccupancyPolicy
    coverage: CoverageMode = CoverageMode.EXACT
    insert_order: Optional[InsertOrder] = None
    placeholder: Optional[PlaceholderPolicy] = None


# ---------------------------------------------------------------------------
# Axis 1 — IntentKind enum
# ---------------------------------------------------------------------------

class IntentKind(StrEnum):
    """Discriminant tag for the CanonicalIntent sum type."""
    REPLACE = "replace"
    INSERT = "insert"
    REPEAL = "repeal"
    TEXT_PATCH = "text_patch"
    RELABEL = "relabel"
    MOVE = "move"


# ---------------------------------------------------------------------------
# Axis 2 — Target families
# ---------------------------------------------------------------------------

@dataclass(frozen=True, init=False)
class NodeTarget:
    """Address a whole structural node (section, subsection, item, row, annex, …).

    The address is the stored address model. Shared core requires a structural
    node address here; facet-targeted addresses belong in ``FacetTarget``.
    """
    address: LegalAddress

    def __init__(self, address: LegalAddress) -> None:
        if address.special is not None:
            raise ValueError(
                "NodeTarget requires a structural address without facet special"
            )
        object.__setattr__(self, "address", address)


@dataclass(frozen=True)
class FacetTarget:
    """Address a named facet of a node rather than the node body as a whole.

    host
        Address of the parent node whose facet is being targeted.

    facet
        Typed facet ontology for the host node. Jurisdiction adapters must
        map any incoming strings to ``FacetKind`` before constructing a
        shared-core target.
    """
    host: LegalAddress
    facet: FacetKind

    def __post_init__(self) -> None:
        if self.host.special is not None:
            raise ValueError(
                "FacetTarget.host must be a structural host address without facet special"
            )
        if self.facet in (FacetKind.NONE, FacetKind.WHOLE_ACT):
            raise ValueError(
                "FacetTarget requires a concrete node facet, not NONE or WHOLE_ACT"
            )


@dataclass(frozen=True)
class TextTarget:
    """Address a word- or phrase-level span within a provision.

    Used for UK text substitution patterns ("for X substitute Y") where the
    granularity is below the facet level.
    """
    host: LegalAddress
    selector: _TextSelector


# Union for type annotations
CanonicalTarget = Union[NodeTarget, FacetTarget, TextTarget]


def _require_kind(actual: IntentKind, expected: IntentKind, cls_name: str) -> None:
    if actual != expected:
        raise ValueError(
            f"{cls_name} requires kind={expected.value!r}, got {actual.value!r}"
        )


def _require_payload(payload: object, cls_name: str) -> None:
    if payload is None:
        raise ValueError(f"{cls_name} requires non-None payload")


# ---------------------------------------------------------------------------
# Axis 1 — Action family dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Replace:
    """Replace the content of an existing node or facet with new payload.

    target
        Either a NodeTarget (whole-node replacement) or a FacetTarget
        (heading/intro/wording replacement).

    payload
        The new IRNode tree to place at the target address.

    contract
        Full execution contract including occupancy expectations.
        Typical: same_slot_replace() occupancy, EXACT coverage.
    """
    kind: Literal[IntentKind.REPLACE]
    target: Union[NodeTarget, FacetTarget]
    payload: _IRNodeLike
    contract: ExecutionContract

    def __post_init__(self) -> None:
        _require_kind(self.kind, IntentKind.REPLACE, "Replace")
        _require_payload(self.payload, "Replace")
        if self.contract.insert_order is not None:
            raise ValueError(
                "Replace cannot carry insert-order execution fields"
            )


@dataclass(frozen=True)
class Insert:
    """Insert a new node at the target address.

    target
        Must be a NodeTarget.  The address specifies where the new node will
        live after insertion (its future identity), not an anchor.

    payload
        The IRNode to insert.

    contract
        Must include insert_order to specify sibling placement.
        Typical occupancy: fresh_insert() or reenact_insert().

    anchor
        When contract.insert_order is BEFORE_ANCHOR or AFTER_ANCHOR, the
        address of the existing sibling node to insert relative to. None for
        SORTED_FAMILY inserts. This is the canonical location for the insert
        anchor.
    """
    kind: Literal[IntentKind.INSERT]
    target: NodeTarget
    payload: _IRNodeLike
    contract: ExecutionContract
    anchor: Optional[LegalAddress] = None

    def __post_init__(self) -> None:
        _require_kind(self.kind, IntentKind.INSERT, "Insert")
        _require_payload(self.payload, "Insert")
        if self.contract.insert_order is None:
            raise ValueError("Insert requires contract.insert_order")
        if self.anchor is not None and self.contract.insert_order not in (
            InsertOrder.BEFORE_ANCHOR,
            InsertOrder.AFTER_ANCHOR,
        ):
            raise ValueError(
                "Insert.anchor requires BEFORE_ANCHOR or AFTER_ANCHOR insert_order"
            )
        if self.contract.insert_order in (InsertOrder.BEFORE_ANCHOR, InsertOrder.AFTER_ANCHOR):
            if self.anchor is None:
                raise ValueError(
                    "Anchored Insert requires anchor"
                )


@dataclass(frozen=True)
class Repeal:
    """Repeal (remove or tombstone) the node at the target address.

    target
        Must be a NodeTarget.

    contract
        Typical occupancy: repeal_to_tombstone().
        PlaceholderPolicy controls whether a tombstone node is kept.
    """
    kind: Literal[IntentKind.REPEAL]
    target: NodeTarget
    contract: ExecutionContract

    def __post_init__(self) -> None:
        _require_kind(self.kind, IntentKind.REPEAL, "Repeal")
        if self.contract.insert_order is not None:
            raise ValueError(
                "Repeal cannot carry insert-order execution fields"
            )


@dataclass(frozen=True)
class TextPatch:
    """Apply a word-level patch to a text span (primarily UK).

    target
        Must be a TextTarget carrying the selector that identifies the span.

    patch
        The patch specification (e.g. ``TextPatchSpec`` from core IR adapters).

    contract
        Occupancy contract.  Typical: same_slot_replace() on the host node.
    """
    kind: Literal[IntentKind.TEXT_PATCH]
    target: TextTarget
    patch: _TextPatchLike
    contract: ExecutionContract

    def __post_init__(self) -> None:
        _require_kind(self.kind, IntentKind.TEXT_PATCH, "TextPatch")
        if self.patch is None:
            raise ValueError("TextPatch requires non-None patch")
        if self.contract.insert_order is not None:
            raise ValueError(
                "TextPatch cannot carry insert-order execution fields"
            )


@dataclass(frozen=True)
class Relabel:
    """Change the label of a node in-place (renumber without moving).

    source
        The node's current address (the address that will be updated).

    destination
        The node's target address (with the new label).  The parent path
        of source and destination must be identical — Relabel does not move
        nodes across parents; use Move for that.

    contract
        Occupancy contract.  Typical: same_slot_replace() on the source.
    """
    kind: Literal[IntentKind.RELABEL]
    source: NodeTarget
    destination: NodeTarget
    contract: ExecutionContract

    def __post_init__(self) -> None:
        _require_kind(self.kind, IntentKind.RELABEL, "Relabel")
        source_parent = self.source.address.parent()
        destination_parent = self.destination.address.parent()
        if source_parent != destination_parent:
            raise ValueError(
                "Relabel source and destination must share the same parent path"
            )
        if self.contract.insert_order is not None:
            raise ValueError(
                "Relabel cannot carry insert-order execution fields"
            )


@dataclass(frozen=True)
class Move:
    """Move a node from its current address to a new parent.

    source
        The node's current address.

    destination_parent
        The address of the new parent.  The node retains its label unless
        a Relabel follows this Move. Move is a cross-parent operation; same-
        parent label changes belong in ``Relabel``.

    contract
        Occupancy contract.  The source slot becomes ABSENT or TOMBSTONE;
        the destination slot follows insert occupancy rules.
    """
    kind: Literal[IntentKind.MOVE]
    source: NodeTarget
    destination_parent: LegalAddress
    contract: ExecutionContract

    def __post_init__(self) -> None:
        _require_kind(self.kind, IntentKind.MOVE, "Move")
        if self.destination_parent.special is not None:
            raise ValueError(
                "Move destination_parent must be a structural parent address without facet special"
            )
        if self.source.address.parent() == self.destination_parent:
            raise ValueError(
                "Move requires a destination_parent distinct from the source parent; use Relabel for same-parent relabeling"
            )
        if self.contract.insert_order is not None:
            raise ValueError(
                "Move cannot carry insert-order execution fields"
            )


# ---------------------------------------------------------------------------
# Top-level union
# ---------------------------------------------------------------------------

CanonicalIntent = Union[Replace, Insert, Repeal, TextPatch, Relabel, Move]

"""Pipeline-waist types for the amendment-body elaboration pipeline.

This module defines the shared-waist types that bracket the pure
pre-elaboration stages of ``_compile_group``:

    Stage 1 → GroupSurface       (pure amendment-XML extraction)
    Stage 2 → PayloadSurface     (source-local normalization; second waist)

Architecture reference: ``notes/PRO_RESPONSE3_1.md`` §§ 3, 5, 7.

PayloadSurface is the **second waist** in LawVM's pipeline:

    ClauseAST       — clause surface waist (PEG parse output)
    PayloadSurface  — amendment body surface waist  ← this module
    Jurisdiction-local elaboration — frontend apply waist

Shared-kernel execution authority still converges on ``LegalOperation``.

Everything **above** PayloadSurface is pure amendment-XML analysis (no live
replay state required).  Everything **below** requires a live snapshot
(``TargetContext`` / ``ReplayLookups``).

Source-local operations that belong **above** PayloadSurface:
- XML → IRNode conversion (``xml_to_ir_node``)
- intro-list subsection collapse
  (``_collapse_intro_list_subsections_inside_section_ir``)
- split-omission prefix folding
  (``_fold_split_omission_subsection_prefix_into_following_intro_list``)
- omission-continuation folding
  (``_fold_intro_list_continuation_subsection_before_omission``)
- sparse subsection payload IR preparation
  (``_prepare_sparse_subsection_payload_ir``)

Live-dependent operations that belong **below** PayloadSurface:
- sparse omission alignment to live subsections
  (``_align_sparse_omission_subsections_to_live``)
- named-row table rewrites against live structure
  (``_rewrite_named_row_table_replaces``, ``_rewrite_named_row_table_repeals``)
- container shadowing pruning
  (``_prune_container_payload_sections_shadowed_by_standalone_targets``)
- subsection slot assignment (``_build_subsection_override_map``)
- omission pre-resolution (``_pre_resolve_omissions``)
- row-continuation folding (``_fold_continuation_row_subsections_into_previous_subsection``)

The shared waist ends at ``PayloadSurface``. The later elaboration carrier is
frontend-local and does not belong in ``core/``.

API tier
--------
Internal pipeline waist. This is a real compiler boundary for source-local
payload facts before live elaboration.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet, List, Optional, Tuple

from lawvm.core.ir import IRNode
from lawvm.core.ir_helpers import _kind_str
from lawvm.core.semantic_types import IRNodeKind, PayloadSourceShape
from lawvm.core.target_scope import TargetUnitKind


# ---------------------------------------------------------------------------
# GroupSurface
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GroupSurface:
    """Raw amendment-group payload extracted from the amendment XML.

    This is the output of Stage 1 (``_build_group_surface``): pure of live
    state, containing only what can be read from the amendment XML itself.

    ``body_ir``
        The IRNode tree parsed from the amendment body XML for this group's
        target.  ``None`` when no matching body element was found.

    ``cross_heading_ir``
        The IRNode for a preceding ``crossHeading`` element, if any.  This
        must travel alongside the section payload for correct structural
        insertion.

    ``source_statute``
        The source statute ID string propagated from the frontend group input.
        Empty string if none. Used for diagnostics and observation emission.

    ``target_unit_kind``
        The amendment target unit kind in neutral shared vocabulary:
        ``"section"``, ``"chapter"``, or ``"part"``.

    ``target_norm``
        Normalised target label (section/chapter/part number).

    ``target_chapter``
        Enclosing chapter label when ``target_unit_kind == "part"`` and the section
        lives inside a chapter, otherwise ``None``.
    """

    body_ir: Optional[IRNode]
    cross_heading_ir: Optional[IRNode]
    source_statute: str

    # Target identity fields — duplicated here so Stage 2 is self-contained
    target_unit_kind: TargetUnitKind
    target_norm: str
    target_chapter: Optional[str]

    def __post_init__(self) -> None:
        if not self.target_norm:
            raise ValueError("GroupSurface.target_norm must be non-empty")


# ---------------------------------------------------------------------------
# PayloadSurface
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PayloadSurface:
    """The amendment body after source-local normalization, before live elaboration.

    This is the **second waist**: everything above is pure amendment-XML
    analysis; everything below requires live replay state (via
    ``TargetContext`` / ``ReplayLookups``).

    ``section_ir``
        The normalized section payload IRNode after all source-local folds
        have been applied.  ``None`` when the amendment has no matching body
        element (e.g. pure repeal, heading-only, or cross-heading-only groups).

    ``cross_heading_ir``
        The preceding ``crossHeading`` IRNode if any.  Travels alongside the
        section payload for structural insertion.

    ``omission_positions``
        Tuple of child-list indices (0-based) where omission marker nodes
        appear inside ``section_ir.children``.  Empty when no omissions are
        present.  Populated here so downstream elaboration does not need to
        re-scan children.

    ``subsection_count``
        Number of ``subsection`` children in ``section_ir``.  Used by
        elaboration to decide whether sparse-omission alignment is needed.

    ``has_heading``
        ``True`` when ``section_ir`` contains a ``<heading>`` child node.

    ``has_intro``
        ``True`` when ``section_ir`` contains an ``<intro>`` or
        ``johdantokappale`` child node.

    ``source_shape``
        Governed coarse shape classification of the payload, derived from the
        normalised ``section_ir`` structure.  The enum members name the
        current amendment-body normalization aid, not a final cross-
        jurisdiction ontology:

        ``PayloadSourceShape.WHOLE_SECTION``
            Payload replaces or inserts the whole section (no subsection
            structure, or a single monolithic body).

        ``PayloadSourceShape.SPARSE_SUBSECTIONS``
            Payload contains multiple subsection children, some of which may
            be omissions.  Requires sparse-omission alignment against live.

        ``PayloadSourceShape.SINGLE_SUBSECTION``
            Payload contains exactly one subsection child (no omissions).
            Common for targeted subsection-level amendments.

        ``PayloadSourceShape.ITEMS_ONLY``
            Payload has no subsection structure — only item/paragraph
            children directly under the section root.

        ``PayloadSourceShape.EMPTY``
            No body payload (``section_ir`` is ``None``).

    ``tags``
        Frozenset of string flags for properties that downstream elaboration
        may branch on.  Populated values include:

        ``'has_table'``      — payload contains a table element
        ``'has_liite'``      — payload references or contains an appendix
        ``'generic_lead'``   — lead text is a generic/vague intro phrase
        ``'omission_tail'``  — last child of section_ir is an omission marker
        ``'omission_head'``  — first child of section_ir is an omission marker

    ``source_statute``
        Provenance string propagated from ``GroupSurface``.
    """

    section_ir: Optional[IRNode]
    cross_heading_ir: Optional[IRNode]

    omission_positions: Tuple[int, ...]
    subsection_count: int
    has_heading: bool
    has_intro: bool
    source_shape: PayloadSourceShape
    tags: FrozenSet[str]

    source_statute: str

    def __post_init__(self) -> None:
        if self.section_ir is None:
            if self.source_shape is not PayloadSourceShape.EMPTY:
                raise ValueError("PayloadSurface with section_ir=None must have source_shape=EMPTY")
            if self.omission_positions:
                raise ValueError("PayloadSurface with section_ir=None cannot carry omission_positions")
            if self.subsection_count != 0:
                raise ValueError("PayloadSurface with section_ir=None must have subsection_count=0")
            if self.has_heading or self.has_intro:
                raise ValueError("PayloadSurface with section_ir=None cannot report heading/intro flags")
            if self.tags:
                raise ValueError("PayloadSurface with section_ir=None cannot carry payload tags")
            return

        direct_children = tuple(self.section_ir.children)
        omission_positions = tuple(idx for idx, child in enumerate(direct_children) if _child_is_omission(child))
        subsection_count = sum(1 for child in direct_children if _kind_str(child.kind) == IRNodeKind.SUBSECTION.value)
        has_heading = any(_kind_str(child.kind) == IRNodeKind.HEADING.value for child in direct_children)
        has_intro = any(_kind_str(child.kind) == IRNodeKind.INTRO.value for child in direct_children)

        if self.source_shape is PayloadSourceShape.EMPTY:
            raise ValueError("PayloadSurface with section_ir must not use source_shape=EMPTY")
        if self.omission_positions != omission_positions:
            raise ValueError("PayloadSurface.omission_positions must match direct omission markers")
        if self.subsection_count != subsection_count:
            raise ValueError("PayloadSurface.subsection_count must match direct subsection count")
        if self.has_heading is not has_heading:
            raise ValueError("PayloadSurface.has_heading must match direct heading presence")
        if self.has_intro is not has_intro:
            raise ValueError("PayloadSurface.has_intro must match direct intro presence")
        if "omission_head" in self.tags and (not omission_positions or omission_positions[0] != 0):
            raise ValueError("PayloadSurface.omission_head tag requires a leading omission marker")
        if "omission_tail" in self.tags and (
            not omission_positions or omission_positions[-1] != len(direct_children) - 1
        ):
            raise ValueError("PayloadSurface.omission_tail tag requires a trailing omission marker")


# ---------------------------------------------------------------------------
# Factory stubs
# ---------------------------------------------------------------------------


def _child_is_omission(node: IRNode) -> bool:
    """Return True for omission-marker nodes.

    Matches ``kind == 'omission'`` or ``hcontainer`` with ``name='omission'``.
    Deliberately mirrors ``helpers._is_omission_ir`` without importing it (this
    module must stay below the jurisdiction layer in the import graph).
    """
    if _kind_str(node.kind) == IRNodeKind.OMISSION.value:
        return True
    if _kind_str(node.kind) == IRNodeKind.HCONTAINER.value and node.attrs.get("name") == "omission":
        return True
    return False


def build_payload_surface(
    section_ir: Optional[IRNode],
    cross_ir: Optional[IRNode] = None,
    *,
    source_statute: str = "",
) -> "PayloadSurface":
    """Build a ``PayloadSurface`` from a source-normalized ``section_ir``.

    This factory derives direct-child structural facts that downstream
    elaboration needs to read from the amendment payload, without requiring
    live replay state.
    It replaces the implicit shape-inspection that previously happened inline
    inside ``prepare_payload_surface`` and ``elaborate_payload_against_live``.

    Args:
        section_ir: The IRNode produced by source-local normalization
            (``prepare_payload_surface`` output before live elaboration).
            Pass ``None`` for pure-repeal, heading-only, or cross-heading-only
            groups that carry no section body payload.
        cross_ir: The preceding ``crossHeading`` IRNode, if any.  Propagated
            unchanged into ``PayloadSurface.cross_heading_ir``.
        source_statute: Provenance string (statute ID).  Empty string if absent.

    Returns:
        A frozen ``PayloadSurface`` capturing the structural facts that
        ``elaborate_payload_against_live`` needs to read.

    """
    if section_ir is None:
        return PayloadSurface(
            section_ir=None,
            cross_heading_ir=cross_ir,
            omission_positions=(),
            subsection_count=0,
            has_heading=False,
            has_intro=False,
            source_shape=PayloadSourceShape.EMPTY,
            tags=frozenset(),
            source_statute=source_statute,
        )

    # Scan children to derive structural facts
    omission_positions: List[int] = []
    subsection_count = 0
    has_heading = False
    has_intro = False
    has_table = False
    has_liite = False

    for idx, child in enumerate(section_ir.children):
        kind = _kind_str(child.kind)
        if _child_is_omission(child):
            omission_positions.append(idx)
        elif kind == IRNodeKind.SUBSECTION.value:
            subsection_count += 1
        elif kind == IRNodeKind.HEADING.value:
            has_heading = True
        elif kind == IRNodeKind.INTRO.value:
            has_intro = True
        elif kind == IRNodeKind.TABLE.value:
            has_table = True
        elif kind == IRNodeKind.APPENDIX.value:
            has_liite = True

    # Derive coarse source_shape
    if subsection_count > 1 or (subsection_count >= 1 and omission_positions):
        source_shape = PayloadSourceShape.SPARSE_SUBSECTIONS
    elif subsection_count == 1 and not omission_positions:
        source_shape = PayloadSourceShape.SINGLE_SUBSECTION
    elif subsection_count == 0 and not omission_positions:
        # Check whether only item/paragraph children are present
        non_structural = [
            c
            for c in section_ir.children
            if _kind_str(c.kind) not in (IRNodeKind.HEADING.value, IRNodeKind.INTRO.value)
        ]
        if non_structural and all(
            _kind_str(c.kind) in (IRNodeKind.PARAGRAPH.value, IRNodeKind.ITEM.value)
            for c in non_structural
        ):
            source_shape = PayloadSourceShape.ITEMS_ONLY
        else:
            source_shape = PayloadSourceShape.WHOLE_SECTION
    else:
        source_shape = PayloadSourceShape.WHOLE_SECTION

    # Build tags
    tags: set = set()
    if has_table:
        tags.add("has_table")
    if has_liite:
        tags.add("has_liite")
    if omission_positions and omission_positions[0] == 0:
        tags.add("omission_head")
    if omission_positions and omission_positions[-1] == len(section_ir.children) - 1:
        tags.add("omission_tail")

    return PayloadSurface(
        section_ir=section_ir,
        cross_heading_ir=cross_ir,
        omission_positions=tuple(omission_positions),
        subsection_count=subsection_count,
        has_heading=has_heading,
        has_intro=has_intro,
        source_shape=source_shape,
        tags=frozenset(tags),
        source_statute=source_statute,
    )


def build_group_surface(
    body_ir: Optional[IRNode],
    cross_heading_ir: Optional[IRNode],
    source_statute: str,
    target_unit_kind: TargetUnitKind,
    target_norm: str,
    target_chapter: Optional[str],
) -> GroupSurface:
    """Build a ``GroupSurface`` from the raw Stage-1 extraction outputs.

    This is a **stub factory** — not wired into the pipeline yet.  It exists
    to define the construction API so callers can be migrated incrementally.

    Args:
        body_ir: IRNode parsed from the amendment body XML.
        cross_heading_ir: IRNode for a preceding crossHeading element, or ``None``.
        source_statute: Source statute ID string (empty string if absent).
        target_unit_kind: Amendment target unit kind in neutral shared vocabulary.
        target_norm: Normalised target label.
        target_chapter: Enclosing chapter label, or ``None``.

    Returns:
        A ``GroupSurface`` capturing the raw Stage-1 extraction.
    """
    return GroupSurface(
        body_ir=body_ir,
        cross_heading_ir=cross_heading_ir,
        source_statute=source_statute,
        target_unit_kind=target_unit_kind,
        target_norm=target_norm,
        target_chapter=target_chapter,
    )

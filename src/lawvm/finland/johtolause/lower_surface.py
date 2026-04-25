"""lower_surface — Bridge from Phase 3 SurfaceClause to Finland ParsedOp.

This module provides backward-compatible lowering from the new typed
SurfaceClause model (surface_model.py) to the legacy ParsedOp
representation.  This enables incremental migration: parser rules can
start emitting SurfaceNode types while the rest of the pipeline still
consumes ParsedOps.

Architecture:

    SurfaceClause (surface_model.py)
        -> lower_surface_clause_to_parsed_ops()
        -> list[ParsedOp]

The lowering is intentionally lossless within the ParsedOp capability:
every field that can be represented in ParsedOp is carried through.
Information that ParsedOp cannot represent (e.g. SurfaceHeadingPlacement
heading_text, SurfaceMetaClause) is dropped with a note in the op's
notes tuple.

Once the full pipeline migrates to SurfaceClause -> ClauseAST, this
bridge becomes unnecessary and can be deleted.
"""

from __future__ import annotations

from lawvm.core.parse_witness import ParseWitness
from lawvm.core.semantic_types import FacetKind
from lawvm.finland.johtolause.surface_model import (
    ScopeKind,
    SurfaceBackRef,
    SurfaceClause,
    SurfaceDescendantCoordination,
    SurfaceHeadingPlacement,
    SurfaceInsertion,
    SurfaceMetaClause,
    SurfaceMoveTail,
    SurfaceNode,
    SurfaceRenumberTail,
    SurfaceScopeBlock,
    SurfaceSubRef,
    SurfaceTargetRef,
    SurfaceTextAmend,
    TargetKind,
    SurfaceValiotsikkoRef,
    SurfaceVerbGroup,
    SurfaceWitness,
)
from lawvm.finland.johtolause.types import ParsedOp


# ---------------------------------------------------------------------------
# Witness conversion
# ---------------------------------------------------------------------------


def _to_parse_witness(w: SurfaceWitness | None) -> ParseWitness | None:
    """Convert a SurfaceWitness to a ParseWitness, or None."""
    if w is None:
        return None
    return ParseWitness(rule_id=w.rule_id, source_span=w.source_span)



# ---------------------------------------------------------------------------
# Facet to special mapping for the Finland ParsedOp bridge
# ---------------------------------------------------------------------------


def _facet_to_special(facet: FacetKind | None) -> str:
    """Map FacetKind to the Finland ParsedOp special string.

    Mapping:
        HEADING -> "otsikko"
        INTRO -> "johd"
        NONE or None -> ""
    """
    if facet is None or facet == FacetKind.NONE:
        return ""
    if facet == FacetKind.HEADING:
        return "otsikko"
    if facet == FacetKind.INTRO:
        return "johd"
    if facet == FacetKind.WHOLE_ACT:
        return ""
    return ""


# ---------------------------------------------------------------------------
# Node lowering: each SurfaceNode type -> list[ParsedOp]
# ---------------------------------------------------------------------------


def _lower_target_ref(node: SurfaceTargetRef, verb: str) -> list[ParsedOp]:
    """Lower a SurfaceTargetRef to ParsedOp(s).

    If the node has sub_refs, one ParsedOp is emitted per sub_ref.
    If no sub_refs, one whole-target ParsedOp is emitted.
    """
    kind = node.kind.value
    witness = _to_parse_witness(node.witness)
    chapter = "" if node.kind == TargetKind.CHAPTER else node.chapter
    part = node.part
    renumber_dest = node.renumber_dest
    renumber_dest_chapter = node.renumber_dest_chapter
    renumber_dest_part = node.renumber_dest_part
    facet_only_subrefs = bool(node.sub_refs) and all(
        sr.facet and not sr.momentti and not sr.item for sr in node.sub_refs
    )

    # Preserve the legacy ParsedOp encoding for whole chapter/part targets.
    # The direct PEG path stores the chapter/part label in the container field
    # when a chapter/part is the actual target, and also mirrors it into the
    # renumber destination fields for heading/renumber families.
    if node.kind == TargetKind.CHAPTER:
        legacy_chapter = node.renumber_dest_chapter or node.renumber_dest or node.label
        # Facet-only chapter headings/intro keep the legacy whole-chapter code
        # shape. Structural chapter targets already scoped under a part keep
        # the legacy "part + number" shape.
        if not facet_only_subrefs and (
            (node.sub_refs and not node.part) or node.renumber_dest or node.renumber_dest_chapter
        ):
            renumber_dest = legacy_chapter
            renumber_dest_chapter = legacy_chapter
    elif node.kind == TargetKind.PART:
        legacy_part = node.renumber_dest_part or node.renumber_dest or node.label
        if not facet_only_subrefs and (node.sub_refs or node.renumber_dest or node.renumber_dest_part):
            part = legacy_part
            renumber_dest = legacy_part
            renumber_dest_part = legacy_part

    if not node.sub_refs:
        op = ParsedOp(
            verb=verb,
            kind=kind,
            chapter=chapter,
            number=node.label,
            momentti=0,
            item="",
            special="",
            raw="",
            part=part,
            notes=node.notes,
            renumber_dest=renumber_dest,
            renumber_dest_chapter=renumber_dest_chapter,
            renumber_dest_part=renumber_dest_part,
            witness=witness,
            move_clause_target_unit_kind=node.move_clause_target_unit_kind,
        )
        op.raw = op.code()
        return [op]

    ops: list[ParsedOp] = []
    for sr in node.sub_refs:
        special = _facet_to_special(sr.facet)
        op = ParsedOp(
            verb=verb,
            kind=kind,
            chapter=chapter,
            number=node.label,
            momentti=sr.momentti,
            item=sr.item,
            special=special,
            facet=sr.facet,
            raw="",
            part=part,
            notes=node.notes,
            renumber_dest=renumber_dest,
            renumber_dest_chapter=renumber_dest_chapter,
            renumber_dest_part=renumber_dest_part,
            witness=witness,
            move_clause_target_unit_kind=node.move_clause_target_unit_kind,
        )
        op.raw = op.code()
        ops.append(op)
    return ops


def _lower_scope_block(node: SurfaceScopeBlock, verb: str) -> list[ParsedOp]:
    """Lower a SurfaceScopeBlock by lowering enclosed targets with scope applied."""
    ops: list[ParsedOp] = []
    for target in node.targets:
        if not isinstance(target, SurfaceTargetRef):
            continue
        # Apply scope context to the target
        chapter = target.chapter
        part = target.part
        if node.scope_kind == ScopeKind.CHAPTER and not chapter:
            chapter = node.scope_label
        elif node.scope_kind == ScopeKind.PART and not part:
            part = node.scope_label

        scoped = SurfaceTargetRef(
            kind=target.kind,
            label=target.label,
            chapter=chapter,
            part=part,
            sub_refs=target.sub_refs,
            notes=target.notes,
            renumber_dest=target.renumber_dest,
            renumber_dest_chapter=target.renumber_dest_chapter,
            renumber_dest_part=target.renumber_dest_part,
            witness=target.witness,
            move_clause_target_unit_kind=target.move_clause_target_unit_kind,
        )
        ops.extend(_lower_target_ref(scoped, verb))
    return ops


def _lower_insertion(node: SurfaceInsertion, verb: str) -> list[ParsedOp]:
    """Lower a SurfaceInsertion to a ParsedOp."""
    kind = node.kind.value
    witness = _to_parse_witness(node.witness)

    momentti = 0
    item = ""
    special = ""
    facet: FacetKind | None = None
    if node.sub_target is not None:
        momentti = node.sub_target.momentti
        item = node.sub_target.item
        facet = node.sub_target.facet
        special = _facet_to_special(facet)

    op = ParsedOp(
        verb=verb,
        kind=kind,
        chapter=node.chapter,
        number=node.label,
        momentti=momentti,
        item=item,
        special=special,
        facet=facet,
        raw="",
        part=node.part,
        witness=witness,
        move_clause_target_unit_kind=None,
    )
    op.raw = op.code()
    return [op]


def _lower_back_ref(node: SurfaceBackRef, verb: str) -> list[ParsedOp]:
    """Lower a SurfaceBackRef.

    BackRefs are unresolved at this stage.  The lowering emits placeholder
    ops with a "backref_unresolved" note.  In practice, backrefs should be
    resolved before lowering (by the clause_surface resolver), so this is
    a safety net.
    """
    # Emit empty ops with notes — these should not appear in normal flow
    # since backrefs are resolved by the resolver before lowering.
    ops: list[ParsedOp] = []
    for sr in node.sub_refs or (SurfaceSubRef(),):
        special = _facet_to_special(sr.facet)
        op = ParsedOp(
            verb=verb,
            kind="P",
            chapter="",
            number="",
            momentti=sr.momentti,
            item=sr.item,
            special=special,
            facet=sr.facet,
            raw="",
            notes=("backref_unresolved", f"referent_type={node.referent_type}"),
            witness=_to_parse_witness(node.witness),
            move_clause_target_unit_kind=None,
        )
        op.raw = op.code()
        ops.append(op)
    return ops


def _lower_heading_placement(node: SurfaceHeadingPlacement, verb: str) -> list[ParsedOp]:
    """Lower a SurfaceHeadingPlacement to a heading op."""
    op = ParsedOp(
        verb=verb,
        kind="P",
        chapter=node.chapter,
        number=node.target_section,
        momentti=0,
        item="",
        special="otsikko",
        facet=FacetKind.HEADING,
        raw="",
        part=node.part,
        witness=_to_parse_witness(node.witness),
        move_clause_target_unit_kind=None,
    )
    op.raw = op.code()
    return [op]


def _lower_meta_clause(_node: SurfaceMetaClause, _verb: str) -> list[ParsedOp]:
    """Lower a SurfaceMetaClause.

    Meta clauses have no ParsedOp representation.  Return empty.
    """
    return []


def _lower_text_amend(_node: SurfaceTextAmend, _verb: str) -> list[ParsedOp]:
    """Lower a SurfaceTextAmend.

    Text amendments have no direct ParsedOp representation yet.
    Return empty.
    """
    return []


def _lower_valio_ref(_node: SurfaceValiotsikkoRef, _verb: str) -> list[ParsedOp]:
    """Lower a SurfaceValiotsikkoRef.

    Valio refs are unresolved at this stage and handled by the resolver.
    Return empty.
    """
    return []


def _lower_descendant_coordination(
    node: SurfaceDescendantCoordination,
    verb: str,
) -> list[ParsedOp]:
    """Lower a SurfaceDescendantCoordination by expanding base + arms."""
    kind = node.base.kind.value
    witness = _to_parse_witness(node.witness or node.base.witness)

    ops: list[ParsedOp] = []
    for sr in node.arms:
        special = _facet_to_special(sr.facet)
        op = ParsedOp(
            verb=verb,
            kind=kind,
            chapter=node.base.chapter,
            number=node.base.label,
            momentti=sr.momentti,
            item=sr.item,
            special=special,
            facet=sr.facet,
            raw="",
            part=node.base.part,
            notes=node.base.notes,
            witness=witness,
            move_clause_target_unit_kind=node.base.move_clause_target_unit_kind,
        )
        op.raw = op.code()
        ops.append(op)
    return ops


# ---------------------------------------------------------------------------
# Node dispatch
# ---------------------------------------------------------------------------


def _lower_node(node: SurfaceNode, verb: str) -> list[ParsedOp]:
    """Lower a single SurfaceNode to ParsedOps."""
    if isinstance(node, SurfaceTargetRef):
        return _lower_target_ref(node, verb)
    if isinstance(node, SurfaceScopeBlock):
        return _lower_scope_block(node, verb)
    if isinstance(node, SurfaceInsertion):
        return _lower_insertion(node, verb)
    if isinstance(node, SurfaceBackRef):
        return _lower_back_ref(node, verb)
    if isinstance(node, SurfaceHeadingPlacement):
        return _lower_heading_placement(node, verb)
    if isinstance(node, SurfaceMetaClause):
        return _lower_meta_clause(node, verb)
    if isinstance(node, SurfaceTextAmend):
        return _lower_text_amend(node, verb)
    if isinstance(node, SurfaceValiotsikkoRef):
        return _lower_valio_ref(node, verb)
    if isinstance(node, SurfaceDescendantCoordination):
        return _lower_descendant_coordination(node, verb)
    # Unreachable for complete SurfaceNode union, but explicit for safety
    raise TypeError(f"Unknown SurfaceNode type: {type(node).__name__}")


# ---------------------------------------------------------------------------
# Verb group lowering
# ---------------------------------------------------------------------------


def _lower_verb_group(vg: SurfaceVerbGroup) -> list[ParsedOp]:
    """Lower a SurfaceVerbGroup to ParsedOps.

    Handles move tail application: when a SurfaceMoveTail follows target
    refs, the move destination is applied to the preceding batch.
    """
    # vg.verb is VerbKind enum or str
    if isinstance(vg.verb, str):
        verb = vg.verb
    else:
        verb = vg.verb.value
    ops: list[ParsedOp] = []
    # Track the last batch for move/renumber tail application
    last_batch_start = 0

    for node in vg.nodes:
        if isinstance(node, SurfaceMoveTail):
            # Apply move tail to preceding batch
            batch = ops[last_batch_start:]
            for op in batch:
                if op.typed_kind is TargetKind.SECTION and not op.momentti and not op.item and not op.facet:
                    if node.destination_chapter:
                        if not op.chapter:
                            op.chapter = node.destination_chapter
                        op.move_clause_target_unit_kind = "chapter"
                    if node.destination_part:
                        if not op.part:
                            op.part = node.destination_part
                        op.move_clause_target_unit_kind = "part"
            continue

        if isinstance(node, SurfaceRenumberTail):
            # Apply renumber tail to the last target
            if ops and last_batch_start < len(ops):
                last_op = ops[-1]
                if not last_op.renumber_dest:
                    last_op.renumber_dest = node.new_label
            continue

        batch_start = len(ops)
        node_ops = _lower_node(node, verb)
        ops.extend(node_ops)
        if node_ops:
            last_batch_start = batch_start

    return ops


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def lower_surface_clause_to_parsed_ops(clause: SurfaceClause) -> list[ParsedOp]:
    """Lower a Phase 3 SurfaceClause to a flat list of ParsedOps.

    This is the backward-compatibility bridge for incremental migration.
    Parser rules that emit SurfaceNode types can be lowered to ParsedOps
    so the rest of the pipeline (clause_surface resolver, grafter, etc.)
    continues to work unchanged.

    Args:
        clause: A SurfaceClause from the Phase 3 surface model.

    Returns:
        Flat list of ParsedOps, in verb-group order.
    """
    all_ops: list[ParsedOp] = []
    for vg in clause.verb_groups:
        all_ops.extend(_lower_verb_group(vg))
    return all_ops

"""lift_to_surface — LEGACY: Lift ParsedOps to Phase 3 SurfaceClause.

DEPRECATION NOTE:
    The parser now emits SurfaceClause natively via surface_parse.parse().
    This module is only needed for round-trip tests that verify the
    ParsedOp → SurfaceClause → ParsedOp equivalence.  No new code should
    depend on this adapter.

Phase 3b Batches 1-3: structural anchors + descendant refs + coordination.

This module provides the backward adapter:
    list[ParsedOp] -> surface_model.SurfaceClause
"""

from __future__ import annotations

from lawvm.core.parse_witness import ParseWitness
from lawvm.core.semantic_types import FacetKind
from lawvm.finland.johtolause.surface_model import (
    SurfaceClause,
    SurfaceDescendantCoordination,
    SurfaceNode,
    SurfaceSubRef,
    SurfaceTargetRef,
    SurfaceVerbGroup,
    SurfaceWitness,
    VerbKind,
)
from lawvm.finland.johtolause.types import ParsedOp


# ---------------------------------------------------------------------------
# Op anchor key — identity tuple for consolidation
# ---------------------------------------------------------------------------


def _anchor_key(op: ParsedOp) -> tuple[str, str, str, str, tuple[str, ...], str, str, str]:
    """Return the structural identity tuple for a ParsedOp.

    Two ops with the same anchor key target the same structural entity
    and differ only in sub-level targeting (momentti/item/special).
    They can be consolidated into one SurfaceTargetRef with multiple sub_refs.
    """
    return (
        op.kind,
        op.number,
        op.chapter,
        op.part,
        op.notes,
        op.renumber_dest,
        op.renumber_dest_chapter,
        op.renumber_dest_part,
    )


# ---------------------------------------------------------------------------
# Witness extraction
# ---------------------------------------------------------------------------


def _extract_witness(op: ParsedOp) -> SurfaceWitness | None:
    """Build SurfaceWitness from ParsedOp's witness if available."""
    pw = op.witness if isinstance(op.witness, ParseWitness) else None
    if pw is None:
        return None
    return SurfaceWitness(
        rule_id=pw.rule_id,
        source_span=pw.source_span,
    )


# ---------------------------------------------------------------------------
# ParsedOp -> SurfaceSubRef
# ---------------------------------------------------------------------------


def _make_sub_ref(op: ParsedOp) -> SurfaceSubRef | None:
    """Extract sub-ref from a ParsedOp, or None if whole-target."""
    if op.momentti or op.item or op.facet:
        facet: FacetKind | None = op.facet
        # Map facet to legacy special field for backward compat
        special = ""
        if facet == FacetKind.HEADING:
            special = "otsikko"
        elif facet == FacetKind.INTRO:
            special = "johd"
        return SurfaceSubRef(
            momentti=op.momentti,
            item=op.item,
            facet=facet,
            special=special,
        )
    return None


# ---------------------------------------------------------------------------
# ParsedOp -> SurfaceTargetRef (single op, no consolidation)
# ---------------------------------------------------------------------------


def _lift_single_op(op: ParsedOp) -> SurfaceTargetRef:
    """Lift one ParsedOp to a SurfaceTargetRef.

    This handles structural anchor families:
    - Section refs (kind=P)
    - Chapter refs (kind=L)
    - Part refs (kind=O)
    - Nimike refs (kind=N)
    - Appendix refs (kind=A)

    Sub-refs (momentti, item, special) are mapped to SurfaceSubRef.
    """
    kind = op.typed_kind
    sr = _make_sub_ref(op)
    sub_refs: tuple[SurfaceSubRef, ...] = (sr,) if sr is not None else ()

    return SurfaceTargetRef(
        kind=kind,
        label=op.number,
        chapter=op.chapter,
        part=op.part,
        sub_refs=sub_refs,
        notes=op.notes,
        is_exception="exception" in op.notes,
        renumber_dest=op.renumber_dest,
        renumber_dest_chapter=op.renumber_dest_chapter,
        renumber_dest_part=op.renumber_dest_part,
        witness=_extract_witness(op),
    )


# ---------------------------------------------------------------------------
# Batch 2: consolidate consecutive same-section ops into multi-sub_ref nodes
# ---------------------------------------------------------------------------


def _consolidate_ops(ops: list[ParsedOp]) -> list[SurfaceNode]:
    """Lift a list of same-verb ParsedOps to SurfaceNodes with consolidation.

    Consecutive ops targeting the same structural entity (same kind, label,
    chapter, part, notes, renumber fields) but with different sub-level
    targeting are consolidated into a single SurfaceTargetRef with multiple
    sub_refs.

    This makes the surface tree faithfully represent descendant coordination
    patterns like "7 §:n 1 ja 3 kohta" as one node with two sub_refs.

    Whole-target ops (no sub_ref) are never consolidated — they remain
    standalone nodes.
    """
    if not ops:
        return []

    nodes: list[SurfaceNode] = []
    i = 0
    while i < len(ops):
        op = ops[i]
        sr = _make_sub_ref(op)

        # Whole-target ops are always standalone
        if sr is None:
            nodes.append(_lift_single_op(op))
            i += 1
            continue

        # Look ahead: collect consecutive ops with same anchor key and sub_refs
        key = _anchor_key(op)
        sub_refs: list[SurfaceSubRef] = [sr]
        witness = _extract_witness(op)
        j = i + 1
        while j < len(ops):
            next_op = ops[j]
            next_sr = _make_sub_ref(next_op)
            if next_sr is None or _anchor_key(next_op) != key:
                break
            sub_refs.append(next_sr)
            # Widen witness span if both have source_span
            next_witness = _extract_witness(next_op)
            if witness is not None and next_witness is not None:
                if witness.source_span is not None and next_witness.source_span is not None:
                    merged_start = min(witness.source_span[0], next_witness.source_span[0])
                    merged_end = max(witness.source_span[1], next_witness.source_span[1])
                    witness = SurfaceWitness(
                        rule_id=witness.rule_id,
                        source_span=(merged_start, merged_end),
                    )
            elif next_witness is not None:
                witness = next_witness
            j += 1

        kind = op.typed_kind
        if len(sub_refs) >= 2:
            # Batch 3: coordinated descendant refs -> SurfaceDescendantCoordination
            base = SurfaceTargetRef(
                kind=kind,
                label=op.number,
                chapter=op.chapter,
                part=op.part,
                sub_refs=(),
                notes=op.notes,
                is_exception="exception" in op.notes,
                renumber_dest=op.renumber_dest,
                renumber_dest_chapter=op.renumber_dest_chapter,
                renumber_dest_part=op.renumber_dest_part,
                witness=witness,
            )
            nodes.append(
                SurfaceDescendantCoordination(
                    base=base,
                    arms=tuple(sub_refs),
                    witness=witness,
                )
            )
        else:
            # Single sub-ref: keep as SurfaceTargetRef
            nodes.append(
                SurfaceTargetRef(
                    kind=kind,
                    label=op.number,
                    chapter=op.chapter,
                    part=op.part,
                    sub_refs=tuple(sub_refs),
                    notes=op.notes,
                    is_exception="exception" in op.notes,
                    renumber_dest=op.renumber_dest,
                    renumber_dest_chapter=op.renumber_dest_chapter,
                    renumber_dest_part=op.renumber_dest_part,
                    witness=witness,
                )
            )
        i = j

    return nodes


# ---------------------------------------------------------------------------
# Group ParsedOps into verb groups
# ---------------------------------------------------------------------------


def _group_by_verb(ops: list[ParsedOp]) -> list[tuple[str, list[ParsedOp]]]:
    """Group a flat op list into consecutive runs of the same verb.

    Returns list of (verb_code, ops) pairs preserving source order.
    """
    if not ops:
        return []

    groups: list[tuple[str, list[ParsedOp]]] = []
    current_verb = ops[0].verb
    current_ops: list[ParsedOp] = [ops[0]]

    for op in ops[1:]:
        if op.verb == current_verb:
            current_ops.append(op)
        else:
            groups.append((current_verb, current_ops))
            current_verb = op.verb
            current_ops = [op]

    groups.append((current_verb, current_ops))
    return groups


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def lift_parsed_ops_to_surface_clause(
    ops: list[ParsedOp],
    source_text: str = "",
) -> SurfaceClause:
    """Lift a flat list of ParsedOps to a Phase 3 SurfaceClause.

    Groups ops by verb (preserving source order) and lifts each op to
    SurfaceNodes.  Consecutive ops targeting the same structural entity
    are consolidated into a single SurfaceTargetRef with multiple sub_refs
    (Batch 2: simple descendant refs).

    Args:
        ops: Flat list of ParsedOps from the parser pipeline.
        source_text: Original source text for diagnostics.

    Returns:
        SurfaceClause with verb groups containing SurfaceNode nodes.
    """
    verb_groups: list[SurfaceVerbGroup] = []

    for verb_code, group_ops in _group_by_verb(ops):
        verb = VerbKind.from_code(verb_code)
        nodes = tuple(_consolidate_ops(group_ops))
        verb_groups.append(SurfaceVerbGroup(verb=verb, nodes=nodes))

    return SurfaceClause(
        verb_groups=tuple(verb_groups),
        source_text=source_text,
    )


def parse_to_surface(text: str) -> SurfaceClause:
    """Parse johtolause text and return a Phase 3 SurfaceClause.

    Pipeline:
        text -> tokenize -> annotate -> parse_surface -> resolve -> lift

    This wraps the existing parser pipeline and lifts the resolved
    ParsedOps to SurfaceClause.

    Round-trip invariant:
        lower_surface_clause_to_parsed_ops(parse_to_surface(text))
        is op-code-equivalent to parse_clause(text).parsed_ops

    Args:
        text: Raw johtolause text.

    Returns:
        Phase 3 SurfaceClause.
    """
    from lawvm.finland.johtolause.api import parse_clause

    ops = parse_clause(text).parsed_ops
    return lift_parsed_ops_to_surface_clause(ops, source_text=text)

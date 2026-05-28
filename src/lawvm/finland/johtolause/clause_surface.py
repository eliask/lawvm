"""ClauseSurface — surface parse representation with unresolved references.

The parser (peg3.py) currently resolves context propagation, back-references,
and anaphoric patterns inline during parsing.  This module defines surface
parse types that can carry unresolved references, and a resolver that
converts them to resolved ParsedOps.

This is Phase 4 of the Pro PEG3 roadmap: separating surface parsing from
resolution.  The parser becomes more local (emitting what it sees in the
tokens) and the resolver becomes explicit (filling in inherited context,
resolving backrefs, expanding anaphoric patterns).

Architecture:
    tokens → parse_surface(tokens) → list[SurfaceNode]  (may have unresolved refs)
    list[SurfaceNode] → resolve(nodes) → list[ParsedOp]  (fully resolved)

    Round-trip invariant:
        resolve(parse_surface(tokens)) == parse(tokens)

    Currently only backref resolution is extracted.  Other patterns
    (chapter carry-forward, anaphoric insertions, cross-verb-group context)
    remain inline in the parser and are emitted as resolved SurfaceTarget
    nodes.  They will be extracted in subsequent steps.

SurfaceNode types:
    SurfaceTarget   — a fully parsed (possibly context-resolved) reference target
    SurfaceBackref  — an unresolved back-reference ("mainitun pykälän ...")
    SurfaceValioRef — an unresolved valio heading reference
    SurfaceVerbGroup — one verb's target list
    SurfaceClause   — the complete surface parse result
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace as _dc_replace
from typing import Optional, Tuple, Union

from lawvm.core.clause_ast import ItemShiftClause, NamedRowClause
from lawvm.core.semantic_types import FacetKind
from lawvm.core.semantic_types import StructuralAction
from lawvm.finland.johtolause.clause_patterns import (
    parse_named_table_row_mixed_clauses,
    parse_named_table_row_single_clauses,
)
from lawvm.finland.johtolause.parsed_op_clause_ast import parsed_op_to_clause_node
from lawvm.finland.johtolause.surface_model import TargetKind
from lawvm.finland.johtolause.types import ParsedOp


# ---------------------------------------------------------------------------
# Sub-reference type (shared with parser)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SubRef:
    """Parsed sub-reference: momentti, item, or special.

    Mirrors peg3.SubRef but frozen for use in immutable surface nodes.
    """

    momentti: int = 0
    item: str = ""
    special: str = ""
    facet: Optional[FacetKind] = None


# ---------------------------------------------------------------------------
# Surface node types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SurfaceTarget:
    """A batch of ops from one _target() parse call.

    A single _target() call can produce multiple ParsedOps (e.g., "5 ja 6 §"
    produces two ops).  These ops form one "batch" — backref resolution
    scopes against the last batch.

    Future extraction steps will split this into "explicit" and
    "context-resolved" variants.
    """

    ops: Tuple[ParsedOp, ...]


@dataclass(frozen=True)
class SurfaceBackref:
    """An unresolved back-reference ("mainitun pykälän 3 momentti").

    The parser recognized a BACKREF token followed by sub-references but
    did NOT look up which previous section(s) to apply them to.  The
    resolver resolves this by scanning preceding SurfaceTarget ops.

    Attributes:
        verb:         The governing verb code (M/K/L/S).
        is_singular:  True if "mainitun/mainittu" (refers to one section),
                      False if "mainittujen/mainitut" (refers to all).
        sub_refs:     Parsed sub-references (momentti, item, special).
        source_tokens: Token span in the filtered stream.
    """

    verb: str
    is_singular: bool
    sub_refs: Tuple[SubRef, ...]
    source_tokens: Optional[Tuple[int, int]] = None


@dataclass(frozen=True)
class SurfaceValioRef:
    """An unresolved valio heading back-reference.

    "sen edellä oleva väliotsikko" — refers to the heading of the
    previously mentioned section(s).  Resolved by looking at preceding
    SurfaceTarget ops and emitting heading ops for them.

    Attributes:
        verb:         The governing verb code.
        source_tokens: Token span in the filtered stream.
    """

    verb: str
    source_tokens: Optional[Tuple[int, int]] = None


SurfaceNode = Union[SurfaceTarget, SurfaceBackref, SurfaceValioRef]


@dataclass(frozen=True)
class SurfaceVerbGroup:
    """One verb's target list with its surface nodes."""

    verb: str  # TODO: replace with a neutral shared action enum
    nodes: Tuple[SurfaceNode, ...]


@dataclass(frozen=True)
class SurfaceClause:
    """Complete surface parse result.

    Contains the ordered list of verb groups as they appear in the source
    johtolause.  Some nodes may be unresolved (SurfaceBackref, SurfaceValioRef).
    """

    verb_groups: Tuple[SurfaceVerbGroup, ...]
    source_text: str = ""


# ---------------------------------------------------------------------------
# Clause-waist parsers for typed johtolause families
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ItemShiftAfterRepealClause:
    """Typed parse result for item-shift-after-repeal clause families.

    The typed `ItemShiftClause` is the owned semantic fact.  The optional extra
    repeal information is carried here so the Finland bridge can synthesize the
    `ParsedOp`/`AmendmentOp` carrier while parsing ownership lives in this module.
    """

    clause: ItemShiftClause
    extra_repeal_target_paragraph: int | None = None


def parse_item_shift_clauses(johto: str) -> list[ItemShiftClause]:
    """Parse item-shift-after-repeal clauses from johtolause text."""
    # johto is already Zs-normalized by _normalize_fi_parse_text upstream.
    text = re.sub(r"\s+", " ", johto or "").lower()
    if "jolloin" not in text or "muuttuvat kohdiksi" not in text:
        return []

    clauses: list[ItemShiftClause] = []
    for match in re.finditer(
        r"(\d+\s*[a-z]?)\s*§:n\s*(\d+)\s+momentin\s*([a-z])\s+kohdan\s*,\s*jolloin\s+kohdat\s+([a-z])\s*[–—―-]\s*([a-z])\s+muuttuvat\s+kohdiksi\s+([a-z])\s*[–—―-]\s*([a-z])",
        text,
        flags=re.I,
    ):
        sec, mom, repealed, src_lo, src_hi, dst_lo, dst_hi = match.groups()
        repealed = repealed.lower()
        src_lo = src_lo.lower()
        src_hi = src_hi.lower()
        dst_lo = dst_lo.lower()
        dst_hi = dst_hi.lower()

        if repealed != dst_lo:
            continue
        if ord(src_lo) - ord(dst_lo) != 1 or ord(src_hi) - ord(dst_hi) != 1:
            continue

        sec_norm = re.sub(r"\s+", "", sec)
        source_items = tuple(chr(c) for c in range(ord(src_lo), ord(src_hi) + 1))
        target_items = tuple(chr(c) for c in range(ord(dst_lo), ord(dst_hi) + 1))
        clauses.append(
            ItemShiftClause(
                source_items=source_items,
                target_items=target_items,
                target_paragraph=int(mom),
                target_section=sec_norm,
            )
        )
    return clauses


def parse_item_shift_after_repeal_clauses(johto: str) -> list[ItemShiftAfterRepealClause]:
    """Parse item-shift clauses that also carry a trailing repeal target."""
    # johto is already Zs-normalized by _normalize_fi_parse_text upstream.
    text = re.sub(r"\s+", " ", johto or "").lower()
    if "jolloin" not in text or "muuttuvat kohdiksi" not in text:
        return []

    results: list[ItemShiftAfterRepealClause] = []
    for match in re.finditer(
        r"(\d+\s*[a-z]?)\s*§:n\s*(\d+)\s+momentin\s*([a-z])\s+kohdan\s*,\s*jolloin\s+kohdat\s+([a-z])\s*[–—―-]\s*([a-z])\s+muuttuvat\s+kohdiksi\s+([a-z])\s*[–—―-]\s*([a-z])\s+ja\s+(\d+)\s+momentin\s*,\s*muutetaan",
        text,
        flags=re.I,
    ):
        sec, repeal_mom, _repealed, src_lo, src_hi, dst_lo, dst_hi, extra_mom = match.groups()
        src_lo = src_lo.lower()
        src_hi = src_hi.lower()
        dst_lo = dst_lo.lower()
        dst_hi = dst_hi.lower()
        if ord(src_lo) - ord(dst_lo) != 1 or ord(src_hi) - ord(dst_hi) != 1:
            continue

        sec_norm = re.sub(r"\s+", "", sec)
        source_items = tuple(chr(c) for c in range(ord(src_lo), ord(src_hi) + 1))
        target_items = tuple(chr(c) for c in range(ord(dst_lo), ord(dst_hi) + 1))
        results.append(
            ItemShiftAfterRepealClause(
                clause=ItemShiftClause(
                    source_items=source_items,
                    target_items=target_items,
                    target_paragraph=int(repeal_mom),
                    target_section=sec_norm,
                ),
                extra_repeal_target_paragraph=int(extra_mom),
            )
        )
    return results


def parse_named_row_clauses(johto: str) -> list[NamedRowClause]:
    """Parse named-row table clauses from johtolause text at the clause waist."""
    clauses: list[NamedRowClause] = []

    mixed = parse_named_table_row_mixed_clauses(johto)
    for clause in mixed:
        sec_norm = clause.section
        repeal_rows = clause.repeal_rows.targets
        replace_rows = clause.replace_rows.targets
        if repeal_rows:
            clauses.append(
                NamedRowClause(
                    action=StructuralAction.REPEAL,
                    named_targets=tuple(repeal_rows),
                    target_section=sec_norm,
                )
            )
        if replace_rows:
            clauses.append(
                NamedRowClause(
                    action=StructuralAction.REPLACE,
                    named_targets=tuple(replace_rows),
                    target_section=sec_norm,
                )
            )

    single = parse_named_table_row_single_clauses(johto)
    for clause in single:
        action_enum = StructuralAction(clause.action)
        clauses.append(
            NamedRowClause(
                action=action_enum,
                named_targets=tuple(clause.rows.targets),
                target_section=clause.section,
            )
        )

    return clauses


# ---------------------------------------------------------------------------
# Resolver: SurfaceClause → list[ParsedOp]
# ---------------------------------------------------------------------------


def resolve(clause: SurfaceClause) -> list[ParsedOp]:
    """Resolve all unresolved surface nodes to concrete ParsedOps.

    Walks verb groups in order, maintaining resolution context.  For each
    SurfaceTarget, the op passes through.  For each SurfaceBackref/
    SurfaceValioRef, the resolver looks up preceding targets and expands
    the reference.

    The resolver processes nodes sequentially because each resolution
    affects the context for subsequent nodes (e.g., resolved backref ops
    become the "last target batch" for the next backref).
    """
    all_ops: list[ParsedOp] = []
    # Track the last batch of ops for backref scoping.
    # last_batch_start is the index into the combined (all_ops + vg_ops) list
    # where the last SurfaceTarget batch begins.  Backrefs resolve against
    # ops from last_batch_start to current end.
    last_batch_start = 0
    last_batch_count = 0  # number of ops in last batch
    # Track chapter/part context across verb groups
    chapter = ""
    part = ""

    for vg in clause.verb_groups:
        vg_ops: list[ParsedOp] = []

        for node in vg.nodes:
            if isinstance(node, SurfaceTarget):
                batch_start = len(all_ops) + len(vg_ops)
                vg_ops.extend(node.ops)
                last_batch_start = batch_start
                last_batch_count = len(node.ops)

            elif isinstance(node, SurfaceBackref):
                resolved = _resolve_backref(
                    node,
                    all_ops + vg_ops,
                    last_batch_start,
                    last_batch_count,
                    chapter,
                )
                vg_ops.extend(resolved)
                if resolved:
                    last_batch_start = len(all_ops) + len(vg_ops) - len(resolved)
                    last_batch_count = len(resolved)

            elif isinstance(node, SurfaceValioRef):
                resolved = _resolve_valio_ref(
                    node,
                    all_ops + vg_ops,
                    last_batch_start,
                    last_batch_count,
                    chapter,
                    part,
                )
                vg_ops.extend(resolved)
                if resolved:
                    last_batch_start = len(all_ops) + len(vg_ops) - len(resolved)
                    last_batch_count = len(resolved)

        # Update cross-group context
        chapter = _extract_chapter_from_ops(vg_ops, chapter)
        part = _extract_part_from_ops(vg_ops, part)
        all_ops.extend(vg_ops)

    return all_ops


def _resolve_backref(
    backref: SurfaceBackref,
    preceding_ops: list[ParsedOp],
    last_batch_start: int,
    last_batch_count: int,
    default_chapter: str,
) -> list[ParsedOp]:
    """Resolve a SurfaceBackref against preceding ops.

    Scans the last target batch (last_batch_count ops starting at
    last_batch_start) for section ops.  For singular backrefs, takes
    only the last section.  For plural, takes all unique sections.

    Returns resolved ParsedOps.
    """
    # Find sections in the last batch
    batch = preceding_ops[last_batch_start : last_batch_start + last_batch_count]
    prev_sections: list[tuple[str, str]] = []  # (number, chapter)
    seen: set[str] = set()
    for prev_op in reversed(batch):
        if prev_op.typed_kind is TargetKind.SECTION and prev_op.number and prev_op.number not in seen:
            seen.add(prev_op.number)
            prev_sections.append((prev_op.number, prev_op.chapter or default_chapter))
            if backref.is_singular:
                break

    if not prev_sections:
        return []

    ops: list[ParsedOp] = []
    for sec_num, sec_ch in prev_sections:
        for sr in backref.sub_refs:
            # Translate SubRef.special → facet + clear momentti for heading/intro refs
            facet = sr.facet
            momentti = sr.momentti
            special = sr.special
            if sr.special == "otsikko" and facet is None:
                facet = FacetKind.HEADING
                momentti = 0
            elif sr.special == "johd" and facet is None:
                facet = FacetKind.INTRO
                momentti = 0
            op = ParsedOp(
                verb=backref.verb,
                kind="P",
                chapter=sec_ch,
                number=sec_num,
                momentti=momentti,
                item=sr.item,
                facet=facet,
                special=special,
                raw="",
            )
            op.raw = op.code()
            ops.append(op)
    return ops


def _resolve_valio_ref(
    valio: SurfaceValioRef,
    preceding_ops: list[ParsedOp],
    last_batch_start: int,
    last_batch_count: int,
    default_chapter: str,
    default_part: str,
) -> list[ParsedOp]:
    """Resolve a SurfaceValioRef to heading ops for preceding section(s)."""
    batch = preceding_ops[last_batch_start : last_batch_start + last_batch_count]
    ops: list[ParsedOp] = []
    seen: set[str] = set()
    for prev_op in reversed(batch):
        if prev_op.typed_kind is TargetKind.SECTION and prev_op.number and prev_op.number not in seen:
            seen.add(prev_op.number)
            op = ParsedOp(
                verb=valio.verb,
                kind="P",
                chapter=prev_op.chapter or default_chapter,
                number=prev_op.number,
                momentti=0,
                item="",
                special="otsikko",
                facet=FacetKind.HEADING,
                raw="",
                part=prev_op.part or default_part,
            )
            op.raw = op.code()
            ops.append(op)
    return ops


# ---------------------------------------------------------------------------
# Context helpers (mirrors peg3 helpers)
# ---------------------------------------------------------------------------


def _extract_chapter_from_ops(ops: list[ParsedOp], current: str) -> str:
    """Extract chapter context from ops for cross-group propagation."""
    for op in reversed(ops):
        if op.typed_kind is TargetKind.CHAPTER and op.number and not op.facet and op.verb not in ("K", "L"):
            return op.number
        if op.typed_kind is TargetKind.CHAPTER and op.number and op.facet:
            return ""
        if op.chapter:
            return op.chapter
    return current


def _extract_part_from_ops(ops: list[ParsedOp], current: str) -> str:
    """Extract part context from ops for cross-group propagation."""
    for op in reversed(ops):
        if op.part:
            return op.part
    return current


def resolve_grouped(clause: SurfaceClause) -> list[list[ParsedOp]]:
    """Resolve SurfaceClause, returning per-verb-group op lists.

    Same resolution logic as resolve(), but preserves verb group boundaries
    instead of flattening to a single list.
    """
    all_ops: list[ParsedOp] = []
    result: list[list[ParsedOp]] = []
    last_batch_start = 0
    last_batch_count = 0
    chapter = ""
    part = ""

    for vg in clause.verb_groups:
        vg_ops: list[ParsedOp] = []

        for node in vg.nodes:
            if isinstance(node, SurfaceTarget):
                batch_start = len(all_ops) + len(vg_ops)
                vg_ops.extend(node.ops)
                last_batch_start = batch_start
                last_batch_count = len(node.ops)

            elif isinstance(node, SurfaceBackref):
                resolved = _resolve_backref(
                    node,
                    all_ops + vg_ops,
                    last_batch_start,
                    last_batch_count,
                    chapter,
                )
                vg_ops.extend(resolved)
                if resolved:
                    last_batch_start = len(all_ops) + len(vg_ops) - len(resolved)
                    last_batch_count = len(resolved)

            elif isinstance(node, SurfaceValioRef):
                resolved = _resolve_valio_ref(
                    node,
                    all_ops + vg_ops,
                    last_batch_start,
                    last_batch_count,
                    chapter,
                    part,
                )
                vg_ops.extend(resolved)
                if resolved:
                    last_batch_start = len(all_ops) + len(vg_ops) - len(resolved)
                    last_batch_count = len(resolved)

        chapter = _extract_chapter_from_ops(vg_ops, chapter)
        part = _extract_part_from_ops(vg_ops, part)
        all_ops.extend(vg_ops)
        result.append(vg_ops)

    return result


# ---------------------------------------------------------------------------
# lower_to_ast: SurfaceClause → ClauseAST (Phase 5)
# ---------------------------------------------------------------------------


def lower_to_ast(clause: SurfaceClause):
    """Lower a SurfaceClause to a ClauseAST, resolving backrefs inline.

    Unlike build_clause_ast (which reconstructs verb groups by partitioning
    a flat op list by verb code, merging same-verb groups), this function
    preserves the actual verb group boundaries from the parser.  This means:

    - Source order is preserved exactly
    - Same-verb groups that are syntactically separate stay separate
    - ScopedBlocks are built from consecutive same-chapter ops within each
      verb group (matching build_clause_ast's chapter grouping logic)

    Round-trip invariant (at the LegalOperation level):
        ops from clause_ast_to_legal_ops(lower_to_ast(clause))
        are op-code-equivalent to resolve(clause)

    Returns:
        ClauseAST instance.
    """
    from lawvm.core.clause_ast import (
        ClauseAST,
        VerbGroup,
    )
    from lawvm.core.semantic_types import StructuralAction

    _VERB_TO_ACTION = {
        "M": StructuralAction.REPLACE,
        "K": StructuralAction.REPEAL,
        "L": StructuralAction.INSERT,
        "S": StructuralAction.RENUMBER,
        "META": StructuralAction.META,
    }

    grouped = resolve_grouped(clause)
    verb_groups: list[VerbGroup] = []

    for vg, vg_ops in zip(clause.verb_groups, grouped, strict=True):
        verb_enum = _VERB_TO_ACTION.get(vg.verb, StructuralAction.REPLACE)
        # Group consecutive same-chapter ops into ScopedBlocks
        nodes = _group_ops_by_chapter_for_ast(vg_ops)
        verb_groups.append(VerbGroup(verb=verb_enum, nodes=tuple(nodes)))

    return ClauseAST(source_text=clause.source_text, verb_groups=tuple(verb_groups))


def _group_ops_by_chapter_for_ast(ops: list[ParsedOp]) -> list:
    """Group consecutive ops by chapter into ScopedBlocks / bare nodes.

    Mirrors clause_ast._group_by_chapter but operates on ParsedOps directly.
    """
    from lawvm.core.clause_ast import ScopedBlock, ClauseNode
    from lawvm.core.ir import LegalAddress

    result: list[ClauseNode] = []
    i = 0
    while i < len(ops):
        chapter = ops[i].chapter
        if not chapter:
            result.append(parsed_op_to_clause_node(ops[i]))
            i += 1
            continue
        j = i
        while j < len(ops) and ops[j].chapter == chapter:
            j += 1
        scoped_parts = {ops[k].part for k in range(i, j) if ops[k].part}
        scope_path: list[tuple[str, str]] = []
        scope_part = ""
        if len(scoped_parts) == 1:
            scope_part = next(iter(scoped_parts))
            scope_path.append(("part", scope_part))
        scope_path.append(("chapter", chapter))
        # Strip part context from children only when it is represented by the
        # scope boundary; otherwise preserve per-target part context.
        children = [
            parsed_op_to_clause_node(_dc_replace(ops[k], part=""))
            if scope_part and ops[k].part == scope_part
            else parsed_op_to_clause_node(ops[k])
            for k in range(i, j)
        ]
        scope = LegalAddress(path=tuple(scope_path))
        result.append(ScopedBlock(scope=scope, children=tuple(children)))
        i = j
    return result


# ---------------------------------------------------------------------------
# parse_surface: build SurfaceClause from parser instrumentation
# ---------------------------------------------------------------------------


def parse_surface(tokens: list) -> SurfaceClause:
    """Parse tokens into a SurfaceClause with unresolved references.

    Converts surface_model node types to clause_surface node types:
    - SurfaceTargetRef and other structural nodes -> lower to ParsedOps,
      wrap in SurfaceTarget
    - SurfaceBackRef -> SurfaceBackref (clause_surface type)
    - SurfaceValiotsikkoRef -> SurfaceValioRef (clause_surface type)
    - SurfaceMoveTail / SurfaceRenumberTail -> applied to preceding batch
      by including in the immediately preceding SurfaceTarget

    Round-trip invariant:
        resolve(parse_surface(tokens)) == parse(tokens)

    Args:
        tokens: Filtered token list (output of apply_annotations).

    Returns:
        SurfaceClause with unresolved backref/valio nodes.
    """
    from lawvm.finland.johtolause.surface_parse import parse as _parse
    from lawvm.finland.johtolause.surface_model import (
        SurfaceBackRef,
        SurfaceCrossVerbMoveTail,
        SurfaceMoveTail,
        SurfaceRelabelFromContext,
        SurfaceRenumberTail,
        SurfaceValiotsikkoRef,
    )
    from lawvm.finland.johtolause.lower_surface import _lower_node

    parsed = _parse(tokens)
    # Use a mutable intermediate: list of (verb_str, list[SurfaceNode])
    # so cross-verb nodes can patch prior verb groups' ParsedOps.
    # We collect ParsedOps directly into pending_ops before wrapping them.
    # Each entry is (verb, [SurfaceNode ...]) where SurfaceTarget.ops
    # contain mutable ParsedOps.
    result_vg_pairs: list[tuple[str, list[SurfaceNode]]] = []

    # Track last section for SurfaceRelabelFromContext resolution
    last_section: str = ""
    last_section_chapter: str = ""

    for vg in parsed.verb_groups:
        verb = vg.verb.value  # e.g. "M", "K", "L", "S"
        result_nodes: list[SurfaceNode] = []

        # Tracks the current batch of ParsedOps being accumulated for a SurfaceTarget
        pending_ops: list[ParsedOp] = []

        for node in vg.nodes:
            if isinstance(node, SurfaceBackRef):
                # Flush pending ops as a SurfaceTarget before the backref
                if pending_ops:
                    result_nodes.append(SurfaceTarget(ops=tuple(pending_ops)))
                    # Update last section context from flushed ops
                    for op in pending_ops:
                        if op.typed_kind is TargetKind.SECTION and op.number:
                            last_section = op.number
                            last_section_chapter = op.chapter
                    pending_ops = []
                is_singular = (node.referent_type.value == "singular")
                cs_sub_refs = tuple(
                    SubRef(momentti=sr.momentti, item=sr.item, special=sr.special, facet=sr.facet)
                    for sr in node.sub_refs
                )
                result_nodes.append(SurfaceBackref(
                    verb=verb,
                    is_singular=is_singular,
                    sub_refs=cs_sub_refs,
                ))
            elif isinstance(node, SurfaceValiotsikkoRef):
                # Flush pending ops as a SurfaceTarget before the valio ref
                if pending_ops:
                    result_nodes.append(SurfaceTarget(ops=tuple(pending_ops)))
                    for op in pending_ops:
                        if op.typed_kind is TargetKind.SECTION and op.number:
                            last_section = op.number
                            last_section_chapter = op.chapter
                    pending_ops = []
                result_nodes.append(SurfaceValioRef(verb=verb))
            elif isinstance(node, SurfaceMoveTail):
                # Apply move tail semantics directly to pending ops
                for op in pending_ops:
                    if op.typed_kind is TargetKind.SECTION and not op.momentti and not op.item and not op.facet:
                        if node.move_clause_target_unit_kind == "chapter" and node.destination_chapter:
                            if not op.chapter:
                                op.chapter = node.destination_chapter
                            op.move_clause_target_unit_kind = "chapter"
                        if node.move_clause_target_unit_kind == "part" and node.destination_part:
                            if not op.part:
                                op.part = node.destination_part
                            op.move_clause_target_unit_kind = "part"
            elif isinstance(node, SurfaceRenumberTail):
                if pending_ops:
                    last_op = pending_ops[-1]
                    if not last_op.renumber_dest:
                        last_op.renumber_dest = node.new_label
            elif isinstance(node, SurfaceCrossVerbMoveTail):
                # Flush current pending ops first
                if pending_ops:
                    result_nodes.append(SurfaceTarget(ops=tuple(pending_ops)))
                    for op in pending_ops:
                        if op.typed_kind is TargetKind.SECTION and op.number:
                            last_section = op.number
                            last_section_chapter = op.chapter
                    pending_ops = []
                # Patch prior verb groups in reverse: find matching section ops
                src_label = node.source_section_label
                patched_vg = False
                for prior_verb, prior_nodes in reversed(result_vg_pairs):
                    for prior_node in prior_nodes:
                        if not isinstance(prior_node, SurfaceTarget):
                            continue
                        for op in prior_node.ops:
                            if (
                                op.typed_kind is TargetKind.SECTION
                                and op.number == src_label
                                and not op.momentti
                                and not op.item
                                and not op.facet
                            ):
                                if node.move_clause_target_unit_kind == "chapter" and node.destination_chapter:
                                    if not op.chapter:
                                        op.chapter = node.destination_chapter
                                    op.move_clause_target_unit_kind = "chapter"
                                if node.move_clause_target_unit_kind == "part" and node.destination_part:
                                    if not op.part:
                                        op.part = node.destination_part
                                    op.move_clause_target_unit_kind = "part"
                                patched_vg = True
                    if patched_vg:
                        break
            elif isinstance(node, SurfaceRelabelFromContext):
                # Flush current pending ops first
                if pending_ops:
                    result_nodes.append(SurfaceTarget(ops=tuple(pending_ops)))
                    for op in pending_ops:
                        if op.typed_kind is TargetKind.SECTION and op.number:
                            last_section = op.number
                            last_section_chapter = op.chapter
                    pending_ops = []
                # Create a relabel op using context from preceding sections
                if last_section:
                    dest_chapter = node.destination_chapter or last_section_chapter
                    src_chapter = last_section_chapter or node.destination_chapter
                    relabel_op = ParsedOp(
                        verb=verb,
                        kind="P",
                        chapter=src_chapter,
                        number=last_section,
                        momentti=0,
                        item="",
                        raw="",
                        renumber_dest=node.destination_label,
                        renumber_dest_chapter=dest_chapter,
                    )
                    result_nodes.append(SurfaceTarget(ops=(relabel_op,)))
            else:
                # Lower normal structural node to ParsedOps and accumulate
                node_ops = _lower_node(node, verb)
                pending_ops.extend(node_ops)

        # Flush any remaining pending ops
        if pending_ops:
            result_nodes.append(SurfaceTarget(ops=tuple(pending_ops)))
            for op in pending_ops:
                if op.typed_kind is TargetKind.SECTION and op.number:
                    last_section = op.number
                    last_section_chapter = op.chapter

        result_vg_pairs.append((verb, result_nodes))

    result_vgs = [
        SurfaceVerbGroup(verb=verb, nodes=tuple(nodes))
        for verb, nodes in result_vg_pairs
    ]
    return SurfaceClause(
        verb_groups=tuple(result_vgs),
        source_text=parsed.source_text,
    )

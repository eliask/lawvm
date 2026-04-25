"""lower_clause_ast — Phase 5: ResolvedSurfaceClause -> ClauseAST.

Native lowering path that produces ClauseAST directly from the resolved
surface clause, bypassing the ParsedOp intermediary entirely.

Architecture (PRO_FI_PEG_VPRI_2026-04-07.md, Phase 5):

    ResolvedSurfaceClause (surface_resolve.py)
        -> lower_to_clause_ast()
        -> ClauseAST

This is the public parse waist milestone: Finland frontend exports ClauseAST
natively rather than through the ParsedOp bridge.

The legacy ParsedOp bridge (parsed_op_to_clause_node / build_clause_ast) is
NOT modified — it remains for backward compatibility and round-trip tests.

Design rules:
1. ClauseAST is the ONLY public parse waist.
2. Scope grouping is preserved: ResolvedScopeBlock -> ScopedBlock (not
   flattened to individual RefAmend nodes).
3. Provenance witnesses from the resolved surface are carried forward where
   ClauseAST node types support them.
4. The lowering is a pure function (no side effects, no mutation).
5. ResolvedDescendantCoordination expands into multiple RefAmend children
   under the same VerbGroup; the base's chapter/part context is applied to
   each child's target address.

Resolved node -> ClauseAST node mapping
---------------------------------------
ResolvedTargetRef (verb M, no renumber_dest)    -> RefAmend(action="replace", ...)
ResolvedTargetRef (verb K)                       -> RefAmend(action="repeal", ...)
ResolvedTargetRef (verb L)                       -> RefAmend(action="insert", ...)
ResolvedTargetRef (verb S)                       -> LabelAmend(action="renumber", ...)
ResolvedTargetRef (any verb, renumber_dest set,
                   no sub-refs)                  -> LabelAmend(action="renumber", ...)
ResolvedTargetRef (any verb, sub_ref.special
                   starts with 'o')              -> LabelAmend(action="heading_replace", ...)
ResolvedInsertion                                -> RefAmend(action="insert", ...)
ResolvedHeadingPlacement                        -> LabelAmend(action="heading_replace", ...)
ResolvedScopeBlock                              -> ScopedBlock(scope=..., children=[...])
ResolvedDescendantCoordination                  -> multiple RefAmend nodes
ResolvedTextAmend                               -> TextAmend(...)
ResolvedMetaClause                              -> MetaClause(...)
"""

from __future__ import annotations

from dataclasses import replace as _dc_replace
from typing import List, Optional, Tuple

from lawvm.core.clause_ast import (
    ClauseAST,
    ClauseNode,
    LabelAmend,
    MetaClause,
    RefAmend,
    ScopedBlock,
    TextAmend,
    VerbGroup,
)
from lawvm.core.ir import LegalAddress, TextPatchSpec, TextSelector
from lawvm.core.semantic_types import FacetKind, LabelAction, StructuralAction, TextPatchKindEnum
from lawvm.finland.johtolause.surface_model import ScopeKind, TargetKind, VerbKind
from lawvm.finland.johtolause.surface_resolve import (
    ResolvedDescendantCoordination,
    ResolvedHeadingPlacement,
    ResolvedInsertion,
    ResolvedMetaClause,
    ResolvedNode,
    ResolvedScopeBlock,
    ResolvedSurfaceClause,
    ResolvedTargetRef,
    ResolvedTextAmend,
    ResolvedVerbGroup,
    ResolutionWitness,
)


# ---------------------------------------------------------------------------
# Verb code -> ClauseAST action
# ---------------------------------------------------------------------------

_VERB_TO_ACTION: dict[VerbKind, StructuralAction] = {
    VerbKind.MUUTTAA: StructuralAction.REPLACE,
    VerbKind.KUMOTA: StructuralAction.REPEAL,
    VerbKind.LISATA: StructuralAction.INSERT,
    VerbKind.SIIRTAA: StructuralAction.RENUMBER,
}

#: Meta verb groups map to None (meta verb groups contain only MetaClause nodes,
#: never RefAmend).
_META_ACTION: Optional[StructuralAction] = None


def _verb_action(verb: VerbKind) -> StructuralAction:
    """Return the StructuralAction for a VerbKind, defaulting to 'replace'."""
    return _VERB_TO_ACTION.get(verb, StructuralAction.REPLACE)


# ---------------------------------------------------------------------------
# Resolution provenance helpers
# ---------------------------------------------------------------------------


def _resolution_fields(
    rw: Optional[ResolutionWitness],
) -> Tuple[Optional[str], Optional[str]]:
    """Extract (resolution_kind, resolution_detail) from a ResolutionWitness.

    Returns (None, None) when there is no witness, so callers can splat the
    result directly into ClauseAST node constructors.

    resolution_detail concatenates antecedent_label and antecedent_chapter
    (when non-empty) as "label@chapter" for compact provenance.  If only
    a label is present, the detail is just the label.
    """
    if rw is None:
        return None, None
    kind: Optional[str] = rw.resolution_kind.value if rw.resolution_kind is not None else None
    detail: Optional[str] = None
    if rw.antecedent_label:
        if rw.antecedent_chapter:
            detail = f"{rw.antecedent_label}@{rw.antecedent_chapter}"
        else:
            detail = rw.antecedent_label
    return kind, detail


def _supplementary_text_targets(resolved: ResolvedSurfaceClause) -> Tuple[LegalAddress, ...]:
    """Return concrete structural targets usable for unscoped text amends.

    Supplementary text-amend clauses sometimes omit an explicit target and
    rely on the surrounding structural clause for scope. Core `TextAmend`
    nodes now require a non-empty target path, so the lowering path must only
    emit concrete provision targets.
    """

    out: list[LegalAddress] = []
    seen: set[tuple[tuple[str, str], ...]] = set()
    for vg in resolved.verb_groups:
        for node in vg.nodes:
            candidates: list[ResolvedTargetRef] = []
            if isinstance(node, ResolvedTargetRef):
                candidates.append(node)
            elif isinstance(node, ResolvedScopeBlock):
                candidates.extend(
                    target for target in node.targets if isinstance(target, ResolvedTargetRef)
                )
            elif isinstance(node, ResolvedDescendantCoordination):
                candidates.append(node.base)
            for target in candidates:
                addr = _build_target_address(
                    target.kind,
                    target.label,
                    target.chapter,
                    target.part,
                    momentti=0,
                    item="",
                    special="",
                )
                if not addr.path or addr.path in seen:
                    continue
                seen.add(addr.path)
                out.append(addr)
    return tuple(out)


# ---------------------------------------------------------------------------
# Address helpers
# ---------------------------------------------------------------------------


def _build_target_address(
    kind: TargetKind,
    label: str,
    chapter: str,
    part: str,
    momentti: int,
    item: str,
    special: str,
) -> LegalAddress:
    """Construct a LegalAddress from resolved target fields.

    Mirrors the address-construction logic in parsed_op_to_clause_node
    (clause_ast.py) but operates on surface model types.
    """
    path: list[tuple[str, str]] = []
    if part:
        path.append(("part", part))
    if chapter:
        path.append(("chapter", chapter))

    if kind == TargetKind.SECTION:
        path.append(("section", label))
        if momentti:
            path.append(("subsection", str(momentti)))
            if item:
                path.append(("item", item))
    elif kind == TargetKind.CHAPTER:
        path.append(("chapter", label))
    elif kind == TargetKind.PART:
        path.append(("part", label))
    elif kind == TargetKind.NIMIKE:
        path.append(("nimike", label))
    elif kind == TargetKind.APPENDIX:
        path.append(("appendix", label))

    addr_special: Optional[FacetKind] = None
    if special:
        addr_special = {"o": FacetKind.HEADING, "j": FacetKind.INTRO}.get(special[0])

    return LegalAddress(path=tuple(path), special=addr_special)


def _build_destination_address(
    kind: TargetKind,
    renumber_dest: str,
    renumber_dest_chapter: str,
    renumber_dest_part: str,
    source_label: str = "",
    *,
    momentti: int = 0,
    item: str = "",
    special: str = "",
) -> Optional[LegalAddress]:
    """Build the destination LegalAddress for renumber/move ops.

    Returns None when no destination is present.  For pure moves (container
    change without label change), source_label populates the leaf.
    """
    if not renumber_dest and not renumber_dest_chapter and not renumber_dest_part and not source_label:
        return None

    if special and special.startswith("o"):
        dest_kind = "section" if kind == TargetKind.SECTION else {
            TargetKind.CHAPTER: "chapter",
            TargetKind.PART: "part",
            TargetKind.NIMIKE: "nimike",
            TargetKind.APPENDIX: "appendix",
        }.get(kind, "section")
    elif item:
        dest_kind = "item"
    elif momentti:
        dest_kind = "subsection"
    else:
        dest_kind = {
            TargetKind.SECTION: "section",
            TargetKind.CHAPTER: "chapter",
            TargetKind.PART: "part",
            TargetKind.NIMIKE: "nimike",
            TargetKind.APPENDIX: "appendix",
        }.get(kind, "section")

    effective_label = renumber_dest or source_label

    dest_path: list[tuple[str, str]] = []
    if dest_kind not in ("part",) and renumber_dest_part:
        dest_path.append(("part", renumber_dest_part))
    if dest_kind in ("section", "subsection", "item") and renumber_dest_chapter:
        dest_path.append(("chapter", renumber_dest_chapter))
    if dest_kind in ("subsection", "item") and source_label:
        dest_path.append(("section", source_label))
    if dest_kind == "item" and momentti:
        dest_path.append(("subsection", str(momentti)))
    if effective_label:
        dest_path.append((dest_kind, effective_label))
    return LegalAddress(path=tuple(dest_path)) if dest_path else None


# ---------------------------------------------------------------------------
# ResolvedTargetRef -> ClauseNode
# ---------------------------------------------------------------------------


def _lower_resolved_target_ref(
    node: ResolvedTargetRef,
    verb: VerbKind,
) -> List[ClauseNode]:
    """Lower one ResolvedTargetRef to one or more ClauseNodes.

    If the target has sub_refs, one ClauseNode is emitted per sub_ref.
    If no sub_refs, one whole-target node is emitted.

    Verb=S and renumber_dest on whole-section nodes produce LabelAmend.
    Heading special produces LabelAmend(action="heading_replace").
    All other cases produce RefAmend.
    """
    witness_rule_id: Optional[str] = None
    source_tokens: Optional[Tuple[int, int]] = None
    if node.surface_witness:
        witness_rule_id = node.surface_witness.rule_id or None
        source_tokens = node.surface_witness.source_span

    res_kind, res_detail = _resolution_fields(node.resolution_witness)

    # --- Whole-target (no sub_refs) ---
    if not node.sub_refs:
        target = _build_target_address(
            node.kind,
            node.label,
            node.chapter,
            node.part,
            momentti=0,
            item="",
            special="",
        )
        notes: Tuple[str, ...] = node.notes

        # Verb=S -> renumber
        if verb == VerbKind.SIIRTAA:
            dest = _build_destination_address(
                node.kind,
                node.renumber_dest,
                node.renumber_dest_chapter,
                node.renumber_dest_part,
                source_label=node.label,
                momentti=0,
                item="",
                special="",
            )
            return [
                LabelAmend(
                    action=LabelAction.RENUMBER,
                    target=target,
                    new_label=node.renumber_dest or None,
                    destination=dest,
                    notes=notes,
                    source_tokens=source_tokens,
                    witness_rule_id=witness_rule_id,
                    resolution_kind=res_kind,
                    resolution_detail=res_detail,
                )
            ]

        # renumber_dest on whole-section (no sub-refs) -> renumber
        if node.renumber_dest:
            dest = _build_destination_address(
                node.kind,
                node.renumber_dest,
                node.renumber_dest_chapter,
                node.renumber_dest_part,
                source_label=node.label,
                momentti=0,
                item="",
                special="",
            )
            return [
                LabelAmend(
                    action=LabelAction.RENUMBER,
                    target=target,
                    new_label=node.renumber_dest,
                    destination=dest,
                    notes=notes,
                    source_tokens=source_tokens,
                    witness_rule_id=witness_rule_id,
                    resolution_kind=res_kind,
                    resolution_detail=res_detail,
                )
            ]

        action = _verb_action(verb)
        return [
            RefAmend(
                action=action,
                target=target,
                notes=notes,
                is_exception=node.is_exception,
                source_tokens=source_tokens,
                witness_rule_id=witness_rule_id,
                resolution_kind=res_kind,
                resolution_detail=res_detail,
            )
        ]

    # --- Per-sub_ref expansion ---
    result: List[ClauseNode] = []
    for sr in node.sub_refs:
        # Map FacetKind enum to legacy special string for _build_target_address
        special_str: str = ""
        if sr.facet is FacetKind.HEADING:
            special_str = "otsikko"
        elif sr.facet is FacetKind.INTRO:
            special_str = "johd"

        target = _build_target_address(
            node.kind,
            node.label,
            node.chapter,
            node.part,
            momentti=sr.momentti,
            item=sr.item,
            special=special_str,
        )
        notes = node.notes

        # Heading sub_ref: renumber when verb=SIIRTAA, heading_replace otherwise
        if special_str and special_str.startswith("o"):
            dest = _build_destination_address(
                node.kind,
                node.renumber_dest,
                node.renumber_dest_chapter,
                node.renumber_dest_part,
                source_label=node.label,
                momentti=sr.momentti,
                item=sr.item,
                special=special_str,
            )
            heading_action = LabelAction.RENUMBER if verb == VerbKind.SIIRTAA else LabelAction.HEADING_REPLACE
            result.append(
                LabelAmend(
                    action=heading_action,
                    target=target,
                    new_label=node.renumber_dest or None,
                    destination=dest,
                    notes=notes,
                    source_tokens=source_tokens,
                    witness_rule_id=witness_rule_id,
                    resolution_kind=res_kind,
                    resolution_detail=res_detail,
                )
            )
            continue

        # Verb=S sub_ref -> renumber
        if verb == VerbKind.SIIRTAA:
            dest = _build_destination_address(
                node.kind,
                node.renumber_dest,
                node.renumber_dest_chapter,
                node.renumber_dest_part,
                source_label=node.label,
                momentti=sr.momentti,
                item=sr.item,
                special=special_str,
            )
            result.append(
                LabelAmend(
                    action=LabelAction.RENUMBER,
                    target=target,
                    new_label=node.renumber_dest or None,
                    destination=dest,
                    notes=notes,
                    source_tokens=source_tokens,
                    witness_rule_id=witness_rule_id,
                    resolution_kind=res_kind,
                    resolution_detail=res_detail,
                )
            )
            continue

        action = _verb_action(verb)
        result.append(
            RefAmend(
                action=action,
                target=target,
                notes=notes,
                is_exception=node.is_exception,
                source_tokens=source_tokens,
                witness_rule_id=witness_rule_id,
                resolution_kind=res_kind,
                resolution_detail=res_detail,
            )
        )

    return result


# ---------------------------------------------------------------------------
# ResolvedInsertion -> ClauseNode
# ---------------------------------------------------------------------------


def _lower_resolved_insertion(
    node: ResolvedInsertion,
    _verb: VerbKind,
) -> List[ClauseNode]:
    """Lower a ResolvedInsertion to a RefAmend(action="insert")."""
    witness_rule_id: Optional[str] = None
    source_tokens: Optional[Tuple[int, int]] = None
    if node.surface_witness:
        witness_rule_id = node.surface_witness.rule_id or None
        source_tokens = node.surface_witness.source_span

    res_kind, res_detail = _resolution_fields(node.resolution_witness)

    momentti = 0
    item = ""
    special = ""
    if node.sub_target is not None:
        momentti = node.sub_target.momentti
        item = node.sub_target.item
        # Map FacetKind enum to legacy special string
        if node.sub_target.facet is FacetKind.HEADING:
            special = "otsikko"
        elif node.sub_target.facet is FacetKind.INTRO:
            special = "johd"

    target = _build_target_address(
        node.kind,
        node.label,
        node.chapter,
        node.part,
        momentti=momentti,
        item=item,
        special=special,
    )
    return [
        RefAmend(
            action=StructuralAction.INSERT,
            target=target,
            source_tokens=source_tokens,
            witness_rule_id=witness_rule_id,
            resolution_kind=res_kind,
            resolution_detail=res_detail,
        )
    ]


# ---------------------------------------------------------------------------
# ResolvedHeadingPlacement -> ClauseNode
# ---------------------------------------------------------------------------


def _lower_resolved_heading_placement(
    node: ResolvedHeadingPlacement,
    _verb: VerbKind,
) -> List[ClauseNode]:
    """Lower a ResolvedHeadingPlacement to a LabelAmend(action="heading_replace")."""
    witness_rule_id: Optional[str] = None
    source_tokens: Optional[Tuple[int, int]] = None
    if node.surface_witness:
        witness_rule_id = node.surface_witness.rule_id or None
        source_tokens = node.surface_witness.source_span

    res_kind, res_detail = _resolution_fields(node.resolution_witness)

    target = _build_target_address(
        TargetKind.SECTION,
        node.target_section,
        node.chapter,
        node.part,
        momentti=0,
        item="",
        special="otsikko",
    )
    return [
        LabelAmend(
            action=LabelAction.HEADING_REPLACE,
            target=target,
            new_label=node.heading_text or None,
            source_tokens=source_tokens,
            witness_rule_id=witness_rule_id,
            resolution_kind=res_kind,
            resolution_detail=res_detail,
        )
    ]


# ---------------------------------------------------------------------------
# ResolvedScopeBlock -> ClauseNode
# ---------------------------------------------------------------------------


def _lower_resolved_scope_block(
    node: ResolvedScopeBlock,
    verb: VerbKind,
) -> List[ClauseNode]:
    """Lower a ResolvedScopeBlock to a ScopedBlock preserving scope grouping.

    The scope is a LegalAddress for the containing element (chapter or part).
    Children are the ClauseNodes produced by lowering the enclosed targets.
    """
    scope_kind = "chapter" if node.scope_kind == ScopeKind.CHAPTER else "part"
    scope_path: list[tuple[str, str]] = []
    scope_part = ""
    if scope_kind == "chapter":
        scoped_parts = {
            target.part
            for target in node.targets
            if isinstance(target, ResolvedTargetRef) and target.part
        }
        if len(scoped_parts) == 1:
            scope_part = next(iter(scoped_parts))
            scope_path.append(("part", scope_part))
    scope_path.append((scope_kind, node.scope_label))
    scope = LegalAddress(path=tuple(scope_path))

    children: List[ClauseNode] = []
    for target in node.targets:
        if isinstance(target, ResolvedTargetRef):
            # When scoped inside a chapter block, strip the inherited part
            # context from each child — part belongs to the scope boundary,
            # not to the individual refs inside it.
            effective_target = target
            if scope_kind == "chapter" and scope_part and target.part == scope_part:
                effective_target = _dc_replace(target, part="")
            children.extend(_lower_resolved_target_ref(effective_target, verb))

    return [ScopedBlock(scope=scope, children=tuple(children))]


# ---------------------------------------------------------------------------
# ResolvedDescendantCoordination -> ClauseNodes
# ---------------------------------------------------------------------------


def _lower_resolved_descendant_coordination(
    node: ResolvedDescendantCoordination,
    verb: VerbKind,
) -> List[ClauseNode]:
    """Lower a ResolvedDescendantCoordination by expanding base + arms.

    Each arm produces a RefAmend (or LabelAmend) targeting the base section
    with the arm's sub-ref applied.
    """
    witness_rule_id: Optional[str] = None
    source_tokens: Optional[Tuple[int, int]] = None
    sw = node.surface_witness or node.base.surface_witness
    if sw:
        witness_rule_id = sw.rule_id or None
        source_tokens = sw.source_span

    res_kind, res_detail = _resolution_fields(node.resolution_witness)

    result: List[ClauseNode] = []
    action = _VERB_TO_ACTION.get(verb, StructuralAction.REPLACE)

    for sr in node.arms:
        # Map FacetKind enum to legacy special string
        special_str: str = ""
        if sr.facet is FacetKind.HEADING:
            special_str = "otsikko"
        elif sr.facet is FacetKind.INTRO:
            special_str = "johd"

        target = _build_target_address(
            node.base.kind,
            node.base.label,
            node.base.chapter,
            node.base.part,
            momentti=sr.momentti,
            item=sr.item,
            special=special_str,
        )
        notes: Tuple[str, ...] = node.base.notes

        if special_str and special_str.startswith("o"):
            result.append(
                LabelAmend(
                    action=LabelAction.HEADING_REPLACE,
                    target=target,
                    notes=notes,
                    source_tokens=source_tokens,
                    witness_rule_id=witness_rule_id,
                    resolution_kind=res_kind,
                    resolution_detail=res_detail,
                )
            )
        else:
            result.append(
                RefAmend(
                    action=action,
                    target=target,
                    notes=notes,
                    source_tokens=source_tokens,
                    witness_rule_id=witness_rule_id,
                    resolution_kind=res_kind,
                    resolution_detail=res_detail,
                )
            )

    return result


# ---------------------------------------------------------------------------
# ResolvedTextAmend -> ClauseNode
# ---------------------------------------------------------------------------


def _lower_resolved_text_amend(
    node: ResolvedTextAmend,
    _verb: VerbKind,
) -> List[ClauseNode]:
    """Lower a ResolvedTextAmend to a TextAmend.

    If the target is None (law-level text amend), the target address is an
    empty path.
    """
    if node.target is not None:
        target = _build_target_address(
            node.target.kind,
            node.target.label,
            node.target.chapter,
            node.target.part,
            momentti=0,
            item="",
            special="",
        )
    else:
        target = LegalAddress(path=())

    # Build a TextPatchSpec from the surface old_text / new_text pair.
    # If old_text is empty (unresolved text amend) skip the TextAmend — the
    # TextSelector requires a non-empty match_text and we cannot construct a
    # semantically valid patch without it.
    if not node.old_text:
        return []

    if node.new_text:
        patch = TextPatchSpec(
            kind=TextPatchKindEnum.REPLACE,
            selector=TextSelector(match_text=node.old_text),
            replacement=node.new_text,
        )
    else:
        patch = TextPatchSpec(
            kind=TextPatchKindEnum.DELETE,
            selector=TextSelector(match_text=node.old_text),
        )
    return [
        TextAmend(
            action=StructuralAction.REPLACE,
            target=target,
            text_patch=patch,
        )
    ]


# ---------------------------------------------------------------------------
# ResolvedMetaClause -> ClauseNode
# ---------------------------------------------------------------------------


def _lower_resolved_meta_clause(
    node: ResolvedMetaClause,
    _verb: VerbKind,
) -> List[ClauseNode]:
    """Lower a ResolvedMetaClause to a MetaClause."""
    return [MetaClause(kind=node.kind, raw_text=node.text)]


# ---------------------------------------------------------------------------
# Node dispatch
# ---------------------------------------------------------------------------


def _lower_resolved_node(node: ResolvedNode, verb: VerbKind) -> List[ClauseNode]:
    """Dispatch one ResolvedNode to its lowering handler.

    Returns a list because some node types (sub_refs, DescendantCoordination)
    expand into multiple ClauseNodes.
    """
    if isinstance(node, ResolvedTargetRef):
        return _lower_resolved_target_ref(node, verb)
    if isinstance(node, ResolvedInsertion):
        return _lower_resolved_insertion(node, verb)
    if isinstance(node, ResolvedHeadingPlacement):
        return _lower_resolved_heading_placement(node, verb)
    if isinstance(node, ResolvedScopeBlock):
        return _lower_resolved_scope_block(node, verb)
    if isinstance(node, ResolvedDescendantCoordination):
        return _lower_resolved_descendant_coordination(node, verb)
    if isinstance(node, ResolvedTextAmend):
        return _lower_resolved_text_amend(node, verb)
    if isinstance(node, ResolvedMetaClause):
        return _lower_resolved_meta_clause(node, verb)
    raise TypeError(f"lower_clause_ast: unknown ResolvedNode type: {type(node).__name__}")


# ---------------------------------------------------------------------------
# Verb group lowering
# ---------------------------------------------------------------------------


def _lower_verb_group(vg: ResolvedVerbGroup) -> VerbGroup:
    """Lower one ResolvedVerbGroup to a VerbGroup.

    The VerbGroup.verb uses the neutral StructuralAction vocabulary.
    """
    verb = vg.verb
    if verb == VerbKind.META:
        verb_action = StructuralAction.META
    else:
        # Map Finnish verb codes to the shared structural action vocabulary.
        _VERB_TO_ACTION: dict[VerbKind, StructuralAction] = {
            VerbKind.MUUTTAA: StructuralAction.REPLACE,
            VerbKind.KUMOTA: StructuralAction.REPEAL,
            VerbKind.LISATA: StructuralAction.INSERT,
            VerbKind.SIIRTAA: StructuralAction.RENUMBER,
        }
        verb_action = _VERB_TO_ACTION.get(verb, StructuralAction.REPLACE)

    nodes: List[ClauseNode] = []
    for node in vg.nodes:
        nodes.extend(_lower_resolved_node(node, verb))

    return VerbGroup(verb=verb_action, nodes=tuple(nodes))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def lower_to_clause_ast(resolved: ResolvedSurfaceClause) -> ClauseAST:
    """Lower a resolved surface clause to the public ClauseAST waist.

    This is the native lowering path — no ParsedOp intermediary.

    The function is a pure value transform: no mutation, no side effects.
    All provenance witnesses available in the resolved surface are threaded
    into the ClauseAST nodes that support them (RefAmend, LabelAmend).

    meta_clauses and text_amend_clauses from the resolved surface are
    emitted as a synthetic META verb group appended after the structural
    verb groups.  This preserves their separation from structural amendment
    nodes while remaining representable in ClauseAST (which only has
    verb_groups as its container structure).

    Args:
        resolved: A ResolvedSurfaceClause from surface_resolve.py.

    Returns:
        A ClauseAST with verb_groups in source order.
        source_text is taken from resolved.source_text.
    """
    verb_groups: List[VerbGroup] = []
    for vg in resolved.verb_groups:
        # Skip META verb groups that have no nodes — they were placeholder
        # containers emitted by surface_parse for meta-only clauses before
        # meta_clauses became a top-level SurfaceClause field.
        if vg.verb == VerbKind.META and not vg.nodes:
            continue
        verb_groups.append(_lower_verb_group(vg))

    # Emit top-level meta_clauses and text_amend_clauses as a synthetic META
    # verb group if either is present.  These are the supplementary nodes that
    # are NOT part of any structural amendment verb group.
    supplementary_nodes: List[ClauseNode] = []
    fallback_text_targets = _supplementary_text_targets(resolved)
    for mc in resolved.meta_clauses:
        supplementary_nodes.append(MetaClause(kind=mc.kind, raw_text=mc.text))
    for ta in resolved.text_amend_clauses:
        if ta.old_text:  # skip text amends with no match_text (unresolvable)
            if ta.target is not None:
                ta_targets = (
                    _build_target_address(
                        ta.target.kind,
                        ta.target.label,
                        ta.target.chapter,
                        ta.target.part,
                        momentti=0,
                        item="",
                        special="",
                    ),
                )
            else:
                ta_targets = fallback_text_targets

            if ta.new_text:
                patch = TextPatchSpec(
                    kind=TextPatchKindEnum.REPLACE,
                    selector=TextSelector(match_text=ta.old_text),
                    replacement=ta.new_text,
                )
            else:
                patch = TextPatchSpec(
                    kind=TextPatchKindEnum.DELETE,
                    selector=TextSelector(match_text=ta.old_text),
                )

            for ta_target in ta_targets:
                supplementary_nodes.append(
                    TextAmend(
                        action=(
                            StructuralAction.TEXT_REPLACE
                            if ta.new_text
                            else StructuralAction.TEXT_REPEAL
                        ),
                        target=ta_target,
                        text_patch=patch,
                    )
                )

    if supplementary_nodes:
        verb_groups.append(
            VerbGroup(verb=StructuralAction.META, nodes=tuple(supplementary_nodes))
        )

    return ClauseAST(
        source_text=resolved.source_text,
        verb_groups=tuple(verb_groups),
    )

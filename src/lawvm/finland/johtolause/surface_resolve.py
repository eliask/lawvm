"""surface_resolve — Phase 4: SurfaceClause -> ResolvedSurfaceClause.

Source-local resolution pass.  No live replay state; only clause-local phenomena.

Architecture (PRO_FI_PEG_VPRI_2026-04-07.md §5, Phase 4):

    SurfaceClause
        -> resolve_surface_clause()
        -> ResolvedSurfaceClause

What this pass does:

1. Backrefs (SurfaceBackRef) — "mainitun pykälän" — resolved against preceding
   verb-group target batch within the same clause context.

2. ValiotsikkoRefs (SurfaceValiotsikkoRef) — "sen edellä oleva väliotsikko" — resolved against
   preceding section targets, producing heading-target refs.

3. MoveTails (SurfaceMoveTail) — attach move destination to the preceding target
   batch by injecting the destination chapter/part into resolved target records.

4. RenumberTails (SurfaceRenumberTail) — attach renumber destination to the
   immediately preceding resolved target.

5. HeadingPlacements (SurfaceHeadingPlacement) — already concrete; pass through
   as a resolved node (no further resolution needed at this stage).

6. TextAmends (SurfaceTextAmend) — pass through; target is already resolved by
   the parser.

7. MetaClauses (SurfaceMetaClause) — pass through; temporal/commencement scope
   requires no clause-local resolution.

8. SurfaceScopeBlock — scope context is applied to enclosed targets during
   pass-through resolution.

9. SurfaceDescendantCoordination — base and arms are already concrete; passes
   through; move tail application may patch the base's chapter/part.

What this pass does NOT do:

- No live replay state (no section lookup against Finlex, no cross-law refs).
- No structural expansion (section ranges are already expanded by the parser).
- No lowering to ParsedOp or ClauseAST — that is Phase 5.

Resolution witnesses (ResolutionWitness) record how each resolution was made so
that downstream phases can explain provenance without re-tracing the algorithm.

Unresolvable nodes (backrefs with no antecedent) are recorded in
ResolvedSurfaceClause.residuals rather than silently dropped.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from enum import Enum

from lawvm.core.elaboration_context import TargetUnitKind
from lawvm.core.semantic_types import FacetKind, MetaClauseKind
from lawvm.finland.johtolause.surface_model import (
    BackRefArity,
    ScopeKind,
    SurfaceBackRef,
    SurfaceClause,
    SurfaceCrossVerbMoveTail,
    SurfaceDescendantCoordination,
    SurfaceHeadingPlacement,
    SurfaceInsertion,
    SurfaceMetaClause,
    SurfaceMoveTail,
    SurfaceNode,
    SurfaceRelabelFromContext,
    SurfaceRenumberTail,
    SurfaceScopeBlock,
    SurfaceSubRef,
    SurfaceTargetRef,
    SurfaceTargetVersionBinding,
    SurfaceTextAmend,
    SurfaceValiotsikkoRef,
    SurfaceVerbGroup,
    SurfaceWitness,
    TargetKind,
    VerbKind,
)


FI_TAIL_UNRESOLVED_RULE_ID = "fi.johtolause.tail_unresolved.v1"
FI_TAIL_UNRESOLVED_KIND = "JOHTOLAUSE.TAIL_UNRESOLVED"


# ---------------------------------------------------------------------------
# ResolutionKind — typed enum for resolution_kind strings (Pro #16)
#
# Each member's .value matches the existing string used by ResolutionWitness.
# No existing consumers are converted yet.
# ---------------------------------------------------------------------------


class ResolutionKind(Enum):
    """Classification of how a surface node was resolved."""

    PASS_THROUGH = "pass_through"
    BACKREF_SINGULAR = "backref_singular"
    BACKREF_PLURAL = "backref_plural"
    VALIOTSIKKO_REF = "valiotsikko_ref"
    MOVE_TAIL_APPLIED = "move_tail_applied"
    RENUMBER_TAIL_APPLIED = "renumber_tail_applied"
    CROSS_VERB_MOVE_RETARGET = "cross_verb_move_retarget"
    RELABEL_FROM_CONTEXT = "relabel_from_context"


# ---------------------------------------------------------------------------
# Resolution outcome — attaches to each resolved node
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ResolutionWitness:
    """Provenance record for a resolution decision.

    Attributes:
        resolution_kind:    How this node was produced.
                            "pass_through" — node was already concrete.
                            "backref_singular" — resolved "mainitun pykälän".
                            "backref_plural" — resolved "mainittujen pykälien".
                            "valiotsikko_ref" — resolved "sen edellä oleva väliotsikko".
                            "move_tail_applied" — move destination injected.
                            "renumber_tail_applied" — renumber destination injected.
        antecedent_label:   For backref/valiotsikko: the section label used as antecedent.
        antecedent_chapter: For backref/valiotsikko: the chapter of the antecedent.
        source_span:        Token span from the original SurfaceWitness, if any.
    """

    resolution_kind: ResolutionKind = ResolutionKind.PASS_THROUGH
    antecedent_label: str = ""
    antecedent_chapter: str = ""
    source_span: Optional[Tuple[int, int]] = None


# ---------------------------------------------------------------------------
# Resolved node types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ResolvedTargetRef:
    """A fully resolved structural target reference.

    Wraps the fields of SurfaceTargetRef but carries a ResolutionWitness.
    All fields that were unresolved (backrefs, applied tails) are now concrete.

    Attributes:
        kind:                  Target type.
        label:                 Resolved label.
        chapter:               Resolved chapter context.
        part:                  Resolved part context.
        sub_refs:              Sub-references.
        notes:                 Parser/resolution notes.
        move_clause_target_unit_kind: Typed move-tail destination kind, if
                               known during resolution.
        is_exception:          True when the source SurfaceTargetRef was a
                               "lukuun ottamatta" exclusion.  Forwarded from
                               SurfaceTargetRef.is_exception.
        renumber_dest:         Resolved renumber destination.
        renumber_dest_chapter: Resolved renumber destination chapter.
        renumber_dest_part:    Resolved renumber destination part.
        surface_witness:       Original SurfaceWitness from parsing.
        resolution_witness:    How this node was produced by the resolver.
    """

    kind: TargetKind
    label: str
    chapter: str = ""
    part: str = ""
    sub_refs: Tuple[SurfaceSubRef, ...] = ()
    notes: Tuple[str, ...] = ()
    move_clause_target_unit_kind: Optional[TargetUnitKind] = None
    is_exception: bool = False
    renumber_dest: str = ""
    renumber_dest_chapter: str = ""
    renumber_dest_part: str = ""
    surface_witness: Optional[SurfaceWitness] = None
    resolution_witness: Optional[ResolutionWitness] = None


@dataclass(frozen=True, slots=True)
class ResolvedHeadingPlacement:
    """A resolved heading placement.

    SurfaceHeadingPlacement passes through as-is; the target section
    is already concrete at parse time.

    Attributes:
        target_section:     Section before which the heading is placed.
        heading_text:       Heading text.
        chapter:            Chapter context.
        part:               Part context.
        surface_witness:    Original SurfaceWitness.
        resolution_witness: Resolution provenance.
    """

    target_section: str
    heading_text: str = ""
    chapter: str = ""
    part: str = ""
    surface_witness: Optional[SurfaceWitness] = None
    resolution_witness: Optional[ResolutionWitness] = None


@dataclass(frozen=True, slots=True)
class ResolvedInsertion:
    """A resolved insertion node.

    SurfaceInsertion passes through — the target is already concrete.

    Attributes:
        kind:               Target type of the inserted entity.
        label:              Resolved label.
        chapter:            Chapter context.
        part:               Part context.
        sub_target:         Sub-reference if inserting a sub-part.
        surface_witness:    Original SurfaceWitness.
        resolution_witness: Resolution provenance.
    """

    kind: TargetKind
    label: str
    chapter: str = ""
    part: str = ""
    sub_target: Optional[SurfaceSubRef] = None
    surface_witness: Optional[SurfaceWitness] = None
    resolution_witness: Optional[ResolutionWitness] = None


@dataclass(frozen=True, slots=True)
class ResolvedScopeBlock:
    """A resolved scope block.

    SurfaceScopeBlock with enclosed targets resolved and scope applied.

    Attributes:
        scope_kind:         "chapter" or "part".
        scope_label:        The scope label.
        targets:            Resolved enclosed nodes (scope applied).
        surface_witness:    Original SurfaceWitness.
        resolution_witness: Resolution provenance.
    """

    scope_kind: ScopeKind
    scope_label: str
    targets: Tuple[ResolvedNode, ...]
    surface_witness: Optional[SurfaceWitness] = None
    resolution_witness: Optional[ResolutionWitness] = None


@dataclass(frozen=True, slots=True)
class ResolvedDescendantCoordination:
    """A resolved descendant coordination node.

    SurfaceDescendantCoordination passes through — base and arms are
    already concrete.

    Attributes:
        base:               Resolved base target reference.
        arms:               Coordinated descendant sub-references.
        surface_witness:    Original SurfaceWitness.
        resolution_witness: Resolution provenance.
    """

    base: ResolvedTargetRef
    arms: Tuple[SurfaceSubRef, ...]
    surface_witness: Optional[SurfaceWitness] = None
    resolution_witness: Optional[ResolutionWitness] = None


@dataclass(frozen=True, slots=True)
class ResolvedTextAmend:
    """A resolved text amendment.

    SurfaceTextAmend passes through — the target is already concrete.

    Attributes:
        target:             Resolved target ref (None = law-level text amend).
        old_text:           Text being replaced.
        new_text:           Replacement text.
        surface_witness:    Original SurfaceWitness.
        resolution_witness: Resolution provenance.
    """

    target: Optional[ResolvedTargetRef]
    old_text: str = ""
    new_text: str = ""
    surface_witness: Optional[SurfaceWitness] = None
    resolution_witness: Optional[ResolutionWitness] = None


@dataclass(frozen=True, slots=True)
class ResolvedMetaClause:
    """A resolved meta/effect clause.

    SurfaceMetaClause passes through — temporal/commencement scope
    requires no clause-local resolution.

    Attributes:
        kind:               Classification (temporal, commencement, transition).
        text:               Raw text of the meta clause.
        surface_witness:    Original SurfaceWitness.
        resolution_witness: Resolution provenance.
    """

    kind: MetaClauseKind
    text: str = ""
    surface_witness: Optional[SurfaceWitness] = None
    resolution_witness: Optional[ResolutionWitness] = None


# Resolved node union type
ResolvedNode = (
    ResolvedTargetRef
    | ResolvedHeadingPlacement
    | ResolvedInsertion
    | ResolvedScopeBlock
    | ResolvedDescendantCoordination
    | ResolvedTextAmend
    | ResolvedMetaClause
)


@dataclass(frozen=True, slots=True)
class SurfaceResolutionResidual:
    """Typed record for a parsed surface node that resolution could not consume."""

    kind: str
    rule_id: str
    phase: str
    family: str
    reason_code: str
    strict_disposition: str
    quirks_disposition: str
    node: SurfaceNode
    detail: dict[str, object]


ResolutionResidual = SurfaceNode | SurfaceResolutionResidual


# ---------------------------------------------------------------------------
# Resolved verb group and top-level clause
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ResolvedVerbGroup:
    """One verb's resolved target list.

    Attributes:
        verb:  The amendment verb for this group.
        nodes: Ordered resolved nodes (all concrete).
    """

    verb: VerbKind
    nodes: Tuple[ResolvedNode, ...]


@dataclass(frozen=True, slots=True)
class ResolvedSurfaceClause:
    """Complete resolved surface clause.

    All unresolved surface phenomena (backrefs, valiotsikko refs, move tails,
    renumber tails) have been resolved to concrete typed nodes.

    meta_clauses and text_amend_clauses mirror the same-named fields on
    SurfaceClause.  They pass through resolution unchanged (both node types
    are already concrete at parse time) so the lowerer can emit them into
    the ClauseAST alongside the structural verb groups.

    Attributes:
        verb_groups:        Ordered resolved verb groups.
        meta_clauses:       Meta/effect clauses passed through from SurfaceClause.
        text_amend_clauses: Textual amendment clauses passed through from SurfaceClause.
        target_version_bindings:
                            Finland-local cited-version selector sidecars passed
                            through unchanged from SurfaceClause.
        source_text:        The original source text.
        residuals:          Surface nodes or typed residual records that could
                            not be resolved.
                            Empty tuple means fully resolved.
    """

    verb_groups: Tuple[ResolvedVerbGroup, ...]
    meta_clauses: Tuple[SurfaceMetaClause, ...] = ()
    text_amend_clauses: Tuple[SurfaceTextAmend, ...] = ()
    target_version_bindings: Tuple[SurfaceTargetVersionBinding, ...] = ()
    source_text: str = ""
    residuals: Tuple[ResolutionResidual, ...] = ()


# ---------------------------------------------------------------------------
# Resolver context — internal, threaded through the resolution pass
# ---------------------------------------------------------------------------


@dataclass
class _ResolverCtx:
    """Mutable resolver context threaded through the resolution pass.

    Not exported.

    Attributes:
        last_target_batch:  Most recently resolved concrete section targets.
                            Used by backref/valiotsikko resolution.
        chapter:            Current chapter context carried across verb groups.
        part:               Current part context carried across verb groups.
        residuals:          Accumulated unresolvable nodes/records.
        last_section:       Most recent section label seen across verb groups.
        last_section_chapter: Chapter of the most recent section.
        last_section_part:  Part of the most recent section.
        all_resolved_vgs:   Accumulator for already-resolved verb groups.
                            Used by cross-verb-group resolution (e.g.
                            SurfaceCrossVerbMoveTail patching prior groups).
    """

    last_target_batch: list[ResolvedTargetRef]
    chapter: str
    part: str
    residuals: list[ResolutionResidual]
    last_section: str = ""
    last_section_chapter: str = ""
    last_section_part: str = ""
    all_resolved_vgs: list[ResolvedVerbGroup] | None = None

    def update_batch(self, batch: list[ResolvedTargetRef]) -> None:
        """Update the last target batch if non-empty."""
        if batch:
            self.last_target_batch = list(batch)

    def update_chapter(self, resolved_nodes: list[ResolvedNode]) -> None:
        """Propagate chapter context from newly resolved nodes."""
        for node in reversed(resolved_nodes):
            if isinstance(node, ResolvedTargetRef):
                if node.kind == TargetKind.CHAPTER and node.label and not node.notes:
                    self.chapter = node.label
                    return
                if node.chapter:
                    self.chapter = node.chapter
                    return
            elif isinstance(node, ResolvedScopeBlock):
                if node.scope_kind == ScopeKind.CHAPTER:
                    self.chapter = node.scope_label
                    return

    def update_part(self, resolved_nodes: list[ResolvedNode]) -> None:
        """Propagate part context from newly resolved nodes."""
        for node in reversed(resolved_nodes):
            if isinstance(node, ResolvedTargetRef):
                if node.kind == TargetKind.PART and node.label:
                    self.part = node.label
                    return
                if node.part:
                    self.part = node.part
                    return
            elif isinstance(node, ResolvedScopeBlock):
                if node.scope_kind == ScopeKind.PART:
                    self.part = node.scope_label
                    return

    def update_section_context(self, resolved_nodes: list[ResolvedNode]) -> None:
        """Update last section context from resolved nodes."""
        for node in reversed(resolved_nodes):
            if isinstance(node, ResolvedTargetRef):
                if node.kind == TargetKind.SECTION and node.label:
                    self.last_section = node.label
                    self.last_section_chapter = node.chapter
                    self.last_section_part = node.part
                    return
            elif isinstance(node, ResolvedScopeBlock):
                for target in reversed(node.targets):
                    if not isinstance(target, ResolvedTargetRef):
                        continue
                    if target.kind == TargetKind.SECTION and target.label:
                        self.last_section = target.label
                        ch = target.chapter
                        if not ch and node.scope_kind == ScopeKind.CHAPTER:
                            ch = node.scope_label
                        self.last_section_chapter = ch
                        part = target.part
                        if not part and node.scope_kind == ScopeKind.PART:
                            part = node.scope_label
                        self.last_section_part = part
                        return
            elif isinstance(node, ResolvedInsertion):
                if node.kind == TargetKind.SECTION and node.label:
                    self.last_section = node.label
                    self.last_section_chapter = node.chapter
                    self.last_section_part = node.part
                    return
            elif isinstance(node, ResolvedDescendantCoordination):
                if node.base.kind == TargetKind.SECTION and node.base.label:
                    self.last_section = node.base.label
                    self.last_section_chapter = node.base.chapter
                    self.last_section_part = node.base.part
                    return


# ---------------------------------------------------------------------------
# Internal: helper predicates
# ---------------------------------------------------------------------------


def _has_any_sub(target: ResolvedTargetRef) -> bool:
    """Return True if the target has any non-trivial sub-references."""
    for sr in target.sub_refs:
        if sr.momentti or sr.item or sr.facet is not None:
            return True
    return False


def _move_tail_applies_to(target: ResolvedTargetRef) -> bool:
    """Return True if a move tail should apply to this target.

    Move tails apply to whole-section targets (kind=SECTION, no sub_refs).
    """
    return target.kind == TargetKind.SECTION and not _has_any_sub(target)


# ---------------------------------------------------------------------------
# Internal: lift SurfaceTargetRef to ResolvedTargetRef (pass-through)
# ---------------------------------------------------------------------------


def _lift_target_ref(
    node: SurfaceTargetRef,
    resolution_kind: ResolutionKind = ResolutionKind.PASS_THROUGH,
    antecedent_label: str = "",
    antecedent_chapter: str = "",
) -> ResolvedTargetRef:
    """Lift a SurfaceTargetRef to a ResolvedTargetRef."""
    span = node.witness.source_span if node.witness else None
    return ResolvedTargetRef(
        kind=node.kind,
        label=node.label,
        chapter=node.chapter,
        part=node.part,
        sub_refs=node.sub_refs,
        notes=node.notes,
        move_clause_target_unit_kind=node.move_clause_target_unit_kind,
        is_exception=node.is_exception,
        renumber_dest=node.renumber_dest,
        renumber_dest_chapter=node.renumber_dest_chapter,
        renumber_dest_part=node.renumber_dest_part,
        surface_witness=node.witness,
        resolution_witness=ResolutionWitness(
            resolution_kind=resolution_kind,  # already a ResolutionKind enum
            antecedent_label=antecedent_label,
            antecedent_chapter=antecedent_chapter,
            source_span=span,
        ),
    )


def _apply_renumber_dest(
    target: ResolvedTargetRef,
    new_label: str,
) -> ResolvedTargetRef:
    """Return a new ResolvedTargetRef with renumber_dest filled in."""
    # Do not overwrite an existing renumber_dest
    dest = target.renumber_dest if target.renumber_dest else new_label
    span = target.resolution_witness.source_span if target.resolution_witness else None
    return ResolvedTargetRef(
        kind=target.kind,
        label=target.label,
        chapter=target.chapter,
        part=target.part,
        sub_refs=target.sub_refs,
        notes=target.notes,
        move_clause_target_unit_kind=target.move_clause_target_unit_kind,
        renumber_dest=dest,
        renumber_dest_chapter=target.renumber_dest_chapter,
        renumber_dest_part=target.renumber_dest_part,
        surface_witness=target.surface_witness,
        resolution_witness=ResolutionWitness(
            resolution_kind=ResolutionKind.RENUMBER_TAIL_APPLIED,
            antecedent_label=target.label,
            antecedent_chapter=target.chapter,
            source_span=span,
        ),
    )


def _apply_move_destination(
    target: ResolvedTargetRef,
    destination_chapter: str,
    destination_part: str,
) -> ResolvedTargetRef:
    """Return a new ResolvedTargetRef with move destination applied."""
    new_chapter = destination_chapter if (destination_chapter and not target.chapter) else target.chapter
    new_part = destination_part if (destination_part and not target.part) else target.part

    notes = target.notes
    move_clause_target_unit_kind = target.move_clause_target_unit_kind
    if destination_chapter:
        move_clause_target_unit_kind = "chapter"
    if destination_part:
        move_clause_target_unit_kind = "part"

    span = target.resolution_witness.source_span if target.resolution_witness else None
    return ResolvedTargetRef(
        kind=target.kind,
        label=target.label,
        chapter=new_chapter,
        part=new_part,
        sub_refs=target.sub_refs,
        notes=notes,
        move_clause_target_unit_kind=move_clause_target_unit_kind,
        renumber_dest=target.renumber_dest,
        renumber_dest_chapter=target.renumber_dest_chapter,
        renumber_dest_part=target.renumber_dest_part,
        surface_witness=target.surface_witness,
        resolution_witness=ResolutionWitness(
            resolution_kind=ResolutionKind.MOVE_TAIL_APPLIED,
            antecedent_label=target.label,
            antecedent_chapter=target.chapter,
            source_span=span,
        ),
    )


# ---------------------------------------------------------------------------
# Internal: resolve individual surface node types
# ---------------------------------------------------------------------------


def _resolve_target_ref(
    node: SurfaceTargetRef,
    ctx: _ResolverCtx,
) -> list[ResolvedNode]:
    """Resolve a SurfaceTargetRef — pass-through.

    Batch update is performed by the verb group loop which accumulates
    consecutive target refs into the same batch.
    """
    return [_lift_target_ref(node)]


def _resolve_scope_block(
    node: SurfaceScopeBlock,
    ctx: _ResolverCtx,
) -> list[ResolvedNode]:
    """Resolve a SurfaceScopeBlock — apply scope to enclosed targets.

    Batch update is performed by the verb group loop.
    """
    resolved_targets: list[ResolvedTargetRef] = []
    for target in node.targets:
        if not isinstance(target, SurfaceTargetRef):
            continue
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
            move_clause_target_unit_kind=target.move_clause_target_unit_kind,
            is_exception=target.is_exception,
            renumber_dest=target.renumber_dest,
            renumber_dest_chapter=target.renumber_dest_chapter,
            renumber_dest_part=target.renumber_dest_part,
            witness=target.witness,
        )
        resolved_targets.append(_lift_target_ref(scoped))

    span = node.witness.source_span if node.witness else None
    return [
        ResolvedScopeBlock(
            scope_kind=node.scope_kind,
            scope_label=node.scope_label,
            targets=tuple(resolved_targets),
            surface_witness=node.witness,
            resolution_witness=ResolutionWitness(
                resolution_kind=ResolutionKind.PASS_THROUGH,
                source_span=span,
            ),
        )
    ]


def _resolve_insertion(
    node: SurfaceInsertion,
    _ctx: _ResolverCtx,
) -> list[ResolvedNode]:
    """Resolve a SurfaceInsertion — pass-through.

    Insertions do not update the target batch for backref resolution
    because "mainitun pykälän" refers to sections being amended, not inserted.
    """
    span = node.witness.source_span if node.witness else None
    return [
        ResolvedInsertion(
            kind=node.kind,
            label=node.label,
            chapter=node.chapter,
            part=node.part,
            sub_target=node.sub_target,
            surface_witness=node.witness,
            resolution_witness=ResolutionWitness(
                resolution_kind=ResolutionKind.PASS_THROUGH,
                source_span=span,
            ),
        )
    ]


def _resolve_back_ref(
    node: SurfaceBackRef,
    ctx: _ResolverCtx,
) -> list[ResolvedNode]:
    """Resolve a SurfaceBackRef against the last target batch.

    "mainitun pykälän" (singular) -> last section in preceding batch.
    "mainittujen pykälien" (plural) -> all unique sections in preceding batch.

    Returns zero or more ResolvedTargetRef nodes.
    Unresolvable backrefs (no preceding sections) are added to ctx.residuals.
    """
    is_singular = node.referent_type == BackRefArity.SINGULAR

    prev_sections: list[tuple[str, str, str]] = []  # (label, chapter, part)
    seen: set[str] = set()
    if is_singular:
        # Singular: scan backwards to find the most recent unique section
        for prev in reversed(ctx.last_target_batch):
            if prev.kind == TargetKind.SECTION and prev.label and prev.label not in seen:
                seen.add(prev.label)
                prev_sections.append((prev.label, prev.chapter or ctx.chapter, prev.part or ctx.part))
                break
    else:
        # Plural: scan forwards to preserve original batch order
        for prev in ctx.last_target_batch:
            if prev.kind == TargetKind.SECTION and prev.label and prev.label not in seen:
                seen.add(prev.label)
                prev_sections.append((prev.label, prev.chapter or ctx.chapter, prev.part or ctx.part))

    if not prev_sections:
        ctx.residuals.append(node)
        return []

    resolved: list[ResolvedNode] = []
    sub_refs = node.sub_refs if node.sub_refs else (SurfaceSubRef(),)
    span = node.witness.source_span if node.witness else None
    resolution_kind = ResolutionKind.BACKREF_SINGULAR if is_singular else ResolutionKind.BACKREF_PLURAL

    for sec_label, sec_chapter, sec_part in prev_sections:
        for sr in sub_refs:
            has_sub = sr.momentti or sr.item or sr.facet is not None
            result = ResolvedTargetRef(
                kind=TargetKind.SECTION,
                label=sec_label,
                chapter=sec_chapter,
                part=sec_part,
                sub_refs=(sr,) if has_sub else (),
                notes=(),
                renumber_dest="",
                renumber_dest_chapter="",
                renumber_dest_part="",
                surface_witness=node.witness,
                resolution_witness=ResolutionWitness(
                    resolution_kind=resolution_kind,
                    antecedent_label=sec_label,
                    antecedent_chapter=sec_chapter,
                    source_span=span,
                ),
            )
            resolved.append(result)

    return resolved


def _resolve_valiotsikko_ref(
    node: SurfaceValiotsikkoRef,
    ctx: _ResolverCtx,
) -> list[ResolvedNode]:
    """Resolve a SurfaceValiotsikkoRef to otsikko op for the preceding section.

    "sen edellä oleva väliotsikko" -> SurfaceSubRef(facet=FacetKind.HEADING) for
    the most recent section target in the preceding batch.

    Only the most recent unique section label is used: "sen" (singular
    demonstrative) refers to the one section immediately preceding the valiotsikko
    ref, not all sections in the batch.
    """
    span = node.witness.source_span if node.witness else None

    for prev in reversed(ctx.last_target_batch):
        if prev.kind == TargetKind.SECTION and prev.label:
            result = ResolvedTargetRef(
                kind=TargetKind.SECTION,
                label=prev.label,
                chapter=prev.chapter or ctx.chapter,
                part=prev.part or ctx.part,
                sub_refs=(SurfaceSubRef(facet=FacetKind.HEADING),),
                notes=(),
                renumber_dest="",
                renumber_dest_chapter="",
                renumber_dest_part="",
                surface_witness=node.witness,
                resolution_witness=ResolutionWitness(
                    resolution_kind=ResolutionKind.VALIOTSIKKO_REF,
                    antecedent_label=prev.label,
                    antecedent_chapter=prev.chapter or ctx.chapter,
                    source_span=span,
                ),
            )
            return [result]

    ctx.residuals.append(node)
    return []


def _resolve_heading_placement(
    node: SurfaceHeadingPlacement,
    _ctx: _ResolverCtx,
) -> list[ResolvedNode]:
    """Resolve a SurfaceHeadingPlacement — pass-through (already concrete)."""
    span = node.witness.source_span if node.witness else None
    return [
        ResolvedHeadingPlacement(
            target_section=node.target_section,
            heading_text=node.heading_text,
            chapter=node.chapter,
            part=node.part,
            surface_witness=node.witness,
            resolution_witness=ResolutionWitness(
                resolution_kind=ResolutionKind.PASS_THROUGH,
                source_span=span,
            ),
        )
    ]


def _resolve_text_amend(
    node: SurfaceTextAmend,
    _ctx: _ResolverCtx,
) -> list[ResolvedNode]:
    """Resolve a SurfaceTextAmend — pass-through."""
    span = node.witness.source_span if node.witness else None
    resolved_target: Optional[ResolvedTargetRef] = None
    if node.target is not None:
        resolved_target = _lift_target_ref(node.target)
    return [
        ResolvedTextAmend(
            target=resolved_target,
            old_text=node.old_text,
            new_text=node.new_text,
            surface_witness=node.witness,
            resolution_witness=ResolutionWitness(
                resolution_kind=ResolutionKind.PASS_THROUGH,
                source_span=span,
            ),
        )
    ]


def _resolve_meta_clause(
    node: SurfaceMetaClause,
    _ctx: _ResolverCtx,
) -> list[ResolvedNode]:
    """Resolve a SurfaceMetaClause — pass-through."""
    span = node.witness.source_span if node.witness else None
    return [
        ResolvedMetaClause(
            kind=node.kind,
            text=node.text,
            surface_witness=node.witness,
            resolution_witness=ResolutionWitness(
                resolution_kind=ResolutionKind.PASS_THROUGH,
                source_span=span,
            ),
        )
    ]


def _resolve_descendant_coordination(
    node: SurfaceDescendantCoordination,
    ctx: _ResolverCtx,
) -> list[ResolvedNode]:
    """Resolve a SurfaceDescendantCoordination — pass-through.

    Batch update is performed by the verb group loop.
    """
    base_resolved = _lift_target_ref(node.base)
    span = node.witness.source_span if node.witness else None
    return [
        ResolvedDescendantCoordination(
            base=base_resolved,
            arms=node.arms,
            surface_witness=node.witness,
            resolution_witness=ResolutionWitness(
                resolution_kind=ResolutionKind.PASS_THROUGH,
                source_span=span,
            ),
        )
    ]


# ---------------------------------------------------------------------------
# Cross-verb-group resolution — new node types from Step 4 refactor
# ---------------------------------------------------------------------------


def _cross_verb_patch_target(
    rn: ResolvedTargetRef,
    node: SurfaceCrossVerbMoveTail,
) -> ResolvedTargetRef:
    """Apply cross-verb move destination to a single ResolvedTargetRef.

    Only patches whole-section targets that match the source label.
    Returns the original target unchanged if it does not match.
    """
    if not (rn.kind == TargetKind.SECTION and rn.label == node.source_section_label and not _has_any_sub(rn)):
        return rn

    notes = rn.notes
    move_clause_target_unit_kind = rn.move_clause_target_unit_kind
    if node.destination_chapter:
        move_clause_target_unit_kind = "chapter"
    if node.destination_part:
        move_clause_target_unit_kind = "part"
    span = rn.resolution_witness.source_span if rn.resolution_witness else None
    return ResolvedTargetRef(
        kind=rn.kind,
        label=rn.label,
        chapter=node.destination_chapter if (node.destination_chapter and not rn.chapter) else rn.chapter,
        part=node.destination_part if (node.destination_part and not rn.part) else rn.part,
        sub_refs=rn.sub_refs,
        notes=notes,
        move_clause_target_unit_kind=move_clause_target_unit_kind,
        renumber_dest=rn.renumber_dest,
        renumber_dest_chapter=rn.renumber_dest_chapter,
        renumber_dest_part=rn.renumber_dest_part,
        surface_witness=rn.surface_witness,
        resolution_witness=ResolutionWitness(
            resolution_kind=ResolutionKind.CROSS_VERB_MOVE_RETARGET,
            antecedent_label=rn.label,
            antecedent_chapter=rn.chapter,
            source_span=span,
        ),
    )


def _resolve_cross_verb_move_tail(
    node: SurfaceCrossVerbMoveTail,
    ctx: _ResolverCtx,
) -> list[ResolvedNode]:
    """Resolve a SurfaceCrossVerbMoveTail by patching the nearest prior verb group.

    Scans previously resolved verb groups in REVERSE order (nearest first) for
    whole-section targets matching the source label and applies the move
    destination (chapter/part).  Stops after the first verb group that yields a
    patch — section labels can repeat under different scopes (e.g. "3 §" in
    chapter 1 and "3 §" in chapter 2), so binding to the nearest antecedent is
    the correct semantics.

    Targets inside ScopeBlocks and DescendantCoordinations are also patched.

    Returns empty list (the effect is entirely on prior resolved verb groups).
    Unresolvable nodes are recorded in ctx.residuals.
    """
    if ctx.all_resolved_vgs is None:
        ctx.residuals.append(node)
        return []

    patched = False
    # Scan backwards — nearest (most recent) verb group first.
    # Stop after the first verb group that contains a matching target (Bug #6 fix).
    for vg_idx in range(len(ctx.all_resolved_vgs) - 1, -1, -1):
        vg = ctx.all_resolved_vgs[vg_idx]
        new_nodes: list[ResolvedNode] = []
        changed = False
        for rn in vg.nodes:
            if isinstance(rn, ResolvedTargetRef):
                new_rn = _cross_verb_patch_target(rn, node)
                if new_rn is not rn:
                    changed = True
                    patched = True
                new_nodes.append(new_rn)
            elif isinstance(rn, ResolvedScopeBlock):
                new_targets: list[ResolvedTargetRef] = []
                scope_changed = False
                for t in rn.targets:
                    if not isinstance(t, ResolvedTargetRef):
                        continue
                    new_t = _cross_verb_patch_target(t, node)
                    if new_t is not t:
                        scope_changed = True
                    new_targets.append(new_t)
                if scope_changed:
                    new_nodes.append(
                        ResolvedScopeBlock(
                            scope_kind=rn.scope_kind,
                            scope_label=rn.scope_label,
                            targets=tuple(new_targets),
                            surface_witness=rn.surface_witness,
                            resolution_witness=rn.resolution_witness,
                        )
                    )
                    changed = True
                    patched = True
                else:
                    new_nodes.append(rn)
            elif isinstance(rn, ResolvedDescendantCoordination):
                new_base = _cross_verb_patch_target(rn.base, node)
                if new_base is not rn.base:
                    new_nodes.append(
                        ResolvedDescendantCoordination(
                            base=new_base,
                            arms=rn.arms,
                            surface_witness=rn.surface_witness,
                            resolution_witness=rn.resolution_witness,
                        )
                    )
                    changed = True
                    patched = True
                else:
                    new_nodes.append(rn)
            else:
                new_nodes.append(rn)
        if changed:
            # Replace verb group in-place (mutable list)
            ctx.all_resolved_vgs[vg_idx] = ResolvedVerbGroup(
                verb=vg.verb,
                nodes=tuple(new_nodes),
            )
            # Stop after patching the nearest matching verb group (Bug #6 fix).
            break

    # Issue #6: refresh context after cross-verb move patching
    if patched:
        # Recompute section context from all resolved verb groups so later
        # anaphoric/section-dependent clauses see the post-move state.
        for vg in reversed(ctx.all_resolved_vgs):
            for rn in reversed(vg.nodes):
                if isinstance(rn, ResolvedTargetRef):
                    if rn.kind == TargetKind.SECTION and rn.label:
                        ctx.last_section = rn.label
                        ctx.last_section_chapter = rn.chapter
                        ctx.last_section_part = rn.part
                        break
                elif isinstance(rn, ResolvedScopeBlock):
                    for t in reversed(rn.targets):
                        if not isinstance(t, ResolvedTargetRef):
                            continue
                        if t.kind == TargetKind.SECTION and t.label:
                            ctx.last_section = t.label
                            ch = t.chapter
                            if not ch and rn.scope_kind == ScopeKind.CHAPTER:
                                ch = rn.scope_label
                            ctx.last_section_chapter = ch
                            part = t.part
                            if not part and rn.scope_kind == ScopeKind.PART:
                                part = rn.scope_label
                            ctx.last_section_part = part
                            break
                    else:
                        continue
                    break
                elif isinstance(rn, ResolvedDescendantCoordination):
                    if rn.base.kind == TargetKind.SECTION and rn.base.label:
                        ctx.last_section = rn.base.label
                        ctx.last_section_chapter = rn.base.chapter
                        ctx.last_section_part = rn.base.part
                        break
            else:
                continue
            break

    if not patched:
        ctx.residuals.append(node)
    return []


def _resolve_relabel_from_context(
    node: SurfaceRelabelFromContext,
    ctx: _ResolverCtx,
) -> list[ResolvedNode]:
    """Resolve a SurfaceRelabelFromContext using preceding section context.

    Looks up the last section label from the resolver context and creates
    a ResolvedTargetRef with renumber_dest set.

    Unresolvable nodes (no preceding section context) are recorded in
    ctx.residuals.
    """
    src_section = ctx.last_section
    src_chapter = ctx.last_section_chapter
    src_part = ctx.last_section_part
    if not src_section:
        ctx.residuals.append(node)
        return []

    dest_chapter = node.destination_chapter or src_chapter
    src_chapter = src_chapter or node.destination_chapter

    span = node.witness.source_span if node.witness else None
    return [
        ResolvedTargetRef(
            kind=TargetKind.SECTION,
            label=src_section,
            chapter=src_chapter,
            part=src_part,
            sub_refs=(),
            notes=(),
            renumber_dest=node.destination_label,
            renumber_dest_chapter=dest_chapter,
            renumber_dest_part=src_part,
            surface_witness=node.witness,
            resolution_witness=ResolutionWitness(
                resolution_kind=ResolutionKind.RELABEL_FROM_CONTEXT,
                antecedent_label=src_section,
                antecedent_chapter=src_chapter,
                source_span=span,
            ),
        )
    ]


def _record_tail_unresolved(
    ctx: _ResolverCtx,
    *,
    node: SurfaceMoveTail | SurfaceRenumberTail,
    tail_kind: str,
    reason_code: str,
    verb: VerbKind,
) -> None:
    if isinstance(node, SurfaceMoveTail):
        detail: dict[str, object] = {
            "tail_kind": tail_kind,
            "reason_code": reason_code,
            "verb": verb.value,
            "destination_chapter": node.destination_chapter,
            "destination_part": node.destination_part,
            "move_clause_target_unit_kind": node.move_clause_target_unit_kind,
        }
        witness = node.witness
    else:
        detail: dict[str, object] = {
            "tail_kind": tail_kind,
            "reason_code": reason_code,
            "verb": verb.value,
            "new_label": node.new_label,
        }
        witness = node.witness

    detail["witness_rule_id"] = witness.rule_id if witness is not None else ""
    detail["source_span"] = witness.source_span if witness is not None else None
    ctx.residuals.append(
        SurfaceResolutionResidual(
            kind=FI_TAIL_UNRESOLVED_KIND,
            rule_id=FI_TAIL_UNRESOLVED_RULE_ID,
            phase="surface_resolve",
            family="target_resolution_recovery",
            reason_code=reason_code,
            strict_disposition="block",
            quirks_disposition="record",
            node=node,
            detail=detail,
        )
    )


# ---------------------------------------------------------------------------
# Node dispatch
# ---------------------------------------------------------------------------


def _dispatch_node(
    node: SurfaceNode,
    ctx: _ResolverCtx,
    *,
    verb: VerbKind = VerbKind.META,
) -> list[ResolvedNode]:
    """Dispatch a SurfaceNode to its resolution handler.

    SurfaceMoveTail and SurfaceRenumberTail are handled by the verb group
    loop directly and should not reach this function in normal operation.
    If they do, record a typed residual rather than silently dropping the
    parsed tail.
    """
    if isinstance(node, SurfaceTargetRef):
        return _resolve_target_ref(node, ctx)
    if isinstance(node, SurfaceScopeBlock):
        return _resolve_scope_block(node, ctx)
    if isinstance(node, SurfaceInsertion):
        return _resolve_insertion(node, ctx)
    if isinstance(node, SurfaceBackRef):
        return _resolve_back_ref(node, ctx)
    if isinstance(node, SurfaceValiotsikkoRef):
        return _resolve_valiotsikko_ref(node, ctx)
    if isinstance(node, SurfaceHeadingPlacement):
        return _resolve_heading_placement(node, ctx)
    if isinstance(node, SurfaceMoveTail):
        _record_tail_unresolved(
            ctx,
            node=node,
            tail_kind="move",
            reason_code="TAIL_REACHED_DISPATCH",
            verb=verb,
        )
        return []
    if isinstance(node, SurfaceRenumberTail):
        _record_tail_unresolved(
            ctx,
            node=node,
            tail_kind="renumber",
            reason_code="TAIL_REACHED_DISPATCH",
            verb=verb,
        )
        return []
    if isinstance(node, SurfaceTextAmend):
        return _resolve_text_amend(node, ctx)
    if isinstance(node, SurfaceMetaClause):
        return _resolve_meta_clause(node, ctx)
    if isinstance(node, SurfaceDescendantCoordination):
        return _resolve_descendant_coordination(node, ctx)
    if isinstance(node, SurfaceCrossVerbMoveTail):
        return _resolve_cross_verb_move_tail(node, ctx)
    if isinstance(node, SurfaceRelabelFromContext):
        return _resolve_relabel_from_context(node, ctx)
    raise TypeError(f"Unknown SurfaceNode type: {type(node).__name__}")


# ---------------------------------------------------------------------------
# Batch classification helpers
# ---------------------------------------------------------------------------


def _is_target_like_node(node: SurfaceNode) -> bool:
    """Return True for node types that contribute to the active target batch.

    SurfaceTargetRef, SurfaceScopeBlock, and SurfaceDescendantCoordination
    extend the current batch when seen consecutively.  Other node types
    (insertions, meta clauses, text amends) do not.

    Backrefs and valiotsikko refs do not themselves start a batch, but they DO
    extend the batch context after resolution (their resolved targets become
    eligible for subsequent tails/backrefs).
    """
    return isinstance(node, (SurfaceTargetRef, SurfaceScopeBlock, SurfaceDescendantCoordination))


def _extract_section_targets(nodes: list[ResolvedNode]) -> list[ResolvedTargetRef]:
    """Extract all ResolvedTargetRef section nodes from a list of resolved nodes.

    For SurfaceDescendantCoordination, the base ref is used.
    For SurfaceScopeBlock, the enclosed targets are used.
    """
    result: list[ResolvedTargetRef] = []
    for node in nodes:
        if isinstance(node, ResolvedTargetRef):
            result.append(node)
        elif isinstance(node, ResolvedScopeBlock):
            for t in node.targets:
                if isinstance(t, ResolvedTargetRef):
                    result.append(t)
        elif isinstance(node, ResolvedDescendantCoordination):
            result.append(node.base)
    return result


# ---------------------------------------------------------------------------
# Verb group resolution with tail application
# ---------------------------------------------------------------------------


def _resolve_verb_group(
    vg: SurfaceVerbGroup,
    ctx: _ResolverCtx,
) -> ResolvedVerbGroup:
    """Resolve one SurfaceVerbGroup.

    Processes nodes sequentially.  Maintains a "current batch window" spanning
    all consecutive target-like nodes since the last non-target node.  Move and
    renumber tails apply to the current batch window.  Backrefs and valiotsikko refs
    resolve against the current batch window.

    After processing each target-like node (or resolved backref/valiotsikko), the
    batch window is extended.  After processing a non-target non-tail node
    (insertion, meta, text amend), the batch window resets.

    Move and renumber tails do not reset the batch window (they just patch it).
    """
    resolved_nodes: list[ResolvedNode] = []
    # Index into resolved_nodes where the current target batch starts
    current_batch_start: int = 0
    # Whether the current window is "active" (has at least one target)
    batch_active: bool = False

    for node in vg.nodes:
        # --- Move tail: apply destination to current batch ---
        if isinstance(node, SurfaceMoveTail):
            if batch_active and (node.destination_chapter or node.destination_part):
                batch = resolved_nodes[current_batch_start:]
                updated_batch: list[ResolvedNode] = []
                applied = False
                for r in batch:
                    if isinstance(r, ResolvedTargetRef) and _move_tail_applies_to(r):
                        updated_batch.append(
                            _apply_move_destination(
                                r,
                                node.destination_chapter,
                                node.destination_part,
                            )
                        )
                        applied = True
                    else:
                        updated_batch.append(r)
                resolved_nodes = resolved_nodes[:current_batch_start] + updated_batch
                # Update ctx with the patched batch
                ctx.last_target_batch = _extract_section_targets(updated_batch)
                if not applied:
                    _record_tail_unresolved(
                        ctx,
                        node=node,
                        tail_kind="move",
                        reason_code="NO_APPLICABLE_TARGET",
                        verb=vg.verb,
                    )
            else:
                reason_code = (
                    "NO_ACTIVE_BATCH" if not batch_active else "MISSING_DESTINATION"
                )
                _record_tail_unresolved(
                    ctx,
                    node=node,
                    tail_kind="move",
                    reason_code=reason_code,
                    verb=vg.verb,
                )
            continue

        # --- Renumber tail: apply new label to the last resolved target in batch ---
        if isinstance(node, SurfaceRenumberTail):
            applied = False
            if node.new_label and batch_active:
                # Find the last target in the current batch, including
                # targets inside ScopeBlocks and DescendantCoordinations.
                last_idx = len(resolved_nodes) - 1
                while last_idx >= current_batch_start and not applied:
                    candidate = resolved_nodes[last_idx]
                    if isinstance(candidate, ResolvedTargetRef):
                        resolved_nodes[last_idx] = _apply_renumber_dest(
                            candidate,
                            node.new_label,
                        )
                        applied = True
                    elif isinstance(candidate, ResolvedScopeBlock):
                        # Patch the last target inside the scope block
                        if candidate.targets:
                            patched_targets = list(candidate.targets)
                            last_target = patched_targets[-1]
                            if not isinstance(last_target, ResolvedTargetRef):
                                last_idx -= 1
                                continue
                            patched_targets[-1] = _apply_renumber_dest(
                                last_target,
                                node.new_label,
                            )
                            resolved_nodes[last_idx] = ResolvedScopeBlock(
                                scope_kind=candidate.scope_kind,
                                scope_label=candidate.scope_label,
                                targets=tuple(patched_targets),
                                surface_witness=candidate.surface_witness,
                                resolution_witness=candidate.resolution_witness,
                            )
                            applied = True
                    elif isinstance(candidate, ResolvedDescendantCoordination):
                        # Patch the base target of the coordination
                        resolved_nodes[last_idx] = ResolvedDescendantCoordination(
                            base=_apply_renumber_dest(
                                candidate.base,
                                node.new_label,
                            ),
                            arms=candidate.arms,
                            surface_witness=candidate.surface_witness,
                            resolution_witness=candidate.resolution_witness,
                        )
                        applied = True
                    last_idx -= 1
            if not applied:
                if not node.new_label:
                    reason_code = "MISSING_NEW_LABEL"
                elif not batch_active:
                    reason_code = "NO_ACTIVE_BATCH"
                else:
                    reason_code = "NO_APPLICABLE_TARGET"
                _record_tail_unresolved(
                    ctx,
                    node=node,
                    tail_kind="renumber",
                    reason_code=reason_code,
                    verb=vg.verb,
                )
            continue

        # --- Target-like nodes: contribute to the current batch ---
        if _is_target_like_node(node):
            if not batch_active:
                current_batch_start = len(resolved_nodes)
                batch_active = True
            new_nodes = _dispatch_node(node, ctx, verb=vg.verb)
            resolved_nodes.extend(new_nodes)
            # Make freshly resolved explicit scope available to later nodes in
            # the same verb group. Long johtolause groups can switch parts
            # mid-group ("V osan 4 luvun numero 25:ksi, VI osan otsikon ...,
            # 1-3 luvun numero 26-28:ksi"), and deferring part/chapter updates
            # until the end of the whole group leaves the later chapter refs
            # stuck on stale context.
            ctx.update_chapter(new_nodes)
            ctx.update_part(new_nodes)
            # Update batch context with all section targets from this node
            new_sections = _extract_section_targets(new_nodes)
            # Extend the batch (do not reset) — multiple consecutive targets form one batch
            if new_sections:
                # Rebuild the full batch context: everything from current_batch_start
                batch_so_far = _extract_section_targets(resolved_nodes[current_batch_start:])
                ctx.last_target_batch = batch_so_far
            continue

        # --- Backrefs and valiotsikko refs: resolve against current batch, then extend it ---
        if isinstance(node, (SurfaceBackRef, SurfaceValiotsikkoRef)):
            new_nodes = _dispatch_node(node, ctx, verb=vg.verb)
            resolved_nodes.extend(new_nodes)
            ctx.update_chapter(new_nodes)
            ctx.update_part(new_nodes)
            # Resolved backref/valiotsikko nodes extend the batch context
            if new_nodes and not batch_active:
                current_batch_start = len(resolved_nodes) - len(new_nodes)
                batch_active = True
            new_sections = _extract_section_targets(new_nodes)
            if new_sections:
                # Extend the batch context with resolved targets
                batch_so_far = _extract_section_targets(resolved_nodes[current_batch_start:])
                ctx.last_target_batch = batch_so_far
            continue

        # --- Non-target nodes (insertion, meta, text amend): reset batch window ---
        new_nodes = _dispatch_node(node, ctx, verb=vg.verb)
        resolved_nodes.extend(new_nodes)
        # Reset batch window: these nodes do not serve as backref antecedents
        # and do not participate in tail application
        current_batch_start = len(resolved_nodes)
        batch_active = False

    # Update cross-verb-group context from this group's output
    ctx.update_chapter(resolved_nodes)
    ctx.update_part(resolved_nodes)
    ctx.update_section_context(resolved_nodes)

    return ResolvedVerbGroup(
        verb=vg.verb,
        nodes=tuple(resolved_nodes),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def resolve_surface_clause(clause: SurfaceClause) -> ResolvedSurfaceClause:
    """Resolve a SurfaceClause to a ResolvedSurfaceClause.

    Source-local resolution only.  No live replay state.

    Resolution is performed left-to-right, preserving source order and
    carrying context (chapter, part, last target batch) across verb groups.

    Unresolvable nodes (e.g. backrefs with no preceding sections) are
    recorded in ResolvedSurfaceClause.residuals rather than silently dropped.

    Args:
        clause: A SurfaceClause from the Phase 3 surface model.

    Returns:
        ResolvedSurfaceClause with all clause-local resolution complete.
        The returned object is a new immutable value; the input is unchanged.
    """
    resolved_vgs: list[ResolvedVerbGroup] = []
    ctx = _ResolverCtx(
        last_target_batch=[],
        chapter="",
        part="",
        residuals=[],
        all_resolved_vgs=resolved_vgs,
    )

    for vg in clause.verb_groups:
        resolved_vg = _resolve_verb_group(vg, ctx)
        resolved_vgs.append(resolved_vg)

    return ResolvedSurfaceClause(
        verb_groups=tuple(resolved_vgs),
        meta_clauses=clause.meta_clauses,
        text_amend_clauses=clause.text_amend_clauses,
        target_version_bindings=clause.target_version_bindings,
        source_text=clause.source_text,
        residuals=tuple(ctx.residuals),
    )

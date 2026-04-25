"""Tests for surface_resolve — Phase 4: SurfaceClause -> ResolvedSurfaceClause.

Covers:
1. Pass-through resolution for concrete nodes (SurfaceTargetRef, SurfaceInsertion,
   SurfaceHeadingPlacement, SurfaceTextAmend, SurfaceMetaClause,
   SurfaceDescendantCoordination, SurfaceScopeBlock)
2. BackRef resolution (singular and plural)
3. ValiotsikkoRef resolution
4. MoveTail application
5. RenumberTail application
6. Cross-verb-group context propagation (chapter, part)
7. Residuals for unresolvable nodes
8. Resolution witness provenance
9. Immutability: original SurfaceClause is unchanged after resolution
10. Empty clause round-trip
"""

from __future__ import annotations

import pytest
from typing import Any, cast

from lawvm.core.semantic_types import FacetKind
from lawvm.core.semantic_types import MetaClauseKind
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
    SurfaceRenumberTail,
    SurfaceScopeBlock,
    SurfaceSubRef,
    SurfaceTargetRef,
    SurfaceTextAmend,
    SurfaceValiotsikkoRef,
    SurfaceVerbGroup,
    SurfaceWitness,
    TargetKind,
    VerbKind,
)
from lawvm.finland.johtolause.surface_resolve import (
    ResolutionKind,
    ResolutionWitness,
    ResolvedDescendantCoordination,
    ResolvedHeadingPlacement,
    ResolvedInsertion,
    ResolvedMetaClause,
    ResolvedScopeBlock,
    ResolvedSurfaceClause,
    ResolvedTargetRef,
    ResolvedTextAmend,
    resolve_surface_clause,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clause(*verb_groups: SurfaceVerbGroup, source_text: str = "") -> SurfaceClause:
    """Build a SurfaceClause from verb groups."""
    return SurfaceClause(verb_groups=tuple(verb_groups), source_text=source_text)


def _vg(verb: VerbKind, *nodes) -> SurfaceVerbGroup:
    """Build a SurfaceVerbGroup."""
    return SurfaceVerbGroup(verb=verb, nodes=tuple(nodes))


def _tref(
    kind: TargetKind = TargetKind.SECTION,
    label: str = "7",
    chapter: str = "",
    part: str = "",
    sub_refs: tuple[SurfaceSubRef, ...] = (),
    notes: tuple[str, ...] = (),
    renumber_dest: str = "",
    renumber_dest_chapter: str = "",
    renumber_dest_part: str = "",
) -> SurfaceTargetRef:
    """Shorthand for SurfaceTargetRef."""
    return SurfaceTargetRef(
        kind=kind,
        label=label,
        chapter=chapter,
        part=part,
        sub_refs=sub_refs,
        notes=notes,
        renumber_dest=renumber_dest,
        renumber_dest_chapter=renumber_dest_chapter,
        renumber_dest_part=renumber_dest_part,
    )


def _backref(
    referent_type: BackRefArity = BackRefArity.SINGULAR,
    sub_refs: tuple[SurfaceSubRef, ...] = (),
) -> SurfaceBackRef:
    """Shorthand for SurfaceBackRef."""
    return SurfaceBackRef(referent_type=referent_type, sub_refs=sub_refs)


# ---------------------------------------------------------------------------
# 1. Empty clause
# ---------------------------------------------------------------------------


class TestEmptyClause:
    """Empty SurfaceClause produces empty ResolvedSurfaceClause."""

    def test_empty_clause(self):
        clause = SurfaceClause(verb_groups=())
        result = resolve_surface_clause(clause)
        assert isinstance(result, ResolvedSurfaceClause)
        assert result.verb_groups == ()
        assert result.residuals == ()
        assert result.source_text == ""

    def test_source_text_preserved(self):
        clause = SurfaceClause(verb_groups=(), source_text="muutetaan 7 §")
        result = resolve_surface_clause(clause)
        assert result.source_text == "muutetaan 7 §"


# ---------------------------------------------------------------------------
# 2. Pass-through: SurfaceTargetRef
# ---------------------------------------------------------------------------


class TestPassThroughTargetRef:
    """SurfaceTargetRef passes through with resolution_kind='pass_through'."""

    def test_single_section_ref(self):
        clause = _clause(_vg(VerbKind.MUUTTAA, _tref(label="7")))
        result = resolve_surface_clause(clause)

        assert len(result.verb_groups) == 1
        vg = result.verb_groups[0]
        assert vg.verb == VerbKind.MUUTTAA
        assert len(vg.nodes) == 1

        node = vg.nodes[0]
        assert isinstance(node, ResolvedTargetRef)
        assert node.kind == TargetKind.SECTION
        assert node.label == "7"
        assert node.chapter == ""
        assert node.move_clause_target_unit_kind is None
        assert node.resolution_witness is not None
        assert node.resolution_witness.resolution_kind == ResolutionKind.PASS_THROUGH

    def test_section_with_chapter(self):
        clause = _clause(_vg(VerbKind.MUUTTAA, _tref(label="12", chapter="3")))
        result = resolve_surface_clause(clause)
        node = result.verb_groups[0].nodes[0]
        assert isinstance(node, ResolvedTargetRef)
        assert node.label == "12"
        assert node.chapter == "3"
        assert node.move_clause_target_unit_kind is None

    def test_section_with_sub_refs(self):
        clause = _clause(
            _vg(
                VerbKind.MUUTTAA,
                _tref(label="7", sub_refs=(SurfaceSubRef(momentti=2),)),
            )
        )
        result = resolve_surface_clause(clause)
        node = result.verb_groups[0].nodes[0]
        assert isinstance(node, ResolvedTargetRef)
        assert node.sub_refs == (SurfaceSubRef(momentti=2),)

    def test_multiple_targets(self):
        clause = _clause(
            _vg(
                VerbKind.MUUTTAA,
                _tref(label="5"),
                _tref(label="6"),
            )
        )
        result = resolve_surface_clause(clause)
        nodes = result.verb_groups[0].nodes
        assert len(nodes) == 2
        n0, n1 = nodes[0], nodes[1]
        assert isinstance(n0, ResolvedTargetRef)
        assert isinstance(n1, ResolvedTargetRef)
        assert n0.label == "5"
        assert n1.label == "6"

    def test_chapter_ref(self):
        clause = _clause(_vg(VerbKind.KUMOTA, _tref(kind=TargetKind.CHAPTER, label="3")))
        result = resolve_surface_clause(clause)
        node = result.verb_groups[0].nodes[0]
        assert isinstance(node, ResolvedTargetRef)
        assert node.kind == TargetKind.CHAPTER
        assert node.label == "3"

    def test_renumber_dest_preserved(self):
        clause = _clause(
            _vg(
                VerbKind.MUUTTAA,
                _tref(label="1", notes=("renumber_clause",), renumber_dest="3"),
            )
        )
        result = resolve_surface_clause(clause)
        node = result.verb_groups[0].nodes[0]
        assert isinstance(node, ResolvedTargetRef)
        assert node.renumber_dest == "3"
        assert "renumber_clause" in node.notes
        assert node.move_clause_target_unit_kind is None


# ---------------------------------------------------------------------------
# 3. Pass-through: other concrete node types
# ---------------------------------------------------------------------------


class TestPassThroughOtherNodes:
    """SurfaceInsertion, HeadingPlacement, TextAmend, MetaClause pass through."""

    def test_insertion_pass_through(self):
        node = SurfaceInsertion(kind=TargetKind.SECTION, label="5a")
        clause = _clause(_vg(VerbKind.LISATA, node))
        result = resolve_surface_clause(clause)
        out = result.verb_groups[0].nodes[0]
        assert isinstance(out, ResolvedInsertion)
        assert out.kind == TargetKind.SECTION
        assert out.label == "5a"
        assert out.resolution_witness is not None
        assert out.resolution_witness.resolution_kind == ResolutionKind.PASS_THROUGH

    def test_insertion_with_sub_target(self):
        node = SurfaceInsertion(
            kind=TargetKind.SECTION,
            label="7",
            sub_target=SurfaceSubRef(momentti=3),
        )
        clause = _clause(_vg(VerbKind.LISATA, node))
        result = resolve_surface_clause(clause)
        out = result.verb_groups[0].nodes[0]
        assert isinstance(out, ResolvedInsertion)
        assert out.sub_target == SurfaceSubRef(momentti=3)

    def test_heading_placement_pass_through(self):
        node = SurfaceHeadingPlacement(target_section="53", chapter="3")
        clause = _clause(_vg(VerbKind.LISATA, node))
        result = resolve_surface_clause(clause)
        out = result.verb_groups[0].nodes[0]
        assert isinstance(out, ResolvedHeadingPlacement)
        assert out.target_section == "53"
        assert out.chapter == "3"
        assert out.resolution_witness is not None
        assert out.resolution_witness.resolution_kind == ResolutionKind.PASS_THROUGH

    def test_text_amend_pass_through(self):
        node = SurfaceTextAmend(
            target=_tref(label="5"),
            old_text="foo",
            new_text="bar",
        )
        clause = _clause(_vg(VerbKind.MUUTTAA, node))
        result = resolve_surface_clause(clause)
        out = result.verb_groups[0].nodes[0]
        assert isinstance(out, ResolvedTextAmend)
        assert out.old_text == "foo"
        assert out.new_text == "bar"
        assert out.target is not None
        assert isinstance(out.target, ResolvedTargetRef)
        assert out.target.label == "5"

    def test_text_amend_no_target(self):
        node = SurfaceTextAmend(old_text="x", new_text="y")
        clause = _clause(_vg(VerbKind.MUUTTAA, node))
        result = resolve_surface_clause(clause)
        out = result.verb_groups[0].nodes[0]
        assert isinstance(out, ResolvedTextAmend)
        assert out.target is None

    def test_meta_clause_pass_through(self):
        node = SurfaceMetaClause(kind=MetaClauseKind.COMMENCEMENT, text="voimaan 1.1.2025")
        clause = _clause(_vg(VerbKind.MUUTTAA, node))
        result = resolve_surface_clause(clause)
        out = result.verb_groups[0].nodes[0]
        assert isinstance(out, ResolvedMetaClause)
        assert out.kind == MetaClauseKind.COMMENCEMENT
        assert out.text == "voimaan 1.1.2025"
        assert out.resolution_witness is not None
        assert out.resolution_witness.resolution_kind == ResolutionKind.PASS_THROUGH


# ---------------------------------------------------------------------------
# 4. Pass-through: SurfaceScopeBlock
# ---------------------------------------------------------------------------


class TestScopeBlockPassThrough:
    """SurfaceScopeBlock applies scope and passes through."""

    def test_chapter_scope_applied(self):
        node = SurfaceScopeBlock(
            scope_kind=ScopeKind.CHAPTER,
            scope_label="3",
            targets=(
                _tref(label="5"),
                _tref(label="7"),
            ),
        )
        clause = _clause(_vg(VerbKind.MUUTTAA, node))
        result = resolve_surface_clause(clause)
        out = result.verb_groups[0].nodes[0]
        assert isinstance(out, ResolvedScopeBlock)
        assert out.scope_kind == ScopeKind.CHAPTER
        assert out.scope_label == "3"
        assert len(out.targets) == 2
        first = cast(ResolvedTargetRef, out.targets[0])
        second = cast(ResolvedTargetRef, out.targets[1])
        assert first.label == "5"
        assert first.chapter == "3"
        assert second.label == "7"
        assert second.chapter == "3"

    def test_part_scope_applied(self):
        node = SurfaceScopeBlock(
            scope_kind=ScopeKind.PART,
            scope_label="II",
            targets=(_tref(label="10"),),
        )
        clause = _clause(_vg(VerbKind.KUMOTA, node))
        result = resolve_surface_clause(clause)
        out = result.verb_groups[0].nodes[0]
        assert isinstance(out, ResolvedScopeBlock)
        assert cast(ResolvedTargetRef, out.targets[0]).part == "II"

    def test_existing_chapter_not_overwritten(self):
        """If target already has a chapter, scope does not override it."""
        node = SurfaceScopeBlock(
            scope_kind=ScopeKind.CHAPTER,
            scope_label="3",
            targets=(_tref(label="5", chapter="4"),),
        )
        clause = _clause(_vg(VerbKind.MUUTTAA, node))
        result = resolve_surface_clause(clause)
        out = result.verb_groups[0].nodes[0]
        assert isinstance(out, ResolvedScopeBlock)
        assert cast(ResolvedTargetRef, out.targets[0]).chapter == "4"  # not overwritten


# ---------------------------------------------------------------------------
# 5. Pass-through: SurfaceDescendantCoordination
# ---------------------------------------------------------------------------


class TestDescendantCoordinationPassThrough:
    """SurfaceDescendantCoordination passes through correctly."""

    def test_coordination_pass_through(self):
        base = _tref(label="5")
        node = SurfaceDescendantCoordination(
            base=base,
            arms=(
                SurfaceSubRef(momentti=1),
                SurfaceSubRef(momentti=3),
            ),
        )
        clause = _clause(_vg(VerbKind.MUUTTAA, node))
        result = resolve_surface_clause(clause)
        out = result.verb_groups[0].nodes[0]
        assert isinstance(out, ResolvedDescendantCoordination)
        assert out.base.label == "5"
        assert len(out.arms) == 2
        assert out.arms[0] == SurfaceSubRef(momentti=1)
        assert out.arms[1] == SurfaceSubRef(momentti=3)
        assert out.resolution_witness is not None
        assert out.resolution_witness.resolution_kind == ResolutionKind.PASS_THROUGH


# ---------------------------------------------------------------------------
# 6. BackRef resolution — singular
# ---------------------------------------------------------------------------


class TestBackRefSingular:
    """SurfaceBackRef with referent_type='singular' resolves to last section."""

    def test_singular_backref_basic(self):
        """'muutetaan 7 §, mainittu pykälä' -> resolves backref to section 7."""
        clause = _clause(
            _vg(
                VerbKind.MUUTTAA,
                _tref(label="7"),
                _backref(referent_type=BackRefArity.SINGULAR),
            )
        )
        result = resolve_surface_clause(clause)
        nodes = result.verb_groups[0].nodes
        assert len(nodes) == 2

        backref_resolved = nodes[1]
        assert isinstance(backref_resolved, ResolvedTargetRef)
        assert backref_resolved.label == "7"
        assert backref_resolved.resolution_witness is not None
        assert backref_resolved.resolution_witness.resolution_kind == ResolutionKind.BACKREF_SINGULAR
        assert backref_resolved.resolution_witness.antecedent_label == "7"

    def test_singular_backref_with_sub_ref(self):
        """'muutetaan 7 §, mainitun pykälän 2 momentti' -> 7 § 2."""
        clause = _clause(
            _vg(
                VerbKind.MUUTTAA,
                _tref(label="7"),
                _backref(referent_type=BackRefArity.SINGULAR, sub_refs=(SurfaceSubRef(momentti=2),)),
            )
        )
        result = resolve_surface_clause(clause)
        nodes = result.verb_groups[0].nodes

        backref_resolved = nodes[1]
        assert isinstance(backref_resolved, ResolvedTargetRef)
        assert backref_resolved.label == "7"
        assert backref_resolved.sub_refs == (SurfaceSubRef(momentti=2),)

    def test_singular_backref_takes_last_section(self):
        """Among 5 § and 6 §, singular backref takes 6 § (last)."""
        clause = _clause(
            _vg(
                VerbKind.MUUTTAA,
                _tref(label="5"),
                _tref(label="6"),
                _backref(referent_type=BackRefArity.SINGULAR),
            )
        )
        result = resolve_surface_clause(clause)
        nodes = result.verb_groups[0].nodes

        backref_resolved = nodes[2]
        assert isinstance(backref_resolved, ResolvedTargetRef)
        assert backref_resolved.label == "6"

    def test_singular_backref_preserves_chapter(self):
        """Backref inherits chapter from preceding section."""
        clause = _clause(
            _vg(
                VerbKind.MUUTTAA,
                _tref(label="12", chapter="3"),
                _backref(referent_type=BackRefArity.SINGULAR, sub_refs=(SurfaceSubRef(momentti=2),)),
            )
        )
        result = resolve_surface_clause(clause)
        nodes = result.verb_groups[0].nodes

        backref_resolved = nodes[1]
        assert isinstance(backref_resolved, ResolvedTargetRef)
        assert backref_resolved.chapter == "3"

    def test_empty_sub_refs_produces_whole_target(self):
        """Backref with empty sub_refs produces whole-target ref."""
        clause = _clause(
            _vg(
                VerbKind.MUUTTAA,
                _tref(label="7"),
                _backref(referent_type=BackRefArity.SINGULAR),
            )
        )
        result = resolve_surface_clause(clause)
        nodes = result.verb_groups[0].nodes
        backref_resolved = nodes[1]
        assert isinstance(backref_resolved, ResolvedTargetRef)
        assert backref_resolved.sub_refs == ()


# ---------------------------------------------------------------------------
# 7. BackRef resolution — plural
# ---------------------------------------------------------------------------


class TestBackRefPlural:
    """SurfaceBackRef with referent_type='plural' resolves to all sections in batch."""

    def test_plural_backref_two_sections(self):
        """'muutetaan 5 ja 6 §, mainitut pykälät' -> resolves to both 5 and 6."""
        clause = _clause(
            _vg(
                VerbKind.MUUTTAA,
                _tref(label="5"),
                _tref(label="6"),
                _backref(referent_type=BackRefArity.PLURAL, sub_refs=(SurfaceSubRef(momentti=2),)),
            )
        )
        result = resolve_surface_clause(clause)
        nodes = result.verb_groups[0].nodes
        # Two original refs + two resolved backref refs
        assert len(nodes) == 4

        backref_nodes = nodes[2:]
        labels = {n.label for n in backref_nodes if isinstance(n, ResolvedTargetRef)}
        assert labels == {"5", "6"}
        for n in backref_nodes:
            assert isinstance(n, ResolvedTargetRef)
            assert n.resolution_witness.resolution_kind == ResolutionKind.BACKREF_PLURAL

    def test_plural_backref_no_preceding_returns_residual(self):
        """Plural backref with no preceding sections goes to residuals."""
        br = _backref(referent_type=BackRefArity.PLURAL)
        clause = _clause(_vg(VerbKind.MUUTTAA, br))
        result = resolve_surface_clause(clause)
        assert len(result.verb_groups[0].nodes) == 0
        assert len(result.residuals) == 1
        assert result.residuals[0] is br


# ---------------------------------------------------------------------------
# 8. BackRef — unresolvable (no preceding sections)
# ---------------------------------------------------------------------------


class TestBackRefUnresolvable:
    """Unresolvable backrefs go to residuals."""

    def test_backref_no_preceding_goes_to_residuals(self):
        br = _backref(referent_type=BackRefArity.SINGULAR)
        clause = _clause(_vg(VerbKind.MUUTTAA, br))
        result = resolve_surface_clause(clause)
        assert len(result.verb_groups[0].nodes) == 0
        assert len(result.residuals) == 1
        assert result.residuals[0] is br

    def test_backref_after_insertion_not_resolved(self):
        """Insertions do not count as antecedents for backrefs."""
        ins = SurfaceInsertion(kind=TargetKind.SECTION, label="5a")
        br = _backref(referent_type=BackRefArity.SINGULAR)
        clause = _clause(_vg(VerbKind.LISATA, ins, br))
        result = resolve_surface_clause(clause)
        # Insertion passes through; backref has no section antecedent
        assert len(result.residuals) == 1


# ---------------------------------------------------------------------------
# 9. ValiotsikkoRef resolution
# ---------------------------------------------------------------------------


class TestValiotsikkoRefResolution:
    """SurfaceValiotsikkoRef resolves to otsikko sub_ref for preceding sections."""

    def test_valiotsikko_ref_single_section(self):
        """'muutetaan 7 §, sen edellä oleva väliotsikko' -> section 7 otsikko."""
        clause = _clause(
            _vg(
                VerbKind.MUUTTAA,
                _tref(label="7"),
                SurfaceValiotsikkoRef(),
            )
        )
        result = resolve_surface_clause(clause)
        nodes = result.verb_groups[0].nodes
        assert len(nodes) == 2

        valiotsikko_resolved = nodes[1]
        assert isinstance(valiotsikko_resolved, ResolvedTargetRef)
        assert valiotsikko_resolved.label == "7"
        assert valiotsikko_resolved.sub_refs == (SurfaceSubRef(facet=FacetKind.HEADING),)
        assert valiotsikko_resolved.resolution_witness is not None
        assert valiotsikko_resolved.resolution_witness.resolution_kind == ResolutionKind.VALIOTSIKKO_REF

    def test_valiotsikko_ref_inherits_chapter(self):
        """Valiotsikko ref inherits chapter context from preceding section."""
        clause = _clause(
            _vg(
                VerbKind.MUUTTAA,
                _tref(label="12", chapter="3"),
                SurfaceValiotsikkoRef(),
            )
        )
        result = resolve_surface_clause(clause)
        nodes = result.verb_groups[0].nodes
        valiotsikko_resolved = nodes[1]
        assert isinstance(valiotsikko_resolved, ResolvedTargetRef)
        assert valiotsikko_resolved.chapter == "3"

    def test_valiotsikko_ref_no_preceding_is_residual(self):
        """ValiotsikkoRef with no preceding sections goes to residuals."""
        vr = SurfaceValiotsikkoRef()
        clause = _clause(_vg(VerbKind.MUUTTAA, vr))
        result = resolve_surface_clause(clause)
        assert len(result.verb_groups[0].nodes) == 0
        assert len(result.residuals) == 1
        assert result.residuals[0] is vr

    def test_valiotsikko_ref_multiple_sections(self):
        """Valiotsikko ref resolves to heading for the most recent preceding section.

        "sen edellä oleva väliotsikko" is singular — only the most recent
        section in the batch is used as the antecedent.
        """
        clause = _clause(
            _vg(
                VerbKind.MUUTTAA,
                _tref(label="5"),
                _tref(label="6"),
                SurfaceValiotsikkoRef(),
            )
        )
        result = resolve_surface_clause(clause)
        nodes = result.verb_groups[0].nodes
        # 2 sections + 1 valiotsikko resolution (most recent = section 6)
        assert len(nodes) == 3
        valiotsikko_node = nodes[2]
        assert isinstance(valiotsikko_node, ResolvedTargetRef)
        assert valiotsikko_node.label == "6"
        assert len(valiotsikko_node.sub_refs) == 1
        assert valiotsikko_node.sub_refs[0].facet == FacetKind.HEADING


# ---------------------------------------------------------------------------
# 10. MoveTail application
# ---------------------------------------------------------------------------


class TestMoveTailApplication:
    """SurfaceMoveTail applies move destination to preceding whole-section targets."""

    def test_move_tail_chapter_applied(self):
        """Move tail fills in chapter on preceding section target."""
        clause = _clause(
            _vg(
                VerbKind.SIIRTAA,
                _tref(label="33"),
                SurfaceMoveTail(destination_chapter="5"),
            )
        )
        result = resolve_surface_clause(clause)
        nodes = result.verb_groups[0].nodes
        # MoveTail consumes itself; only the patched target remains
        assert len(nodes) == 1
        node = nodes[0]
        assert isinstance(node, ResolvedTargetRef)
        assert node.label == "33"
        assert node.chapter == "5"
        assert node.move_clause_target_unit_kind == "chapter"
        assert node.resolution_witness is not None
        assert node.resolution_witness.resolution_kind == ResolutionKind.MOVE_TAIL_APPLIED

    def test_move_tail_does_not_overwrite_existing_chapter(self):
        """Move tail preserves an existing chapter while keeping the move signal."""
        clause = _clause(
            _vg(
                VerbKind.SIIRTAA,
                _tref(label="33", chapter="2"),
                SurfaceMoveTail(destination_chapter="5"),
            )
        )
        result = resolve_surface_clause(clause)
        nodes = result.verb_groups[0].nodes
        assert len(nodes) == 1
        node = nodes[0]
        assert isinstance(node, ResolvedTargetRef)
        assert node.chapter == "2"  # existing chapter preserved
        assert node.move_clause_target_unit_kind == "chapter"

    def test_move_tail_does_not_overwrite_existing_part(self):
        """Move tail preserves an existing part while keeping the move signal."""
        clause = _clause(
            _vg(
                VerbKind.SIIRTAA,
                _tref(label="33", part="I"),
                SurfaceMoveTail(destination_part="II"),
            )
        )
        result = resolve_surface_clause(clause)
        nodes = result.verb_groups[0].nodes
        assert len(nodes) == 1
        node = nodes[0]
        assert isinstance(node, ResolvedTargetRef)
        assert node.part == "I"
        assert node.move_clause_target_unit_kind == "part"

    def test_move_tail_preserves_part_scope_inside_scope_block(self):
        """Move tail keeps part scope from a ScopeBlock while preserving the move signal."""
        clause = _clause(
            _vg(
                VerbKind.SIIRTAA,
                SurfaceScopeBlock(
                    scope_kind=ScopeKind.PART,
                    scope_label="II",
                    targets=(_tref(label="33"),),
                ),
                SurfaceMoveTail(destination_part="III"),
            )
        )
        result = resolve_surface_clause(clause)
        nodes = result.verb_groups[0].nodes
        assert len(nodes) == 1
        scope_block = nodes[0]
        assert isinstance(scope_block, ResolvedScopeBlock)
        target = cast(ResolvedTargetRef, scope_block.targets[0])
        assert target.label == "33"
        assert target.part == "II"
        assert target.chapter == ""
        assert target.move_clause_target_unit_kind is None

    def test_move_tail_part_applied(self):
        """Move tail fills in part on preceding section target."""
        clause = _clause(
            _vg(
                VerbKind.SIIRTAA,
                _tref(label="10"),
                SurfaceMoveTail(destination_part="II"),
            )
        )
        result = resolve_surface_clause(clause)
        nodes = result.verb_groups[0].nodes
        assert len(nodes) == 1
        node = nodes[0]
        assert isinstance(node, ResolvedTargetRef)
        assert node.part == "II"
        assert node.move_clause_target_unit_kind == "part"

    def test_move_tail_skips_sub_ref_targets(self):
        """Move tail does not apply to targets with sub-references (momentti etc.)."""
        clause = _clause(
            _vg(
                VerbKind.SIIRTAA,
                _tref(label="7", sub_refs=(SurfaceSubRef(momentti=2),)),
                SurfaceMoveTail(destination_chapter="3"),
            )
        )
        result = resolve_surface_clause(clause)
        nodes = result.verb_groups[0].nodes
        # The target has a sub_ref, so move tail should not apply
        node = nodes[0]
        assert isinstance(node, ResolvedTargetRef)
        assert node.chapter == ""  # not changed
        assert node.move_clause_target_unit_kind is None

    def test_move_tail_multiple_targets(self):
        """Move tail applies to all whole-section targets in preceding batch."""
        clause = _clause(
            _vg(
                VerbKind.SIIRTAA,
                _tref(label="5"),
                _tref(label="6"),
                SurfaceMoveTail(destination_chapter="3"),
            )
        )
        result = resolve_surface_clause(clause)
        nodes = result.verb_groups[0].nodes
        assert len(nodes) == 2
        for node in nodes:
            assert isinstance(node, ResolvedTargetRef)
            assert node.chapter == "3"


# ---------------------------------------------------------------------------
# 11. RenumberTail application
# ---------------------------------------------------------------------------


class TestRenumberTailApplication:
    """SurfaceRenumberTail applies renumber destination to preceding target."""

    def test_renumber_tail_sets_dest(self):
        """Renumber tail sets renumber_dest on the preceding target."""
        clause = _clause(
            _vg(
                VerbKind.MUUTTAA,
                _tref(label="1"),
                SurfaceRenumberTail(new_label="3"),
            )
        )
        result = resolve_surface_clause(clause)
        nodes = result.verb_groups[0].nodes
        assert len(nodes) == 1
        node = nodes[0]
        assert isinstance(node, ResolvedTargetRef)
        assert node.renumber_dest == "3"
        assert node.resolution_witness is not None
        assert node.resolution_witness.resolution_kind == ResolutionKind.RENUMBER_TAIL_APPLIED

    def test_renumber_tail_does_not_overwrite_existing_dest(self):
        """Renumber tail does not overwrite existing renumber_dest."""
        clause = _clause(
            _vg(
                VerbKind.MUUTTAA,
                _tref(label="1", renumber_dest="2"),
                SurfaceRenumberTail(new_label="3"),
            )
        )
        result = resolve_surface_clause(clause)
        nodes = result.verb_groups[0].nodes
        node = nodes[0]
        assert isinstance(node, ResolvedTargetRef)
        assert node.renumber_dest == "2"  # existing preserved

    def test_renumber_tail_empty_new_label_no_effect(self):
        """Renumber tail with empty new_label has no effect."""
        clause = _clause(
            _vg(
                VerbKind.MUUTTAA,
                _tref(label="1"),
                SurfaceRenumberTail(new_label=""),
            )
        )
        result = resolve_surface_clause(clause)
        nodes = result.verb_groups[0].nodes
        assert len(nodes) == 1
        node = nodes[0]
        assert isinstance(node, ResolvedTargetRef)
        assert node.renumber_dest == ""


# ---------------------------------------------------------------------------
# 12. Cross-verb-group context propagation
# ---------------------------------------------------------------------------


class TestCrossVerbGroupContext:
    """Chapter/part context carries across verb groups for backref resolution."""

    def test_backref_cross_group_chapter(self):
        """Backref in second verb group inherits chapter from first group's target."""
        clause = _clause(
            _vg(VerbKind.MUUTTAA, _tref(label="12", chapter="3")),
            _vg(
                VerbKind.KUMOTA,
                _backref(referent_type=BackRefArity.SINGULAR),
            ),
        )
        result = resolve_surface_clause(clause)
        # The backref in the second group should resolve against the section from the first
        second_vg = result.verb_groups[1]
        assert len(second_vg.nodes) == 1
        backref_resolved = second_vg.nodes[0]
        assert isinstance(backref_resolved, ResolvedTargetRef)
        assert backref_resolved.label == "12"
        assert backref_resolved.chapter == "3"

    def test_multi_verb_group_structure(self):
        """Multi-verb-group clause produces correct verb groups."""
        clause = _clause(
            _vg(VerbKind.KUMOTA, _tref(label="3")),
            _vg(VerbKind.MUUTTAA, _tref(label="5")),
        )
        result = resolve_surface_clause(clause)
        assert len(result.verb_groups) == 2
        assert result.verb_groups[0].verb == VerbKind.KUMOTA
        assert result.verb_groups[1].verb == VerbKind.MUUTTAA


# ---------------------------------------------------------------------------
# 13. Resolution witnesses
# ---------------------------------------------------------------------------


class TestResolutionWitness:
    """Resolution witnesses record provenance correctly."""

    def test_pass_through_witness_on_target_ref(self):
        clause = _clause(_vg(VerbKind.MUUTTAA, _tref(label="7")))
        result = resolve_surface_clause(clause)
        node = result.verb_groups[0].nodes[0]
        assert isinstance(node, ResolvedTargetRef)
        assert node.resolution_witness is not None
        assert node.resolution_witness.resolution_kind == ResolutionKind.PASS_THROUGH
        assert node.resolution_witness.antecedent_label == ""

    def test_backref_witness_records_antecedent(self):
        clause = _clause(
            _vg(
                VerbKind.MUUTTAA,
                _tref(label="12", chapter="3"),
                _backref(referent_type=BackRefArity.SINGULAR),
            )
        )
        result = resolve_surface_clause(clause)
        nodes = result.verb_groups[0].nodes
        backref_resolved = nodes[1]
        assert isinstance(backref_resolved, ResolvedTargetRef)
        w = backref_resolved.resolution_witness
        assert w is not None
        assert w.resolution_kind == ResolutionKind.BACKREF_SINGULAR
        assert w.antecedent_label == "12"
        assert w.antecedent_chapter == "3"

    def test_valiotsikko_ref_witness_records_antecedent(self):
        clause = _clause(
            _vg(
                VerbKind.MUUTTAA,
                _tref(label="7", chapter="2"),
                SurfaceValiotsikkoRef(),
            )
        )
        result = resolve_surface_clause(clause)
        nodes = result.verb_groups[0].nodes
        valiotsikko_resolved = nodes[1]
        assert isinstance(valiotsikko_resolved, ResolvedTargetRef)
        w = valiotsikko_resolved.resolution_witness
        assert w is not None
        assert w.resolution_kind == ResolutionKind.VALIOTSIKKO_REF
        assert w.antecedent_label == "7"
        assert w.antecedent_chapter == "2"

    def test_surface_witness_preserved(self):
        """Original SurfaceWitness is preserved in the resolved node."""
        sw = SurfaceWitness(rule_id="test_rule", source_span=(0, 5))
        ref = SurfaceTargetRef(kind=TargetKind.SECTION, label="7", witness=sw)
        clause = _clause(_vg(VerbKind.MUUTTAA, ref))
        result = resolve_surface_clause(clause)
        node = result.verb_groups[0].nodes[0]
        assert isinstance(node, ResolvedTargetRef)
        assert node.surface_witness is sw
        assert node.resolution_witness is not None
        assert node.resolution_witness.source_span == (0, 5)


# ---------------------------------------------------------------------------
# 14. Immutability: original SurfaceClause unchanged
# ---------------------------------------------------------------------------


class TestImmutability:
    """resolve_surface_clause does not mutate the input SurfaceClause."""

    def test_original_clause_unchanged(self):
        original_node = _tref(label="7")
        clause = _clause(_vg(VerbKind.MUUTTAA, original_node))
        _ = resolve_surface_clause(clause)
        # original_node is frozen; accessing it after resolution should be unchanged
        assert original_node.label == "7"
        assert len(clause.verb_groups) == 1

    def test_multiple_resolutions_independent(self):
        """Calling resolve_surface_clause twice gives the same result."""
        clause = _clause(
            _vg(
                VerbKind.MUUTTAA,
                _tref(label="7"),
                _backref(referent_type=BackRefArity.SINGULAR),
            )
        )
        result1 = resolve_surface_clause(clause)
        result2 = resolve_surface_clause(clause)
        # Both should produce identical results
        assert len(result1.verb_groups[0].nodes) == len(result2.verb_groups[0].nodes)
        n1 = result1.verb_groups[0].nodes[1]
        n2 = result2.verb_groups[0].nodes[1]
        assert isinstance(n1, ResolvedTargetRef)
        assert isinstance(n2, ResolvedTargetRef)
        assert n1.label == n2.label


# ---------------------------------------------------------------------------
# 15. Frozen types
# ---------------------------------------------------------------------------


class TestFrozenResolvedTypes:
    """Resolved types are frozen and immutable."""

    def test_resolved_target_ref_frozen(self):
        ref = ResolvedTargetRef(kind=TargetKind.SECTION, label="7")
        with pytest.raises((AttributeError, TypeError)):
            cast(Any, ref).label = "8"

    def test_resolved_surface_clause_frozen(self):
        clause = ResolvedSurfaceClause(verb_groups=())
        with pytest.raises((AttributeError, TypeError)):
            cast(Any, clause).source_text = "x"

    def test_resolution_witness_frozen(self):
        w = ResolutionWitness(resolution_kind=ResolutionKind.PASS_THROUGH)
        with pytest.raises((AttributeError, TypeError)):
            cast(Any, w).resolution_kind = "other"


# ---------------------------------------------------------------------------
# 16. Integration: parse through lift + resolve
# ---------------------------------------------------------------------------


class TestLiftResolveIntegration:
    """Integration test: parse -> lift -> resolve pipeline."""

    def test_simple_clause_through_pipeline(self):
        """End-to-end: parse text, lift to surface, resolve."""
        from lawvm.finland.johtolause.lift_to_surface import parse_to_surface

        surface = parse_to_surface("muutetaan 7 §")
        result = resolve_surface_clause(surface)

        assert isinstance(result, ResolvedSurfaceClause)
        assert len(result.verb_groups) == 1
        vg = result.verb_groups[0]
        assert vg.verb == VerbKind.MUUTTAA
        assert len(vg.nodes) == 1
        node = vg.nodes[0]
        assert isinstance(node, ResolvedTargetRef)
        assert node.label == "7"
        assert result.residuals == ()

    def test_multi_section_clause_through_pipeline(self):
        """Multi-section clause resolves correctly."""
        from lawvm.finland.johtolause.lift_to_surface import parse_to_surface

        surface = parse_to_surface("muutetaan 5, 7 ja 9 §")
        result = resolve_surface_clause(surface)

        assert len(result.verb_groups) == 1
        nodes = result.verb_groups[0].nodes
        assert len(nodes) == 3
        labels = [n.label for n in nodes if isinstance(n, ResolvedTargetRef)]
        assert labels == ["5", "7", "9"]

    def test_chapter_section_clause_through_pipeline(self):
        """Section with chapter context resolves correctly."""
        from lawvm.finland.johtolause.lift_to_surface import parse_to_surface

        surface = parse_to_surface("muutetaan 3 luvun 12 §")
        result = resolve_surface_clause(surface)

        node = result.verb_groups[0].nodes[0]
        assert isinstance(node, ResolvedTargetRef)
        assert node.label == "12"
        assert node.chapter == "3"

    def test_no_residuals_on_clean_clause(self):
        """Standard clauses produce no residuals."""
        from lawvm.finland.johtolause.lift_to_surface import parse_to_surface

        surface = parse_to_surface("kumotaan 3 § sekä muutetaan 5 §")
        result = resolve_surface_clause(surface)
        assert result.residuals == ()

    def test_descendant_coordination_through_pipeline(self):
        """Descendant coordination passes through resolve correctly."""
        from lawvm.finland.johtolause.lift_to_surface import parse_to_surface

        surface = parse_to_surface("muutetaan 7 §:n 1 ja 3 momentti")
        result = resolve_surface_clause(surface)

        assert len(result.verb_groups) == 1
        nodes = result.verb_groups[0].nodes
        assert len(nodes) == 1
        node = nodes[0]
        assert isinstance(node, ResolvedDescendantCoordination)
        assert node.base.label == "7"
        assert len(node.arms) == 2


# ---------------------------------------------------------------------------
# 17. Cross-verb move tail — scope block and coordination targets (Pro #5)
# ---------------------------------------------------------------------------


class TestCrossVerbMoveTailScopeBlock:
    """Cross-verb move tail patches targets inside scope blocks and coordinations."""

    def test_cross_verb_move_patches_scope_block_target(self):
        """Cross-verb move patches a section target inside a ScopeBlock."""
        clause = _clause(
            _vg(
                VerbKind.MUUTTAA,
                SurfaceScopeBlock(
                    scope_kind=ScopeKind.CHAPTER,
                    scope_label="2",
                    targets=(_tref(label="85b"),),
                ),
            ),
            _vg(
                VerbKind.SIIRTAA,
                SurfaceCrossVerbMoveTail(
                    source_section_label="85b",
                    destination_chapter="5",
                ),
            ),
        )
        result = resolve_surface_clause(clause)
        # The first verb group's scope block target should be patched
        first_vg = result.verb_groups[0]
        assert len(first_vg.nodes) == 1
        scope_block = first_vg.nodes[0]
        assert isinstance(scope_block, ResolvedScopeBlock)
        target = cast(ResolvedTargetRef, scope_block.targets[0])
        assert target.label == "85b"
        # Scoped chapter "2" was already set, so move should not overwrite
        assert target.chapter == "2"
        assert target.move_clause_target_unit_kind == "chapter"
        assert result.residuals == ()

    def test_cross_verb_move_patches_scope_block_no_chapter(self):
        """Cross-verb move patches a section without chapter inside a ScopeBlock."""
        # Use part-scope so the targets have no chapter set
        clause = _clause(
            _vg(
                VerbKind.MUUTTAA,
                SurfaceScopeBlock(
                    scope_kind=ScopeKind.PART,
                    scope_label="II",
                    targets=(_tref(label="10"),),
                ),
            ),
            _vg(
                VerbKind.SIIRTAA,
                SurfaceCrossVerbMoveTail(
                    source_section_label="10",
                    destination_chapter="3",
                ),
            ),
        )
        result = resolve_surface_clause(clause)
        first_vg = result.verb_groups[0]
        scope_block = first_vg.nodes[0]
        assert isinstance(scope_block, ResolvedScopeBlock)
        target = cast(ResolvedTargetRef, scope_block.targets[0])
        assert target.label == "10"
        assert target.chapter == "3"
        assert target.move_clause_target_unit_kind == "chapter"
        assert result.residuals == ()

    def test_cross_verb_move_patches_descendant_coordination(self):
        """Cross-verb move patches a section inside a DescendantCoordination."""
        clause = _clause(
            _vg(
                VerbKind.MUUTTAA,
                SurfaceDescendantCoordination(
                    base=_tref(label="20"),
                    arms=(SurfaceSubRef(momentti=1),),
                ),
            ),
            _vg(
                VerbKind.SIIRTAA,
                SurfaceCrossVerbMoveTail(
                    source_section_label="20",
                    destination_chapter="7",
                ),
            ),
        )
        result = resolve_surface_clause(clause)
        first_vg = result.verb_groups[0]
        coord = first_vg.nodes[0]
        assert isinstance(coord, ResolvedDescendantCoordination)
        assert coord.base.label == "20"
        assert coord.base.chapter == "7"
        assert coord.base.move_clause_target_unit_kind == "chapter"
        assert result.residuals == ()

    def test_cross_verb_move_plain_target_still_works(self):
        """Cross-verb move still patches plain ResolvedTargetRef (regression check)."""
        clause = _clause(
            _vg(VerbKind.MUUTTAA, _tref(label="33")),
            _vg(
                VerbKind.SIIRTAA,
                SurfaceCrossVerbMoveTail(
                    source_section_label="33",
                    destination_chapter="5",
                ),
            ),
        )
        result = resolve_surface_clause(clause)
        first_vg = result.verb_groups[0]
        node = first_vg.nodes[0]
        assert isinstance(node, ResolvedTargetRef)
        assert node.label == "33"
        assert node.chapter == "5"
        assert result.residuals == ()

    def test_cross_verb_move_part_destination(self):
        """Cross-verb move applies part destination."""
        clause = _clause(
            _vg(VerbKind.MUUTTAA, _tref(label="15")),
            _vg(
                VerbKind.SIIRTAA,
                SurfaceCrossVerbMoveTail(
                    source_section_label="15",
                    destination_part="III",
                ),
            ),
        )
        result = resolve_surface_clause(clause)
        first_vg = result.verb_groups[0]
        node = first_vg.nodes[0]
        assert isinstance(node, ResolvedTargetRef)
        assert node.label == "15"
        assert node.part == "III"
        assert node.move_clause_target_unit_kind == "part"
        assert result.residuals == ()

    def test_cross_verb_move_preserves_existing_part_scope(self):
        """Cross-verb move keeps an existing part scope while preserving the move signal."""
        clause = _clause(
            _vg(VerbKind.MUUTTAA, _tref(label="15", part="II")),
            _vg(
                VerbKind.SIIRTAA,
                SurfaceCrossVerbMoveTail(
                    source_section_label="15",
                    destination_part="III",
                ),
            ),
        )
        result = resolve_surface_clause(clause)
        first_vg = result.verb_groups[0]
        node = first_vg.nodes[0]
        assert isinstance(node, ResolvedTargetRef)
        assert node.label == "15"
        assert node.part == "II"
        assert node.move_clause_target_unit_kind == "part"
        assert result.residuals == ()


# ---------------------------------------------------------------------------
# 18. Cross-verb move refreshes context (Pro #6)
# ---------------------------------------------------------------------------


class TestCrossVerbMoveContextRefresh:
    """After cross-verb move patching, context is refreshed for later groups."""

    def test_context_refreshed_after_cross_verb_move(self):
        """After cross-verb move patches a section, last_section_chapter is updated.

        A SurfaceRelabelFromContext in a later verb group should see the
        post-move chapter, not the stale pre-move chapter.
        """
        from lawvm.finland.johtolause.surface_model import SurfaceRelabelFromContext

        clause = _clause(
            # VG1: muutetaan 85b §  (no chapter initially)
            _vg(VerbKind.MUUTTAA, _tref(label="85b")),
            # VG2: siirretään 85b § 5 lukuun (patches VG1 target to chapter 5)
            _vg(
                VerbKind.SIIRTAA,
                SurfaceCrossVerbMoveTail(
                    source_section_label="85b",
                    destination_chapter="5",
                ),
            ),
            # VG3: relabel from context — should see chapter "5" from the patched target
            _vg(
                VerbKind.SIIRTAA,
                SurfaceRelabelFromContext(
                    destination_label="61",
                ),
            ),
        )
        result = resolve_surface_clause(clause)
        # VG1 should be patched
        first_vg = result.verb_groups[0]
        node = first_vg.nodes[0]
        assert isinstance(node, ResolvedTargetRef)
        assert node.chapter == "5"

        # VG3 should resolve using the refreshed context (chapter "5")
        third_vg = result.verb_groups[2]
        assert len(third_vg.nodes) == 1
        relabel_node = third_vg.nodes[0]
        assert isinstance(relabel_node, ResolvedTargetRef)
        assert relabel_node.label == "85b"
        assert relabel_node.renumber_dest == "61"
        # The chapter should come from the post-move context, not empty
        assert relabel_node.chapter == "5"

    def test_context_refreshed_after_cross_verb_move_preserves_part_for_relabel(self):
        """Cross-verb refresh keeps inherited part on relabel-from-context too."""
        from lawvm.finland.johtolause.surface_model import SurfaceRelabelFromContext

        clause = _clause(
            _vg(VerbKind.MUUTTAA, _tref(label="85b", part="II")),
            _vg(
                VerbKind.SIIRTAA,
                SurfaceCrossVerbMoveTail(
                    source_section_label="85b",
                    destination_chapter="5",
                ),
            ),
            _vg(
                VerbKind.SIIRTAA,
                SurfaceRelabelFromContext(
                    destination_label="61",
                ),
            ),
        )

        result = resolve_surface_clause(clause)
        relabel_node = result.verb_groups[2].nodes[0]

        assert isinstance(relabel_node, ResolvedTargetRef)
        assert relabel_node.label == "85b"
        assert relabel_node.chapter == "5"
        assert relabel_node.part == "II"
        assert relabel_node.renumber_dest == "61"
        assert relabel_node.renumber_dest_chapter == "5"
        assert relabel_node.renumber_dest_part == "II"


# ---------------------------------------------------------------------------
# 19. RenumberTail binds inside ScopeBlock / DescendantCoordination (Pro #12)
# ---------------------------------------------------------------------------


class TestRenumberTailInsideContainers:
    """RenumberTail binds to targets inside ScopeBlock and DescendantCoordination."""

    def test_renumber_tail_inside_scope_block(self):
        """RenumberTail applies to the last target inside a ScopeBlock."""
        clause = _clause(
            _vg(
                VerbKind.MUUTTAA,
                SurfaceScopeBlock(
                    scope_kind=ScopeKind.CHAPTER,
                    scope_label="3",
                    targets=(
                        _tref(label="5"),
                        _tref(label="7"),
                    ),
                ),
                SurfaceRenumberTail(new_label="8"),
            )
        )
        result = resolve_surface_clause(clause)
        nodes = result.verb_groups[0].nodes
        assert len(nodes) == 1
        scope_block = nodes[0]
        assert isinstance(scope_block, ResolvedScopeBlock)
        # First target unchanged
        assert cast(ResolvedTargetRef, scope_block.targets[0]).renumber_dest == ""
        # Last target patched
        assert cast(ResolvedTargetRef, scope_block.targets[1]).renumber_dest == "8"
        assert scope_block.targets[1].resolution_witness is not None
        assert scope_block.targets[1].resolution_witness.resolution_kind == ResolutionKind.RENUMBER_TAIL_APPLIED

    def test_renumber_tail_inside_descendant_coordination(self):
        """RenumberTail applies to the base of a DescendantCoordination."""
        clause = _clause(
            _vg(
                VerbKind.MUUTTAA,
                SurfaceDescendantCoordination(
                    base=_tref(label="10"),
                    arms=(SurfaceSubRef(momentti=1),),
                ),
                SurfaceRenumberTail(new_label="12"),
            )
        )
        result = resolve_surface_clause(clause)
        nodes = result.verb_groups[0].nodes
        assert len(nodes) == 1
        coord = nodes[0]
        assert isinstance(coord, ResolvedDescendantCoordination)
        assert coord.base.renumber_dest == "12"
        assert coord.base.resolution_witness is not None
        assert coord.base.resolution_witness.resolution_kind == ResolutionKind.RENUMBER_TAIL_APPLIED

    def test_renumber_tail_prefers_direct_target_over_container(self):
        """When last node is a direct ResolvedTargetRef, renumber applies there."""
        clause = _clause(
            _vg(
                VerbKind.MUUTTAA,
                SurfaceScopeBlock(
                    scope_kind=ScopeKind.CHAPTER,
                    scope_label="3",
                    targets=(_tref(label="5"),),
                ),
                _tref(label="9"),
                SurfaceRenumberTail(new_label="10"),
            )
        )
        result = resolve_surface_clause(clause)
        nodes = result.verb_groups[0].nodes
        assert len(nodes) == 2
        # Scope block target unchanged
        scope_block = nodes[0]
        assert isinstance(scope_block, ResolvedScopeBlock)
        assert cast(ResolvedTargetRef, scope_block.targets[0]).renumber_dest == ""
        # Direct target patched
        direct = nodes[1]
        assert isinstance(direct, ResolvedTargetRef)
        assert direct.renumber_dest == "10"

    def test_renumber_tail_scope_block_single_target(self):
        """RenumberTail works on scope block with a single target."""
        clause = _clause(
            _vg(
                VerbKind.MUUTTAA,
                SurfaceScopeBlock(
                    scope_kind=ScopeKind.CHAPTER,
                    scope_label="2",
                    targets=(_tref(label="1"),),
                ),
                SurfaceRenumberTail(new_label="3"),
            )
        )
        result = resolve_surface_clause(clause)
        scope_block = result.verb_groups[0].nodes[0]
        assert isinstance(scope_block, ResolvedScopeBlock)
        assert cast(ResolvedTargetRef, scope_block.targets[0]).renumber_dest == "3"


# ---------------------------------------------------------------------------
# 20. Cross-verb move binds to nearest antecedent only (Pro audit #6)
# ---------------------------------------------------------------------------


class TestCrossVerbMoveNearestAntecedent:
    """_resolve_cross_verb_move_tail patches ONLY the nearest matching verb group.

    Section labels can repeat under different scopes (e.g. "3 §" in chapter 1
    and "3 §" in chapter 2).  The cross-verb move tail must bind to the nearest
    (most recent) matching target, not all prior occurrences.
    """

    def test_same_label_two_verb_groups_only_nearest_patched(self):
        """When label "3" appears in VG1 and VG2, only VG2 (nearest) is patched."""
        clause = _clause(
            # VG1: muutetaan 3 § (chapter 1 context)
            _vg(VerbKind.MUUTTAA, _tref(label="3", chapter="1")),
            # VG2: muutetaan 3 § (chapter 2 context — same label, different scope)
            _vg(VerbKind.MUUTTAA, _tref(label="3", chapter="2")),
            # VG3: cross-verb move for label "3" to part III
            _vg(
                VerbKind.SIIRTAA,
                SurfaceCrossVerbMoveTail(
                    source_section_label="3",
                    destination_part="III",
                ),
            ),
        )
        result = resolve_surface_clause(clause)

        # VG1 (label "3" in chapter "1") — must NOT be patched
        vg1_node = result.verb_groups[0].nodes[0]
        assert isinstance(vg1_node, ResolvedTargetRef)
        assert vg1_node.label == "3"
        assert vg1_node.chapter == "1"
        assert vg1_node.part == ""  # not patched

        # VG2 (label "3" in chapter "2") — must be patched (nearest)
        vg2_node = result.verb_groups[1].nodes[0]
        assert isinstance(vg2_node, ResolvedTargetRef)
        assert vg2_node.label == "3"
        assert vg2_node.chapter == "2"
        assert vg2_node.part == "III"  # patched
        assert vg2_node.move_clause_target_unit_kind == "part"

        assert result.residuals == ()

    def test_same_label_three_verb_groups_only_nearest_patched(self):
        """With three prior groups having label "5", only the last one is patched.

        The nearest VG (VG3) has label "5" with no chapter; the move tail fills
        in the destination chapter.  Earlier VGs (VG1 and VG2) have label "5"
        with existing chapters that must not be targeted by the backward-scan,
        since we stop at VG3 (nearest match).
        """
        clause = _clause(
            _vg(VerbKind.MUUTTAA, _tref(label="5", chapter="1")),
            _vg(VerbKind.MUUTTAA, _tref(label="5", chapter="2")),
            # VG3 has no chapter — the cross-verb move tail will fill it in
            _vg(VerbKind.MUUTTAA, _tref(label="5")),
            _vg(
                VerbKind.SIIRTAA,
                SurfaceCrossVerbMoveTail(
                    source_section_label="5",
                    destination_chapter="9",
                ),
            ),
        )
        result = resolve_surface_clause(clause)

        # VG1 (chapter "1") and VG2 (chapter "2") — must NOT be touched
        vg1_node = result.verb_groups[0].nodes[0]
        assert isinstance(vg1_node, ResolvedTargetRef)
        assert vg1_node.chapter == "1"

        vg2_node = result.verb_groups[1].nodes[0]
        assert isinstance(vg2_node, ResolvedTargetRef)
        assert vg2_node.chapter == "2"

        # VG3 (nearest, no chapter) — must be patched to chapter "9"
        vg3_node = result.verb_groups[2].nodes[0]
        assert isinstance(vg3_node, ResolvedTargetRef)
        assert vg3_node.label == "5"
        assert vg3_node.chapter == "9"
        assert vg3_node.move_clause_target_unit_kind == "chapter"

        assert result.residuals == ()

    def test_different_labels_both_patched_independently(self):
        """Two cross-verb move tails with different labels both find their targets."""
        clause = _clause(
            _vg(VerbKind.MUUTTAA, _tref(label="7")),
            _vg(VerbKind.MUUTTAA, _tref(label="9")),
            _vg(
                VerbKind.SIIRTAA,
                SurfaceCrossVerbMoveTail(
                    source_section_label="7",
                    destination_chapter="4",
                ),
                SurfaceCrossVerbMoveTail(
                    source_section_label="9",
                    destination_chapter="6",
                ),
            ),
        )
        result = resolve_surface_clause(clause)

        vg1_node = result.verb_groups[0].nodes[0]
        assert isinstance(vg1_node, ResolvedTargetRef)
        assert vg1_node.chapter == "4"

        vg2_node = result.verb_groups[1].nodes[0]
        assert isinstance(vg2_node, ResolvedTargetRef)
        assert vg2_node.chapter == "6"

        assert result.residuals == ()

    def test_unmatched_cross_verb_tail_goes_to_residuals(self):
        """Cross-verb move with no matching prior label → residuals."""
        tail = SurfaceCrossVerbMoveTail(
            source_section_label="99",
            destination_chapter="5",
        )
        clause = _clause(
            _vg(VerbKind.MUUTTAA, _tref(label="7")),
            _vg(VerbKind.SIIRTAA, tail),
        )
        result = resolve_surface_clause(clause)
        assert len(result.residuals) == 1
        assert result.residuals[0] is tail

    def test_nearest_wins_over_earlier_when_same_label_no_chapter(self):
        """Nearest antecedent wins even when both have no chapter context."""
        clause = _clause(
            _vg(VerbKind.MUUTTAA, _tref(label="3")),  # VG1: label "3", no chapter
            _vg(VerbKind.MUUTTAA, _tref(label="3")),  # VG2: label "3", no chapter (nearest)
            _vg(
                VerbKind.SIIRTAA,
                SurfaceCrossVerbMoveTail(
                    source_section_label="3",
                    destination_chapter="5",
                ),
            ),
        )
        result = resolve_surface_clause(clause)

        # VG1 must NOT be patched
        vg1_node = result.verb_groups[0].nodes[0]
        assert isinstance(vg1_node, ResolvedTargetRef)
        assert vg1_node.chapter == ""

        # VG2 (nearest) must be patched
        vg2_node = result.verb_groups[1].nodes[0]
        assert isinstance(vg2_node, ResolvedTargetRef)
        assert vg2_node.chapter == "5"

        assert result.residuals == ()


# ---------------------------------------------------------------------------
# 21. Inline move tail scoped to current batch only (Pro audit #7)
# ---------------------------------------------------------------------------


class TestInlineMoveTailBatchScope:
    """SurfaceMoveTail applies only within the current batch slice.

    The batch is defined by current_batch_start inside _resolve_verb_group.
    Targets that appear before a batch reset (e.g. after a SurfaceInsertion or
    SurfaceMetaClause) must not be retargeted by a move tail in a later batch.
    """

    def test_move_tail_does_not_reach_before_batch_reset(self):
        """A move tail in batch 2 does not patch targets from batch 1."""
        # SurfaceInsertion resets the batch window.  Any move tail after it
        # should only affect the new batch, not the pre-insertion targets.
        clause = _clause(
            _vg(
                VerbKind.SIIRTAA,
                # batch 1: section "5" (whole-section target)
                _tref(label="5"),
                # batch reset: insertion (non-target node resets current_batch_start)
                SurfaceInsertion(kind=TargetKind.SECTION, label="5a"),
                # batch 2: section "7" then move tail
                _tref(label="7"),
                SurfaceMoveTail(destination_chapter="3"),
            )
        )
        result = resolve_surface_clause(clause)
        nodes = result.verb_groups[0].nodes

        # Find the resolved targets by label
        section_5 = next(n for n in nodes if isinstance(n, ResolvedTargetRef) and n.label == "5")
        section_7 = next(n for n in nodes if isinstance(n, ResolvedTargetRef) and n.label == "7")

        # section "5" was in batch 1 — move tail must NOT have patched it
        assert section_5.chapter == ""
        assert section_5.move_clause_target_unit_kind is None

        # section "7" was in batch 2 — move tail MUST have patched it
        assert section_7.chapter == "3"
        assert section_7.move_clause_target_unit_kind == "chapter"

    def test_move_tail_after_meta_clause_does_not_reach_earlier_target(self):
        """Move tail after a meta clause does not patch targets before the meta clause."""
        clause = _clause(
            _vg(
                VerbKind.SIIRTAA,
                # batch 1: section "10"
                _tref(label="10"),
                # batch reset: meta clause
                SurfaceMetaClause(kind=MetaClauseKind.COMMENCEMENT, text="voimaan 1.1.2026"),
                # batch 2: section "12" then move tail
                _tref(label="12"),
                SurfaceMoveTail(destination_chapter="4"),
            )
        )
        result = resolve_surface_clause(clause)
        nodes = result.verb_groups[0].nodes

        section_10 = next(n for n in nodes if isinstance(n, ResolvedTargetRef) and n.label == "10")
        section_12 = next(n for n in nodes if isinstance(n, ResolvedTargetRef) and n.label == "12")

        assert section_10.chapter == ""
        assert section_12.chapter == "4"

    def test_move_tail_scoped_same_label_in_two_batches(self):
        """When the same label appears in two batches, move tail only hits the current one."""
        clause = _clause(
            _vg(
                VerbKind.SIIRTAA,
                # batch 1: section "3"
                _tref(label="3"),
                # batch reset
                SurfaceInsertion(kind=TargetKind.SECTION, label="3a"),
                # batch 2: section "3" again (same label, different batch)
                _tref(label="3"),
                SurfaceMoveTail(destination_chapter="7"),
            )
        )
        result = resolve_surface_clause(clause)
        nodes = result.verb_groups[0].nodes

        # Two ResolvedTargetRef nodes with label "3" — only the second is in batch 2
        section_refs = [n for n in nodes if isinstance(n, ResolvedTargetRef) and n.label == "3"]
        assert len(section_refs) == 2

        # First "3" (batch 1) must NOT be patched
        assert section_refs[0].chapter == ""

        # Second "3" (batch 2, after insertion reset) must be patched
        assert section_refs[1].chapter == "7"

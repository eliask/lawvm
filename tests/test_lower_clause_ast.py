"""Tests for lower_clause_ast — Phase 5: ResolvedSurfaceClause -> ClauseAST.

Covers:
1. Unit tests: each ResolvedNode type lowers to the expected ClauseAST node.
2. VerbGroup grouping: multiple nodes under same verb produce one VerbGroup.
3. Multi-verb clause: two verb groups produce two VerbGroups in order.
4. ScopedBlock preservation: ResolvedScopeBlock stays a ScopedBlock.
5. DescendantCoordination expansion: base + arms become multiple ClauseNodes.
6. Renumber (verb=S or renumber_dest set) -> LabelAmend.
7. Heading special -> LabelAmend(action="heading_replace").
8. Sub-ref expansion: one ResolvedTargetRef with sub_refs -> multiple nodes.
9. MetaClause pass-through.
10. TextAmend pass-through.
11. HeadingPlacement -> LabelAmend.
12. Insertion -> RefAmend(action=StructuralAction.INSERT).
13. Round-trip validation: native path ClauseAST is op-code-equivalent to
    the Finland ParsedOp -> ClauseAST path for curated cases.
"""

from __future__ import annotations

from typing import List, Tuple

import pytest

from lawvm.core.clause_ast import (
    ClauseAST,
    ClauseNode,
    LabelAmend,
    MetaClause,
    RefAmend,
    ScopedBlock,
    clause_ast_to_legal_ops,
)
from lawvm.core.ir import LegalAddress
from lawvm.core.semantic_types import FacetKind, LabelAction, MetaClauseKind, StructuralAction
from lawvm.finland.johtolause.lower_clause_ast import lower_to_clause_ast
from lawvm.finland.johtolause.parsed_op_clause_ast import build_clause_ast
from lawvm.finland.johtolause.surface_model import (
    ScopeKind,
    SurfaceSubRef,
    SurfaceWitness,
    TargetKind,
    VerbKind,
)
from lawvm.finland.johtolause.surface_resolve import (
    ResolvedDescendantCoordination,
    ResolvedHeadingPlacement,
    ResolvedInsertion,
    ResolvedMetaClause,
    ResolvedScopeBlock,
    ResolvedSurfaceClause,
    ResolvedTargetRef,
    ResolvedVerbGroup,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tref(
    kind: TargetKind = TargetKind.SECTION,
    label: str = "7",
    chapter: str = "",
    part: str = "",
    sub_refs: tuple = (),
    notes: tuple = (),
    renumber_dest: str = "",
    renumber_dest_chapter: str = "",
    renumber_dest_part: str = "",
) -> ResolvedTargetRef:
    """Build a ResolvedTargetRef with defaults."""
    return ResolvedTargetRef(
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


def _vg(verb: VerbKind, *nodes) -> ResolvedVerbGroup:
    """Build a ResolvedVerbGroup."""
    return ResolvedVerbGroup(verb=verb, nodes=tuple(nodes))


def _clause(*verb_groups: ResolvedVerbGroup, source_text: str = "") -> ResolvedSurfaceClause:
    """Build a ResolvedSurfaceClause from verb groups."""
    return ResolvedSurfaceClause(verb_groups=tuple(verb_groups), source_text=source_text)


def _addr(*pairs: Tuple[str, str], special: FacetKind | None = None) -> LegalAddress:
    """Shorthand for LegalAddress."""
    return LegalAddress(path=tuple(pairs), special=special)


def _flatten_nodes(ast: ClauseAST) -> List[ClauseNode]:
    """Flatten all VerbGroup nodes from a ClauseAST."""
    result: List[ClauseNode] = []
    for vg in ast.verb_groups:
        result.extend(vg.nodes)
    return result


# ---------------------------------------------------------------------------
# 1. Single ResolvedTargetRef per verb
# ---------------------------------------------------------------------------

class TestSingleTargetRef:
    """Each verb maps to the correct RefAmend action."""

    def test_muuttaa_to_replace(self):
        ast = lower_to_clause_ast(_clause(_vg(VerbKind.MUUTTAA, _tref(label="7"))))
        nodes = _flatten_nodes(ast)
        assert len(nodes) == 1
        assert isinstance(nodes[0], RefAmend)
        assert nodes[0].action == StructuralAction.REPLACE
        assert nodes[0].target == _addr(("section", "7"))

    def test_kumota_to_repeal(self):
        ast = lower_to_clause_ast(_clause(_vg(VerbKind.KUMOTA, _tref(label="3"))))
        nodes = _flatten_nodes(ast)
        assert len(nodes) == 1
        assert isinstance(nodes[0], RefAmend)
        assert nodes[0].action == StructuralAction.REPEAL
        assert nodes[0].target == _addr(("section", "3"))

    def test_lisata_to_insert(self):
        ast = lower_to_clause_ast(_clause(_vg(VerbKind.LISATA, _tref(label="5"))))
        nodes = _flatten_nodes(ast)
        assert len(nodes) == 1
        assert isinstance(nodes[0], RefAmend)
        assert nodes[0].action == StructuralAction.INSERT

    def test_siirtaa_to_renumber(self):
        ast = lower_to_clause_ast(_clause(_vg(VerbKind.SIIRTAA, _tref(label="8"))))
        nodes = _flatten_nodes(ast)
        assert len(nodes) == 1
        assert isinstance(nodes[0], LabelAmend)
        assert nodes[0].action == LabelAction.RENUMBER
        assert nodes[0].destination is not None
        assert nodes[0].destination.path == (("section", "8"),)

    def test_source_text_preserved(self):
        ast = lower_to_clause_ast(_clause(_vg(VerbKind.MUUTTAA, _tref()), source_text="muutetaan 7 §"))
        assert ast.source_text == "muutetaan 7 §"


# ---------------------------------------------------------------------------
# 2. Address construction
# ---------------------------------------------------------------------------

class TestAddressConstruction:
    """LegalAddress is built correctly from kind/chapter/part fields."""

    def test_section_without_chapter(self):
        ast = lower_to_clause_ast(_clause(_vg(VerbKind.MUUTTAA, _tref(label="12"))))
        node = _flatten_nodes(ast)[0]
        assert isinstance(node, RefAmend)
        assert node.target.path == (("section", "12"),)

    def test_section_with_chapter(self):
        ast = lower_to_clause_ast(_clause(_vg(VerbKind.MUUTTAA, _tref(label="12", chapter="3"))))
        node = _flatten_nodes(ast)[0]
        assert isinstance(node, RefAmend)
        assert node.target.path == (("chapter", "3"), ("section", "12"))

    def test_section_with_part(self):
        ast = lower_to_clause_ast(_clause(_vg(VerbKind.MUUTTAA, _tref(label="5", part="II"))))
        node = _flatten_nodes(ast)[0]
        assert isinstance(node, RefAmend)
        assert node.target.path == (("part", "II"), ("section", "5"))

    def test_chapter_ref(self):
        ast = lower_to_clause_ast(_clause(_vg(VerbKind.KUMOTA, _tref(kind=TargetKind.CHAPTER, label="3"))))
        node = _flatten_nodes(ast)[0]
        assert isinstance(node, RefAmend)
        assert node.target.path == (("chapter", "3"),)

    def test_appendix_ref(self):
        ast = lower_to_clause_ast(_clause(_vg(VerbKind.KUMOTA, _tref(kind=TargetKind.APPENDIX, label="1"))))
        node = _flatten_nodes(ast)[0]
        assert isinstance(node, RefAmend)
        assert node.target.path == (("appendix", "1"),)

    def test_section_with_momentti_sub_ref(self):
        tref = _tref(label="5", sub_refs=(SurfaceSubRef(momentti=2),))
        ast = lower_to_clause_ast(_clause(_vg(VerbKind.MUUTTAA, tref)))
        nodes = _flatten_nodes(ast)
        assert len(nodes) == 1
        assert isinstance(nodes[0], RefAmend)
        assert nodes[0].target.path == (("section", "5"), ("subsection", "2"))

    def test_section_with_momentti_and_item(self):
        tref = _tref(label="5", sub_refs=(SurfaceSubRef(momentti=1, item="3"),))
        ast = lower_to_clause_ast(_clause(_vg(VerbKind.MUUTTAA, tref)))
        nodes = _flatten_nodes(ast)
        assert len(nodes) == 1
        assert isinstance(nodes[0], RefAmend)
        assert nodes[0].target.path == (("section", "5"), ("subsection", "1"), ("item", "3"))


# ---------------------------------------------------------------------------
# 3. Sub-ref expansion (multiple sub_refs -> multiple ClauseNodes)
# ---------------------------------------------------------------------------

class TestSubRefExpansion:
    """Multiple sub_refs expand to multiple ClauseNodes."""

    def test_two_momentti_sub_refs(self):
        tref = _tref(label="70", sub_refs=(SurfaceSubRef(momentti=2), SurfaceSubRef(momentti=4)))
        ast = lower_to_clause_ast(_clause(_vg(VerbKind.MUUTTAA, tref)))
        nodes = _flatten_nodes(ast)
        assert len(nodes) == 2
        assert isinstance(nodes[0], RefAmend)
        assert isinstance(nodes[1], RefAmend)
        assert nodes[0].target.path == (("section", "70"), ("subsection", "2"))
        assert nodes[1].target.path == (("section", "70"), ("subsection", "4"))

    def test_heading_sub_ref_produces_label_amend(self):
        tref = _tref(label="6", sub_refs=(SurfaceSubRef(facet=FacetKind.HEADING),))
        ast = lower_to_clause_ast(_clause(_vg(VerbKind.MUUTTAA, tref)))
        nodes = _flatten_nodes(ast)
        assert len(nodes) == 1
        assert isinstance(nodes[0], LabelAmend)
        assert nodes[0].action == LabelAction.HEADING_REPLACE
        assert nodes[0].target.special == FacetKind.HEADING


# ---------------------------------------------------------------------------
# 4. Renumber (renumber_dest set on whole-section target)
# ---------------------------------------------------------------------------

class TestRenumberDest:
    """renumber_dest on a whole-section node produces LabelAmend(renumber)."""

    def test_renumber_dest_produces_label_amend(self):
        tref = _tref(label="5", renumber_dest="5a")
        ast = lower_to_clause_ast(_clause(_vg(VerbKind.MUUTTAA, tref)))
        nodes = _flatten_nodes(ast)
        assert len(nodes) == 1
        assert isinstance(nodes[0], LabelAmend)
        assert nodes[0].action == LabelAction.RENUMBER
        assert nodes[0].new_label == "5a"

    def test_siirtaa_verb_with_dest(self):
        tref = _tref(label="3", renumber_dest="3a", renumber_dest_chapter="2")
        ast = lower_to_clause_ast(_clause(_vg(VerbKind.SIIRTAA, tref)))
        nodes = _flatten_nodes(ast)
        assert len(nodes) == 1
        assert isinstance(nodes[0], LabelAmend)
        assert nodes[0].action == LabelAction.RENUMBER
        assert nodes[0].new_label == "3a"
        # Destination should include the chapter
        assert nodes[0].destination is not None
        assert any(p[0] == "chapter" for p in nodes[0].destination.path)

    def test_siirtaa_preserves_source_and_destination(self):
        """Move ops must preserve explicit from→to: target=source, destination=target."""
        tref = _tref(label="5", chapter="3", renumber_dest="7", renumber_dest_chapter="3")
        ast = lower_to_clause_ast(_clause(_vg(VerbKind.SIIRTAA, tref)))
        nodes = _flatten_nodes(ast)
        assert len(nodes) == 1
        node = nodes[0]
        assert isinstance(node, LabelAmend)
        # Source address preserved as target
        assert node.target == _addr(("chapter", "3"), ("section", "5"))
        # Destination address preserved as destination
        assert node.destination == _addr(("chapter", "3"), ("section", "7"))
        assert node.new_label == "7"

    def test_cross_part_move_preserves_from_to(self):
        """Cross-part move: source part preserved in target, dest part in destination."""
        tref = _tref(label="38", part="II", renumber_dest="38", renumber_dest_part="I")
        ast = lower_to_clause_ast(_clause(_vg(VerbKind.SIIRTAA, tref)))
        nodes = _flatten_nodes(ast)
        assert len(nodes) == 1
        node = nodes[0]
        assert isinstance(node, LabelAmend)
        # Source: part II, section 38
        assert node.target == _addr(("part", "II"), ("section", "38"))
        # Destination: part I, section 38 (same label, different container)
        assert node.destination is not None
        assert node.destination == _addr(("part", "I"), ("section", "38"))


# ---------------------------------------------------------------------------
# 5. ResolvedInsertion -> RefAmend(action=StructuralAction.INSERT)
# ---------------------------------------------------------------------------

class TestResolvedInsertion:
    """ResolvedInsertion lowers to RefAmend(action=StructuralAction.INSERT)."""

    def test_section_insertion(self):
        ins = ResolvedInsertion(kind=TargetKind.SECTION, label="5a")
        ast = lower_to_clause_ast(_clause(_vg(VerbKind.LISATA, ins)))
        nodes = _flatten_nodes(ast)
        assert len(nodes) == 1
        assert isinstance(nodes[0], RefAmend)
        assert nodes[0].action == StructuralAction.INSERT
        assert nodes[0].target.path == (("section", "5a"),)

    def test_momentti_insertion(self):
        ins = ResolvedInsertion(
            kind=TargetKind.SECTION, label="7",
            sub_target=SurfaceSubRef(momentti=3),
        )
        ast = lower_to_clause_ast(_clause(_vg(VerbKind.LISATA, ins)))
        nodes = _flatten_nodes(ast)
        assert len(nodes) == 1
        assert isinstance(nodes[0], RefAmend)
        assert nodes[0].action == StructuralAction.INSERT
        assert nodes[0].target.path == (("section", "7"), ("subsection", "3"))


# ---------------------------------------------------------------------------
# 6. ResolvedHeadingPlacement -> LabelAmend(action="heading_replace")
# ---------------------------------------------------------------------------

class TestResolvedHeadingPlacement:
    """ResolvedHeadingPlacement -> LabelAmend(heading_replace)."""

    def test_heading_placement(self):
        hp = ResolvedHeadingPlacement(target_section="53", chapter="3")
        ast = lower_to_clause_ast(_clause(_vg(VerbKind.LISATA, hp)))
        nodes = _flatten_nodes(ast)
        assert len(nodes) == 1
        assert isinstance(nodes[0], LabelAmend)
        assert nodes[0].action == LabelAction.HEADING_REPLACE
        assert nodes[0].target.special == FacetKind.HEADING
        # Chapter context in path
        assert any(p[0] == "chapter" for p in nodes[0].target.path)
        assert any(p == ("section", "53") for p in nodes[0].target.path)


# ---------------------------------------------------------------------------
# 7. ResolvedScopeBlock -> ScopedBlock (scope grouping preserved)
# ---------------------------------------------------------------------------

class TestResolvedScopeBlock:
    """ResolvedScopeBlock preserves scope grouping as ScopedBlock."""

    def test_scope_block_produces_scoped_block(self):
        target1 = _tref(label="5")
        target2 = _tref(label="7")
        scope_block = ResolvedScopeBlock(
            scope_kind=ScopeKind.CHAPTER,
            scope_label="3",
            targets=(target1, target2),
        )
        ast = lower_to_clause_ast(_clause(_vg(VerbKind.MUUTTAA, scope_block)))
        nodes = _flatten_nodes(ast)
        assert len(nodes) == 1
        assert isinstance(nodes[0], ScopedBlock)
        # Scope is the chapter
        assert nodes[0].scope == _addr(("chapter", "3"))
        # Two children
        assert len(nodes[0].children) == 2

    def test_scope_block_children_are_ref_amends(self):
        scope_block = ResolvedScopeBlock(
            scope_kind=ScopeKind.CHAPTER,
            scope_label="2",
            targets=(
                _tref(label="3", chapter="2"),
                _tref(label="4", chapter="2"),
            ),
        )
        ast = lower_to_clause_ast(_clause(_vg(VerbKind.KUMOTA, scope_block)))
        nodes = _flatten_nodes(ast)
        assert isinstance(nodes[0], ScopedBlock)
        for child in nodes[0].children:
            assert isinstance(child, RefAmend)
            assert child.action == StructuralAction.REPEAL


# ---------------------------------------------------------------------------
# 8. ResolvedDescendantCoordination -> multiple ClauseNodes
# ---------------------------------------------------------------------------

class TestResolvedDescendantCoordination:
    """ResolvedDescendantCoordination expands base + arms into ClauseNodes."""

    def test_coordination_expands(self):
        base = _tref(label="5")
        coord = ResolvedDescendantCoordination(
            base=base,
            arms=(SurfaceSubRef(momentti=1), SurfaceSubRef(momentti=3)),
        )
        ast = lower_to_clause_ast(_clause(_vg(VerbKind.MUUTTAA, coord)))
        nodes = _flatten_nodes(ast)
        assert len(nodes) == 2
        assert isinstance(nodes[0], RefAmend)
        assert isinstance(nodes[1], RefAmend)
        assert nodes[0].target.path == (("section", "5"), ("subsection", "1"))
        assert nodes[1].target.path == (("section", "5"), ("subsection", "3"))

    def test_coordination_johd_arm(self):
        base = _tref(label="5")
        coord = ResolvedDescendantCoordination(
            base=base,
            arms=(SurfaceSubRef(momentti=2, facet=FacetKind.INTRO),),
        )
        ast = lower_to_clause_ast(_clause(_vg(VerbKind.MUUTTAA, coord)))
        nodes = _flatten_nodes(ast)
        assert len(nodes) == 1
        assert isinstance(nodes[0], RefAmend)
        assert nodes[0].target.special == FacetKind.INTRO


# ---------------------------------------------------------------------------
# 9. ResolvedMetaClause -> MetaClause
# ---------------------------------------------------------------------------

class TestResolvedMetaClause:
    """ResolvedMetaClause passes through as MetaClause."""

    def test_meta_clause(self):
        meta = ResolvedMetaClause(kind=MetaClauseKind.COMMENCEMENT, text="Tämä laki tulee voimaan 1.1.2025.")
        ast = lower_to_clause_ast(_clause(_vg(VerbKind.MUUTTAA, meta)))
        nodes = _flatten_nodes(ast)
        assert len(nodes) == 1
        assert isinstance(nodes[0], MetaClause)
        assert nodes[0].kind == MetaClauseKind.COMMENCEMENT
        assert "2025" in nodes[0].raw_text


# ---------------------------------------------------------------------------
# 10. Multi-verb clause
# ---------------------------------------------------------------------------

class TestMultiVerbClause:
    """Multi-verb clause produces multiple VerbGroups in source order."""

    def test_replace_then_repeal(self):
        ast = lower_to_clause_ast(_clause(
            _vg(VerbKind.MUUTTAA, _tref(label="5")),
            _vg(VerbKind.KUMOTA, _tref(label="8")),
        ))
        assert len(ast.verb_groups) == 2
        assert ast.verb_groups[0].verb == StructuralAction.REPLACE
        assert ast.verb_groups[1].verb == StructuralAction.REPEAL

    def test_insert_then_repeal_then_replace(self):
        ast = lower_to_clause_ast(_clause(
            _vg(VerbKind.LISATA, ResolvedInsertion(kind=TargetKind.SECTION, label="9a")),
            _vg(VerbKind.KUMOTA, _tref(label="10")),
            _vg(VerbKind.MUUTTAA, _tref(label="11")),
        ))
        assert len(ast.verb_groups) == 3
        assert ast.verb_groups[0].verb == StructuralAction.INSERT
        assert ast.verb_groups[1].verb == StructuralAction.REPEAL
        assert ast.verb_groups[2].verb == StructuralAction.REPLACE


# ---------------------------------------------------------------------------
# 11. Empty clause
# ---------------------------------------------------------------------------

class TestEmptyClause:
    """Empty ResolvedSurfaceClause produces empty ClauseAST."""

    def test_empty(self):
        ast = lower_to_clause_ast(ResolvedSurfaceClause(verb_groups=()))
        assert isinstance(ast, ClauseAST)
        assert ast.verb_groups == ()
        assert ast.source_text == ""


# ---------------------------------------------------------------------------
# 12. Witness provenance threaded through
# ---------------------------------------------------------------------------

class TestWitnessProvenance:
    """Source witness fields are carried to RefAmend / LabelAmend."""

    def test_witness_rule_id_carried(self):
        sw = SurfaceWitness(rule_id="target.section_ref", source_span=(0, 3))
        tref = ResolvedTargetRef(
            kind=TargetKind.SECTION,
            label="7",
            surface_witness=sw,
        )
        ast = lower_to_clause_ast(_clause(_vg(VerbKind.MUUTTAA, tref)))
        node = _flatten_nodes(ast)[0]
        assert isinstance(node, RefAmend)
        assert node.witness_rule_id == "target.section_ref"
        assert node.source_tokens == (0, 3)


# ---------------------------------------------------------------------------
# 13. Round-trip validation: native path vs parsed bridge path
#
# For a selection of curated johtolause texts:
#   native:  text -> parse_to_surface -> resolve_surface_clause -> lower_to_clause_ast
#   bridge:  text -> extract_ops -> build_clause_ast
#
# The ClauseASTs should produce equivalent LegalOperation lists (same
# action/target structure) when flattened via clause_ast_to_legal_ops().
# ---------------------------------------------------------------------------

# Select a focused set of curated cases covering the key node types:
# - simple section refs, multi-section, chapter-scoped, momentti, insertion,
#   multi-verb, repeal, renumber.
_ROUND_TRIP_TEXTS = [
    "muutetaan 12 §",
    "kumotaan 7 §",
    "lisätään 8 §:ään uusi 3 momentti",
    "muutetaan 3, 5 ja 7 §",
    "muutetaan 21–23 §",
    "muutetaan 5 §:n 2 momentti",
    "muutetaan 70 §:n 2 momentin 1 ja 3 kohta",
    "muutetaan 3 luvun 12 §:n 2 momentti",
    "kumotaan 3 luku",
    "muutetaan 5 luvun otsikko",
    "lisätään lakiin uusi 5 a §",
    "muutetaan 3 §, kumotaan 5 §",
    "muutetaan 5 §:n 2 momentti ja kumotaan 7 §",
]


def _op_signature(lo) -> tuple:
    """Return a stable comparison key for a LegalOperation."""
    path_key = tuple(lo.target.path)
    return (lo.action, path_key, lo.target.special)


# ---------------------------------------------------------------------------
# 14. Move semantics round-trip: LabelAmend -> LegalOperation -> back
# ---------------------------------------------------------------------------

class TestMoveSemanticsPipeline:
    """Move operations preserve from→to through the full lowering pipeline."""

    def test_move_through_legal_operation_round_trip(self):
        """LabelAmend(target=source, destination=dest) → LegalOperation → LabelAmend."""
        from lawvm.core.clause_ast import clause_node_to_legal_operation, legal_op_to_clause_node

        source = _addr(("chapter", "3"), ("section", "5"))
        dest = _addr(("chapter", "3"), ("section", "7"))
        node = LabelAmend(
            action=LabelAction.RENUMBER,
            target=source,
            new_label="7",
            destination=dest,
        )
        # Forward: LabelAmend -> LegalOperation
        lo = clause_node_to_legal_operation(node)
        assert lo is not None
        assert lo.action == StructuralAction.RENUMBER
        assert lo.target == source  # source preserved
        assert lo.destination == dest  # destination preserved

        # Reverse: LegalOperation -> LabelAmend
        roundtripped = legal_op_to_clause_node(lo)
        assert isinstance(roundtripped, LabelAmend)
        assert roundtripped.target == source
        assert roundtripped.destination == dest
        assert roundtripped.new_label == "7"

    def test_cross_part_move_through_pipeline(self):
        """Cross-part move survives native lowering → LegalOperation."""
        tref = _tref(label="38", part="II", renumber_dest="38", renumber_dest_part="I")
        ast = lower_to_clause_ast(_clause(_vg(VerbKind.SIIRTAA, tref)))
        ops = clause_ast_to_legal_ops(ast)
        assert len(ops) == 1
        lo = ops[0]
        # Source address
        assert lo.target == _addr(("part", "II"), ("section", "38"))
        # Destination address
        assert lo.destination == _addr(("part", "I"), ("section", "38"))


# ---------------------------------------------------------------------------
# 15. is_exception propagation through the full pipeline
# ---------------------------------------------------------------------------

class TestIsExceptionPropagation:
    """is_exception=True on SurfaceTargetRef survives all phases to RefAmend."""

    def test_exception_flag_reaches_ref_amend(self):
        """ResolvedTargetRef(is_exception=True) -> RefAmend(is_exception=True)."""
        tref = ResolvedTargetRef(
            kind=TargetKind.SECTION,
            label="73",
            chapter="7",
            is_exception=True,
        )
        ast = lower_to_clause_ast(_clause(_vg(VerbKind.MUUTTAA, tref)))
        nodes = _flatten_nodes(ast)
        assert len(nodes) == 1
        assert isinstance(nodes[0], RefAmend)
        assert nodes[0].is_exception is True

    def test_non_exception_flag_default_false(self):
        """Normal ResolvedTargetRef yields RefAmend(is_exception=False)."""
        tref = _tref(label="5")
        ast = lower_to_clause_ast(_clause(_vg(VerbKind.MUUTTAA, tref)))
        nodes = _flatten_nodes(ast)
        assert isinstance(nodes[0], RefAmend)
        assert nodes[0].is_exception is False

    def test_exception_flag_with_sub_refs(self):
        """is_exception propagates when sub_refs expand to multiple nodes."""
        tref = ResolvedTargetRef(
            kind=TargetKind.SECTION,
            label="73",
            chapter="7",
            sub_refs=(SurfaceSubRef(momentti=1), SurfaceSubRef(momentti=2)),
            is_exception=True,
        )
        ast = lower_to_clause_ast(_clause(_vg(VerbKind.MUUTTAA, tref)))
        nodes = _flatten_nodes(ast)
        assert len(nodes) == 2
        for node in nodes:
            assert isinstance(node, RefAmend)
            assert node.is_exception is True

    def test_exception_surface_to_resolved_to_clause_ast(self):
        """End-to-end: parse text with lukuun ottamatta -> RefAmend(is_exception=True)."""
        from lawvm.finland.johtolause.lift_to_surface import parse_to_surface
        from lawvm.finland.johtolause.surface_resolve import resolve_surface_clause

        text = "muutetaan 4–7 luku, lukuun ottamatta kuitenkaan 7 luvun 73 §:ää"
        surface = parse_to_surface(text)
        resolved = resolve_surface_clause(surface)
        ast = lower_to_clause_ast(resolved)
        all_nodes = _flatten_nodes(ast)
        # At least one node should be an exception
        exception_nodes = [n for n in all_nodes if isinstance(n, RefAmend) and n.is_exception]
        assert exception_nodes, (
            f"Expected at least one RefAmend with is_exception=True; got: {all_nodes}"
        )
        # The exception node targets section 73 in chapter 7
        exc = exception_nodes[0]
        assert any(p == ("section", "73") for p in exc.target.path)


class TestRoundTripBridge:
    """Native path ClauseAST is op-equivalent to bridge path ClauseAST."""

    @pytest.mark.parametrize("text", _ROUND_TRIP_TEXTS)
    def test_roundtrip(self, text: str) -> None:
        from lawvm.finland.johtolause.compat import parse_clause
        from lawvm.finland.johtolause.lift_to_surface import parse_to_surface
        from lawvm.finland.johtolause.surface_resolve import resolve_surface_clause

        # Native path
        surface = parse_to_surface(text)
        resolved = resolve_surface_clause(surface)
        native_ast = lower_to_clause_ast(resolved)
        native_ops = clause_ast_to_legal_ops(native_ast)

        # Bridge path (via parse_clause)
        bridge_parsed_ops = parse_clause(text).parsed_ops
        bridge_ast = build_clause_ast(bridge_parsed_ops, text)
        bridge_ops = clause_ast_to_legal_ops(bridge_ast)

        native_sigs = [_op_signature(op) for op in native_ops]
        bridge_sigs = [_op_signature(op) for op in bridge_ops]

        assert native_sigs == bridge_sigs, (
            f"Round-trip mismatch for {text!r}:\n"
            f"  native:  {native_sigs}\n"
            f"  bridge:  {bridge_sigs}"
        )

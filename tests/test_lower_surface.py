"""Tests for the Phase 3 surface model and lowering bridge.

Validates that:
1. SurfaceClause types can represent parser phenomena
2. The bridge (lower_surface_clause_to_parsed_ops) produces ParsedOps
   matching what the current parser produces
"""

from __future__ import annotations

import pytest
from typing import Any, cast

from lawvm.core.semantic_types import FacetKind
from lawvm.finland.johtolause.lower_surface import lower_surface_clause_to_parsed_ops
from lawvm.finland.johtolause.surface_parse import _surface_target_kind_for_pair_kind
from lawvm.finland.johtolause.surface_model import (
    ScopeKind,
    SurfaceClause,
    SurfaceDescendantCoordination,
    SurfaceHeadingPlacement,
    SurfaceInsertion,
    SurfaceMoveTail,
    SurfaceScopeBlock,
    SurfaceSubRef,
    SurfaceTargetRef,
    SurfaceVerbGroup,
    TargetKind,
    VerbKind,
)
from lawvm.finland.johtolause.types import ParsedOp


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _op(
    verb: str,
    kind: str,
    chapter: str,
    number: str,
    momentti: int = 0,
    item: str = "",
    facet: FacetKind | None = None,
    part: str = "",
) -> ParsedOp:
    """Shorthand for creating a ParsedOp with computed raw code."""
    op = ParsedOp(
        verb=verb,
        kind=kind,
        chapter=chapter,
        number=number,
        momentti=momentti,
        item=item,
        facet=facet,
        raw="",
        part=part,
    )
    op.raw = op.code()
    return op


def _codes(ops: list[ParsedOp]) -> list[str]:
    """Extract op-code strings for easy comparison."""
    return [op.code() for op in ops]


# ---------------------------------------------------------------------------
# Proof-of-concept: single REPLACE section reference
# ---------------------------------------------------------------------------


class TestSingleReplaceSectionRef:
    """Proof-of-concept: construct a SurfaceClause for 'muutetaan 7 §'
    and verify the bridge produces the same ParsedOp as the parser.
    """

    def test_single_section_replace(self):
        """'muutetaan 7 §' -> M P 7."""
        clause = SurfaceClause(
            verb_groups=(
                SurfaceVerbGroup(
                    verb=VerbKind.MUUTTAA,
                    nodes=(
                        SurfaceTargetRef(
                            kind=TargetKind.SECTION,
                            label="7",
                        ),
                    ),
                ),
            ),
        )

        ops = lower_surface_clause_to_parsed_ops(clause)

        assert len(ops) == 1
        assert ops[0].code() == "M P 7"
        assert ops[0].verb == "M"
        assert ops[0].kind == "P"
        assert ops[0].number == "7"
        assert ops[0].chapter == ""
        assert ops[0].momentti == 0

    def test_matches_parser_output(self):
        """Verify bridge output matches actual parser for 'muutetaan 7 §'."""
        from lawvm.finland.johtolause.api import parse_clause

        # What the parser produces
        parser_result = parse_clause("muutetaan 7 §")
        parser_ops = parser_result.parsed_ops

        # What the bridge produces from an equivalent SurfaceClause
        clause = SurfaceClause(
            verb_groups=(
                SurfaceVerbGroup(
                    verb=VerbKind.MUUTTAA,
                    nodes=(
                        SurfaceTargetRef(
                            kind=TargetKind.SECTION,
                            label="7",
                        ),
                    ),
                ),
            ),
        )
        bridge_ops = lower_surface_clause_to_parsed_ops(clause)

        assert _codes(parser_ops) == _codes(bridge_ops)


# ---------------------------------------------------------------------------
# Section with chapter context
# ---------------------------------------------------------------------------


class TestSectionWithChapter:
    """'muutetaan 3 luvun 12 §' -> M P L:3 12."""

    def test_chapter_scoped_section(self):
        clause = SurfaceClause(
            verb_groups=(
                SurfaceVerbGroup(
                    verb=VerbKind.MUUTTAA,
                    nodes=(
                        SurfaceTargetRef(
                            kind=TargetKind.SECTION,
                            label="12",
                            chapter="3",
                        ),
                    ),
                ),
            ),
        )

        ops = lower_surface_clause_to_parsed_ops(clause)
        assert _codes(ops) == ["M P L:3 12"]

    def test_matches_parser_output(self):
        from lawvm.finland.johtolause.api import parse_clause

        parser_result = parse_clause("muutetaan 3 luvun 12 §")
        parser_ops = parser_result.parsed_ops
        clause = SurfaceClause(
            verb_groups=(
                SurfaceVerbGroup(
                    verb=VerbKind.MUUTTAA,
                    nodes=(
                        SurfaceTargetRef(
                            kind=TargetKind.SECTION,
                            label="12",
                            chapter="3",
                        ),
                    ),
                ),
            ),
        )
        bridge_ops = lower_surface_clause_to_parsed_ops(clause)
        assert _codes(parser_ops) == _codes(bridge_ops)


# ---------------------------------------------------------------------------
# Section with sub-reference (momentti)
# ---------------------------------------------------------------------------


class TestSectionWithMomenttiSubRef:
    """'muutetaan 7 §:n 2 momentti' -> M P 7 2."""

    def test_section_with_momentti(self):
        clause = SurfaceClause(
            verb_groups=(
                SurfaceVerbGroup(
                    verb=VerbKind.MUUTTAA,
                    nodes=(
                        SurfaceTargetRef(
                            kind=TargetKind.SECTION,
                            label="7",
                            sub_refs=(SurfaceSubRef(momentti=2),),
                        ),
                    ),
                ),
            ),
        )

        ops = lower_surface_clause_to_parsed_ops(clause)
        assert _codes(ops) == ["M P 7 2"]

    def test_matches_parser_output(self):
        from lawvm.finland.johtolause.api import parse_clause

        parser_result = parse_clause("muutetaan 7 §:n 2 momentti")
        parser_ops = parser_result.parsed_ops
        clause = SurfaceClause(
            verb_groups=(
                SurfaceVerbGroup(
                    verb=VerbKind.MUUTTAA,
                    nodes=(
                        SurfaceTargetRef(
                            kind=TargetKind.SECTION,
                            label="7",
                            sub_refs=(SurfaceSubRef(momentti=2),),
                        ),
                    ),
                ),
            ),
        )
        bridge_ops = lower_surface_clause_to_parsed_ops(clause)
        assert _codes(parser_ops) == _codes(bridge_ops)


# ---------------------------------------------------------------------------
# Multiple targets in one verb group
# ---------------------------------------------------------------------------


class TestMultipleTargetsOneVerbGroup:
    """'muutetaan 5 ja 6 §' -> M P 5, M P 6."""

    def test_two_sections(self):
        clause = SurfaceClause(
            verb_groups=(
                SurfaceVerbGroup(
                    verb=VerbKind.MUUTTAA,
                    nodes=(
                        SurfaceTargetRef(kind=TargetKind.SECTION, label="5"),
                        SurfaceTargetRef(kind=TargetKind.SECTION, label="6"),
                    ),
                ),
            ),
        )

        ops = lower_surface_clause_to_parsed_ops(clause)
        assert _codes(ops) == ["M P 5", "M P 6"]


# ---------------------------------------------------------------------------
# Multi-verb clause
# ---------------------------------------------------------------------------


class TestMultiVerbClause:
    """'muutetaan 5 §, kumotaan 8 §' -> M P 5, K P 8."""

    def test_replace_and_repeal(self):
        clause = SurfaceClause(
            verb_groups=(
                SurfaceVerbGroup(
                    verb=VerbKind.MUUTTAA,
                    nodes=(SurfaceTargetRef(kind=TargetKind.SECTION, label="5"),),
                ),
                SurfaceVerbGroup(
                    verb=VerbKind.KUMOTA,
                    nodes=(SurfaceTargetRef(kind=TargetKind.SECTION, label="8"),),
                ),
            ),
        )

        ops = lower_surface_clause_to_parsed_ops(clause)
        assert _codes(ops) == ["M P 5", "K P 8"]


# ---------------------------------------------------------------------------
# Chapter reference
# ---------------------------------------------------------------------------


class TestChapterRef:
    """'muutetaan 3 luvun otsikko' -> M L 3 o."""

    def test_chapter_heading(self):
        clause = SurfaceClause(
            verb_groups=(
                SurfaceVerbGroup(
                    verb=VerbKind.MUUTTAA,
                    nodes=(
                        SurfaceTargetRef(
                            kind=TargetKind.CHAPTER,
                            label="3",
                            sub_refs=(SurfaceSubRef(facet=FacetKind.HEADING),),
                        ),
                    ),
                ),
            ),
        )

        ops = lower_surface_clause_to_parsed_ops(clause)
        assert _codes(ops) == ["M L 3 o"]

    def test_chapter_intro(self):
        clause = SurfaceClause(
            verb_groups=(
                SurfaceVerbGroup(
                    verb=VerbKind.MUUTTAA,
                    nodes=(
                        SurfaceTargetRef(
                            kind=TargetKind.CHAPTER,
                            label="3",
                            sub_refs=(SurfaceSubRef(facet=FacetKind.INTRO),),
                        ),
                    ),
                ),
            ),
        )

        ops = lower_surface_clause_to_parsed_ops(clause)
        assert _codes(ops) == ["M L 3 j"]


# ---------------------------------------------------------------------------
# Insertion
# ---------------------------------------------------------------------------


class TestInsertion:
    """'lisätään uusi 5 a §' -> L P 5a."""

    def test_section_insertion(self):
        clause = SurfaceClause(
            verb_groups=(
                SurfaceVerbGroup(
                    verb=VerbKind.LISATA,
                    nodes=(
                        SurfaceInsertion(
                            kind=TargetKind.SECTION,
                            label="5a",
                        ),
                    ),
                ),
            ),
        )

        ops = lower_surface_clause_to_parsed_ops(clause)
        assert _codes(ops) == ["L P 5a"]


# ---------------------------------------------------------------------------
# Insertion with sub-target (momentti)
# ---------------------------------------------------------------------------


class TestInsertionWithSubTarget:
    """'lisätään 7 §:ään uusi 3 momentti' -> L P 7 3."""

    def test_momentti_insertion(self):
        clause = SurfaceClause(
            verb_groups=(
                SurfaceVerbGroup(
                    verb=VerbKind.LISATA,
                    nodes=(
                        SurfaceInsertion(
                            kind=TargetKind.SECTION,
                            label="7",
                            sub_target=SurfaceSubRef(momentti=3),
                        ),
                    ),
                ),
            ),
        )

        ops = lower_surface_clause_to_parsed_ops(clause)
        assert _codes(ops) == ["L P 7 3"]


# ---------------------------------------------------------------------------
# HeadingPlacement
# ---------------------------------------------------------------------------


class TestHeadingPlacement:
    """SurfaceHeadingPlacement -> otsikko op."""

    def test_heading_placement(self):
        clause = SurfaceClause(
            verb_groups=(
                SurfaceVerbGroup(
                    verb=VerbKind.LISATA,
                    nodes=(
                        SurfaceHeadingPlacement(
                            target_section="53",
                            chapter="3",
                        ),
                    ),
                ),
            ),
        )

        ops = lower_surface_clause_to_parsed_ops(clause)
        assert _codes(ops) == ["L P L:3 53 o"]


# ---------------------------------------------------------------------------
# MoveTail application
# ---------------------------------------------------------------------------


class TestMoveTail:
    """SurfaceMoveTail patches preceding target's chapter."""

    def test_move_tail_patches_chapter(self):
        clause = SurfaceClause(
            verb_groups=(
                SurfaceVerbGroup(
                    verb=VerbKind.MUUTTAA,
                    nodes=(
                        SurfaceTargetRef(kind=TargetKind.SECTION, label="33"),
                        SurfaceMoveTail(destination_chapter="5"),
                    ),
                ),
            ),
        )

        ops = lower_surface_clause_to_parsed_ops(clause)
        assert len(ops) == 1
        assert ops[0].chapter == "5"
        assert ops[0].move_clause_target_unit_kind == "chapter"
        assert "move_clause_target_chapter" not in ops[0].notes

    def test_move_tail_preserves_existing_chapter_scope(self):
        clause = SurfaceClause(
            verb_groups=(
                SurfaceVerbGroup(
                    verb=VerbKind.MUUTTAA,
                    nodes=(
                        SurfaceTargetRef(kind=TargetKind.SECTION, label="33", chapter="2"),
                        SurfaceMoveTail(destination_chapter="5"),
                    ),
                ),
            ),
        )

        ops = lower_surface_clause_to_parsed_ops(clause)
        assert len(ops) == 1
        assert ops[0].chapter == "2"
        assert ops[0].move_clause_target_unit_kind == "chapter"

    def test_move_tail_preserves_part_scope_inside_scope_block(self):
        clause = SurfaceClause(
            verb_groups=(
                SurfaceVerbGroup(
                    verb=VerbKind.SIIRTAA,
                    nodes=(
                        SurfaceScopeBlock(
                            scope_kind=ScopeKind.PART,
                            scope_label="II",
                            targets=(SurfaceTargetRef(kind=TargetKind.SECTION, label="33"),),
                        ),
                        SurfaceMoveTail(destination_part="III"),
                    ),
                ),
            ),
        )

        ops = lower_surface_clause_to_parsed_ops(clause)
        assert len(ops) == 1
        assert _codes(ops) == ["S P O:II 33"]
        assert ops[0].part == "II"
        assert ops[0].chapter == ""
        assert ops[0].move_clause_target_unit_kind == "part"


# ---------------------------------------------------------------------------
# DescendantCoordination
# ---------------------------------------------------------------------------


class TestDescendantCoordination:
    """SurfaceDescendantCoordination expands base + arms."""

    def test_coordination(self):
        clause = SurfaceClause(
            verb_groups=(
                SurfaceVerbGroup(
                    verb=VerbKind.MUUTTAA,
                    nodes=(
                        SurfaceDescendantCoordination(
                            base=SurfaceTargetRef(
                                kind=TargetKind.SECTION,
                                label="5",
                            ),
                            arms=(
                                SurfaceSubRef(momentti=1),
                                SurfaceSubRef(momentti=3),
                                SurfaceSubRef(momentti=2, facet=FacetKind.INTRO),
                            ),
                        ),
                    ),
                ),
            ),
        )

        ops = lower_surface_clause_to_parsed_ops(clause)
        assert _codes(ops) == ["M P 5 1", "M P 5 3", "M P 5 2 j"]


# ---------------------------------------------------------------------------
# VerbKind and TargetKind enums
# ---------------------------------------------------------------------------


class TestEnums:
    """Verify enum construction from codes."""

    def test_verb_kind_from_code(self):
        assert VerbKind.from_code("M") == VerbKind.MUUTTAA
        assert VerbKind.from_code("K") == VerbKind.KUMOTA
        assert VerbKind.from_code("L") == VerbKind.LISATA
        assert VerbKind.from_code("S") == VerbKind.SIIRTAA

    def test_target_kind_helpers(self):
        assert TargetKind.for_leaf_kind("section") == TargetKind.SECTION
        assert TargetKind.for_leaf_kind("subsection") == TargetKind.SECTION
        assert TargetKind.CHAPTER.leaf_kind() == "chapter"

    def test_pair_kind_projection_helper(self):
        assert _surface_target_kind_for_pair_kind("P") == TargetKind.SECTION
        assert _surface_target_kind_for_pair_kind("L") == TargetKind.CHAPTER
        assert _surface_target_kind_for_pair_kind("x") == TargetKind.SECTION

    def test_verb_kind_value(self):
        assert VerbKind.MUUTTAA.value == "M"
        assert VerbKind.KUMOTA.value == "K"

    def test_target_kind_value(self):
        assert TargetKind.SECTION.value == "P"
        assert TargetKind.CHAPTER.value == "L"


# ---------------------------------------------------------------------------
# Frozen immutability
# ---------------------------------------------------------------------------


class TestFrozen:
    """Verify surface types are truly frozen."""

    def test_target_ref_frozen(self):
        ref = SurfaceTargetRef(kind=TargetKind.SECTION, label="7")
        with pytest.raises(AttributeError):
            cast(Any, ref).label = "changed"

    def test_clause_frozen(self):
        clause = SurfaceClause(verb_groups=())
        with pytest.raises(AttributeError):
            cast(Any, clause).verb_groups = ()


# ---------------------------------------------------------------------------
# ScopeBlock
# ---------------------------------------------------------------------------


class TestScopeBlock:
    """SurfaceScopeBlock applies chapter scope to enclosed targets."""

    def test_scope_block_applies_chapter(self):
        clause = SurfaceClause(
            verb_groups=(
                SurfaceVerbGroup(
                    verb=VerbKind.MUUTTAA,
                    nodes=(
                        SurfaceScopeBlock(
                            scope_kind=ScopeKind.CHAPTER,
                            scope_label="3",
                            targets=(
                                SurfaceTargetRef(kind=TargetKind.SECTION, label="5"),
                                SurfaceTargetRef(kind=TargetKind.SECTION, label="7"),
                            ),
                        ),
                    ),
                ),
            ),
        )

        ops = lower_surface_clause_to_parsed_ops(clause)
        assert _codes(ops) == ["M P L:3 5", "M P L:3 7"]


# ---------------------------------------------------------------------------
# Empty clause
# ---------------------------------------------------------------------------


class TestEmptyClause:
    """Empty SurfaceClause produces no ops."""

    def test_empty(self):
        clause = SurfaceClause(verb_groups=())
        ops = lower_surface_clause_to_parsed_ops(clause)
        assert ops == []

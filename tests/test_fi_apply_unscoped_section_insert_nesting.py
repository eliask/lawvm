"""Unscoped section INSERTs join an unambiguous enclosing chapter, not body level.

A Finnish amendment often adds ``uusi 48 a §`` (or re-adds a repealed
``46―48 §`` cluster) with no chapter citation in the johtolause. Historically
the whole-section INSERT parent resolver dropped to the body-level provisions
wrapper and hoisted the new section out as a bare sibling of the chapters — the
``mixed_hierarchy`` structural defect (issue #145). When the new section's live
numeric neighbours (predecessor + successor) agree on a single enclosing
chapter, the gap being filled is interior to that chapter and the section joins
it. When the neighbours straddle a chapter boundary the placement is genuinely
ambiguous and the rule declines, preserving the prior fallback.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, cast

import pytest

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.apply_structure_ops import (
    _agreeing_neighbor_chapter_for_unscoped_section_insert,
)

if TYPE_CHECKING:
    from lawvm.finland.apply_structure_ops import ReplayState


def _section(label: str) -> IRNode:
    return IRNode(kind=IRNodeKind.SECTION, label=label)


def _chapter(label: str, *sections: str) -> IRNode:
    return IRNode(
        kind=IRNodeKind.CHAPTER,
        label=label,
        children=tuple(_section(s) for s in sections),
    )


class _FakeState:
    def __init__(self, ir: IRNode) -> None:
        self.ir = ir


def _chaptered_tree() -> IRNode:
    # body → hcontainer → [chapter 7 (§40..45), chapter 8 (§45..50), chapter 13 (§76..78)]
    return IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.HCONTAINER,
                children=(
                    _chapter("7", "40", "41", "44"),
                    _chapter("8", "45", "49", "50"),
                    _chapter("13", "76", "77", "78"),
                ),
            ),
        ),
    )


def test_interior_gap_between_agreeing_neighbours_binds_that_chapter() -> None:
    state = cast("ReplayState", _FakeState(_chaptered_tree()))
    # §46 sits between §45 (ch8) and §49 (ch8) → both agree on chapter 8.
    assert _agreeing_neighbor_chapter_for_unscoped_section_insert(state, "46") == "8"
    # §48 likewise interior to chapter 8.
    assert _agreeing_neighbor_chapter_for_unscoped_section_insert(state, "48") == "8"


def test_letter_suffix_section_binds_its_base_chapter() -> None:
    state = cast("ReplayState", _FakeState(_chaptered_tree()))
    # §48a orders between §45/§49 (both ch8) → chapter 8.
    assert _agreeing_neighbor_chapter_for_unscoped_section_insert(state, "48a") == "8"


def test_chapter_boundary_straddle_declines() -> None:
    state = cast("ReplayState", _FakeState(_chaptered_tree()))
    # §60 sits between §50 (ch8) and §76 (ch13) → neighbours disagree → decline.
    assert _agreeing_neighbor_chapter_for_unscoped_section_insert(state, "60") is None


def test_trailing_tail_without_successor_declines() -> None:
    state = cast("ReplayState", _FakeState(_chaptered_tree()))
    # §90 has no chaptered successor → ambiguous tail → decline (do not guess a
    # final-provisions chapter that may or may not exist).
    assert _agreeing_neighbor_chapter_for_unscoped_section_insert(state, "90") is None


def test_body_level_neighbours_are_not_chapter_anchors() -> None:
    # Neighbour sections that live at body level (no enclosing chapter) provide
    # no chapter anchor; the rule declines rather than inventing scope.
    ir = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.HCONTAINER,
                children=(_section("45"), _section("49")),
            ),
        ),
    )
    state = cast("ReplayState", _FakeState(ir))
    assert _agreeing_neighbor_chapter_for_unscoped_section_insert(state, "46") is None


def _corpus_available() -> bool:
    return bool(os.environ.get("LAWVM_CANONICAL_DATA_ROOT"))


def _section_chapter_map(ir: IRNode) -> dict[str, str | None]:
    out: dict[str, str | None] = {}

    def walk(node: IRNode, chapter: str | None) -> None:
        for child in node.children:
            kind = str(getattr(child.kind, "value", child.kind))
            next_chapter = child.label if kind == "chapter" else chapter
            if kind == "section" and child.label:
                out[child.label] = chapter
            walk(child, next_chapter)

    walk(ir, None)
    return out


@pytest.mark.skipif(
    not _corpus_available(),
    reason="LAWVM_CANONICAL_DATA_ROOT not set; real-corpus INSERT-nesting check skipped",
)
@pytest.mark.slow
def test_1993_1072_unscoped_suffix_sections_nest_in_chapter_8() -> None:
    """Regression for #145: §46,47,48,48a,48b,48c land in chapter 8, not body level.

    2007/747 adds ``lakiin uusi 46―48 § ja lakiin uusi 48 a―48 c §`` with no
    chapter scope. Their live neighbours §45 and §49 both sit in chapter 8, so
    every one of them must nest there rather than hoist to a bare body-level
    ``hcontainer`` sibling of the chapters.
    """
    from lawvm.finland.replay_entrypoint import replay_xml
    from lawvm.finland.replay_request import ReplayXmlRequest

    result = replay_xml(
        request=ReplayXmlRequest(parent_id="1993/1072", mode="legal_pit", quiet=True)
    )
    chapter_of = _section_chapter_map(result.products.materialized_state.ir)
    for label in ("46", "47", "48", "48a", "48b", "48c"):
        assert chapter_of.get(label) == "8", (
            f"section {label} expected in chapter 8, found in {chapter_of.get(label)!r}"
        )

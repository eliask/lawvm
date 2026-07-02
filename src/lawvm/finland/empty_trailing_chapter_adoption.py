"""Adopt base-authored trailing flat sections into a base-declared empty chapter.

Some Finnish statutes declare a trailing chapter (typically ``13 luku
Voimaantulo``) whose ``<chapter>`` element is closed with zero sections, and
then place the final-provisions sections (``78 §``, ``79 §``) as flat siblings
that follow the chapter in document order. The as-enacted editor closed the
chapter element too early; the base source itself declares the target chapter
for those trailing sections, and the in-force consolidation nests them into it.

This is a base-tree repair, not an apply-time inference: it runs once on the
as-enacted base IR before the amendment loop. Because it runs on the base, an
empty chapter is authored-empty by construction (never repealed-empty), which
is the guard the analysis requires. It fires only when the empty chapter is the
LAST container and is immediately followed by a bare block of flat sections.

Verified oracle-correct: 2011/311 (empty ``13 luku Voimaantulo`` → §78, §79)
and 2001/275 (empty ``6 luku Voimaantulo ja siirtymäsäännökset`` → §33, §34,
§35).
"""

from __future__ import annotations

from dataclasses import dataclass, replace as dc_replace

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core import tree_ops as _tops


@dataclass(frozen=True, slots=True)
class EmptyTrailingChapterAdoption:
    """One trailing empty chapter that adopted a block of flat sections."""

    chapter_label: str
    adopted_section_labels: tuple[str, ...]


def adopt_empty_trailing_chapter_flat_sections(
    base_ir: IRNode,
) -> tuple[IRNode, tuple[EmptyTrailingChapterAdoption, ...]]:
    """Move base trailing flat sections into a preceding base-empty chapter.

    Returns the (possibly rewritten) tree and the adoptions performed. The tree
    is returned unchanged (identity) when no adoption applies.
    """
    parent_path = _tops.find_provisions_parent(base_ir)
    parent = _tops.resolve(base_ir, parent_path) if parent_path else base_ir
    if parent is None:
        return base_ir, ()

    children = list(parent.children)

    # Locate the last CHAPTER child and require that only flat sections follow it
    # (a bare trailing block). A PART after it, or another chapter, disqualifies.
    last_chapter_index: int | None = None
    for index, child in enumerate(children):
        if child.kind is IRNodeKind.CHAPTER:
            last_chapter_index = index
        elif child.kind is IRNodeKind.PART:
            # A part regime is out of scope for this simple trailing adoption.
            return base_ir, ()
    if last_chapter_index is None:
        return base_ir, ()

    chapter = children[last_chapter_index]
    # Authored-empty gate: the chapter must carry no sections of its own.
    if any(c.kind is IRNodeKind.SECTION for c in chapter.children):
        return base_ir, ()

    trailing = children[last_chapter_index + 1 :]
    if not trailing:
        return base_ir, ()
    # Everything after the empty chapter must be flat sections (a bare block);
    # any other structural node means this is not the closed-too-early shape.
    trailing_sections = [c for c in trailing if c.kind is IRNodeKind.SECTION and c.label]
    if len(trailing_sections) != len(trailing):
        return base_ir, ()

    new_chapter = dc_replace(
        chapter,
        children=tuple(chapter.children) + tuple(trailing_sections),
    )
    new_children = children[:last_chapter_index] + [new_chapter]
    new_parent = dc_replace(parent, children=tuple(new_children))
    if parent_path:
        new_ir = _tops.replace_at(base_ir, parent_path, new_parent)
    else:
        new_ir = new_parent

    adoption = EmptyTrailingChapterAdoption(
        chapter_label=chapter.label or "",
        adopted_section_labels=tuple(c.label or "" for c in trailing_sections),
    )
    return new_ir, (adoption,)

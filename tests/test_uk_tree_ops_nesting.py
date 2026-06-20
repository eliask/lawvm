"""Tree-invariant nesting rules for UK canonical structure.

UK source XML commonly places crossheading/Pblock wrappers directly inside
parts/chapters, wraps sections inside P1group wrappers, and (in older or
Scottish statutes) places paragraphs directly inside sections.  These shapes
are legitimate for the UK frontend and must not be flagged as unexpected
children by the shared core invariant scanner.
"""
from __future__ import annotations

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.tree_ops import TreeInvariantKind, iter_tree_invariant_violations


def _uk_part_with_crossheading_and_p1group() -> IRNode:
    return IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.PART,
                label="I",
                children=(
                    IRNode(
                        kind=IRNodeKind.CROSSHEADING,
                        label="secretary-of-state",
                        children=(
                            IRNode(
                                kind=IRNodeKind.P1GROUP,
                                label="",
                                children=(
                                    IRNode(
                                        kind=IRNodeKind.SECTION,
                                        label="1",
                                        children=(),
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )


def _uk_chapter_with_crossheading() -> IRNode:
    return IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.PART,
                label="I",
                children=(
                    IRNode(
                        kind=IRNodeKind.CHAPTER,
                        label="I",
                        children=(
                            IRNode(
                                kind=IRNodeKind.CROSSHEADING,
                                label="introductory",
                                children=(
                                    IRNode(
                                        kind=IRNodeKind.P1GROUP,
                                        children=(
                                            IRNode(kind=IRNodeKind.SECTION, label="1"),
                                        ),
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )


def _uk_section_with_paragraph() -> IRNode:
    """Older/Scottish UK statutes sometimes contain paragraphs directly in sections."""
    return IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.PART,
                label="II",
                children=(
                    IRNode(
                        kind=IRNodeKind.CROSSHEADING,
                        children=(
                            IRNode(
                                kind=IRNodeKind.P1GROUP,
                                children=(
                                    IRNode(
                                        kind=IRNodeKind.SECTION,
                                        label="24",
                                        children=(
                                            IRNode(
                                                kind=IRNodeKind.PARAGRAPH,
                                                label="1",
                                                children=(),
                                            ),
                                        ),
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )


def _uk_subsection_with_schedule_entry() -> IRNode:
    """UK sections/subsections may contain table rows modelled as schedule_entry."""
    return IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.PART,
                label="II",
                children=(
                    IRNode(
                        kind=IRNodeKind.CROSSHEADING,
                        children=(
                            IRNode(
                                kind=IRNodeKind.P1GROUP,
                                children=(
                                    IRNode(
                                        kind=IRNodeKind.SECTION,
                                        label="22",
                                        children=(
                                            IRNode(
                                                kind=IRNodeKind.SUBSECTION,
                                                label="1",
                                                children=(
                                                    IRNode(
                                                        kind=IRNodeKind.SCHEDULE_ENTRY,
                                                        label="1",
                                                        children=(),
                                                    ),
                                                ),
                                            ),
                                        ),
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )


def _count_violations(tree: IRNode, *, families: tuple[TreeInvariantKind, ...]) -> int:
    return sum(1 for _ in iter_tree_invariant_violations(tree, families=families))


def test_uk_part_allows_crossheading_and_p1group_sections() -> None:
    tree = _uk_part_with_crossheading_and_p1group()
    assert _count_violations(tree, families=("unexpected_child_kind",)) == 0


def test_uk_chapter_allows_crossheading_and_p1group_sections() -> None:
    tree = _uk_chapter_with_crossheading()
    assert _count_violations(tree, families=("unexpected_child_kind",)) == 0


def test_uk_section_allows_direct_paragraph() -> None:
    tree = _uk_section_with_paragraph()
    assert _count_violations(tree, families=("unexpected_child_kind",)) == 0


def test_uk_subsection_allows_schedule_entry() -> None:
    tree = _uk_subsection_with_schedule_entry()
    assert _count_violations(tree, families=("unexpected_child_kind",)) == 0

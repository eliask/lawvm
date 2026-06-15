"""Regression tests for IRNodeKind enum comparisons in repeal consolidation.

``_consolidate_kumottu_range`` merges contiguous ``... on kumottu ...`` repeal
placeholders into a single range placeholder.  Its inner walkers compared
``IRNode.kind`` against bare strings (``"section"``, ``"subsection"``,
``"content"``, ``"p"``).  Because ``IRNodeKind`` is a plain ``Enum`` (not a
``str``-mixin) those comparisons were always False, so the entire consolidation
body was dead: contiguous subsection / section repeals were never merged.

These tests construct the exact placeholder shape the replay pipeline produces
and assert the consolidation actually fires (it could not before the fix).
"""
from __future__ import annotations

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.post_process import _consolidate_kumottu_range


def _subsection_placeholder(text: str) -> IRNode:
    return IRNode(
        kind=IRNodeKind.SUBSECTION,
        children=(
            IRNode(
                kind=IRNodeKind.CONTENT,
                children=(IRNode(kind=IRNodeKind.P, text=text),),
            ),
        ),
    )


def _section_placeholder(label: str, text: str) -> IRNode:
    return IRNode(
        kind=IRNodeKind.SECTION,
        label=label,
        children=(
            IRNode(kind=IRNodeKind.NUM, text=f"{label} §"),
            _subsection_placeholder(text),
        ),
    )


def _p_texts(node: IRNode) -> list[str]:
    out: list[str] = []
    if node.kind == IRNodeKind.P and node.text:
        out.append(node.text)
    for child in node.children:
        out.extend(_p_texts(child))
    return out


def test_contiguous_subsection_repeals_merge_into_range() -> None:
    """3 contiguous momentti repeals in one section collapse to one range."""
    section = IRNode(
        kind=IRNodeKind.SECTION,
        label="5",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="5 §"),
            _subsection_placeholder("5 § 2 momentti on kumottu L:lla 1/2020."),
            _subsection_placeholder("5 § 3 momentti on kumottu L:lla 1/2020."),
            _subsection_placeholder("5 § 4 momentti on kumottu L:lla 1/2020."),
        ),
    )

    result = _consolidate_kumottu_range(section)
    texts = _p_texts(result)

    # Before the fix the dead branch left all three placeholders untouched.
    assert any("2–4 momentit on kumottu" in t for t in texts), texts
    # The three individual single-momentti placeholders are gone.
    assert not any("2 momentti on kumottu" in t for t in texts), texts
    assert not any("4 momentti on kumottu" in t for t in texts), texts


def test_contiguous_section_repeals_merge_into_range() -> None:
    """Section-level repeals under a body collapse to one range.

    Section contiguity is a letter-suffix-shadow rule: a suffixed section
    (``10a``) followed by the next plain number (``11``) sharing the same
    attribution forms a contiguous run.  Before the enum fix the ``BODY``
    branch never ran, so this never merged.
    """
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            _section_placeholder("10a", "10 a § on kumottu L:lla 2/2021."),
            _section_placeholder("11", "11 § on kumottu L:lla 2/2021."),
        ),
    )

    result = _consolidate_kumottu_range(body)
    # The two sections collapse into a single range-labelled section.
    section_children = [c for c in result.children if c.kind == IRNodeKind.SECTION]
    assert len(section_children) == 1, [c.label for c in section_children]
    assert section_children[0].label == "10a–11", section_children[0].label


def test_noncontiguous_subsection_repeals_not_merged() -> None:
    """Non-adjacent momentti repeals must remain distinct (no false merge)."""
    section = IRNode(
        kind=IRNodeKind.SECTION,
        label="7",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="7 §"),
            _subsection_placeholder("7 § 1 momentti on kumottu L:lla 9/2019."),
            _subsection_placeholder("7 § 3 momentti on kumottu L:lla 9/2019."),
        ),
    )

    result = _consolidate_kumottu_range(section)
    texts = _p_texts(result)
    assert any("1 momentti on kumottu" in t for t in texts), texts
    assert any("3 momentti on kumottu" in t for t in texts), texts
    assert not any("momentit on kumottu" in t for t in texts), texts

"""Hypothesis properties for the Finland apply seam.

These target the two apply-side failure classes from the execution plan:

* sparse item replacement must preserve untouched items
* omission markers must not authorize undeclared deletion
"""

from __future__ import annotations

import string
from typing import cast

from hypothesis import given, settings
from hypothesis import strategies as st

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.tree_ops import check_invariants
from lawvm.finland.merge import (
    _merge_sparse_alakohta_replace_ir,
    _merge_subsection_with_omission_ir,
)


SHORT_TEXT = st.text(
    alphabet=string.ascii_letters + string.digits + " .,;-",
    min_size=1,
    max_size=24,
)

LETTERS = tuple("abcdef")


def _content(text: str) -> IRNode:
    return IRNode(kind=IRNodeKind.CONTENT, text=text)


def _intro(text: str) -> IRNode:
    return IRNode(kind=IRNodeKind.INTRO, text=text)


def _num(label: str) -> IRNode:
    return IRNode(kind=IRNodeKind.NUM, text=f"{label})")


def _para(label: str, *children: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.PARAGRAPH, label=label, children=tuple(children))


def _sub(label: str, *children: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.SUBSECTION, label=label, children=tuple(children))


def _sp(label: str, text: str) -> IRNode:
    return IRNode(kind=IRNodeKind.SUBPARAGRAPH, label=label, children=(_content(text),))


def _omission() -> IRNode:
    return IRNode(kind=IRNodeKind.OMISSION)


def _node_signature(node: IRNode) -> tuple:
    return (
        node.kind.value,
        node.label,
        node.text,
        tuple(sorted(node.attrs.items())),
        tuple(_node_signature(child) for child in node.children),
    )


def _subparagraph_by_label(node: IRNode) -> dict[str, IRNode]:
    return {
        child.label: child
        for child in node.children
        if child.kind == IRNodeKind.SUBPARAGRAPH and child.label is not None
    }


@st.composite
def sparse_alakohta_case(draw) -> tuple[IRNode, IRNode, tuple[str, ...]]:
    total = draw(st.integers(min_value=2, max_value=6))
    touched_count = draw(st.integers(min_value=1, max_value=total - 1))
    master_labels = LETTERS[:total]
    touched_set = draw(st.sets(st.sampled_from(master_labels), min_size=touched_count, max_size=touched_count))
    touched = tuple(sorted([cast(str, lbl) for lbl in touched_set]))
    intro = draw(SHORT_TEXT).rstrip(" .;:!?") + ":"
    master_children = [_num("1"), _intro(intro)]
    master_children.extend(_sp(lbl, f"master {lbl}: {draw(SHORT_TEXT)}") for lbl in master_labels)
    master = _para("1", *master_children)
    amend_anchor = _para(
        "1",
        _intro(f"amend {intro}"),
        *(_sp(lbl, f"amend {lbl}: {draw(SHORT_TEXT)}") for lbl in touched),
    )
    amend = _sub("1", amend_anchor, _omission())
    return master, amend, touched


@st.composite
def omission_subsection_case(draw) -> tuple[IRNode, IRNode, tuple[str, ...]]:
    total = draw(st.integers(min_value=3, max_value=6))
    labels = tuple(str(i) for i in range(1, total + 1))
    touched_count = draw(st.integers(min_value=1, max_value=total - 1))
    touched = labels[:touched_count]
    master = _sub(
        "1",
        *(_para(lbl, _content(f"master {lbl}: {draw(SHORT_TEXT)}")) for lbl in labels),
    )
    amend = _sub(
        "1",
        *(_para(lbl, _content(f"amend {lbl}: {draw(SHORT_TEXT)}")) for lbl in touched),
        _omission(),
    )
    return master, amend, touched


@given(sparse_alakohta_case())
@settings(max_examples=100, deadline=None)
def test_sparse_item_replacement_preserves_untouched_items(case: tuple[IRNode, IRNode, tuple[str, ...]]) -> None:
    """Sparse item replacement must keep untouched subparagraphs bit-for-bit."""
    master, amend, touched = case

    result = _merge_sparse_alakohta_replace_ir(master, amend, "1")

    assert result is not None
    assert check_invariants(result) == []

    master_map = _subparagraph_by_label(master)
    result_map = _subparagraph_by_label(result)
    assert list(result_map) != []
    assert list(result_map) == list(master_map)

    for label, master_child in master_map.items():
        if label not in touched:
            assert _node_signature(result_map[label]) == _node_signature(master_child)


@given(omission_subsection_case())
@settings(max_examples=100, deadline=None)
def test_omission_markers_do_not_authorize_undeclared_deletion(
    case: tuple[IRNode, IRNode, tuple[str, ...]],
) -> None:
    """Trailing omission may preserve or bridge, but it must not drop undeclared slots."""
    master, amend, touched = case

    result = _merge_subsection_with_omission_ir(master, amend)

    assert result is not None
    assert check_invariants(result) == []

    master_paras = [c for c in master.children if c.kind == IRNodeKind.PARAGRAPH]
    result_paras = [c for c in result.children if c.kind == IRNodeKind.PARAGRAPH]
    assert [c.label for c in result_paras] == [c.label for c in master_paras]

    untouched_labels = [c.label for c in master_paras if c.label not in touched]
    untouched_result = [c for c in result_paras if c.label in untouched_labels]
    untouched_master = [c for c in master_paras if c.label in untouched_labels]
    assert len(untouched_result) == len(untouched_master)
    for got, want in zip(untouched_result, untouched_master):
        assert _node_signature(got) == _node_signature(want)

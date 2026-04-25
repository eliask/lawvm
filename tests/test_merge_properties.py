"""Hypothesis properties for the Finland merge seam.

These focus on the exact shape bugs behind the long-tail replay regressions:

* sparse section replacement must preserve untouched tail subsections
 * trailing prose after numbered items is hoisted to wrapUp only in
   rangaistussäännös-style provisions
"""

from __future__ import annotations

import string

from hypothesis import given, settings
from hypothesis import strategies as st

from lawvm.core.ir import IRNode
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.tree_ops import check_invariants
from lawvm.finland import merge as finland_merge
from lawvm.finland.grafter import _hoist_trailing_wrapup_ir
from lawvm.finland.helpers import classify_rangaistussaannos


SHORT_TEXT = st.text(
    alphabet=string.ascii_letters + string.digits + " .,;-",
    min_size=1,
    max_size=24,
)


def _content(text: str) -> IRNode:
    return IRNode(kind=IRNodeKind.CONTENT, text=text)


def _paragraph(label: str, text: str) -> IRNode:
    return IRNode(kind=IRNodeKind.PARAGRAPH, label=label, children=(_content(text),))


def _numbered_paragraph(label: str, text: str) -> IRNode:
    return IRNode(
        kind=IRNodeKind.PARAGRAPH,
        label=label,
        children=(
            IRNode(kind=IRNodeKind.NUM, text=f"{label})"),
            _content(text),
        ),
    )


def _subsection(label: str, text: str) -> IRNode:
    return IRNode(kind=IRNodeKind.SUBSECTION, label=label, children=(_content(text),))


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


@st.composite
def sparse_section_case(draw) -> tuple[IRNode, IRNode, int]:
    """Generate a master/amendment pair with a sparse section omission boundary."""
    total_subsections = draw(st.integers(min_value=2, max_value=6))
    prefix_len = draw(st.integers(min_value=1, max_value=total_subsections - 1))
    section_label = draw(st.integers(min_value=1, max_value=50).map(str))

    master_subsections = tuple(
        _subsection(str(idx), f"master {idx}: {draw(SHORT_TEXT)}")
        for idx in range(1, total_subsections + 1)
    )
    amend_subsections = tuple(
        _subsection(str(idx), f"amend {idx}: {draw(SHORT_TEXT)}")
        for idx in range(1, prefix_len + 1)
    )

    master = IRNode(kind=IRNodeKind.SECTION, label=section_label, children=master_subsections)
    amend = IRNode(kind=IRNodeKind.SECTION, label=section_label, children=(*amend_subsections, _omission()))
    return master, amend, prefix_len


@st.composite
def subsection_with_trailing_wrapup_case(draw) -> tuple[IRNode, int, str]:
    """Generate a penal-style subsection with numbered items and trailing wrapUp."""
    n_items = draw(st.integers(min_value=1, max_value=4))
    intro = "Joka " + draw(SHORT_TEXT).strip().rstrip(" .;:!?")
    numbered = [
        _numbered_paragraph(str(idx), draw(SHORT_TEXT))
        for idx in range(1, n_items + 1)
    ]
    wrapup = "on tuomittava sakkoon."
    return IRNode(
        kind=IRNodeKind.SUBSECTION,
        label="1",
        children=(
            IRNode(kind=IRNodeKind.INTRO, text=intro),
            *numbered,
            IRNode(kind=IRNodeKind.PARAGRAPH, children=(_content(wrapup),)),
        ),
    ), n_items, wrapup


@st.composite
def subsection_with_non_penal_trailing_prose_case(draw) -> tuple[IRNode, int, str]:
    """Generate an ordinary list provision whose trailing prose must not become wrapUp."""
    n_items = draw(st.integers(min_value=1, max_value=4))
    intro = draw(SHORT_TEXT).strip().rstrip(" .;:!?") + ":"
    numbered = [
        _numbered_paragraph(str(idx), draw(SHORT_TEXT))
        for idx in range(1, n_items + 1)
    ]
    prose = draw(SHORT_TEXT).strip().rstrip(" .;:!?") + "."
    return IRNode(
        kind=IRNodeKind.SUBSECTION,
        label="1",
        children=(
            IRNode(kind=IRNodeKind.INTRO, text=intro),
            *numbered,
            IRNode(kind=IRNodeKind.PARAGRAPH, children=(_content(prose),)),
        ),
    ), n_items, prose


@st.composite
def subsection_with_sparse_tail_items_case(draw) -> tuple[IRNode, IRNode, int]:
    """Generate a subsection merge pair with a sparse item omission boundary."""
    total_items = draw(st.integers(min_value=2, max_value=6))
    prefix_len = draw(st.integers(min_value=1, max_value=total_items - 1))

    master = IRNode(
        kind=IRNodeKind.SUBSECTION,
        label="1",
        children=tuple(
            _numbered_paragraph(str(idx), f"master {idx}: {draw(SHORT_TEXT)}")
            for idx in range(1, total_items + 1)
        ),
    )
    amend = IRNode(
        kind=IRNodeKind.SUBSECTION,
        label="1",
        children=(
            *(
                _numbered_paragraph(str(idx), f"amend {idx}: {draw(SHORT_TEXT)}")
                for idx in range(1, prefix_len + 1)
            ),
            _omission(),
        ),
    )
    return master, amend, prefix_len


@st.composite
def subsection_with_sparse_gap_case(draw) -> tuple[IRNode, IRNode, int]:
    """Generate a subsection merge pair with an explicit item gap around omission."""
    total_items = draw(st.integers(min_value=4, max_value=6))
    tail_label = draw(st.integers(min_value=3, max_value=total_items))

    master = IRNode(
        kind=IRNodeKind.SUBSECTION,
        label="1",
        children=tuple(
            _numbered_paragraph(str(idx), f"master {idx}: {draw(SHORT_TEXT)}")
            for idx in range(1, total_items + 1)
        ),
    )
    amend = IRNode(
        kind=IRNodeKind.SUBSECTION,
        label="1",
        children=(
            _numbered_paragraph("1", f"amend 1: {draw(SHORT_TEXT)}"),
            _omission(),
            _numbered_paragraph(str(tail_label), f"amend {tail_label}: {draw(SHORT_TEXT)}"),
        ),
    )
    return master, amend, tail_label


@given(sparse_section_case())
@settings(max_examples=100, deadline=None)
def test_sparse_section_merge_preserves_untouched_tail_subsections(
    case: tuple[IRNode, IRNode, int],
) -> None:
    """Sparse section merges must keep untouched tail subsections exactly intact."""
    master, amend, prefix_len = case

    result = finland_merge._merge_section_with_omission_ir(master, amend)

    assert result is not None
    assert check_invariants(result) == []

    master_subsections = [c for c in master.children if c.kind == IRNodeKind.SUBSECTION]
    result_subsections = [c for c in result.children if c.kind == IRNodeKind.SUBSECTION]

    assert [c.label for c in result_subsections] == [c.label for c in master_subsections]

    untouched_master = master_subsections[prefix_len:]
    untouched_result = result_subsections[prefix_len:]
    assert len(untouched_result) == len(untouched_master)
    for got, want in zip(untouched_result, untouched_master):
        assert _node_signature(got) == _node_signature(want)


@given(subsection_with_trailing_wrapup_case())
@settings(max_examples=100, deadline=None)
def test_trailing_prose_after_numbered_items_becomes_wrapup(case: tuple[IRNode, int, str]) -> None:
    """Penal-style trailing prose after numbered items must be hoisted to wrapUp."""
    subsection, n_items, wrapup = case
    assert classify_rangaistussaannos(subsection) == "yes"
    result = _hoist_trailing_wrapup_ir(subsection)

    assert result.kind == IRNodeKind.SUBSECTION
    assert check_invariants(result) == []

    paragraph_kinds = [child.kind for child in result.children if child.kind == IRNodeKind.PARAGRAPH]
    assert len(paragraph_kinds) == n_items

    assert result.children[-1].kind == IRNodeKind.WRAP_UP
    assert result.children[-1].text is not None
    assert result.children[-1].text.endswith(".")
    assert result.children[-1].text == wrapup

    original_wrap = next(child for child in subsection.children if child.kind == IRNodeKind.PARAGRAPH and child.label is None)
    assert irnode_to_text(original_wrap).strip() == wrapup


@given(subsection_with_non_penal_trailing_prose_case())
@settings(max_examples=100, deadline=None)
def test_trailing_prose_after_numbered_items_is_not_forced_into_wrapup(
    case: tuple[IRNode, int, str],
) -> None:
    """Ordinary list provisions must keep trailing prose out of wrapUp."""
    subsection, n_items, prose = case
    assert classify_rangaistussaannos(subsection) in {"no", "unknown"}
    result = _hoist_trailing_wrapup_ir(subsection)

    assert result.kind == IRNodeKind.SUBSECTION
    assert check_invariants(result) == []

    paragraph_kinds = [child.kind for child in result.children if child.kind == IRNodeKind.PARAGRAPH]
    assert len(paragraph_kinds) == n_items + 1
    assert result.children[-1].kind == IRNodeKind.PARAGRAPH
    assert irnode_to_text(result.children[-1]).strip() == prose


@given(subsection_with_sparse_tail_items_case())
@settings(max_examples=100, deadline=None)
def test_sparse_subsection_merge_preserves_untouched_tail_items(
    case: tuple[IRNode, IRNode, int],
) -> None:
    """Sparse subsection merges must keep untouched tail items exactly intact."""
    master, amend, prefix_len = case

    result = finland_merge._merge_subsection_with_omission_ir(master, amend)

    assert result is not None
    assert check_invariants(result) == []

    master_items = [c for c in master.children if c.kind == IRNodeKind.PARAGRAPH]
    result_items = [c for c in result.children if c.kind == IRNodeKind.PARAGRAPH]

    assert [c.label for c in result_items] == [c.label for c in master_items]

    untouched_master = master_items[prefix_len:]
    untouched_result = result_items[prefix_len:]
    assert len(untouched_result) == len(untouched_master)
    for got, want in zip(untouched_result, untouched_master):
        assert _node_signature(got) == _node_signature(want)


@given(subsection_with_sparse_gap_case())
@settings(max_examples=100, deadline=None)
def test_sparse_subsection_merge_preserves_undeclared_item_gap(
    case: tuple[IRNode, IRNode, int],
) -> None:
    """Omission may not silently delete untouched item units in the middle."""
    master, amend, tail_label = case

    result = finland_merge._merge_subsection_with_omission_ir(master, amend)

    assert result is not None
    assert check_invariants(result) == []

    master_items = [c for c in master.children if c.kind == IRNodeKind.PARAGRAPH]
    result_items = [c for c in result.children if c.kind == IRNodeKind.PARAGRAPH]

    assert [c.label for c in result_items] == [c.label for c in master_items]

    untouched_master = [
        item
        for item in master_items
        if item.label is not None and int(item.label) not in {1, tail_label}
    ]
    untouched_result = [
        item
        for item in result_items
        if item.label is not None and int(item.label) not in {1, tail_label}
    ]
    assert len(untouched_result) == len(untouched_master)
    for got, want in zip(untouched_result, untouched_master):
        assert _node_signature(got) == _node_signature(want)

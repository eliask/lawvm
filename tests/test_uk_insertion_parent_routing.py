"""Tests for UK structural insert parent routing (AGENTS.md §1.1).

When a source insertion anchor resolves to a descendant of an existing sibling
provision, the replay must not place the new provision inside that sibling.
For example, inserting subsection 6B anchored after subsection 6 paragraph (a)
must produce a sibling of subsection 6, not a child of subsection 6.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, Optional

from lawvm.core.ir import IRNode, IRNodeKind, LegalAddress
from lawvm.uk_legislation.canonicalize import uk_resolve_insertion_parent
from lawvm.uk_legislation.ordering import _label_sort_key


def _node(
    kind: str,
    label: str,
    *,
    eid: str = "",
    children: list[IRNode] | tuple[IRNode, ...] | None = None,
) -> IRNode:
    attrs: dict[str, Any] = {}
    if eid:
        attrs["eId"] = eid
    child_tuple = tuple(children) if children else ()
    return IRNode(
        kind=IRNodeKind(kind),
        label=label,
        text="",
        attrs=attrs,
        children=child_tuple,
    )


def _make_body() -> IRNode:
    """Return a minimal body containing section 350 / subsection 6 / paragraph a."""
    return _node(
        "body",
        "",
        children=[
            _node(
                "section",
                "350",
                eid="section-350",
                children=[
                    _node("heading", "", children=[]),
                    _node(
                        "subsection",
                        "6",
                        eid="section-350-subsection-6",
                        children=[
                            _node(
                                "paragraph",
                                "a",
                                eid="section-350-subsection-6-paragraph-a",
                            ),
                            _node(
                                "paragraph",
                                "b",
                                eid="section-350-subsection-6-paragraph-b",
                            ),
                        ],
                    ),
                    _node(
                        "subsection",
                        "7",
                        eid="section-350-subsection-7",
                        children=(),
                    ),
                ],
            )
        ],
    )


def _find_node_and_parent_statute(
    root: IRNode,
) -> Callable[..., tuple[IRNode | None, IRNode | None, int | None]]:
    """Return a find_node_and_parent_statute callable that matches exact eIds."""

    def _search(eid: str) -> tuple[Optional[IRNode], Optional[IRNode], Optional[int]]:
        def walk(node: IRNode, parent: IRNode | None, idx: int | None) -> tuple[IRNode | None, IRNode | None, int | None]:
            node_eid = node.attrs.get("eId") or node.attrs.get("id")
            if node_eid and str(node_eid).lower() == eid.lower():
                return node, parent, idx
            for i, child in enumerate(node.children):
                found = walk(child, node, i)
                if found[0] is not None:
                    return found
            return None, None, None

        return walk(root, None, None)

    def finder(eid: str, *, allow_sequence_match: bool = True) -> tuple[IRNode | None, IRNode | None, int | None]:
        return _search(eid)

    return finder


def _find_node_by_target(root: IRNode) -> Callable[[LegalAddress], tuple[IRNode | None, IRNode | None, int | None]]:
    """Return a find_node_by_target callable that resolves by exact path match."""

    def finder(target: LegalAddress) -> tuple[IRNode | None, IRNode | None, int | None]:
        nodes: list[tuple[IRNode, IRNode | None, int | None]] = [(root, None, None)]
        for p_kind, p_label in target.path:
            next_nodes: list[tuple[IRNode, IRNode | None, int | None]] = []
            for node, parent, idx in nodes:
                if node is None:
                    continue
                for i, child in enumerate(node.children):
                    if child.kind.value == p_kind and child.label == p_label:
                        next_nodes.append((child, node, i))
            nodes = next_nodes
            if not nodes:
                break
        if len(nodes) == 1:
            return nodes[0]
        return None, None, None

    return finder


def test_insert_sibling_subsection_after_descendant_anchor_routes_to_section() -> None:
    """Inserting subsection 6B anchored after subsection-6 paragraph-a must land in section 350, not in subsection 6."""
    root = _make_body()
    target = LegalAddress(path=(("section", "350"), ("subsection", "6b")))
    parent, insert_idx = uk_resolve_insertion_parent(
        target=target,
        body_root=root,
        node_kind="subsection",
        node_label="6b",
        preceding_eid="section-350-subsection-6-paragraph-a",
        following_eid=None,
        find_node_by_target=_find_node_by_target(root),
        find_node_and_parent_statute=_find_node_and_parent_statute(root),
        label_sort_key=_label_sort_key,
    )
    assert parent is not None, "Parent must resolve"
    assert parent.kind.value == "section", f"Expected parent=section, got {parent.kind.value}"
    assert parent.label == "350", f"Expected section 350, got {parent.label}"
    assert insert_idx is None, "Sibling structural inserts should use sorted insert, not a routed index"


def _make_section_with_subparagraph() -> IRNode:
    """Body containing section 350 / subsection 6 / paragraph a / subparagraph i."""
    return _node(
        "body",
        "",
        children=[
            _node(
                "section",
                "350",
                eid="section-350",
                children=[
                    _node("heading", "", children=[]),
                    _node(
                        "subsection",
                        "6",
                        eid="section-350-subsection-6",
                        children=[
                            _node(
                                "paragraph",
                                "a",
                                eid="section-350-subsection-6-paragraph-a",
                                children=[
                                    _node(
                                        "subparagraph",
                                        "i",
                                        eid="section-350-subsection-6-paragraph-a-subparagraph-i",
                                    ),
                                ],
                            ),
                            _node(
                                "paragraph",
                                "b",
                                eid="section-350-subsection-6-paragraph-b",
                            ),
                        ],
                    ),
                    _node(
                        "subsection",
                        "7",
                        eid="section-350-subsection-7",
                        children=(),
                    ),
                ],
            )
        ],
    )


def test_insert_subparagraph_after_subparagraph_anchor_keeps_paragraph_parent() -> None:
    """Inserting subparagraph (ii) anchored after subparagraph (i) stays inside paragraph (a)."""
    root = _make_section_with_subparagraph()
    target = LegalAddress(path=(("section", "350"), ("subsection", "6"), ("paragraph", "a"), ("subparagraph", "ii")))
    parent, insert_idx = uk_resolve_insertion_parent(
        target=target,
        body_root=root,
        node_kind="subparagraph",
        node_label="ii",
        preceding_eid="section-350-subsection-6-paragraph-a-subparagraph-i",
        following_eid=None,
        find_node_by_target=_find_node_by_target(root),
        find_node_and_parent_statute=_find_node_and_parent_statute(root),
        label_sort_key=_label_sort_key,
    )
    assert parent is not None
    assert parent.kind.value == "paragraph", f"Expected parent=paragraph, got {parent.kind.value}"
    assert parent.label == "a", f"Expected paragraph a, got {parent.label}"
    assert insert_idx == 1, f"Expected routed index 1 after subparagraph i, got {insert_idx}"

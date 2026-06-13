"""Regression test for the IRNodeKind enum comparison in EE blame walking.

``_walk_provisions`` selected provision nodes with
``node.kind in ("section", "subsection", "item")``.  ``IRNodeKind`` is a plain
``Enum``, so that membership test was always False and the walker returned no
provisions at all — the EE blame report listed nothing.
"""
from __future__ import annotations

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.tools.ee_blame import _walk_provisions


def test_walk_provisions_collects_labelled_provisions() -> None:
    tree = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.SECTION,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.SUBSECTION, label="1"),
                    IRNode(
                        kind=IRNodeKind.SUBSECTION,
                        label="2",
                        children=(IRNode(kind=IRNodeKind.ITEM, label="1"),),
                    ),
                ),
            ),
            # Unlabelled structural node must NOT be collected.
            IRNode(kind=IRNodeKind.HEADING, text="otsikko"),
        ),
    )

    provisions = _walk_provisions(tree)
    collected_kinds = [node.kind for _, node in provisions]

    assert provisions, "walker returned nothing — enum comparison still dead"
    # Order is pre-order traversal: section, its two subsections, the item.
    assert collected_kinds == [
        IRNodeKind.SECTION,
        IRNodeKind.SUBSECTION,
        IRNodeKind.SUBSECTION,
        IRNodeKind.ITEM,
    ]
    # Heading (no label, non-provision kind) is excluded.
    assert all(node.kind != IRNodeKind.HEADING for _, node in provisions)

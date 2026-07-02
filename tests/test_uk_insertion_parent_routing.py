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


# --- Unscoped section INSERT container-depth hoist (#178, FI #145 analogue) ---------
#
# When a scope-less UK section INSERT (``6A``, ``41ZA``) lowers to a container
# that natively holds only parts/chapters, planting it there as a bare sibling
# of the parts produces the ``mixed_hierarchy_child`` invariant violation. The
# agreeing-neighbour rule hoists the new section into the enclosing part/chapter
# its live numeric neighbours *both* sit in, and declines (leaving the root
# fallback untouched) when the neighbours straddle a boundary or one is absent.


def _sec(label: str, *, eid: str = "") -> IRNode:
    return _node("section", label, eid=eid or f"section-{label}")


def _part(label: str, sections: list[IRNode], *, eid: str = "") -> IRNode:
    return _node("part", label, eid=eid or f"part-{label}", children=sections)


def _make_parted_schedule_body(
    part_i_sections: list[str],
    part_ii_sections: list[str],
) -> IRNode:
    """Body holding schedule 1 whose parts I/II each hold numbered sections."""
    return _node(
        "body",
        "",
        children=[
            _node(
                "schedule",
                "1",
                eid="schedule-1",
                children=[
                    _part("I", [_sec(s, eid=f"schedule-1-part-I-section-{s}") for s in part_i_sections]),
                    _part("II", [_sec(s, eid=f"schedule-1-part-II-section-{s}") for s in part_ii_sections]),
                ],
            )
        ],
    )


def _resolve(root: IRNode, target: LegalAddress, node_kind: str, node_label: str):
    return uk_resolve_insertion_parent(
        target=target,
        body_root=root,
        node_kind=node_kind,
        node_label=node_label,
        preceding_eid=None,
        following_eid=None,
        find_node_by_target=_find_node_by_target(root),
        find_node_and_parent_statute=_find_node_and_parent_statute(root),
        label_sort_key=_label_sort_key,
    )


def test_unscoped_schedule_section_insert_hoists_into_agreeing_part() -> None:
    """schedule:1/section:6A with parent = schedule root must nest into part I (bracketed by 6 and 7)."""
    root = _make_parted_schedule_body(["5", "6", "7"], ["10", "11"])
    target = LegalAddress(path=(("schedule", "1"), ("section", "6a")))
    parent, insert_idx = _resolve(root, target, "section", "6a")
    assert parent is not None
    assert parent.kind.value == "part", f"Expected hoist into a part, got {parent.kind.value}"
    assert parent.label == "I", f"Expected part I (neighbours 6/7), got {parent.label}"
    assert insert_idx is None, "Sorted-insert into the hoisted container, not a routed index"


def test_unscoped_schedule_section_insert_straddling_boundary_declines() -> None:
    """A section whose below/above neighbours sit in *different* parts is ambiguous → root fallback."""
    # 8A brackets part I's last (7) and part II's first (10) — straddles the boundary.
    root = _make_parted_schedule_body(["5", "6", "7"], ["10", "11"])
    target = LegalAddress(path=(("schedule", "1"), ("section", "8a")))
    parent, insert_idx = _resolve(root, target, "section", "8a")
    assert parent is not None
    assert parent.kind.value == "schedule", "Straddling neighbours must decline to the schedule root fallback"
    assert parent.label == "1"
    assert insert_idx is None


def test_unscoped_schedule_section_insert_leading_tail_declines() -> None:
    """A section below every live provision (no ``below`` neighbour) is a leading tail → decline."""
    root = _make_parted_schedule_body(["5", "6", "7"], ["10", "11"])
    target = LegalAddress(path=(("schedule", "1"), ("section", "1a")))
    parent, insert_idx = _resolve(root, target, "section", "1a")
    assert parent is not None
    assert parent.kind.value == "schedule", "A leading tail (no lower neighbour) must decline to the root fallback"


def test_unscoped_schedule_section_insert_trailing_tail_declines() -> None:
    """A section above every live provision (no ``above`` neighbour) is a trailing tail → decline."""
    root = _make_parted_schedule_body(["5", "6", "7"], ["10", "11"])
    target = LegalAddress(path=(("schedule", "1"), ("section", "12a")))
    parent, insert_idx = _resolve(root, target, "section", "12a")
    assert parent is not None
    assert parent.kind.value == "schedule", "A trailing tail (no higher neighbour) must decline to the root fallback"


def _make_parted_body(
    part_ii_sections: list[str],
    part_iii_sections: list[str],
) -> IRNode:
    """Top-level body whose parts II/III each hold numbered sections (no schedule)."""
    return _node(
        "body",
        "",
        children=[
            _part("II", [_sec(s, eid=f"part-II-section-{s}") for s in part_ii_sections]),
            _part("III", [_sec(s, eid=f"part-III-section-{s}") for s in part_iii_sections]),
        ],
    )


def test_unscoped_body_section_insert_hoists_into_agreeing_part() -> None:
    """A scope-less top-level section:4A (len-1 path) must nest into part II (bracketed by 4 and 5)."""
    root = _make_parted_body(["3", "4", "5"], ["8", "9"])
    target = LegalAddress(path=(("section", "4a"),))
    parent, insert_idx = _resolve(root, target, "section", "4a")
    assert parent is not None
    assert parent.kind.value == "part", f"Expected hoist into a part, got {parent.kind.value}"
    assert parent.label == "II", f"Expected part II (neighbours 4/5), got {parent.label}"
    assert insert_idx is None


def test_unscoped_body_section_insert_straddling_boundary_not_hoisted_to_wrong_part() -> None:
    """A body-level section straddling the part II/III boundary must never hoist into the upper part."""
    root = _make_parted_body(["3", "4", "5"], ["8", "9"])
    target = LegalAddress(path=(("section", "6a"),))
    parent, _ = _resolve(root, target, "section", "6a")
    # Neighbours are 5 (part II) and 8 (part III) — they straddle, so the
    # agreeing-neighbour hoist declines. The load-bearing guarantee is that the
    # section is NOT planted inside the wrong (upper) part III.
    if parent is not None and parent.kind.value == "part":
        assert parent.label != "III", "Must not hoist a straddling section into the wrong (upper) part"


def test_explicit_part_scoped_section_insert_not_regressed() -> None:
    """A section INSERT whose target names its part (part:II/section:4A) still lands in that part."""
    root = _make_parted_body(["3", "4", "5"], ["8", "9"])
    target = LegalAddress(path=(("part", "II"), ("section", "4a")))
    parent, insert_idx = _resolve(root, target, "section", "4a")
    assert parent is not None
    assert parent.kind.value == "part"
    assert parent.label == "II", "An explicitly part-scoped section INSERT must land in its named part"
    assert insert_idx is None

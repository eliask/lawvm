from __future__ import annotations

from typing import Any

from lawvm.semantic.model import (
    _FACET_KINDS,
    _WORDING_FACET_KIND,
    AlignedSemanticNode,
    SemanticStructureFacet,
    SemanticStructureNode,
    _facet_map_from_tuple,
    _node_wording_facet,
    semantic_structural_children,
)


def _aligned_semantic_facets_to_dict(
    left: SemanticStructureNode | None,
    right: SemanticStructureNode | None,
) -> dict[str, Any]:
    aligned: dict[str, Any] = {}
    for left_facet, right_facet, match_basis in align_semantic_facets(left, right):
        facet = left_facet if left_facet is not None else right_facet
        if facet is None:
            continue
        item: dict[str, Any] = {"match_basis": match_basis}
        if left_facet is not None:
            item["left"] = left_facet.to_dict()
        if right_facet is not None:
            item["right"] = right_facet.to_dict()
        aligned[facet.kind] = item
    left_wording = _node_wording_facet(left)
    right_wording = _node_wording_facet(right)
    if left_wording is not None or right_wording is not None:
        item = {
            "match_basis": (
                "exact_kind"
                if left_wording is not None and right_wording is not None
                else "left_only"
                if left_wording is not None
                else "right_only"
            )
        }
        if left_wording is not None:
            item["left"] = left_wording.to_dict()
        if right_wording is not None:
            item["right"] = right_wording.to_dict()
        aligned[_WORDING_FACET_KIND] = item
    return aligned


def align_semantic_facets(
    left: SemanticStructureNode | None,
    right: SemanticStructureNode | None,
) -> list[tuple[SemanticStructureFacet | None, SemanticStructureFacet | None, str]]:
    left_facets = _facet_map_from_tuple(left.facets if left is not None else ())
    right_facets = _facet_map_from_tuple(right.facets if right is not None else ())
    aligned: list[tuple[SemanticStructureFacet | None, SemanticStructureFacet | None, str]] = []
    for kind in _FACET_KINDS:
        left_facet = left_facets.get(kind)
        right_facet = right_facets.get(kind)
        if left_facet is not None and right_facet is not None:
            aligned.append((left_facet, right_facet, "exact_kind"))
        elif left_facet is not None:
            aligned.append((left_facet, None, "left_only"))
        elif right_facet is not None:
            aligned.append((None, right_facet, "right_only"))
    return aligned


def _structural_children(
    node: SemanticStructureNode | None,
) -> tuple[SemanticStructureNode, ...]:
    if node is None:
        return ()
    return node.children


def align_semantic_children(
    left_children: tuple[SemanticStructureNode, ...] | list[SemanticStructureNode],
    right_children: tuple[SemanticStructureNode, ...] | list[SemanticStructureNode],
) -> list[tuple[SemanticStructureNode | None, SemanticStructureNode | None, str]]:
    aligned: list[tuple[SemanticStructureNode | None, SemanticStructureNode | None, str]] = []
    left_children = semantic_structural_children(left_children)
    right_children = semantic_structural_children(right_children)
    right_map: dict[str, list[SemanticStructureNode]] = {}
    for idx, child in enumerate(right_children):
        right_map.setdefault(child.key(idx), []).append(child)
    unmatched_left: list[SemanticStructureNode] = []
    for idx, child in enumerate(left_children):
        key = child.key(idx)
        bucket = right_map.get(key)
        if bucket:
            match = bucket.pop(0)
            if child.label:
                basis = (
                    "ordinal_fallback"
                    if "ordinal_fallback" in {child.label_basis, match.label_basis}
                    else "exact_label"
                )
            else:
                basis = "exact_kind"
            aligned.append((child, match, basis))
            if not bucket:
                right_map.pop(key, None)
        else:
            unmatched_left.append(child)

    unmatched_right: list[SemanticStructureNode] = []
    for bucket in right_map.values():
        unmatched_right.extend(bucket)

    ordinal_kinds = {"subsection", "item", "subitem"}
    left_by_kind: dict[str, list[SemanticStructureNode]] = {}
    right_by_kind: dict[str, list[SemanticStructureNode]] = {}
    for child in unmatched_left:
        left_by_kind.setdefault(child.kind, []).append(child)
    for child in unmatched_right:
        right_by_kind.setdefault(child.kind, []).append(child)

    matched_left_ids: set[int] = set()
    matched_right_ids: set[int] = set()
    for kind in ordinal_kinds:
        left_kind = left_by_kind.get(kind, [])
        right_kind = right_by_kind.get(kind, [])
        if not left_kind or not right_kind:
            continue
        pair_count = min(len(left_kind), len(right_kind))
        for idx in range(pair_count):
            left_child = left_kind[idx]
            right_child = right_kind[idx]
            if not left_child.label or not right_child.label:
                continue
            if left_child.label_basis != "ordinal_fallback" or right_child.label_basis != "ordinal_fallback":
                continue
            aligned.append((left_child, right_child, "ordinal_fallback"))
            matched_left_ids.add(id(left_child))
            matched_right_ids.add(id(right_child))

    for child in unmatched_left:
        if id(child) not in matched_left_ids:
            aligned.append((child, None, "left_only"))
    for child in unmatched_right:
        if id(child) not in matched_right_ids:
            aligned.append((None, child, "right_only"))
    return aligned


def align_semantic_trees(
    left: SemanticStructureNode | None,
    right: SemanticStructureNode | None,
    *,
    match_basis: str | None = None,
) -> AlignedSemanticNode | None:
    if left is None and right is None:
        return None
    if match_basis is not None:
        basis = match_basis
    elif left is None:
        basis = "right_only"
    elif right is None:
        basis = "left_only"
    elif left.kind == right.kind and left.label == right.label:
        if left.label:
            if "ordinal_fallback" in {left.label_basis, right.label_basis}:
                basis = "ordinal_fallback"
            else:
                basis = "exact_label"
        else:
            basis = "exact_kind"
    else:
        basis = "ambiguous"
    left_children = _structural_children(left)
    right_children = _structural_children(right)
    aligned_structural_children = align_semantic_children(left_children, right_children)
    children = tuple(
        child
        for child in (
            align_semantic_trees(left_child, right_child, match_basis=child_basis)
            for left_child, right_child, child_basis in aligned_structural_children
        )
        if child is not None
    )
    return AlignedSemanticNode(left=left, right=right, match_basis=basis, children=children)

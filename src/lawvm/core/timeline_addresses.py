"""Address/tree helper utilities for core timeline operations.

This module owns the low-level address traversal, label sorting, and migrated
root retargeting helpers used by timeline compilation and PIT materialization.
It is extracted from ``timeline.py`` as a low-risk first split step; the
legacy module re-exports these helpers for compatibility.
"""

from __future__ import annotations

import re as _re
from typing import Callable, Iterator, Optional, Tuple

from lawvm.core.ir import IRNode, IRStatute, LegalAddress, ProvisionVersion
from lawvm.core.ir_helpers import _kind_str, irnode_content_hash
from lawvm.core.semantic_types import IRNodeKind
from lawvm.roman import roman_to_arabic


def _sort_label_key(label: Optional[str]) -> Tuple[Tuple[int, int, str], ...]:
    """Sort key that orders labels in legal-family order."""
    if not label:
        return ((1, 0, ""),)
    parts = label.split("_")
    result = []
    for p in parts:
        try:
            result.append((0, int(p), ""))
        except ValueError:
            m = _re.match(r"^(\d+)([a-z]+)$", p, _re.IGNORECASE)
            if m:
                result.append((0, int(m.group(1)), m.group(2).lower()))
            else:
                roman_val = roman_to_arabic(p)
                if roman_val is not None:
                    result.append((0, roman_val, ""))
                else:
                    result.append((1, 0, p))
    return tuple(result)


def _iter_nodes_with_address(
    node: IRNode,
    current_path: Tuple[Tuple[str, str], ...] = (),
) -> Iterator[Tuple[LegalAddress, IRNode]]:
    """Recursively yield (address, node) for each addressable node in the tree."""
    if node.kind == IRNodeKind.BODY:
        for child in node.children:
            yield from _iter_nodes_with_address(child, current_path)
        return

    if node.label is not None:
        addr_path = current_path + ((_kind_str(node.kind), node.label),)
        address = LegalAddress(path=addr_path)
        yield address, node
        for child in node.children:
            yield from _iter_nodes_with_address(child, addr_path)
    else:
        for child in node.children:
            yield from _iter_nodes_with_address(child, current_path)


def _iter_statute_nodes_with_address(statute: IRStatute) -> Iterator[Tuple[LegalAddress, IRNode]]:
    """Yield addressable nodes from both statute body and top-level schedules."""
    yield from _iter_nodes_with_address(statute.body)
    for supplement in statute.supplements:
        yield from _iter_nodes_with_address(supplement)


def _canonical_root_num_text(kind: IRNodeKind, label: str) -> str | None:
    """Return a neutral canonical num-child label for migrated root nodes."""
    kind_value = str(kind)
    if kind_value == IRNodeKind.SECTION.value:
        return f"{label} section"
    if kind_value == IRNodeKind.CHAPTER.value:
        return f"{label} chapter"
    if kind_value == IRNodeKind.PART.value:
        return f"{label} part"
    if kind_value == IRNodeKind.ITEM.value:
        return f"{label} item"
    return None


def _retarget_root_node(
    node: IRNode,
    address: LegalAddress,
    *,
    root_num_text_fn: Callable[[IRNodeKind, str], str | None] | None = None,
) -> IRNode:
    """Copy a migrated node so its root kind/label matches the destination address."""
    if not address.path:
        return node
    kind, label = address.path[-1]
    kind_enum = IRNodeKind(kind)
    num_text_fn = root_num_text_fn or _canonical_root_num_text
    children = tuple(node.children)
    if children and children[0].kind == IRNodeKind.NUM:
        replacement_num_text = num_text_fn(kind_enum, label)
        existing_num_text = children[0].text or ""
        if root_num_text_fn is None and node.label and existing_num_text.startswith(node.label):
            replacement_num_text = f"{label}{existing_num_text[len(node.label):]}"
        if replacement_num_text is not None:
            children = (
                IRNode(
                    kind=children[0].kind,
                    label=children[0].label,
                    text=replacement_num_text,
                    attrs=dict(children[0].attrs),
                    children=tuple(children[0].children),
                ),
                *children[1:],
            )
    return IRNode(
        kind=kind_enum,
        label=label,
        text=node.text,
        attrs=dict(node.attrs),
        children=children,
    )


def _retarget_version_content(
    version: ProvisionVersion,
    address: LegalAddress,
    *,
    root_num_text_fn: Callable[[IRNodeKind, str], str | None] | None = None,
) -> ProvisionVersion:
    """Copy a selected version so root content matches a migrated address."""
    content = version.content
    if content is not None:
        content = _retarget_root_node(
            content,
            address,
            root_num_text_fn=root_num_text_fn,
        )
    return ProvisionVersion(
        effective=version.effective,
        enacted=version.enacted,
        expires=version.expires,
        variant_kind=version.variant_kind,
        content=content,
        source=version.source,
        applicability=list(version.applicability),
        content_hash=irnode_content_hash(content),
    )


def _address_prefix_matches(address: LegalAddress, prefix: LegalAddress) -> bool:
    """Return True when ``prefix`` matches the leading path of ``address``."""
    if len(prefix.path) > len(address.path):
        return False
    if address.path[: len(prefix.path)] != prefix.path:
        return False
    if prefix.special:
        return prefix.special == address.special
    return True

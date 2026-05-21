"""Payload conversion helpers for the UK replay frontend."""

from __future__ import annotations

from typing import Any

from lawvm.core.ir import IRNode
from lawvm.uk_legislation.mutable_ir import UKMutableNode


def _to_mutable_node(node: Any) -> UKMutableNode:
    """Convert core payloads or dict-shaped payloads into a UK mutable node."""
    if isinstance(node, UKMutableNode):
        return node
    if isinstance(node, IRNode):
        return UKMutableNode.from_irnode(node)
    if isinstance(node, dict):
        return UKMutableNode.from_dict(node)
    raise TypeError(f"Unsupported payload type for UK mutable conversion: {type(node)!r}")


def _to_irnode(node: Any) -> IRNode:
    """Convert UK-local mutable payloads back into frozen core IR nodes."""
    if isinstance(node, IRNode):
        return node
    if isinstance(node, UKMutableNode):
        return node.to_irnode()
    if isinstance(node, dict):
        return UKMutableNode.from_dict(node).to_irnode()
    raise TypeError(f"Unsupported payload type for frozen IR conversion: {type(node)!r}")

"""Payload conversion helpers for the UK replay frontend.

Historically this module bridged dict-shaped payloads and the frozen core
``IRNode`` to the UK-local ``UKMutableNode`` workspace (mutable copy-on-write
shadow). The frozen ``IRNode`` migration (mutable_ir Wave N3d) retired that
shadow: these helpers now return frozen ``IRNode`` directly (an identity for
``IRNode`` inputs, a recursive dict→``IRNode`` builder for the legacy
dict-shaped source-payload case). The function names are preserved so existing
call sites continue to read naturally; only the implementation moved to the
frozen boundary.
"""

from __future__ import annotations

from typing import Any

from lawvm.core.ir import IRNode
from lawvm.uk_legislation.apply_rebuild import uk_ir_node_kind


def _to_mutable_node(node: Any) -> IRNode:
    """Convert a payload into a frozen ``IRNode`` workspace.

    Pre-Wave-N3d this returned a ``UKMutableNode``; it now returns the frozen
    ``IRNode`` directly. Accepts either an ``IRNode`` (identity) or a dict
    payload shaped as ``{"kind", "label", "text", "attrs", "children"}``.
    """
    if isinstance(node, IRNode):
        return node
    if isinstance(node, dict):
        return _irnode_from_dict(node)
    raise TypeError(
        f"Unsupported payload type for IRNode conversion: {type(node)!r}"
    )


def _to_irnode(node: Any) -> IRNode:
    """Return a frozen ``IRNode`` for the given payload (identity for IRNode)."""
    if isinstance(node, IRNode):
        return node
    if isinstance(node, dict):
        return _irnode_from_dict(node)
    raise TypeError(
        f"Unsupported payload type for frozen IR conversion: {type(node)!r}"
    )


def _irnode_from_dict(data: dict[str, Any]) -> IRNode:
    """Build a frozen ``IRNode`` tree from a dict payload (recursive)."""
    return IRNode(
        kind=uk_ir_node_kind(data.get("kind", "")),
        label=data.get("label"),
        text=data.get("text", ""),
        attrs=dict(data.get("attrs", {}) or {}),
        children=tuple(
            _irnode_from_dict(child) for child in data.get("children", []) or []
        ),
    )

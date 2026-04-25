"""Small helper functions for the core IR carriers."""

from __future__ import annotations

import functools
import hashlib
from typing import TYPE_CHECKING, Any, Optional

import icontract

from lawvm.core.semantic_types import IRNodeKind

if TYPE_CHECKING:
    from lawvm.core.ir import IRNode, IRStatute


def kind_for_tag(tag: str) -> IRNodeKind | None:
    """Return the node-kind enum for a known XML tag."""
    try:
        return IRNodeKind(tag)
    except ValueError:
        return None


@functools.lru_cache(maxsize=256)
def kind_str(kind: IRNodeKind | str) -> str:
    """Return a stable string form for an IR node kind."""
    return kind.value if isinstance(kind, IRNodeKind) else str(kind)


def is_zombie(node: "IRNode", pit_date: Optional[str] = None) -> bool:
    """Return True when an IRNode is effectively repealed or inactive."""
    import re

    if node.attrs.get("Status") in ("Repealed", "Prospective"):
        return True

    start = node.attrs.get("RestrictStartDate")
    end = node.attrs.get("RestrictEndDate")
    if pit_date:
        if start and start > pit_date:
            return True
        if end and end <= pit_date:
            return True

    if node.text and re.match(r"^[.\s]+$", node.text):
        if not any(not is_zombie(child, pit_date) for child in node.children):
            return True

    return False


def irnode_to_text(node: "IRNode") -> str:
    """Recursively collect all text content from an IRNode tree."""
    parts: list[str] = []
    if node.text:
        parts.append(node.text)
    parts.extend(irnode_to_text(child) for child in node.children)
    return " ".join(p for p in parts if p)


@icontract.ensure(
    lambda node, result: (node is None and result == "") or (node is not None and len(result) == 64),
    "hash is empty for tombstone, 64 hex chars for live content",
)
def irnode_content_hash(node: Optional["IRNode"]) -> str:
    """Return SHA-256 hex digest of irnode_to_text(node), or '' for None."""
    if node is None:
        return ""
    text = irnode_to_text(node)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def irnode_from_dict(data: dict[str, Any]) -> "IRNode":
    """Deserialize a bare IRNode payload into an IRNode."""
    from lawvm.core.ir import FrozenDict, IRNode  # noqa: PLC0415

    if any(key in data for key in ("schema", "producer", "version", "payload", "status")):
        raise ValueError("IRNode.from_dict expects a bare node payload; unpack the artifact envelope first")
    kind_raw = data.get("kind", "")
    if isinstance(kind_raw, IRNodeKind):
        kind = kind_raw
    elif isinstance(kind_raw, str):
        kind = IRNodeKind(kind_raw)
    else:
        raise TypeError(f"IRNode.kind must be a string or IRNodeKind, got {type(kind_raw).__name__}")
    return IRNode(
        kind=kind,
        label=data.get("label"),
        text=data.get("text", ""),
        attrs=FrozenDict(dict(data.get("attrs", {}))),
        children=tuple(irnode_from_dict(child) for child in data.get("children", [])),
    )


def ir_statute_from_dict(data: dict[str, Any]) -> "IRStatute":
    """Deserialize a bare IRStatute payload into an IRStatute."""
    from lawvm.core.ir import IRStatute  # noqa: PLC0415

    if any(key in data for key in ("schema", "producer", "version", "payload", "status")):
        raise ValueError("IRStatute.from_dict expects a bare statute payload; unpack the artifact envelope first")
    if "schedules" in data:
        raise ValueError("IRStatute.from_dict rejects schedules payload; use supplements")
    serialized_supplements = data.get("supplements", [])
    return IRStatute(
        statute_id=data.get("statute_id", "0/0"),
        title=data.get("title", "Unknown"),
        body=irnode_from_dict(data.get("body", {})),
        supplements=[irnode_from_dict(s) for s in serialized_supplements],
        metadata=data.get("metadata", {}),
    )


_kind_str = kind_str

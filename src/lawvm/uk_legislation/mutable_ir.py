"""UK-local mutable replay workspace.

This module exists only as a jurisdiction-local adaptation layer after the core
IR became frozen. It must not become a new shared contract or leak across the
kernel boundary. The authoritative runtime IR remains ``lawvm.core.ir``; UK
code may mutate these local wrappers internally, then must convert back to
frozen ``IRNode``/``IRStatute`` at the boundary.

TODO(arch): replace this mutable mirror with explicit rebuild/copy-on-write
helpers once the UK replay executor is fully migrated off in-place tree edits.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import warnings
from typing import Any, Optional, cast

from lawvm.core.ir import IRNode, IRStatute
from lawvm.core.semantic_types import IRNodeKind


def uk_ir_node_kind(kind: Any) -> IRNodeKind:
    """Coerce UK-local source/address kind aliases to core IR node kinds."""
    if isinstance(kind, IRNodeKind):
        return kind
    if isinstance(kind, str):
        if kind == "point":
            return IRNodeKind.ITEM
        if kind == "article":
            return IRNodeKind.SECTION
        return IRNodeKind(kind)
    raise TypeError(f"UKMutableNode.kind must be a string or IRNodeKind, got {type(kind).__name__}")


@dataclass
class UKMutableNode:
    kind: IRNodeKind
    label: Optional[str] = None
    text: str = ""
    attrs: dict[str, Any] = field(default_factory=dict)
    children: list["UKMutableNode"] = field(default_factory=list)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

    def __post_init__(self) -> None:
        self.kind = uk_ir_node_kind(self.kind)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value if isinstance(self.kind, IRNodeKind) else str(self.kind),
            "label": self.label,
            "text": self.text,
            "attrs": dict(self.attrs),
            "children": [child.to_dict() for child in self.children],
        }

    def to_irnode(self) -> IRNode:
        return IRNode(
            kind=self.kind,
            label=self.label,
            text=self.text,
            attrs=dict(self.attrs),
            children=tuple(child.to_irnode() for child in self.children),
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UKMutableNode":
        return cls(
            kind=uk_ir_node_kind(data.get("kind", "")),
            label=data.get("label"),
            text=data.get("text", ""),
            attrs=dict(data.get("attrs", {}) or {}),
            children=[cls.from_dict(child) for child in data.get("children", []) or []],
        )

    @classmethod
    def from_irnode(cls, node: IRNode) -> "UKMutableNode":
        return cls(
            kind=node.kind,
            label=node.label,
            text=node.text,
            attrs=dict(node.attrs),
            children=[cls.from_irnode(child) for child in node.children],
        )


@dataclass
class UKMutableStatute:
    statute_id: str
    title: str
    body: UKMutableNode
    supplements: list[UKMutableNode] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def schedules(self) -> list[UKMutableNode]:
        warnings.warn(
            "UKMutableStatute.schedules is a transitional compatibility alias; use supplements instead.",
            stacklevel=2,
        )
        return self.supplements

    def to_irstatute(self) -> IRStatute:
        return IRStatute(
            statute_id=self.statute_id,
            title=self.title,
            body=self.body.to_irnode(),
            supplements=tuple(supplement.to_irnode() for supplement in self.supplements),
            metadata=dict(self.metadata),
        )

    @classmethod
    def from_irstatute(cls, statute: IRStatute) -> "UKMutableStatute":
        return cls(
            statute_id=statute.statute_id,
            title=statute.title,
            body=UKMutableNode.from_irnode(statute.body),
            supplements=[UKMutableNode.from_irnode(supplement) for supplement in statute.supplements],
            metadata=dict(statute.metadata),
        )


def uk_replace_children(node: UKMutableNode, new_children: list[UKMutableNode]) -> bool:
    node.children = list(new_children)
    return True


def uk_insert_child_sorted(parent: UKMutableNode, new_node: UKMutableNode) -> bool:
    from lawvm.uk_legislation.canonicalize import uk_insert_into_children
    from lawvm.uk_legislation.ordering import _label_sort_key
    from lawvm.uk_legislation.uk_grafter import _clean_num

    if new_node.label:
        insert_kind = uk_ir_node_kind(new_node.kind)
        insert_label = _clean_num(new_node.label or "")
        if any(
            uk_ir_node_kind(child.kind) == insert_kind
            and _clean_num(child.label or "") == insert_label
            for child in parent.children
        ):
            return False

    uk_insert_into_children(
        cast(list[IRNode], parent.children),
        cast(IRNode, new_node),
        label_sort_key=_label_sort_key,
    )
    return True

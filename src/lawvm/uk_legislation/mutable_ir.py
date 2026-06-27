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
        self.children = list(self.children)

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


def uk_replace_children_cow(
    node: UKMutableNode,
    new_children: list[UKMutableNode],
) -> UKMutableNode:
    """PR3 (audit XJUR-02 / AGENTS.md §2.3): copy-on-write variant of
    ``uk_replace_children``. Returns a NEW ``UKMutableNode`` with the supplied
    children, sharing every other field, instead of mutating in place.

    The mutation_boundary / replay invariants depend on every node upstream of
    a modified subtree being rebuilt rather than mutated in place. The in-place
    ``uk_replace_children`` is preserved for legacy callers and tests; new replay
    state mutation sites route through this CoW variant.
    """
    from dataclasses import replace as _dc_replace

    return _dc_replace(node, children=list(new_children))


def uk_insert_node_at_index_cow(
    children: list[UKMutableNode],
    index: int,
    new_node: UKMutableNode,
) -> tuple[list[UKMutableNode], bool]:
    """PR3 (audit XJUR-02 / AGENTS.md §2.3): copy-on-write variant of
    ``uk_insert_node_at_index``. Returns a NEW list with ``new_node`` inserted
    at ``index`` instead of mutating the input. The original list is left
    untouched.

    Returns ``(new_children, success)``. ``success`` is False when a duplicate
    (kind, label) sibling is already present (matching the in-place variant).
    """
    if uk_has_same_kind_label_child(children, new_node):
        return list(children), False
    new_children = list(children)
    new_children.insert(index, new_node)
    return new_children, True


def uk_insert_node_sorted_cow(
    children: list[UKMutableNode],
    new_node: UKMutableNode,
) -> tuple[list[UKMutableNode], int | None]:
    """PR3 (audit XJUR-02 / AGENTS.md §2.3): copy-on-write variant of
    ``uk_insert_node_sorted``. Returns ``(new_children, inserted_idx_or_None)``
    instead of mutating the input list in place.

    ``inserted_idx`` is the index of ``new_node`` in the returned list (matching
    existing siblings if any), or ``None`` when insertion was rejected by the
    duplicate-(kind, label) guard. The returned list is always a fresh copy
    regardless of success so callers may safely assign it back to a CoW-rebuilt
    parent without aliasing the original list.
    """
    from lawvm.uk_legislation.canonicalize import uk_insert_into_children
    from lawvm.uk_legislation.ordering import _label_sort_key
    from lawvm.core.ir import IRNode
    from typing import cast

    if uk_has_same_kind_label_child(children, new_node):
        return list(children), None
    new_children = list(children)
    # ``uk_insert_into_children`` accepts list[IRNode] and mutates it; UKMutableNode
    # is structurally compatible (same .kind/.label/.attrs/.children shape the
    # ordering logic consults). We pass a mutable copy and return it.
    uk_insert_into_children(
        cast(list[IRNode], new_children),
        cast(IRNode, new_node),
        label_sort_key=_label_sort_key,
    )
    try:
        inserted_idx = new_children.index(new_node)
    except ValueError:
        inserted_idx = None
    return new_children, inserted_idx


def uk_insert_child_sorted_cow(
    parent: UKMutableNode,
    new_node: UKMutableNode,
) -> tuple[UKMutableNode, int | None]:
    """PR3 (audit XJUR-02 / AGENTS.md §2.3): copy-on-write variant of
    ``uk_insert_child_sorted``. Returns ``(new_parent, inserted_idx_or_None)``
    instead of mutating ``parent.children`` in place.

    ``inserted_idx`` is ``None`` when the duplicate-(kind, label) guard rejected
    the insertion (matching the in-place variant's ``False`` return); callers
    that intend to thread the new parent up the replay tree should treat that
    case as a no-op rather than building a shell parent.
    """
    new_children, inserted_idx = uk_insert_node_sorted_cow(parent.children, new_node)
    new_parent = uk_replace_children_cow(parent, new_children)
    return new_parent, inserted_idx


def uk_has_same_kind_label_child(children: list[UKMutableNode], new_node: UKMutableNode) -> bool:
    from lawvm.uk_legislation.uk_grafter import _clean_num

    if new_node.label:
        insert_kind = uk_ir_node_kind(new_node.kind)
        insert_label = _clean_num(new_node.label or "")
        return any(
            uk_ir_node_kind(child.kind) == insert_kind
            and _clean_num(child.label or "") == insert_label
            for child in children
        )
    return False


def uk_insert_node_at_index(children: list[UKMutableNode], index: int, new_node: UKMutableNode) -> bool:
    if uk_has_same_kind_label_child(children, new_node):
        return False
    children.insert(index, new_node)
    return True


def uk_insert_node_sorted(children: list[UKMutableNode], new_node: UKMutableNode) -> bool:
    from lawvm.uk_legislation.canonicalize import uk_insert_into_children
    from lawvm.uk_legislation.ordering import _label_sort_key

    if uk_has_same_kind_label_child(children, new_node):
        return False

    uk_insert_into_children(
        cast(list[IRNode], children),
        cast(IRNode, new_node),
        label_sort_key=_label_sort_key,
    )
    return True


def uk_insert_child_sorted(parent: UKMutableNode, new_node: UKMutableNode) -> bool:
    return uk_insert_node_sorted(parent.children, new_node)

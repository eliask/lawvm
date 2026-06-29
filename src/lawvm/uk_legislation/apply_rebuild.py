"""UK-local copy-on-write rebuild helpers for replay mutation sites.

This module exists only as a jurisdiction-local adaptation layer after PR3 of
the mutable_ir ratchet (audit XJUR-02 / AGENTS.md §2.3) introduced CoW variants
of the in-place ``uk_*`` mutation helpers originally defined in
``mutable_ir.py``. PR4 of the same ratchet relocated these CoW helpers out of
``mutable_ir.py`` so that the ``mutable_ir`` shadow module's eventual deletion
does not need to touch any of the apply sites that already route through CoW.

Sub-PR C+D (mutable_ir Wave N3d): these helpers now operate on the frozen
``IRNode`` tree directly (the ``UKMutableStatute`` mirror was retired at the
``replay_executor`` boundary). They MUST NOT mutate inputs in place; the
helpers themselves only build fresh nodes / fresh lists via
``dataclasses.replace``.

Sub-PR F (mutable_ir Wave N3d, final): the ``uk_ir_node_kind`` (UK-local
source/address kind alias coercion) and ``uk_has_same_kind_label_child``
helpers were relocated here from ``mutable_ir.py`` just before that shadow
module's deletion, since CoW helpers in this module and the
``uk_grafter``/``replay_{renumber,target_diagnostics}_apply`` modules must
continue to coerce UK source kind strings (e.g. ``"point"``→``ITEM``,
``"article"``→``SECTION``) to core ``IRNodeKind`` at construction sites.
"""

from __future__ import annotations

from dataclasses import replace as dc_replace
from typing import Any

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.tree_ops import with_children


def uk_ir_node_kind(kind: Any) -> IRNodeKind:
    """Coerce UK-local source/address kind aliases to core IR node kinds.

    UK source XML / address surfaces carry legacy string aliases that pre-date
    the frozen core ``IRNodeKind`` (``"point"``→``ITEM``, ``"article"``→
    ``SECTION``). This helper is a single coercion point kept at the CoW/kind
    boundary so callers that pass ``str`` kinds (parsed from source XML) reuse
    the same coercion as the frozen ``IRNode`` constructor; for inputs that are
    already ``IRNodeKind`` it is a typed identity. Relocated here from
    ``mutable_ir.py`` (Sub-PR F) so that ``mutable_ir`` can be deleted while
    the alias-coercion contract stays in a single, audited place.
    """
    if isinstance(kind, IRNodeKind):
        return kind
    if isinstance(kind, str):
        if kind == "point":
            return IRNodeKind.ITEM
        if kind == "article":
            return IRNodeKind.SECTION
        return IRNodeKind(kind)
    raise TypeError(
        f"uk_ir_node_kind must be a string or IRNodeKind, got {type(kind).__name__}"
    )


def uk_has_same_kind_label_child(
    children: list[IRNode],
    new_node: IRNode,
) -> bool:
    """Return True if ``children`` already contains a (kind, label) sibling.

    The duplicate-(kind,label) guard drives every CoW insert helper
    (``uk_insert_node_at_index_cow`` / ``uk_insert_node_sorted_cow``) and the
    in-place variant historically living in ``mutable_ir.py``. Relocated here
    (Sub-PR F) to keep the guard next to its only CoW consumers.
    """
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


def uk_with_attr_set(node: IRNode, key: str, value: Any) -> IRNode:
    """Sub-PR C+D CoW helper: return a NEW ``IRNode`` with ``node.attrs[key]``
    set to ``value``. ``IRNode.attrs`` is a ``FrozenDict`` so the in-place
    ``node.attrs[k] = v`` mutation pattern (valid on the former UKMutableNode)
    is replaced by this CoW rebuild helper at every apply-site."""
    return dc_replace(node, attrs={**dict(node.attrs), key: value})


def uk_with_attr_pop(node: IRNode, *keys: str) -> IRNode:
    """Sub-PR C+D CoW helper: return a NEW ``IRNode`` with ``node.attrs`` minus
    every key in ``*keys``. Replaces ``node.attrs.pop(key, None)`` mutation."""
    pop_set = set(keys)
    new_attrs = {k: v for k, v in node.attrs.items() if k not in pop_set}
    return dc_replace(node, attrs=new_attrs)


def uk_insert_node_at_index_cow(
    children: list[IRNode],
    index: int,
    new_node: IRNode,
) -> tuple[list[IRNode], bool]:
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
    children: list[IRNode],
    new_node: IRNode,
) -> tuple[list[IRNode], int | None]:
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

    if uk_has_same_kind_label_child(children, new_node):
        return list(children), None
    new_children = list(children)
    # ``uk_insert_into_children`` accepts list[IRNode] and mutates it; we pass a
    # mutable copy and return it (the caller never aliases the input list).
    uk_insert_into_children(
        new_children,
        new_node,
        label_sort_key=_label_sort_key,
    )
    try:
        inserted_idx = new_children.index(new_node)
    except ValueError:
        inserted_idx = None
    return new_children, inserted_idx


def uk_insert_child_sorted_cow(
    parent: IRNode,
    new_node: IRNode,
) -> tuple[IRNode, int | None]:
    """PR3 (audit XJUR-02 / AGENTS.md §2.3): copy-on-write variant of
    ``uk_insert_child_sorted``. Returns ``(new_parent, inserted_idx_or_None)``
    instead of mutating ``parent.children`` in place.

    ``inserted_idx`` is ``None`` when the duplicate-(kind, label) guard rejected
    the insertion (matching the in-place variant's ``False`` return); callers
    that intend to thread the new parent up the replay tree should treat that
    case as a no-op rather than building a shell parent.
    """
    new_children, inserted_idx = uk_insert_node_sorted_cow(list(parent.children), new_node)
    new_parent = with_children(parent, new_children)
    return new_parent, inserted_idx

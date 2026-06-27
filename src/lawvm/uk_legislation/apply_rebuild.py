"""UK-local copy-on-write rebuild helpers for replay mutation sites.

This module exists only as a jurisdiction-local adaptation layer after PR3 of
the mutable_ir ratchet (audit XJUR-02 / AGENTS.md §2.3) introduced CoW variants
of the in-place ``uk_*`` mutation helpers from ``mutable_ir.py``. PR4 of the
same ratchet relocated these CoW helpers out of ``mutable_ir.py`` so that the
``mutable_ir`` shadow module's eventual deletion does not need to touch any of
the apply sites that already route through CoW.

These helpers accept the still-mutable ``UKMutableNode`` (which is the runtime
intermediate representation used by the UK replay engine). They MUST NOT be
mutated in place by callers; the helpers themselves only build fresh nodes /
fresh lists via ``dataclasses.replace``.
"""

from __future__ import annotations

from typing import cast

from lawvm.core.ir import IRNode
from lawvm.uk_legislation.mutable_ir import (
    UKMutableNode,
    uk_has_same_kind_label_child,
)
from dataclasses import replace as dc_replace


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
    return dc_replace(node, children=list(new_children))


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

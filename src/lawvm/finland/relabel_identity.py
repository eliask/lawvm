"""Typed identity helpers for same-parent Finnish relabel chains."""

from __future__ import annotations

from dataclasses import dataclass

from lawvm.core.canonical_intent import Relabel
from lawvm.core.tree_ops import Path
from lawvm.finland.ops import ResolvedOp


@dataclass(frozen=True, slots=True)
class RelabelParentKey:
    """Identity of a relabel chain constrained to one parent path."""

    unit_kind: str
    parent_path: Path


def stabilize_same_parent_relabel_order(resolved: list[ResolvedOp]) -> list[ResolvedOp]:
    """Reorder same-parent RELABEL chains so consumers run before producers.

    This covers chapter relabel chains like ``10 luku -> 11 luku`` / ``11 luku -> 12 luku``
    and same-parent section relabel chains like ``9 § -> 10 §`` / ``10 § -> 11 §`` /
    ``11 § -> 12 §``. Applied naively in textual order, the first relabel can create the
    label that the second relabel then mistakenly consumes from the just-renamed node.

    We group relabels by (unit_kind, parent_path) so only genuine same-parent chains are
    reordered. Non-relabel ops remain in their original positions.
    """

    def _relabel_key(rop: ResolvedOp) -> RelabelParentKey | None:
        if rop.resolved_action_type != "RENUMBER":
            return None
        intent = rop.intent
        if not isinstance(intent, Relabel):
            return None
        if intent.destination is None:
            return None
        if not intent.source.address.path or not intent.destination.address.path:
            return None
        unit_kind = intent.source.address.path[-1][0]
        if unit_kind != intent.destination.address.path[-1][0]:
            return None
        if unit_kind not in {"chapter", "section", "subsection", "item", "subitem"}:
            return None
        source_parent = intent.source.address.path[:-1]
        dest_parent = intent.destination.address.path[:-1]
        if source_parent != dest_parent:
            return None
        return RelabelParentKey(unit_kind=unit_kind, parent_path=source_parent)

    def _relabel_dest(rop: ResolvedOp) -> str | None:
        intent = rop.intent
        if not isinstance(intent, Relabel) or intent.destination is None:
            return None
        return intent.destination.address.leaf_label()

    def _relabel_source(rop: ResolvedOp) -> str | None:
        intent = rop.intent
        if not isinstance(intent, Relabel):
            return None
        return intent.source.address.leaf_label()

    keyed_positions: dict[RelabelParentKey, list[int]] = {}
    keyed_ops: dict[RelabelParentKey, list[ResolvedOp]] = {}
    keyed_dests: dict[RelabelParentKey, list[str]] = {}
    for idx, rop in enumerate(resolved):
        key = _relabel_key(rop)
        dest = _relabel_dest(rop)
        if key is None or dest is None:
            continue
        keyed_positions.setdefault(key, []).append(idx)
        keyed_ops.setdefault(key, []).append(rop)
        keyed_dests.setdefault(key, []).append(dest)

    result = list(resolved)
    for key, relabel_ops in keyed_ops.items():
        if len(relabel_ops) < 2:
            continue
        relabel_positions = keyed_positions[key]
        relabel_dests = keyed_dests[key]

        source_to_rel_idx: dict[str, int] = {}
        for rel_idx, rop in enumerate(relabel_ops):
            source_label = _relabel_source(rop)
            if source_label is not None:
                source_to_rel_idx[source_label] = rel_idx

        n_rel = len(relabel_ops)
        before: list[set[int]] = [set() for _ in range(n_rel)]
        has_chain = False
        for rel_idx, dest in enumerate(relabel_dests):
            if dest in source_to_rel_idx:
                consumer_idx = source_to_rel_idx[dest]
                if consumer_idx != rel_idx:
                    before[rel_idx].add(consumer_idx)
                    has_chain = True

        if not has_chain:
            continue

        in_degree = [len(b) for b in before]
        unblocks: list[list[int]] = [[] for _ in range(n_rel)]
        for j in range(n_rel):
            for k in before[j]:
                unblocks[k].append(j)

        queue = [j for j in range(n_rel) if in_degree[j] == 0]
        topo_order: list[int] = []
        while queue:
            cur = queue.pop(0)
            topo_order.append(cur)
            for nxt in unblocks[cur]:
                in_degree[nxt] -= 1
                if in_degree[nxt] == 0:
                    queue.append(nxt)

        if len(topo_order) != n_rel:
            continue

        for pos_in_list, rel_idx in zip(relabel_positions, topo_order, strict=True):
            result[pos_in_list] = relabel_ops[rel_idx]
    return result


def stabilize_chapter_relabel_order(resolved: list[ResolvedOp]) -> list[ResolvedOp]:
    """Backward-compat alias for the broader same-parent relabel ordering helper."""
    return stabilize_same_parent_relabel_order(resolved)

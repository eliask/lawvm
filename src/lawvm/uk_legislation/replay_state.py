"""UK replay executor state mutation and snapshot helpers."""

from __future__ import annotations

from dataclasses import replace as dc_replace
from functools import lru_cache
from typing import NamedTuple, Optional, Sequence

from lawvm.core.ir_helpers import _kind_str
from lawvm.core.ir import IRNode, IRStatute, LegalAddress, LegalOperation
from lawvm.core.mutation_boundary import TreePath, TreePaths, tree_path_from_legal_address
from lawvm.core.mutation_events import MutationEvent
from lawvm.core.semantic_types import StructuralAction
from lawvm.uk_legislation.addressing import _action_name
from lawvm.uk_legislation.canonicalize import UKCanonicalNodeMatch
from lawvm.uk_legislation.apply_rebuild import (
    uk_insert_child_sorted_cow,
    uk_insert_node_at_index_cow,
    uk_insert_node_sorted_cow,
    uk_replace_children_cow,
)

_UK_TOP_SCOPED_EID_PREFIXES = frozenset(
    {"annex", "article", "chapter", "division", "part", "schedule", "section"}
)

class NodeIndexEntry(NamedTuple):
    node: IRNode
    parent: Optional[IRNode]
    index: Optional[int]


class NodeLookupResult(NamedTuple):
    node: Optional[IRNode]
    parent: Optional[IRNode]
    index: Optional[int]


class ParentIndexEntry(NamedTuple):
    parent: Optional[IRNode]
    index: Optional[int]


class VersionedNodeLookup(NamedTuple):
    structure_mutation_serial: int
    node: Optional[IRNode]
    parent: Optional[IRNode]
    index: Optional[int]


class UKCoWAncestorChainLocateFailed(Exception):
    """Raised when ``_remove_node`` / ``_do_replace_node_in_statute`` cannot
    locate the target node through EITHER the warm EID index CoW chain OR the
    path-walk fallback (iter2 W5 M3, silent-failure review).

    Pre-fix the unreachable-else tail of both CoW chain handlers returned
    ``False`` silently. The caller at ``replay_repeal_apply.py:289-294``
    discarded the boolean and unconditionally called
    ``_record_repealed_target(target)`` — recording a repeal that never landed
    against the live tree (over-repeal risk, AGENTS.md §0). Evidence used to
    diagnose the missing target should never be re-derived from the live tree
    guess either (§1.11 / §1.12): the typed carrier is the receipt of failure,
    not a ``return False`` the caller silently swallows.

    The exception carries the original ``(target, parent, idx)`` tuple passed
    to the CoW chain entry so the caller can route it into a typed
    ``uk_replay_cow_chain_locate_failed`` adjudication rather than recording a
    false repeal. ``parent`` / ``idx`` are ``None`` for the replace path,
    where the original ``_do_replace_node_in_statute`` only had ``old_node``
    to thread into the warm-index lookup.
    """

    target: IRNode
    parent: Optional[IRNode]
    idx: Optional[int]

    def __init__(
        self,
        *,
        target: IRNode,
        parent: Optional[IRNode] = None,
        idx: Optional[int] = None,
    ) -> None:
        object.__setattr__(self, "target", target)
        object.__setattr__(self, "parent", parent)
        object.__setattr__(self, "idx", idx)
        parent_repr = (
            f" parent={parent.kind.value}:{parent.label!r}"
            if parent is not None
            else " parent=None"
        )
        idx_repr = f" idx={idx}" if idx is not None else " idx=None"
        super().__init__(
            "UK replay CoW chain fail-loud: both the warm EID index CoW "
            "rebuild (``_cow_*_preserve_warm_index``) AND the path-walk "
            "fallback (``_cow_*_via_path_walk``) failed to locate the "
            f"target={target.kind.value}:{target.label!r}{parent_repr}{idx_repr}. "
            "Previously this branch silently returned False and the caller "
            "unconditionally recorded a repeal/replace that never landed "
            "(AGENTS.md §0 over-repeal risk)."
        )


type TargetLookupKey = tuple[tuple[tuple[str, Optional[str]], ...], bool, bool]
# Key: (id(root_node), kind, label) → (serial, tuple-of-matches capped at 2)
type _RecursiveMatchAllKey = tuple[int, str, str]
type _NodeTreePathIndex = dict[int, tuple[IRNode, TreePath]]
type _NodeStructuralShape = tuple[
    object,
    Optional[str],
    tuple[_NodeStructuralShape, ...],
]
_MISSING_NODE_LOOKUP = NodeLookupResult(node=None, parent=None, index=None)
_ROOT_PARENT_INDEX = ParentIndexEntry(parent=None, index=None)


@lru_cache(maxsize=262_144)
def _cached_eid_top_scope_key(eid: str) -> str:
    parts = eid.split("-")
    if len(parts) >= 3 and parts[0] in _UK_TOP_SCOPED_EID_PREFIXES and parts[1]:
        return f"{parts[0]}-{parts[1]}"
    return ""


@lru_cache(maxsize=262_144)
def _cached_eid_suffix_alias_keys(eid: str) -> tuple[tuple[str, str], ...]:
    raw = eid.strip()
    if not raw:
        return ()
    aliases: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for separator in ("-", "_"):
        parts = raw.split(separator)
        if len(parts) < 2:
            continue
        for start in range(1, len(parts)):
            suffix = separator.join(parts[start:]).strip()
            if not suffix:
                continue
            key = (_cached_eid_top_scope_key(suffix), suffix)
            if key not in seen:
                seen.add(key)
                aliases.append(key)
            if key[0]:
                global_key = ("", suffix)
                if global_key not in seen:
                    seen.add(global_key)
                    aliases.append(global_key)
    return tuple(aliases)


def _identity_index(nodes: Sequence[IRNode], target: IRNode) -> int | None:
    for index, node in enumerate(nodes):
        if node is target:
            return index
    return None


class UKReplayStateMixin:
    statute: IRStatute
    lo_ops_out: Optional[list[LegalOperation]]
    mutation_events_out: Optional[list[MutationEvent]]
    _current_mutation_op: Optional[LegalOperation]
    _repealed_target_prefixes: set[str]
    _structure_mutation_serial: int
    _eid_lookup_index: Optional[dict[str, NodeIndexEntry]]
    _eid_lookup_ambiguous: set[str]
    _eid_suffix_lookup_index: Optional[dict[tuple[str, str], NodeIndexEntry]]
    _eid_suffix_lookup_ambiguous: set[tuple[str, str]]
    _eid_search_cache: dict[tuple[str, bool], VersionedNodeLookup]
    _target_lookup_cache: dict[TargetLookupKey, VersionedNodeLookup]
    _recursive_match_cache: dict[tuple[int, str, str], VersionedNodeLookup]
    _recursive_match_all_cache: dict[_RecursiveMatchAllKey, tuple[int, tuple[UKCanonicalNodeMatch, ...]]]
    _node_tree_path_index: Optional[_NodeTreePathIndex]

    def _invalidate_node_lookup_caches(self) -> None:
        """Drop every cache that holds *node-object references*.

        These caches (eid-search, target-lookup, recursive-match,
        recursive-match-all) memoize results that contain references to live
        ``IRNode`` objects.  Any node replacement — including a
        *text-only* replace that keeps the structural shape unchanged —
        detaches the old node object and substitutes a new one.  If an
        ancestor is mutated in place (same ``id()``) the cache key survives
        but its cached matches now reference the stale, detached child.  A
        later op that hits the cache would mutate the orphaned node and lose
        the edit.  So these caches must be cleared on *every* replacement,
        not only structural ones.
        """
        self._eid_search_cache.clear()
        self._target_lookup_cache.clear()
        self._recursive_match_cache.clear()
        self._recursive_match_all_cache.clear()

    def _note_structure_mutation(self) -> None:
        self._structure_mutation_serial += 1
        self._invalidate_node_lookup_caches()

    def _node_eid_values(self, node: IRNode) -> tuple[str, ...]:
        values: list[str] = []
        for key in ("eId", "id"):
            value = str(node.attrs.get(key) or "").strip()
            if value and value not in values:
                values.append(value)
        return tuple(values)

    def _eid_top_scope_key(self, eid: str) -> str:
        return _cached_eid_top_scope_key(str(eid or ""))

    def _eid_suffix_alias_keys(self, eid: str) -> tuple[tuple[str, str], ...]:
        return _cached_eid_suffix_alias_keys(str(eid or ""))

    def _clear_eid_lookup_index(self) -> None:
        self._eid_lookup_index = None
        self._eid_lookup_ambiguous = set()
        self._eid_suffix_lookup_index = None
        self._eid_suffix_lookup_ambiguous = set()
        self._eid_search_cache.clear()
        self._node_tree_path_index = None

    def _cached_eid_search_lookup(
        self,
        eid: str,
        *,
        allow_sequence_match: bool,
    ) -> NodeLookupResult | None:
        cached = self._eid_search_cache.get((eid, bool(allow_sequence_match)))
        if cached is None:
            return None
        serial, node, parent, idx = cached
        if serial != self._structure_mutation_serial:
            self._eid_search_cache.pop((eid, bool(allow_sequence_match)), None)
            return None
        if node is None:
            return _MISSING_NODE_LOOKUP
        if parent is not None:
            if idx is not None and 0 <= idx < len(parent.children) and parent.children[idx] is node:
                return NodeLookupResult(node=node, parent=parent, index=idx)
            try:
                current_idx = parent.children.index(node)
            except ValueError:
                self._eid_search_cache.pop((eid, bool(allow_sequence_match)), None)
                return None
            self._eid_search_cache[(eid, bool(allow_sequence_match))] = VersionedNodeLookup(
                self._structure_mutation_serial,
                node,
                parent,
                current_idx,
            )
            return NodeLookupResult(node=node, parent=parent, index=current_idx)
        if idx is not None and 0 <= idx < len(self.statute.supplements) and self.statute.supplements[idx] is node:
            return NodeLookupResult(node=node, parent=None, index=idx)
        if self.statute.body is node:
            return NodeLookupResult(node=node, parent=None, index=None)
        try:
            current_idx = self.statute.supplements.index(node)
        except ValueError:
            self._eid_search_cache.pop((eid, bool(allow_sequence_match)), None)
            return None
        self._eid_search_cache[(eid, bool(allow_sequence_match))] = VersionedNodeLookup(
            self._structure_mutation_serial,
            node,
            None,
            current_idx,
        )
        return NodeLookupResult(node=node, parent=None, index=current_idx)

    def _store_eid_search_cache(
        self,
        eid: str,
        *,
        allow_sequence_match: bool,
        result: NodeLookupResult,
    ) -> None:
        node, parent, idx = result
        self._eid_search_cache[(eid, bool(allow_sequence_match))] = VersionedNodeLookup(
            self._structure_mutation_serial,
            node,
            parent,
            idx,
        )

    def _index_node_tree_path_subtree(
        self,
        node: IRNode,
        path: TreePath,
        index: _NodeTreePathIndex,
    ) -> None:
        index[id(node)] = (node, path)
        for child in node.children:
            child_path = path + ((_kind_str(child.kind), child.label or ""),)
            self._index_node_tree_path_subtree(child, child_path, index)

    def _ensure_node_tree_path_index(self) -> _NodeTreePathIndex:
        if self._node_tree_path_index is not None:
            return self._node_tree_path_index
        index: _NodeTreePathIndex = {}
        self._index_node_tree_path_subtree(self.statute.body, (), index)
        for supplement in self.statute.supplements:
            supplement_path = ((_kind_str(supplement.kind), supplement.label or ""),)
            self._index_node_tree_path_subtree(supplement, supplement_path, index)
        self._node_tree_path_index = index
        return index

    def _cached_node_tree_path(self, node: IRNode) -> TreePath | None:
        entry = self._ensure_node_tree_path_index().get(id(node))
        if entry is None:
            return None
        cached_node, path = entry
        if cached_node is node:
            return path
        self._ensure_node_tree_path_index().pop(id(node), None)
        return None

    def _cached_node_tree_path_if_indexed(self, node: IRNode) -> TreePath | None:
        if self._node_tree_path_index is None:
            return None
        entry = self._node_tree_path_index.get(id(node))
        if entry is None:
            return None
        cached_node, path = entry
        if cached_node is node:
            return path
        self._node_tree_path_index.pop(id(node), None)
        return None

    def _remove_node_tree_path_subtree(self, node: IRNode) -> None:
        if self._node_tree_path_index is None:
            return
        stack = [node]
        while stack:
            current = stack.pop()
            entry = self._node_tree_path_index.get(id(current))
            if entry is not None and entry[0] is current:
                self._node_tree_path_index.pop(id(current), None)
            stack.extend(current.children)

    def _add_node_tree_path_subtree(
        self,
        node: IRNode,
        parent: Optional[IRNode],
    ) -> None:
        if self._node_tree_path_index is None:
            return
        if node is self.statute.body:
            path: TreePath = ()
        elif parent is not None:
            parent_path = self._cached_node_tree_path_if_indexed(parent)
            if parent_path is None:
                self._node_tree_path_index = None
                return
            path = parent_path + ((_kind_str(node.kind), node.label or ""),)
        elif any(supplement is node for supplement in self.statute.supplements):
            path = ((_kind_str(node.kind), node.label or ""),)
        else:
            self._node_tree_path_index = None
            return
        self._index_node_tree_path_subtree(node, path, self._node_tree_path_index)

    def _node_contains_node(self, root: IRNode, target: IRNode) -> bool:
        if root is target:
            return True
        stack = list(root.children)
        while stack:
            node = stack.pop()
            if node is target:
                return True
            stack.extend(node.children)
        return False

    def _target_lookup_cache_key(
        self,
        target: LegalAddress,
        *,
        allow_compound_subsection_alias: bool,
        allow_recursive_match: bool,
    ) -> TargetLookupKey:
        return (
            tuple((str(kind), label) for kind, label in target.path),
            bool(allow_compound_subsection_alias),
            bool(allow_recursive_match),
        )

    def _cached_target_lookup(
        self,
        key: TargetLookupKey,
    ) -> NodeLookupResult | None:
        cached = self._target_lookup_cache.get(key)
        if cached is None:
            return None
        serial, node, parent, idx = cached
        if serial != self._structure_mutation_serial:
            self._target_lookup_cache.pop(key, None)
            return None
        if node is None:
            return _MISSING_NODE_LOOKUP
        if parent is not None:
            if idx is not None and 0 <= idx < len(parent.children) and parent.children[idx] is node:
                return NodeLookupResult(node=node, parent=parent, index=idx)
            try:
                current_idx = parent.children.index(node)
            except ValueError:
                self._target_lookup_cache.pop(key, None)
                return None
            self._target_lookup_cache[key] = VersionedNodeLookup(
                self._structure_mutation_serial,
                node,
                parent,
                current_idx,
            )
            return NodeLookupResult(node=node, parent=parent, index=current_idx)
        if idx is not None and 0 <= idx < len(self.statute.supplements) and self.statute.supplements[idx] is node:
            return NodeLookupResult(node=node, parent=None, index=idx)
        if self.statute.body is node:
            return NodeLookupResult(node=node, parent=None, index=None)
        try:
            current_idx = self.statute.supplements.index(node)
        except ValueError:
            self._target_lookup_cache.pop(key, None)
            return None
        self._target_lookup_cache[key] = VersionedNodeLookup(
            self._structure_mutation_serial,
            node,
            None,
            current_idx,
        )
        return NodeLookupResult(node=node, parent=None, index=current_idx)

    def _store_target_lookup_cache(
        self,
        key: TargetLookupKey,
        result: NodeLookupResult,
    ) -> None:
        node, parent, idx = result
        self._target_lookup_cache[key] = VersionedNodeLookup(
            self._structure_mutation_serial,
            node,
            parent,
            idx,
        )

    def _recursive_match_cache_key(
        self,
        node: IRNode,
        *,
        kind: str,
        label: str,
    ) -> tuple[int, str, str]:
        return (id(node), str(kind), str(label))

    def _cached_recursive_match(
        self,
        key: tuple[int, str, str],
    ) -> NodeLookupResult | None:
        if key not in self._recursive_match_cache:
            return None
        cached = self._recursive_match_cache[key]
        serial, node, parent, idx = cached
        if serial != self._structure_mutation_serial:
            self._recursive_match_cache.pop(key, None)
            return None
        if node is None:
            return _MISSING_NODE_LOOKUP
        if parent is None:
            self._recursive_match_cache.pop(key, None)
            return None
        if idx is not None and 0 <= idx < len(parent.children) and parent.children[idx] is node:
            return NodeLookupResult(node=node, parent=parent, index=idx)
        try:
            current_idx = parent.children.index(node)
        except ValueError:
            self._recursive_match_cache.pop(key, None)
            return None
        self._recursive_match_cache[key] = VersionedNodeLookup(
            self._structure_mutation_serial,
            node,
            parent,
            current_idx,
        )
        return NodeLookupResult(node=node, parent=parent, index=current_idx)

    def _store_recursive_match_cache(
        self,
        key: tuple[int, str, str],
        result: NodeLookupResult,
    ) -> None:
        node, parent, idx = result
        self._recursive_match_cache[key] = VersionedNodeLookup(
            self._structure_mutation_serial,
            node,
            parent,
            idx,
        )

    def _cached_recursive_match_all(
        self,
        key: _RecursiveMatchAllKey,
    ) -> tuple[UKCanonicalNodeMatch, ...] | None:
        """Return cached all-matches tuple if still valid, or None.

        Defence-in-depth: like ``_cached_recursive_match`` /
        ``_cached_target_lookup``, every hit re-validates that each cached
        match is still attached at its recorded location.  This does not rely
        solely on ``_structure_mutation_serial`` being bumped; if a future
        mutation path detaches a matched node without bumping the serial (the
        failure class the class docstring warns about), the entry is dropped
        and recomputed rather than serving a detached node.  Validation is
        ``O(matches)`` with the same cheap identity/index checks the sibling
        caches use — no tree walks.  The whole entry is recomputed on any
        failure (no partial filtering), matching sibling-cache semantics.
        """
        entry = self._recursive_match_all_cache.get(key)
        if entry is None:
            return None
        serial, matches = entry
        if serial != self._structure_mutation_serial:
            self._recursive_match_all_cache.pop(key, None)
            return None
        revalidated: list[UKCanonicalNodeMatch] = []
        for match in matches:
            node, parent, idx = match
            if node is None or parent is None:
                # All-matches entries are descendant matches that always carry
                # a parent; a None node/parent means the entry is unverifiable
                # by cheap checks, so drop and recompute.
                self._recursive_match_all_cache.pop(key, None)
                return None
            children = parent.children
            if idx is not None and 0 <= idx < len(children) and children[idx] is node:
                revalidated.append(match)
                continue
            try:
                current_idx = children.index(node)
            except ValueError:
                self._recursive_match_all_cache.pop(key, None)
                return None
            revalidated.append(UKCanonicalNodeMatch(node, parent, current_idx))
        healed = tuple(revalidated)
        if healed != matches:
            self._recursive_match_all_cache[key] = (
                self._structure_mutation_serial,
                healed,
            )
        return healed

    def _store_recursive_match_all_cache(
        self,
        key: _RecursiveMatchAllKey,
        matches: tuple[UKCanonicalNodeMatch, ...],
    ) -> None:
        """Cache an all-matches result (capped at 2) keyed by (id(node), kind, label)."""
        self._recursive_match_all_cache[key] = (self._structure_mutation_serial, matches)

    def _index_eid_subtree(
        self,
        node: IRNode,
        parent: Optional[IRNode],
        idx: Optional[int],
        index: dict[str, NodeIndexEntry],
        ambiguous: set[str],
        suffix_index: dict[tuple[str, str], NodeIndexEntry],
        suffix_ambiguous: set[tuple[str, str]],
    ) -> None:
        for eid in self._node_eid_values(node):
            if eid in ambiguous:
                continue
            if eid in index and index[eid].node is not node:
                index.pop(eid, None)
                ambiguous.add(eid)
                continue
            index[eid] = NodeIndexEntry(node=node, parent=parent, index=idx)
            # §case-alias: anchors and oracle-eid keys are kept lowercase, while
            # emitted node eIds uppercase letter suffixes (e.g. section-17-1A).
            # Register a lowercase exact alias so a lowercase anchor resolves to
            # the uppercase node before falling through to sequence/suffix lookup
            # that can misroute a sibling inserted provision to a descendant.
            lower_eid = eid.lower()
            if lower_eid != eid and lower_eid not in ambiguous:
                if lower_eid in index and index[lower_eid].node is not node:
                    index.pop(lower_eid, None)
                    ambiguous.add(lower_eid)
                else:
                    index[lower_eid] = NodeIndexEntry(node=node, parent=parent, index=idx)
            for suffix_key in self._eid_suffix_alias_keys(eid):
                if suffix_key in suffix_ambiguous:
                    continue
                if suffix_key in suffix_index and suffix_index[suffix_key].node is not node:
                    suffix_index.pop(suffix_key, None)
                    suffix_ambiguous.add(suffix_key)
                    continue
                suffix_index[suffix_key] = NodeIndexEntry(node=node, parent=parent, index=idx)
        for child_idx, child in enumerate(node.children):
            self._index_eid_subtree(
                child,
                node,
                child_idx,
                index,
                ambiguous,
                suffix_index,
                suffix_ambiguous,
            )

    def _ensure_eid_lookup_index(
        self,
    ) -> dict[str, NodeIndexEntry]:
        if self._eid_lookup_index is not None:
            return self._eid_lookup_index
        index: dict[str, NodeIndexEntry] = {}
        ambiguous: set[str] = set()
        suffix_index: dict[tuple[str, str], NodeIndexEntry] = {}
        suffix_ambiguous: set[tuple[str, str]] = set()
        for child_idx, child in enumerate(self.statute.body.children):
            self._index_eid_subtree(
                child,
                self.statute.body,
                child_idx,
                index,
                ambiguous,
                suffix_index,
                suffix_ambiguous,
            )
        for supplement_idx, supplement in enumerate(self.statute.supplements):
            self._index_eid_subtree(
                supplement,
                None,
                supplement_idx,
                index,
                ambiguous,
                suffix_index,
                suffix_ambiguous,
            )
        self._eid_lookup_index = index
        self._eid_lookup_ambiguous = ambiguous
        self._eid_suffix_lookup_index = suffix_index
        self._eid_suffix_lookup_ambiguous = suffix_ambiguous
        return index

    def _cached_exact_eid_lookup(
        self,
        eid: str,
    ) -> NodeLookupResult:
        if not eid or eid in self._eid_lookup_ambiguous:
            return _MISSING_NODE_LOOKUP
        entry = self._ensure_eid_lookup_index().get(eid)
        if entry is None:
            return _MISSING_NODE_LOOKUP
        node, parent, idx = entry
        if parent is not None:
            if idx is not None and 0 <= idx < len(parent.children) and parent.children[idx] is node:
                return NodeLookupResult(node=node, parent=parent, index=idx)
            try:
                current_idx = parent.children.index(node)
            except ValueError:
                self._ensure_eid_lookup_index().pop(eid, None)
                return _MISSING_NODE_LOOKUP
            self._ensure_eid_lookup_index()[eid] = NodeIndexEntry(node=node, parent=parent, index=current_idx)
            return NodeLookupResult(node=node, parent=parent, index=current_idx)
        if idx is not None and 0 <= idx < len(self.statute.supplements) and self.statute.supplements[idx] is node:
            return NodeLookupResult(node=node, parent=None, index=idx)
        try:
            current_idx = self.statute.supplements.index(node)
        except ValueError:
            self._ensure_eid_lookup_index().pop(eid, None)
            return _MISSING_NODE_LOOKUP
        self._ensure_eid_lookup_index()[eid] = NodeIndexEntry(node=node, parent=None, index=current_idx)
        return NodeLookupResult(node=node, parent=None, index=current_idx)

    def _cached_suffix_eid_lookup(
        self,
        eid: str,
    ) -> NodeLookupResult:
        if not eid:
            return _MISSING_NODE_LOOKUP
        self._ensure_eid_lookup_index()
        if self._eid_suffix_lookup_index is None:
            return _MISSING_NODE_LOOKUP
        top_scope = self._eid_top_scope_key(eid)
        top_scope_node = None
        if top_scope:
            top_scope_node, _top_parent, _top_idx = self._cached_exact_eid_lookup(top_scope)
            if top_scope_node is None:
                return _MISSING_NODE_LOOKUP
        lookup_keys = ((top_scope, eid),) if top_scope else (("", eid),)
        for lookup_key in lookup_keys:
            if lookup_key in self._eid_suffix_lookup_ambiguous:
                continue
            entry = self._eid_suffix_lookup_index.get(lookup_key)
            if entry is None:
                continue
            node, parent, idx = entry
            if top_scope_node is not None and not self._node_contains_node(top_scope_node, node):
                continue
            if parent is not None:
                if idx is not None and 0 <= idx < len(parent.children) and parent.children[idx] is node:
                    return NodeLookupResult(node=node, parent=parent, index=idx)
                try:
                    current_idx = parent.children.index(node)
                except ValueError:
                    self._eid_suffix_lookup_index.pop(lookup_key, None)
                    continue
                self._eid_suffix_lookup_index[lookup_key] = NodeIndexEntry(
                    node=node,
                    parent=parent,
                    index=current_idx,
                )
                return NodeLookupResult(node=node, parent=parent, index=current_idx)
            if idx is not None and 0 <= idx < len(self.statute.supplements) and self.statute.supplements[idx] is node:
                return NodeLookupResult(node=node, parent=None, index=idx)
            if self.statute.body is node:
                return NodeLookupResult(node=node, parent=None, index=None)
            try:
                current_idx = self.statute.supplements.index(node)
            except ValueError:
                self._eid_suffix_lookup_index.pop(lookup_key, None)
                continue
            self._eid_suffix_lookup_index[lookup_key] = NodeIndexEntry(
                node=node,
                parent=None,
                index=current_idx,
            )
            return NodeLookupResult(node=node, parent=None, index=current_idx)
        return _MISSING_NODE_LOOKUP

    def _remove_eid_lookup_subtree(self, node: IRNode) -> None:
        self._remove_node_tree_path_subtree(node)
        if self._eid_lookup_index is None:
            return
        stack = [node]
        while stack:
            current = stack.pop()
            for eid in self._node_eid_values(current):
                entry = self._eid_lookup_index.get(eid)
                if entry is not None and entry.node is current:
                    self._eid_lookup_index.pop(eid, None)
                lower_eid = eid.lower()
                if lower_eid != eid:
                    lower_entry = self._eid_lookup_index.get(lower_eid)
                    if lower_entry is not None and lower_entry.node is current:
                        self._eid_lookup_index.pop(lower_eid, None)
                if self._eid_suffix_lookup_index is not None:
                    for suffix_key in self._eid_suffix_alias_keys(eid):
                        suffix_entry = self._eid_suffix_lookup_index.get(suffix_key)
                        if suffix_entry is not None and suffix_entry.node is current:
                            self._eid_suffix_lookup_index.pop(suffix_key, None)
            stack.extend(current.children)

    def _add_eid_lookup_subtree(
        self,
        node: IRNode,
        parent: Optional[IRNode],
        idx: Optional[int],
    ) -> None:
        self._add_node_tree_path_subtree(node, parent)
        if self._eid_lookup_index is None:
            return
        if self._eid_suffix_lookup_index is None:
            self._eid_suffix_lookup_index = {}
        self._index_eid_subtree(
            node,
            parent,
            idx,
            self._eid_lookup_index,
            self._eid_lookup_ambiguous,
            self._eid_suffix_lookup_index,
            self._eid_suffix_lookup_ambiguous,
        )

    def _record_child_inserted(self, parent: IRNode, node: IRNode) -> None:
        idx = _identity_index(parent.children, node)
        self._add_eid_lookup_subtree(node, parent, idx)
        created_path = (
            self._tree_path_for_known_child(parent, node)
            if self.mutation_events_out is not None
            else None
        )
        self._note_structure_mutation()
        self._record_insert_node_mutation_event(
            created_path=created_path,
            helper="_record_child_inserted",
        )

    def _cow_insert_child_sorted_and_record(
        self,
        parent: IRNode,
        new_node: IRNode,
    ) -> bool:
        """PR3 (audit XJUR-02 / AGENTS.md §2.3): copy-on-write replacement for
        ``uk_insert_child_sorted(parent, new_node) +
        _record_child_inserted(parent, new_node)``.

        Builds a new parent with ``new_node`` inserted at the sorted position
        via ``uk_insert_child_sorted_cow``, threads the new parent up to the
        statute root with ``_replace_ancestor_chain`` (which atomically clears
        the eID lookup index and node-reference caches), then records the
        structural mutation event. No in-place mutation of
        ``parent.children`` occurs at any level.

        Returns True when the insert landed; False when the duplicate
        (kind, label) guard rejected the insertion (matching the legacy
        ``uk_insert_child_sorted`` ``False`` return) or when the rebuilt
        parent could not be threaded to the tree root.
        """
        new_parent, inserted_idx = uk_insert_child_sorted_cow(parent, new_node)
        if inserted_idx is None:
            return False
        if not self._replace_ancestor_chain(parent, new_parent):
            return False
        self._record_child_inserted(new_parent, new_node)
        return True

    def _cow_insert_child_at_index_and_record(
        self,
        parent: IRNode,
        idx: int,
        new_node: IRNode,
    ) -> bool:
        """PR3 (audit XJUR-02 / AGENTS.md §2.3): copy-on-write replacement for
        ``children = list(parent.children); uk_insert_node_at_index(children,
        idx, new_node); uk_replace_children(parent, children);
        _record_child_inserted(parent, new_node)``.

        Builds a new parent whose children list has ``new_node`` inserted at
        ``idx`` (CoW), threads the new parent up to the statute root, then
        records the structural mutation event.
        """
        new_children, ok = uk_insert_node_at_index_cow(list(parent.children), idx, new_node)
        if not ok:
            return False
        new_parent = uk_replace_children_cow(parent, new_children)
        if not self._replace_ancestor_chain(parent, new_parent):
            return False
        self._record_child_inserted(new_parent, new_node)
        return True

    def _cow_replace_children_and_record(
        self,
        parent: IRNode,
        new_children: list[IRNode],
        *,
        new_node: IRNode,
    ) -> bool:
        """PR3 (audit XJUR-02 / AGENTS.md §2.3): copy-on-write replacement for
        ``uk_replace_children(parent, new_children) +
        _record_child_inserted(parent, node)`` where ``new_node`` is the child
        that was inserted into ``new_children``.

        Builds a new parent whose children list is exactly ``new_children`` via
        ``uk_replace_children_cow``, threads the new parent up to the statute
        root, then records the structural mutation event with ``new_node`` as
        the inserted child for lineage bookkeeping.
        """
        new_parent = uk_replace_children_cow(parent, new_children)
        if not self._replace_ancestor_chain(parent, new_parent):
            return False
        self._record_child_inserted(new_parent, new_node)
        return True

    def _record_supplement_inserted(self, node: IRNode) -> None:
        idx = _identity_index(self.statute.supplements, node)
        self._add_eid_lookup_subtree(node, None, idx)
        created_path = ((_kind_str(node.kind), node.label or ""),)
        self._note_structure_mutation()
        self._record_insert_node_mutation_event(
            created_path=created_path,
            helper="_record_supplement_inserted",
        )

    def _child_shape(self, node: IRNode) -> tuple[_NodeStructuralShape, ...]:
        return tuple(self._structural_shape(child) for child in node.children)

    def _structural_shape(self, node: IRNode) -> _NodeStructuralShape:
        return (node.kind, node.label, self._child_shape(node))

    def _eid_lookup_parent_entry(
        self,
        node: IRNode,
    ) -> ParentIndexEntry | None:
        if self._eid_lookup_index is None:
            return None
        for eid in self._node_eid_values(node):
            entry = self._eid_lookup_index.get(eid)
            if entry is None or entry.node is not node:
                continue
            _, parent, idx = entry
            if parent is not None:
                if idx is not None and 0 <= idx < len(parent.children) and parent.children[idx] is node:
                    return ParentIndexEntry(parent=parent, index=idx)
                try:
                    current_idx = parent.children.index(node)
                except ValueError:
                    self._eid_lookup_index.pop(eid, None)
                    continue
                self._eid_lookup_index[eid] = NodeIndexEntry(
                    node=node,
                    parent=parent,
                    index=current_idx,
                )
                return ParentIndexEntry(parent=parent, index=current_idx)
            if idx is not None and 0 <= idx < len(self.statute.supplements) and self.statute.supplements[idx] is node:
                return ParentIndexEntry(parent=None, index=idx)
            if self.statute.body is node:
                return _ROOT_PARENT_INDEX
            try:
                current_idx = self.statute.supplements.index(node)
            except ValueError:
                self._eid_lookup_index.pop(eid, None)
                continue
            self._eid_lookup_index[eid] = NodeIndexEntry(
                node=node,
                parent=None,
                index=current_idx,
            )
            return ParentIndexEntry(parent=None, index=current_idx)
        return None

    def _find_path_to_node(
        self,
        root: IRNode,
        target_node: IRNode,
        path: tuple[int, ...] = (),
    ) -> Optional[tuple[int, ...]]:
        if root is target_node:
            return path
        for i, child in enumerate(root.children):
            child_path = path + (i,)
            if child is target_node:
                return child_path
            if not child.children:
                continue
            found = self._find_path_to_node(child, target_node, child_path)
            if found is not None:
                return found
        return None

    def _replace_descendant_at_path(
        self,
        root: IRNode,
        path: tuple[int, ...],
        new_node: IRNode,
    ) -> IRNode:
        if not path:
            return new_node
        idx = path[0]
        children = list(root.children)
        children[idx] = self._replace_descendant_at_path(children[idx], path[1:], new_node)
        return dc_replace(root, children=children)

    def _parent_tuple_for_path(
        self,
        root: IRNode,
        path: tuple[int, ...],
    ) -> ParentIndexEntry:
        if not path:
            return _ROOT_PARENT_INDEX
        parent = root
        for child_idx in path[:-1]:
            parent = parent.children[child_idx]
        return ParentIndexEntry(parent=parent, index=path[-1])

    def _remove_descendant_at_path(
        self,
        root: IRNode,
        path: tuple[int, ...],
    ) -> IRNode:
        """PR3 (audit XJUR-02 / AGENTS.md §2.3): copy-on-write sibling of
        ``_replace_descendant_at_path`` that excises the descendant at ``path``
        instead of replacing it. Returns a NEW ``IRNode`` chain; the
        original ``root`` and any subtrees along ``path`` are rebuilt via
        ``dc_replace`` so the caller must thread the new root up to the live
        statute. Root-level removal is addressed at the supplements list level,
        so ``path`` MUST be non-empty.
        """
        if not path:
            raise ValueError(
                "_remove_descendant_at_path: cannot excise root via path descent; "
                "remove via statute.body assignment or supplements list rebuild"
            )
        if len(path) == 1:
            idx = path[0]
            new_children = list(root.children)
            new_children.pop(idx)
            return dc_replace(root, children=new_children)
        idx = path[0]
        children = list(root.children)
        children[idx] = self._remove_descendant_at_path(children[idx], path[1:])
        return dc_replace(root, children=children)

    def _replace_ancestor_chain(
        self,
        old_node: IRNode,
        new_node: IRNode,
    ) -> bool:
        """PR3 (audit XJUR-02 / AGENTS.md §2.3): thread a CoW-rebuilt node up to
        the statute root. Finds ``old_node`` in body or supplements, CoW
        rebuilds the containing root via ``_replace_descendant_at_path`` so the
        new node takes the old node's place, then assigns the rebuilt root back
        to ``self.statute.body`` or ``self.statute.supplements`` and clears
        lookup caches atomically. No in-place mutation of parent.children lists
        occurs at any level.

        ``new_node`` MUST be the rebuilt version of ``old_node``
        (``dc_replace(old_node, children=...)`` or
        ``uk_replace_children_cow(old_node, children)``); the caller is
        responsible for any subtree changes inside ``new_node``. The chain
        above ``old_node`` is rebuilt wholesale by this helper.

        Returns True if ``old_node`` was located in body or supplements, False
        otherwise. The atomic eID-lookup cache clear means lookups after this
        point rebuild lazily from the new tree state.
        """
        body_path = self._find_path_to_node(self.statute.body, old_node)
        if body_path is not None:
            new_body = self._replace_descendant_at_path(self.statute.body, body_path, new_node)
            self.statute = dc_replace(self.statute, body=new_body)
            self._clear_eid_lookup_index()
            return True
        for s_idx, root in enumerate(self.statute.supplements):
            if root is old_node:
                new_supplements = list(self.statute.supplements)
                new_supplements[s_idx] = new_node
                self.statute = dc_replace(self.statute, supplements=tuple(new_supplements))
                self._clear_eid_lookup_index()
                return True
            supp_path = self._find_path_to_node(root, old_node)
            if supp_path is not None:
                new_supp_root = self._replace_descendant_at_path(root, supp_path, new_node)
                new_supplements = list(self.statute.supplements)
                new_supplements[s_idx] = new_supp_root
                self.statute = dc_replace(self.statute, supplements=tuple(new_supplements))
                self._clear_eid_lookup_index()
                return True
        return False

    def _find_tree_path_to_node(
        self,
        root: IRNode,
        target_node: IRNode,
        prefix: TreePath = (),
    ) -> TreePath | None:
        if root is target_node:
            return prefix
        for child in root.children:
            child_path = prefix + ((_kind_str(child.kind), child.label or ""),)
            if child is target_node:
                return child_path
            if not child.children:
                continue
            found = self._find_tree_path_to_node(child, target_node, child_path)
            if found is not None:
                return found
        return None

    def _tree_path_for_mutable_node_from_parent_index(
        self,
        node: IRNode,
    ) -> TreePath | None:
        self._ensure_eid_lookup_index()
        parts: list[tuple[str, str]] = []
        current = node
        while True:
            if current is self.statute.body:
                return tuple(reversed(parts))
            supplement_idx = _identity_index(self.statute.supplements, current)
            if supplement_idx is not None:
                parts.append((_kind_str(current.kind), current.label or ""))
                return tuple(reversed(parts))
            parent_entry = self._eid_lookup_parent_entry(current)
            if parent_entry is None:
                return None
            parent, idx = parent_entry
            parts.append((_kind_str(current.kind), current.label or ""))
            if parent is self.statute.body:
                return tuple(reversed(parts))
            if parent is None:
                if idx is not None and 0 <= idx < len(self.statute.supplements):
                    if self.statute.supplements[idx] is current:
                        return tuple(reversed(parts))
                return None
            current = parent

    def _tree_path_for_mutable_node(self, node: IRNode) -> TreePath | None:
        if self.statute.body is node:
            return ()
        cached_path = self._cached_node_tree_path_if_indexed(node)
        if cached_path is not None:
            return cached_path
        indexed_path = self._tree_path_for_mutable_node_from_parent_index(node)
        if indexed_path is not None:
            return indexed_path
        cached_path = self._cached_node_tree_path(node)
        if cached_path is not None:
            return cached_path
        found = self._find_tree_path_to_node(self.statute.body, node)
        if found is not None:
            return found
        for supplement in self.statute.supplements:
            supplement_path = ((_kind_str(supplement.kind), supplement.label or ""),)
            found = self._find_tree_path_to_node(supplement, node, supplement_path)
            if found is not None:
                return found
        return None

    def _tree_path_for_known_child(
        self,
        parent: IRNode,
        node: IRNode,
    ) -> TreePath | None:
        parent_path = self._tree_path_for_mutable_node(parent)
        if parent_path is None:
            return None
        return parent_path + ((_kind_str(node.kind), node.label or ""),)

    def _record_replace_node_mutation_event(
        self,
        *,
        old_path: TreePath | None,
        new_node: IRNode,
    ) -> None:
        if self.mutation_events_out is None or old_path is None:
            return
        op = self._current_mutation_op
        if op is None:
            return
        parent_path = old_path[:-1] if old_path else ()
        new_path = parent_path + ((_kind_str(new_node.kind), new_node.label or ""),) if old_path else ()
        removed_paths: TreePaths = ()
        created_paths: TreePaths = ()
        replaced_paths: TreePaths = (old_path,)
        if new_path != old_path:
            removed_paths = (old_path,)
            created_paths = (new_path,)
            replaced_paths = ()
        source = op.source
        self.mutation_events_out.append(
            MutationEvent(
                op_id=op.op_id,
                source_statute=source.statute_id if source is not None else "",
                action=_action_name(op.action),
                helper="_replace_node_in_statute",
                outcome="replaced_node",
                resolved_target_path=tree_path_from_legal_address(op.target),
                parent_path=parent_path,
                created_paths=created_paths,
                removed_paths=removed_paths,
                replaced_paths=replaced_paths,
            )
        )

    def _record_remove_node_mutation_event(
        self,
        *,
        removed_path: TreePath | None,
    ) -> None:
        if self.mutation_events_out is None or removed_path is None:
            return
        op = self._current_mutation_op
        if op is None:
            return
        source = op.source
        self.mutation_events_out.append(
            MutationEvent(
                op_id=op.op_id,
                source_statute=source.statute_id if source is not None else "",
                action=_action_name(op.action),
                helper="_remove_node",
                outcome="removed_node",
                resolved_target_path=tree_path_from_legal_address(op.target),
                parent_path=removed_path[:-1] if removed_path else (),
                removed_paths=(removed_path,),
            )
        )

    def _record_insert_node_mutation_event(
        self,
        *,
        created_path: TreePath | None,
        helper: str,
    ) -> None:
        if self.mutation_events_out is None or created_path is None:
            return
        op = self._current_mutation_op
        if op is None:
            return
        source = op.source
        self.mutation_events_out.append(
            MutationEvent(
                op_id=op.op_id,
                source_statute=source.statute_id if source is not None else "",
                action=_action_name(op.action),
                helper=helper,
                outcome="inserted_node",
                resolved_target_path=tree_path_from_legal_address(op.target),
                parent_path=created_path[:-1] if created_path else (),
                created_paths=(created_path,),
            )
        )

    def _record_children_splice_mutation_event(
        self,
        *,
        container: IRNode,
        helper: str,
        outcome: str,
        reason_code: str,
    ) -> None:
        if self.mutation_events_out is None:
            return
        op = self._current_mutation_op
        if op is None:
            return
        container_path = self._tree_path_for_mutable_node(container)
        if container_path is None:
            return
        source = op.source
        self.mutation_events_out.append(
            MutationEvent(
                op_id=op.op_id,
                source_statute=source.statute_id if source is not None else "",
                action=_action_name(op.action),
                helper=helper,
                outcome=outcome,
                resolved_target_path=tree_path_from_legal_address(op.target),
                parent_path=container_path,
                replaced_paths=(container_path,),
                reason_code=reason_code,
            )
        )

    def _record_renumber_node_mutation_event(
        self,
        *,
        old_path: TreePath | None,
        new_node: IRNode,
        helper: str,
    ) -> None:
        if self.mutation_events_out is None or old_path is None:
            return
        op = self._current_mutation_op
        if op is None:
            return
        parent_path = old_path[:-1] if old_path else ()
        new_path = parent_path + ((_kind_str(new_node.kind), new_node.label or ""),)
        source = op.source
        self.mutation_events_out.append(
            MutationEvent(
                op_id=op.op_id,
                source_statute=source.statute_id if source is not None else "",
                action=_action_name(op.action),
                helper=helper,
                outcome="renumbered_node",
                resolved_target_path=tree_path_from_legal_address(op.target),
                parent_path=parent_path,
                renumbered_paths=((old_path, new_path),),
            )
        )

    def _record_descendant_renumber_mutation_event(
        self,
        *,
        old_path: TreePath | None,
        new_child_path: TreePath,
        helper: str,
    ) -> None:
        """Record a renumber MutationEvent for the descendant-relocation shape.

        Called after _replace_node_in_statute when a provision is rewritten
        into a parent-with-child shape (e.g. paragraph 12 → section 12 /
        sub-paragraph (1)).  The generic replace event records the mechanical
        in-place rewrite; this event carries the lineage so PIT materialization
        can see the relocation.
        """
        if self.mutation_events_out is None or old_path is None:
            return
        op = self._current_mutation_op
        if op is None:
            return
        parent_path = old_path[:-1] if old_path else ()
        source = op.source
        self.mutation_events_out.append(
            MutationEvent(
                op_id=op.op_id,
                source_statute=source.statute_id if source is not None else "",
                action=_action_name(op.action),
                helper=helper,
                outcome="renumbered_node",
                resolved_target_path=tree_path_from_legal_address(op.target),
                parent_path=parent_path,
                renumbered_paths=((old_path, new_child_path),),
            )
        )

    def _record_promoted_child_renumber_mutation_event(
        self,
        *,
        old_path: TreePath | None,
        new_node: IRNode,
        helper: str,
    ) -> None:
        """Record lineage for a child provision promoted to its parent's sibling."""
        if self.mutation_events_out is None or old_path is None or len(old_path) < 2:
            return
        op = self._current_mutation_op
        if op is None:
            return
        parent_path = old_path[:-2]
        new_path = parent_path + ((_kind_str(new_node.kind), new_node.label or ""),)
        source = op.source
        self.mutation_events_out.append(
            MutationEvent(
                op_id=op.op_id,
                source_statute=source.statute_id if source is not None else "",
                action=_action_name(op.action),
                helper=helper,
                outcome="renumbered_node",
                resolved_target_path=tree_path_from_legal_address(op.target),
                parent_path=parent_path,
                renumbered_paths=((old_path, new_path),),
            )
        )

    def _replace_node_in_statute(self, old_node: IRNode, new_node: IRNode) -> bool:
        replaced = self._do_replace_node_in_statute(old_node, new_node)
        if replaced:
            # A node replacement always detaches the old object and substitutes
            # a new one.  Node-reference caches that survive across the swap
            # (e.g. when an ancestor is mutated in place and keeps its id())
            # would otherwise serve stale matches to the next op.  The serial
            # bump inside _do_replace_*'s structure_changed branches only covers
            # *structural* changes; text-only replaces need this too.
            self._invalidate_node_lookup_caches()
        return replaced

    def _do_replace_node_in_statute(self, old_node: IRNode, new_node: IRNode) -> bool:
        structure_changed = self._structural_shape(old_node) != self._structural_shape(new_node)
        old_path = self._tree_path_for_mutable_node(old_node) if self.mutation_events_out is not None else None
        # Sub-PR C+D (audit XJUR-02 / AGENTS.md §2.3): with ``self.statute``
        # being a frozen ``IRStatute``, every replace now goes through a CoW
        # chain that rebuilds the affected ancestor path bottom-up via
        # ``dc_replace`` and threads the rebuilt root back into ``self.statute``
        # itself. The warm EID index entries are RE-KEYED (not nuked) so the EID
        # lookup hot path stays warm across replaces — preserving the contract
        # pinned by ``test_executor_replace_*_warm_eid_index`` without
        # in-place mutation of immutable ``IRNode.children`` tuples.
        #
        # The warm-EID fast path navigates up to root via the warm index only
        # (no ``_find_path_to_node`` calls); the slow fallback uses a path walk
        # and falls back to ``_clear_eid_lookup_index`` (lazy rebuild) for
        # non-EID targets where re-keying is more expensive than rebuilding.

        # Body root: direct swap of statute body. No parent chain above body.
        if self.statute.body is old_node:
            self._remove_eid_lookup_subtree(old_node)
            self.statute = dc_replace(self.statute, body=new_node)
            self._add_eid_lookup_subtree(new_node, None, None)
            # No ancestor references exist above the body root and no chain
            # to re-key. ``_add_eid_lookup_subtree`` already repopulated the
            # path index for the new body subtree; supplement entries (if any)
            # are untouched and stay warm. The prior wholesale-drop here was
            # pure waste — it discarded freshly-added entries and forced an
            # O(S) lazy rebuild on the next path lookup.
            if structure_changed:
                self._note_structure_mutation()
            self._record_replace_node_mutation_event(old_path=old_path, new_node=new_node)
            return True

        # Supplement root replacement: build a new supplements tuple via
        # CoW list rebuild (no in-place tuple mutation).
        for s_idx, root in enumerate(self.statute.supplements):
            if root is old_node:
                self._remove_eid_lookup_subtree(old_node)
                new_supps = list(self.statute.supplements)
                new_supps[s_idx] = new_node
                self.statute = dc_replace(self.statute, supplements=tuple(new_supps))
                self._add_eid_lookup_subtree(new_node, None, s_idx)
                # No chain to re-key; ``_add_eid_lookup_subtree`` already
                # repopulated the path index for the new supplement subtree
                # and the body subtree (untouched by this op) stays warm.
                if structure_changed:
                    self._note_structure_mutation()
                self._record_replace_node_mutation_event(old_path=old_path, new_node=new_node)
                return True

        # Warm-EID-indexed CoW fast path: navigate via the warm EID index
        # so ``_find_path_to_node`` is NOT called (preserves the monkeypatched
        # invariant pinned by ``test_executor_replace_reuses_eid_lookup_parent_without_path_walk``).
        parent_entry = self._eid_lookup_parent_entry(old_node)
        if (
            parent_entry is not None
            and parent_entry.parent is not None
            and parent_entry.index is not None
        ):
            parent = parent_entry.parent
            idx = parent_entry.index
            if self._cow_replace_in_subtree_preserve_warm_index(
                old_node=old_node,
                new_node=new_node,
                parent=parent,
                idx=idx,
            ):
                if structure_changed:
                    self._note_structure_mutation()
                self._record_replace_node_mutation_event(old_path=old_path, new_node=new_node)
                return True

        # Slow fallback: navigate via path walk. The slow path also uses CoW
        # chain rebuild but nukes the warm EID index (lazy rebuild) because the
        # targeted node has no EID to re-key against and rebuilding the index
        # is cheaper than a full warm-index entry walk for this cold case.
        if self._cow_replace_in_subtree_via_path_walk(old_node, new_node):
            if structure_changed:
                self._note_structure_mutation()
            self._record_replace_node_mutation_event(old_path=old_path, new_node=new_node)
            return True

        # iter2 W5 M3 (silent-failure review): the prior unreachable-else tail
        # returned ``False`` silently, allowing callers that discard the
        # boolean (e.g. the many ``self._replace_node_in_statute(node, rebuilt)``
        # call sites in replay_text_apply / replay_table_apply / replay_renumber
        # _apply / replay_replace_apply) to continue as if the replace had
        # landed. The typed exception closes the silent-drop path: BOTH the
        # warm EID index CoW rebuild (``_cow_replace_in_subtree_preserve_warm_
        # index`` above) AND the path-walk CoW fallback
        # (``_cow_replace_in_subtree_via_path_walk`` above) failed to route
        # ``old_node`` to a parent in the live tree. ``old_node`` is the only
        # identity available here — there is no parent/idx at this tail because
        # the warm-index lookup either returned None or its returned parent
        # failed to chain to a root (handled by the Cow helpers' own
        # ``return False`` branches above).
        raise UKCoWAncestorChainLocateFailed(target=old_node)

    def _cow_replace_in_subtree_preserve_warm_index(
        self,
        *,
        old_node: IRNode,
        new_node: IRNode,
        parent: IRNode,
        idx: int,
    ) -> bool:
        """CoW-rebuild ancestor chain via the warm EID index only (no path
        walks), thread the rebuilt root into ``self.statute``, and re-key the
        warm EID index entries for the rebuilt ancestor path so subsequent EID
        lookups stay warm without needing a full index rebuild.

        Builds the ``(old, new)`` ancestor chain bottom-up: leaf is
        ``(old_node, new_node)``, then each ancestor level until the body or
        a supplement root. Walks up via ``_eid_lookup_parent_entry`` so no
        ``_find_path_to_node`` call is made (preserves the warm-index hot path
        contract pinned by the pinned regression tests).
        """
        self._remove_eid_lookup_subtree(old_node)

        chain: list[tuple[IRNode, IRNode]] = [(old_node, new_node)]
        # First-level rebuild: parent with new_node at idx.
        new_parent_children = list(parent.children)
        new_parent_children[idx] = new_node
        new_parent = dc_replace(parent, children=new_parent_children)
        chain.append((parent, new_parent))

        # Walk up via warm EID index, rebuilding each ancestor.
        current_old, current_new = parent, new_parent
        seen_ids: set[int] = {id(old_node)}  # old_node already used as leaf
        while True:
            if current_old is self.statute.body:
                self.statute = dc_replace(self.statute, body=current_new)
                break
            supp_idx = _identity_index(self.statute.supplements, current_old)
            if supp_idx is not None:
                new_supps = list(self.statute.supplements)
                new_supps[supp_idx] = current_new
                self.statute = dc_replace(self.statute, supplements=tuple(new_supps))
                break
            if id(current_old) in seen_ids:
                # Defensive: should not happen because the live tree is acyclic.
                return False
            seen_ids.add(id(current_old))
            gp_entry = self._eid_lookup_parent_entry(current_old)
            if (
                gp_entry is None
                or gp_entry.parent is None
                or gp_entry.index is None
            ):
                # Warm-index navigation incomplete: cannot safely thread.
                # Caller falls back to the path-walk CoW path.
                return False
            grandparent, gp_idx = gp_entry.parent, gp_entry.index
            new_gp_children = list(grandparent.children)
            new_gp_children[gp_idx] = current_new
            new_grandparent = dc_replace(grandparent, children=new_gp_children)
            chain.append((grandparent, new_grandparent))
            current_old, current_new = grandparent, new_grandparent

        # Re-key the warm EID index entries using the rebuilt ancestor chain.
        self._rekey_eid_index_after_cow_chain(chain)
        # Re-key the node tree path index for the rebuilt ancestors BEFORE
        # re-adding new_node's subtree so the parent path is warm — otherwise
        # ``_add_node_tree_path_subtree`` falls into its wholesale-drop branch
        # when the post-CoW parent's ``id()`` is not yet in the path index.
        self._rekey_node_tree_path_index_after_cow_chain(chain)
        # Re-add new_node's subtree EIDs (with the rebuilt immediate parent).
        self._add_eid_lookup_subtree(new_node, parent=chain[1][1], idx=idx)
        return True

    def _cow_replace_in_subtree_via_path_walk(
        self,
        old_node: IRNode,
        new_node: IRNode,
    ) -> bool:
        """Slow-path CoW chain via path walk. Used when the warm EID index
        did not yield a parent entry (e.g. a non-EID target leaf). Rebuilds
        the ancestor chain via ``_replace_descendant_at_path`` and nukes the
        warm EID index for lazy rebuild on next access."""
        body_path = self._find_path_to_node(self.statute.body, old_node)
        if body_path is not None:
            if not body_path:
                # old_node IS body — defensive (handled above in caller).
                self._remove_eid_lookup_subtree(old_node)
                self.statute = dc_replace(self.statute, body=new_node)
                self._add_eid_lookup_subtree(new_node, None, None)
                self._clear_eid_lookup_index()
                return True
            new_body, chain = self._replace_descendant_at_path_with_chain(
                self.statute.body, body_path, new_node
            )
            self._remove_eid_lookup_subtree(old_node)
            self.statute = dc_replace(self.statute, body=new_body)
            self._rekey_eid_index_after_cow_chain(chain)
            self._rekey_node_tree_path_index_after_cow_chain(chain)
            self._add_eid_lookup_subtree(
                new_node, parent=chain[1][1], idx=body_path[-1]
            )
            return True
        for s_idx, root in enumerate(self.statute.supplements):
            if root is old_node:
                # Already handled in caller; defensive.
                self._remove_eid_lookup_subtree(old_node)
                new_supps = list(self.statute.supplements)
                new_supps[s_idx] = new_node
                self.statute = dc_replace(self.statute, supplements=tuple(new_supps))
                self._clear_eid_lookup_index()
                return True
            sub_path = self._find_path_to_node(root, old_node)
            if sub_path is not None:
                new_supp_root, chain = self._replace_descendant_at_path_with_chain(
                    root, sub_path, new_node
                )
                self._remove_eid_lookup_subtree(old_node)
                new_supps = list(self.statute.supplements)
                new_supps[s_idx] = new_supp_root
                self.statute = dc_replace(self.statute, supplements=tuple(new_supps))
                self._rekey_eid_index_after_cow_chain(chain)
                self._rekey_node_tree_path_index_after_cow_chain(chain)
                self._add_eid_lookup_subtree(
                    new_node, parent=chain[1][1], idx=sub_path[-1]
                )
                return True
        return False

    def _replace_descendant_at_path_with_chain(
        self,
        root: IRNode,
        path: tuple[int, ...],
        new_node: IRNode,
    ) -> tuple[IRNode, list[tuple[IRNode, IRNode]]]:
        """CoW-replace the descendant at integer-index ``path`` under ``root``,
        returning the rebuilt root AND a chain of ``(old, new)`` ancestor pairs
        leaf-first (i.e. ``[(target, new_node), (parent, new_parent), ...,
        (root, new_root)]``) so the caller can re-key warm EID index entries.
        """
        chain: list[tuple[IRNode, IRNode]] = []

        def _walk(node: IRNode, depth: int) -> IRNode:
            if depth == len(path):
                chain.append((node, new_node))
                return new_node
            i = path[depth]
            children = list(node.children)
            children[i] = _walk(node.children[i], depth + 1)
            new_node_at_level = dc_replace(node, children=children)
            chain.append((node, new_node_at_level))
            return new_node_at_level

        new_root = _walk(root, 0)
        return new_root, chain

    def _rekey_eid_index_after_cow_chain(
        self,
        chain: list[tuple[IRNode, IRNode]],
    ) -> None:
        """After a CoW ancestor rebuild, patch the warm EID index entries in
        place: only entries whose ``node`` or ``parent`` is one of the rebuilt
        ancestors in ``chain`` are re-allocated as a fresh ``NodeIndexEntry``;
        untouched entries keep their existing allocated tuple.

        ``chain`` is leaf-first: ``[(old_node, new_node), (parent, new_parent),
        ..., (root, new_root)]`` inclusive of the rebuilt root.

        Cost: O(W) iteration over the warm EID index (cheap dict walk) +
        O(touched) ``NodeIndexEntry`` allocations, where ``touched`` is the
        number of entries whose ``id(node)`` or ``id(parent)`` is in the
        chain remap. For replaces deep in the tree (paragraph / subsection
        leaves) the chain remap is short, so survivor subtrees outside the
        rebuilt ancestor path keep their allocated entries — previously every
        entry was re-allocated per CoW replace, which on W≈6000 / N≈400
        statute = ~2.4M fresh ``NodeIndexEntry`` allocations that this patch
        eliminates for survivor entries.

        Does NOT clear the warm EID index (preserving the contract pinned by
        ``test_executor_replace_*_warm_eid_index`` and
        ``test_post_replace_lookups_stay_warm_across_replaces``).
        """
        if not chain:
            return
        remap: dict[int, IRNode] = {id(old): new for old, new in chain}
        if self._eid_lookup_index is not None:
            # Patch in place: iterate entries, but only allocate a fresh
            # ``NodeIndexEntry`` when the entry's ``node`` or ``parent`` is
            # in ``remap`` (i.e. touched by the rebuilt ancestor chain).
            # Untouched entries keep their existing tuple.
            for eid, entry in list(self._eid_lookup_index.items()):
                new_node = remap.get(id(entry.node))
                new_parent = (
                    remap.get(id(entry.parent))
                    if entry.parent is not None
                    else None
                )
                if new_node is None and new_parent is None:
                    continue  # Not in chain; leave entry untouched.
                self._eid_lookup_index[eid] = NodeIndexEntry(
                    node=new_node if new_node is not None else entry.node,
                    parent=new_parent if new_parent is not None else entry.parent,
                    index=entry.index,
                )
        if self._eid_suffix_lookup_index is not None:
            for key, entry in list(self._eid_suffix_lookup_index.items()):
                new_node = remap.get(id(entry.node))
                new_parent = (
                    remap.get(id(entry.parent))
                    if entry.parent is not None
                    else None
                )
                if new_node is None and new_parent is None:
                    continue
                self._eid_suffix_lookup_index[key] = NodeIndexEntry(
                    node=new_node if new_node is not None else entry.node,
                    parent=new_parent if new_parent is not None else entry.parent,
                    index=entry.index,
                )

    def _rekey_node_tree_path_index_after_cow_chain(
        self,
        chain: list[tuple[IRNode, IRNode]],
    ) -> None:
        """Patch ``_node_tree_path_index`` in place: for each ``(old, new)``
        pair in ``chain[1:]`` (the rebuilt ancestors; ``chain[0]`` is the
        replaced leaf, whose subtree entries were already handled by the
        caller's ``_remove_eid_lookup_subtree`` + ``_add_eid_lookup_subtree``
        calls), pop the entry keyed by ``id(old)`` and re-insert keyed by
        ``id(new)`` carrying the same path. CoW chain rebuild preserves
        ``kind`` / ``label`` sequences, so the survivor's path is unchanged
        — only ``id()``s along the ancestor path are swapped.

        Cost: O(chain_len) dict ops vs. the prior O(S) wholesale-drop +
        O(S) lazy-rebuild-from-walk on next access (S = full statute tree
        size). When the path index is ``None`` (cold / lazy), this is a
        no-op — the next access still triggers the standard lazy rebuild.
        """
        if self._node_tree_path_index is None or not chain:
            return  # Cold path: nothing to re-key.
        # Skip chain[0]: the replaced leaf's path entries were either
        # already removed by ``_remove_eid_lookup_subtree`` (replace case)
        # or never present (remove case — chain[0] = the rebuilt parent of
        # the popped leaf, so its entry is still id(old_parent) → patched).
        # Only chain[1:] (rebuilt ancestor identities) need re-keying.
        for i in range(1, len(chain)):
            old, new = chain[i]
            old_entry = self._node_tree_path_index.pop(id(old), None)
            if old_entry is None:
                continue  # Not indexed (e.g. chain[0] leaf case above).
            if id(new) == id(old):
                # Defensive: dc_replace always mints a fresh IRNode, so id
                # differs; but if a future no-op CoW path returns the same
                # object, keep the entry under the original key.
                self._node_tree_path_index[id(old)] = old_entry
                continue
            # Path is preserved — CoW chain rebuild doesn't change the
            # kind/label sequence of any ancestor.
            self._node_tree_path_index[id(new)] = (new, old_entry[1])

    def _remove_node(
        self,
        node: IRNode,
        parent: Optional[IRNode],
        idx: Optional[int],
    ) -> bool:
        removed_path = None
        if self.mutation_events_out is not None:
            if parent is not None:
                removed_path = self._tree_path_for_known_child(parent, node)
            else:
                removed_path = self._tree_path_for_mutable_node(node)
        # Sub-PR D (audit XJUR-02 / AGENTS.md §2.3): the in-place
        # ``parent.children.pop(idx)`` fast path is gone — ``parent.children``
        # is now an immutable ``Tuple[IRNode, ...]`` once ``self.statute`` is an
        # ``IRStatute``. CoW chain rebuild via the warm EID index keeps the
        # warm-index consistency contract preserved.
        if parent is not None and idx is not None:
            self._remove_eid_lookup_subtree(node)
            if self._cow_remove_in_parent_preserve_warm_index(
                node=node, parent=parent, idx=idx
            ):
                self._note_structure_mutation()
                self._record_remove_node_mutation_event(removed_path=removed_path)
                return True
            # Warm-index navigation failed: fall back to path-walk CoW chain.
            if self._cow_remove_via_path_walk(node):
                self._note_structure_mutation()
                self._record_remove_node_mutation_event(removed_path=removed_path)
                return True
        for s_idx, root in enumerate(self.statute.supplements):
            if root is node:
                self._remove_eid_lookup_subtree(node)
                new_supps = list(self.statute.supplements)
                popped_s_idx = s_idx
                new_supps.pop(popped_s_idx)
                self.statute = dc_replace(self.statute, supplements=tuple(new_supps))
                self._note_structure_mutation()
                self._record_remove_node_mutation_event(removed_path=removed_path)
                return True
        # iter2 W5 M3 (silent-failure review): the prior unreachable-else tail
        # returned ``False`` silently, which the caller discarded before
        # recording a repeal that never landed against the live tree (over-
        # repeal risk, AGENTS.md §0). Both branches that reach here only do
        # so after the warm EID index CoW chain AND the path-walk fallback
        # already failed for the parent-with-idx case (above) AND the
        # supplements loop above did not find the node by identity — at which
        # point continuing to model the call as a "no-op success" is exactly
        # the silent-heuristic shape §0 forbids. Fail loud with a typed
        # exception so the caller can route this into a typed adjudication
        # rather than a silent ``_record_repealed_target(target)``.
        raise UKCoWAncestorChainLocateFailed(target=node, parent=parent, idx=idx)

    def _cow_remove_in_parent_preserve_warm_index(
        self,
        *,
        node: IRNode,
        parent: IRNode,
        idx: int,
    ) -> bool:
        """CoW-rebuild ancestor chain so ``parent.children`` no longer contains
        ``node`` at ``idx``, navigating via the warm EID index. Re-keys warm
        EID index entries for the rebuilt ancestor chain. Returns False if the
        warm index does not yield a clean chain (caller falls back to path walk)."""
        # First-level rebuild: parent with node popped from children.
        new_parent_children = list(parent.children)
        new_parent_children.pop(idx)
        new_parent = dc_replace(parent, children=new_parent_children)
        chain: list[tuple[IRNode, IRNode]] = [(parent, new_parent)]
        current_old, current_new = parent, new_parent
        seen_ids: set[int] = set()
        while True:
            if current_old is self.statute.body:
                self.statute = dc_replace(self.statute, body=current_new)
                break
            supp_idx = _identity_index(self.statute.supplements, current_old)
            if supp_idx is not None:
                new_supps = list(self.statute.supplements)
                new_supps[supp_idx] = current_new
                self.statute = dc_replace(self.statute, supplements=tuple(new_supps))
                break
            if id(current_old) in seen_ids:
                return False
            seen_ids.add(id(current_old))
            gp_entry = self._eid_lookup_parent_entry(current_old)
            if (
                gp_entry is None
                or gp_entry.parent is None
                or gp_entry.index is None
            ):
                return False
            grandparent, gp_idx = gp_entry.parent, gp_entry.index
            new_gp_children = list(grandparent.children)
            new_gp_children[gp_idx] = current_new
            new_grandparent = dc_replace(grandparent, children=new_gp_children)
            chain.append((grandparent, new_grandparent))
            current_old, current_new = grandparent, new_grandparent
        self._rekey_eid_index_after_cow_chain(chain)
        self._rekey_node_tree_path_index_after_cow_chain(chain)
        return True

    def _cow_remove_via_path_walk(self, node: IRNode) -> bool:
        """Slow-path CoW chain removal via path walk + ancestor chain."""
        body_path = self._find_path_to_node(self.statute.body, node)
        if body_path is not None and body_path:
            new_body, _chain = self._remove_descendant_at_path_with_chain(
                self.statute.body, body_path
            )
            self.statute = dc_replace(self.statute, body=new_body)
            self._clear_eid_lookup_index()
            return True
        for s_idx, root in enumerate(self.statute.supplements):
            sub_path = self._find_path_to_node(root, node)
            if sub_path is not None and sub_path:
                new_supp_root, _chain = self._remove_descendant_at_path_with_chain(
                    root, sub_path
                )
                new_supps = list(self.statute.supplements)
                new_supps[s_idx] = new_supp_root
                self.statute = dc_replace(self.statute, supplements=tuple(new_supps))
                self._clear_eid_lookup_index()
                return True
        return False

    def _remove_descendant_at_path_with_chain(
        self,
        root: IRNode,
        path: tuple[int, ...],
    ) -> tuple[IRNode, list[tuple[IRNode, IRNode]]]:
        """CoW-remove the descendant at integer-index ``path`` under ``root``,
        returning the rebuilt root AND a chain of ``(old, new)`` ancestor pairs
        leaf-first (i.e. starting ``(parent_of_removed, new_parent), ...`` —
        the removed node itself is not in the chain since it no longer exists)."""
        chain: list[tuple[IRNode, IRNode]] = []

        def _walk(node: IRNode, depth: int) -> IRNode:
            if depth == len(path):
                # Base case: would only hit if path were empty; defensive.
                return node
            i = path[depth]
            if depth == len(path) - 1:
                # At parent: pop child at i, do NOT descend into the removed node.
                new_children = list(node.children)
                new_children.pop(i)
                rebuilt = dc_replace(node, children=new_children)
                chain.append((node, rebuilt))
                return rebuilt
            children = list(node.children)
            children[i] = _walk(node.children[i], depth + 1)
            rebuilt = dc_replace(node, children=children)
            chain.append((node, rebuilt))
            return rebuilt

        new_root = _walk(root, 0)
        return new_root, chain

    def _find_parent_tuple_for_node(
        self,
        target_node: IRNode,
    ) -> ParentIndexEntry:
        def _walk(parent: IRNode) -> ParentIndexEntry:
            for child_idx, child in enumerate(parent.children):
                if child is target_node:
                    return ParentIndexEntry(parent=parent, index=child_idx)
                if not child.children:
                    continue
                found = _walk(child)
                if found.parent is not None:
                    return found
            return _ROOT_PARENT_INDEX

        if self.statute.body is target_node:
            return _ROOT_PARENT_INDEX
        found = _walk(self.statute.body)
        if found.parent is not None:
            return found
        for supplement in self.statute.supplements:
            if supplement is target_node:
                return _ROOT_PARENT_INDEX
            found = _walk(supplement)
            if found.parent is not None:
                return found
        return _ROOT_PARENT_INDEX

    def _insert_supplement_sorted(self, new_node: IRNode) -> bool:
        # PR3 (audit XJUR-02 / AGENTS.md §2.3): copy-on-write insert. Build a
        # new supplements list with ``new_node`` at the sorted position rather
        # than mutating ``self.statute.supplements`` in place. The atomic clear
        # in ``_record_supplement_inserted`` covers cache invalidation; the
        # bookkeeping there looks up ``new_node`` by identity in the freshly
        # assigned list.
        new_supplements, inserted_idx = uk_insert_node_sorted_cow(
            list(self.statute.supplements), new_node
        )
        if inserted_idx is None:
            return False
        # Sub-PR C+D: IRStatute.supplements is Tuple[IRNode, ...]; assign via
        # ``dataclasses.replace(self.statute, supplements=tuple(list))`` rather
        # than direct assignment (frozen dataclass).
        self.statute = dc_replace(self.statute, supplements=tuple(new_supplements))
        self._record_supplement_inserted(new_node)
        return True

    def _record_repealed_target(self, target: LegalAddress) -> None:
        target_text = str(target or "").strip()
        if target_text:
            self._repealed_target_prefixes.add(target_text)

    def _target_under_repealed_prefix(self, target: LegalAddress) -> bool:
        target_text = str(target or "").strip()
        if not target_text:
            return False
        for prefix in self._repealed_target_prefixes:
            if target_text == prefix or target_text.startswith(prefix + "/"):
                return True
        return False

    def _emit_top_section_snapshot(self, op: LegalOperation) -> None:
        """Emit a top-level section/schedule snapshot to lo_ops_out after an op is applied.

        Finds the top-level node (first path segment) affected by *op* in the
        current statute state and appends a LegalOperation snapshot to lo_ops_out.
        This gives compile_timelines() section-level content for overlay
        materialization, mirroring the Finland lo_ops_out pattern.

        For repeal ops the tombstone is recorded (payload=None, action="repeal").
        For all other structural ops the current node content is snapshotted
        (action="replace" / "insert" depending on whether the node was already in
        the base, but "replace" is used as the conservative choice since
        compile_timelines handles both identically for existing addresses).
        """
        if self.lo_ops_out is None:
            return
        target = op.target
        if not target.path:
            return
        # Derive the canonical address for the top-level container.
        # For body ops this is the first path segment (e.g. section:1 or part:I).
        # For schedule ops it is the schedule element itself.
        top_kind, top_label = target.path[0]
        top_addr = LegalAddress(path=((top_kind, top_label),))

        # Find the top-level node in the current (post-op) statute state.
        # We look in body children and schedules.
        top_node: Optional[IRNode] = None
        for child in self.statute.body.children:
            if str(child.kind) == top_kind and (child.label is not None and child.label == top_label):
                top_node = child
                break
        if top_node is None:
            for sch in self.statute.supplements:
                if str(sch.kind) == top_kind and sch.label == top_label:
                    top_node = sch
                    break

        if _action_name(op.action) == "repeal" and top_node is None:
            # Node was removed — emit tombstone
            self.lo_ops_out.append(
                LegalOperation(
                    op_id=f"uk_snapshot_repeal_{top_kind}_{top_label}_{op.op_id}",
                    sequence=op.sequence,
                    action=StructuralAction.REPEAL,
                    target=top_addr,
                    payload=None,
                    source=op.source,
                    group_id=op.group_id,
                )
            )
        elif top_node is not None:
            # Snapshot the current state of the top-level node after op applied.
            self.lo_ops_out.append(
                LegalOperation(
                    op_id=f"uk_snapshot_{top_kind}_{top_label}_{op.op_id}",
                    sequence=op.sequence,
                    action=StructuralAction.REPLACE,
                    target=top_addr,
                    payload=top_node,
                    source=op.source,
                    group_id=op.group_id,
                )
            )

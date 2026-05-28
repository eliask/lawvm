"""UK replay executor state mutation and snapshot helpers."""

from __future__ import annotations

from dataclasses import replace as dc_replace
from typing import NamedTuple, Optional, Sequence, TypeAlias

from lawvm.core.ir_helpers import _kind_str
from lawvm.core.ir import LegalAddress, LegalOperation
from lawvm.core.mutation_boundary import TreePath, TreePaths, tree_path_from_legal_address
from lawvm.core.mutation_events import MutationEvent
from lawvm.core.semantic_types import StructuralAction
from lawvm.uk_legislation.addressing import _action_name
from lawvm.uk_legislation.mutable_ir import UKMutableNode, UKMutableStatute, uk_insert_node_sorted

_UK_TOP_SCOPED_EID_PREFIXES = frozenset(
    {"annex", "article", "chapter", "division", "part", "schedule", "section"}
)

class NodeIndexEntry(NamedTuple):
    node: UKMutableNode
    parent: Optional[UKMutableNode]
    index: Optional[int]


class NodeLookupResult(NamedTuple):
    node: Optional[UKMutableNode]
    parent: Optional[UKMutableNode]
    index: Optional[int]


class ParentIndexEntry(NamedTuple):
    parent: Optional[UKMutableNode]
    index: Optional[int]


class VersionedNodeLookup(NamedTuple):
    structure_mutation_serial: int
    node: Optional[UKMutableNode]
    parent: Optional[UKMutableNode]
    index: Optional[int]


TargetLookupKey: TypeAlias = tuple[tuple[tuple[str, Optional[str]], ...], bool, bool]
_NodeStructuralShape: TypeAlias = tuple[
    object,
    Optional[str],
    tuple["_NodeStructuralShape", ...],
]
_MISSING_NODE_LOOKUP = NodeLookupResult(node=None, parent=None, index=None)
_ROOT_PARENT_INDEX = ParentIndexEntry(parent=None, index=None)


def _identity_index(nodes: Sequence[UKMutableNode], target: UKMutableNode) -> int | None:
    for index, node in enumerate(nodes):
        if node is target:
            return index
    return None


class UKReplayStateMixin:
    statute: UKMutableStatute
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

    def _note_structure_mutation(self) -> None:
        self._structure_mutation_serial += 1
        self._eid_search_cache.clear()
        self._target_lookup_cache.clear()
        self._recursive_match_cache.clear()

    def _node_eid_values(self, node: UKMutableNode) -> tuple[str, ...]:
        values: list[str] = []
        for key in ("eId", "id"):
            value = str(node.attrs.get(key) or "").strip()
            if value and value not in values:
                values.append(value)
        return tuple(values)

    def _eid_top_scope_key(self, eid: str) -> str:
        parts = str(eid or "").split("-")
        if len(parts) >= 3 and parts[0] in _UK_TOP_SCOPED_EID_PREFIXES and parts[1]:
            return f"{parts[0]}-{parts[1]}"
        return ""

    def _eid_suffix_alias_keys(self, eid: str) -> tuple[tuple[str, str], ...]:
        raw = str(eid or "").strip()
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
                key = (self._eid_top_scope_key(suffix), suffix)
                if key not in seen:
                    seen.add(key)
                    aliases.append(key)
                if key[0]:
                    global_key = ("", suffix)
                    if global_key not in seen:
                        seen.add(global_key)
                        aliases.append(global_key)
        return tuple(aliases)

    def _clear_eid_lookup_index(self) -> None:
        self._eid_lookup_index = None
        self._eid_lookup_ambiguous = set()
        self._eid_suffix_lookup_index = None
        self._eid_suffix_lookup_ambiguous = set()
        self._eid_search_cache.clear()

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

    def _node_contains_node(self, root: UKMutableNode, target: UKMutableNode) -> bool:
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
        node: UKMutableNode,
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

    def _index_eid_subtree(
        self,
        node: UKMutableNode,
        parent: Optional[UKMutableNode],
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

    def _remove_eid_lookup_subtree(self, node: UKMutableNode) -> None:
        if self._eid_lookup_index is None:
            return
        stack = [node]
        while stack:
            current = stack.pop()
            for eid in self._node_eid_values(current):
                entry = self._eid_lookup_index.get(eid)
                if entry is not None and entry.node is current:
                    self._eid_lookup_index.pop(eid, None)
                if self._eid_suffix_lookup_index is not None:
                    for suffix_key in self._eid_suffix_alias_keys(eid):
                        suffix_entry = self._eid_suffix_lookup_index.get(suffix_key)
                        if suffix_entry is not None and suffix_entry.node is current:
                            self._eid_suffix_lookup_index.pop(suffix_key, None)
            stack.extend(current.children)

    def _add_eid_lookup_subtree(
        self,
        node: UKMutableNode,
        parent: Optional[UKMutableNode],
        idx: Optional[int],
    ) -> None:
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

    def _record_child_inserted(self, parent: UKMutableNode, node: UKMutableNode) -> None:
        idx = _identity_index(parent.children, node)
        self._add_eid_lookup_subtree(node, parent, idx)
        self._note_structure_mutation()
        self._record_insert_node_mutation_event(
            created_path=self._tree_path_for_mutable_node(node),
            helper="_record_child_inserted",
        )

    def _record_supplement_inserted(self, node: UKMutableNode) -> None:
        idx = _identity_index(self.statute.supplements, node)
        self._add_eid_lookup_subtree(node, None, idx)
        self._note_structure_mutation()
        self._record_insert_node_mutation_event(
            created_path=self._tree_path_for_mutable_node(node),
            helper="_record_supplement_inserted",
        )

    def _child_shape(self, node: UKMutableNode) -> tuple[_NodeStructuralShape, ...]:
        return tuple(self._structural_shape(child) for child in node.children)

    def _structural_shape(self, node: UKMutableNode) -> _NodeStructuralShape:
        return (node.kind, node.label, self._child_shape(node))

    def _eid_lookup_parent_entry(
        self,
        node: UKMutableNode,
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
        root: UKMutableNode,
        target_node: UKMutableNode,
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
        root: UKMutableNode,
        path: tuple[int, ...],
        new_node: UKMutableNode,
    ) -> UKMutableNode:
        if not path:
            return new_node
        idx = path[0]
        children = list(root.children)
        children[idx] = self._replace_descendant_at_path(children[idx], path[1:], new_node)
        return dc_replace(root, children=children)

    def _parent_tuple_for_path(
        self,
        root: UKMutableNode,
        path: tuple[int, ...],
    ) -> ParentIndexEntry:
        if not path:
            return _ROOT_PARENT_INDEX
        parent = root
        for child_idx in path[:-1]:
            parent = parent.children[child_idx]
        return ParentIndexEntry(parent=parent, index=path[-1])

    def _find_tree_path_to_node(
        self,
        root: UKMutableNode,
        target_node: UKMutableNode,
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

    def _tree_path_for_mutable_node(self, node: UKMutableNode) -> TreePath | None:
        if self.statute.body is node:
            return ()
        found = self._find_tree_path_to_node(self.statute.body, node)
        if found is not None:
            return found
        for supplement in self.statute.supplements:
            supplement_path = ((_kind_str(supplement.kind), supplement.label or ""),)
            found = self._find_tree_path_to_node(supplement, node, supplement_path)
            if found is not None:
                return found
        return None

    def _record_replace_node_mutation_event(
        self,
        *,
        old_path: TreePath | None,
        new_node: UKMutableNode,
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
        container: UKMutableNode,
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
        new_node: UKMutableNode,
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

    def _replace_node_in_statute(self, old_node: UKMutableNode, new_node: UKMutableNode) -> bool:
        structure_changed = self._structural_shape(old_node) != self._structural_shape(new_node)
        old_path = self._tree_path_for_mutable_node(old_node) if self.mutation_events_out is not None else None
        if self.statute.body is old_node:
            self._remove_eid_lookup_subtree(old_node)
            self.statute.body = new_node
            self._add_eid_lookup_subtree(new_node, None, None)
            self._clear_eid_lookup_index()
            if structure_changed:
                self._note_structure_mutation()
            self._record_replace_node_mutation_event(old_path=old_path, new_node=new_node)
            return True
        parent_entry = self._eid_lookup_parent_entry(old_node)
        if parent_entry is not None:
            parent, idx = parent_entry
            if parent is not None and idx is not None:
                self._remove_eid_lookup_subtree(old_node)
                parent.children[idx] = new_node
                self._add_eid_lookup_subtree(new_node, parent, idx)
                if structure_changed:
                    self._note_structure_mutation()
                self._record_replace_node_mutation_event(old_path=old_path, new_node=new_node)
                return True
            if idx is not None:
                self._remove_eid_lookup_subtree(old_node)
                self.statute.supplements[idx] = new_node
                self._add_eid_lookup_subtree(new_node, None, idx)
                if structure_changed:
                    self._note_structure_mutation()
                self._record_replace_node_mutation_event(old_path=old_path, new_node=new_node)
                return True
        body_path = self._find_path_to_node(self.statute.body, old_node)
        if body_path is not None:
            self._remove_eid_lookup_subtree(old_node)
            self.statute.body = self._replace_descendant_at_path(self.statute.body, body_path, new_node)
            self._clear_eid_lookup_index()
            if structure_changed:
                self._note_structure_mutation()
            self._record_replace_node_mutation_event(old_path=old_path, new_node=new_node)
            return True
        for idx, root in enumerate(self.statute.supplements):
            if root is old_node:
                self._remove_eid_lookup_subtree(old_node)
                self.statute.supplements[idx] = new_node
                self._add_eid_lookup_subtree(new_node, None, idx)
                if structure_changed:
                    self._note_structure_mutation()
                self._record_replace_node_mutation_event(old_path=old_path, new_node=new_node)
                return True
            sub_path = self._find_path_to_node(root, old_node)
            if sub_path is not None:
                self._remove_eid_lookup_subtree(old_node)
                self.statute.supplements[idx] = self._replace_descendant_at_path(root, sub_path, new_node)
                self._clear_eid_lookup_index()
                if structure_changed:
                    self._note_structure_mutation()
                self._record_replace_node_mutation_event(old_path=old_path, new_node=new_node)
                return True
        return False

    def _remove_node(self, node: UKMutableNode, parent: Optional[UKMutableNode], idx: Optional[int]) -> bool:
        removed_path = self._tree_path_for_mutable_node(node) if self.mutation_events_out is not None else None
        if parent is not None and idx is not None:
            self._remove_eid_lookup_subtree(node)
            parent.children.pop(idx)
            self._note_structure_mutation()
            self._record_remove_node_mutation_event(removed_path=removed_path)
            return True
        for s_idx, root in enumerate(self.statute.supplements):
            if root is node:
                self._remove_eid_lookup_subtree(node)
                self.statute.supplements.pop(s_idx)
                self._note_structure_mutation()
                self._record_remove_node_mutation_event(removed_path=removed_path)
                return True
        return False

    def _find_parent_tuple_for_node(
        self,
        target_node: UKMutableNode,
    ) -> ParentIndexEntry:
        def _walk(parent: UKMutableNode) -> ParentIndexEntry:
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

    def _insert_supplement_sorted(self, new_node: UKMutableNode) -> bool:
        if not uk_insert_node_sorted(self.statute.supplements, new_node):
            return False
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
        top_node: Optional[UKMutableNode] = None
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
                    payload=top_node.to_irnode(),
                    source=op.source,
                    group_id=op.group_id,
                )
            )

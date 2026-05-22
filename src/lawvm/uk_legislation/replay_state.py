"""UK replay executor state mutation and snapshot helpers."""

from __future__ import annotations

from typing import Any, Optional, cast

from lawvm.core.ir import IRNode, LegalAddress, LegalOperation
from lawvm.core.semantic_types import StructuralAction
from lawvm.uk_legislation.addressing import _action_name
from lawvm.uk_legislation.mutable_ir import UKMutableNode, UKMutableStatute
from lawvm.uk_legislation.ordering import _label_sort_key


class UKReplayStateMixin:
    statute: UKMutableStatute
    lo_ops_out: Optional[list[LegalOperation]]
    _repealed_target_prefixes: set[str]
    _structure_mutation_serial: int
    _eid_lookup_index: Optional[
        dict[str, tuple[UKMutableNode, Optional[UKMutableNode], Optional[int]]]
    ]
    _eid_lookup_ambiguous: set[str]
    _eid_search_cache: dict[
        tuple[str, bool],
        tuple[int, Optional[UKMutableNode], Optional[UKMutableNode], Optional[int]],
    ]
    _target_lookup_cache: dict[
        tuple[tuple[tuple[str, Optional[str]], ...], bool, bool],
        tuple[int, Optional[UKMutableNode], Optional[UKMutableNode], Optional[int]],
    ]
    _recursive_match_cache: dict[
        tuple[int, str, str],
        tuple[int, Optional[UKMutableNode], Optional[UKMutableNode], Optional[int]],
    ]

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

    def _clear_eid_lookup_index(self) -> None:
        self._eid_lookup_index = None
        self._eid_lookup_ambiguous = set()
        self._eid_search_cache.clear()

    def _cached_eid_search_lookup(
        self,
        eid: str,
        *,
        allow_sequence_match: bool,
    ) -> tuple[Optional[UKMutableNode], Optional[UKMutableNode], Optional[int]] | None:
        cached = self._eid_search_cache.get((eid, bool(allow_sequence_match)))
        if cached is None:
            return None
        serial, node, parent, idx = cached
        if serial != self._structure_mutation_serial:
            self._eid_search_cache.pop((eid, bool(allow_sequence_match)), None)
            return None
        if node is None:
            return None, None, None
        if parent is not None:
            if idx is not None and 0 <= idx < len(parent.children) and parent.children[idx] is node:
                return node, parent, idx
            try:
                current_idx = parent.children.index(node)
            except ValueError:
                self._eid_search_cache.pop((eid, bool(allow_sequence_match)), None)
                return None
            self._eid_search_cache[(eid, bool(allow_sequence_match))] = (
                self._structure_mutation_serial,
                node,
                parent,
                current_idx,
            )
            return node, parent, current_idx
        if idx is not None and 0 <= idx < len(self.statute.supplements) and self.statute.supplements[idx] is node:
            return node, None, idx
        if self.statute.body is node:
            return node, None, None
        try:
            current_idx = self.statute.supplements.index(node)
        except ValueError:
            self._eid_search_cache.pop((eid, bool(allow_sequence_match)), None)
            return None
        self._eid_search_cache[(eid, bool(allow_sequence_match))] = (
            self._structure_mutation_serial,
            node,
            None,
            current_idx,
        )
        return node, None, current_idx

    def _store_eid_search_cache(
        self,
        eid: str,
        *,
        allow_sequence_match: bool,
        result: tuple[Optional[UKMutableNode], Optional[UKMutableNode], Optional[int]],
    ) -> None:
        node, parent, idx = result
        self._eid_search_cache[(eid, bool(allow_sequence_match))] = (
            self._structure_mutation_serial,
            node,
            parent,
            idx,
        )

    def _target_lookup_cache_key(
        self,
        target: LegalAddress,
        *,
        allow_compound_subsection_alias: bool,
        allow_recursive_match: bool,
    ) -> tuple[tuple[tuple[str, Optional[str]], ...], bool, bool]:
        return (
            tuple((str(kind), label) for kind, label in target.path),
            bool(allow_compound_subsection_alias),
            bool(allow_recursive_match),
        )

    def _cached_target_lookup(
        self,
        key: tuple[tuple[tuple[str, Optional[str]], ...], bool, bool],
    ) -> tuple[Optional[UKMutableNode], Optional[UKMutableNode], Optional[int]] | None:
        cached = self._target_lookup_cache.get(key)
        if cached is None:
            return None
        serial, node, parent, idx = cached
        if serial != self._structure_mutation_serial:
            self._target_lookup_cache.pop(key, None)
            return None
        if node is None:
            return None, None, None
        if parent is not None:
            if idx is not None and 0 <= idx < len(parent.children) and parent.children[idx] is node:
                return node, parent, idx
            try:
                current_idx = parent.children.index(node)
            except ValueError:
                self._target_lookup_cache.pop(key, None)
                return None
            self._target_lookup_cache[key] = (
                self._structure_mutation_serial,
                node,
                parent,
                current_idx,
            )
            return node, parent, current_idx
        if idx is not None and 0 <= idx < len(self.statute.supplements) and self.statute.supplements[idx] is node:
            return node, None, idx
        if self.statute.body is node:
            return node, None, None
        try:
            current_idx = self.statute.supplements.index(node)
        except ValueError:
            self._target_lookup_cache.pop(key, None)
            return None
        self._target_lookup_cache[key] = (
            self._structure_mutation_serial,
            node,
            None,
            current_idx,
        )
        return node, None, current_idx

    def _store_target_lookup_cache(
        self,
        key: tuple[tuple[tuple[str, Optional[str]], ...], bool, bool],
        result: tuple[Optional[UKMutableNode], Optional[UKMutableNode], Optional[int]],
    ) -> None:
        node, parent, idx = result
        self._target_lookup_cache[key] = (
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
    ) -> tuple[Optional[UKMutableNode], Optional[UKMutableNode], Optional[int]] | None:
        if key not in self._recursive_match_cache:
            return None
        cached = self._recursive_match_cache[key]
        serial, node, parent, idx = cached
        if serial != self._structure_mutation_serial:
            self._recursive_match_cache.pop(key, None)
            return None
        if node is None:
            return None, None, None
        if parent is None:
            self._recursive_match_cache.pop(key, None)
            return None
        if idx is not None and 0 <= idx < len(parent.children) and parent.children[idx] is node:
            return node, parent, idx
        try:
            current_idx = parent.children.index(node)
        except ValueError:
            self._recursive_match_cache.pop(key, None)
            return None
        self._recursive_match_cache[key] = (
            self._structure_mutation_serial,
            node,
            parent,
            current_idx,
        )
        return node, parent, current_idx

    def _store_recursive_match_cache(
        self,
        key: tuple[int, str, str],
        result: tuple[Optional[UKMutableNode], Optional[UKMutableNode], Optional[int]],
    ) -> None:
        node, parent, idx = result
        self._recursive_match_cache[key] = (
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
        index: dict[str, tuple[UKMutableNode, Optional[UKMutableNode], Optional[int]]],
        ambiguous: set[str],
    ) -> None:
        for eid in self._node_eid_values(node):
            if eid in ambiguous:
                continue
            if eid in index and index[eid][0] is not node:
                index.pop(eid, None)
                ambiguous.add(eid)
                continue
            index[eid] = (node, parent, idx)
        for child_idx, child in enumerate(node.children):
            self._index_eid_subtree(child, node, child_idx, index, ambiguous)

    def _ensure_eid_lookup_index(
        self,
    ) -> dict[str, tuple[UKMutableNode, Optional[UKMutableNode], Optional[int]]]:
        if self._eid_lookup_index is not None:
            return self._eid_lookup_index
        index: dict[str, tuple[UKMutableNode, Optional[UKMutableNode], Optional[int]]] = {}
        ambiguous: set[str] = set()
        for child_idx, child in enumerate(self.statute.body.children):
            self._index_eid_subtree(child, self.statute.body, child_idx, index, ambiguous)
        for supplement_idx, supplement in enumerate(self.statute.supplements):
            self._index_eid_subtree(supplement, None, supplement_idx, index, ambiguous)
        self._eid_lookup_index = index
        self._eid_lookup_ambiguous = ambiguous
        return index

    def _cached_exact_eid_lookup(
        self,
        eid: str,
    ) -> tuple[Optional[UKMutableNode], Optional[UKMutableNode], Optional[int]]:
        if not eid or eid in self._eid_lookup_ambiguous:
            return None, None, None
        entry = self._ensure_eid_lookup_index().get(eid)
        if entry is None:
            return None, None, None
        node, parent, idx = entry
        if parent is not None:
            if idx is not None and 0 <= idx < len(parent.children) and parent.children[idx] is node:
                return node, parent, idx
            try:
                current_idx = parent.children.index(node)
            except ValueError:
                self._ensure_eid_lookup_index().pop(eid, None)
                return None, None, None
            self._ensure_eid_lookup_index()[eid] = (node, parent, current_idx)
            return node, parent, current_idx
        if idx is not None and 0 <= idx < len(self.statute.supplements) and self.statute.supplements[idx] is node:
            return node, None, idx
        try:
            current_idx = self.statute.supplements.index(node)
        except ValueError:
            self._ensure_eid_lookup_index().pop(eid, None)
            return None, None, None
        self._ensure_eid_lookup_index()[eid] = (node, None, current_idx)
        return node, None, current_idx

    def _remove_eid_lookup_subtree(self, node: UKMutableNode) -> None:
        if self._eid_lookup_index is None:
            return
        stack = [node]
        while stack:
            current = stack.pop()
            for eid in self._node_eid_values(current):
                entry = self._eid_lookup_index.get(eid)
                if entry is not None and entry[0] is current:
                    self._eid_lookup_index.pop(eid, None)
            stack.extend(current.children)

    def _add_eid_lookup_subtree(
        self,
        node: UKMutableNode,
        parent: Optional[UKMutableNode],
        idx: Optional[int],
    ) -> None:
        if self._eid_lookup_index is None:
            return
        self._index_eid_subtree(
            node,
            parent,
            idx,
            self._eid_lookup_index,
            self._eid_lookup_ambiguous,
        )

    def _record_child_inserted(self, parent: UKMutableNode, node: UKMutableNode) -> None:
        try:
            idx = parent.children.index(node)
        except ValueError:
            idx = None
        self._add_eid_lookup_subtree(node, parent, idx)
        self._note_structure_mutation()

    def _record_supplement_inserted(self, node: UKMutableNode) -> None:
        try:
            idx = self.statute.supplements.index(node)
        except ValueError:
            idx = None
        self._add_eid_lookup_subtree(node, None, idx)
        self._note_structure_mutation()

    def _child_shape(self, node: UKMutableNode) -> tuple[tuple[object, Optional[str]], ...]:
        return tuple((child.kind, child.label) for child in node.children)

    def _replace_statute(
        self,
        *,
        body: Optional[UKMutableNode] = None,
        supplements: Optional[list[UKMutableNode]] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        """Replace the UK-local mutable runtime state."""
        if body is not None:
            self.statute.body = body
            self._clear_eid_lookup_index()
            self._note_structure_mutation()
        if supplements is not None:
            self.statute.supplements = list(supplements)
            self._clear_eid_lookup_index()
            self._note_structure_mutation()
        if metadata is not None:
            self.statute.metadata = dict(metadata)

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
        root.children[idx] = self._replace_descendant_at_path(root.children[idx], path[1:], new_node)
        return root

    def _parent_tuple_for_path(
        self,
        root: UKMutableNode,
        path: tuple[int, ...],
    ) -> tuple[Optional[UKMutableNode], Optional[int]]:
        if not path:
            return None, None
        parent = root
        for child_idx in path[:-1]:
            parent = parent.children[child_idx]
        return parent, path[-1]

    def _replace_node_in_statute(self, old_node: UKMutableNode, new_node: UKMutableNode) -> bool:
        structure_changed = self._child_shape(old_node) != self._child_shape(new_node)
        if self.statute.body is old_node:
            self._remove_eid_lookup_subtree(old_node)
            self.statute.body = new_node
            self._add_eid_lookup_subtree(new_node, None, None)
            self._clear_eid_lookup_index()
            if structure_changed:
                self._note_structure_mutation()
            return True
        body_path = self._find_path_to_node(self.statute.body, old_node)
        if body_path is not None:
            parent, idx = self._parent_tuple_for_path(self.statute.body, body_path)
            self._remove_eid_lookup_subtree(old_node)
            self._replace_descendant_at_path(self.statute.body, body_path, new_node)
            self._add_eid_lookup_subtree(new_node, parent, idx)
            if structure_changed:
                self._note_structure_mutation()
            return True
        for idx, root in enumerate(self.statute.supplements):
            if root is old_node:
                self._remove_eid_lookup_subtree(old_node)
                self.statute.supplements[idx] = new_node
                self._add_eid_lookup_subtree(new_node, None, idx)
                if structure_changed:
                    self._note_structure_mutation()
                return True
            sub_path = self._find_path_to_node(root, old_node)
            if sub_path is not None:
                parent, child_idx = self._parent_tuple_for_path(root, sub_path)
                self._remove_eid_lookup_subtree(old_node)
                self._replace_descendant_at_path(root, sub_path, new_node)
                self._add_eid_lookup_subtree(new_node, parent, child_idx)
                if structure_changed:
                    self._note_structure_mutation()
                return True
        return False

    def _remove_node(self, node: UKMutableNode, parent: Optional[UKMutableNode], idx: Optional[int]) -> bool:
        if parent is not None and idx is not None:
            self._remove_eid_lookup_subtree(node)
            parent.children.pop(idx)
            self._note_structure_mutation()
            return True
        for s_idx, root in enumerate(self.statute.supplements):
            if root is node:
                self._remove_eid_lookup_subtree(node)
                self.statute.supplements.pop(s_idx)
                self._note_structure_mutation()
                return True
        return False

    def _find_parent_tuple_for_node(
        self,
        target_node: UKMutableNode,
    ) -> tuple[Optional[UKMutableNode], Optional[int]]:
        def _walk(parent: UKMutableNode) -> tuple[Optional[UKMutableNode], Optional[int]]:
            for child_idx, child in enumerate(parent.children):
                if child is target_node:
                    return parent, child_idx
                if not child.children:
                    continue
                found_parent, found_idx = _walk(child)
                if found_parent is not None:
                    return found_parent, found_idx
            return None, None

        if self.statute.body is target_node:
            return None, None
        found_parent, found_idx = _walk(self.statute.body)
        if found_parent is not None:
            return found_parent, found_idx
        for supplement in self.statute.supplements:
            if supplement is target_node:
                return None, None
            found_parent, found_idx = _walk(supplement)
            if found_parent is not None:
                return found_parent, found_idx
        return None, None

    def _insert_supplement_sorted(self, new_node: UKMutableNode) -> bool:
        from lawvm.uk_legislation.canonicalize import uk_insert_into_children

        uk_insert_into_children(
            cast(list[IRNode], self.statute.supplements),
            cast(IRNode, new_node),
            label_sort_key=_label_sort_key,
        )
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

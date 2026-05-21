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
        if supplements is not None:
            self.statute.supplements = list(supplements)
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
            found = self._find_path_to_node(child, target_node, path + (i,))
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

    def _replace_node_in_statute(self, old_node: UKMutableNode, new_node: UKMutableNode) -> bool:
        if self.statute.body is old_node:
            self.statute.body = new_node
            return True
        body_path = self._find_path_to_node(self.statute.body, old_node)
        if body_path is not None:
            self._replace_descendant_at_path(self.statute.body, body_path, new_node)
            return True
        for idx, root in enumerate(self.statute.supplements):
            if root is old_node:
                self.statute.supplements[idx] = new_node
                return True
            sub_path = self._find_path_to_node(root, old_node)
            if sub_path is not None:
                self._replace_descendant_at_path(root, sub_path, new_node)
                return True
        return False

    def _remove_node(self, node: UKMutableNode, parent: Optional[UKMutableNode], idx: Optional[int]) -> bool:
        if parent is not None and idx is not None:
            parent.children.pop(idx)
            return True
        for s_idx, root in enumerate(self.statute.supplements):
            if root is node:
                self.statute.supplements.pop(s_idx)
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


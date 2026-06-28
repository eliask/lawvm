"""UK replay renumber operation helpers."""

from __future__ import annotations

from dataclasses import replace as dc_replace
from typing import Protocol, cast

from lawvm.core.ir import IRNode, IRNodeKind, LegalAddress, LegalOperation
from lawvm.core.ir_helpers import _kind_str
from lawvm.core.mutation_boundary import TreePath
from lawvm.core.tree_ops import _NESTING_ORDER
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.uk_legislation.addressing import _addr_leaf_kind, _addr_leaf_label
from lawvm.uk_legislation.canonicalize import canonicalize_uk_address
from lawvm.uk_legislation.metadata_rewrites import (
    _is_uk_parent_sibling_promotion_renumber_shape,
    _renumbered_descendant_text,
)
from lawvm.uk_legislation.apply_rebuild import uk_insert_node_sorted_cow, uk_ir_node_kind
from lawvm.uk_legislation.replay_records import (
    _append_uk_replay_adjudication,
    uk_replay_blocking_action_target_detail,
)
from lawvm.uk_legislation.replay_state import NodeLookupResult
from lawvm.uk_legislation.uk_grafter import _clean_num

# Rule ID for the descendant-relocation renumber shape (§1.6 migration/lineage).
# Emitted when a provision is rewritten into a parent-with-child shape and the
# lineage event carries the old→new path pair for PIT materialization.
UK_REPLAY_DESCENDANT_RENUMBER_RULE_ID = "uk_replay_descendant_renumber_provision"


class _RenumberReplaySelf(Protocol):
    adjudications_out: list[CompileAdjudication]

    def _record_invariant_violations(self, op: LegalOperation) -> None: ...

    def _emit_top_section_snapshot(self, op: LegalOperation) -> None: ...

    def _find_node_by_target(
        self,
        target: LegalAddress,
        *,
        allow_compound_subsection_alias: bool = False,
        allow_recursive_match: bool = True,
        target_resolution_op: LegalOperation | None = None,
    ) -> NodeLookupResult: ...

    def _log(self, message: str) -> None: ...

    def _derive_target_eid(self, addr: LegalAddress) -> str: ...

    def _replace_node_in_statute(self, old_node: IRNode, new_node: IRNode) -> bool: ...

    def _replace_ancestor_chain(
        self,
        old_node: IRNode,
        new_node: IRNode,
    ) -> bool: ...

    def _remove_eid_lookup_subtree(self, node: IRNode) -> None: ...

    def _add_eid_lookup_subtree(
        self,
        node: IRNode,
        parent: IRNode | None,
        idx: int | None,
    ) -> None: ...

    def _note_structure_mutation(self) -> None: ...

    def _tree_path_for_mutable_node(self, node: IRNode) -> TreePath | None: ...

    def _record_renumber_node_mutation_event(
        self,
        *,
        old_path: TreePath | None,
        new_node: IRNode,
        helper: str,
    ) -> None: ...

    def _record_descendant_renumber_mutation_event(
        self,
        *,
        old_path: TreePath | None,
        new_child_path: TreePath,
        helper: str,
    ) -> None: ...

    def _record_promoted_child_renumber_mutation_event(
        self,
        *,
        old_path: TreePath | None,
        new_node: IRNode,
        helper: str,
    ) -> None: ...


def _renumber_replay_self(replay: object) -> _RenumberReplaySelf:
    return cast(_RenumberReplaySelf, replay)


class UKReplayRenumberApplyMixin:

    def _apply_renumber_op(self, op: LegalOperation, target: LegalAddress) -> None:
        replay = _renumber_replay_self(self)
        if self._apply_same_provision_descendant_renumber(op):
            replay._record_invariant_violations(op)
            replay._emit_top_section_snapshot(op)
            return
        if self._apply_same_parent_sibling_renumber(op):
            replay._record_invariant_violations(op)
            replay._emit_top_section_snapshot(op)
            return
        if self._apply_parent_sibling_promotion_renumber(op):
            replay._record_invariant_violations(op)
            replay._emit_top_section_snapshot(op)
            return
        source_target = canonicalize_uk_address(op.target)
        destination = canonicalize_uk_address(op.destination) if op.destination is not None else None
        if destination is not None and self._renumber_shape_supported(source_target, destination):
            source_node, _source_parent, _source_idx = replay._find_node_by_target(source_target)
            if source_node is None:
                _append_uk_replay_adjudication(
                    replay.adjudications_out,
                    kind="uk_replay_missing_source_target_gap",
                    message="UK replay skipped renumber: source target is absent from replay state.",
                    op=op,
                    detail=uk_replay_blocking_action_target_detail(
                        op,
                        target,
                        destination=str(destination),
                        family="source_shape_gap",
                        reason_code="renumber_source_target_absent",
                    ),
                )
                return
            destination_node, _destination_parent, _destination_idx = replay._find_node_by_target(destination)
            if destination_node is not None:
                _append_uk_replay_adjudication(
                    replay.adjudications_out,
                    kind="uk_replay_existing_target_conflict_gap",
                    message="UK replay skipped renumber: destination target already exists.",
                    op=op,
                    detail=uk_replay_blocking_action_target_detail(
                        op,
                        target,
                        destination=str(destination),
                        family="source_shape_gap",
                        reason_code="renumber_destination_target_present",
                    ),
                )
                return
        replay._log(f"  EXECUTOR: unsupported renumber shape — skipping {op.op_id}")
        _append_uk_replay_adjudication(
            replay.adjudications_out,
            kind="uk_replay_unsupported_action",
            message="UK replay skipped unsupported action.",
            op=op,
            detail=uk_replay_blocking_action_target_detail(
                op,
                target,
                destination=str(op.destination) if op.destination is not None else "",
            ),
        )

    def _renumber_shape_supported(
        self,
        source_target: LegalAddress,
        destination: LegalAddress,
    ) -> bool:
        if len(destination.path) == len(source_target.path) + 1 and destination.path[:-1] == source_target.path:
            return True
        return (
            len(destination.path) == len(source_target.path)
            and destination.path[:-1] == source_target.path[:-1]
            and _addr_leaf_kind(destination) == _addr_leaf_kind(source_target)
        ) or _is_uk_parent_sibling_promotion_renumber_shape(
            source_target,
            destination,
        )

    def _apply_same_provision_descendant_renumber(self, op: LegalOperation) -> bool:
        replay = _renumber_replay_self(self)
        source_target = canonicalize_uk_address(op.target)
        destination = canonicalize_uk_address(op.destination) if op.destination is not None else None
        if destination is None:
            return False
        if len(destination.path) != len(source_target.path) + 1 or destination.path[:-1] != source_target.path:
            return False

        source_node, _source_parent, _source_idx = replay._find_node_by_target(source_target)
        if source_node is None:
            return False
        destination_kind = _addr_leaf_kind(destination) or ""
        destination_label = _addr_leaf_label(destination)
        # Descendant renumbering creates the destination as an immediate child of
        # the source provision.  Do not use broad recursive target lookup here:
        # schedule item "i" may normalize like subparagraph "1", but it is not a
        # destination collision for "paragraph 12 becomes sub-paragraph (1)".
        for child in source_node.children:
            child_kind = str(child.kind or "").lower()
            child_label = _clean_num(str(child.label or ""))
            if child_kind == destination_kind and child_label == _clean_num(destination_label or ""):
                if len(source_node.children) > 1:
                    continue
                return False

        if not destination_kind:
            return False

        # Capture the old path before replacing; _replace_node_in_statute clears
        # or updates the eId lookup index, so the path may not be recoverable
        # from the mutable node afterward.
        old_path = replay._tree_path_for_mutable_node(source_node)
        dest_admitted = _NESTING_ORDER.get(destination_kind, set())
        retained_children = [
            child
            for child in source_node.children
            if child.kind in (IRNodeKind.HEADING, IRNodeKind.NUM)
            or _kind_str(child.kind) not in dest_admitted
        ]
        moved_children = [
            child
            for child in source_node.children
            if child.kind not in (IRNodeKind.HEADING, IRNodeKind.NUM)
            and _kind_str(child.kind) in dest_admitted
        ]
        child = IRNode(
            kind=uk_ir_node_kind(destination_kind),
            label=destination_label,
            text=_renumbered_descendant_text(
                source_node.text or "",
                source_label=source_node.label,
                destination_label=destination_label,
            ),
            attrs={"eId": replay._derive_target_eid(destination)},
            children=tuple(moved_children),
        )
        replacement_children = list(retained_children)
        replacement_children.append(child)
        replacement = IRNode(
            kind=source_node.kind,
            label=source_node.label,
            text="",
            attrs=dict(source_node.attrs),
            children=tuple(replacement_children),
        )
        replaced = replay._replace_node_in_statute(source_node, replacement)
        if replaced and old_path is not None:
            # Emit a renumber-specific MutationEvent that carries the lineage:
            # old_path (e.g. paragraph:12) → new_child_path (e.g. paragraph:12/
            # sub-paragraph:(1)).  The generic replace event already records the
            # mechanical in-place rewrite; this event exists for §1.6 provenance.
            new_child_path = old_path + ((_kind_str(child.kind), child.label or ""),)
            replay._record_descendant_renumber_mutation_event(
                old_path=old_path,
                new_child_path=new_child_path,
                helper="_apply_same_provision_descendant_renumber",
            )
        return replaced

    def _apply_same_parent_sibling_renumber(self, op: LegalOperation) -> bool:
        replay = _renumber_replay_self(self)
        source_target = canonicalize_uk_address(op.target)
        destination = canonicalize_uk_address(op.destination) if op.destination is not None else None
        if destination is None:
            return False
        if (
            len(destination.path) != len(source_target.path)
            or destination.path[:-1] != source_target.path[:-1]
            or _addr_leaf_kind(destination) != _addr_leaf_kind(source_target)
        ):
            return False

        source_node, source_parent, source_idx = replay._find_node_by_target(source_target)
        if source_node is None or source_parent is None or source_idx is None:
            return False
        destination_node, _destination_parent, _destination_idx = replay._find_node_by_target(destination)
        if destination_node is not None:
            return False

        destination_label = _addr_leaf_label(destination)
        destination_label_clean = _clean_num(destination_label or "")
        destination_kind = uk_ir_node_kind(source_node.kind)
        if any(
            child is not source_node
            and uk_ir_node_kind(child.kind) == destination_kind
            and _clean_num(child.label or "") == destination_label_clean
            for child in source_parent.children
        ):
            _append_uk_replay_adjudication(
                replay.adjudications_out,
                kind="uk_replay_existing_target_conflict_gap",
                message="UK replay skipped renumber: destination sibling already exists.",
                op=op,
                detail=uk_replay_blocking_action_target_detail(
                    op,
                    source_target,
                    destination=str(destination),
                    family="source_shape_gap",
                    reason_code="renumber_destination_sibling_collision",
                ),
            )
            return True
        moved = dc_replace(
            source_node,
            label=destination_label,
            text=_renumbered_descendant_text(
                source_node.text or "",
                source_label=source_node.label,
                destination_label=destination_label,
            ),
            attrs={**dict(source_node.attrs), "eId": replay._derive_target_eid(destination)},
        )
        old_path = replay._tree_path_for_mutable_node(source_node)
        # PR3 (audit XJUR-02 / AGENTS.md §2.3): combine the remove + sorted-insert
        # into a single copy-on-write rebuild of ``source_parent`` so no
        # in-place ``source_parent.children.pop`` / ``children.insert`` ever
        # runs against the live tree; the rebuilt parent is then threaded up to
        # the statute root via ``_replace_ancestor_chain``.
        children_without_source = [
            child
            for index, child in enumerate(source_parent.children)
            if index != source_idx
        ]
        new_parent_children, moved_idx = uk_insert_node_sorted_cow(
            children_without_source, moved
        )
        new_source_parent = dc_replace(source_parent, children=new_parent_children)
        if not replay._replace_ancestor_chain(source_parent, new_source_parent):
            return False
        if moved_idx is None:
            # ``uk_insert_node_sorted_cow`` rejected the insert (duplicate
            # kind+label after removing the source). This is unreachable here
            # because the destination-sibling-collision guard above already
            # filtered this branch, but we record the deduced index by identity
            # so downstream bookkeeping does not dereference a stale reference.
            try:
                moved_idx = new_parent_children.index(moved)
            except ValueError:
                moved_idx = None
        replay._note_structure_mutation()
        replay._record_renumber_node_mutation_event(
            old_path=old_path,
            new_node=moved,
            helper="_apply_same_parent_sibling_renumber",
        )
        return True

    def _apply_parent_sibling_promotion_renumber(self, op: LegalOperation) -> bool:
        replay = _renumber_replay_self(self)
        source_target = canonicalize_uk_address(op.target)
        destination = canonicalize_uk_address(op.destination) if op.destination is not None else None
        if destination is None:
            return False
        if not _is_uk_parent_sibling_promotion_renumber_shape(source_target, destination):
            return False

        source_node, source_parent, source_idx = replay._find_node_by_target(source_target)
        if source_node is None or source_parent is None or source_idx is None:
            return False
        destination_node, _destination_parent, _destination_idx = replay._find_node_by_target(destination)
        if destination_node is not None:
            return False
        grandparent_target = LegalAddress(path=source_target.path[:-2])
        grandparent_node, _grandparent_parent, _grandparent_idx = replay._find_node_by_target(
            grandparent_target
        )
        if grandparent_node is None or source_parent not in grandparent_node.children:
            return False

        destination_label = _addr_leaf_label(destination)
        destination_kind = _addr_leaf_kind(destination) or ""
        if not destination_kind:
            return False

        moved = dc_replace(
            source_node,
            kind=uk_ir_node_kind(destination_kind),
            label=destination_label,
            text=_renumbered_descendant_text(
                source_node.text or "",
                source_label=source_node.label,
                destination_label=destination_label,
            ),
            attrs={**dict(source_node.attrs), "eId": replay._derive_target_eid(destination)},
        )
        old_path = replay._tree_path_for_mutable_node(source_node)
        # PR3 (audit XJUR-02 / AGENTS.md §2.3): copy-on-write remove + insert.
        # Rebuild ``source_parent`` with the source node excised, then rebuild
        # ``grandparent_node`` so its children include ``new_source_parent``
        # (in place of the original ``source_parent``) and ``moved`` at the
        # sorted position, then thread ``new_grandparent`` up to the statute
        # root via ``_replace_ancestor_chain``. No in-place
        # ``source_parent.children.pop`` / ``grandparent_node.children.insert``
        # occurs against the live tree.
        children_without_source = [
            child
            for index, child in enumerate(source_parent.children)
            if index != source_idx
        ]
        new_source_parent = dc_replace(source_parent, children=children_without_source)
        grandparent_children_intermediate = [
            new_source_parent if child is source_parent else child
            for child in grandparent_node.children
        ]
        new_grandparent_children, moved_idx = uk_insert_node_sorted_cow(
            grandparent_children_intermediate, moved
        )
        if moved_idx is None:
            try:
                moved_idx = new_grandparent_children.index(moved)
            except ValueError:
                moved_idx = None
        new_grandparent = dc_replace(grandparent_node, children=new_grandparent_children)
        if not replay._replace_ancestor_chain(grandparent_node, new_grandparent):
            return False
        replay._note_structure_mutation()
        replay._record_promoted_child_renumber_mutation_event(
            old_path=old_path,
            new_node=moved,
            helper="_apply_parent_sibling_promotion_renumber",
        )
        return True

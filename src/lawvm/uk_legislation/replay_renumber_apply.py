"""UK replay renumber operation helpers."""

from __future__ import annotations

from dataclasses import replace as dc_replace
from typing import Protocol, cast

from lawvm.core.ir import LegalAddress, LegalOperation
from lawvm.core.mutation_boundary import TreePath
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.uk_legislation.addressing import _addr_leaf_kind, _addr_leaf_label
from lawvm.uk_legislation.canonicalize import canonicalize_uk_address
from lawvm.uk_legislation.metadata_rewrites import _renumbered_descendant_text
from lawvm.uk_legislation.mutable_ir import UKMutableNode, uk_insert_child_sorted, uk_ir_node_kind
from lawvm.uk_legislation.replay_records import (
    _append_uk_replay_adjudication,
    uk_replay_blocking_action_target_detail,
)
from lawvm.uk_legislation.replay_state import NodeLookupResult
from lawvm.uk_legislation.uk_grafter import _clean_num


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

    def _replace_node_in_statute(self, old_node: UKMutableNode, new_node: UKMutableNode) -> bool: ...

    def _remove_eid_lookup_subtree(self, node: UKMutableNode) -> None: ...

    def _record_child_inserted(self, parent: UKMutableNode, node: UKMutableNode) -> None: ...

    def _tree_path_for_mutable_node(self, node: UKMutableNode) -> TreePath | None: ...

    def _record_renumber_node_mutation_event(
        self,
        *,
        old_path: TreePath | None,
        new_node: UKMutableNode,
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

        child = UKMutableNode(
            kind=uk_ir_node_kind(destination_kind),
            label=destination_label,
            text=_renumbered_descendant_text(
                source_node.text or "",
                source_label=source_node.label,
                destination_label=destination_label,
            ),
            attrs={"eId": replay._derive_target_eid(destination)},
            children=list(source_node.children),
        )
        replacement = UKMutableNode(
            kind=source_node.kind,
            label=source_node.label,
            text="",
            attrs=dict(source_node.attrs),
            children=[child],
        )
        return replay._replace_node_in_statute(source_node, replacement)

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
        replay._remove_eid_lookup_subtree(source_node)
        source_parent.children.pop(source_idx)
        uk_insert_child_sorted(source_parent, moved)
        replay._record_renumber_node_mutation_event(
            old_path=old_path,
            new_node=moved,
            helper="_apply_same_parent_sibling_renumber",
        )
        return True

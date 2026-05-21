"""UK replay renumber operation helpers."""

from __future__ import annotations

from dataclasses import replace as dc_replace

from lawvm.core.ir import LegalAddress, LegalOperation
from lawvm.core.semantic_types import IRNodeKind
from lawvm.uk_legislation.addressing import _addr_leaf_kind, _addr_leaf_label
from lawvm.uk_legislation.canonicalize import canonicalize_uk_address
from lawvm.uk_legislation.metadata_rewrites import _renumbered_descendant_text
from lawvm.uk_legislation.mutable_ir import UKMutableNode, uk_insert_child_sorted
from lawvm.uk_legislation.replay_records import (
    _append_uk_replay_adjudication,
    uk_replay_blocking_action_target_detail,
)
from lawvm.uk_legislation.uk_grafter import _clean_num


class UKReplayRenumberApplyMixin:

    def _apply_renumber_op(self, op: LegalOperation, target: LegalAddress) -> None:
        if self._apply_same_provision_descendant_renumber(op):
            self._record_invariant_violations(op)
            self._emit_top_section_snapshot(op)
            return
        if self._apply_same_parent_sibling_renumber(op):
            self._record_invariant_violations(op)
            self._emit_top_section_snapshot(op)
            return
        self._log(f"  EXECUTOR: unsupported renumber shape — skipping {op.op_id}")
        _append_uk_replay_adjudication(
            self.adjudications_out,
            kind="uk_replay_unsupported_action",
            message="UK replay skipped unsupported action.",
            op=op,
            detail=uk_replay_blocking_action_target_detail(
                op,
                target,
                destination=str(op.destination) if op.destination is not None else "",
            ),
        )

    def _apply_same_provision_descendant_renumber(self, op: LegalOperation) -> bool:
        source_target = canonicalize_uk_address(op.target)
        destination = canonicalize_uk_address(op.destination) if op.destination is not None else None
        if destination is None:
            return False
        if len(destination.path) != len(source_target.path) + 1 or destination.path[:-1] != source_target.path:
            return False

        source_node, _source_parent, _source_idx = self._find_node_by_target(source_target)
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
                return False

        if not destination_kind:
            return False

        child = UKMutableNode(
            kind=IRNodeKind(destination_kind),
            label=destination_label,
            text=_renumbered_descendant_text(
                source_node.text or "",
                source_label=source_node.label,
                destination_label=destination_label,
            ),
            attrs={"eId": self._derive_target_eid(destination)},
            children=list(source_node.children),
        )
        replacement = UKMutableNode(
            kind=source_node.kind,
            label=source_node.label,
            text="",
            attrs=dict(source_node.attrs),
            children=[child],
        )
        return self._replace_node_in_statute(source_node, replacement)

    def _apply_same_parent_sibling_renumber(self, op: LegalOperation) -> bool:
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

        source_node, source_parent, source_idx = self._find_node_by_target(source_target)
        if source_node is None or source_parent is None or source_idx is None:
            return False
        destination_node, _destination_parent, _destination_idx = self._find_node_by_target(destination)
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
            attrs={**dict(source_node.attrs), "eId": self._derive_target_eid(destination)},
        )
        source_parent.children.pop(source_idx)
        uk_insert_child_sorted(source_parent, moved)
        return True

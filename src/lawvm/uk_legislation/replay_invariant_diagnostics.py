"""UK replay tree-invariant diagnostics.

This module owns post-apply invariant classification for the UK replay
executor. It records existing replay findings; it does not repair or mutate the
tree.
"""

from __future__ import annotations

from lawvm.core import tree_ops
from lawvm.core.ir import LegalAddress, LegalOperation
from lawvm.core.ir_helpers import _kind_str
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.uk_legislation.mutable_ir import UKMutableNode, UKMutableStatute
from lawvm.uk_legislation.uk_grafter import _clean_num
from lawvm.uk_legislation.replay_records import (
    _append_uk_replay_adjudication,
    uk_replay_action_target_detail,
)
from lawvm.uk_legislation.replay_target_gaps import (
    uk_chapter_order_shape_gap,
    uk_item_order_shape_gap,
    uk_paragraph_order_shape_gap,
    uk_part_order_shape_gap,
    uk_payload_container_shape_gap,
    uk_payload_shape_invariant_violations,
    uk_repeated_form_label_payload_shape_gap,
    uk_replace_payload_kind_mismatch_gap,
    uk_section_order_shape_gap,
    uk_source_anchored_order_observation,
    uk_subparagraph_order_shape_gap,
)

_ORDERED_INVARIANT_KINDS = frozenset(
    {
        "section",
        "chapter",
        "part",
        "division",
        "schedule",
        "appendix",
        "paragraph",
        "subparagraph",
        "item",
        "sentence",
    }
)


def _invariant_kind_str(kind: object) -> str:
    if isinstance(kind, str):
        return kind
    return _kind_str(kind)


def _invariant_detail(
    op: LegalOperation,
    scoped_violation: str,
    **extra: str,
) -> dict[str, object]:
    return uk_replay_action_target_detail(
        op,
        op.target,
        blocking=False,
        violation=scoped_violation,
        **extra,
    )


def _collect_duplicate_order_invariants(root: UKMutableNode, initial_path: str | None = None) -> list[str]:
    """Return the duplicate/order subset that UK replay diagnostics persist.

    ``tree_ops.check_invariants`` also checks nesting and normalized-label
    aliases, but this caller immediately filters those families out.  Replay
    invokes this after many individual mutations, so scanning only the families
    that can be emitted here keeps the diagnostic lane equivalent without
    paying for discarded checks.
    """
    violations: list[str] = []
    stack: list[tuple[UKMutableNode, str]] = [(root, initial_path or _kind_str(root.kind))]
    while stack:
        node, path = stack.pop()
        children = node.children
        if len(children) >= 2:
            seen: dict[tuple[str, str], int] = {}
            by_kind: dict[str, list[str]] = {}
            for child in children:
                child_kind = _invariant_kind_str(child.kind)
                if child.label:
                    label = str(child.label)
                    key = (child_kind, label)
                    seen[key] = seen.get(key, 0) + 1
                    if child_kind in _ORDERED_INVARIANT_KINDS:
                        by_kind.setdefault(child_kind, []).append(label)
            for (kind, label), count in seen.items():
                if count > 1:
                    violations.append(f"{path}: duplicate {kind}:{label} ({count} times)")
            for kind, labels in by_kind.items():
                keys = [tree_ops._default_sort_key(label) for label in labels]
                for index in range(len(keys) - 1):
                    if keys[index] > keys[index + 1]:
                        violations.append(
                            f"{path}: {kind} out of order: {labels[index]} > {labels[index + 1]}"
                        )
        for child in reversed(children):
            if not child.children:
                continue
            child_path = f"{path}/{_kind_str(child.kind)}:{child.label or '?'}"
            stack.append((child, child_path))
    return violations


class UKReplayInvariantDiagnosticsMixin:
    statute: UKMutableStatute
    adjudications_out: list[CompileAdjudication]
    _seen_invariant_violations: set[str]
    _structure_mutation_serial: int
    _last_invariant_structure_serial: int

    def _invariant_root_filter_for_op(self, op: LegalOperation) -> set[tuple[str, str]] | None:
        if str(op.target.special or "") == "whole_act":
            return None
        if not op.target.path:
            return None
        root_kind, root_label = op.target.path[0]
        if root_kind == "schedule":
            clean_label = _clean_num(str(root_label or ""))
            if not clean_label:
                return None
            return {("schedule", clean_label)}
        return {("body", "")}

    def _node_invariant_path(self, root: UKMutableNode, node: UKMutableNode, root_path: str) -> str:
        for child in root.children:
            if child is node:
                return f"{root_path}/{_kind_str(child.kind)}:{child.label or '?'}"
        path = self._find_path_to_node(root, node)
        if path is None:
            return root_path
        parts = [root_path]
        current = root
        for child_idx in path:
            current = current.children[child_idx]
            parts.append(f"{_kind_str(current.kind)}:{current.label or '?'}")
        return "/".join(parts)

    def _find_invariant_scope_node(
        self,
        address: LegalAddress,
    ) -> tuple[UKMutableNode | None, UKMutableNode | None]:
        node = None
        target_eid = self._derive_target_eid(address)
        if target_eid:
            node, _parent, _idx = self._find_node_and_parent_statute(
                target_eid,
                allow_sequence_match=False,
            )
            if node is not None and not self._eid_candidate_matches_target_leaf(
                node,
                address,
            ):
                node = None
        if node is None:
            node, _parent, _idx = self._find_node_by_target(
                address,
                allow_recursive_match=False,
            )
        if node is None:
            node, _parent, _idx = self._find_node_by_target(
                address,
                allow_recursive_match=True,
            )
        schedule_root = None
        if address.path and address.path[0][0] == "schedule":
            schedule_label = _clean_num(str(address.path[0][1] or ""))
            for schedule in self.statute.supplements:
                if _clean_num(str(schedule.label or "")) == schedule_label:
                    schedule_root = schedule
                    break
        return node, schedule_root

    def _invariant_parent_target_roots_for_op(
        self,
        op: LegalOperation,
    ) -> list[tuple[str, UKMutableNode, str, str]]:
        if len(op.target.path) <= 1:
            return []
        parent_address = LegalAddress(path=op.target.path[:-1], special=None)
        node, schedule_root = self._find_invariant_scope_node(parent_address)
        if node is None:
            return []
        if schedule_root is not None:
            root_name = f"schedule:{schedule_root.label or '?'}"
            initial_path = self._node_invariant_path(
                schedule_root,
                node,
                _kind_str(schedule_root.kind),
            )
            return [(root_name, node, initial_path, f"{root_name}:{initial_path}")]
        initial_path = self._node_invariant_path(self.statute.body, node, "body")
        return [("body", node, initial_path, f"body:{initial_path}")]

    def _invariant_target_roots(
        self,
        root_filter: set[tuple[str, str]] | None = None,
    ) -> list[tuple[str, UKMutableNode, str, str]]:
        targets: list[tuple[str, UKMutableNode, str, str]] = []
        if root_filter is None or ("body", "") in root_filter:
            targets.append(("body", self.statute.body, "body", "body:"))
        for schedule in self.statute.supplements:
            clean_label = _clean_num(str(schedule.label or ""))
            if root_filter is None or ("schedule", clean_label) in root_filter:
                root_name = f"schedule:{schedule.label or '?'}"
                initial_path = _kind_str(schedule.kind)
                targets.append((root_name, schedule, initial_path, f"{root_name}:"))
        return targets

    def _invariant_target_roots_for_op(
        self,
        op: LegalOperation,
    ) -> list[tuple[str, UKMutableNode, str, str]]:
        root_filter = self._invariant_root_filter_for_op(op)
        if root_filter is None or root_filter != {("body", "")}:
            return self._invariant_target_roots(root_filter)
        if len(op.target.path) <= 1:
            return self._invariant_target_roots(root_filter)
        parent_roots = self._invariant_parent_target_roots_for_op(op)
        if parent_roots:
            return parent_roots
        top_kind, top_label = op.target.path[0]
        top_address = LegalAddress(path=((top_kind, top_label),), special=None)
        top_node = None
        top_eid = self._derive_target_eid(top_address)
        if top_eid:
            top_node, _top_parent, _top_idx = self._find_node_and_parent_statute(
                top_eid,
                allow_sequence_match=False,
            )
            if top_node is not None and not self._eid_candidate_matches_target_leaf(top_node, top_address):
                top_node = None
        if top_node is None:
            top_node, _top_parent, _top_idx = self._find_node_by_target(
                top_address,
                allow_recursive_match=True,
            )
        if top_node is None:
            return self._invariant_target_roots(root_filter)
        initial_path = self._node_invariant_path(self.statute.body, top_node, "body")
        return [("body", top_node, initial_path, f"body:{initial_path}")]

    def _collect_invariant_violations(
        self,
        root_filter: set[tuple[str, str]] | None = None,
    ) -> set[str]:
        violations: set[str] = set()
        for root_name, node, initial_path, _scope_prefix in self._invariant_target_roots(root_filter):
            for violation in _collect_duplicate_order_invariants(node, initial_path=initial_path):
                violations.add(f"{root_name}:{violation}")
        return violations

    def _record_invariant_violations(self, op: LegalOperation) -> None:
        if self._structure_mutation_serial == self._last_invariant_structure_serial:
            return
        target_roots = self._invariant_target_roots_for_op(op)
        current_violations: set[str] = set()
        scoped_prefixes: set[str] = set()
        for root_name, node, initial_path, scope_prefix in target_roots:
            scoped_prefixes.add(scope_prefix)
            for violation in _collect_duplicate_order_invariants(node, initial_path=initial_path):
                current_violations.add(f"{root_name}:{violation}")
        scoped_seen = {
            violation
            for violation in self._seen_invariant_violations
            if any(violation.startswith(scope_prefix) for scope_prefix in scoped_prefixes)
        }
        new_violations = sorted(current_violations - scoped_seen)
        if not new_violations:
            self._seen_invariant_violations.difference_update(scoped_seen)
            self._seen_invariant_violations.update(current_violations)
            self._last_invariant_structure_serial = self._structure_mutation_serial
            return
        payload_shape_violations = uk_payload_shape_invariant_violations(op)
        for scoped_violation in new_violations:
            if payload_shape_violations and uk_repeated_form_label_payload_shape_gap(op, payload_shape_violations):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_repeated_form_label_payload_shape_gap",
                    message=(
                        "UK replay applied an inserted schedule payload whose form-like source "
                        "structure repeats local item labels under the same paragraph."
                    ),
                    op=op,
                    detail=_invariant_detail(
                        op,
                        scoped_violation,
                        payload_violations="; ".join(payload_shape_violations),
                    ),
                )
            elif payload_shape_violations or uk_payload_container_shape_gap(op, scoped_violation):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_payload_shape_gap",
                    message="UK replay applied a payload that already violated order/duplication tree invariants.",
                    op=op,
                    detail=_invariant_detail(
                        op,
                        scoped_violation,
                        payload_violations="; ".join(payload_shape_violations),
                    ),
                )
            elif uk_replace_payload_kind_mismatch_gap(op, scoped_violation):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_replace_payload_target_leaf_mismatch_gap",
                    message="UK replay hit an invariant because the replace payload kind does not match the lowered target leaf.",
                    op=op,
                    detail=_invariant_detail(
                        op,
                        scoped_violation,
                        payload_kind=str(op.payload.kind) if op.payload is not None else "",
                    ),
                )
            elif uk_part_order_shape_gap(op, scoped_violation):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_part_order_shape_gap",
                    message="UK replay hit a mixed-label part ordering seam that is not yet canonically ordered.",
                    op=op,
                    detail=_invariant_detail(op, scoped_violation),
                )
            elif uk_chapter_order_shape_gap(op, scoped_violation):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_chapter_order_shape_gap",
                    message="UK replay hit a mixed-label chapter ordering seam that is not yet canonically ordered.",
                    op=op,
                    detail=_invariant_detail(op, scoped_violation),
                )
            elif uk_source_anchored_order_observation(op, scoped_violation):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_source_anchored_order_observed",
                    message=(
                        "UK replay retained explicit source insertion order even though the "
                        "generic label-order invariant would sort the inserted label elsewhere."
                    ),
                    op=op,
                    detail=_invariant_detail(op, scoped_violation),
                )
            elif uk_section_order_shape_gap(op, scoped_violation):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_section_order_shape_gap",
                    message="UK replay hit an alphanumeric section ordering seam that is not yet canonically ordered.",
                    op=op,
                    detail=_invariant_detail(op, scoped_violation),
                )
            elif uk_paragraph_order_shape_gap(op, scoped_violation):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_paragraph_order_shape_gap",
                    message="UK replay hit a mixed-label paragraph ordering seam that is not yet canonically ordered.",
                    op=op,
                    detail=_invariant_detail(op, scoped_violation),
                )
            elif uk_subparagraph_order_shape_gap(op, scoped_violation):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_subparagraph_order_shape_gap",
                    message="UK replay hit a mixed-label subparagraph ordering seam that is not yet canonically ordered.",
                    op=op,
                    detail=_invariant_detail(op, scoped_violation),
                )
            elif uk_item_order_shape_gap(op, scoped_violation):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_item_order_shape_gap",
                    message="UK replay hit a mixed-label item ordering seam that is not yet canonically ordered.",
                    op=op,
                    detail=_invariant_detail(op, scoped_violation),
                )
            else:
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_tree_invariant_violation",
                    message="UK replay violated order/duplication tree invariant after applying an op.",
                    op=op,
                    detail=_invariant_detail(op, scoped_violation),
                )
        self._seen_invariant_violations.difference_update(scoped_seen)
        self._seen_invariant_violations.update(current_violations)
        self._last_invariant_structure_serial = self._structure_mutation_serial

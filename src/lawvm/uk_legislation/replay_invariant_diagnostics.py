"""UK replay tree-invariant diagnostics.

This module owns post-apply invariant classification for the UK replay
executor. It records existing replay findings; it does not repair or mutate the
tree.
"""

from __future__ import annotations

from typing import NamedTuple, Optional, Protocol, cast

from lawvm.core import tree_ops
from lawvm.core.ir import LegalAddress, LegalOperation
from lawvm.core.mutation_accounting import build_mutation_invariant_reports
from lawvm.core.mutation_boundary import TreePathStep, TreePaths
from lawvm.core.mutation_events import MutationEvent
from lawvm.core.semantic_types import StructuralAction
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.uk_legislation.mutable_ir import UKMutableNode, UKMutableStatute
from lawvm.uk_legislation.replay_state import NodeLookupResult
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
    uk_payload_shape_invariant_violation_records,
    uk_repeated_form_label_payload_shape_gap,
    uk_replace_payload_kind_mismatch_gap,
    uk_section_order_shape_gap,
    uk_source_anchored_order_observation,
    uk_subparagraph_order_shape_gap,
)


class _InvariantTargetRoot(NamedTuple):
    root_name: str
    node: UKMutableNode
    initial_path: str
    scope_prefix: str


class _InvariantReplaySelf(Protocol):
    statute: UKMutableStatute
    adjudications_out: list[CompileAdjudication]
    mutation_events_out: list[MutationEvent] | None
    _seen_invariant_violations: set[str]
    _structure_mutation_serial: int
    _last_invariant_structure_serial: int

    def _find_path_to_node(
        self,
        root: UKMutableNode,
        target_node: UKMutableNode,
        path: tuple[int, ...] = (),
    ) -> Optional[tuple[int, ...]]: ...

    def _derive_target_eid(self, addr: LegalAddress) -> str: ...

    def _find_node_and_parent_statute(
        self,
        eid: str,
        *,
        allow_sequence_match: bool = True,
    ) -> NodeLookupResult: ...

    def _eid_candidate_matches_target_leaf(self, node: UKMutableNode, target: LegalAddress) -> bool: ...

    def _find_node_by_target(
        self,
        target: LegalAddress,
        *,
        allow_compound_subsection_alias: bool = False,
        allow_recursive_match: bool = True,
        target_resolution_op: LegalOperation | None = None,
    ) -> NodeLookupResult: ...


def _invariant_replay_self(replay: object) -> _InvariantReplaySelf:
    return cast(_InvariantReplaySelf, replay)


_DUPLICATE_ORDER_INVARIANT_FAMILIES: frozenset[tree_ops.TreeInvariantKind] = frozenset(
    {
        "duplicate_label",
        "sort_order",
    }
)


def _parse_invariant_path_text(path: str) -> tree_ops.InvariantPath:
    steps: list[tree_ops.InvariantPathStep] = []
    for part in path.split("/"):
        kind, separator, label = part.partition(":")
        steps.append((kind, label if separator else None))
    return tuple(steps)


def _invariant_detail(
    op: LegalOperation,
    scoped_violation: str,
    **extra: object,
) -> dict[str, object]:
    return uk_replay_action_target_detail(
        op,
        op.target,
        blocking=False,
        violation=scoped_violation,
        **extra,
    )


def _tree_paths_jsonable(paths: TreePaths) -> list[list[tuple[str, str]]]:
    return [list(path) for path in paths]


def _collect_duplicate_order_invariants(root: UKMutableNode, initial_path: str | None = None) -> list[str]:
    return [violation.message for violation in _collect_duplicate_order_invariant_records(root, initial_path)]


def _collect_duplicate_order_invariant_records(
    root: UKMutableNode,
    initial_path: str | None = None,
) -> list[tree_ops.TreeInvariantViolation]:
    """Return the duplicate/order subset that UK replay diagnostics persist.

    ``tree_ops.check_invariants`` also checks nesting and normalized-label
    aliases, but this caller persists only duplicate/order families.  The
    shared typed iterator accepts UK's mutable replay nodes through a read-only
    protocol, so this path does not convert the subtree to frozen IR.
    """
    root_path = _parse_invariant_path_text(initial_path or root.kind.value)
    return list(
        tree_ops.iter_tree_invariant_violations(
            root,
            families=_DUPLICATE_ORDER_INVARIANT_FAMILIES,
            root_path=root_path,
        )
    )


class UKReplayInvariantDiagnosticsMixin:
    statute: UKMutableStatute
    adjudications_out: list[CompileAdjudication]
    _seen_invariant_violations: set[str]
    _structure_mutation_serial: int
    _last_invariant_structure_serial: int

    def _invariant_root_filter_for_op(self, op: LegalOperation) -> set[TreePathStep] | None:
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
                return f"{root_path}/{child.kind.value}:{child.label or '?'}"
        replay = _invariant_replay_self(self)
        path = replay._find_path_to_node(root, node)
        if path is None:
            return root_path
        parts = [root_path]
        current = root
        for child_idx in path:
            current = current.children[child_idx]
            parts.append(f"{current.kind.value}:{current.label or '?'}")
        return "/".join(parts)

    def _find_invariant_scope_node(
        self,
        address: LegalAddress,
    ) -> tuple[UKMutableNode | None, UKMutableNode | None]:
        replay = _invariant_replay_self(self)
        node = None
        target_eid = replay._derive_target_eid(address)
        if target_eid:
            node, _parent, _idx = replay._find_node_and_parent_statute(
                target_eid,
                allow_sequence_match=False,
            )
            if node is not None and not replay._eid_candidate_matches_target_leaf(
                node,
                address,
            ):
                node = None
        if node is None:
            node, _parent, _idx = replay._find_node_by_target(
                address,
                allow_recursive_match=False,
            )
        if node is None:
            node, _parent, _idx = replay._find_node_by_target(
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
    ) -> list[_InvariantTargetRoot]:
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
                schedule_root.kind.value,
            )
            return [_InvariantTargetRoot(root_name, node, initial_path, f"{root_name}:{initial_path}")]
        initial_path = self._node_invariant_path(self.statute.body, node, "body")
        return [_InvariantTargetRoot("body", node, initial_path, f"body:{initial_path}")]

    def _invariant_target_roots(
        self,
        root_filter: set[TreePathStep] | None = None,
    ) -> list[_InvariantTargetRoot]:
        targets: list[_InvariantTargetRoot] = []
        if root_filter is None or ("body", "") in root_filter:
            targets.append(_InvariantTargetRoot("body", self.statute.body, "body", "body:"))
        for schedule in self.statute.supplements:
            clean_label = _clean_num(str(schedule.label or ""))
            if root_filter is None or ("schedule", clean_label) in root_filter:
                root_name = f"schedule:{schedule.label or '?'}"
                initial_path = schedule.kind.value
                targets.append(_InvariantTargetRoot(root_name, schedule, initial_path, f"{root_name}:"))
        return targets

    def _invariant_target_roots_for_op(
        self,
        op: LegalOperation,
    ) -> list[_InvariantTargetRoot]:
        root_filter = self._invariant_root_filter_for_op(op)
        if root_filter is None:
            return self._invariant_target_roots(root_filter)
        if len(op.target.path) <= 1:
            return self._invariant_target_roots(root_filter)
        parent_roots = self._invariant_parent_target_roots_for_op(op)
        if parent_roots:
            return parent_roots
        if root_filter != {("body", "")}:
            return self._invariant_target_roots(root_filter)
        top_kind, top_label = op.target.path[0]
        top_address = LegalAddress(path=((top_kind, top_label),), special=None)
        top_node = None
        replay = _invariant_replay_self(self)
        top_eid = replay._derive_target_eid(top_address)
        if top_eid:
            top_node, _top_parent, _top_idx = replay._find_node_and_parent_statute(
                top_eid,
                allow_sequence_match=False,
            )
            if top_node is not None and not replay._eid_candidate_matches_target_leaf(top_node, top_address):
                top_node = None
        if top_node is None:
            top_node, _top_parent, _top_idx = replay._find_node_by_target(
                top_address,
                allow_recursive_match=True,
            )
        if top_node is None:
            return self._invariant_target_roots(root_filter)
        initial_path = self._node_invariant_path(self.statute.body, top_node, "body")
        return [_InvariantTargetRoot("body", top_node, initial_path, f"body:{initial_path}")]

    def _collect_invariant_violations(
        self,
        root_filter: set[TreePathStep] | None = None,
    ) -> set[str]:
        violations: set[str] = set()
        for target_root in self._invariant_target_roots(root_filter):
            for violation in _collect_duplicate_order_invariants(
                target_root.node,
                initial_path=target_root.initial_path,
            ):
                violations.add(f"{target_root.root_name}:{violation}")
        return violations

    def _invariant_removal_only_op(self, op: LegalOperation) -> bool:
        return op.action is StructuralAction.REPEAL

    def _latest_mutation_event_invariant_detail(self, op: LegalOperation) -> dict[str, object]:
        mutation_events = _invariant_replay_self(self).mutation_events_out
        if not mutation_events:
            return {}
        for event in reversed(mutation_events):
            if event.op_id != op.op_id:
                continue
            report = build_mutation_invariant_reports((event,))[0]
            return {
                "mutation_event_helper": report.helper,
                "mutation_event_outcome": report.outcome,
                "mutation_event_touched_paths": _tree_paths_jsonable(report.touched_paths),
                "mutation_event_permitted_paths": _tree_paths_jsonable(report.permitted_paths),
                "mutation_event_covered_changed_paths": _tree_paths_jsonable(report.covered_changed_paths),
                "mutation_event_unexplained_changed_paths": _tree_paths_jsonable(report.unexplained_changed_paths),
                "mutation_event_allowed_non_target_paths": _tree_paths_jsonable(report.allowed_non_target_paths),
                "mutation_event_path_set_invariant_holds": report.path_set_invariant_holds,
            }
        return {}

    def _record_invariant_violations(self, op: LegalOperation) -> None:
        if self._structure_mutation_serial == self._last_invariant_structure_serial:
            return
        if self._invariant_removal_only_op(op) and not self._seen_invariant_violations:
            self._last_invariant_structure_serial = self._structure_mutation_serial
            return
        target_roots = self._invariant_target_roots_for_op(op)
        scoped_prefixes: set[str] = set()
        for target_root in target_roots:
            scoped_prefixes.add(target_root.scope_prefix)
        scoped_seen = {
            violation
            for violation in self._seen_invariant_violations
            if any(violation.startswith(scope_prefix) for scope_prefix in scoped_prefixes)
        }
        if self._invariant_removal_only_op(op) and not scoped_seen:
            self._last_invariant_structure_serial = self._structure_mutation_serial
            return

        current_violations: set[str] = set()
        current_violation_records: dict[str, tree_ops.TreeInvariantViolation] = {}
        for target_root in target_roots:
            for violation in _collect_duplicate_order_invariant_records(
                target_root.node,
                initial_path=target_root.initial_path,
            ):
                scoped_violation = f"{target_root.root_name}:{violation.message}"
                current_violations.add(scoped_violation)
                current_violation_records[scoped_violation] = violation
        new_violations = sorted(current_violations - scoped_seen)
        if not new_violations:
            self._seen_invariant_violations.difference_update(scoped_seen)
            self._seen_invariant_violations.update(current_violations)
            self._last_invariant_structure_serial = self._structure_mutation_serial
            return
        payload_shape_violation_records = uk_payload_shape_invariant_violation_records(op)
        payload_shape_violations = [violation.message for violation in payload_shape_violation_records]
        mutation_event_detail = self._latest_mutation_event_invariant_detail(op)
        for scoped_violation in new_violations:
            invariant_record = current_violation_records.get(scoped_violation, scoped_violation)
            if payload_shape_violation_records and uk_repeated_form_label_payload_shape_gap(
                op,
                payload_shape_violation_records,
            ):
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
                        **mutation_event_detail,
                        payload_violations="; ".join(payload_shape_violations),
                    ),
                )
            elif payload_shape_violations or uk_payload_container_shape_gap(op, invariant_record):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_payload_shape_gap",
                    message="UK replay applied a payload that already violated order/duplication tree invariants.",
                    op=op,
                    detail=_invariant_detail(
                        op,
                        scoped_violation,
                        **mutation_event_detail,
                        payload_violations="; ".join(payload_shape_violations),
                    ),
                )
            elif uk_replace_payload_kind_mismatch_gap(op, invariant_record):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_replace_payload_target_leaf_mismatch_gap",
                    message="UK replay hit an invariant because the replace payload kind does not match the lowered target leaf.",
                    op=op,
                    detail=_invariant_detail(
                        op,
                        scoped_violation,
                        **mutation_event_detail,
                        payload_kind=str(op.payload.kind) if op.payload is not None else "",
                    ),
                )
            elif uk_part_order_shape_gap(op, invariant_record):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_part_order_shape_gap",
                    message="UK replay hit a mixed-label part ordering seam that is not yet canonically ordered.",
                    op=op,
                    detail=_invariant_detail(op, scoped_violation, **mutation_event_detail),
                )
            elif uk_chapter_order_shape_gap(op, invariant_record):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_chapter_order_shape_gap",
                    message="UK replay hit a mixed-label chapter ordering seam that is not yet canonically ordered.",
                    op=op,
                    detail=_invariant_detail(op, scoped_violation, **mutation_event_detail),
                )
            elif uk_source_anchored_order_observation(op, invariant_record):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_source_anchored_order_observed",
                    message=(
                        "UK replay retained explicit source insertion order even though the "
                        "generic label-order invariant would sort the inserted label elsewhere."
                    ),
                    op=op,
                    detail=_invariant_detail(op, scoped_violation, **mutation_event_detail),
                )
            elif uk_section_order_shape_gap(op, invariant_record):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_section_order_shape_gap",
                    message="UK replay hit an alphanumeric section ordering seam that is not yet canonically ordered.",
                    op=op,
                    detail=_invariant_detail(op, scoped_violation, **mutation_event_detail),
                )
            elif uk_paragraph_order_shape_gap(op, invariant_record):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_paragraph_order_shape_gap",
                    message="UK replay hit a mixed-label paragraph ordering seam that is not yet canonically ordered.",
                    op=op,
                    detail=_invariant_detail(op, scoped_violation, **mutation_event_detail),
                )
            elif uk_subparagraph_order_shape_gap(op, invariant_record):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_subparagraph_order_shape_gap",
                    message="UK replay hit a mixed-label subparagraph ordering seam that is not yet canonically ordered.",
                    op=op,
                    detail=_invariant_detail(op, scoped_violation, **mutation_event_detail),
                )
            elif uk_item_order_shape_gap(op, invariant_record):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_item_order_shape_gap",
                    message="UK replay hit a mixed-label item ordering seam that is not yet canonically ordered.",
                    op=op,
                    detail=_invariant_detail(op, scoped_violation, **mutation_event_detail),
                )
            else:
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_tree_invariant_violation",
                    message="UK replay violated order/duplication tree invariant after applying an op.",
                    op=op,
                    detail=_invariant_detail(op, scoped_violation, **mutation_event_detail),
                )
        self._seen_invariant_violations.difference_update(scoped_seen)
        self._seen_invariant_violations.update(current_violations)
        self._last_invariant_structure_serial = self._structure_mutation_serial

"""UK replay insertion routing and EID lookup helpers."""

from __future__ import annotations

from typing import Any, Optional, Protocol, cast

from lawvm.core.ir import IRNode, LegalAddress, LegalOperation
from lawvm.uk_legislation.addressing import (
    _addr_container,
    _addr_field,
    _addr_leaf_kind,
    _addr_leaf_label,
    _canonicalize_eid_tail_label,
    _canonicalize_schedule_paragraph_eid_label,
    _schedule_target_levels,
)
from lawvm.uk_legislation.authority_filter import _following_eid, _preceding_eid
from lawvm.uk_legislation.canonicalize import uk_find_body_predecessor_parent
from lawvm.uk_legislation.mutable_ir import (
    UKMutableNode,
    UKMutableStatute,
    uk_insert_child_sorted,
    uk_insert_node_at_index,
    uk_replace_children,
)
from lawvm.uk_legislation.ordering import _label_sort_key
from lawvm.uk_legislation.provenance_notes import (
    _schedule_list_entry_selector,
    _schedule_list_entry_table_rows_selector,
    _schedule_table_end_rows_selector,
    _table_cell_child_list_insert_selector,
    _table_column_insert_selector,
    _table_row_insert_selector,
)
from lawvm.uk_legislation.provision_extractor import _get_id_sequence
from lawvm.uk_legislation.replay_records import (
    _append_uk_replay_adjudication,
    uk_replay_action_target_detail,
    uk_replay_blocking_action_target_detail,
    uk_replay_recovery_action_target_detail,
)
from lawvm.uk_legislation.replay_state import NodeLookupResult
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.uk_legislation.replay_target_gaps import (
    uk_crossheading_insert_target_gap,
    uk_existing_target_insert_already_materialized,
    uk_existing_target_insert_conflict_detail,
    uk_existing_target_insert_gap,
)
from lawvm.uk_legislation.target_anchors import _body_target_eid_suffixes, uk_match_kind_label
from lawvm.uk_legislation.source_definition_structural_insert import (
    UK_DEFINITION_CHILD_STRUCTURAL_INSERT_BEFORE_TAIL_CONNECTOR_RULE_ID,
    UK_DEFINITION_CHILD_STRUCTURAL_SIBLING_INSERT_RULE_ID,
)
from lawvm.uk_legislation.uk_grafter import _clean_num

_TOP_SCOPED_EID_PREFIXES = frozenset(
    {"annex", "article", "chapter", "division", "part", "schedule", "section"}
)


class _InsertReplaySelf(Protocol):
    statute: UKMutableStatute
    eid_map: dict[str, str]
    adjudications_out: list[CompileAdjudication]

    def _record_invariant_violations(self, op: LegalOperation) -> None: ...

    def _emit_top_section_snapshot(self, op: LegalOperation) -> None: ...

    def _malformed_target_gap(self, target: LegalAddress) -> bool: ...

    def _malformed_target_gap_kind(self, target: LegalAddress) -> str: ...

    def _missing_parent_shape_gap(self, target: LegalAddress) -> bool: ...

    def _missing_parent_shape_gap_kind(self, target: LegalAddress) -> str: ...

    def _schedule_paragraph_carrier_gap(self, target: LegalAddress) -> bool: ...

    def _schedule_paragraph_carrier_gap_kind(self, target: LegalAddress) -> str: ...

    def _leading_blank_subparagraph_gap(self, target: LegalAddress) -> bool: ...

    def _missing_sibling_range_gap(self, target: LegalAddress) -> bool: ...

    def _empty_descendant_shape_gap(self, target: LegalAddress) -> bool: ...

    def _insert_schedule_list_entry_table_rows(
        self,
        target: LegalAddress,
        new_node: UKMutableNode,
        op: LegalOperation,
        selector: dict[str, Any],
    ) -> bool: ...

    def _insert_schedule_list_entry(
        self,
        target: LegalAddress,
        new_node: UKMutableNode,
        op: LegalOperation,
        selector: dict[str, Any],
    ) -> bool: ...

    def _insert_table_cell_child_list_item(
        self,
        target: LegalAddress,
        new_node: UKMutableNode,
        op: LegalOperation,
        selector: dict[str, Any],
    ) -> bool: ...

    def _insert_table_column(
        self,
        target: LegalAddress,
        new_node: UKMutableNode,
        op: LegalOperation,
        selector: dict[str, Any],
    ) -> bool: ...

    def _insert_table_entry_row(
        self,
        target: LegalAddress,
        new_node: UKMutableNode,
        op: LegalOperation,
        selector: dict[str, Any],
    ) -> bool: ...

    def _find_node_by_target(
        self,
        target: LegalAddress,
        *,
        allow_compound_subsection_alias: bool = False,
        allow_recursive_match: bool = True,
        target_resolution_op: LegalOperation | None = None,
    ) -> NodeLookupResult: ...

    def _find_node_and_parent_statute(
        self,
        eid: str,
        *,
        allow_sequence_match: bool = True,
    ) -> NodeLookupResult: ...

    def _log(self, message: str) -> None: ...

    def _record_child_inserted(self, parent: UKMutableNode, node: UKMutableNode) -> None: ...

    def _record_supplement_inserted(self, node: UKMutableNode) -> None: ...

    def _insert_supplement_sorted(self, new_node: UKMutableNode) -> bool: ...

    def _cached_exact_eid_lookup(self, eid: str) -> NodeLookupResult: ...

    def _cached_suffix_eid_lookup(self, eid: str) -> NodeLookupResult: ...

    def _cached_eid_search_lookup(
        self,
        eid: str,
        *,
        allow_sequence_match: bool,
    ) -> NodeLookupResult | None: ...

    def _store_eid_search_cache(
        self,
        eid: str,
        *,
        allow_sequence_match: bool,
        result: NodeLookupResult,
    ) -> None: ...


def _insert_replay_self(replay: object) -> _InsertReplaySelf:
    return cast(_InsertReplaySelf, replay)


def _normalized_definition_text(text: object) -> str:
    return " ".join(str(text or "").split()).strip().lower()


def _definition_child_identity(node: UKMutableNode) -> tuple[str, str]:
    return (
        _normalized_definition_text(node.attrs.get("definition_term")),
        _clean_num(str(node.attrs.get("definition_child_label") or "")),
    )


class UKReplayInsertApplyMixin:
    statute: UKMutableStatute
    eid_map: dict[str, str]

    def _skip_insert_if_parent_already_has_target_child(
        self,
        *,
        parent_node: UKMutableNode,
        target: LegalAddress,
        op: LegalOperation,
        target_resolution_recovery: str,
    ) -> bool:
        replay = _insert_replay_self(self)
        leaf_kind = _addr_leaf_kind(target)
        leaf_label = _addr_leaf_label(target)
        if not leaf_kind or not leaf_label:
            return False
        if op.payload is None:
            return False
        payload_label = _clean_num(str(op.payload.label or ""))
        if payload_label != _clean_num(str(leaf_label)):
            return False
        payload_kind = str(getattr(op.payload.kind, "value", op.payload.kind) or "").lower()
        if payload_kind and payload_kind != str(leaf_kind).lower():
            return False
        existing_child = next(
            (
                child
                for child in parent_node.children
                if uk_match_kind_label(child, str(leaf_kind), str(leaf_label))
            ),
            None,
        )
        if existing_child is None:
            return False
        if uk_existing_target_insert_already_materialized(existing_child, op):
            _append_uk_replay_adjudication(
                replay.adjudications_out,
                kind="uk_replay_existing_target_already_materialized",
                message=(
                    "UK replay skipped insert: target child already exists under "
                    "the resolved parent with the same normalized payload text."
                ),
                op=op,
                detail=uk_replay_action_target_detail(
                    op,
                    target,
                    blocking=False,
                    payload_kind=str(op.payload.kind),
                    payload_label=op.payload.label or "",
                    target_resolution_recovery=target_resolution_recovery,
                ),
            )
            return True
        conflict_detail = uk_existing_target_insert_conflict_detail(existing_child, op)
        _append_uk_replay_adjudication(
            replay.adjudications_out,
            kind="uk_replay_existing_target_conflict_gap" if conflict_detail else "uk_replay_existing_target_gap",
            message=(
                "UK replay skipped insert: resolved parent already contains "
                "the target child before applying the op."
            ),
            op=op,
            detail=uk_replay_blocking_action_target_detail(
                op,
                target,
                payload_kind=str(op.payload.kind),
                payload_label=op.payload.label or "",
                target_resolution_recovery=target_resolution_recovery,
                **(conflict_detail or {}),
            ),
        )
        return True

    def _apply_insert_op(
        self,
        op: LegalOperation,
        target: LegalAddress,
        node: UKMutableNode | None,
        insert_existing_target_resolution: str,
    ) -> None:
        replay = _insert_replay_self(self)
        if op.payload is not None:
            if uk_crossheading_insert_target_gap(target, op):
                _append_uk_replay_adjudication(
                    replay.adjudications_out,
                    kind="uk_replay_crossheading_target_gap",
                    message=(
                        "UK replay skipped crossheading insert: target has no explicit "
                        "crossheading identity or placement anchor."
                    ),
                    op=op,
                    detail=uk_replay_blocking_action_target_detail(
                        op,
                        target,
                        payload_kind=str(op.payload.kind),
                        payload_text=(op.payload.text or "")[:200],
                    ),
                )
                return
            if uk_existing_target_insert_gap(target, node, op):
                if uk_existing_target_insert_already_materialized(node, op):
                    _append_uk_replay_adjudication(
                        replay.adjudications_out,
                        kind="uk_replay_existing_target_already_materialized",
                        message=(
                            "UK replay skipped insert: target already exists with the same "
                            "normalized payload text."
                        ),
                        op=op,
                        detail=uk_replay_action_target_detail(
                            op,
                            target,
                            blocking=False,
                            payload_kind=str(op.payload.kind),
                            payload_label=op.payload.label or "",
                            target_resolution_recovery=insert_existing_target_resolution,
                        ),
                    )
                    return
                if conflict_detail := uk_existing_target_insert_conflict_detail(node, op):
                    _append_uk_replay_adjudication(
                        replay.adjudications_out,
                        kind="uk_replay_existing_target_conflict_gap",
                        message=(
                            "UK replay skipped insert: target path already exists with "
                            "different normalized payload text."
                        ),
                        op=op,
                        detail=uk_replay_blocking_action_target_detail(
                            op,
                            target,
                            payload_kind=str(op.payload.kind),
                            payload_label=op.payload.label or "",
                            target_resolution_recovery=insert_existing_target_resolution,
                            **conflict_detail,
                        ),
                    )
                    return
                _append_uk_replay_adjudication(
                    replay.adjudications_out,
                    kind="uk_replay_existing_target_gap",
                    message="UK replay skipped insert: target path already exists before applying the op.",
                    op=op,
                    detail=uk_replay_blocking_action_target_detail(
                        op,
                        target,
                        payload_kind=str(op.payload.kind),
                        payload_label=op.payload.label or "",
                        target_resolution_recovery=insert_existing_target_resolution,
                    ),
                )
                return
            # Clone payload so repeated ops (same source for multiple targets) don't share nodes
            inserted = self._insert_node_v2(
                target,
                UKMutableNode.from_dict(op.payload.to_jsonable_dict()),
                op,
            )
            if inserted:
                replay._record_invariant_violations(op)
                replay._emit_top_section_snapshot(op)
            else:
                if _schedule_list_entry_table_rows_selector(op) is not None:
                    return
                if _schedule_table_end_rows_selector(op) is not None:
                    return
                if _schedule_list_entry_selector(op) is not None:
                    return
                if _table_cell_child_list_insert_selector(op) is not None:
                    return
                if _table_column_insert_selector(op) is not None:
                    return
                if _table_row_insert_selector(op) is not None:
                    return
                if replay._malformed_target_gap(target):
                    _append_uk_replay_adjudication(
                        replay.adjudications_out,
                        kind=replay._malformed_target_gap_kind(target),
                        message="UK replay skipped insert: lowered target path is malformed.",
                        op=op,
                        detail=uk_replay_blocking_action_target_detail(
                            op,
                            target,
                            payload_kind=str(op.payload.kind),
                            payload_label=op.payload.label or "",
                        ),
                    )
                    return
                if replay._missing_parent_shape_gap(target):
                    _append_uk_replay_adjudication(
                        replay.adjudications_out,
                        kind=replay._missing_parent_shape_gap_kind(target),
                        message="UK replay skipped insert: immediate parent target path is structurally absent.",
                        op=op,
                        detail=uk_replay_blocking_action_target_detail(
                            op,
                            target,
                            payload_kind=str(op.payload.kind),
                            payload_label=op.payload.label or "",
                        ),
                    )
                    return
                if replay._schedule_paragraph_carrier_gap(target):
                    _append_uk_replay_adjudication(
                        replay.adjudications_out,
                        kind=replay._schedule_paragraph_carrier_gap_kind(target),
                        message="UK replay skipped insert: schedule target expects a paragraph carrier that is absent or wrapped by legacy p1group structure.",
                        op=op,
                        detail=uk_replay_blocking_action_target_detail(
                            op,
                            target,
                            payload_kind=str(op.payload.kind),
                            payload_label=op.payload.label or "",
                        ),
                    )
                    return
                if replay._leading_blank_subparagraph_gap(target):
                    _append_uk_replay_adjudication(
                        replay.adjudications_out,
                        kind="uk_replay_absent_sibling_range_gap",
                        message="UK replay skipped insert: target falls inside an absent leading numeric subparagraph gap under blank schedule placeholders.",
                        op=op,
                        detail=uk_replay_blocking_action_target_detail(
                            op,
                            target,
                            payload_kind=str(op.payload.kind),
                            payload_label=op.payload.label or "",
                        ),
                    )
                    return
                if replay._missing_sibling_range_gap(target):
                    _append_uk_replay_adjudication(
                        replay.adjudications_out,
                        kind="uk_replay_absent_sibling_range_gap",
                        message="UK replay skipped insert: target falls inside an absent sibling range under the parent path.",
                        op=op,
                        detail=uk_replay_blocking_action_target_detail(
                            op,
                            target,
                            payload_kind=str(op.payload.kind),
                            payload_label=op.payload.label or "",
                        ),
                    )
                    return
                if replay._empty_descendant_shape_gap(target):
                    _append_uk_replay_adjudication(
                        replay.adjudications_out,
                        kind="uk_replay_empty_descendant_shape_gap",
                        message="UK replay skipped insert: parent target exists but has no descendant structural shape.",
                        op=op,
                        detail=uk_replay_blocking_action_target_detail(
                            op,
                            target,
                            payload_kind=str(op.payload.kind),
                            payload_label=op.payload.label or "",
                        ),
                    )
                    return
                _append_uk_replay_adjudication(
                    replay.adjudications_out,
                    kind="uk_replay_payload_mismatch",
                    message="UK replay skipped insert: payload could not be inserted by target path.",
                    op=op,
                    detail=uk_replay_blocking_action_target_detail(
                        op,
                        target,
                        payload_kind=str(op.payload.kind),
                        payload_label=op.payload.label or "",
                    ),
                )
        else:
            _append_uk_replay_adjudication(
                replay.adjudications_out,
                kind="uk_replay_payload_missing",
                message="UK replay skipped insert: payload missing.",
                op=op,
                detail=uk_replay_blocking_action_target_detail(op, target),
            )

    def _insert_node_v2(
        self,
        target: LegalAddress,
        new_node: UKMutableNode,
        op: LegalOperation,
    ) -> bool:
        replay = _insert_replay_self(self)
        from lawvm.uk_legislation.canonicalize import uk_resolve_insertion_parent

        schedule_list_entry_table_rows_selector = _schedule_list_entry_table_rows_selector(op)
        if schedule_list_entry_table_rows_selector is not None:
            return replay._insert_schedule_list_entry_table_rows(
                target,
                new_node,
                op,
                schedule_list_entry_table_rows_selector,
            )
        schedule_table_end_rows_selector = _schedule_table_end_rows_selector(op)
        if schedule_table_end_rows_selector is not None:
            return replay._insert_schedule_list_entry_table_rows(
                target,
                new_node,
                op,
                schedule_table_end_rows_selector,
            )
        schedule_list_entry_selector = _schedule_list_entry_selector(op)
        if schedule_list_entry_selector is not None:
            return replay._insert_schedule_list_entry(target, new_node, op, schedule_list_entry_selector)
        table_cell_child_list_insert_selector = _table_cell_child_list_insert_selector(op)
        if table_cell_child_list_insert_selector is not None:
            return replay._insert_table_cell_child_list_item(
                target,
                new_node,
                op,
                table_cell_child_list_insert_selector,
            )
        table_column_insert_selector = _table_column_insert_selector(op)
        if table_column_insert_selector is not None:
            return replay._insert_table_column(target, new_node, op, table_column_insert_selector)
        table_row_insert_selector = _table_row_insert_selector(op)
        if table_row_insert_selector is not None:
            return replay._insert_table_entry_row(target, new_node, op, table_row_insert_selector)
        if self._insert_definition_child_structural_sibling(target, new_node, op):
            return True

        prec_eid = _preceding_eid(op)
        following_eid = _following_eid(op)
        parent_node, insert_idx = uk_resolve_insertion_parent(
            target=target,
            body_root=cast(IRNode, self.statute.body),
            node_kind=str(new_node.kind),
            node_label=new_node.label,
            preceding_eid=prec_eid,
            following_eid=following_eid,
            find_node_by_target=replay._find_node_by_target,
            find_node_and_parent_statute=self._find_node_and_parent_statute,
            label_sort_key=_label_sort_key,
        )
        parent_node = cast(Optional[UKMutableNode], parent_node)
        target_eid = self._derive_target_eid(target)
        if target_eid and "eId" not in new_node.attrs and "id" not in new_node.attrs:
            new_node.attrs["eId"] = target_eid

        def _inherit_parent_local_eid(parent_node: UKMutableNode, candidate: UKMutableNode) -> UKMutableNode:
            parent_eid = str(parent_node.attrs.get("eId") or parent_node.attrs.get("id") or "")
            current_eid = str(candidate.attrs.get("eId") or candidate.attrs.get("id") or "")
            label = str(candidate.label or _addr_leaf_label(target) or "").strip()
            if not parent_eid or not label:
                return candidate
            if current_eid and (
                (current_eid == target_eid and _addr_container(target) == "schedule")
                or current_eid in self.eid_map.values()
            ):
                return candidate
            if target_eid and _addr_container(target) == "schedule":
                candidate.attrs["eId"] = target_eid
                return candidate
            candidate.attrs["eId"] = f"{parent_eid}-{label}"
            return candidate

        if parent_node and insert_idx is not None:
            if self._skip_insert_if_parent_already_has_target_child(
                parent_node=parent_node,
                target=target,
                op=op,
                target_resolution_recovery="resolved_parent_child_duplicate_guard",
            ):
                return True
            new_node = _inherit_parent_local_eid(parent_node, new_node)
            replay._log(f"  EXECUTOR: inserting {new_node.kind} {new_node.label} at routed index {insert_idx}")
            children = list(parent_node.children)
            if not uk_insert_node_at_index(children, insert_idx, new_node):
                return False
            uk_replace_children(parent_node, children)
            replay._record_child_inserted(parent_node, new_node)
            return True
        if parent_node:
            if self._skip_insert_if_parent_already_has_target_child(
                parent_node=parent_node,
                target=target,
                op=op,
                target_resolution_recovery="resolved_parent_child_duplicate_guard",
            ):
                return True
            new_node = _inherit_parent_local_eid(parent_node, new_node)
            replay._log(
                f"  EXECUTOR: inserting {new_node.kind} {new_node.label} into {parent_node.kind} {parent_node.label}"
            )
            inserted = uk_insert_child_sorted(parent_node, new_node)
            if inserted:
                replay._record_child_inserted(parent_node, new_node)
            return inserted

        # Build parent address by dropping the last path segment.
        # Single-segment paths (e.g. section:2a) get parent = body/schedules directly,
        # matching the old IRTargetRef behaviour where parent_target.section=None caused
        # _find_node_by_target to return the body node for non-schedule containers.
        container = _addr_container(target)
        parent_addr = target.parent() if len(target.path) > 1 else None

        if parent_addr is not None:
            p_node, _, _ = replay._find_node_by_target(parent_addr)
            if p_node is not None and ("definition_term" in p_node.attrs or "definition_child_label" in p_node.attrs):
                p_node = None

            if p_node:
                if self._skip_insert_if_parent_already_has_target_child(
                    parent_node=p_node,
                    target=target,
                    op=op,
                    target_resolution_recovery="explicit_parent_child_duplicate_guard",
                ):
                    return True
                new_node = _inherit_parent_local_eid(p_node, new_node)
                replay._log(
                    f"  EXECUTOR: inserting {new_node.kind} {new_node.label} into {p_node.kind} {p_node.label}"
                )
                inserted = uk_insert_child_sorted(p_node, new_node)
                if inserted:
                    replay._record_child_inserted(p_node, new_node)
                return inserted
        elif container == "schedule":
            # Single-segment schedule target: the target IS the schedule — insert payload into it,
            # but only when the payload is a part, chapter, or section (structural containers
            # that appear as direct children of schedules).  Paragraph/subsection payloads
            # targeted at a whole schedule are likely table-row inserts (e.g. concordat
            # schedules) whose EIDs don't match oracle EIDs — fall through to the EID-derived
            # logic in those cases.
            #
            # A schedule payload targeted at a whole schedule path (for example
            # ``schedule:7a`` with payload kind ``schedule``) is a top-level
            # schedule insertion and must be added to ``statute.supplements``.
            # Falling through to the EID-derived parent lookup turns
            # ``schedule-7a`` into parent ``schedule`` and can incorrectly nest
            # the new schedule under an existing schedule branch like
            # ``schedule-7``.
            _sch_structural = {"part", "chapter", "section", "article", "p1group", "crossheading"}
            new_kind = str(new_node.kind).lower()
            if new_kind == "schedule":
                replay._log(f"  EXECUTOR: inserting schedule {new_node.label} at top-level")
                return replay._insert_supplement_sorted(new_node)
            if new_kind in _sch_structural:
                sch_node, _, _ = replay._find_node_by_target(target)
                if sch_node:
                    sch_node = cast(UKMutableNode, sch_node)
                    if self._skip_insert_if_parent_already_has_target_child(
                        parent_node=sch_node,
                        target=target,
                        op=op,
                        target_resolution_recovery="schedule_root_child_duplicate_guard",
                    ):
                        return True
                    new_node = _inherit_parent_local_eid(sch_node, new_node)
                    replay._log(
                        f"  EXECUTOR: inserting {new_node.kind} {new_node.label} into schedule {sch_node.label}"
                    )
                    inserted = uk_insert_child_sorted(sch_node, new_node)
                    if inserted:
                        replay._record_child_inserted(sch_node, new_node)
                    return inserted
                return False
        else:
            # Single-segment non-schedule target: prefer inserting after the
            # nearest existing same-kind predecessor in its actual parent,
            # because UK body sections/articles often live under wrappers like
            # crossheading -> p1group rather than directly under body.
            pred_parent, pred_idx, pred_label = uk_find_body_predecessor_parent(
                cast(IRNode, self.statute.body),
                str(new_node.kind),
                new_node.label,
                label_sort_key=_label_sort_key,
            )
            if pred_parent is not None and pred_idx is not None:
                pred_parent = cast(UKMutableNode, pred_parent)
                if self._skip_insert_if_parent_already_has_target_child(
                    parent_node=pred_parent,
                    target=target,
                    op=op,
                    target_resolution_recovery="body_predecessor_parent_child_duplicate_guard",
                ):
                    return True
                replay._log(
                    f"  EXECUTOR: inserting {new_node.kind} {new_node.label} after body predecessor {pred_label}"
                )
                children: list[UKMutableNode] = list(pred_parent.children)
                if not uk_insert_node_at_index(children, pred_idx + 1, new_node):
                    return False
                uk_replace_children(pred_parent, children)
                replay._record_child_inserted(pred_parent, new_node)
                return True

            # No suitable predecessor exists in the body tree: fall back to a
            # true body-root insertion.
            replay._log(f"  EXECUTOR: inserting {new_node.kind} {new_node.label} into body (top-level)")
            inserted = uk_insert_child_sorted(self.statute.body, new_node)
            if inserted:
                replay._record_child_inserted(self.statute.body, new_node)
            return inserted

        if "-" in target_eid:
            parent_eid = "-".join(target_eid.split("-")[:-1])
            p_node, _, _ = self._find_node_and_parent_statute(parent_eid)
            if p_node:
                if self._skip_insert_if_parent_already_has_target_child(
                    parent_node=p_node,
                    target=target,
                    op=op,
                    target_resolution_recovery="eid_parent_child_duplicate_guard",
                ):
                    return True
                new_node = _inherit_parent_local_eid(p_node, new_node)
                replay._log(f"  EXECUTOR: inserting {new_node.kind} {new_node.label} into parent {parent_eid}")
                parent_node = cast(UKMutableNode, p_node)
                inserted = uk_insert_child_sorted(parent_node, new_node)
                if inserted:
                    replay._record_child_inserted(parent_node, new_node)
                return inserted

        if container == "schedule" and len(target.path) > 1:
            replay._log(
                "  EXECUTOR: refusing body-root fallback for schedule descendant "
                f"{new_node.kind} {new_node.label} target {target}"
            )
            return False

        body_root_kinds = {
            "part",
            "chapter",
            "crossheading",
            "pblock",
            "division",
            "section",
            "article",
            "rule",
            "regulation",
            "p1group",
            "schedule",
        }
        new_kind = str(new_node.kind).lower()
        if new_kind not in body_root_kinds:
            replay._log(
                "  EXECUTOR: WARN refusing impossible body-root fallback for "
                f"{new_node.kind} {new_node.label} target {target}"
            )
            return False
        replay._log(f"  EXECUTOR: fallback inserting {new_node.kind} {new_node.label} into body")
        if new_kind == "schedule":
            inserted = replay._insert_supplement_sorted(new_node)
        else:
            inserted = uk_insert_child_sorted(self.statute.body, new_node)
            if inserted:
                replay._record_child_inserted(self.statute.body, new_node)
        if not inserted:
            return False
        _append_uk_replay_adjudication(
            replay.adjudications_out,
            kind="uk_replay_body_root_fallback_insert_resolved",
            message=(
                "UK replay inserted a payload at body/supplement root after "
                "target parent resolution failed."
            ),
            op=op,
            detail=uk_replay_recovery_action_target_detail(
                op,
                target,
                family="target_resolution_recovery",
                recovery_target="supplement_root" if new_kind == "schedule" else "body_root",
                payload_kind=str(new_node.kind),
                payload_label=new_node.label or "",
                derived_target_eid=target_eid,
            ),
        )
        return True

    def _insert_definition_child_structural_sibling(
        self,
        target: LegalAddress,
        new_node: UKMutableNode,
        op: LegalOperation,
    ) -> bool:
        replay = _insert_replay_self(self)
        if new_node.attrs.get("source_rule_id") not in {
            UK_DEFINITION_CHILD_STRUCTURAL_SIBLING_INSERT_RULE_ID,
            UK_DEFINITION_CHILD_STRUCTURAL_INSERT_BEFORE_TAIL_CONNECTOR_RULE_ID,
        }:
            return False
        parent_addr = target.parent() if len(target.path) > 1 else None
        if parent_addr is None:
            return False
        parent_node, _, _ = replay._find_node_by_target(parent_addr)
        if parent_node is None:
            _append_uk_replay_adjudication(
                replay.adjudications_out,
                kind="uk_replay_definition_child_structural_sibling_parent_gap",
                message="UK replay skipped definition-child sibling insert: definition section parent was absent.",
                op=op,
                detail=uk_replay_blocking_action_target_detail(
                    op,
                    target,
                    payload_kind=str(new_node.kind),
                    payload_label=new_node.label or "",
                    strict_disposition="block",
                ),
            )
            return True
        definition_term, inserted_label = _definition_child_identity(new_node)
        anchor_label = _clean_num(str(new_node.attrs.get("source_anchor_child_label") or ""))
        if not definition_term or not inserted_label or not anchor_label:
            _append_uk_replay_adjudication(
                replay.adjudications_out,
                kind="uk_replay_definition_child_structural_sibling_anchor_gap",
                message="UK replay skipped definition-child sibling insert: payload lacks scoped definition-child identity.",
                op=op,
                detail=uk_replay_blocking_action_target_detail(
                    op,
                    target,
                    payload_kind=str(new_node.kind),
                    payload_label=new_node.label or "",
                    strict_disposition="block",
                ),
            )
            return True

        existing_indexes = [
            index
            for index, child in enumerate(parent_node.children)
            if _definition_child_identity(child) == (definition_term, inserted_label)
        ]
        if existing_indexes:
            existing = parent_node.children[existing_indexes[0]]
            if _normalized_definition_text(existing.text) == _normalized_definition_text(new_node.text):
                _append_uk_replay_adjudication(
                    replay.adjudications_out,
                    kind="uk_replay_definition_child_structural_sibling_already_materialized",
                    message=(
                        "UK replay skipped definition-child sibling insert: target definition child "
                        "already exists with the same normalized payload text."
                    ),
                    op=op,
                    detail=uk_replay_action_target_detail(
                        op,
                        target,
                        blocking=False,
                        payload_kind=str(new_node.kind),
                        payload_label=new_node.label or "",
                    ),
                )
                return True
            _append_uk_replay_adjudication(
                replay.adjudications_out,
                kind="uk_replay_definition_child_structural_sibling_conflict_gap",
                message=(
                    "UK replay skipped definition-child sibling insert: target definition child "
                    "already exists with different normalized payload text."
                ),
                op=op,
                detail=uk_replay_blocking_action_target_detail(
                    op,
                    target,
                    payload_kind=str(new_node.kind),
                    payload_label=new_node.label or "",
                    existing_text=str(existing.text or "")[:200],
                    payload_text=str(new_node.text or "")[:200],
                    strict_disposition="block",
                ),
            )
            return True

        anchor_indexes = [
            index
            for index, child in enumerate(parent_node.children)
            if _definition_child_identity(child) == (definition_term, anchor_label)
        ]
        if len(anchor_indexes) != 1:
            _append_uk_replay_adjudication(
                replay.adjudications_out,
                kind="uk_replay_definition_child_structural_sibling_anchor_gap",
                message=(
                    "UK replay skipped definition-child sibling insert: source-named "
                    "definition child anchor did not resolve uniquely."
                ),
                op=op,
                detail=uk_replay_blocking_action_target_detail(
                    op,
                    target,
                    payload_kind=str(new_node.kind),
                    payload_label=new_node.label or "",
                    definition_term=definition_term,
                    anchor_label=anchor_label,
                    anchor_match_count=len(anchor_indexes),
                    strict_disposition="block",
                ),
            )
            return True
        children = list(parent_node.children)
        if not uk_insert_node_at_index(children, anchor_indexes[0] + 1, new_node):
            return False
        uk_replace_children(parent_node, children)
        replay._record_child_inserted(parent_node, new_node)
        _append_uk_replay_adjudication(
            replay.adjudications_out,
            kind="uk_replay_definition_child_structural_sibling_insert_applied",
            message=(
                "UK replay inserted a source-owned definition child after the "
                "source-named sibling anchor."
            ),
            op=op,
            detail=uk_replay_action_target_detail(
                op,
                target,
                blocking=False,
                payload_kind=str(new_node.kind),
                payload_label=new_node.label or "",
                definition_term=definition_term,
                anchor_label=anchor_label,
            ),
        )
        return True

    def _eid_candidate_matches_target_leaf(self, node: UKMutableNode, target: LegalAddress) -> bool:
        leaf_kind = _addr_leaf_kind(target)
        if not leaf_kind:
            return True
        return uk_match_kind_label(node, str(leaf_kind), _addr_leaf_label(target))

    def _derive_target_eid(self, addr: LegalAddress) -> str:
        is_eur = self.statute.metadata.get("is_eur", False)
        container = _addr_container(addr)
        section = _addr_field(addr, "schedule") or _addr_field(addr, "section")
        part = _addr_field(addr, "part")
        chapter = _addr_field(addr, "chapter")
        if container == "schedule":
            schedule_levels = _schedule_target_levels(addr)
            paragraph = schedule_levels.paragraph
            subsection = schedule_levels.subparagraph
            item_labels = schedule_levels.item_labels
        else:
            paragraph = None
            subsection = None
            item_labels = []

        def _get_candidates():
            parts: list[str] = []
            if container == "schedule":
                sch_prefix = "annex" if is_eur else "schedule"
                if section:
                    parts.append(f"{sch_prefix}-{_clean_num(section)}")
                else:
                    parts.append(sch_prefix)

                # EU specific: very flat scheme for Annexes
                if is_eur:
                    eu_parts = list(parts)
                    if paragraph:
                        eu_parts.append(f"paragraph-{_clean_num(paragraph)}")
                    if subsection:
                        eu_parts.append(_clean_num(subsection))
                    for item_label in item_labels:
                        eu_parts.append(_canonicalize_eid_tail_label(item_label))
                    yield "-".join(eu_parts)
                    # Reset parts for hierarchical try
                    parts = [f"{sch_prefix}-{_clean_num(section)}"] if section else [sch_prefix]

                if part:
                    parts.append(f"part-{_clean_num(part)}")
                if chapter:
                    parts.append(f"chapter-{_clean_num(chapter)}")
                if paragraph:
                    if is_eur:
                        parts.append(f"paragraph-{_clean_num(paragraph)}")
                    else:
                        parts.append(f"paragraph-{_canonicalize_schedule_paragraph_eid_label(paragraph)}")
                if subsection:
                    parts.append(_clean_num(subsection))
                for item_label in item_labels:
                    parts.append(_canonicalize_eid_tail_label(item_label))
                yield "-".join(parts)
            else:
                # Try section and article prefixes
                for prefix in ["article", "section"] if is_eur else ["section", "article"]:
                    parts = []
                    if section:
                        parts.append(f"{prefix}-{_clean_num(section)}")
                        for suffix_label in _body_target_eid_suffixes(addr):
                            parts.append(_canonicalize_eid_tail_label(suffix_label))
                    yield "-".join(parts)

        for full_key in _get_candidates():
            if not full_key:
                continue
            if full_key.lower() in self.eid_map:
                return self.eid_map[full_key.lower()]

        # Fallback to the first best guess
        return next(_get_candidates(), "")

    def _eid_top_scope_lookup(
        self,
        eid: str,
    ) -> NodeLookupResult:
        replay = _insert_replay_self(self)
        parts = str(eid or "").split("-")
        if len(parts) < 3:
            return NodeLookupResult(node=None, parent=None, index=None)
        prefix = parts[0]
        if prefix not in _TOP_SCOPED_EID_PREFIXES:
            return NodeLookupResult(node=None, parent=None, index=None)
        top_eid = f"{prefix}-{parts[1]}"
        return replay._cached_exact_eid_lookup(top_eid)

    def _eid_has_strict_top_scope(self, eid: str) -> bool:
        parts = str(eid or "").split("-")
        return len(parts) >= 3 and parts[0] in _TOP_SCOPED_EID_PREFIXES and bool(parts[1])

    def _find_node_and_parent_statute(
        self,
        eid: str,
        *,
        allow_sequence_match: bool = True,
    ) -> NodeLookupResult:
        replay = _insert_replay_self(self)
        cached_node, cached_parent, cached_idx = replay._cached_exact_eid_lookup(eid)
        if cached_node is not None:
            return NodeLookupResult(node=cached_node, parent=cached_parent, index=cached_idx)
        suffix_node, suffix_parent, suffix_idx = replay._cached_suffix_eid_lookup(eid)
        if suffix_node is not None:
            return NodeLookupResult(node=suffix_node, parent=suffix_parent, index=suffix_idx)
        cached_search = replay._cached_eid_search_lookup(
            eid,
            allow_sequence_match=allow_sequence_match,
        )
        if cached_search is not None:
            return cached_search
        scope_node, _scope_parent, _scope_idx = self._eid_top_scope_lookup(eid)
        if scope_node is not None:
            node, parent, idx = self._find_node_and_parent(
                scope_node,
                eid,
                allow_sequence_match=allow_sequence_match,
            )
            if node:
                result = NodeLookupResult(node=node, parent=parent, index=idx)
                replay._store_eid_search_cache(
                    eid,
                    allow_sequence_match=allow_sequence_match,
                    result=result,
                )
                return result
            if self._eid_has_strict_top_scope(eid):
                result = NodeLookupResult(node=None, parent=None, index=None)
                replay._store_eid_search_cache(
                    eid,
                    allow_sequence_match=allow_sequence_match,
                    result=result,
                )
                return result
        elif self._eid_has_strict_top_scope(eid):
            result = NodeLookupResult(node=None, parent=None, index=None)
            replay._store_eid_search_cache(
                eid,
                allow_sequence_match=allow_sequence_match,
                result=result,
            )
            return result
        node, parent, idx = self._find_node_and_parent(
            self.statute.body,
            eid,
            allow_sequence_match=allow_sequence_match,
        )
        if node:
            result = NodeLookupResult(node=node, parent=parent, index=idx)
            replay._store_eid_search_cache(
                eid,
                allow_sequence_match=allow_sequence_match,
                result=result,
            )
            return result
        for sched_idx, sched in enumerate(self.statute.supplements):
            if sched.attrs.get("eId") == eid:
                result = NodeLookupResult(node=sched, parent=None, index=sched_idx)
                replay._store_eid_search_cache(
                    eid,
                    allow_sequence_match=allow_sequence_match,
                    result=result,
                )
                return result
            node, parent, idx = self._find_node_and_parent(
                sched,
                eid,
                allow_sequence_match=allow_sequence_match,
            )
            if node:
                result = NodeLookupResult(node=node, parent=parent, index=idx)
                replay._store_eid_search_cache(
                    eid,
                    allow_sequence_match=allow_sequence_match,
                    result=result,
                )
                return result
        result = NodeLookupResult(node=None, parent=None, index=None)
        replay._store_eid_search_cache(
            eid,
            allow_sequence_match=allow_sequence_match,
            result=result,
        )
        return result

    def _find_node_and_parent(
        self,
        node: UKMutableNode,
        eid: str,
        *,
        allow_sequence_match: bool = True,
        target_seq: tuple[str, ...] | None = None,
        suffix_eids: tuple[str, str] | None = None,
    ) -> NodeLookupResult:
        if target_seq is None:
            target_seq = _get_id_sequence(eid)
        if suffix_eids is None:
            suffix_eids = (f"-{eid}", f"_{eid}")
        for i, child in enumerate(node.children):
            c_eid = child.attrs.get("eId") or child.attrs.get("id")
            if c_eid:
                if c_eid == eid:
                    return NodeLookupResult(node=child, parent=node, index=i)
                if c_eid.endswith(suffix_eids):
                    return NodeLookupResult(node=child, parent=node, index=i)
                if allow_sequence_match and _get_id_sequence(c_eid) == target_seq:
                    return NodeLookupResult(node=child, parent=node, index=i)
            if not child.children:
                continue
            res_node, res_parent, res_idx = self._find_node_and_parent(
                child,
                eid,
                allow_sequence_match=allow_sequence_match,
                target_seq=target_seq,
                suffix_eids=suffix_eids,
            )
            if res_node:
                return NodeLookupResult(node=res_node, parent=res_parent, index=res_idx)
        return NodeLookupResult(node=None, parent=None, index=None)

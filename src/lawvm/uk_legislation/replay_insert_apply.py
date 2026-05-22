"""UK replay insertion routing and EID lookup helpers."""

from __future__ import annotations

from typing import Optional, cast

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
from lawvm.uk_legislation.mutable_ir import UKMutableNode, UKMutableStatute, uk_insert_child_sorted, uk_replace_children
from lawvm.uk_legislation.ordering import _label_sort_key
from lawvm.uk_legislation.provenance_notes import (
    _schedule_list_entry_selector,
    _schedule_list_entry_table_rows_selector,
    _schedule_table_end_rows_selector,
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
from lawvm.uk_legislation.replay_target_gaps import (
    uk_crossheading_insert_target_gap,
    uk_existing_target_insert_already_materialized,
    uk_existing_target_insert_conflict_detail,
    uk_existing_target_insert_gap,
)
from lawvm.uk_legislation.target_anchors import _body_target_eid_suffixes, uk_match_kind_label
from lawvm.uk_legislation.uk_grafter import _clean_num


class UKReplayInsertApplyMixin:
    statute: UKMutableStatute
    eid_map: dict[str, str]

    def _apply_insert_op(
        self,
        op: LegalOperation,
        target: LegalAddress,
        node: UKMutableNode | None,
        insert_existing_target_resolution: str,
    ) -> None:
        if op.payload is not None:
            if uk_crossheading_insert_target_gap(target, op):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
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
                        self.adjudications_out,
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
                        self.adjudications_out,
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
                    self.adjudications_out,
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
                self._record_invariant_violations(op)
                self._emit_top_section_snapshot(op)
            else:
                if _schedule_list_entry_table_rows_selector(op) is not None:
                    return
                if _schedule_table_end_rows_selector(op) is not None:
                    return
                if _schedule_list_entry_selector(op) is not None:
                    return
                if _table_column_insert_selector(op) is not None:
                    return
                if _table_row_insert_selector(op) is not None:
                    return
                if self._malformed_target_gap(target):
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind=self._malformed_target_gap_kind(target),
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
                if self._missing_parent_shape_gap(target):
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind=self._missing_parent_shape_gap_kind(target),
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
                if self._schedule_paragraph_carrier_gap(target):
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind=self._schedule_paragraph_carrier_gap_kind(target),
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
                if self._leading_blank_subparagraph_gap(target):
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
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
                if self._missing_sibling_range_gap(target):
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
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
                if self._empty_descendant_shape_gap(target):
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
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
                    self.adjudications_out,
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
                self.adjudications_out,
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
        from lawvm.uk_legislation.canonicalize import (
            uk_insert_into_children,
            uk_resolve_insertion_parent,
        )

        schedule_list_entry_table_rows_selector = _schedule_list_entry_table_rows_selector(op)
        if schedule_list_entry_table_rows_selector is not None:
            return self._insert_schedule_list_entry_table_rows(
                target,
                new_node,
                op,
                schedule_list_entry_table_rows_selector,
            )
        schedule_table_end_rows_selector = _schedule_table_end_rows_selector(op)
        if schedule_table_end_rows_selector is not None:
            return self._insert_schedule_list_entry_table_rows(
                target,
                new_node,
                op,
                schedule_table_end_rows_selector,
            )
        schedule_list_entry_selector = _schedule_list_entry_selector(op)
        if schedule_list_entry_selector is not None:
            return self._insert_schedule_list_entry(target, new_node, op, schedule_list_entry_selector)
        table_column_insert_selector = _table_column_insert_selector(op)
        if table_column_insert_selector is not None:
            return self._insert_table_column(target, new_node, op, table_column_insert_selector)
        table_row_insert_selector = _table_row_insert_selector(op)
        if table_row_insert_selector is not None:
            return self._insert_table_entry_row(target, new_node, op, table_row_insert_selector)

        prec_eid = _preceding_eid(op)
        following_eid = _following_eid(op)
        parent_node, insert_idx = uk_resolve_insertion_parent(
            target=target,
            body_root=cast(IRNode, self.statute.body),
            node_kind=str(new_node.kind),
            node_label=new_node.label,
            preceding_eid=prec_eid,
            following_eid=following_eid,
            find_node_by_target=self._find_node_by_target,
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
            new_node = _inherit_parent_local_eid(parent_node, new_node)
            self._log(f"  EXECUTOR: inserting {new_node.kind} {new_node.label} at routed index {insert_idx}")
            children = list(parent_node.children)
            children.insert(insert_idx, new_node)
            uk_replace_children(parent_node, children)
            return True
        if parent_node:
            new_node = _inherit_parent_local_eid(parent_node, new_node)
            self._log(
                f"  EXECUTOR: inserting {new_node.kind} {new_node.label} into {parent_node.kind} {parent_node.label}"
            )
            return uk_insert_child_sorted(parent_node, new_node)

        # Build parent address by dropping the last path segment.
        # Single-segment paths (e.g. section:2a) get parent = body/schedules directly,
        # matching the old IRTargetRef behaviour where parent_target.section=None caused
        # _find_node_by_target to return the body node for non-schedule containers.
        container = _addr_container(target)
        parent_addr = target.parent() if len(target.path) > 1 else None

        if parent_addr is not None:
            p_node, _, _ = self._find_node_by_target(parent_addr)
            if p_node:
                new_node = _inherit_parent_local_eid(p_node, new_node)
                self._log(f"  EXECUTOR: inserting {new_node.kind} {new_node.label} into {p_node.kind} {p_node.label}")
                return uk_insert_child_sorted(p_node, new_node)
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
                self._log(f"  EXECUTOR: inserting schedule {new_node.label} at top-level")
                return self._insert_supplement_sorted(new_node)
            if new_kind in _sch_structural:
                sch_node, _, _ = self._find_node_by_target(target)
                if sch_node:
                    sch_node = cast(UKMutableNode, sch_node)
                    new_node = _inherit_parent_local_eid(sch_node, new_node)
                    self._log(f"  EXECUTOR: inserting {new_node.kind} {new_node.label} into schedule {sch_node.label}")
                    return uk_insert_child_sorted(sch_node, new_node)
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
                self._log(f"  EXECUTOR: inserting {new_node.kind} {new_node.label} after body predecessor {pred_label}")
                children: list[UKMutableNode] = list(pred_parent.children)
                children.insert(pred_idx + 1, new_node)
                uk_replace_children(pred_parent, children)
                return True

            # No suitable predecessor exists in the body tree: fall back to a
            # true body-root insertion.
            self._log(f"  EXECUTOR: inserting {new_node.kind} {new_node.label} into body (top-level)")
            body_children: list[UKMutableNode] = list(self.statute.body.children)
            uk_insert_into_children(
                cast(list[IRNode], body_children),
                cast(IRNode, new_node),
                label_sort_key=_label_sort_key,
            )
            self.statute.body.children = body_children
            return True

        if "-" in target_eid:
            parent_eid = "-".join(target_eid.split("-")[:-1])
            p_node, _, _ = self._find_node_and_parent_statute(parent_eid)
            if p_node:
                new_node = _inherit_parent_local_eid(p_node, new_node)
                self._log(f"  EXECUTOR: inserting {new_node.kind} {new_node.label} into parent {parent_eid}")
                return uk_insert_child_sorted(cast(UKMutableNode, p_node), new_node)

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
            self._log(
                "  EXECUTOR: WARN refusing impossible body-root fallback for "
                f"{new_node.kind} {new_node.label} target {target}"
            )
            return False
        self._log(f"  EXECUTOR: fallback inserting {new_node.kind} {new_node.label} into body")
        _append_uk_replay_adjudication(
            self.adjudications_out,
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
                payload_kind=str(new_node.kind),
                payload_label=new_node.label or "",
                derived_target_eid=target_eid,
            ),
        )
        if new_kind == "schedule":
            supplements = list(self.statute.supplements)
            supplements.append(new_node)
            self._replace_statute(supplements=supplements)
            return True
        else:
            body_children: list[UKMutableNode] = list(self.statute.body.children)
            uk_insert_into_children(
                cast(list[IRNode], body_children),
                cast(IRNode, new_node),
                label_sort_key=_label_sort_key,
            )
            self.statute.body.children = body_children
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
            paragraph, subsection, item_labels = _schedule_target_levels(addr)
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

    def _find_node_and_parent_statute(
        self,
        eid: str,
        *,
        allow_sequence_match: bool = True,
    ) -> tuple[Optional[UKMutableNode], Optional[UKMutableNode], Optional[int]]:
        node, parent, idx = self._find_node_and_parent(
            self.statute.body,
            eid,
            allow_sequence_match=allow_sequence_match,
        )
        if node:
            return node, parent, idx
        for sched_idx, sched in enumerate(self.statute.supplements):
            if sched.attrs.get("eId") == eid:
                return sched, None, sched_idx
            node, parent, idx = self._find_node_and_parent(
                sched,
                eid,
                allow_sequence_match=allow_sequence_match,
            )
            if node:
                return node, parent, idx
        return None, None, None

    def _find_node_and_parent(
        self,
        node: UKMutableNode,
        eid: str,
        *,
        allow_sequence_match: bool = True,
        target_seq: tuple[str, ...] | None = None,
    ) -> tuple[Optional[UKMutableNode], Optional[UKMutableNode], Optional[int]]:
        if target_seq is None:
            target_seq = _get_id_sequence(eid)
        for i, child in enumerate(node.children):
            c_eid = child.attrs.get("eId") or child.attrs.get("id")
            if c_eid:
                if c_eid == eid:
                    return child, node, i
                if c_eid.endswith("-" + eid) or c_eid.endswith("_" + eid):
                    return child, node, i
                if allow_sequence_match and _get_id_sequence(c_eid) == target_seq:
                    return child, node, i
            res_node, res_parent, res_idx = self._find_node_and_parent(
                child,
                eid,
                allow_sequence_match=allow_sequence_match,
                target_seq=target_seq,
            )
            if res_node:
                return res_node, res_parent, res_idx
        return None, None, None

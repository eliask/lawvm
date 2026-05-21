"""UK replay structural replacement action branch."""

from __future__ import annotations

from lawvm.core.ir import LegalAddress, LegalOperation
from lawvm.uk_legislation.addressing import _action_name, _addr_leaf_kind, _addr_leaf_label
from lawvm.uk_legislation.canonicalize import uk_kind_matches
from lawvm.uk_legislation.mutable_ir import UKMutableNode, uk_replace_text
from lawvm.uk_legislation.provenance_notes import _schedule_list_entry_replace_selector
from lawvm.uk_legislation.replay_records import _append_uk_replay_adjudication
from lawvm.uk_legislation.replay_schedule_list_apply import (
    _UK_REPLAY_SCHEDULE_LIST_ENTRY_REPLACE_UNRESOLVED_RULE_ID,
)
from lawvm.uk_legislation.substitution_metadata import (
    UK_SOURCE_LABEL_CHANGING_SUBSTITUTION_RULE_ID,
)
from lawvm.uk_legislation.text_rewrite_fragments import _fragment_substitution
from lawvm.uk_legislation.uk_grafter import _clean_num


_UK_REPLAY_SOURCE_LABEL_CHANGING_SUBSTITUTION_RESOLVED_RULE_ID = (
    "uk_replay_source_label_changing_substitution_resolved"
)


class UKReplayReplaceApplyMixin:

    def _apply_replace_op(
        self,
        op: LegalOperation,
        target: LegalAddress,
        node: UKMutableNode | None,
        parent: UKMutableNode | None,
        idx: int | None,
        target_found: bool,
    ) -> None:
        schedule_list_entry_replace_selector = _schedule_list_entry_replace_selector(op)
        if schedule_list_entry_replace_selector is not None:
            if op.payload is None:
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind=_UK_REPLAY_SCHEDULE_LIST_ENTRY_REPLACE_UNRESOLVED_RULE_ID,
                    message=(
                        "UK replay skipped schedule-list-entry replacement: "
                        "replacement payload was missing."
                    ),
                    op=op,
                    detail={
                        "target": str(target),
                        "selector": dict(schedule_list_entry_replace_selector),
                        "reason_code": "payload_missing",
                        "family": "source_schedule_list_entry_elaboration",
                        "blocking": True,
                        "strict_disposition": "block",
                        "quirks_disposition": "record",
                    },
                )
                return
            new_node = UKMutableNode.from_dict(op.payload.to_jsonable_dict())
            if self._replace_schedule_list_entry(
                target,
                new_node,
                op,
                schedule_list_entry_replace_selector,
            ):
                self._record_invariant_violations(op)
                self._emit_top_section_snapshot(op)
            return
        frag_subs = _fragment_substitution(op)
        if frag_subs is not None:
            if node:
                self._log(f"  EXECUTOR: substituting text in {node.kind} {node.label}")
                self._apply_text_substitution_on_node(node, frag_subs)
                self._record_invariant_violations(op)
            else:
                if self._malformed_target_gap(target):
                    kind = self._malformed_target_gap_kind(target)
                    message = "UK replay skipped replace: lowered target path is malformed."
                elif self._missing_parent_shape_gap(target):
                    kind = self._missing_parent_shape_gap_kind(target)
                    message = "UK replay skipped replace: immediate parent target path is structurally absent."
                elif self._missing_sectionlike_gap(target):
                    kind = "uk_replay_missing_sectionlike_range_gap"
                    message = "UK replay skipped replace: target falls inside an absent sectionlike range gap."
                else:
                    kind = "uk_replay_target_not_found"
                    message = "UK replay skipped replace: target not found."
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind=str(kind),
                    message=message,
                    op=op,
                    detail={"action": _action_name(op.action), "target": str(target)},
                )
        elif op.payload is not None:
            # Clone payload so repeated ops don't share state
            new_node = UKMutableNode.from_dict(op.payload.to_jsonable_dict())
            if node:
                node_kind = str(node.kind).lower()
                new_kind = str(new_node.kind).lower()
                if node_kind != "content" and new_kind != "content":
                    label_changing_substitution = (
                        op.witness_rule_id == UK_SOURCE_LABEL_CHANGING_SUBSTITUTION_RULE_ID
                    )
                    existing_eid = str(node.attrs.get("eId") or node.attrs.get("id") or "")
                    if existing_eid and not label_changing_substitution:
                        new_node.attrs["eId"] = existing_eid
                    if parent and idx is not None:
                        self._replace_node_in_statute(node, new_node)
                        if label_changing_substitution:
                            _append_uk_replay_adjudication(
                                self.adjudications_out,
                                kind=_UK_REPLAY_SOURCE_LABEL_CHANGING_SUBSTITUTION_RESOLVED_RULE_ID,
                                message=(
                                    "UK replay applied a source-owned label-changing "
                                    "substitution by replacing the old sibling with "
                                    "the new labelled payload."
                                ),
                                op=op,
                                detail={
                                    "target": str(target),
                                    "source_label": str(node.label or ""),
                                    "replacement_label": str(new_node.label or ""),
                                    "family": "lineage_normalization",
                                    "blocking": False,
                                    "strict_disposition": "record",
                                    "quirks_disposition": "record",
                                },
                            )
                        self._record_invariant_violations(op)
                    elif idx is not None and node in self.statute.supplements:
                        self._replace_node_in_statute(node, new_node)
                        if label_changing_substitution:
                            _append_uk_replay_adjudication(
                                self.adjudications_out,
                                kind=_UK_REPLAY_SOURCE_LABEL_CHANGING_SUBSTITUTION_RESOLVED_RULE_ID,
                                message=(
                                    "UK replay applied a source-owned label-changing "
                                    "substitution by replacing the old sibling with "
                                    "the new labelled payload."
                                ),
                                op=op,
                                detail={
                                    "target": str(target),
                                    "source_label": str(node.label or ""),
                                    "replacement_label": str(new_node.label or ""),
                                    "family": "lineage_normalization",
                                    "blocking": False,
                                    "strict_disposition": "record",
                                    "quirks_disposition": "record",
                                },
                            )
                        self._record_invariant_violations(op)
                elif node_kind != "content" and new_kind == "content":
                    uk_replace_text(node, new_node.text)
                else:
                    existing_eid = str(node.attrs.get("eId") or node.attrs.get("id") or "")
                    if existing_eid:
                        new_node.attrs["eId"] = existing_eid
                    if parent and idx is not None:
                        self._replace_node_in_statute(node, new_node)
                        self._record_invariant_violations(op)
            elif self._recover_source_carried_structured_tail_substitution(op, target, new_node):
                self._record_invariant_violations(op)
                self._emit_top_section_snapshot(op)
            elif uk_kind_matches(
                node_kind=str(new_node.kind),
                target_kind=_addr_leaf_kind(op.target) or "",
                node_label=_clean_num(new_node.label or ""),
                target_label=_clean_num(_addr_leaf_label(op.target) or ""),
            ) and _clean_num(new_node.label or "") == _clean_num(_addr_leaf_label(op.target) or ""):
                # Some UK replace ops target a node that is missing from the
                # base shape but present in the commensurable oracle shape
                # (for example a collapsed section lead becoming an explicit
                # subsection 1). If the replacement payload already matches
                # the missing target leaf exactly, materialize it under the
                # parent instead of silently dropping the replace.
                leaf_kind = str(_addr_leaf_kind(op.target) or "").lower()
                parent_target = LegalAddress(path=target.path[:-1], special=None)
                parent_node, _, _ = self._find_node_by_target(parent_target)
                inserted = False
                if parent_node is not None and leaf_kind not in {"subparagraph", "item", "point"}:
                    inserted = self._insert_node_v2(op.target, new_node, op)
                if inserted:
                    self._record_invariant_violations(op)
                    self._emit_top_section_snapshot(op)
                else:
                    if self._malformed_target_gap(target):
                        _append_uk_replay_adjudication(
                            self.adjudications_out,
                            kind=self._malformed_target_gap_kind(target),
                            message="UK replay skipped replace: lowered target path is malformed.",
                            op=op,
                            detail={
                                "action": _action_name(op.action),
                                "target": str(target),
                                "payload_kind": str(new_node.kind),
                                "payload_label": new_node.label or "",
                            },
                        )
                        return
                    if self._missing_parent_shape_gap(target):
                        _append_uk_replay_adjudication(
                            self.adjudications_out,
                            kind=self._missing_parent_shape_gap_kind(target),
                            message="UK replay skipped replace: immediate parent target path is structurally absent.",
                            op=op,
                            detail={
                                "action": _action_name(op.action),
                                "target": str(target),
                                "payload_kind": str(new_node.kind),
                                "payload_label": new_node.label or "",
                            },
                        )
                        return
                    if self._schedule_paragraph_carrier_gap(target):
                        _append_uk_replay_adjudication(
                            self.adjudications_out,
                            kind=self._schedule_paragraph_carrier_gap_kind(target),
                            message="UK replay skipped replace: schedule paragraph carrier is structurally absent or wrapped.",
                            op=op,
                            detail={
                                "action": _action_name(op.action),
                                "target": str(target),
                                "payload_kind": str(new_node.kind),
                                "payload_label": new_node.label or "",
                            },
                        )
                        return
                    if self._leading_blank_subparagraph_gap(target):
                        _append_uk_replay_adjudication(
                            self.adjudications_out,
                            kind="uk_replay_absent_sibling_range_gap",
                            message="UK replay skipped replace: target falls inside an absent leading numeric subparagraph gap under blank schedule placeholders.",
                            op=op,
                            detail={
                                "action": _action_name(op.action),
                                "target": str(target),
                                "payload_kind": str(new_node.kind),
                                "payload_label": new_node.label or "",
                            },
                        )
                        return
                    if self._missing_sibling_range_gap(target):
                        _append_uk_replay_adjudication(
                            self.adjudications_out,
                            kind="uk_replay_absent_sibling_range_gap",
                            message="UK replay skipped replace: target falls inside an absent sibling range under the parent path.",
                            op=op,
                            detail={
                                "action": _action_name(op.action),
                                "target": str(target),
                                "payload_kind": str(new_node.kind),
                                "payload_label": new_node.label or "",
                            },
                        )
                        return
                    if self._empty_descendant_shape_gap(target):
                        _append_uk_replay_adjudication(
                            self.adjudications_out,
                            kind="uk_replay_empty_descendant_shape_gap",
                            message="UK replay skipped replace: parent target exists but has no descendant structural shape.",
                            op=op,
                            detail={
                                "action": _action_name(op.action),
                                "target": str(target),
                                "payload_kind": str(new_node.kind),
                                "payload_label": new_node.label or "",
                            },
                        )
                        return
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind="uk_replay_payload_mismatch",
                        message="UK replay skipped replace: payload could not be inserted by target path.",
                        op=op,
                        detail={
                            "action": _action_name(op.action),
                            "target": str(target),
                            "payload_kind": str(new_node.kind),
                            "payload_label": new_node.label or "",
                        },
                    )
            else:
                if _addr_leaf_kind(op.target) and (
                    str(new_node.kind or "").lower() != str(_addr_leaf_kind(op.target) or "").lower()
                    or _clean_num(new_node.label or "") != _clean_num(_addr_leaf_label(op.target) or "")
                ):
                    kind = "uk_replay_replace_payload_target_leaf_mismatch_gap"
                    message = "UK replay skipped replace: payload does not match lowered target leaf."
                elif self._malformed_target_gap(target):
                    kind = self._malformed_target_gap_kind(target)
                    message = "UK replay skipped replace: lowered target path is malformed."
                elif self._missing_parent_shape_gap(target):
                    kind = self._missing_parent_shape_gap_kind(target)
                    message = "UK replay skipped replace: immediate parent target path is structurally absent."
                elif self._missing_sectionlike_gap(target):
                    kind = "uk_replay_missing_sectionlike_range_gap"
                    message = "UK replay skipped replace: target falls inside an absent sectionlike range gap."
                else:
                    kind = "uk_replay_target_not_found"
                    message = "UK replay skipped replace: target not found."
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind=str(kind),
                    message=message,
                    op=op,
                    detail={"action": _action_name(op.action), "target": str(target)},
                )
        else:
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind="uk_replay_payload_missing",
                message="UK replay skipped replace: payload missing.",
                op=op,
                detail={"action": _action_name(op.action), "target": str(target)},
            )
        if target_found or node is not None:
            self._emit_top_section_snapshot(op)

"""UK replay structural replacement action branch."""

from __future__ import annotations

from dataclasses import replace as dc_replace
from typing import Any, Protocol

from lawvm.core.ir import LegalAddress, LegalOperation
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.uk_legislation.addressing import _addr_leaf_kind, _addr_leaf_label
from lawvm.uk_legislation.canonicalize import uk_kind_matches
from lawvm.uk_legislation.mutable_ir import UKMutableNode, UKMutableStatute
from lawvm.uk_legislation.provenance_notes import (
    _schedule_list_entry_replace_selector,
    _table_row_replace_selector,
)
from lawvm.uk_legislation.replay_records import (
    _append_uk_replay_adjudication,
    uk_replay_action_target_detail,
    uk_replay_blocking_action_target_detail,
    uk_replay_recovery_action_target_detail,
)
from lawvm.uk_legislation.replay_schedule_list_apply import (
    _UK_REPLAY_SCHEDULE_LIST_ENTRY_REPLACE_UNRESOLVED_RULE_ID,
)
from lawvm.uk_legislation.replay_state import NodeLookupResult
from lawvm.uk_legislation.source_definition_structural_insert import (
    UK_DEFINITION_CHILD_STRUCTURAL_SUBSTITUTION_RULE_ID,
)
from lawvm.uk_legislation.substitution_metadata import (
    UK_SOURCE_LABEL_CHANGING_SUBSTITUTION_RULE_ID,
)
from lawvm.uk_legislation.text_rewrite_fragments import _fragment_substitution
from lawvm.uk_legislation.uk_grafter import _clean_num


_UK_REPLAY_SOURCE_LABEL_CHANGING_SUBSTITUTION_RESOLVED_RULE_ID = (
    "uk_replay_source_label_changing_substitution_resolved"
)
_UK_REPLAY_REPLACE_MATERIALIZED_AS_INSERT_FOR_MISSING_LEAF_RULE_ID = (
    "uk_replay_replace_materialized_as_insert_for_missing_leaf"
)


class _ReplaceReplaySelf(Protocol):
    statute: UKMutableStatute
    adjudications_out: list[CompileAdjudication]

    def _find_node_by_target(
        self,
        target: LegalAddress,
        *,
        allow_compound_subsection_alias: bool = False,
        allow_recursive_match: bool = True,
        target_resolution_op: LegalOperation | None = None,
    ) -> NodeLookupResult: ...

    def _replace_node_in_statute(self, old_node: UKMutableNode, new_node: UKMutableNode) -> bool: ...

    def _replace_schedule_list_entry(
        self,
        target: LegalAddress,
        new_node: UKMutableNode,
        op: LegalOperation,
        selector: dict[str, Any],
    ) -> bool: ...

    def _replace_table_entry_rows(
        self,
        target: LegalAddress,
        new_node: UKMutableNode,
        op: LegalOperation,
        selector: dict[str, Any],
    ) -> bool: ...

    def _record_invariant_violations(self, op: LegalOperation) -> None: ...

    def _emit_top_section_snapshot(self, op: LegalOperation) -> None: ...

    def _log(self, message: str) -> None: ...

    def _apply_text_substitution_on_node(
        self,
        node: UKMutableNode,
        subs: list[dict[str, Any]],
    ) -> tuple[UKMutableNode, tuple[dict[str, Any], ...]]: ...

    def _malformed_target_gap(self, target: LegalAddress) -> bool: ...

    def _malformed_target_gap_kind(self, target: LegalAddress) -> str: ...

    def _missing_parent_shape_gap(self, target: LegalAddress) -> bool: ...

    def _missing_parent_shape_gap_kind(self, target: LegalAddress) -> str: ...

    def _missing_sectionlike_gap(self, target: LegalAddress) -> bool: ...

    def _recover_source_carried_structured_tail_substitution(
        self,
        op: LegalOperation,
        target: LegalAddress,
        new_node: UKMutableNode,
    ) -> bool: ...

    def _insert_node_v2(
        self,
        target: LegalAddress,
        new_node: UKMutableNode,
        op: LegalOperation,
    ) -> bool: ...

    def _schedule_paragraph_carrier_gap(self, target: LegalAddress) -> bool: ...

    def _schedule_paragraph_carrier_gap_kind(self, target: LegalAddress) -> str: ...

    def _leading_blank_subparagraph_gap(self, target: LegalAddress) -> bool: ...

    def _missing_sibling_range_gap(self, target: LegalAddress) -> bool: ...

    def _empty_descendant_shape_gap(self, target: LegalAddress) -> bool: ...

    def _replace_definition_child_structural_substitution(
        self,
        target: LegalAddress,
        new_node: UKMutableNode,
        op: LegalOperation,
    ) -> bool: ...


def _source_label_changing_substitution_detail(
    op: LegalOperation,
    target: LegalAddress,
    node: UKMutableNode,
    new_node: UKMutableNode,
) -> dict[str, object]:
    return uk_replay_action_target_detail(
        op,
        target,
        blocking=False,
        source_label=str(node.label or ""),
        replacement_label=str(new_node.label or ""),
        family="lineage_normalization",
    )


class UKReplayReplaceApplyMixin:
    def _replace_definition_child_structural_substitution(
        self: _ReplaceReplaySelf,
        target: LegalAddress,
        new_node: UKMutableNode,
        op: LegalOperation,
    ) -> bool:
        if new_node.attrs.get("source_rule_id") != UK_DEFINITION_CHILD_STRUCTURAL_SUBSTITUTION_RULE_ID:
            return False
        if len(target.path) < 2:
            return False
        definition_term = " ".join(str(new_node.attrs.get("definition_term") or "").split()).strip()
        child_label = _clean_num(str(new_node.attrs.get("definition_child_label") or ""))
        target_label = _clean_num(_addr_leaf_label(target) or "")
        if not definition_term or not child_label or child_label != target_label:
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind="uk_replay_definition_child_structural_substitution_target_gap",
                message=(
                    "UK replay skipped definition-child structural substitution: "
                    "payload identity did not match the lowered target."
                ),
                op=op,
                detail=uk_replay_blocking_action_target_detail(
                    op,
                    target,
                    definition_term=definition_term,
                    child_label=child_label,
                    target_label=target_label,
                    strict_disposition="block",
                ),
            )
            return True
        parent_target = LegalAddress(path=target.path[:-1], special=None)
        parent_node, _, _ = self._find_node_by_target(parent_target)
        if parent_node is None:
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind="uk_replay_definition_child_structural_substitution_parent_gap",
                message=(
                    "UK replay skipped definition-child structural substitution: "
                    "definition child parent target was absent."
                ),
                op=op,
                detail=uk_replay_blocking_action_target_detail(
                    op,
                    target,
                    definition_term=definition_term,
                    child_label=child_label,
                    strict_disposition="block",
                ),
            )
            return True

        matches: list[UKMutableNode] = []
        stack = list(parent_node.children)
        while stack:
            current = stack.pop()
            if (
                current.kind == new_node.kind
                and _clean_num(current.attrs.get("definition_child_label") or "") == child_label
                and " ".join(str(current.attrs.get("definition_term") or "").split()).strip().lower()
                == definition_term.lower()
            ):
                matches.append(current)
            stack.extend(reversed(current.children))
        if len(matches) != 1:
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind="uk_replay_definition_child_structural_substitution_target_gap",
                message=(
                    "UK replay skipped definition-child structural substitution: "
                    "definition child target did not resolve uniquely."
                ),
                op=op,
                detail=uk_replay_blocking_action_target_detail(
                    op,
                    target,
                    definition_term=definition_term,
                    child_label=child_label,
                    match_count=len(matches),
                    strict_disposition="block",
                ),
            )
            return True
        existing = matches[0]
        existing_eid = str(existing.attrs.get("eId") or existing.attrs.get("id") or "")
        if existing_eid:
            new_node.attrs["eId"] = existing_eid
        self._replace_node_in_statute(existing, new_node)
        _append_uk_replay_adjudication(
            self.adjudications_out,
            kind="uk_replay_definition_child_structural_substitution_applied",
            message=(
                "UK replay replaced a source-owned definition child with a "
                "structured payload scoped by definition term and child label."
            ),
            op=op,
            detail=uk_replay_action_target_detail(
                op,
                target,
                blocking=False,
                definition_term=definition_term,
                child_label=child_label,
                payload_child_count=len(new_node.children),
            ),
        )
        return True

    def _apply_replace_op(
        self: _ReplaceReplaySelf,
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
                    detail=uk_replay_blocking_action_target_detail(
                        op,
                        target,
                        selector=dict(schedule_list_entry_replace_selector),
                        reason_code="payload_missing",
                        family="source_schedule_list_entry_elaboration",
                    ),
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
        table_row_replace_selector = _table_row_replace_selector(op)
        if table_row_replace_selector is not None:
            if op.payload is None:
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_table_entry_row_replace_unresolved",
                    message=(
                        "UK replay skipped table-entry row replacement: "
                        "replacement payload was missing."
                    ),
                    op=op,
                    detail=uk_replay_blocking_action_target_detail(
                        op,
                        target,
                        selector=dict(table_row_replace_selector),
                        reason_code="payload_missing",
                        family="source_table_elaboration",
                    ),
                )
                return
            new_node = UKMutableNode.from_dict(op.payload.to_jsonable_dict())
            if self._replace_table_entry_rows(
                target,
                new_node,
                op,
                table_row_replace_selector,
            ):
                self._record_invariant_violations(op)
                self._emit_top_section_snapshot(op)
            return
        frag_subs = _fragment_substitution(op)
        if frag_subs is not None:
            if node:
                self._log(f"  EXECUTOR: substituting text in {node.kind} {node.label}")
                _, substitution_observations = self._apply_text_substitution_on_node(node, frag_subs)
                for observation in substitution_observations:
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind="uk_replay_fragment_substitution_child_range_deleted",
                        message=(
                            "UK replay applied a fragment-substitution child range "
                            "deletion after resolving both labelled child endpoints."
                        ),
                        op=op,
                        detail=uk_replay_action_target_detail(
                            op,
                            target,
                            blocking=False,
                            family="text_rewrite_recovery",
                            **observation,
                        ),
                    )
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
                    detail=uk_replay_blocking_action_target_detail(op, target),
                )
        elif op.payload is not None:
            # Clone payload so repeated ops don't share state
            new_node = UKMutableNode.from_dict(op.payload.to_jsonable_dict())
            if self._replace_definition_child_structural_substitution(target, new_node, op):
                self._record_invariant_violations(op)
                self._emit_top_section_snapshot(op)
                return
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
                                detail=_source_label_changing_substitution_detail(
                                    op,
                                    target,
                                    node,
                                    new_node,
                                ),
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
                                detail=_source_label_changing_substitution_detail(
                                    op,
                                    target,
                                    node,
                                    new_node,
                                ),
                            )
                        self._record_invariant_violations(op)
                elif node_kind != "content" and new_kind == "content":
                    self._replace_node_in_statute(node, dc_replace(node, text=new_node.text))
                    self._record_invariant_violations(op)
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
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind=_UK_REPLAY_REPLACE_MATERIALIZED_AS_INSERT_FOR_MISSING_LEAF_RULE_ID,
                        message=(
                            "UK replay materialized a REPLACE op as an INSERT because the "
                            "target leaf was absent from the base shape but the replacement "
                            "payload matched the target leaf kind and label exactly."
                        ),
                        op=op,
                        detail=uk_replay_recovery_action_target_detail(
                            op,
                            target,
                            family="target_resolution_recovery",
                            leaf_kind=leaf_kind,
                            parent_path=str(parent_target),
                            payload_kind=str(new_node.kind),
                            payload_label=str(new_node.label or ""),
                        ),
                    )
                    self._record_invariant_violations(op)
                    self._emit_top_section_snapshot(op)
                else:
                    if self._malformed_target_gap(target):
                        _append_uk_replay_adjudication(
                            self.adjudications_out,
                            kind=self._malformed_target_gap_kind(target),
                            message="UK replay skipped replace: lowered target path is malformed.",
                            op=op,
                            detail=uk_replay_blocking_action_target_detail(
                                op,
                                target,
                                payload_kind=str(new_node.kind),
                                payload_label=new_node.label or "",
                            ),
                        )
                        return
                    if self._missing_parent_shape_gap(target):
                        _append_uk_replay_adjudication(
                            self.adjudications_out,
                            kind=self._missing_parent_shape_gap_kind(target),
                            message="UK replay skipped replace: immediate parent target path is structurally absent.",
                            op=op,
                            detail=uk_replay_blocking_action_target_detail(
                                op,
                                target,
                                payload_kind=str(new_node.kind),
                                payload_label=new_node.label or "",
                            ),
                        )
                        return
                    if self._schedule_paragraph_carrier_gap(target):
                        _append_uk_replay_adjudication(
                            self.adjudications_out,
                            kind=self._schedule_paragraph_carrier_gap_kind(target),
                            message="UK replay skipped replace: schedule paragraph carrier is structurally absent or wrapped.",
                            op=op,
                            detail=uk_replay_blocking_action_target_detail(
                                op,
                                target,
                                payload_kind=str(new_node.kind),
                                payload_label=new_node.label or "",
                            ),
                        )
                        return
                    if self._leading_blank_subparagraph_gap(target):
                        _append_uk_replay_adjudication(
                            self.adjudications_out,
                            kind="uk_replay_absent_sibling_range_gap",
                            message="UK replay skipped replace: target falls inside an absent leading numeric subparagraph gap under blank schedule placeholders.",
                            op=op,
                            detail=uk_replay_blocking_action_target_detail(
                                op,
                                target,
                                payload_kind=str(new_node.kind),
                                payload_label=new_node.label or "",
                            ),
                        )
                        return
                    if self._missing_sibling_range_gap(target):
                        _append_uk_replay_adjudication(
                            self.adjudications_out,
                            kind="uk_replay_absent_sibling_range_gap",
                            message="UK replay skipped replace: target falls inside an absent sibling range under the parent path.",
                            op=op,
                            detail=uk_replay_blocking_action_target_detail(
                                op,
                                target,
                                payload_kind=str(new_node.kind),
                                payload_label=new_node.label or "",
                            ),
                        )
                        return
                    if self._empty_descendant_shape_gap(target):
                        _append_uk_replay_adjudication(
                            self.adjudications_out,
                            kind="uk_replay_empty_descendant_shape_gap",
                            message="UK replay skipped replace: parent target exists but has no descendant structural shape.",
                            op=op,
                            detail=uk_replay_blocking_action_target_detail(
                                op,
                                target,
                                payload_kind=str(new_node.kind),
                                payload_label=new_node.label or "",
                            ),
                        )
                        return
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind="uk_replay_payload_mismatch",
                        message="UK replay skipped replace: payload could not be inserted by target path.",
                        op=op,
                        detail=uk_replay_blocking_action_target_detail(
                            op,
                            target,
                            payload_kind=str(new_node.kind),
                            payload_label=new_node.label or "",
                        ),
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
                    detail=uk_replay_blocking_action_target_detail(op, target),
                )
        else:
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind="uk_replay_payload_missing",
                message="UK replay skipped replace: payload missing.",
                op=op,
                detail=uk_replay_blocking_action_target_detail(op, target),
            )
        if target_found or node is not None:
            self._emit_top_section_snapshot(op)

"""UK replay structural repeal action branch."""

from __future__ import annotations

from typing import Any, Optional, Protocol, cast

from lawvm.core.ir import LegalAddress, LegalOperation
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.uk_legislation.heading_facets import _UK_REPLAY_CROSSHEADING_AND_STRUCTURAL_REPEAL_UNRESOLVED_RULE_ID
from lawvm.uk_legislation.mutable_ir import UKMutableNode, UKMutableStatute
from lawvm.uk_legislation.provenance_notes import _crossheading_group_repeal_selector, _schedule_list_entry_repeal_selector
from lawvm.uk_legislation.replay_records import (
    _append_uk_replay_adjudication,
    uk_replay_action_target_detail,
    uk_replay_blocking_action_target_detail,
)
from lawvm.uk_legislation.replay_state import NodeLookupResult
from lawvm.uk_legislation.replay_target_gaps import uk_missing_source_target_gap


class _RepealReplaySelf(Protocol):
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

    def _repeal_schedule_list_entries(
        self,
        target: LegalAddress,
        op: LegalOperation,
        selector: dict[str, Any],
    ) -> bool: ...

    def _record_invariant_violations(self, op: LegalOperation) -> None: ...

    def _emit_top_section_snapshot(self, op: LegalOperation) -> None: ...

    def _repeal_crossheading_group(
        self,
        target: LegalAddress,
        node: UKMutableNode,
        parent: Optional[UKMutableNode],
        op: LegalOperation,
        selector: dict[str, Any],
    ) -> bool: ...

    def _target_under_repealed_prefix(self, target: LegalAddress) -> bool: ...

    def _doubled_alpha_gap(self, target: LegalAddress) -> bool: ...

    def _malformed_target_gap(self, target: LegalAddress) -> bool: ...

    def _malformed_target_gap_kind(self, target: LegalAddress) -> str: ...

    def _missing_sibling_range_gap(self, target: LegalAddress) -> bool: ...

    def _empty_descendant_shape_gap(self, target: LegalAddress) -> bool: ...

    def _missing_sectionlike_gap(self, target: LegalAddress) -> bool: ...

    def _missing_schedule_branch_gap(self, target: LegalAddress) -> bool: ...

    def _missing_schedule_root_gap(self, target: LegalAddress) -> bool: ...

    def _missing_parent_shape_gap(self, target: LegalAddress) -> bool: ...

    def _missing_parent_shape_gap_kind(self, target: LegalAddress) -> str: ...

    def _schedule_paragraph_carrier_gap(self, target: LegalAddress) -> bool: ...

    def _schedule_paragraph_carrier_gap_kind(self, target: LegalAddress) -> str: ...

    def _leading_blank_subparagraph_gap(self, target: LegalAddress) -> bool: ...

    def _log(self, message: str) -> None: ...

    def _remove_node(
        self,
        node: UKMutableNode,
        parent: Optional[UKMutableNode],
        idx: Optional[int],
    ) -> bool: ...

    def _record_repealed_target(self, target: LegalAddress) -> None: ...


def _repeal_replay_self(replay: object) -> _RepealReplaySelf:
    return cast(_RepealReplaySelf, replay)


class UKReplayRepealApplyMixin:
    def _present_parent_absent_leaf_repeal_gap(self, target: LegalAddress) -> bool:
        replay = _repeal_replay_self(self)
        path = target.path
        if len(path) < 2:
            return False
        leaf_kind = str(path[-1][0] or "").lower()
        leaf_label = str(path[-1][1] or "").strip()
        if leaf_kind not in {"paragraph", "subparagraph", "item", "point"} or not leaf_label:
            return False
        parent_target = LegalAddress(path=path[:-1], special=None)
        parent_node, _, _ = replay._find_node_by_target(parent_target)
        if parent_node is None:
            return False
        for child in getattr(parent_node, "children", ()) or ():
            child_kind = str(getattr(child, "kind", "") or "").lower()
            child_label = str(getattr(child, "label", "") or "").strip().lower()
            if child_kind == leaf_kind and child_label == leaf_label.lower():
                return False
        return bool((getattr(parent_node, "text", "") or "").strip() or getattr(parent_node, "children", ()))

    def _apply_repeal_op(
        self,
        op: LegalOperation,
        target: LegalAddress,
        node: UKMutableNode | None,
        parent: UKMutableNode | None,
        idx: int | None,
    ) -> None:
        replay = _repeal_replay_self(self)
        schedule_list_entry_repeal_selector = _schedule_list_entry_repeal_selector(op)
        if schedule_list_entry_repeal_selector is not None:
            if replay._repeal_schedule_list_entries(target, op, schedule_list_entry_repeal_selector):
                replay._record_invariant_violations(op)
                replay._emit_top_section_snapshot(op)
            return
        crossheading_group_repeal_selector = _crossheading_group_repeal_selector(op)
        if crossheading_group_repeal_selector is not None and node is not None:
            if replay._repeal_crossheading_group(target, node, parent, op, crossheading_group_repeal_selector):
                replay._record_invariant_violations(op)
                replay._emit_top_section_snapshot(op)
            return
        if crossheading_group_repeal_selector is not None:
            _append_uk_replay_adjudication(
                replay.adjudications_out,
                kind=_UK_REPLAY_CROSSHEADING_AND_STRUCTURAL_REPEAL_UNRESOLVED_RULE_ID,
                message=(
                    "UK replay skipped cross-heading group repeal: "
                    "structural target was not found."
                ),
                op=op,
                detail=uk_replay_blocking_action_target_detail(
                    op,
                    target,
                    reason_code="target_not_found",
                    selector=dict(crossheading_group_repeal_selector),
                ),
            )
            return
        if node is None:
            if replay._target_under_repealed_prefix(target):
                _append_uk_replay_adjudication(
                    replay.adjudications_out,
                    kind="uk_replay_repeal_target_already_absent_observed",
                    message=(
                        "UK replay observed a structural repeal whose target path "
                        "was already repealed earlier in the chain."
                    ),
                    op=op,
                    detail=uk_replay_action_target_detail(
                        op,
                        target,
                        blocking=False,
                        reason_code="target_previously_repealed",
                        family="structural_repeal_idempotence",
                    ),
                )
            elif replay._doubled_alpha_gap(target):
                _append_uk_replay_adjudication(
                    replay.adjudications_out,
                    kind="uk_replay_absent_sibling_range_gap",
                    message="UK replay skipped repeal: target falls inside an absent doubled-alpha sibling range under the parent path.",
                    op=op,
                    detail=uk_replay_blocking_action_target_detail(op, target),
                )
            elif replay._malformed_target_gap(target):
                _append_uk_replay_adjudication(
                    replay.adjudications_out,
                    kind=replay._malformed_target_gap_kind(target),
                    message="UK replay skipped repeal: lowered target path is malformed.",
                    op=op,
                    detail=uk_replay_blocking_action_target_detail(op, target),
                )
            elif uk_missing_source_target_gap(op):
                _append_uk_replay_adjudication(
                    replay.adjudications_out,
                    kind="uk_replay_missing_source_target_gap",
                    message="UK replay skipped repeal: target comes from index-only effect row without extracted source text.",
                    op=op,
                    detail=uk_replay_blocking_action_target_detail(op, target),
                )
            elif replay._missing_sibling_range_gap(target):
                _append_uk_replay_adjudication(
                    replay.adjudications_out,
                    kind="uk_replay_absent_sibling_range_gap",
                    message="UK replay skipped repeal: target falls inside an absent sibling range under the parent path.",
                    op=op,
                    detail=uk_replay_blocking_action_target_detail(op, target),
                )
            elif self._present_parent_absent_leaf_repeal_gap(target):
                _append_uk_replay_adjudication(
                    replay.adjudications_out,
                    kind="uk_replay_absent_child_repeal_target_gap",
                    message="UK replay skipped repeal: parent target exists but the repealed child is already absent.",
                    op=op,
                    detail=uk_replay_blocking_action_target_detail(op, target),
                )
            elif replay._empty_descendant_shape_gap(target):
                _append_uk_replay_adjudication(
                    replay.adjudications_out,
                    kind="uk_replay_empty_descendant_shape_gap",
                    message="UK replay skipped repeal: parent target exists but has no descendant structural shape.",
                    op=op,
                    detail=uk_replay_blocking_action_target_detail(op, target),
                )
            elif replay._missing_sectionlike_gap(target):
                _append_uk_replay_adjudication(
                    replay.adjudications_out,
                    kind="uk_replay_missing_sectionlike_range_gap",
                    message="UK replay skipped repeal: target falls inside an absent sectionlike range gap.",
                    op=op,
                    detail=uk_replay_blocking_action_target_detail(op, target),
                )
            elif replay._missing_schedule_branch_gap(target):
                _append_uk_replay_adjudication(
                    replay.adjudications_out,
                    kind="uk_replay_missing_schedule_branch_gap",
                    message="UK replay skipped repeal: schedule root branch is absent.",
                    op=op,
                    detail=uk_replay_blocking_action_target_detail(op, target),
                )
            elif replay._missing_schedule_root_gap(target):
                _append_uk_replay_adjudication(
                    replay.adjudications_out,
                    kind="uk_replay_missing_schedule_range_gap",
                    message="UK replay skipped repeal: target falls inside an absent alphanumeric schedule range gap.",
                    op=op,
                    detail=uk_replay_blocking_action_target_detail(op, target),
                )
            elif replay._missing_schedule_branch_gap(target):
                _append_uk_replay_adjudication(
                    replay.adjudications_out,
                    kind="uk_replay_missing_schedule_branch_gap",
                    message="UK replay skipped repeal: schedule root branch is absent.",
                    op=op,
                    detail=uk_replay_blocking_action_target_detail(op, target),
                )
            elif replay._missing_parent_shape_gap(target):
                _append_uk_replay_adjudication(
                    replay.adjudications_out,
                    kind=replay._missing_parent_shape_gap_kind(target),
                    message="UK replay skipped repeal: immediate parent target path is structurally absent.",
                    op=op,
                    detail=uk_replay_blocking_action_target_detail(op, target),
                )
            elif replay._schedule_paragraph_carrier_gap(target):
                _append_uk_replay_adjudication(
                    replay.adjudications_out,
                    kind=replay._schedule_paragraph_carrier_gap_kind(target),
                    message="UK replay skipped repeal: schedule paragraph carrier is structurally absent or wrapped.",
                    op=op,
                    detail=uk_replay_blocking_action_target_detail(op, target),
                )
            elif replay._leading_blank_subparagraph_gap(target):
                _append_uk_replay_adjudication(
                    replay.adjudications_out,
                    kind="uk_replay_absent_sibling_range_gap",
                    message="UK replay skipped repeal: target falls inside an absent leading numeric subparagraph gap under blank schedule placeholders.",
                    op=op,
                    detail=uk_replay_blocking_action_target_detail(op, target),
                )
            else:
                _append_uk_replay_adjudication(
                    replay.adjudications_out,
                    kind="uk_replay_target_not_found",
                    message="UK replay skipped repeal: target not found.",
                    op=op,
                    detail=uk_replay_blocking_action_target_detail(op, target),
                )
            return
        if parent and idx is not None:
            replay._log(f"  EXECUTOR: repealing {node.kind} {node.label} from parent {parent.kind} {parent.label}")
            replay._remove_node(node, parent, idx)
            replay._record_repealed_target(target)
        elif node in replay.statute.supplements:
            replay._log(f"  EXECUTOR: repealing schedule {node.label}")
            replay._remove_node(node, None, None)
            replay._record_repealed_target(target)
        replay._record_invariant_violations(op)
        replay._emit_top_section_snapshot(op)

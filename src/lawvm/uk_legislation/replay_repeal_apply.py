"""UK replay structural repeal action branch."""

from __future__ import annotations

from lawvm.core.ir import LegalAddress, LegalOperation
from lawvm.uk_legislation.addressing import _action_name
from lawvm.uk_legislation.heading_facets import _UK_REPLAY_CROSSHEADING_AND_STRUCTURAL_REPEAL_UNRESOLVED_RULE_ID
from lawvm.uk_legislation.mutable_ir import UKMutableNode
from lawvm.uk_legislation.provenance_notes import _crossheading_group_repeal_selector, _schedule_list_entry_repeal_selector
from lawvm.uk_legislation.replay_records import (
    _append_uk_replay_adjudication,
    uk_replay_blocking_action_target_detail,
)
from lawvm.uk_legislation.replay_target_gaps import uk_missing_source_target_gap


class UKReplayRepealApplyMixin:

    def _apply_repeal_op(
        self,
        op: LegalOperation,
        target: LegalAddress,
        node: UKMutableNode | None,
        parent: UKMutableNode | None,
        idx: int | None,
    ) -> None:
        schedule_list_entry_repeal_selector = _schedule_list_entry_repeal_selector(op)
        if schedule_list_entry_repeal_selector is not None:
            if self._repeal_schedule_list_entries(target, op, schedule_list_entry_repeal_selector):
                self._record_invariant_violations(op)
                self._emit_top_section_snapshot(op)
            return
        crossheading_group_repeal_selector = _crossheading_group_repeal_selector(op)
        if crossheading_group_repeal_selector is not None and node is not None:
            if self._repeal_crossheading_group(target, node, parent, op, crossheading_group_repeal_selector):
                self._record_invariant_violations(op)
                self._emit_top_section_snapshot(op)
            return
        if crossheading_group_repeal_selector is not None:
            _append_uk_replay_adjudication(
                self.adjudications_out,
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
            if self._target_under_repealed_prefix(target):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_repeal_target_already_absent_observed",
                    message=(
                        "UK replay observed a structural repeal whose target path "
                        "was already repealed earlier in the chain."
                    ),
                    op=op,
                    detail={
                        "action": _action_name(op.action),
                        "target": str(target),
                        "reason_code": "target_previously_repealed",
                        "family": "structural_repeal_idempotence",
                        "blocking": False,
                        "strict_disposition": "record",
                        "quirks_disposition": "record",
                    },
                )
            elif self._doubled_alpha_gap(target):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_absent_sibling_range_gap",
                    message="UK replay skipped repeal: target falls inside an absent doubled-alpha sibling range under the parent path.",
                    op=op,
                    detail=uk_replay_blocking_action_target_detail(op, target),
                )
            elif self._malformed_target_gap(target):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind=self._malformed_target_gap_kind(target),
                    message="UK replay skipped repeal: lowered target path is malformed.",
                    op=op,
                    detail=uk_replay_blocking_action_target_detail(op, target),
                )
            elif uk_missing_source_target_gap(op):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_missing_source_target_gap",
                    message="UK replay skipped repeal: target comes from index-only effect row without extracted source text.",
                    op=op,
                    detail=uk_replay_blocking_action_target_detail(op, target),
                )
            elif self._missing_sibling_range_gap(target):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_absent_sibling_range_gap",
                    message="UK replay skipped repeal: target falls inside an absent sibling range under the parent path.",
                    op=op,
                    detail=uk_replay_blocking_action_target_detail(op, target),
                )
            elif self._empty_descendant_shape_gap(target):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_empty_descendant_shape_gap",
                    message="UK replay skipped repeal: parent target exists but has no descendant structural shape.",
                    op=op,
                    detail=uk_replay_blocking_action_target_detail(op, target),
                )
            elif self._missing_sectionlike_gap(target):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_missing_sectionlike_range_gap",
                    message="UK replay skipped repeal: target falls inside an absent sectionlike range gap.",
                    op=op,
                    detail=uk_replay_blocking_action_target_detail(op, target),
                )
            elif self._missing_schedule_branch_gap(target):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_missing_schedule_branch_gap",
                    message="UK replay skipped repeal: schedule root branch is absent.",
                    op=op,
                    detail=uk_replay_blocking_action_target_detail(op, target),
                )
            elif self._missing_schedule_root_gap(target):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_missing_schedule_range_gap",
                    message="UK replay skipped repeal: target falls inside an absent alphanumeric schedule range gap.",
                    op=op,
                    detail=uk_replay_blocking_action_target_detail(op, target),
                )
            elif self._missing_schedule_branch_gap(target):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_missing_schedule_branch_gap",
                    message="UK replay skipped repeal: schedule root branch is absent.",
                    op=op,
                    detail=uk_replay_blocking_action_target_detail(op, target),
                )
            elif self._missing_parent_shape_gap(target):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind=self._missing_parent_shape_gap_kind(target),
                    message="UK replay skipped repeal: immediate parent target path is structurally absent.",
                    op=op,
                    detail=uk_replay_blocking_action_target_detail(op, target),
                )
            elif self._schedule_paragraph_carrier_gap(target):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind=self._schedule_paragraph_carrier_gap_kind(target),
                    message="UK replay skipped repeal: schedule paragraph carrier is structurally absent or wrapped.",
                    op=op,
                    detail=uk_replay_blocking_action_target_detail(op, target),
                )
            elif self._leading_blank_subparagraph_gap(target):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_absent_sibling_range_gap",
                    message="UK replay skipped repeal: target falls inside an absent leading numeric subparagraph gap under blank schedule placeholders.",
                    op=op,
                    detail=uk_replay_blocking_action_target_detail(op, target),
                )
            else:
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_target_not_found",
                    message="UK replay skipped repeal: target not found.",
                    op=op,
                    detail=uk_replay_blocking_action_target_detail(op, target),
                )
            return
        if parent and idx is not None:
            self._log(f"  EXECUTOR: repealing {node.kind} {node.label} from parent {parent.kind} {parent.label}")
            self._remove_node(node, parent, idx)
            self._record_repealed_target(target)
        elif node in self.statute.supplements:
            self._log(f"  EXECUTOR: repealing schedule {node.label}")
            self._remove_node(node, None, None)
            self._record_repealed_target(target)
        self._record_invariant_violations(op)
        self._emit_top_section_snapshot(op)

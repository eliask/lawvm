"""UK replay executor and public replay API."""

from __future__ import annotations

from typing import Any, List, Optional

from lawvm.core.ir import IRStatute, LegalOperation
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.uk_legislation.addressing import _action_name
from lawvm.uk_legislation.mutable_ir import UKMutableNode, UKMutableStatute
from lawvm.uk_legislation.replay_grounding import UKReplayGroundingMixin
from lawvm.uk_legislation.replay_heading_apply import UKReplayHeadingApplyMixin
from lawvm.uk_legislation.replay_insert_apply import UKReplayInsertApplyMixin
from lawvm.uk_legislation.replay_invariant_diagnostics import UKReplayInvariantDiagnosticsMixin
from lawvm.uk_legislation.replay_prepare import prepare_replay_uk_ops
from lawvm.uk_legislation.replay_records import (
    UKReplayPrepareResult,
    append_replay_fold_text_duplication_adjudications,
    _append_uk_replay_adjudication,
)
from lawvm.uk_legislation.replay_renumber_apply import UKReplayRenumberApplyMixin
from lawvm.uk_legislation.replay_repeal_apply import UKReplayRepealApplyMixin
from lawvm.uk_legislation.replay_replace_apply import UKReplayReplaceApplyMixin
from lawvm.uk_legislation.replay_schedule_list_apply import UKReplayScheduleListApplyMixin
from lawvm.uk_legislation.replay_state import UKReplayStateMixin
from lawvm.uk_legislation.replay_table_apply import UKReplayTableApplyMixin
from lawvm.uk_legislation.replay_target_diagnostics import UKReplayTargetDiagnosticsMixin
from lawvm.uk_legislation.replay_target_lookup import UKReplayTargetLookupMixin
from lawvm.uk_legislation.replay_text_action_apply import UKReplayTextActionApplyMixin
from lawvm.uk_legislation.replay_text_apply import UKReplayTextApplyMixin


class UKReplayExecutor(
    UKReplayTableApplyMixin,
    UKReplayTextActionApplyMixin,
    UKReplayTextApplyMixin,
    UKReplayInvariantDiagnosticsMixin,
    UKReplayScheduleListApplyMixin,
    UKReplayGroundingMixin,
    UKReplayTargetDiagnosticsMixin,
    UKReplayTargetLookupMixin,
    UKReplayInsertApplyMixin,
    UKReplayStateMixin,
    UKReplayRenumberApplyMixin,
    UKReplayHeadingApplyMixin,
    UKReplayRepealApplyMixin,
    UKReplayReplaceApplyMixin,
):
    def __init__(
        self,
        statute: IRStatute,
        eid_map: Optional[dict[str, str]] = None,
        text_map: Optional[dict[str, str]] = None,
        verbose: bool = False,
        lo_ops_out: Optional[List[LegalOperation]] = None,
        adjudications_out: Optional[List[CompileAdjudication]] = None,
    ):
        self.statute = UKMutableStatute.from_irstatute(statute)
        self.eid_map = eid_map or {}
        self.text_map = text_map or {}
        self.verbose = bool(verbose)
        self.lo_ops_out = lo_ops_out  # None = don't collect snapshots
        self.adjudications_out = adjudications_out
        self._seen_invariant_violations = self._collect_invariant_violations()
        self._repealed_target_prefixes: set[str] = set()
        self._applied_text_patch_targets: dict[str, list[str]] = {}
        self.oracle_alignment_events: list[dict[str, Any]] = []

    def _log(self, message: str) -> None:
        if self.verbose:
            print(message)

    def apply_op(self, op: LegalOperation):
        target = op.target
        # Keep legacy warnings visible during replay runs while also recording
        # structured adjudications for downstream analyses.

        if str(target.special or "") == "whole_act":
            if _action_name(op.action) == "repeal":
                self._log("  EXECUTOR: repealing WHOLE ACT")
                self.statute.body.children = []
                self.statute.supplements = []
                self._record_invariant_violations(op)
            else:
                self._log(
                    f"  EXECUTOR: WARN whole_act target with unhandled action {op.action!r} — skipping {op.op_id}"
                )
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_unsupported_action",
                    message="UK replay skipped unsupported whole-act action.",
                    op=op,
                    detail={"action": _action_name(op.action), "target": str(target)},
                )
            return

        target_eid = self._derive_target_eid(target)
        node: Optional[UKMutableNode]
        parent: Optional[UKMutableNode]
        idx: Optional[int]
        node, parent, idx = None, None, None
        if target_eid:
            node, parent, idx = self._find_node_and_parent_statute(
                target_eid,
                allow_sequence_match=False,
            )
            if node is not None and not self._eid_candidate_matches_target_leaf(node, target):
                node, parent, idx = None, None, None

        if not node:
            allow_compound_subsection_alias = _action_name(op.action) in ("text_replace", "text_repeal")
            node, parent, idx = self._find_node_by_target(
                target,
                allow_compound_subsection_alias=allow_compound_subsection_alias,
                allow_recursive_match=_action_name(op.action) != "insert",
                target_resolution_op=op,
            )
        insert_existing_target_resolution = ""
        if not node:
            node, parent, idx, insert_existing_target_resolution = (
                self._find_existing_insert_target_by_explicit_parent_leaf(target, op)
            )
        if not node and _action_name(op.action) in {"replace", "repeal"}:
            node, parent, idx = self._find_unique_schedule_item_for_source_parent_substitution_range_target(
                target,
                op,
            )
        target_found = node is not None
        if not target_found and self._empty_schedule_root_shape_gap(target):
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind="uk_replay_empty_schedule_shape_gap",
                message="UK replay skipped text-based op: empty schedule root has no descendant target shape.",
                op=op,
                detail={
                    "action": _action_name(op.action),
                    "target": str(target),
                    "source_shape": "empty_schedule_root",
                },
            )
            return

        if _action_name(op.action) == "repeal":
            self._apply_repeal_op(op, target, node, parent, idx)
            return
        elif _action_name(op.action) == "replace":
            self._apply_replace_op(op, target, node, parent, idx, target_found)
            return
        elif _action_name(op.action) in ("text_replace", "text_repeal"):
            self._apply_text_action_op(op, target, node, parent)
            return
        elif _action_name(op.action) == "insert":
            self._apply_insert_op(op, target, node, insert_existing_target_resolution)
            return
        elif _action_name(op.action) == "renumber":
            self._apply_renumber_op(op, target)
            return
        elif _action_name(op.action) == "unknown":
            self._log(f"  EXECUTOR: unknown action — skipping {op.op_id}")
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind="uk_replay_unsupported_action",
                message="UK replay skipped unsupported action.",
                op=op,
                detail={"action": _action_name(op.action), "target": str(target)},
            )
        else:
            raise ValueError(
                f"UKReplayExecutor.apply_op: unhandled action {op.action!r} "
                f"on op {op.op_id}. This is a programming error — every action "
                f"type must be explicitly handled (even if only to skip+warn)."
            )



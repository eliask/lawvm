"""UK replay tree-invariant diagnostics.

This module owns post-apply invariant classification for the UK replay
executor. It records existing replay findings; it does not repair or mutate the
tree.
"""

from __future__ import annotations

from typing import cast

from lawvm.core import tree_ops
from lawvm.core.ir import IRNode, LegalOperation
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


class UKReplayInvariantDiagnosticsMixin:
    statute: UKMutableStatute
    adjudications_out: list[CompileAdjudication]
    _seen_invariant_violations: set[str]

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

    def _invariant_target_roots(
        self,
        root_filter: set[tuple[str, str]] | None = None,
    ) -> list[tuple[str, UKMutableNode]]:
        targets: list[tuple[str, UKMutableNode]] = []
        if root_filter is None or ("body", "") in root_filter:
            targets.append(("body", self.statute.body))
        for schedule in self.statute.supplements:
            clean_label = _clean_num(str(schedule.label or ""))
            if root_filter is None or ("schedule", clean_label) in root_filter:
                targets.append((f"schedule:{schedule.label or '?'}", schedule))
        return targets

    def _collect_invariant_violations(
        self,
        root_filter: set[tuple[str, str]] | None = None,
    ) -> set[str]:
        violations: set[str] = set()
        for root_name, node in self._invariant_target_roots(root_filter):
            for violation in tree_ops.check_invariants(cast(IRNode, node)):
                if "duplicate " not in violation and " out of order:" not in violation:
                    continue
                violations.add(f"{root_name}:{violation}")
        return violations

    def _record_invariant_violations(self, op: LegalOperation) -> None:
        root_filter = self._invariant_root_filter_for_op(op)
        current_violations = self._collect_invariant_violations(root_filter)
        scoped_root_names = {
            root_name for root_name, _node in self._invariant_target_roots(root_filter)
        }
        scoped_seen = {
            violation
            for violation in self._seen_invariant_violations
            if any(violation.startswith(f"{root_name}:") for root_name in scoped_root_names)
        }
        payload_shape_violations = uk_payload_shape_invariant_violations(op)
        for scoped_violation in sorted(current_violations - scoped_seen):
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

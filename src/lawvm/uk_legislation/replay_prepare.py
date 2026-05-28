"""UK replay preparation orchestration."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Optional, Sequence

from lawvm.core.ir import LegalAddress, LegalOperation
from lawvm.core.mutation_boundary import TreePathStep
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.uk_legislation.addressing import _action_name
from lawvm.uk_legislation.replay_prepare_filters import _is_unsafe_schedule_entry_repeal_op
from lawvm.uk_legislation.replay_prepare_ordering import (
    _classify_same_source_text_patch_overlaps,
    _order_ops_by_before_edges,
)
from lawvm.uk_legislation.replay_records import (
    UKReplayPrepareResult,
    append_same_source_text_patch_overlap_blocked_adjudication,
    append_same_source_text_patch_overlap_disjoint_adjudication,
    append_schedule_entry_repeal_granularity_blocked_adjudication,
    append_unsupported_whole_act_prepare_filter_adjudication,
)


_UK_ADDRESS_ALIAS_PROVENANCE_TAG = "uk_address_alias:point_to_item"


def _canonicalize_uk_address_aliases(address: Optional[LegalAddress]) -> Optional[LegalAddress]:
    """Canonicalize UK-local address aliases before crossing replay/core boundaries."""
    if address is None:
        return None
    changed = False
    path: list[TreePathStep] = []
    for kind, label in address.path:
        if kind == "point":
            path.append(("item", label))
            changed = True
        else:
            path.append((kind, label))
    if not changed:
        return address
    return LegalAddress(path=tuple(path), special=address.special)


def _canonicalize_uk_operation_address_aliases(op: LegalOperation) -> LegalOperation:
    target = _canonicalize_uk_address_aliases(op.target)
    anchor = _canonicalize_uk_address_aliases(op.anchor)
    destination = _canonicalize_uk_address_aliases(op.destination)
    if target is op.target and anchor is op.anchor and destination is op.destination:
        return op

    provenance_tags = op.provenance_tags
    if _UK_ADDRESS_ALIAS_PROVENANCE_TAG not in provenance_tags:
        provenance_tags = (*provenance_tags, _UK_ADDRESS_ALIAS_PROVENANCE_TAG)

    return replace(
        op,
        target=target if target is not None else op.target,
        anchor=anchor,
        destination=destination,
        provenance_tags=provenance_tags,
    )


def prepare_replay_uk_ops(
    ops: Sequence[LegalOperation],
    *,
    base_executor: Optional[Any] = None,
    verbose: bool = False,
    adjudications_out: Optional[list[CompileAdjudication]] = None,
) -> UKReplayPrepareResult:
    """Normalize replay ops so every UK replay entry point applies the same semantics."""
    prepared_input_ops = tuple(_canonicalize_uk_operation_address_aliases(op) for op in ops)
    overlap_classification = _classify_same_source_text_patch_overlaps(
        prepared_input_ops,
        base_executor=base_executor,
    )

    filtered_ops: list[LegalOperation] = []
    rejected_ops: list[LegalOperation] = []
    rejected_adjudications: list[CompileAdjudication] = []
    for op in prepared_input_ops:
        if _is_unsafe_schedule_entry_repeal_op(op):
            if verbose:
                print("  replay_uk_ops: skipping unsafe schedule-entry repeal widened to schedule")
            rejected_ops.append(op)
            rejected_adjudications.append(
                append_schedule_entry_repeal_granularity_blocked_adjudication(
                    adjudications_out,
                    op=op,
                )
            )
            continue
        if op.op_id in overlap_classification.disjoint_overlap_op_ids:
            append_same_source_text_patch_overlap_disjoint_adjudication(
                adjudications_out,
                op=op,
                ordered_before_op_ids=tuple(
                    sorted(overlap_classification.disjoint_before_edges.get(op.op_id, ()))
                ),
            )
        if op.op_id in overlap_classification.overlapping_op_ids:
            if verbose:
                print("  replay_uk_ops: skipping overlapping same-source ordinal text patch")
            rejected_ops.append(op)
            rejected_adjudications.append(
                append_same_source_text_patch_overlap_blocked_adjudication(
                    adjudications_out,
                    op=op,
                )
            )
            continue
        if str(op.target.special or "") == "whole_act":
            if _action_name(op.action) == "repeal":
                filtered_ops.append(op)
                continue
            if verbose:
                print("  replay_uk_ops: skipping unsupported whole_act op")
            rejected_ops.append(op)
            rejected_adjudications.append(
                append_unsupported_whole_act_prepare_filter_adjudication(
                    adjudications_out,
                    op=op,
                )
            )
            continue
        filtered_ops.append(op)
    filtered_ops = _order_ops_by_before_edges(
        filtered_ops,
        overlap_classification.disjoint_before_edges,
    )
    return UKReplayPrepareResult(
        accepted_ops=tuple(filtered_ops),
        rejected_ops=tuple(rejected_ops),
        rejected_adjudications=tuple(rejected_adjudications),
    )

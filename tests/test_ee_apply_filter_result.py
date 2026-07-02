"""Unit tests for ``EEPitResult.apply_filter_result`` — the conserved
apply-phase partition field that gives downstream tooling visibility
into accepted-vs-rejected op counts without re-parsing adjudications."""
from __future__ import annotations

from lawvm.core.filter_result import FilterResult, RejectedItem
from lawvm.core.ir import LegalAddress, LegalOperation, OperationSource
from lawvm.core.semantic_types import StructuralAction
from lawvm.estonia.replay import EEPitResult


def test_apply_filter_result_defaults_to_none() -> None:
    """New EEPitResult without a conserved-apply path starts with
    ``apply_filter_result=None``. Downstream consumers must check for None
    before accessing the FilterResult."""
    result = EEPitResult(
        base_id="ee/test",
        as_of="2024-01-01",
        amendments_total=[],
        amendments_applied=[],
        amendments_skipped=[],
        amendments_failed=[],
    )
    assert result.apply_filter_result is None


def test_apply_filter_result_carries_accepted_rejected_partition() -> None:
    """When the conserved apply path succeeds (no ValueError),
    ``apply_filter_result`` is a ``FilterResult[LegalOperation]``
    partitioning every input op into accepted or rejected with
    ``RejectedItem`` receipts."""
    accepted_op = LegalOperation(
        op_id="accepted-op-1",
        sequence=1,
        action=StructuralAction.TEXT_PATCH,
        target=LegalAddress(path=(("section", "1"),)),
        source=OperationSource(statute_id="ee/amendment"),
    )
    rejected_op = LegalOperation(
        op_id="rejected-op-1",
        sequence=2,
        action=StructuralAction.HEADING_REPLACE,
        target=LegalAddress(path=(("section", "2"),)),
        source=OperationSource(statute_id="ee/amendment"),
    )
    fr = FilterResult(
        accepted_items=(accepted_op,),
        rejected_items=(
            RejectedItem(
                item=rejected_op,
                reason="EE replay skipped unsupported action.",
                reason_code="ee_replay_unsupported_action",
                blocking=False,
            ),
        ),
    )
    result = EEPitResult(
        base_id="ee/test",
        as_of="2024-01-01",
        amendments_total=[],
        amendments_applied=[],
        amendments_skipped=[],
        amendments_failed=[],
        apply_filter_result=fr,
    )

    assert result.apply_filter_result is not None
    assert len(result.apply_filter_result.accepted_items) == 1
    assert result.apply_filter_result.accepted_items[0].op_id == "accepted-op-1"
    assert len(result.apply_filter_result.rejected_items) == 1
    rejected = result.apply_filter_result.rejected_items[0]
    assert rejected.item.op_id == "rejected-op-1"
    assert rejected.reason_code == "ee_replay_unsupported_action"

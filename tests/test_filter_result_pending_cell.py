"""The §6.3 pending third conservation cell (Fable UNIVERSAL_ALGEBRA delta #3).

A pending item is neither accepted nor rejected: adjudication is temporally
deferred. These tests pin the additive carrier — default-empty back-compat, the
three-way totality/disjointness identity, the closed-vocabulary validation, and
the ``PartitionResult`` / ``filter_result_from_parts`` accessor parity.
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from lawvm.core.filter_result import (
    PENDING_CONDITIONS,
    FilterResult,
    PendingItem,
    RejectedItem,
    filter_result_from_parts,
)
from lawvm.core.stage_result import PartitionResult


def test_pending_defaults_empty_back_compat() -> None:
    accepted = "keep-a"
    rejected = RejectedItem(item="drop-a", reason="unsupported")

    result: FilterResult[str] = FilterResult(
        accepted_items=(accepted,),
        rejected_items=(rejected,),
    )

    assert result.pending_items == ()
    assert result.pending_payloads == ()
    assert result.pending_reason_counts() == {}


def test_three_way_totality() -> None:
    ops = ["op-a", "op-b", "op-c", "op-d", "op-e"]
    accepted = (ops[0], ops[1])
    rejected = (RejectedItem(item=ops[2], reason="unsupported"),)
    pending = (
        PendingItem(item=ops[3], reason="awaits SI", condition="later_instrument"),
        PendingItem(item=ops[4], reason="18 months after enactment", condition="computed"),
    )

    result: FilterResult[str] = FilterResult(
        accepted_items=accepted,
        rejected_items=rejected,
        pending_items=pending,
    )

    total = len(ops)
    assert len(result.accepted_items) + len(result.rejected_items) + len(result.pending_items) == total


def test_cells_are_disjoint() -> None:
    result: FilterResult[str] = FilterResult(
        accepted_items=("op-a",),
        rejected_items=(RejectedItem(item="op-b", reason="unsupported"),),
        pending_items=(PendingItem(item="op-c", reason="external", condition="external_event"),),
    )

    accepted = set(result.accepted_items)
    rejected = set(result.rejected_payloads)
    pending = set(result.pending_payloads)

    assert accepted & rejected == set()
    assert accepted & pending == set()
    assert rejected & pending == set()
    # Each op lands in exactly one cell.
    for op in ("op-a", "op-b", "op-c"):
        assert (op in accepted) + (op in rejected) + (op in pending) == 1


def test_pending_item_requires_reason() -> None:
    with pytest.raises(ValueError, match="reason"):
        PendingItem(item="op-a", reason="", condition="computed")


def test_pending_item_rejects_unknown_condition() -> None:
    with pytest.raises(ValueError, match="condition"):
        PendingItem(item="op-a", reason="deferred", condition="whenever")
    # Empty (default) condition is not a member of the closed vocabulary either.
    with pytest.raises(ValueError, match="condition"):
        PendingItem(item="op-a", reason="deferred")


def test_pending_item_accepts_the_three_valid_conditions() -> None:
    assert PENDING_CONDITIONS == {"later_instrument", "computed", "external_event"}
    for condition in PENDING_CONDITIONS:
        item = PendingItem(item="op", reason="deferred", condition=condition)
        assert item.condition == condition


def test_pending_items_coerced_to_tuple() -> None:
    pending = PendingItem(item="op-a", reason="deferred", condition="computed")
    result: FilterResult[str] = FilterResult(pending_items=cast(Any, [pending]))

    assert result.pending_items == (pending,)
    assert isinstance(result.pending_items, tuple)


def test_pending_items_rejects_non_pending_element() -> None:
    with pytest.raises(ValueError, match="PendingItem"):
        FilterResult(pending_items=cast(Any, ["not-a-pending-item"]))


def test_partition_result_pending_delegates() -> None:
    pending = PendingItem(item="op-a", reason="awaits SI", condition="later_instrument")
    filter_result: FilterResult[str] = FilterResult(pending_items=(pending,))
    partition: PartitionResult[str] = PartitionResult(filter_result=filter_result)

    assert partition.pending == (pending,)
    assert partition.pending is partition.filter_result.pending_items


def test_filter_result_from_parts_forwards_pending() -> None:
    pending = PendingItem(item="op-a", reason="computed date", condition="computed")

    result = filter_result_from_parts(
        accepted_items=["keep-a"],
        rejected_items=[RejectedItem(item="drop-a", reason="unsupported")],
        pending_items=[pending],
    )

    assert result.pending_items == (pending,)
    assert result.pending_payloads == ("op-a",)
    assert result.pending_reason_counts() == {"computed date": 1}

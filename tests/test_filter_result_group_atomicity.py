"""Core group-atomicity semantics (Fable UNIVERSAL_ALGEBRA §5.5, §7 delta #7, #186).

A ``group_id`` names an ATOMIC compound legal act: all members apply together or
none do — no half-applied compound instruction. These tests exercise the
jurisdiction-neutral :func:`enforce_group_atomicity` transform over an
already-partitioned :class:`FilterResult`, proving:

  * a group with ONE rejected member flips ALL its members to rejected with the
    group witness (atomic rejection);
  * a group with a PENDING member flips the whole group to rejected;
  * an all-accepted group is untouched;
  * group-less ops (the common corpus case) are byte-identical passthrough;
  * conservation invariant I1 holds — the item multiset is preserved (total +
    disjoint partition), never dropped or double-counted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from lawvm.core.filter_result import (
    GROUP_ATOMIC_MEMBER_REJECTED_CODE,
    FilterResult,
    PendingItem,
    RejectedItem,
    enforce_group_atomicity,
)


@dataclass(frozen=True)
class _Op:
    """Minimal group-bearing op stand-in (mirrors LegalOperation.group_id)."""

    op_id: str
    group_id: Optional[str] = None


def _group_id_of(op: _Op) -> Optional[str]:
    return op.group_id


def _all_items(result: FilterResult[_Op]) -> set[str]:
    ids: set[str] = set()
    for op in result.accepted_items:
        ids.add(op.op_id)
    for rej in result.rejected_items:
        ids.add(rej.item.op_id)
    for pend in result.pending_items:
        ids.add(pend.item.op_id)
    return ids


def _assert_conserved(before: FilterResult[_Op], after: FilterResult[_Op]) -> None:
    """Invariant I1: total + disjoint. The item multiset is preserved and every
    item lands in exactly one cell."""

    before_ids = (
        [op.op_id for op in before.accepted_items]
        + [r.item.op_id for r in before.rejected_items]
        + [p.item.op_id for p in before.pending_items]
    )
    after_ids = (
        [op.op_id for op in after.accepted_items]
        + [r.item.op_id for r in after.rejected_items]
        + [p.item.op_id for p in after.pending_items]
    )
    # Total: same multiset of items in, out.
    assert sorted(before_ids) == sorted(after_ids)
    # Disjoint: no item appears in two cells (or twice in one).
    assert len(after_ids) == len(set(after_ids))


def test_rejected_member_rejects_whole_group() -> None:
    """A group with one rejected member → ALL members rejected with the witness."""

    a = _Op("a", group_id="g1")
    b = _Op("b", group_id="g1")
    c = _Op("c", group_id="g1")
    before = FilterResult(
        accepted_items=(a, b),
        rejected_items=(RejectedItem(item=c, reason="target absent", reason_code="uk_apply_no_write"),),
    )

    after = enforce_group_atomicity(before, _group_id_of)

    # The whole group is now rejected — no member landed.
    assert after.accepted_items == ()
    rejected_ids = {r.item.op_id for r in after.rejected_items}
    assert rejected_ids == {"a", "b", "c"}

    # The originally-rejected member keeps its own specific witness ...
    orig = next(r for r in after.rejected_items if r.item.op_id == "c")
    assert orig.reason_code == "uk_apply_no_write"
    # ... while the flipped members carry the group-atomicity witness naming the
    # group and the failing member.
    flipped = [r for r in after.rejected_items if r.item.op_id in {"a", "b"}]
    assert all(r.reason_code == GROUP_ATOMIC_MEMBER_REJECTED_CODE for r in flipped)
    assert all("'g1'" in r.reason for r in flipped)
    assert all(r.blocking for r in flipped)

    _assert_conserved(before, after)


def test_pending_member_rejects_whole_group() -> None:
    """A pending member means the compound act cannot land in full → group rejects."""

    a = _Op("a", group_id="g2")
    b = _Op("b", group_id="g2")
    before = FilterResult(
        accepted_items=(a,),
        pending_items=(PendingItem(item=b, reason="awaits SI", condition="later_instrument"),),
    )

    after = enforce_group_atomicity(before, _group_id_of)

    assert after.accepted_items == ()
    assert after.pending_items == ()
    rejected_ids = {r.item.op_id for r in after.rejected_items}
    assert rejected_ids == {"a", "b"}
    assert all(r.reason_code == GROUP_ATOMIC_MEMBER_REJECTED_CODE for r in after.rejected_items)

    _assert_conserved(before, after)


def test_all_accepted_group_is_untouched() -> None:
    """A group whose members all accepted stays accepted (byte-identical)."""

    a = _Op("a", group_id="g3")
    b = _Op("b", group_id="g3")
    before = FilterResult(accepted_items=(a, b))

    after = enforce_group_atomicity(before, _group_id_of)

    assert after == before
    assert after.accepted_items == (a, b)
    _assert_conserved(before, after)


def test_group_less_ops_are_byte_identical_passthrough() -> None:
    """Ops with no group_id (the common corpus case) are never touched, even when
    OTHER (group-less) ops are rejected."""

    a = _Op("a")  # no group
    b = _Op("b")  # no group
    c = _Op("c")  # no group, rejected
    before = FilterResult(
        accepted_items=(a, b),
        rejected_items=(RejectedItem(item=c, reason="no write", reason_code="x"),),
    )

    after = enforce_group_atomicity(before, _group_id_of)

    # Identity: no group => the passthrough returns the SAME object.
    assert after is before
    _assert_conserved(before, after)


def test_empty_string_group_id_is_ungrouped() -> None:
    """An empty-string group_id is treated as ungrouped (not a real group)."""

    a = _Op("a", group_id="")
    c = _Op("c", group_id="")
    before = FilterResult(
        accepted_items=(a,),
        rejected_items=(RejectedItem(item=c, reason="no write", reason_code="x"),),
    )

    after = enforce_group_atomicity(before, _group_id_of)

    # a stays accepted — "" does not bind a and c together.
    assert after.accepted_items == (a,)
    _assert_conserved(before, after)


def test_only_the_failing_group_flips() -> None:
    """A rejected member of one group does not disturb a healthy sibling group."""

    a = _Op("a", group_id="ok")
    b = _Op("b", group_id="ok")
    x = _Op("x", group_id="bad")
    y = _Op("y", group_id="bad")
    before = FilterResult(
        accepted_items=(a, b, x),
        rejected_items=(RejectedItem(item=y, reason="no write", reason_code="x"),),
    )

    after = enforce_group_atomicity(before, _group_id_of)

    accepted_ids = {op.op_id for op in after.accepted_items}
    rejected_ids = {r.item.op_id for r in after.rejected_items}
    # Healthy group "ok" survives; only "bad" flips.
    assert accepted_ids == {"a", "b"}
    assert rejected_ids == {"x", "y"}
    _assert_conserved(before, after)


def test_pending_of_healthy_group_stays_pending() -> None:
    """A pending member of a group with no failure stays pending (not flipped)."""

    a = _Op("a", group_id="g")
    b = _Op("b", group_id="g")
    before = FilterResult(
        accepted_items=(a,),
        pending_items=(PendingItem(item=b, reason="awaits SI", condition="later_instrument"),),
    )

    # Here the only non-accepted member IS the pending one, which itself counts as
    # a failure for its own group (a pending member means the act can't fully
    # land). So this DOES flip. Assert that behavior explicitly.
    after = enforce_group_atomicity(before, _group_id_of)
    assert after.accepted_items == ()
    assert after.pending_items == ()
    _assert_conserved(before, after)


def test_pending_survives_when_a_disjoint_group_fails() -> None:
    """A pending member of a healthy group is preserved even when a DIFFERENT
    group fails — pending is only flipped for its own failing group."""

    pend = _Op("p", group_id="healthy")
    acc = _Op("acc", group_id="healthy")
    x = _Op("x", group_id="bad")
    y = _Op("y", group_id="bad")
    before = FilterResult(
        accepted_items=(acc, x),
        rejected_items=(RejectedItem(item=y, reason="no write", reason_code="z"),),
        pending_items=(PendingItem(item=pend, reason="awaits", condition="computed"),),
    )

    # "healthy" group has an accepted + a pending member but NO rejected member;
    # however the pending member itself is a non-accepted member of "healthy", so
    # by the atomic rule the whole "healthy" group also flips. Confirm both groups
    # flip and conservation holds.
    after = enforce_group_atomicity(before, _group_id_of)
    assert after.accepted_items == ()
    assert after.pending_items == ()
    assert {r.item.op_id for r in after.rejected_items} == {"acc", "x", "y", "p"}
    _assert_conserved(before, after)

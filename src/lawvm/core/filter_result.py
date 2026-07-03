"""Shared lossless filter-result carriers.

Filtering legal operations is a semantic act: accepted and rejected lanes must
both remain inspectable. These records standardize that shape without deciding
frontend-local rejection policy.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Callable, Iterable, Optional


#: Witness reason-code stamped on a member that is flipped accepted→rejected by
#: the group-atomicity rule (Fable UNIVERSAL_ALGEBRA §5.5, §7 delta #7). This is a
#: :class:`RejectedItem.reason_code` in the ``core.`` namespace, NOT a
#: jurisdiction ``CompileAdjudication`` kind (so it does not collide with any
#: per-frontend ``*_replay_*`` adjudication-kind ownership registry): a group is
#: an ATOMIC legal act, so a single rejected member rejects the WHOLE group with
#: no half-applied compound instruction.
GROUP_ATOMIC_MEMBER_REJECTED_CODE: str = "core.group_atomic_member_rejected"


#: The closed vocabulary of §6.3 pending classes (Fable UNIVERSAL_ALGEBRA §6.3,
#: delta #3 carrier half). A pending item is neither accepted nor rejected: it is
#: an op whose applicability is TEMPORALLY DEFERRED — adjudication is postponed
#: until a triggering condition of one of these kinds resolves.
#:   * ``later_instrument`` — deferred to a later instrument resolvable in-corpus
#:     (a UK SI / FI decree that will commence or fill in the operation).
#:   * ``computed`` — deferred to a date/quantity groundable at parse time
#:     (e.g. "18 months after enactment").
#:   * ``external_event`` — deferred to an event never groundable in-corpus
#:     (e.g. "when the Secretary determines").
PENDING_CONDITIONS: frozenset[str] = frozenset({"later_instrument", "computed", "external_event"})


@dataclass(frozen=True, slots=True)
class RejectedItem[T]:
    item: T
    reason: str
    reason_code: str = ""
    blocking: bool = True

    def __post_init__(self) -> None:
        if not str(self.reason or "").strip():
            raise ValueError("RejectedItem.reason must be non-empty")
        if not isinstance(self.blocking, bool):
            raise ValueError("RejectedItem.blocking must be a boolean")


@dataclass(frozen=True, slots=True)
class PendingItem[T]:
    """A temporally-deferred op (Fable UNIVERSAL_ALGEBRA §6.3 pending cell).

    The third conservation cell, disjoint from accepted and rejected: a pending
    item is neither applied nor refused, its adjudication is POSTPONED until a
    ``condition`` of one of the closed :data:`PENDING_CONDITIONS` classes resolves.
    Deliberately carries NO ``blocking`` field — pending is orthogonal to the
    accept/reject axis that ``blocking`` modulates; modeling it as a rejected item
    would corrupt the distinction this cell exists to create.
    """

    item: T
    reason: str
    reason_code: str = ""
    condition: str = ""

    def __post_init__(self) -> None:
        if not str(self.reason or "").strip():
            raise ValueError("PendingItem.reason must be non-empty")
        if self.condition not in PENDING_CONDITIONS:
            raise ValueError(
                f"PendingItem.condition must be one of {sorted(PENDING_CONDITIONS)}, got {self.condition!r}"
            )


@dataclass(frozen=True, slots=True)
class FilterResult[T]:
    accepted_items: tuple[T, ...] = ()
    rejected_items: tuple[RejectedItem[T], ...] = ()
    pending_items: tuple[PendingItem[T], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "accepted_items", tuple(self.accepted_items))
        rejected_items = tuple(self.rejected_items)
        if not all(isinstance(rejected, RejectedItem) for rejected in rejected_items):
            raise ValueError("FilterResult.rejected_items must contain RejectedItem records")
        object.__setattr__(self, "rejected_items", rejected_items)
        pending_items = tuple(self.pending_items)
        if not all(isinstance(pending, PendingItem) for pending in pending_items):
            raise ValueError("FilterResult.pending_items must contain PendingItem records")
        object.__setattr__(self, "pending_items", pending_items)

    @property
    def rejected_payloads(self) -> tuple[T, ...]:
        return tuple(rejected.item for rejected in self.rejected_items)

    @property
    def pending_payloads(self) -> tuple[T, ...]:
        return tuple(pending.item for pending in self.pending_items)

    def rejected_reason_counts(self) -> dict[str, int]:
        return dict(Counter(rejected.reason for rejected in self.rejected_items if rejected.reason))

    def pending_reason_counts(self) -> dict[str, int]:
        return dict(Counter(pending.reason for pending in self.pending_items if pending.reason))


def filter_result_from_parts[T](
    *,
    accepted_items: Iterable[T] = (),
    rejected_items: Iterable[RejectedItem[T]] = (),
    pending_items: Iterable[PendingItem[T]] = (),
) -> FilterResult[T]:
    """Build a normalized immutable filter result from iterable parts."""

    return FilterResult(
        accepted_items=tuple(accepted_items),
        rejected_items=tuple(rejected_items),
        pending_items=tuple(pending_items),
    )


def enforce_group_atomicity[T](
    result: FilterResult[T],
    group_id_of: Callable[[T], Optional[str]],
    *,
    member_label: Callable[[T], str] = repr,
) -> FilterResult[T]:
    """Apply the §5.5 group-atomicity rule over an already-partitioned result.

    A ``group_id`` names an ATOMIC compound legal act (Fable UNIVERSAL_ALGEBRA
    §5.5 / §7 delta #7): all members apply together or none do — there is no
    half-applied compound instruction (US "striking X and inserting <block>" as
    one legal act; FI multi-op johtolause items). This function composes with the
    per-op θ dispositions (§2.3) that already produced ``result``: after the
    accepted / rejected / pending partition is computed, for every ``group_id``
    that has AT LEAST ONE member NOT accepted (a rejected OR pending member — a
    pending member means the whole act cannot yet land), EVERY member of that
    group is moved to the rejected lane, carrying a
    :data:`GROUP_ATOMIC_MEMBER_REJECTED_CODE` witness that names the group and the
    failing member.

    Conservation (invariant I1). The transform is a permutation of cells that is
    TOTAL and DISJOINT-preserving: it only ever moves an item accepted→rejected or
    pending→rejected, never drops or duplicates one, and an item already in the
    rejected lane keeps its own (more specific) witness. The output multiset of
    items equals the input multiset.

    Byte-identical for the common case. ``group_id_of`` returning ``None`` (or an
    empty string) for an item excludes it from every group, so group-less ops are
    untouched. A group whose members are ALL accepted is untouched. A
    single-member group with an accepted member is untouched. Only a group with a
    genuinely failing member is flipped — nothing else moves.

    :param group_id_of: extracts an item's group key; ``None``/empty ⇒ ungrouped.
    :param member_label: renders a member for the witness reason (default ``repr``).
    """

    def _key(item: T) -> Optional[str]:
        gid = group_id_of(item)
        if gid is None:
            return None
        gid = str(gid)
        return gid or None

    # First pass: which groups have a non-accepted member? Rejected and pending
    # members both count as "the compound act cannot land in full".
    failing_groups: dict[str, T] = {}

    def _note_failure(item: T) -> None:
        gid = _key(item)
        if gid is not None and gid not in failing_groups:
            failing_groups[gid] = item

    for rejected in result.rejected_items:
        _note_failure(rejected.item)
    for pending in result.pending_items:
        _note_failure(pending.item)

    if not failing_groups:
        # No grouped failure anywhere — byte-identical passthrough (the common
        # case, including every group-less corpus op).
        return result

    kept_accepted: list[T] = []
    new_rejected: list[RejectedItem[T]] = []
    for accepted in result.accepted_items:
        gid = _key(accepted)
        if gid is not None and gid in failing_groups:
            failing = failing_groups[gid]
            new_rejected.append(
                RejectedItem(
                    item=accepted,
                    reason=(
                        f"Group {gid!r} is an atomic legal act but member "
                        f"{member_label(failing)} did not apply; the whole group "
                        "is rejected (no half-applied compound instruction)."
                    ),
                    reason_code=GROUP_ATOMIC_MEMBER_REJECTED_CODE,
                    blocking=True,
                )
            )
        else:
            kept_accepted.append(accepted)

    # Pending members of a failing group become rejected (the group cannot land);
    # pending members of a NON-failing group stay pending untouched.
    kept_pending: list[PendingItem[T]] = []
    for pending in result.pending_items:
        gid = _key(pending.item)
        if gid is not None and gid in failing_groups:
            failing = failing_groups[gid]
            new_rejected.append(
                RejectedItem(
                    item=pending.item,
                    reason=(
                        f"Group {gid!r} is an atomic legal act but member "
                        f"{member_label(failing)} did not apply; the whole group "
                        "is rejected (no half-applied compound instruction)."
                    ),
                    reason_code=GROUP_ATOMIC_MEMBER_REJECTED_CODE,
                    blocking=True,
                )
            )
        else:
            kept_pending.append(pending)

    return FilterResult(
        accepted_items=tuple(kept_accepted),
        # Original rejected items keep their own specific witness; the flipped
        # members are appended after, so the rejected lane stays a faithful log.
        rejected_items=tuple(result.rejected_items) + tuple(new_rejected),
        pending_items=tuple(kept_pending),
    )

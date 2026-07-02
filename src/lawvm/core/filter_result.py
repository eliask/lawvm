"""Shared lossless filter-result carriers.

Filtering legal operations is a semantic act: accepted and rejected lanes must
both remain inspectable. These records standardize that shape without deciding
frontend-local rejection policy.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Iterable


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

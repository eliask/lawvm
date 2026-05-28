"""Shared lossless filter-result carriers.

Filtering legal operations is a semantic act: accepted and rejected lanes must
both remain inspectable. These records standardize that shape without deciding
frontend-local rejection policy.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Generic, Iterable, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class RejectedItem(Generic[T]):
    item: T
    reason: str
    reason_code: str = ""
    blocking: bool = True


@dataclass(frozen=True)
class FilterResult(Generic[T]):
    accepted_items: tuple[T, ...] = ()
    rejected_items: tuple[RejectedItem[T], ...] = ()

    @property
    def rejected_payloads(self) -> tuple[T, ...]:
        return tuple(rejected.item for rejected in self.rejected_items)

    def rejected_reason_counts(self) -> dict[str, int]:
        return dict(Counter(rejected.reason for rejected in self.rejected_items if rejected.reason))


def filter_result_from_parts(
    *,
    accepted_items: Iterable[T] = (),
    rejected_items: Iterable[RejectedItem[T]] = (),
) -> FilterResult[T]:
    """Build a normalized immutable filter result from iterable parts."""

    return FilterResult(
        accepted_items=tuple(accepted_items),
        rejected_items=tuple(rejected_items),
    )


"""Typed future-repeal bookkeeping for Finland replay."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from lawvm.core.elaboration_context import TargetUnitKind


@dataclass(frozen=True, slots=True)
class RepealTargetRef:
    """Typed repeal-target carrier for future-repeal suppression bookkeeping."""

    target_unit_kind: TargetUnitKind
    target_norm: str
    target_chapter: Optional[str] = None

    @classmethod
    def section(cls, target_norm: str, target_chapter: Optional[str] = None) -> "RepealTargetRef":
        return cls("section", target_norm, target_chapter)

    @classmethod
    def chapter(cls, target_norm: str) -> "RepealTargetRef":
        return cls("chapter", target_norm, None)

    @classmethod
    def part(cls, target_norm: str) -> "RepealTargetRef":
        return cls("part", target_norm, None)


def build_future_repeal_suffix(
    per_amendment: list[set[RepealTargetRef]],
) -> list[set[RepealTargetRef]]:
    """Pre-compute suffix unions of REPEAL targets in O(A) time.

    ``result[i]`` is the union of ``per_amendment[i+1 .. N-1]``, i.e. all
    repeal targets from amendments after index ``i``.  This keeps future-repeal
    suppression out of the replay loop's hot path.
    """
    n = len(per_amendment)
    suffix: list[set[RepealTargetRef]] = [set() for _ in range(n)]
    for i in range(n - 2, -1, -1):
        suffix[i] = suffix[i + 1] | per_amendment[i + 1]
    return suffix

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

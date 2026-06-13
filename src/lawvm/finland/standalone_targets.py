"""Typed section-target ownership carriers for Finland container replay."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, TypeAlias, cast

from lawvm.finland.helpers import _norm_num_token


@dataclass(frozen=True, slots=True)
class StandaloneSectionTarget:
    """Normalized standalone section target carried beside a container op."""

    part: str | None
    chapter: str | None
    label: str


LegacyStandaloneSectionTarget: TypeAlias = (
    tuple[str | None, str] | tuple[str | None, str | None, str]
)
StandaloneSectionTargetInput: TypeAlias = (
    StandaloneSectionTarget | LegacyStandaloneSectionTarget
)
StandaloneSectionTargetsInput: TypeAlias = Iterable[StandaloneSectionTargetInput] | None


def normalize_standalone_section_target(
    raw_target: StandaloneSectionTargetInput,
) -> StandaloneSectionTarget | None:
    """Normalize typed or legacy standalone-section target input.

    Legacy compatibility accepts the historical ``(chapter, section)`` and
    ``(part, chapter, section)`` tuple shapes at the apply boundary.
    """
    if isinstance(raw_target, StandaloneSectionTarget):
        raw_part = raw_target.part
        raw_chapter = raw_target.chapter
        raw_label = raw_target.label
    elif len(raw_target) == 2:
        raw_part = None
        raw_chapter, raw_label = cast(tuple[str | None, str | None], raw_target)
    elif len(raw_target) == 3:
        raw_part, raw_chapter, raw_label = cast(
            tuple[str | None, str | None, str | None],
            raw_target,
        )
    else:
        return None
    if raw_label is None:
        return None
    return StandaloneSectionTarget(
        part=_norm_num_token(str(raw_part)) if raw_part not in (None, "") else None,
        chapter=_norm_num_token(str(raw_chapter)) if raw_chapter not in (None, "") else None,
        label=_norm_num_token(str(raw_label)),
    )


def normalize_standalone_section_targets(
    targets: StandaloneSectionTargetsInput,
) -> tuple[StandaloneSectionTarget, ...]:
    normalized: list[StandaloneSectionTarget] = []
    for raw_target in targets or ():
        target = normalize_standalone_section_target(raw_target)
        if target is not None:
            normalized.append(target)
    return tuple(normalized)

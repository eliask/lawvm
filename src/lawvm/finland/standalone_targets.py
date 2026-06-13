"""Typed section-target ownership carriers for Finland container replay."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, TypeAlias, cast

from lawvm.finland.helpers import _norm_num_token
from lawvm.finland.ops import AmendmentOp


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


def build_standalone_section_targets(
    ops: list[AmendmentOp],
) -> frozenset[StandaloneSectionTarget]:
    """Collect standalone whole-section targets for container ownership guards.

    Container payload pruning and apply-time chapter-child stripping should only
    react to whole-section claims. Descendant-only section ops like ``1 § 5
    mom`` do not own the ``1 §`` shell and must not cause the parent chapter
    payload to drop that child section.
    """
    standalone_targets: set[StandaloneSectionTarget] = set()
    for op in ops:
        if op.target_unit_kind != "section" or not op.target_section:
            continue
        if op.target_paragraph is not None or op.target_item or op.target_special:
            continue
        norm_label = _norm_num_token(op.target_section)
        standalone_targets.add(
            StandaloneSectionTarget(
                part=_norm_num_token(op.target_part) if op.target_part else None,
                chapter=_norm_num_token(op.target_chapter) if op.target_chapter else None,
                label=norm_label,
            )
        )
        if op.lo is None:
            continue
        for tag in op.lo.provenance_tags:
            if not tag.startswith("body_chapter_retargeted_from:"):
                continue
            orig_chapter = tag.split(":", 1)[1]
            standalone_targets.add(
                StandaloneSectionTarget(
                    part=_norm_num_token(op.target_part) if op.target_part else None,
                    chapter=_norm_num_token(orig_chapter),
                    label=norm_label,
                )
            )
    return frozenset(standalone_targets)

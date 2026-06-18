"""Typed section-target ownership carriers for Finland container replay."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, TypeAlias, cast

from lawvm.core.elaboration_context import TargetUnitKind
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


def group_shadow_pruning_section_targets(
    ops: list[AmendmentOp],
    *,
    target_unit_kind: TargetUnitKind,
    target_norm: str,
    target_part: str | None,
    duplicate_section_labels: frozenset[str],
) -> set[str]:
    """Return standalone section labels that may shadow a container payload."""
    if target_unit_kind not in {"chapter", "part"}:
        return set()

    out: set[str] = set()
    for op in ops:
        section_label = _norm_num_token(op.target_section or "")
        if op.target_unit_kind != "section" or not section_label:
            continue
        if section_label in duplicate_section_labels:
            continue
        if op.target_part == target_part and op.target_chapter == target_norm:
            continue
        out.add(section_label)
    return out


def group_shadow_pruning_foreign_scoped_section_targets(
    ops: list[AmendmentOp],
    *,
    target_unit_kind: TargetUnitKind,
    target_norm: str,
    target_part: str | None,
    duplicate_section_labels: frozenset[str],
) -> set[str]:
    """Return shadowable section labels with explicit foreign container scope.

    This narrows container payload pruning for live heading-only container
    replaces: only prune carried "new" sections when the same amendment also
    owns the section as a standalone target in another explicit scope.
    """
    if target_unit_kind not in {"chapter", "part"}:
        return set()

    out: set[str] = set()
    for op in ops:
        section_label = _norm_num_token(op.target_section or "")
        if op.target_unit_kind != "section" or not section_label:
            continue
        # Only INSERT ops can shadow carry-forward content; REPLACE ops act on
        # already-existing sections and must not suppress container payload.
        if op.op_type != "INSERT":
            continue
        # Carry-forward INSERTs have inferred/stale chapter scope; they should
        # not shadow container payload since their chapter attribution is
        # unreliable.
        if (
            op.scope_confidence is not None
            and op.scope_confidence.source == "carry_forward"
        ):
            continue
        if section_label in duplicate_section_labels:
            continue
        if op.target_part == target_part and op.target_chapter == target_norm:
            continue
        if op.target_chapter is None and target_unit_kind == "chapter":
            continue
        if op.target_part is None and target_unit_kind == "part":
            continue
        out.add(section_label)
    return out


def group_shadow_pruning_foreign_scoped_replace_section_targets(
    ops: list[AmendmentOp],
    *,
    target_unit_kind: TargetUnitKind,
    target_norm: str,
    target_part: str | None,
    duplicate_section_labels: frozenset[str],
) -> set[str]:
    """Return foreign-scoped REPLACE labels for carried-payload pruning."""
    if target_unit_kind not in {"chapter", "part"}:
        return set()

    out: set[str] = set()
    for op in ops:
        section_label = _norm_num_token(op.target_section or "")
        if op.target_unit_kind != "section" or not section_label:
            continue
        if op.op_type != "REPLACE":
            continue
        if section_label in duplicate_section_labels:
            continue
        if op.target_part == target_part and op.target_chapter == target_norm:
            continue
        if op.target_chapter is None and target_unit_kind == "chapter":
            continue
        if op.target_part is None and target_unit_kind == "part":
            continue
        out.add(section_label)
    return out

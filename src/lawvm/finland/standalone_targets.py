"""Typed section-target ownership carriers for Finland container replay."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, TypeAlias

from lawvm.core.elaboration_context import TargetUnitKind
from lawvm.finland.helpers import _norm_num_token
from lawvm.finland.ops import AmendmentOp, ScopeResolutionSource

_SCOPE_CONFIDENCE_OVERRIDES_BODY_CHAPTER = frozenset(
    {
        ScopeResolutionSource.EXPLICIT_SCOPE_REWRITE,
        ScopeResolutionSource.LIVE_STEM_HOST,
    }
)


@dataclass(frozen=True, slots=True)
class StandaloneSectionTarget:
    """Normalized standalone section target carried beside a container op."""

    part: str | None
    chapter: str | None
    label: str

    def __post_init__(self) -> None:
        raw_label = str(self.label).strip()
        if not raw_label:
            raise ValueError("StandaloneSectionTarget.label must be non-empty")
        part = _norm_num_token(str(self.part)) if self.part not in (None, "") else None
        chapter = _norm_num_token(str(self.chapter)) if self.chapter not in (None, "") else None
        object.__setattr__(self, "part", part)
        object.__setattr__(self, "chapter", chapter)
        object.__setattr__(self, "label", _norm_num_token(raw_label))


StandaloneSectionTargetInput: TypeAlias = StandaloneSectionTarget
StandaloneSectionTargetsInput: TypeAlias = Iterable[StandaloneSectionTarget] | None


def normalize_standalone_section_target(
    raw_target: StandaloneSectionTargetInput,
) -> StandaloneSectionTarget | None:
    """Normalize a typed standalone-section target at the apply boundary."""
    if not isinstance(raw_target, StandaloneSectionTarget):
        raise TypeError("standalone_section_targets must contain StandaloneSectionTarget rows")
    return raw_target


def normalize_standalone_section_targets(
    targets: StandaloneSectionTargetsInput,
) -> tuple[StandaloneSectionTarget, ...]:
    normalized: list[StandaloneSectionTarget] = []
    for raw_target in targets or ():
        target = normalize_standalone_section_target(raw_target)
        if target is not None:
            normalized.append(target)
    return tuple(normalized)


def _effective_target_chapter(op: AmendmentOp) -> str | None:
    """Return the chapter that owns section-target shadowing decisions."""
    witness = op.scope_confidence
    if (
        witness is not None
        and witness.source in _SCOPE_CONFIDENCE_OVERRIDES_BODY_CHAPTER
        and witness.resolved_chapter
    ):
        return _norm_num_token(witness.resolved_chapter)
    return _norm_num_token(op.target_chapter) if op.target_chapter else None


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
        if op.target_chapter in (None, "") and op.op_type != "INSERT":
            continue
        norm_label = _norm_num_token(op.target_section)
        standalone_targets.add(
            StandaloneSectionTarget(
                part=_norm_num_token(op.target_part) if op.target_part else None,
                chapter=_effective_target_chapter(op),
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
        op_part = _norm_num_token(op.target_part) if op.target_part else None
        op_chapter = _effective_target_chapter(op)
        if op_part == target_part and op_chapter == target_norm:
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
            and op.scope_confidence.source is ScopeResolutionSource.CARRY_FORWARD
        ):
            continue
        if section_label in duplicate_section_labels:
            continue
        op_part = _norm_num_token(op.target_part) if op.target_part else None
        op_chapter = _effective_target_chapter(op)
        if op_part == target_part and op_chapter == target_norm:
            continue
        if op_chapter is None and target_unit_kind == "chapter":
            continue
        if op_part is None and target_unit_kind == "part":
            continue
        out.add(section_label)
    return out


def group_shadow_pruning_foreign_scoped_descendant_section_targets(
    ops: list[AmendmentOp],
    *,
    target_unit_kind: TargetUnitKind,
    target_norm: str,
    target_part: str | None,
    duplicate_section_labels: frozenset[str],
) -> set[str]:
    """Return foreign-scoped INSERT labels that only own section descendants."""
    if target_unit_kind not in {"chapter", "part"}:
        return set()

    out: set[str] = set()
    for op in ops:
        section_label = _norm_num_token(op.target_section or "")
        if op.target_unit_kind != "section" or not section_label:
            continue
        if op.op_type != "INSERT":
            continue
        if op.target_paragraph is None and not op.target_item and not op.target_special:
            continue
        if (
            op.scope_confidence is not None
            and op.scope_confidence.source is ScopeResolutionSource.CARRY_FORWARD
        ):
            continue
        if section_label in duplicate_section_labels:
            continue
        op_part = _norm_num_token(op.target_part) if op.target_part else None
        op_chapter = _effective_target_chapter(op)
        if op_part == target_part and op_chapter == target_norm:
            continue
        if op_chapter is None and target_unit_kind == "chapter":
            continue
        if op_part is None and target_unit_kind == "part":
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
        op_part = _norm_num_token(op.target_part) if op.target_part else None
        op_chapter = _effective_target_chapter(op)
        if op_part == target_part and op_chapter == target_norm:
            continue
        if op_chapter is None and target_unit_kind == "chapter":
            continue
        if op_part is None and target_unit_kind == "part":
            continue
        out.add(section_label)
    return out


def group_shadow_pruning_foreign_scoped_replace_section_target_scopes(
    ops: list[AmendmentOp],
    *,
    target_unit_kind: TargetUnitKind,
    target_norm: str,
    target_part: str | None,
    duplicate_section_labels: frozenset[str],
) -> frozenset[StandaloneSectionTarget]:
    """Return typed foreign-scoped REPLACE targets for carried-payload pruning."""
    if target_unit_kind not in {"chapter", "part"}:
        return frozenset()

    out: set[StandaloneSectionTarget] = set()
    for op in ops:
        section_label = _norm_num_token(op.target_section or "")
        if op.target_unit_kind != "section" or not section_label:
            continue
        if op.op_type != "REPLACE":
            continue
        if section_label in duplicate_section_labels:
            continue
        op_part = _norm_num_token(op.target_part) if op.target_part else None
        op_chapter = _effective_target_chapter(op)
        if op_part == target_part and op_chapter == target_norm:
            continue
        if op_chapter is None and target_unit_kind == "chapter":
            continue
        if op_part is None and target_unit_kind == "part":
            continue
        out.add(
            StandaloneSectionTarget(
                part=op_part,
                chapter=op_chapter,
                label=section_label,
            )
        )
    return frozenset(out)

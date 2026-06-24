"""Disposition decisions for uncovered-body recovery.

The ``WHAT do we do at the resolved target?`` phase: given a resolved EXISTING
section, decide whether the amendment body may wholly REPLACE it. Kept separate
from resolution (uncovered_target_resolve) and from the mutation that applies the
decision, so each placement is a pure, auditable verdict.

Reconstruction note (hostile-source / missing-spec compilation): the legacy
cascade computed this inline and immediately acted on it. Here the decision is a
typed value carrying every input it weighed, so "why did this section REPLACE /
not REPLACE?" is answerable from the verdict alone.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping, Optional

from lawvm.core.ir import IRNode
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.helpers import _is_omission_ir, _norm_num_token
from lawvm.finland.merge import _has_section_omissions_ir

# Minimum fraction of the master section's text the merged result must retain;
# below this, the omission-merge is treated as text corruption and rejected.
_MERGE_MIN_TEXT_RATIO = 0.75


@dataclass(frozen=True, slots=True)
class ReplaceDecision:
    """Whether an uncovered EXISTING-section candidate may whole-section REPLACE.

    Carries the weighed inputs (subsection counts, omission/cross-chapter flags)
    and a ``reason`` so the verdict is self-explaining for the audit trail.
    """

    can_replace: bool
    amend_subsec_count: int
    master_subsec_count: int
    would_lose_subsections: bool
    has_omissions: bool
    effective_would_lose: bool
    reason: str


def compute_replace_decision(
    amend_section_ir: IRNode,
    existing_section: IRNode,
    has_content_ops: bool,
    cross_chapter: bool,
    whole_chapter_replace: bool,
) -> ReplaceDecision:
    """Decide whether an uncovered EXISTING-section candidate may REPLACE.

    A whole-section REPLACE is allowed only when the amendment carries content
    ops, the body has no omissions, the resolution is not cross-chapter, and the
    replacement would not silently shed subsections — unless the johtolause
    declared a whole-chapter replace, in which case a lower subsection count is
    the intended new truth. Pure.
    """
    amend_subsec_count = sum(1 for c in amend_section_ir.children if c.kind is IRNodeKind.SUBSECTION)
    master_subsec_count = sum(1 for c in existing_section.children if c.kind is IRNodeKind.SUBSECTION)
    would_lose_subsections = amend_subsec_count < master_subsec_count
    has_omissions = _has_section_omissions_ir(amend_section_ir)
    # For whole-chapter replacements the amendment body is authoritative even when
    # the section shrinks its subsection count.
    effective_would_lose = would_lose_subsections and not whole_chapter_replace
    can_replace = (
        has_content_ops
        and not has_omissions
        and not cross_chapter
        and not effective_would_lose
    )
    if can_replace:
        reason = "replace_allowed"
    elif not has_content_ops:
        reason = "no_content_ops"
    elif has_omissions:
        reason = "has_omissions"
    elif cross_chapter:
        reason = "cross_chapter"
    elif effective_would_lose:
        reason = "would_lose_subsections"
    else:
        reason = "blocked"
    return ReplaceDecision(
        can_replace=can_replace,
        amend_subsec_count=amend_subsec_count,
        master_subsec_count=master_subsec_count,
        would_lose_subsections=would_lose_subsections,
        has_omissions=has_omissions,
        effective_would_lose=effective_would_lose,
        reason=reason,
    )


@dataclass(frozen=True, slots=True)
class MergeDecision:
    """Whether an omission-merged section may be accepted as the new content.

    ``skip_reason`` is None when accepted; otherwise the bare reason (the caller
    prefixes it for the finding stream). Post-merge guards reject merges that
    shed subsections, corrupt text, or duplicate subsection labels.
    """

    accept: bool
    merged_subsec_count: int
    master_subsec_count: int
    text_ratio: float
    has_dup_labels: bool
    skip_reason: Optional[str]


def evaluate_omission_merge(merged: IRNode, existing_section: IRNode) -> MergeDecision:
    """Decide whether an omission-merged section is safe to adopt. Pure.

    Accept only when the merge keeps at least as many subsections as the master
    (additions allowed), retains enough text (``_MERGE_MIN_TEXT_RATIO``), and
    introduces no duplicate subsection labels (a merge-corruption signal).
    """
    merged_subsec_count = sum(1 for c in merged.children if c.kind is IRNodeKind.SUBSECTION)
    master_subsec_count = sum(1 for c in existing_section.children if c.kind is IRNodeKind.SUBSECTION)
    master_text = irnode_to_text(existing_section)
    merged_text = irnode_to_text(merged)
    text_ratio = len(merged_text) / len(master_text) if master_text else 1.0
    merged_labels = [c.label for c in merged.children if c.kind is IRNodeKind.SUBSECTION and c.label]
    has_dup_labels = len(merged_labels) != len(set(merged_labels))

    accept = (
        merged_subsec_count >= master_subsec_count
        and text_ratio >= _MERGE_MIN_TEXT_RATIO
        and not has_dup_labels
    )
    if accept:
        skip_reason: Optional[str] = None
    elif merged_subsec_count < master_subsec_count:
        skip_reason = "would_lose_subsections"
    elif text_ratio < _MERGE_MIN_TEXT_RATIO:
        skip_reason = "low_text_ratio"
    elif has_dup_labels:
        skip_reason = "duplicate_subsection_labels"
    else:
        skip_reason = "blocked"
    return MergeDecision(
        accept=accept,
        merged_subsec_count=merged_subsec_count,
        master_subsec_count=master_subsec_count,
        text_ratio=text_ratio,
        has_dup_labels=has_dup_labels,
        skip_reason=skip_reason,
    )


@dataclass(frozen=True, slots=True)
class PastRepealVerdict:
    """Whether an uncovered candidate may replace a repeal-placeholder slot."""

    applies: bool       # the live slot is a repeal tombstone
    bypass: bool        # if applies: reinstate it (replace the placeholder)?
    bypass_reason: Optional[str]  # "tilalle_insert" | "whole_chapter_replace" | None


def evaluate_past_repeal_guard(
    existing_attrs: Mapping[str, Any],
    ops: Iterable[Any],
    label: str,
    amend_chapter: Optional[str],
    whole_chapter_replace: bool,
    amend_part: Optional[str] = None,
    part_insert_labels: Optional[Iterable[str]] = None,
) -> PastRepealVerdict:
    """Decide whether a repeal-placeholder slot may be reinstated. Pure.

    A section whose live slot is a repeal tombstone is normally left alone; it is
    reinstated only when this amendment explicitly inserts the same section
    ("tilalle" INSERT) or wholly replaces the chapter.
    """
    from lawvm.finland.ops import OpType

    if existing_attrs.get("lawvm_repeal_placeholder") != "1":
        return PastRepealVerdict(applies=False, bypass=False, bypass_reason=None)
    if amend_part and part_insert_labels:
        part_norm = _norm_num_token(amend_part)
        if part_norm and part_norm in {_norm_num_token(p) for p in part_insert_labels}:
            return PastRepealVerdict(
                applies=True,
                bypass=True,
                bypass_reason="part_insert_subtree",
            )
    has_insert_op = any(
        op.op_type == OpType.INSERT
        and op.target_unit_kind == "section"
        and op.target_section
        and _norm_num_token(op.target_section) == label
        and (
            not op.target_chapter
            or not amend_chapter
            or _norm_num_token(op.target_chapter) == amend_chapter
        )
        for op in ops
    )
    if has_insert_op:
        return PastRepealVerdict(applies=True, bypass=True, bypass_reason="tilalle_insert")
    if whole_chapter_replace:
        return PastRepealVerdict(applies=True, bypass=True, bypass_reason="whole_chapter_replace")
    return PastRepealVerdict(applies=True, bypass=False, bypass_reason=None)


class ExistingDisposition(Enum):
    """Terminal action for an uncovered candidate that resolved to a live section.

    The single outcome of the EXISTING-path's replace/merge/skip cascade, once the
    voimaantulo-relabel, johto, and past-repeal guards have been cleared. Naming
    the terminal action as one enum makes the disposition of every EXISTING-path
    candidate inspectable from one value instead of a scattered if/elif chain.
    """

    REPLACE = "replace"              # whole-section REPLACE is allowed
    MERGE_CANDIDATE = "merge"        # section-level omission → attempt an omission merge
    SKIP_CROSS_CHAPTER = "skip_cross_chapter"
    SKIP_NO_CONTENT_OPS = "skip_no_content_ops"
    SKIP_WOULD_LOSE_SUBSECTIONS = "skip_would_lose_subsections"
    SKIP_BLOCKED = "skip_blocked"    # blocked for none of the named reasons above


@dataclass(frozen=True, slots=True)
class ExistingDispositionVerdict:
    """Typed terminal verdict for the EXISTING-path disposition cascade.

    ``skip_reason`` is the bare finding reason for the SKIP_* outcomes (None for
    REPLACE / MERGE_CANDIDATE). MERGE_CANDIDATE still requires the caller to run
    the omission merge and re-check via :func:`evaluate_omission_merge`; the merge
    can still be rejected, but the decision to *attempt* it is captured here.
    """

    outcome: ExistingDisposition
    skip_reason: Optional[str]

    def __post_init__(self) -> None:
        committing = self.outcome in (
            ExistingDisposition.REPLACE,
            ExistingDisposition.MERGE_CANDIDATE,
        )
        if committing and self.skip_reason is not None:
            raise ValueError(
                f"{self.outcome.value} disposition must not carry a skip_reason"
            )
        if not committing and self.skip_reason is None:
            raise ValueError(
                f"{self.outcome.value} disposition requires a skip_reason"
            )


def classify_existing_disposition(
    amend_section_ir: IRNode,
    replace_decision: ReplaceDecision,
    has_content_ops: bool,
    cross_chapter: bool,
) -> ExistingDispositionVerdict:
    """Classify the terminal action for an EXISTING-path uncovered candidate. Pure.

    Mirrors the legacy cascade's first-match order: a permitted whole-section
    REPLACE wins; otherwise a same-chapter section-level omission is a merge
    candidate; otherwise the candidate is skipped with the most specific reason
    the replace decision surfaced (cross-chapter, no content ops, subsection
    loss), defaulting to a generic blocked skip.
    """
    if replace_decision.can_replace:
        return ExistingDispositionVerdict(ExistingDisposition.REPLACE, None)

    is_merge_candidate = (
        has_content_ops
        and replace_decision.has_omissions
        and not cross_chapter
        and any(_is_omission_ir(c) for c in amend_section_ir.children)
    )
    if is_merge_candidate:
        return ExistingDispositionVerdict(ExistingDisposition.MERGE_CANDIDATE, None)

    if cross_chapter:
        return ExistingDispositionVerdict(
            ExistingDisposition.SKIP_CROSS_CHAPTER, "cross_chapter_existing_target"
        )
    if not has_content_ops:
        return ExistingDispositionVerdict(
            ExistingDisposition.SKIP_NO_CONTENT_OPS, "no_content_ops"
        )
    if replace_decision.effective_would_lose:
        return ExistingDispositionVerdict(
            ExistingDisposition.SKIP_WOULD_LOSE_SUBSECTIONS, "would_lose_subsections"
        )
    return ExistingDispositionVerdict(ExistingDisposition.SKIP_BLOCKED, "blocked")

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

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.merge import _has_section_omissions_ir


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

"""Tests for the uncovered-body disposition decision (compute_replace_decision).

Pure decision over two section IR trees — tested with hand-built IRNodes, no
corpus. Each test pins one branch of the REPLACE-allowed predicate + its reason.
"""
from __future__ import annotations

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.uncovered_dispose import compute_replace_decision


def _section(n_subsections: int) -> IRNode:
    subs = tuple(
        IRNode(kind=IRNodeKind.SUBSECTION, label=str(i + 1), text="x", attrs={}, children=())
        for i in range(n_subsections)
    )
    return IRNode(kind=IRNodeKind.SECTION, label="5", text="", attrs={}, children=subs)


def test_replace_allowed_when_clean() -> None:
    d = compute_replace_decision(_section(3), _section(3), has_content_ops=True, cross_chapter=False, whole_chapter_replace=False)
    assert d.can_replace is True
    assert d.reason == "replace_allowed"


def test_blocked_without_content_ops() -> None:
    d = compute_replace_decision(_section(3), _section(3), has_content_ops=False, cross_chapter=False, whole_chapter_replace=False)
    assert d.can_replace is False
    assert d.reason == "no_content_ops"


def test_blocked_cross_chapter() -> None:
    d = compute_replace_decision(_section(3), _section(3), has_content_ops=True, cross_chapter=True, whole_chapter_replace=False)
    assert d.can_replace is False
    assert d.reason == "cross_chapter"


def test_would_lose_subsections_blocks_replace() -> None:
    # amend has fewer subsections than master → would shed content.
    d = compute_replace_decision(_section(2), _section(4), has_content_ops=True, cross_chapter=False, whole_chapter_replace=False)
    assert d.would_lose_subsections is True
    assert d.can_replace is False
    assert d.reason == "would_lose_subsections"


def test_whole_chapter_replace_overrides_would_lose() -> None:
    # Same shrink, but a whole-chapter replace makes the lower count intentional.
    d = compute_replace_decision(_section(2), _section(4), has_content_ops=True, cross_chapter=False, whole_chapter_replace=True)
    assert d.would_lose_subsections is True
    assert d.effective_would_lose is False
    assert d.can_replace is True
    assert d.reason == "replace_allowed"


def test_counts_reported_for_audit() -> None:
    d = compute_replace_decision(_section(2), _section(5), has_content_ops=True, cross_chapter=False, whole_chapter_replace=False)
    assert d.amend_subsec_count == 2
    assert d.master_subsec_count == 5

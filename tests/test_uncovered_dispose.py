"""Tests for the uncovered-body disposition decision (compute_replace_decision).

Pure decision over two section IR trees — tested with hand-built IRNodes, no
corpus. Each test pins one branch of the REPLACE-allowed predicate + its reason.
"""
from __future__ import annotations

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.uncovered_dispose import compute_replace_decision, evaluate_omission_merge


def _section(n_subsections: int) -> IRNode:
    subs = tuple(
        IRNode(kind=IRNodeKind.SUBSECTION, label=str(i + 1), text="x", attrs={}, children=())
        for i in range(n_subsections)
    )
    return IRNode(kind=IRNodeKind.SECTION, label="5", text="", attrs={}, children=subs)


def _section_with(subs: list[tuple[str, str]]) -> IRNode:
    children = tuple(
        IRNode(kind=IRNodeKind.SUBSECTION, label=lbl, text=txt, attrs={}, children=())
        for lbl, txt in subs
    )
    return IRNode(kind=IRNodeKind.SECTION, label="5", text="", attrs={}, children=children)


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


def test_merge_accepted_when_clean() -> None:
    existing = _section_with([("1", "aaaa"), ("2", "bbbb")])
    merged = _section_with([("1", "aaaa"), ("2", "bbbb")])
    d = evaluate_omission_merge(merged, existing)
    assert d.accept is True
    assert d.skip_reason is None


def test_merge_rejected_would_lose_subsections() -> None:
    existing = _section_with([("1", "aaaa"), ("2", "bbbb"), ("3", "cccc")])
    merged = _section_with([("1", "aaaa"), ("2", "bbbb")])
    d = evaluate_omission_merge(merged, existing)
    assert d.accept is False
    assert d.skip_reason == "would_lose_subsections"


def test_merge_rejected_low_text_ratio() -> None:
    existing = _section_with([("1", "a" * 100), ("2", "b" * 100)])
    merged = _section_with([("1", "a"), ("2", "b")])  # same count, far less text
    d = evaluate_omission_merge(merged, existing)
    assert d.accept is False
    assert d.skip_reason == "low_text_ratio"


def test_merge_rejected_duplicate_labels() -> None:
    existing = _section_with([("1", "aaaa"), ("2", "bbbb")])
    merged = _section_with([("1", "aaaa"), ("1", "bbbb")])  # duplicate label "1"
    d = evaluate_omission_merge(merged, existing)
    assert d.accept is False
    assert d.skip_reason == "duplicate_subsection_labels"


class _Op:
    def __init__(self, op_type, unit_kind, section, chapter=None):
        self.op_type = op_type
        self.target_unit_kind = unit_kind
        self.target_section = section
        self.target_chapter = chapter


def test_past_repeal_not_a_placeholder() -> None:
    from lawvm.finland.uncovered_dispose import evaluate_past_repeal_guard
    v = evaluate_past_repeal_guard({}, [], "5", "2", False)
    assert v.applies is False and v.bypass is False


def test_past_repeal_blocks_without_tilalle_or_whole_chapter() -> None:
    from lawvm.finland.uncovered_dispose import evaluate_past_repeal_guard
    v = evaluate_past_repeal_guard({"lawvm_repeal_placeholder": "1"}, [], "5", "2", False)
    assert v.applies is True and v.bypass is False


def test_past_repeal_bypassed_by_tilalle_insert() -> None:
    from lawvm.finland.uncovered_dispose import evaluate_past_repeal_guard
    ops = [_Op("INSERT", "section", "5", "2")]
    v = evaluate_past_repeal_guard({"lawvm_repeal_placeholder": "1"}, ops, "5", "2", False)
    assert v.bypass is True and v.bypass_reason == "tilalle_insert"


def test_past_repeal_bypassed_by_whole_chapter_replace() -> None:
    from lawvm.finland.uncovered_dispose import evaluate_past_repeal_guard
    v = evaluate_past_repeal_guard({"lawvm_repeal_placeholder": "1"}, [], "5", "2", True)
    assert v.bypass is True and v.bypass_reason == "whole_chapter_replace"

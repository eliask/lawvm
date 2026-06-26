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

    @property
    def target_cols(self):
        """Mirror AmendmentOp.target_cols so the shim satisfies the column-read API."""
        from lawvm.finland.target_selector_codec import (
            AmendmentOpV1Record,
            TargetSelectorCodecV1,
        )

        record = AmendmentOpV1Record(
            target_unit_kind=self.target_unit_kind,
            target_section=self.target_section,
            target_chapter=self.target_chapter,
            target_part=None,
            target_paragraph=None,
            target_item=None,
            target_subitem=None,
            target_special=None,
        )
        return TargetSelectorCodecV1.to_legacy(
            TargetSelectorCodecV1.from_legacy(record)
        )


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


def test_past_repeal_bypassed_by_part_insert_subtree() -> None:
    from lawvm.finland.uncovered_dispose import evaluate_past_repeal_guard
    v = evaluate_past_repeal_guard(
        {"lawvm_repeal_placeholder": "1"},
        [],
        "110",
        "1",
        False,
        amend_part="5",
        part_insert_labels={"5"},
    )
    assert v.bypass is True and v.bypass_reason == "part_insert_subtree"


# ---------------------------------------------------------------------------
# classify_existing_disposition — EXISTING-path terminal verdict
# ---------------------------------------------------------------------------

import pytest

from lawvm.finland.uncovered_dispose import (
    ExistingDisposition,
    ExistingDispositionVerdict,
    classify_existing_disposition,
)


def _section_with_omission(n_subs: int) -> IRNode:
    """A section carrying a leading omission marker plus ``n_subs`` subsections."""
    children = (
        IRNode(kind=IRNodeKind.OMISSION, label=None, text="— —", attrs={}, children=()),
        *(
            IRNode(kind=IRNodeKind.SUBSECTION, label=str(i + 1), text="x", attrs={}, children=())
            for i in range(n_subs)
        ),
    )
    return IRNode(kind=IRNodeKind.SECTION, label="5", text="", attrs={}, children=children)


def test_existing_disposition_replace_wins() -> None:
    d = compute_replace_decision(_section(3), _section(3), has_content_ops=True, cross_chapter=False, whole_chapter_replace=False)
    v = classify_existing_disposition(_section(3), d, has_content_ops=True, cross_chapter=False)
    assert v.outcome is ExistingDisposition.REPLACE
    assert v.skip_reason is None


def test_existing_disposition_merge_candidate_on_omission() -> None:
    amend = _section_with_omission(2)
    d = compute_replace_decision(amend, _section(3), has_content_ops=True, cross_chapter=False, whole_chapter_replace=False)
    # has_omissions blocks REPLACE; same-chapter omission → merge candidate.
    assert d.can_replace is False
    v = classify_existing_disposition(amend, d, has_content_ops=True, cross_chapter=False)
    assert v.outcome is ExistingDisposition.MERGE_CANDIDATE
    assert v.skip_reason is None


def test_existing_disposition_cross_chapter_skip_beats_merge() -> None:
    amend = _section_with_omission(2)
    d = compute_replace_decision(amend, _section(3), has_content_ops=True, cross_chapter=True, whole_chapter_replace=False)
    v = classify_existing_disposition(amend, d, has_content_ops=True, cross_chapter=True)
    assert v.outcome is ExistingDisposition.SKIP_CROSS_CHAPTER
    assert v.skip_reason == "cross_chapter_existing_target"


def test_existing_disposition_no_content_ops_skip() -> None:
    d = compute_replace_decision(_section(3), _section(3), has_content_ops=False, cross_chapter=False, whole_chapter_replace=False)
    v = classify_existing_disposition(_section(3), d, has_content_ops=False, cross_chapter=False)
    assert v.outcome is ExistingDisposition.SKIP_NO_CONTENT_OPS
    assert v.skip_reason == "no_content_ops"


def test_existing_disposition_would_lose_subsections_skip() -> None:
    # Fewer subsections, no omission marker → not a merge candidate → would-lose skip.
    d = compute_replace_decision(_section(2), _section(4), has_content_ops=True, cross_chapter=False, whole_chapter_replace=False)
    v = classify_existing_disposition(_section(2), d, has_content_ops=True, cross_chapter=False)
    assert v.outcome is ExistingDisposition.SKIP_WOULD_LOSE_SUBSECTIONS
    assert v.skip_reason == "would_lose_subsections"


def test_existing_disposition_verdict_invariants() -> None:
    with pytest.raises(ValueError):
        ExistingDispositionVerdict(ExistingDisposition.REPLACE, "x")
    with pytest.raises(ValueError):
        ExistingDispositionVerdict(ExistingDisposition.SKIP_BLOCKED, None)

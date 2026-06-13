"""Tests for the chapter-qualified uncovered-body target resolver.

The resolver is pure, so it is tested against a tiny fake state — no corpus,
no full ReplayState. Each test pins one resolution rule / invariant.
"""
from __future__ import annotations

from typing import Optional, Set

import pytest

from lawvm.finland.uncovered_target_resolve import (
    ProvisionPath,
    ResolvedTarget,
    TargetVerdict,
    resolve_target,
)


class FakeState:
    """Minimal StateLookup: a label→path map keyed by (label, chapter).

    ``find_section_path(label, chapter)`` returns the scoped path; a bare-label
    lookup returns the path registered under chapter=None (the "unique" slot).
    """

    def __init__(
        self,
        scoped: dict[tuple[str, Optional[str]], ProvisionPath],
        duplicate_section_labels: Set[str],
    ) -> None:
        self._scoped = scoped
        self.duplicate_section_labels = duplicate_section_labels

    def find_section_path(self, label: str, chapter_num: Optional[str] = None) -> Optional[ProvisionPath]:
        return self._scoped.get((label, chapter_num))


def test_scoped_match_is_existing() -> None:
    state = FakeState({("5", "2"): (("chapter", "2"), ("section", "5"))}, set())
    r = resolve_target("5", "2", None, state, owned_chapter_labels=set())
    assert r.verdict is TargetVerdict.EXISTING
    assert r.cross_chapter is False
    assert r.reason == "scoped_match"


def test_no_live_section_is_new() -> None:
    state = FakeState({}, set())
    r = resolve_target("5", "2", None, state, owned_chapter_labels=set())
    assert r.verdict is TargetVerdict.NEW
    assert r.existing_path is None


def test_unscoped_fallback_only_for_unique_label() -> None:
    # Label "5" is unique (not duplicated) and not scoped to chapter 2, but exists
    # bare → unscoped fallback finds it.
    state = FakeState({("5", None): (("chapter", "9"), ("section", "5"))}, duplicate_section_labels=set())
    r = resolve_target("5", "2", None, state, owned_chapter_labels=set())
    assert r.verdict is TargetVerdict.EXISTING
    assert r.used_unscoped_fallback is True
    # Found in chapter 9 but declared chapter 2 → cross-chapter.
    assert r.cross_chapter is True
    assert r.reason == "cross_chapter_mismatch"


def test_duplicate_label_blocks_unscoped_fallback() -> None:
    # "1" exists bare but is duplicated across chapters → no unscoped fallback,
    # and scoped lookup for chapter 7 misses → NEW (the chapter-restart guard:
    # do not grab an arbitrary chapter's "1 §").
    state = FakeState({("1", None): (("chapter", "3"), ("section", "1"))}, duplicate_section_labels={"1"})
    r = resolve_target("1", "7", None, state, owned_chapter_labels=set())
    assert r.verdict is TargetVerdict.NEW
    assert r.used_unscoped_fallback is False


def test_no_chapter_context_duplicate_label_is_ambiguous() -> None:
    # No chapter context + duplicated label + a bare match → AMBIGUOUS, not a
    # silent wrong-chapter replace.
    state = FakeState({("1", None): (("chapter", "3"), ("section", "1"))}, duplicate_section_labels={"1"})
    r = resolve_target("1", None, None, state, owned_chapter_labels=set())
    assert r.verdict is TargetVerdict.AMBIGUOUS
    assert r.cross_chapter is True
    assert r.reason == "duplicate_label_no_chapter_context"


def test_newly_owned_chapter_blocks_unscoped_fallback() -> None:
    # Chapter 4 is newly inserted by this amendment; a same-numbered section in an
    # existing chapter must not be matched via the unscoped fallback.
    state = FakeState({("5", None): (("chapter", "9"), ("section", "5"))}, duplicate_section_labels=set())
    r = resolve_target("5", "4", None, state, owned_chapter_labels={"4"})
    assert r.verdict is TargetVerdict.NEW
    assert r.used_unscoped_fallback is False


def test_invariant_rejects_malformed_existing() -> None:
    with pytest.raises(ValueError):
        ResolvedTarget(
            verdict=TargetVerdict.EXISTING,
            label="5",
            amend_chapter="2",
            amend_part=None,
            existing_path=None,
            cross_chapter=False,
            used_unscoped_fallback=False,
            reason="x",
        )

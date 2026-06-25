"""Tests for the chapter-qualified uncovered-body target resolver.

The resolver is pure, so it is tested against a tiny fake state — no corpus,
no full ReplayState. Each test pins one resolution rule / invariant.
"""
from __future__ import annotations

from typing import Optional, Set

import pytest

import lawvm.finland.uncovered_target_resolve as utr
from lawvm.finland.uncovered_target_resolve import (
    ProvisionPath,
    ResolvedTarget,
    TargetVerdict,
    resolve_insert_chapter,
    resolve_target,
)


class FakeOp:
    """Minimal duck-typed op for the family-base-repeal check."""

    def __init__(self, op_type: str, section: Optional[str]) -> None:
        self.op_type = op_type
        self.target_unit_kind = "section"
        self.target_section = section
        self.target_paragraph = None
        self.target_item = None
        self.target_special = None

    @property
    def target_cols(self):
        """Mirror AmendmentOp.target_cols so the shim satisfies the column-read API."""
        from lawvm.finland.target_selector_codec import (
            AmendmentOpV1Record,
            TargetSelectorCodecV1,
        )

        record = AmendmentOpV1Record(
            target_unit_kind="section",
            target_section=self.target_section or "",
            target_chapter=None,
            target_part=None,
            target_paragraph=self.target_paragraph,
            target_item=self.target_item,
            target_subitem=None,
            target_special=self.target_special,
        )
        return TargetSelectorCodecV1.to_legacy(
            TargetSelectorCodecV1.from_legacy(record)
        )


class FakeIRState:
    """Carries only ``.ir`` (an opaque sentinel); find_family is monkeypatched."""

    ir = object()


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


# ---------------------------------------------------------------------------
# resolve_insert_chapter — NEW-insert family-base chapter override
# ---------------------------------------------------------------------------


def _patch_find_family(monkeypatch, result: Optional[ProvisionPath]) -> None:
    monkeypatch.setattr(utr._tops, "find_family", lambda *a, **k: result)


def test_insert_no_chapter_context_keeps_declared(monkeypatch) -> None:
    r = resolve_insert_chapter("5a", None, "1", FakeIRState(), [], None, set())
    assert (r.effective_chapter, r.effective_part) == (None, "1")
    assert r.reason == "no_chapter_context"


def test_insert_chapter_not_new_keeps_declared(monkeypatch) -> None:
    # chapter 2 is owned (not new) → no override.
    r = resolve_insert_chapter("5a", "2", None, FakeIRState(), [], None, owned_chapter_labels={"2"})
    assert r.effective_chapter == "2"
    assert r.reason == "declared_chapter_not_new"


def test_insert_no_family_base_keeps_declared(monkeypatch) -> None:
    _patch_find_family(monkeypatch, None)
    r = resolve_insert_chapter("5a", "9", None, FakeIRState(), [], new_chapter_labels={"9"}, owned_chapter_labels=set())
    assert r.effective_chapter == "9"
    assert r.reason == "no_family_base"


def test_insert_family_in_other_chapter_overrides(monkeypatch) -> None:
    # Family base "5" lives in chapter 3; declared new chapter is "9"; not a
    # sub-chapter, base not repealed → redirect to chapter 3.
    _patch_find_family(monkeypatch, (("chapter", "3"), ("section", "5")))
    r = resolve_insert_chapter("5a", "9", None, FakeIRState(), [], new_chapter_labels={"9"}, owned_chapter_labels=set())
    assert r.effective_chapter == "3"
    assert r.reason == "family_base_override"


def test_insert_source_owned_new_chapter_beats_family_base_override(monkeypatch) -> None:
    _patch_find_family(monkeypatch, (("chapter", "2"), ("section", "17")))
    r = resolve_insert_chapter(
        "17a",
        "3",
        None,
        FakeIRState(),
        [],
        new_chapter_labels={"3"},
        owned_chapter_labels={"3"},
        source_owned_chapter_labels={"3"},
    )
    assert r.effective_chapter == "3"
    assert r.reason == "source_owned_chapter"


def test_insert_source_owned_new_part_beats_family_base_override(monkeypatch) -> None:
    _patch_find_family(monkeypatch, (("part", "1"), ("chapter", "4"), ("section", "129")))
    r = resolve_insert_chapter(
        "129",
        "4",
        "5",
        FakeIRState(),
        [],
        new_chapter_labels={"4"},
        owned_chapter_labels={"4"},
        source_owned_part_labels={"5"},
    )
    assert (r.effective_chapter, r.effective_part) == ("4", "5")
    assert r.reason == "source_owned_part"


def test_insert_family_base_repealed_keeps_declared(monkeypatch) -> None:
    _patch_find_family(monkeypatch, (("chapter", "3"), ("section", "5")))
    ops = [FakeOp("REPEAL", "5")]
    r = resolve_insert_chapter("5a", "9", None, FakeIRState(), ops, new_chapter_labels={"9"}, owned_chapter_labels=set())
    assert r.effective_chapter == "9"
    assert r.reason == "family_base_repealed"


def test_insert_sub_chapter_keeps_declared(monkeypatch) -> None:
    # Declared chapter "3a" is a sub-chapter of the family's chapter "3" → keep.
    _patch_find_family(monkeypatch, (("chapter", "3"), ("section", "5")))
    r = resolve_insert_chapter("5a", "3a", None, FakeIRState(), [], new_chapter_labels={"3a"}, owned_chapter_labels=set())
    assert r.effective_chapter == "3a"
    assert r.reason == "declared_is_sub_chapter"

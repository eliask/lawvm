"""Tests for span-level anchoring infrastructure (L0 preparation).

Verifies SpanAnchor/SectionAnchors creation, content-addressing
stability, and extract_span_anchors / extract_all_anchors logic.
"""
from __future__ import annotations

import pytest
from typing import Any, Sequence, cast

from lawvm.core.ir import IRNode, LegalAddress
from lawvm.core.semantic_types import IRNodeKind, SpanKind
from lawvm.core.span_anchor import (
    SectionAnchors,
    SpanAnchor,
    extract_all_anchors,
    extract_span_anchors,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_section(label: str, children: Sequence[IRNode]) -> IRNode:
    """Build a section IRNode with given children."""
    return IRNode(kind=IRNodeKind.SECTION, label=label, children=tuple(children))


def _make_subsection(
    label: str,
    text: str = "",
    children: Sequence[IRNode] | None = None,
) -> IRNode:
    """Build a subsection IRNode."""
    if children is None:
        children = (IRNode(kind=IRNodeKind.CONTENT, text=text),) if text else ()
    return IRNode(kind=IRNodeKind.SUBSECTION, label=label, children=tuple(children))


def _make_item(label: str, text: str) -> IRNode:
    """Build an item IRNode."""
    return IRNode(kind=IRNodeKind.ITEM, label=label, text=text)


def _make_heading(text: str) -> IRNode:
    return IRNode(kind=IRNodeKind.HEADING, text=text)


def _make_intro(text: str) -> IRNode:
    return IRNode(kind=IRNodeKind.INTRO, text=text)


def _addr(*parts: tuple[str, str]) -> LegalAddress:
    return LegalAddress(path=parts)


# ---------------------------------------------------------------------------
# Test 1: Single section with subsections -> correct anchors
# ---------------------------------------------------------------------------

def test_section_with_subsections_produces_correct_anchors() -> None:
    section = _make_section("12", [
        _make_subsection("1", "First subsection text."),
        _make_subsection("2", "Second subsection text."),
    ])
    result = extract_span_anchors(section, _addr(("chapter", "3"), ("section", "12")))

    assert isinstance(result, SectionAnchors)
    assert result.section_address == _addr(("chapter", "3"), ("section", "12"))
    assert len(result.anchors) > 0
    # At minimum: 2 subsection anchors + their content children
    subsection_anchors = [a for a in result.anchors if a.span_kind is SpanKind.SUBSECTION]
    assert len(subsection_anchors) == 2
    assert subsection_anchors[0].span_index == 0
    assert subsection_anchors[1].span_index == 1
    assert subsection_anchors[0].section_address == _addr(("chapter", "3"), ("section", "12"))


# ---------------------------------------------------------------------------
# Test 2: Section with items in subsections -> nested anchors
# ---------------------------------------------------------------------------

def test_section_with_nested_items_produces_nested_anchors() -> None:
    section = _make_section("5", [
        _make_subsection(
            "1",
            children=(
                _make_intro("The following items apply:"),
                _make_item("1", "First item."),
                _make_item("2", "Second item."),
            ),
        ),
    ])
    result = extract_span_anchors(section, _addr(("section", "5")))

    # Should have: 1 subsection anchor + nested intro + 2 nested item anchors
    kinds = [a.span_kind for a in result.anchors]
    assert SpanKind.SUBSECTION in kinds
    assert SpanKind.ITEM in kinds
    item_anchors = [a for a in result.anchors if a.span_kind is SpanKind.ITEM]
    assert len(item_anchors) == 2
    assert item_anchors[0].text_preview == "First item."
    assert item_anchors[1].text_preview == "Second item."


def test_nested_item_anchors_do_not_collide_across_subsections() -> None:
    section = _make_section("5", [
        _make_subsection("1", children=(_make_item("a", "Same item."),)),
        _make_subsection("2", children=(_make_item("a", "Same item."),)),
    ])
    result = extract_span_anchors(section, _addr(("section", "5")))

    item_anchors = [a for a in result.anchors if a.span_kind is SpanKind.ITEM]
    assert len(item_anchors) == 2
    assert item_anchors[0].span_path == (0, 0)
    assert item_anchors[1].span_path == (1, 0)
    assert item_anchors[0].anchor_id != item_anchors[1].anchor_id


# ---------------------------------------------------------------------------
# Test 3: Empty section -> no anchors
# ---------------------------------------------------------------------------

def test_empty_section_produces_no_anchors() -> None:
    section = _make_section("99", [])
    result = extract_span_anchors(section, _addr(("section", "99")))

    assert result.anchors == ()
    assert result.section_address == _addr(("section", "99"))


# ---------------------------------------------------------------------------
# Test 4: anchor_id is deterministic
# ---------------------------------------------------------------------------

def test_anchor_id_is_deterministic() -> None:
    anchor1 = SpanAnchor(
        section_address=_addr(("chapter", "1"), ("section", "5")),
        span_kind=SpanKind.SUBSECTION,
        span_index=0,
        text_hash="abc123def456",
        text_preview="Some text here",
    )
    anchor2 = SpanAnchor(
        section_address=_addr(("chapter", "1"), ("section", "5")),
        span_kind=SpanKind.SUBSECTION,
        span_index=0,
        text_hash="abc123def456",
        text_preview="Some text here",
    )
    assert anchor1.anchor_id == anchor2.anchor_id
    assert len(anchor1.anchor_id) == 24  # 24 hex chars


# ---------------------------------------------------------------------------
# Test 5: anchor_id changes when content changes
# ---------------------------------------------------------------------------

def test_anchor_id_changes_when_content_changes() -> None:
    anchor_v1 = SpanAnchor(
        section_address=_addr(("section", "1")),
        span_kind=SpanKind.SUBSECTION,
        span_index=0,
        text_hash="aaaa" * 16,
        text_preview="Original text",
    )
    anchor_v2 = SpanAnchor(
        section_address=_addr(("section", "1")),
        span_kind=SpanKind.SUBSECTION,
        span_index=0,
        text_hash="bbbb" * 16,
        text_preview="Modified text",
    )
    assert anchor_v1.anchor_id != anchor_v2.anchor_id


# ---------------------------------------------------------------------------
# Test 6: extract_all_anchors on a body with multiple sections
# ---------------------------------------------------------------------------

def test_extract_all_anchors_multiple_sections() -> None:
    body = IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.CHAPTER, label="1", children=(_make_section("1", [
                _make_subsection("1", "Chapter 1, section 1 text."),
            ]),
            _make_section("2", [
                _make_subsection("1", "Chapter 1, section 2 text."),
                _make_subsection("2", "More text."),
            ]),)),
        IRNode(kind=IRNodeKind.CHAPTER, label="2", children=(_make_section("3", [
                _make_heading("A heading"),
            ]),)),))
    result = extract_all_anchors(body)

    assert len(result) == 3
    assert _addr(("chapter", "1"), ("section", "1")) in result
    assert _addr(("chapter", "1"), ("section", "2")) in result
    assert _addr(("chapter", "2"), ("section", "3")) in result

    # Section 2 in chapter 1 should have 2 subsection anchors plus their nested content
    s2_anchors = result[_addr(("chapter", "1"), ("section", "2"))]
    subsection_anchors = [a for a in s2_anchors.anchors if a.span_kind is SpanKind.SUBSECTION]
    assert len(subsection_anchors) == 2


# ---------------------------------------------------------------------------
# Test 7: SpanAnchor is frozen/hashable
# ---------------------------------------------------------------------------

def test_span_anchor_is_frozen_and_hashable() -> None:
    anchor = SpanAnchor(
        section_address=_addr(("section", "1")),
        span_kind=SpanKind.SUBSECTION,
        span_index=0,
        text_hash="deadbeef" * 8,
        text_preview="test",
    )
    # Should be hashable (usable in sets/dicts)
    s = {anchor}
    assert anchor in s

    # Should be frozen (immutable)
    with pytest.raises(AttributeError):
        cast_anchor = cast(Any, anchor)
        cast_anchor.span_index = 5


# ---------------------------------------------------------------------------
# Test 8: text_preview truncates correctly
# ---------------------------------------------------------------------------

def test_text_preview_truncates_at_80_chars() -> None:
    long_text = "A" * 200
    section = _make_section("1", [
        _make_subsection("1", long_text),
    ])
    result = extract_span_anchors(section, _addr(("section", "1")))

    # Find the subsection anchor (top-level)
    sub_anchors = [a for a in result.anchors if a.span_kind is SpanKind.SUBSECTION]
    assert len(sub_anchors) == 1
    assert len(sub_anchors[0].text_preview) == 80
    assert sub_anchors[0].text_preview == "A" * 80


# ---------------------------------------------------------------------------
# Test 9: content_hash is populated for non-empty sections
# ---------------------------------------------------------------------------

def test_section_anchors_has_content_hash() -> None:
    section = _make_section("1", [
        _make_subsection("1", "Some provision text."),
    ])
    result = extract_span_anchors(section, _addr(("section", "1")))

    assert result.content_hash != ""
    assert len(result.content_hash) == 64  # SHA-256 hex


# ---------------------------------------------------------------------------
# Test 10: SectionAnchors is frozen/hashable
# ---------------------------------------------------------------------------

def test_section_anchors_is_frozen_and_hashable() -> None:
    sa = SectionAnchors(
        section_address=_addr(("section", "1")),
        anchors=(),
        content_hash="abc",
    )
    s = {sa}
    assert sa in s

    with pytest.raises(AttributeError):
        cast_sa = cast(Any, sa)
        cast_sa.section_address = "changed"


# ---------------------------------------------------------------------------
# Test 11: Sections directly under body (no chapter wrapper)
# ---------------------------------------------------------------------------

def test_extract_all_anchors_sections_at_body_level() -> None:
    body = IRNode(kind=IRNodeKind.BODY, children=(_make_section("1", [
            _make_subsection("1", "Direct section text."),
        ]),))
    result = extract_all_anchors(body)

    assert len(result) == 1
    assert _addr(("section", "1")) in result


# ---------------------------------------------------------------------------
# Test 12: Heading-only section produces heading anchor
# ---------------------------------------------------------------------------

def test_heading_anchor_produced() -> None:
    section = _make_section("7", [
        _make_heading("Special provisions"),
        _make_subsection("1", "Content."),
    ])
    result = extract_span_anchors(section, _addr(("section", "7")))

    heading_anchors = [a for a in result.anchors if a.span_kind is SpanKind.HEADING]
    assert len(heading_anchors) == 1
    assert heading_anchors[0].text_preview == "Special provisions"


def test_span_anchor_rejects_mismatched_path_and_index() -> None:
    with pytest.raises(ValueError, match="span_path must terminate at span_index"):
        SpanAnchor(
            section_address=_addr(("section", "7")),
            span_kind=SpanKind.HEADING,
            span_index=1,
            span_path=(0,),
            text_hash="deadbeef" * 8,
            text_preview="Special provisions",
        )

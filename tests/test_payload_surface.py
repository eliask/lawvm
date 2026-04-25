"""Tests for build_payload_surface factory (payload_surface.py)."""

from typing import Any, cast

import pytest

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind, PayloadSourceShape
from lawvm.core.payload_surface import GroupSurface, PayloadSurface, build_group_surface, build_payload_surface


def _node(kind: str, label=None, **attrs) -> IRNode:
    """Convenience: build a leaf IRNode."""
    return IRNode(kind=cast(Any, kind), label=label, attrs=dict(attrs))


def _section(*children) -> IRNode:
    """Convenience: build a section IRNode with the given children."""
    return IRNode(kind=IRNodeKind.SECTION, label="1", children=tuple(children))


# ---------------------------------------------------------------------------
# Test 1: None input → empty surface
# ---------------------------------------------------------------------------

def test_build_payload_surface_none_returns_empty():
    """None section_ir produces an 'empty' PayloadSurface with zero counts."""
    ps = build_payload_surface(None)
    assert isinstance(ps, PayloadSurface)
    assert ps.section_ir is None
    assert ps.source_shape == PayloadSourceShape.EMPTY
    assert ps.omission_positions == ()
    assert ps.subsection_count == 0
    assert ps.has_heading is False
    assert ps.has_intro is False
    assert ps.tags == frozenset()


def test_build_payload_surface_none_with_cross_ir():
    """None section_ir with a cross_ir still captures cross_heading_ir."""
    cross = _node("crossHeading", label="A")
    ps = build_payload_surface(None, cross_ir=cross)
    assert ps.section_ir is None
    assert ps.cross_heading_ir is cross
    assert ps.source_shape == PayloadSourceShape.EMPTY


# ---------------------------------------------------------------------------
# Test 2: Sparse subsections with omission → correct shape + positions
# ---------------------------------------------------------------------------

def test_build_payload_surface_sparse_subsections():
    """Two subsections and an omission → sparse_subsections shape."""
    sec = _section(
        _node("subsection", label="1"),
        _node("omission"),
        _node("subsection", label="3"),
    )
    ps = build_payload_surface(sec)
    assert ps.source_shape == PayloadSourceShape.SPARSE_SUBSECTIONS
    assert ps.subsection_count == 2
    assert ps.omission_positions == (1,)
    assert "omission_tail" not in ps.tags
    assert "omission_head" not in ps.tags


# ---------------------------------------------------------------------------
# Test 3: Omission at tail + head → omission_tail / omission_head tags
# ---------------------------------------------------------------------------

def test_build_payload_surface_omission_head_and_tail_tags():
    """Omission at index 0 → omission_head; at last index → omission_tail."""
    sec = _section(
        _node("omission"),
        _node("subsection", label="2"),
        _node("omission"),
    )
    ps = build_payload_surface(sec)
    assert "omission_head" in ps.tags
    assert "omission_tail" in ps.tags
    assert ps.omission_positions == (0, 2)
    assert ps.subsection_count == 1
    # Still sparse because there's a subsection + omission
    assert ps.source_shape == PayloadSourceShape.SPARSE_SUBSECTIONS


# ---------------------------------------------------------------------------
# Test 4: Single subsection, no omissions → single_subsection shape
# ---------------------------------------------------------------------------

def test_build_payload_surface_single_subsection():
    """Exactly one subsection, no omissions → single_subsection."""
    sec = _section(
        _node("heading"),
        _node("intro"),
        _node("subsection", label="1"),
    )
    ps = build_payload_surface(sec)
    assert ps.source_shape == PayloadSourceShape.SINGLE_SUBSECTION
    assert ps.has_heading is True
    assert ps.has_intro is True
    assert ps.subsection_count == 1
    assert ps.omission_positions == ()


# ---------------------------------------------------------------------------
# Test 5: Items-only payload → items_only shape
# ---------------------------------------------------------------------------

def test_build_payload_surface_items_only():
    """No subsections, only paragraph/item children → items_only shape."""
    sec = _section(
        _node("paragraph"),
        _node("item"),
        _node("paragraph"),
    )
    ps = build_payload_surface(sec)
    assert ps.source_shape == PayloadSourceShape.ITEMS_ONLY
    assert ps.subsection_count == 0
    assert ps.omission_positions == ()


# ---------------------------------------------------------------------------
# Test 6: Table child → has_table tag; cross_ir propagated
# ---------------------------------------------------------------------------

def test_build_payload_surface_table_tag_and_cross_ir():
    """Table child sets has_table tag; cross_ir is forwarded to cross_heading_ir."""
    cross = _node("crossHeading", label="B")
    sec = _section(
        _node("heading"),
        _node("table"),
    )
    ps = build_payload_surface(sec, cross_ir=cross, source_statute="2023/42")
    assert "has_table" in ps.tags
    assert ps.cross_heading_ir is cross
    assert ps.source_statute == "2023/42"
    # No subsections, no omissions, but has a table (not purely paragraph/item)
    assert ps.source_shape == PayloadSourceShape.WHOLE_SECTION


# ---------------------------------------------------------------------------
# Test 7: hcontainer name=omission variant is detected as omission
# ---------------------------------------------------------------------------

def test_build_payload_surface_hcontainer_omission_variant():
    """hcontainer with name='omission' is treated as an omission position."""
    omission_node = IRNode(kind=IRNodeKind.HCONTAINER, attrs={"name": "omission"})
    sec = _section(
        _node("subsection", label="1"),
        omission_node,
    )
    ps = build_payload_surface(sec)
    assert ps.omission_positions == (1,)
    assert "omission_tail" in ps.tags
    assert ps.source_shape == PayloadSourceShape.SPARSE_SUBSECTIONS


def test_build_group_surface_uses_neutral_field_names():
    """GroupSurface exposes neutral body/cross-heading field names."""
    body = _section(_node("subsection", label="1"))
    cross = _node("crossHeading", label="A")
    gs = build_group_surface(
        body,
        cross,
        source_statute="2023/42",
        target_unit_kind="section",
        target_norm="1",
        target_chapter=None,
    )
    assert isinstance(gs, GroupSurface)
    assert gs.body_ir is body
    assert gs.cross_heading_ir is cross
    assert gs.source_statute == "2023/42"
    assert gs.target_unit_kind == "section"
    assert gs.target_norm == "1"


def test_payload_surface_direct_init_rejects_inconsistent_empty_payload() -> None:
    with pytest.raises(ValueError, match="source_shape=EMPTY"):
        PayloadSurface(
            section_ir=_section(),
            cross_heading_ir=None,
            omission_positions=(),
            subsection_count=0,
            has_heading=False,
            has_intro=False,
            source_shape=PayloadSourceShape.EMPTY,
            tags=frozenset(),
            source_statute="",
        )


def test_payload_surface_direct_init_rejects_mismatched_subsection_count() -> None:
    sec = _section(_node("subsection", label="1"))
    with pytest.raises(ValueError, match="subsection_count"):
        PayloadSurface(
            section_ir=sec,
            cross_heading_ir=None,
            omission_positions=(),
            subsection_count=0,
            has_heading=False,
            has_intro=False,
            source_shape=PayloadSourceShape.SINGLE_SUBSECTION,
            tags=frozenset(),
            source_statute="",
        )


def test_group_surface_rejects_empty_target_norm() -> None:
    with pytest.raises(ValueError, match="target_norm"):
        GroupSurface(
            body_ir=None,
            cross_heading_ir=None,
            source_statute="",
            target_unit_kind="section",
            target_norm="",
            target_chapter=None,
        )

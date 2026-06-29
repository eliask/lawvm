"""Synthetic tests for tombstone rendering in ``format_ir_tree`` / ``format_ir_pretty``.

Covers AGENTS.md §0 over-repeal visibility: when the FI materialization drops
a sourced-repeal address from ``master.ir`` silently, the show/dump formatters
must surface a ``[TOMBSTONED]`` marker inline at the target address's position
with metadata (source statute, effective/enacted dates, variant_kind).

The synthetic IR here mirrors a small statute shape (BODY → CHAPTER → one
surviving SECTION), and a tombstone record for a sibling SECTION "34" that
materialization dropped. The formatters must:
* Place the tombstone at its label-sorted position relative to existing siblings.
* Carry the source statute + effective date so a reviewer can trace the repeal.
* Stay quiet (no phantom tombstone line) when ``tombstones`` is empty.
"""
from __future__ import annotations

from typing import Any, Final, cast

from lawvm.core.ir import IRNode, LegalAddress
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.timeline_results import TombstoneRecord
from lawvm.finland.ir_tree_dump import format_ir_pretty, format_ir_tree, format_unified_statute


_TOMB_SOURCE_STATUTE: Final = "2014/1291"
_TOMB_EFFECTIVE: Final = "2015-01-01"
_TOMB_ENACTED: Final = "2014-12-18"
_TOMB_OP_ID: Final = "fi-repeal:2014/1291:sec_34"


def _build_simple_statute_ir() -> IRNode:
    """BODY → CHAPTER "5" → NUM + HEADING + SECTION "39" tree.

    The IR deliberately lacks SECTION "34" — materialization dropped it
    because the source repeal was applied; the tombstone record carries the
    metadata that lets the formatters surface the dropped address inline.
    """
    chapter = IRNode(
        kind=IRNodeKind.CHAPTER,
        label="5",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="5 luku"),
            IRNode(kind=IRNodeKind.HEADING, text="Erinäiset säännökset"),
            IRNode(
                kind=IRNodeKind.SECTION,
                label="39",
                children=(
                    IRNode(kind=IRNodeKind.NUM, text="39 §"),
                    IRNode(
                        kind=IRNodeKind.CONTENT,
                        text="Tämä on pykälän 39 teksti.",
                    ),
                ),
            ),
        ),
    )
    return IRNode(kind=IRNodeKind.BODY, children=(chapter,))


def _build_section_tombstone(label: str, *, op_id: str = _TOMB_OP_ID) -> TombstoneRecord:
    """A SECTION tombstone under CHAPTER "5" sourced from a 2014 repeal act."""
    return TombstoneRecord(
        address=LegalAddress(
            path=(("chapter", "5"), ("section", label)),
        ),
        kind="section",
        label=label,
        source_statute=_TOMB_SOURCE_STATUTE,
        effective=_TOMB_EFFECTIVE,
        enacted=_TOMB_ENACTED,
        variant_kind="permanent",
        op_id=op_id,
    )


def test_format_ir_tree_renders_tombstone_inline_with_metadata() -> None:
    body = _build_simple_statute_ir()
    tombstones = (_build_section_tombstone("34"),)

    rendered = format_ir_tree(body, tombstones=tombstones)

    assert "[TOMBSTONED" in rendered
    assert 'SECTION "34"' in rendered
    assert _TOMB_SOURCE_STATUTE in rendered
    assert _TOMB_EFFECTIVE in rendered
    assert _TOMB_ENACTED in rendered
    assert "variant_kind=\"permanent\"" in rendered
    assert f'op_id="{_TOMB_OP_ID}"' in rendered


def test_format_ir_tree_places_tombstone_before_later_real_section() -> None:
    """Tombstone's label-sorted position precedes the next surviving section."""
    body = _build_simple_statute_ir()
    tombstones = (_build_section_tombstone("34"),)

    rendered = format_ir_tree(body, tombstones=tombstones)

    tomb_pos = rendered.index("[TOMBSTONED")
    real_section_pos = rendered.index('SECTION "39"')
    assert tomb_pos < real_section_pos, (
        "tombstone should appear before the next surviving section "
        "(label-sorted interleave), not appended at the end of the chapter"
    )


def test_format_ir_tree_with_empty_tombstones_has_no_marker() -> None:
    body = _build_simple_statute_ir()
    rendered = format_ir_tree(body, tombstones=())
    assert "[TOMBSTONED" not in rendered


def test_format_ir_pretty_renders_tombstone_marker_with_source_statute() -> None:
    body = _build_simple_statute_ir()
    tombstones = (_build_section_tombstone("34"),)

    rendered = format_ir_pretty(body, tombstones=tombstones)

    assert "[TOMBSTONED" in rendered
    assert _TOMB_SOURCE_STATUTE in rendered
    assert _TOMB_EFFECTIVE in rendered
    # Pretty form prefixes section with the § marker (Finnish convention).
    assert "34 §" in rendered


def test_format_ir_pretty_filters_out_tombstone_when_real_child_shares_label() -> None:
    """A stale tombstone that matches an existing labeled child is suppressed.

    The address is present in the tree, so rendering both the real node and a
    phantom tombstone would duplicate the entry. Surfacing stays additive —
    it never re-mints a present address.
    """
    chapter = IRNode(
        kind=IRNodeKind.CHAPTER,
        label="5",
        children=(
            IRNode(kind=IRNodeKind.HEADING, text="Erinäiset säännökset"),
            IRNode(
                kind=IRNodeKind.SECTION,
                label="34",
                children=(
                    IRNode(kind=IRNodeKind.CONTENT, text="Tämä on pykälän 34 teksti."),
                ),
            ),
            IRNode(
                kind=IRNodeKind.SECTION,
                label="39",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="Pykälän 39 teksti."),),
            ),
        ),
    )
    body = IRNode(kind=IRNodeKind.BODY, children=(chapter,))
    stale_tombstone = _build_section_tombstone("34")

    rendered = format_ir_pretty(body, tombstones=(stale_tombstone,))

    # Real section's body renders, but no phantom tombstone line appears.
    assert "Tämä on pykälän 34 teksti." in rendered
    assert "[TOMBSTONED" not in rendered


def_tombstone_multiple_labels_preserve_label_order = (
    # Module-level sentinel kept here for readability; actual implementation is
    # in the test below. Tombstones for sibling labels render label-sorted.
)


def test_format_ir_pretty_renders_multiple_sibling_tombstones_label_sorted() -> None:
    body = _build_simple_statute_ir()
    tombstones = (
        _build_section_tombstone("35"),
        _build_section_tombstone("34"),
    )

    rendered = format_ir_pretty(body, tombstones=tombstones)

    # Both tombstones surface with their source statute + effective date.
    assert rendered.count("[TOMBSTONED") == 2
    assert "34 §" in rendered
    assert "35 §" in rendered
    # Label-sorted order: 34 before 35 before the surviving 39.
    position_34 = rendered.index("34 § [TOMBSTONED")
    position_35 = rendered.index("35 § [TOMBSTONED")
    position_39 = rendered.index("39 §")
    assert position_34 < position_35 < position_39


def test_format_unified_statute_threads_tombstones_into_pretty_walk() -> None:
    body = _build_simple_statute_ir()
    tombstones = (_build_section_tombstone("34"),)

    rendered = format_unified_statute(
        body,
        attachment_supplements=(),
        tombstones=tombstones,
    )

    assert "[TOMBSTONED" in rendered
    assert _TOMB_SOURCE_STATUTE in rendered
    assert _TOMB_EFFECTIVE in rendered


def test_tombstone_record_rejects_empty_kind_or_label() -> None:
    import pytest

    valid_addr = LegalAddress(path=(("chapter", "5"), ("section", "34")))
    with pytest.raises(ValueError):
        TombstoneRecord(
            address=valid_addr,
            kind="",
            label="34",
            source_statute=_TOMB_SOURCE_STATUTE,
            effective=_TOMB_EFFECTIVE,
        )
    with pytest.raises(ValueError):
        TombstoneRecord(
            address=valid_addr,
            kind="section",
            label="",
            source_statute=_TOMB_SOURCE_STATUTE,
            effective=_TOMB_EFFECTIVE,
        )
    with pytest.raises(ValueError):
        TombstoneRecord(
            address=valid_addr,
            kind="section",
            label="34",
            source_statute="",
            effective=_TOMB_EFFECTIVE,
        )


def test_tombstone_record_rejects_unsupported_variant_kind() -> None:
    import pytest

    valid_addr = LegalAddress(path=(("chapter", "5"), ("section", "34")))
    with pytest.raises(ValueError):
        TombstoneRecord(
            address=valid_addr,
            kind="section",
            label="34",
            source_statute=_TOMB_SOURCE_STATUTE,
            effective=_TOMB_EFFECTIVE,
            variant_kind=cast(Any, "unknown"),
        )


def test_tombstone_record_rejects_empty_address_path() -> None:
    import pytest

    empty_addr = LegalAddress(path=())
    with pytest.raises(ValueError):
        TombstoneRecord(
            address=empty_addr,
            kind="section",
            label="34",
            source_statute=_TOMB_SOURCE_STATUTE,
        )

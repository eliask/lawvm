"""Tests for the FI typed target-selector constructor facades.

The facades (``fi_section_target`` / ``fi_chapter_target`` / ``fi_part_target``)
are the sanctioned typed front door for constructing an ``AmendmentOp`` target.
Each builds a :class:`TargetSelector` and returns it wrapped as the single
``target_selector`` construction kwarg (W6 Phase C: ``AmendmentOp`` stores the
typed selector directly; the legacy 8-column construction kwargs are gone).

These tests prove the facade's selector lowers to the expected legacy 8-column
record exactly — i.e. the typed entry point reproduces the intended target shape
with ZERO behaviour change. Representative shapes mirror the codec's golden
fixtures (``test_target_selector_codec.py``): plain section, section-in-chapter,
section+chapter+part, chapter, part (+redundant-scope), section+facet,
section+subsection+item(+subitem).
"""

from __future__ import annotations

from dataclasses import replace as dc_replace

import pytest

from lawvm.finland.target_selector_codec import (
    AmendmentOpV1Record,
    TargetSelectorCodecV1,
)
from lawvm.finland.ops import AmendmentOp, OpType
from lawvm.finland.target_selector_facades import (
    TargetSelectorKwarg,
    fi_chapter_target,
    fi_part_target,
    fi_section_target,
    replace_target,
)


# (facade-produced ``target_selector`` kwarg, hand-written legacy record) pairs.
# The right side is the exact 8-column shape the facade's selector must lower to.
_SELECTOR_CASES: list[tuple[TargetSelectorKwarg, AmendmentOpV1Record]] = [
    # plain section
    (
        fi_section_target("2"),
        AmendmentOpV1Record("section", "2", None, None, None, None, None, None),
    ),
    # section in chapter scope
    (
        fi_section_target("73", chapter="7"),
        AmendmentOpV1Record("section", "73", "7", None, None, None, None, None),
    ),
    # section + chapter + part scope
    (
        fi_section_target("8", chapter="1", part="2"),
        AmendmentOpV1Record("section", "8", "1", "2", None, None, None, None),
    ),
    # section + momentti
    (
        fi_section_target("1", subsection=3),
        AmendmentOpV1Record("section", "1", None, None, 3, None, None, None),
    ),
    # section + momentti + kohta
    (
        fi_section_target("32", subsection=1, item="h"),
        AmendmentOpV1Record("section", "32", None, None, 1, "h", None, None),
    ),
    # section + momentti + kohta + alakohta
    (
        fi_section_target("5", subsection=2, item="3", subitem="a"),
        AmendmentOpV1Record("section", "5", None, None, 2, "3", "a", None),
    ),
    # section + facet otsikko
    (
        fi_section_target("2", special_raw="otsikko"),
        AmendmentOpV1Record("section", "2", None, None, None, None, None, "otsikko"),
    ),
    # section + facet otsikko_edella (lossy under FacetKind, kept via special_raw)
    (
        fi_section_target("2", special_raw="otsikko_edella"),
        AmendmentOpV1Record(
            "section", "2", None, None, None, None, None, "otsikko_edella"
        ),
    ),
    # section + momentti + facet johd
    (
        fi_section_target("1", subsection=1, special_raw="johd"),
        AmendmentOpV1Record("section", "1", None, None, 1, None, None, "johd"),
    ),
    # chapter focus
    (
        fi_chapter_target("7"),
        AmendmentOpV1Record("chapter", "7", None, None, None, None, None, None),
    ),
    # chapter + part scope
    (
        fi_chapter_target("5", part="2"),
        AmendmentOpV1Record("chapter", "5", None, "2", None, None, None, None),
    ),
    # bare part (no redundant target_part column)
    (
        fi_part_target("5"),
        AmendmentOpV1Record("part", "5", None, None, None, None, None, None),
    ),
    # part carrying the redundant target_part column (W2 corpus finding shape)
    (
        fi_part_target("III", redundant_part_scope=True),
        AmendmentOpV1Record("part", "III", None, "III", None, None, None, None),
    ),
]


@pytest.mark.parametrize("produced,expected_rec", _SELECTOR_CASES)
def test_facade_selector_lowers_to_expected_record(
    produced: TargetSelectorKwarg, expected_rec: AmendmentOpV1Record
) -> None:
    """Each facade emits a single ``target_selector`` kwarg lowering to the record."""
    assert set(produced) == {"target_selector"}
    assert TargetSelectorCodecV1.to_legacy(produced["target_selector"]) == expected_rec


@pytest.mark.parametrize("produced,expected_rec", _SELECTOR_CASES)
def test_facade_selector_round_trips_through_codec(
    produced: TargetSelectorKwarg, expected_rec: AmendmentOpV1Record
) -> None:
    """The facade selector and the codec's golden selector for that record agree."""
    golden = TargetSelectorCodecV1.from_legacy(expected_rec)
    assert produced["target_selector"] == golden


@pytest.mark.parametrize("produced,expected_rec", _SELECTOR_CASES)
def test_facade_kwarg_splats_into_amendment_op(
    produced: TargetSelectorKwarg, expected_rec: AmendmentOpV1Record
) -> None:
    """Splatting the facade kwarg builds an op whose target_cols match the record."""
    op = AmendmentOp(op_id="", op_type=OpType.REPLACE, **produced)
    assert op.target_cols == expected_rec


def test_section_target_unknown_facet_token_fails_loud() -> None:
    """An unknown special token is rejected (fail-loud, not silently dropped)."""
    with pytest.raises(ValueError, match="unknown special token"):
        fi_section_target("2", special_raw="not_a_real_token")


def test_part_target_without_redundant_scope_omits_target_part() -> None:
    """A bare part op must NOT populate target_part (only the redundant shape does)."""
    op = AmendmentOp(op_id="", op_type=OpType.REPLACE, **fi_part_target("5"))
    assert op.target_cols.target_part is None
    assert op.target_cols.target_unit_kind == "part"
    assert op.target_cols.target_section == "5"


def test_chapter_target_label_lands_in_target_section() -> None:
    """The legacy encoding stores the focus label in target_section for any kind."""
    op = AmendmentOp(op_id="", op_type=OpType.REPLACE, **fi_chapter_target("9"))
    assert op.target_cols.target_unit_kind == "chapter"
    assert op.target_cols.target_section == "9"
    assert op.target_cols.target_chapter is None


# --- replace_target (typed partial re-target) -------------------------------

# Representative op shapes spanning section/chapter/part focus, scope columns,
# descendant tails, the redundant part-scope shape, and a facet. Each must
# round-trip byte-identically under a no-op replace_target call.
_REPLACE_TARGET_OP_SHAPES: list[AmendmentOp] = [
    AmendmentOp(op_type=OpType.REPLACE, target_unit_kind="section", target_section="5"),
    AmendmentOp(
        op_type=OpType.REPLACE,
        target_unit_kind="section",
        target_section="73",
        target_chapter="7",
    ),
    AmendmentOp(
        op_type=OpType.REPLACE,
        target_unit_kind="section",
        target_section="3",
        target_chapter="2",
        target_part="II",
        target_paragraph=1,
        target_item="4",
    ),
    AmendmentOp(
        op_type=OpType.REPLACE,
        target_unit_kind="section",
        target_section="8",
        target_special="otsikko",
    ),
    AmendmentOp(op_type=OpType.REPLACE, target_unit_kind="chapter", target_section="9"),
    AmendmentOp(op_type=OpType.REPLACE, target_unit_kind="part", target_section="III"),
    # part op carrying the redundant target_part column equal to the focus.
    AmendmentOp(
        op_type=OpType.REPLACE,
        target_unit_kind="part",
        target_section="V",
        target_part="V",
    ),
]


@pytest.mark.parametrize("op", _REPLACE_TARGET_OP_SHAPES)
def test_replace_target_noop_is_byte_identical(op: AmendmentOp) -> None:
    """A no-override replace_target reproduces the op's stored target exactly."""
    produced = replace_target(op)
    assert set(produced) == {"target_selector"}
    assert produced["target_selector"] == op.target_selector


def test_replace_target_item_override_changes_only_item() -> None:
    """Overriding target_item changes that column and preserves all others."""
    op = AmendmentOp(
        op_type=OpType.REPLACE,
        target_unit_kind="section",
        target_section="3",
        target_chapter="2",
        target_paragraph=1,
        target_item="4",
    )
    new_op = dc_replace(op, **replace_target(op, target_item="7"), lo=None)
    expected = AmendmentOpV1Record("section", "3", "2", None, 1, "7", None, None)
    assert new_op.target_cols == expected


def test_replace_target_clear_special_with_none() -> None:
    """Passing None clears a column (distinct from the unset sentinel)."""
    op = AmendmentOp(
        op_type=OpType.REPLACE,
        target_unit_kind="section",
        target_section="8",
        target_special="otsikko",
    )
    new_op = dc_replace(op, **replace_target(op, target_special=None), lo=None)
    assert new_op.target_cols.target_special is None
    # Unchanged: the focus and unit kind.
    assert new_op.target_cols.target_section == "8"
    assert new_op.target_cols.target_unit_kind == "section"


def test_replace_target_fails_loud_on_empty_string_override() -> None:
    """An empty-string override cannot round-trip; the helper fails loud.

    The op's stored selector can never carry an empty-string chapter (the codec
    drops it at construction), so the only way an empty string reaches the codec
    boundary is an explicit override — which is rejected rather than silently
    cleared.
    """
    op = AmendmentOp(
        op_type=OpType.REPLACE,
        target_unit_kind="section",
        target_section="5",
    )
    with pytest.raises(ValueError, match="empty-string target_chapter"):
        replace_target(op, target_chapter="")


def test_replace_target_splats_into_dataclasses_replace() -> None:
    """The returned kwarg splats into dataclasses.replace as the dispatch site uses."""
    op = AmendmentOp(
        op_type=OpType.REPLACE,
        target_unit_kind="section",
        target_section="3",
        target_paragraph=2,
    )
    new_op = dc_replace(op, **replace_target(op, target_item="5"), lo=None)
    assert new_op.target_cols.target_item == "5"
    assert new_op.target_cols.target_section == "3"
    assert new_op.target_cols.target_paragraph == 2
    assert new_op.lo is None

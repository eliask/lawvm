"""Byte-identity tests for the FI typed ``target_*`` constructor facades (W3a).

The facades (``fi_section_target`` / ``fi_chapter_target`` / ``fi_part_target``)
are the sanctioned typed front door for constructing an ``AmendmentOp`` target.
Each builds a :class:`TargetSelector` and lowers it through the existing
``TargetSelectorCodecV1.to_legacy`` codec.

These tests prove the facade is *byte-identical* to hand-writing the legacy
``target_*`` kwargs for the same logical shape — i.e. it adds a typed entry point
with ZERO behaviour change. Representative shapes mirror the codec's golden
fixtures (``test_target_selector_codec.py``): plain section, section-in-chapter,
section+chapter+part, chapter, part (+redundant-scope), section+facet,
section+subsection+item(+subitem).
"""

from __future__ import annotations

from typing import cast

import pytest

from lawvm.core.target_scope import TargetUnitKind

from lawvm.finland.target_selector_codec import (
    AmendmentOpV1Record,
    TargetSelectorCodecV1,
)
from lawvm.finland.target_selector_facades import (
    LegacyTargetKwargs,
    fi_chapter_target,
    fi_part_target,
    fi_section_target,
)


def _legacy_kwargs(rec: AmendmentOpV1Record) -> LegacyTargetKwargs:
    """Hand-written legacy kwargs equivalent of a record (the byte-identity oracle)."""
    return LegacyTargetKwargs(
        target_unit_kind=cast(TargetUnitKind, rec.target_unit_kind),
        target_section=rec.target_section,
        target_chapter=rec.target_chapter,
        target_part=rec.target_part,
        target_paragraph=rec.target_paragraph,
        target_item=rec.target_item,
        target_subitem=rec.target_subitem,
        target_special=rec.target_special,
    )


# (facade-produced kwargs, hand-written legacy record) pairs. The right side is
# the exact 8-column shape a producer would otherwise write by hand.
_BYTE_IDENTITY_CASES: list[tuple[LegacyTargetKwargs, AmendmentOpV1Record]] = [
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


@pytest.mark.parametrize(
    "produced,expected_rec",
    _BYTE_IDENTITY_CASES,
    ids=lambda x: x.target_unit_kind if isinstance(x, AmendmentOpV1Record) else "",
)
def test_facade_is_byte_identical_to_handwritten_kwargs(
    produced: LegacyTargetKwargs, expected_rec: AmendmentOpV1Record
) -> None:
    """Each facade's kwargs equal the hand-written legacy kwargs exactly."""
    assert produced == _legacy_kwargs(expected_rec)


@pytest.mark.parametrize("produced,expected_rec", _BYTE_IDENTITY_CASES)
def test_facade_round_trips_through_codec(
    produced: LegacyTargetKwargs, expected_rec: AmendmentOpV1Record
) -> None:
    """The facade kwargs reconstruct the expected legacy record exactly.

    Rebuilds an ``AmendmentOpV1Record`` from the facade kwargs and asserts it is
    byte-identical to the hand-written record — closing the loop with the codec's
    own ``test_target_selector_codec`` golden fixtures.
    """
    rebuilt = AmendmentOpV1Record(
        target_unit_kind=produced["target_unit_kind"],
        target_section=produced["target_section"],
        target_chapter=produced["target_chapter"],
        target_part=produced["target_part"],
        target_paragraph=produced["target_paragraph"],
        target_item=produced["target_item"],
        target_subitem=produced["target_subitem"],
        target_special=produced["target_special"],
    )
    assert rebuilt == expected_rec
    # And re-encoding the codec's selector for that record matches too.
    selector = TargetSelectorCodecV1.from_legacy(expected_rec)
    assert TargetSelectorCodecV1.to_legacy(selector) == rebuilt


def test_section_target_unknown_facet_token_fails_loud() -> None:
    """An unknown special token is rejected (fail-loud, not silently dropped)."""
    with pytest.raises(ValueError, match="unknown special token"):
        fi_section_target("2", special_raw="not_a_real_token")


def test_part_target_without_redundant_scope_omits_target_part() -> None:
    """A bare part op must NOT populate target_part (only the redundant shape does)."""
    kwargs = fi_part_target("5")
    assert kwargs["target_part"] is None
    assert kwargs["target_unit_kind"] == "part"
    assert kwargs["target_section"] == "5"


def test_chapter_target_label_lands_in_target_section() -> None:
    """The legacy encoding stores the focus label in target_section for any kind."""
    kwargs = fi_chapter_target("9")
    assert kwargs["target_unit_kind"] == "chapter"
    assert kwargs["target_section"] == "9"
    assert kwargs["target_chapter"] is None

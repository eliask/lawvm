"""Tests for ``lawvm.roman`` shared Roman numeral utilities."""

from __future__ import annotations

from typing import Any, cast

import pytest

from lawvm.roman import arabic_to_roman, roman_to_arabic


CANONICAL_PAIRS = [
    (1, "I"),
    (2, "II"),
    (3, "III"),
    (4, "IV"),
    (5, "V"),
    (6, "VI"),
    (7, "VII"),
    (8, "VIII"),
    (9, "IX"),
    (10, "X"),
    (11, "XI"),
    (14, "XIV"),
    (19, "XIX"),
    (20, "XX"),
    (40, "XL"),
    (49, "XLIX"),
    (50, "L"),
    (90, "XC"),
    (99, "XCIX"),
    (100, "C"),
    (400, "CD"),
    (500, "D"),
    (900, "CM"),
    (1000, "M"),
    (1994, "MCMXCIV"),
    (3999, "MMMCMXCIX"),
]


@pytest.mark.parametrize("value,glyph", CANONICAL_PAIRS)
def test_arabic_to_roman_canonical(value: int, glyph: str) -> None:
    assert arabic_to_roman(value) == glyph


@pytest.mark.parametrize("value,glyph", CANONICAL_PAIRS)
def test_roman_to_arabic_canonical(value: int, glyph: str) -> None:
    assert roman_to_arabic(glyph) == value


@pytest.mark.parametrize("value,glyph", CANONICAL_PAIRS)
def test_round_trip_lowercase(value: int, glyph: str) -> None:
    assert roman_to_arabic(glyph.lower()) == value


def test_round_trip_full_range() -> None:
    for value in range(1, 4000):
        glyph = arabic_to_roman(value)
        assert roman_to_arabic(glyph) == value


@pytest.mark.parametrize(
    "non_canonical",
    [
        "IIII",   # canonical 4 is IV
        "VIIII",  # canonical 9 is IX
        "XXXX",   # canonical 40 is XL
        "LL",     # never doubled
        "DD",     # never doubled
        "VV",     # never doubled
        "IIV",    # malformed subtractive
        "IIX",    # malformed subtractive
        "VX",     # V is never subtractive
        "IC",     # I never subtracts from C
        "IL",     # I never subtracts from L
        "XD",     # X never subtracts from D
        "XM",     # X never subtracts from M
    ],
)
def test_roman_to_arabic_rejects_non_canonical(non_canonical: str) -> None:
    assert roman_to_arabic(non_canonical) is None


@pytest.mark.parametrize(
    "bad",
    ["", "  ", "ABC", "12", "I1", "X-X", "I.V"],
)
def test_roman_to_arabic_rejects_garbage(bad: str) -> None:
    assert roman_to_arabic(bad) is None


def test_roman_to_arabic_handles_whitespace() -> None:
    assert roman_to_arabic("  IV  ") == 4


def test_roman_to_arabic_rejects_non_string() -> None:
    assert roman_to_arabic(cast(Any, None)) is None
    assert roman_to_arabic(cast(Any, 4)) is None


def test_arabic_to_roman_rejects_out_of_range() -> None:
    with pytest.raises(ValueError):
        arabic_to_roman(0)
    with pytest.raises(ValueError):
        arabic_to_roman(-1)
    with pytest.raises(ValueError):
        arabic_to_roman(4000)


def test_arabic_to_roman_rejects_non_int() -> None:
    with pytest.raises(ValueError):
        arabic_to_roman(cast(Any, 1.5))
    with pytest.raises(ValueError):
        arabic_to_roman(cast(Any, "1"))
    with pytest.raises(ValueError):
        arabic_to_roman(cast(Any, True))

"""Roman numeral parsing and formatting, shared across LawVM jurisdictions.

Why this lives at the top of the package
----------------------------------------
Roman numerals appear as structural labels (``III luku``, ``Part IV``,
``Schedule II``) in every jurisdiction that LawVM ingests.  Before this
module existed there were seven independent implementations across
``finland/``, ``estonia/``, ``norway/``, ``uk_legislation/``, and a script,
each with subtly different signatures, return types, and edge-case
handling — including at least two with a quiet bug in the prev-tracking
of the subtractive algorithm.

This is not core/ material: ``src/lawvm/core/`` holds the IR kernel and
phase-contract types, which Roman numeral handling is not.  It is a
free-standing string-level utility, so it sits next to the other
top-level utilities (``corpus_store.py``, ``graph_build.py`` etc.).

Strictness
----------
``roman_to_arabic`` rejects non-canonical forms.  ``"IIII"``, ``"VV"``,
``"IIV"`` are *not* accepted, even though a permissive subtractive
parser would happily compute integer values for them.  The check is a
round-trip through ``arabic_to_roman``: only the canonical spelling for
each integer in ``1..3999`` is accepted.

Legal text in the LawVM corpus uses canonical Roman numerals.  Accepting
non-canonical spellings would let upstream OCR or transcription noise
match where it shouldn't, which is exactly the failure mode a
high-assurance compiler must avoid.
"""

from __future__ import annotations

from typing import Optional

__all__ = ["roman_to_arabic", "arabic_to_roman"]


_ROMAN_VALUES: dict[str, int] = {
    "I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000,
}

# Pairs are ordered largest-to-smallest, including the subtractive
# combinations, so the greedy algorithm in arabic_to_roman emits
# canonical forms.
_ARABIC_PAIRS: tuple[tuple[int, str], ...] = (
    (1000, "M"),
    (900, "CM"),
    (500, "D"),
    (400, "CD"),
    (100, "C"),
    (90, "XC"),
    (50, "L"),
    (40, "XL"),
    (10, "X"),
    (9, "IX"),
    (5, "V"),
    (4, "IV"),
    (1, "I"),
)

# Maximum integer expressible as a canonical Roman numeral without
# extension notation: MMMCMXCIX.
_MAX_ROMAN_VALUE = 3999


def arabic_to_roman(value: int) -> str:
    """Format ``value`` as a canonical Roman numeral string (uppercase).

    Raises ``ValueError`` if ``value`` is outside ``1..3999``.
    """
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"arabic_to_roman expects int, got {type(value).__name__}")
    if value < 1 or value > _MAX_ROMAN_VALUE:
        raise ValueError(f"arabic_to_roman value out of range 1..{_MAX_ROMAN_VALUE}: {value}")
    out: list[str] = []
    remaining = value
    for arabic, glyph in _ARABIC_PAIRS:
        while remaining >= arabic:
            out.append(glyph)
            remaining -= arabic
    return "".join(out)


def roman_to_arabic(token: str) -> Optional[int]:
    """Convert a Roman numeral token to its integer value.

    Returns ``None`` for the empty string, for tokens containing
    non-Roman characters, and for non-canonical spellings such as
    ``"IIII"`` or ``"IIV"``.  Case-insensitive; surrounding whitespace
    is stripped.

    The accepted range is ``1..3999`` (``I``..``MMMCMXCIX``).
    """
    if not isinstance(token, str):
        return None
    text = token.strip().upper()
    if not text:
        return None
    if any(ch not in _ROMAN_VALUES for ch in text):
        return None

    # Subtractive parser: walk right-to-left, subtract when the next
    # glyph is smaller than the running maximum.
    total = 0
    prev = 0
    for ch in reversed(text):
        v = _ROMAN_VALUES[ch]
        if v < prev:
            total -= v
        else:
            total += v
            prev = v

    if total < 1 or total > _MAX_ROMAN_VALUE:
        return None

    # Round-trip check rejects non-canonical spellings.  This is the
    # whole reason for the strict contract: ``"IIII"`` parses to 4 with
    # the subtractive walk, but its canonical form is ``"IV"``, so the
    # round trip mismatches and we return None.
    if arabic_to_roman(total) != text:
        return None

    return total

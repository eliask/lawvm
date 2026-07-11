"""``lawvm.ingest.text_layer_repair`` — validated glyph-substitution token repair.

Hermetic, no farchive. Exercises the GENERAL contract of
:func:`repair_glyph_substitution` (restore a known glyph confusion, adopt ONLY
when an independent validator confirms it) and proves the seam is real by
plugging in a SECOND, unrelated confusion (``O``↔``0``) with its own validator —
not a one-off rename of the FI cite helper. The REPAIR sibling of
:mod:`lawvm.ingest.suspect_region` (detection).
"""
from __future__ import annotations

import re

from lawvm.ingest.text_layer_repair import repair_glyph_substitution

# --------------------------------------------------------------------------- #
# The general mechanic: restore + independent validator.                       #
# --------------------------------------------------------------------------- #


def test_repair_adopts_only_when_validator_confirms() -> None:
    # A trivial confusion: a "#N" token whose "#" the layer dropped, leaving "xN"; restore the
    # "#" only when N is an even number (a stand-in independent constraint).
    corrupt = re.compile(r"x(\d)")
    def even_only(m):
        return int(m.group(1)) % 2 == 0
    assert repair_glyph_substitution("x2 x3 x4", corrupt_re=corrupt, restore=r"#\1", is_plausible=even_only) == "#2 x3 #4"
    # Default validator (always plausible) restores every match.
    assert repair_glyph_substitution("x2 x3", corrupt_re=corrupt, restore=r"#\1") == "#2 #3"


def test_rejected_match_is_byte_identical() -> None:
    # A match the validator rejects must leave the ORIGINAL substring untouched, not a partial
    # rebuild — so a genuine token that merely resembles the corrupt shape is never mangled.
    corrupt = re.compile(r"x(\d)")
    def never(_m):
        return False
    src = "x1 x9 keep-me"
    assert repair_glyph_substitution(src, corrupt_re=corrupt, restore=r"#\1", is_plausible=never) == src


# --------------------------------------------------------------------------- #
# Generality: a SECOND confusion (O↔0) plugs in trivially with its own          #
# validator — proving the seam is not an FI one-off.                            #
# --------------------------------------------------------------------------- #


def test_second_confusion_O_as_zero_plugs_in() -> None:
    # An ISBN-ish id whose leading "O" the font rendered as "0" ("O123" → "0123"). Restore the
    # "O" only when the whole token is a known FOUR-glyph id shape (validator: length band) — a
    # different confusion, a different validator, ZERO change to the general primitive.
    corrupt = re.compile(r"\b0(\d{3})\b")
    def four_glyph(m):
        return len(m.group(0)) == 4
    assert repair_glyph_substitution("id 0123 here", corrupt_re=corrupt, restore=r"O\1", is_plausible=four_glyph) == "id O123 here"
    # A five-digit number is NOT the id shape → the shape re never matches it, so even the
    # default (always-plausible) validator leaves it byte-identical.
    assert repair_glyph_substitution("qty 01234 units", corrupt_re=corrupt, restore=r"O\1") == "qty 01234 units"


def test_second_confusion_rn_as_m_plugs_in() -> None:
    # The classic "rn"↔"m" OCR confusion: "modem" mis-read as "modern"-ish "moderndemo" — here a
    # toy where "rnodem" should be "modem", gated on a tiny known-word validator.
    corrupt = re.compile(r"\brn(\w+)\b")
    def known(m):
        return m.group(1) in {"odem", "ap"}
    assert repair_glyph_substitution("the rnodem and rnxyz", corrupt_re=corrupt, restore=r"m\1", is_plausible=known) == "the modem and rnxyz"


# --------------------------------------------------------------------------- #
# The FI cite confusion, exercised through the GENERAL primitive directly       #
# (mirrors the registered caller in fi_he_ir_compare).                          #
# --------------------------------------------------------------------------- #


def test_fi_cite_slash_as_one_through_general_primitive() -> None:
    four = re.compile(r"\((\d{1,4})1(\d{4})\)")
    def band(m):
        return 1600 <= int(m.group(2)) <= 2099
    assert repair_glyph_substitution("(150511992)", corrupt_re=four, restore=r"(\1/\2)", is_plausible=band) == "(1505/1992)"
    # A parenthesised number whose trailing 4 digits are not a plausible year is left untouched.
    assert repair_glyph_substitution("(123499999)", corrupt_re=four, restore=r"(\1/\2)", is_plausible=band) == "(123499999)"

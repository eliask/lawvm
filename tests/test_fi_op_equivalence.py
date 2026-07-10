"""Tests for the op-equivalence quotient (``finland.op_equivalence``).

The relation folds ONLY the unarguably-inert invisible/whitespace layer and emits
everything else as a typed residual. These tests pin both halves: the inert folds
collapse (soft-hyphen joins, Cf format chars, whitespace), and — deliberately —
VISIBLE glyph differences (dashes) do NOT collapse but fall through as residuals, so a
genuine numeric/citation difference can never hide inside a fold.
"""
from __future__ import annotations

from lawvm.finland.op_equivalence import EncodingFold, text_equivalence


def test_identical_text_is_equal_with_no_folds():
    v = text_equivalence("3 § muutetaan", "3 § muutetaan")
    assert v.equal and not v.residual
    assert v.folds == ()  # clean payload → output-sparse audit trail


def test_soft_hyphen_line_join_folds():
    # discretionary soft hyphen (U+00AD) at a line break → fused word
    v = text_equivalence("kriisinrat­\nkaisusta", "kriisinratkaisusta")
    assert v.equal
    assert EncodingFold.SOFT_HYPHEN_JOIN in v.folds


def test_cf_format_char_deleted():
    # ZERO WIDTH SPACE (U+200B, category Cf) is invisible → deleted
    v = text_equivalence("sana​toinen", "sanatoinen")
    assert v.equal
    assert EncodingFold.CF_FORMAT in v.folds


def test_nbsp_and_whitespace_collapse():
    # NBSP (Zs) → space, then run/newline collapse + trim
    v = text_equivalence("3 §   muutetaan\n", "3 § muutetaan")
    assert v.equal
    assert EncodingFold.WHITESPACE in v.folds


def test_genuine_numeric_difference_is_a_residual():
    v = text_equivalence("veroprosentti 5,9", "veroprosentti 6,5")
    assert not v.equal and v.residual
    # the residual carries the canonical forms for adjudication
    assert v.left_canon == "veroprosentti 5,9"
    assert v.right_canon == "veroprosentti 6,5"


def test_dash_glyph_variants_fold_to_canonical():
    # en-dash vs em-dash (and hyphen, horizontal bar, minus): the dash GLYPH identity is a
    # rendering artifact — the discovery loop convicted it inert (DASH_GLYPH). The dash is
    # KEPT (1:1 to "-"), not deleted, so a single dash stays present/substantive.
    v = text_equivalence("16 a–b", "16 a—b")
    assert v.equal
    assert EncodingFold.DASH_GLYPH in v.folds
    # all dash-family codepoints collapse to the same canonical hyphen-minus
    for other in ("16 a-b", "16 a―b", "16 a‐b", "16 a−b"):
        assert text_equivalence("16 a—b", other).equal


def test_separator_dash_run_is_folded():
    # A run of 2+ dashes ("— — —") is an inert statute rule / elision marker the text
    # layer captures but the clean XML body omits — folded so the bodies compare equal.
    v = text_equivalence(
        "Uskotun miehen palkkio maksetaan.",
        "Uskotun miehen palkkio maksetaan. — — — — — —",
    )
    assert v.equal
    assert EncodingFold.SEPARATOR_DASH_RUN in v.folds


def test_dot_leader_run_is_folded_decimal_preserved():
    # a run of 2+ dots is a table/TOC leader (inert) → folded; a SINGLE dot is a decimal
    # point and stays substantive so a genuine number difference is not hidden.
    v = text_equivalence("Käsivarsi 2,46", "Käsivarsi.................. 2,46")
    assert v.equal
    assert EncodingFold.DOT_LEADER in v.folds
    v2 = text_equivalence("vero 2.46", "vero 2.99")
    assert not v2.equal and EncodingFold.DOT_LEADER not in v2.folds


def test_single_dash_is_normalized_not_deleted_as_a_run():
    # The {2,}-dash requirement keeps a SINGLE dash from being SWEPT (deleted) by the run
    # fold: DASH_GLYPH normalises its glyph (em/en → "-") but SEPARATOR_DASH_RUN must not
    # fire, so the dash stays PRESENT (not merged away).
    v = text_equivalence("veroluokka 5—10", "veroluokka 5–10")
    assert v.equal  # same range, only the dash glyph differs
    assert EncodingFold.DASH_GLYPH in v.folds
    assert EncodingFold.SEPARATOR_DASH_RUN not in v.folds  # single dash: not a run, not deleted
    # the dash is preserved in the canon (words not merged): "a—b" -> "a-b", never "ab"
    solo = text_equivalence("a—b", "a-b")
    assert solo.equal and solo.left_canon == "a-b"


def test_dash_glyph_does_not_hide_numeric_difference():
    # DASH_GLYPH touches ONLY dash codepoints — the digits around a range are untouched, so
    # a genuine numeric difference across a range separator is NEVER hidden.
    v = text_equivalence("momentin 5—10 kohta", "momentin 5—11 kohta")
    assert not v.equal and v.residual  # 10 != 11
    v2 = text_equivalence("60―62 §", "60―63 §")
    assert not v2.equal and v2.residual  # 62 != 63, even with the horizontal-bar glyph folded


def test_folds_are_deterministic_and_sorted():
    v = text_equivalence("a​ b­\nc", "a bc")
    assert v.equal
    assert list(v.folds) == sorted(v.folds)


def test_empty_inputs_are_equal_never_raise():
    assert text_equivalence("", "").equal
    assert text_equivalence("   \n ", "").equal  # pure-whitespace canonicalises to ""


# ---------------------------------------------------------------------------
# WHITESPACE_PUNCT — whitespace adjacent to punctuation (fold #3)
# ---------------------------------------------------------------------------


def test_space_before_colon_folds():
    # "9 § :n" vs "9 §:n": a space before the ":n" genitive suffix is a typesetting artifact.
    v = text_equivalence("muutetaan 9 § :n 1 momentti", "muutetaan 9 §:n 1 momentti")
    assert v.equal
    assert EncodingFold.WHITESPACE_PUNCT in v.folds


def test_spaces_inside_parens_around_slash_fold():
    # "( / )" vs "(/)": spaces inside the parens (around a slash) are inert typesetting.
    v = text_equivalence("kohta ( / ) korvataan", "kohta (/) korvataan")
    assert v.equal
    assert EncodingFold.WHITESPACE_PUNCT in v.folds
    # all interior-space variants collapse to the same canonical "(/)"
    for variant in ("(/ )", "( /)", "( / )"):
        assert text_equivalence("(/)", variant).equal


def test_space_before_period_folds():
    # "20 ." vs "20.": a space before a period is inert (the period is still PRESENT on both).
    v = text_equivalence("annetun lain 20 .", "annetun lain 20.")
    assert v.equal
    assert EncodingFold.WHITESPACE_PUNCT in v.folds


def test_thin_space_before_section_sign_is_already_folded_by_whitespace():
    # The convicted "2 §:n" residual differs ONLY by a THIN SPACE U+2009 (vs an ordinary
    # space) before "§". U+2009 is in ZS_NON_ASCII_SPACE_CPS, so the pre-existing WHITESPACE
    # fold (Zs→space + run-collapse) already equalises it — no §-specific handling is needed.
    v = text_equivalence("2 §:n mukaan", "2 §:n mukaan")  # left "§" preceded by U+2009
    assert v.equal
    assert EncodingFold.WHITESPACE in v.folds
    # U+202F NARROW NO-BREAK SPACE and U+00A0 NBSP fold the same way (all in the Zs table).
    for zs in (" ", " "):
        assert text_equivalence(f"2{zs}§:n mukaan", "2 §:n mukaan").equal


def test_space_before_section_sign_is_deliberately_not_folded():
    # "§" is EXCLUDED from the before-set: the standard "N §" reference legitimately carries
    # a space, so WHITESPACE_PUNCT must NOT strip it (that would fire on almost every body and
    # recover no equivalences). "3 § muutetaan" stays byte-clean → no punct fold recorded.
    v = text_equivalence("3 § muutetaan", "3 § muutetaan")
    assert v.equal and v.folds == ()
    # a genuine section-number difference is of course still a residual
    assert text_equivalence("3 § muutetaan", "4 § muutetaan").residual


def test_whitespace_punct_recorded_only_when_it_fires():
    # a clean payload with no punctuation-adjacent space does not record the fold
    v = text_equivalence("3 §:n 1 momentti", "3 §:n 1 momentti")
    assert v.equal
    assert EncodingFold.WHITESPACE_PUNCT not in v.folds


def test_whitespace_punct_does_not_hide_numeric_difference():
    # WHITESPACE_PUNCT removes ONLY spaces next to punctuation — digits are untouched, so a
    # genuine numeric difference across a comma/decimal is never hidden.
    v = text_equivalence("veroprosentti 5,9", "veroprosentti 5,10")
    assert not v.equal and v.residual  # 9 != 10
    assert EncodingFold.WHITESPACE_PUNCT not in v.folds


def test_whitespace_punct_does_not_hide_word_difference():
    # a dropped word is content, not typesetting — stays a residual.
    v = text_equivalence("veroviraston tai kunnan", "veroviraston kunnan")
    assert not v.equal and v.residual


def test_whitespace_punct_does_not_hide_citation_difference():
    # a citation year difference survives — WHITESPACE_PUNCT touches no digit or the "/".
    v = text_equivalence("annetun lain (768/2005)", "annetun lain (768/2006)")
    assert not v.equal and v.residual
    assert EncodingFold.WHITESPACE_PUNCT not in v.folds


def test_terminal_period_presence_stays_a_residual():
    # WHITESPACE_PUNCT folds whitespace AROUND punctuation, NEVER a terminal period's
    # PRESENCE — a trailing period can be load-bearing, so it stays a residual.
    v = text_equivalence("maksetaan markkaa.", "maksetaan markkaa")
    assert not v.equal and v.residual
    assert EncodingFold.WHITESPACE_PUNCT not in v.folds

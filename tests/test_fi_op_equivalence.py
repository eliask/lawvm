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


def test_visible_dash_is_not_folded_conservatively():
    # en-dash vs em-dash: inert for PARSE text, but NOT speculatively folded for
    # payload body text — it survives as a residual for the discovery loop to judge.
    v = text_equivalence("16 a–b", "16 a—b")
    assert not v.equal and v.residual
    assert EncodingFold.WHITESPACE not in v.folds  # nothing inert fired to hide it


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


def test_single_dash_is_still_a_residual_not_a_run():
    # The {2,}-dash requirement is load-bearing: a SINGLE en/em dash stays substantive,
    # so a genuine one-dash difference is NOT hidden by the separator-run fold.
    v = text_equivalence("veroluokka 5—10", "veroluokka 5–10")
    assert not v.equal and v.residual
    assert EncodingFold.SEPARATOR_DASH_RUN not in v.folds


def test_folds_are_deterministic_and_sorted():
    v = text_equivalence("a​ b­\nc", "a bc")
    assert v.equal
    assert list(v.folds) == sorted(v.folds)


def test_empty_inputs_are_equal_never_raise():
    assert text_equivalence("", "").equal
    assert text_equivalence("   \n ", "").equal  # pure-whitespace canonicalises to ""

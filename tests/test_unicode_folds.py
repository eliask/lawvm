"""Drift guard for the static Unicode fold sets in ``lawvm.core.unicode_folds``.

The ``Zs`` (Space Separator), ``Cf`` (Format), and ``Pd`` (Dash Punctuation)
codepoint sets used by the Finnish and Estonian structural-parse normalisers
are committed as static literals in ``lawvm.core.unicode_folds`` so that no
CLI invocation pays the ~1.1M-codepoint ``unicodedata`` scan at runtime.

These tests are the regenerate-and-compare guard: they recompute each set from
the running CPython's ``unicodedata`` and assert the committed literals still
match.  If a CPython Unicode-version bump adds or removes a codepoint, the
relevant test fails — regenerate the literal in ``lawvm/core/unicode_folds.py``
(the failure message shows the delta).
"""
import sys
import unicodedata

from lawvm.core.unicode_folds import CF_FORMAT_CPS, PD_DASH_CPS, ZS_NON_ASCII_SPACE_CPS
from lawvm.finland.metadata import _TYPO_TRANSLATION_TABLE, _normalize_fi_parse_text
from lawvm.estonia.peg import _normalize_ee_parse_text


# ---------------------------------------------------------------------------
# Core set drift guards
# ---------------------------------------------------------------------------

def test_zs_non_ascii_space_cps_match_unicodedata():
    live = frozenset(
        cp
        for cp in range(sys.maxunicode + 1)
        if cp != 0x20 and unicodedata.category(chr(cp)) == "Zs"
    )
    assert ZS_NON_ASCII_SPACE_CPS == live, {
        "missing_from_literal": sorted(live - ZS_NON_ASCII_SPACE_CPS),
        "stale_in_literal": sorted(ZS_NON_ASCII_SPACE_CPS - live),
    }


def test_cf_format_cps_match_unicodedata():
    live = frozenset(
        cp
        for cp in range(sys.maxunicode + 1)
        if unicodedata.category(chr(cp)) == "Cf"
    )
    assert CF_FORMAT_CPS == live, {
        "missing_from_literal": sorted(live - CF_FORMAT_CPS),
        "stale_in_literal": sorted(CF_FORMAT_CPS - live),
    }


def test_pd_dash_cps_match_unicodedata():
    live = frozenset(
        cp
        for cp in range(sys.maxunicode + 1)
        if unicodedata.category(chr(cp)) == "Pd"
    )
    assert PD_DASH_CPS == live, {
        "missing_from_literal": sorted(live - PD_DASH_CPS),
        "stale_in_literal": sorted(PD_DASH_CPS - live),
    }


# ---------------------------------------------------------------------------
# Finnish normaliser fold correctness
# ---------------------------------------------------------------------------

def test_fi_translation_table_folds_are_correct():
    # NBSP / narrow-NBSP / ideographic space → ordinary space
    assert _normalize_fi_parse_text("3  　§") == "3   §"
    # em-dash → en-dash (Finnish range dash)
    assert _normalize_fi_parse_text("16 a—" "16 g") == "16 a–16 g"
    # zero-width joiner (Cf) deleted
    assert _normalize_fi_parse_text("3‍ §") == "3 §"
    # ASCII space (U+0020) is NOT in the table (it is the fold target)
    assert 0x20 not in _TYPO_TRANSLATION_TABLE


# ---------------------------------------------------------------------------
# Estonian normaliser fold correctness
# ---------------------------------------------------------------------------

def test_ee_normalizer_folds_spaces():
    # NBSP (U+00A0) → ordinary space
    assert _normalize_ee_parse_text("3 §") == "3 §"
    # ideographic space (U+3000) → ordinary space
    assert _normalize_ee_parse_text("3　§") == "3 §"


def test_ee_normalizer_folds_dashes_to_en_dash():
    # em-dash → en-dash
    assert _normalize_ee_parse_text("1—2") == "1–2"
    # hyphen-minus (Pd) → en-dash
    assert _normalize_ee_parse_text("1-2") == "1–2"
    # U+2212 MINUS SIGN (appended explicitly) → en-dash
    assert _normalize_ee_parse_text("1−2") == "1–2"


def test_ee_normalizer_deletes_cf_chars():
    # zero-width joiner (U+200D, Cf) deleted
    assert _normalize_ee_parse_text("3‍ §") == "3 §"
    # soft hyphen (U+00AD, Cf) deleted
    assert _normalize_ee_parse_text("3­§") == "3§"

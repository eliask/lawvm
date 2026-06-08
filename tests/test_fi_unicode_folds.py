"""Drift guard for the static Unicode fold sets in ``lawvm.finland.metadata``.

The ``Zs`` (Space Separator) and ``Cf`` (Format) codepoint sets used by the
Finnish structural-parse normaliser are committed as static literals so that
no CLI invocation pays the ~1.1M-codepoint ``unicodedata`` scan at runtime.

These tests are the regenerate-and-compare guard: they recompute the sets from
the running CPython's ``unicodedata`` and assert the committed literals still
match. If a CPython Unicode-version bump adds or removes a Zs/Cf codepoint,
these fail — regenerate the literals in ``finland/metadata.py`` (the failure
message lists the delta).
"""
import sys
import unicodedata

from lawvm.finland.metadata import (
    _CF_FORMAT_CPS,
    _TYPO_TRANSLATION_TABLE,
    _ZS_NON_ASCII_SPACE_CPS,
    _normalize_fi_parse_text,
)


def test_zs_non_ascii_space_cps_match_unicodedata():
    live = frozenset(
        cp
        for cp in range(sys.maxunicode + 1)
        if cp != 0x20 and unicodedata.category(chr(cp)) == "Zs"
    )
    assert _ZS_NON_ASCII_SPACE_CPS == live, {
        "missing_from_literal": sorted(live - _ZS_NON_ASCII_SPACE_CPS),
        "stale_in_literal": sorted(_ZS_NON_ASCII_SPACE_CPS - live),
    }


def test_cf_format_cps_match_unicodedata():
    live = frozenset(
        cp
        for cp in range(sys.maxunicode + 1)
        if unicodedata.category(chr(cp)) == "Cf"
    )
    assert _CF_FORMAT_CPS == live, {
        "missing_from_literal": sorted(live - _CF_FORMAT_CPS),
        "stale_in_literal": sorted(_CF_FORMAT_CPS - live),
    }


def test_translation_table_folds_are_correct():
    # NBSP / narrow-NBSP / ideographic space → ordinary space
    assert _normalize_fi_parse_text("3  　§") == "3   §"
    # em-dash → en-dash (Finnish range dash)
    assert _normalize_fi_parse_text("16 a—16 g") == "16 a–16 g"
    # zero-width joiner (Cf) deleted
    assert _normalize_fi_parse_text("3‍ §") == "3 §"
    # ASCII space (U+0020) is NOT in the table (it is the fold target)
    assert 0x20 not in _TYPO_TRANSLATION_TABLE

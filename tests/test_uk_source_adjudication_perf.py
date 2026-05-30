"""Performance regression tests for UK source_adjudication hot-path functions.

Bounded-regex + fast-guard fix (2026-05-29) for
_looks_like_referent_qualified_text_substitution.

cProfile witness: .tmp/uk_sensor_profile_1970_9.md
  - Before fix: 89 calls * ~1.18 s/call = 104.74 s (51.4 % of wall on ukpga/1970/9)
  - After fix:  89 calls * <1 ms/call (short-circuit + bounded .{0,500} regex)

These tests verify:
  1. Positive inputs still match (true-positive parity).
  2. Short-circuit paths return False instantly.
  3. Adversarial inputs that would have caused catastrophic backtracking now
     complete in well under 100 ms.
"""
from __future__ import annotations

import time

from lawvm.uk_legislation.source_adjudication import (
    _looks_like_referent_qualified_text_substitution,
)

_CEILING_MS = 100  # generous ceiling; old code took ~1180 ms on these inputs


# ---------------------------------------------------------------------------
# Positive cases
# ---------------------------------------------------------------------------


def test_referent_qualified_ascii_quotes_matches() -> None:
    text = (
        'for the words "chief constable" substitute "commissioner" '
        'where it refers to a member of the metropolitan police'
    )
    assert _looks_like_referent_qualified_text_substitution(text) is True


def test_referent_qualified_curly_quotes_matches() -> None:
    # Left U+201C and right U+201D curly quotes as used in UK legislation XML
    lq = chr(0x201C)  # U+201C left double quotation mark
    rq = chr(0x201D)  # U+201D right double quotation mark
    text = f"for the word {lq}transport{rq} substitute {lq}vehicle{rq} where it refers to a kind of vehicle"
    assert _looks_like_referent_qualified_text_substitution(text) is True


def test_referent_qualified_plural_words_matches() -> None:
    text = (
        'for "those words" substitute "the following words" '
        'where those words refer to a person'
    )
    assert _looks_like_referent_qualified_text_substitution(text) is True


# ---------------------------------------------------------------------------
# Negative cases
# ---------------------------------------------------------------------------


def test_referent_qualified_no_where_returns_false() -> None:
    text = 'for "widget" substitute "gadget"'
    assert _looks_like_referent_qualified_text_substitution(text) is False


def test_referent_qualified_no_refers_returns_false() -> None:
    text = 'for "widget" substitute "gadget" where the words appear'
    assert _looks_like_referent_qualified_text_substitution(text) is False


def test_referent_qualified_no_substitute_returns_false() -> None:
    text = 'for "widget" where it refers to a small tool'
    assert _looks_like_referent_qualified_text_substitution(text) is False


def test_referent_qualified_no_quotes_returns_false() -> None:
    text = 'for the word substitute another where it refers to something'
    assert _looks_like_referent_qualified_text_substitution(text) is False


def test_referent_qualified_empty_returns_false() -> None:
    assert _looks_like_referent_qualified_text_substitution('') is False


def test_referent_qualified_whitespace_only_returns_false() -> None:
    assert _looks_like_referent_qualified_text_substitution('   ') is False


# ---------------------------------------------------------------------------
# Performance regression: adversarial inputs that caused catastrophic
# backtracking in the old unanchored-regex form.
# ---------------------------------------------------------------------------


def test_referent_qualified_adversarial_long_no_quotes_is_fast() -> None:
    """Long input with 'where', 'refers', 'substitute' but no quote chars.

    Old code: the regex ran for ~1.18 s on inputs like this.
    New code: short-circuit guard fires before regex; must complete in < 100 ms.
    """
    text = (
        'for '
        + 'x' * 1000
        + ' substitute something where it refers to the end but '
        + 'no quote characters anywhere in this long string'
    )
    t0 = time.perf_counter()
    result = _looks_like_referent_qualified_text_substitution(text)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    assert result is False
    assert elapsed_ms < _CEILING_MS, (
        f'adversarial no-quotes took {elapsed_ms:.1f} ms (ceiling {_CEILING_MS} ms); '
        'catastrophic backtracking may have regressed'
    )


def test_referent_qualified_adversarial_quotes_no_suffix_is_fast() -> None:
    """Long input with quotes and guards-passing keywords but no terminal

    Uses a 600-char gap after the second quote (exceeds the .{0,500} bound)
    to confirm the bounded regex fails fast without O(N^3) backtracking.
    The bounded quantifier prevents exhaustive backtracking when the terminal
    "where ... refers to" suffix pattern cannot be reached within the bound.
    """
    # 600-char gap between last quote and "where ... refers to":
    # the .{0,500} bound means the regex fails in O(1) once the bound is hit.
    text = (
        'for "word" substitute "other"'
        + 'x' * 600
        + ' where it refers to end'
    )
    t0 = time.perf_counter()
    result = _looks_like_referent_qualified_text_substitution(text)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    assert result is False
    assert elapsed_ms < _CEILING_MS, (
        f'adversarial with-quotes took {elapsed_ms:.1f} ms (ceiling {_CEILING_MS} ms); '
        'bounded regex may have regressed'
    )

def test_referent_qualified_adversarial_many_calls_is_fast() -> None:
    """Simulate 89-call batch (the corpus-scale count on ukpga/1970/9).

    89 calls on adversarial inputs should complete well under 89 * 100 ms = 8.9 s.
    Before fix: 89 * 1180 ms = 104.74 s.
    """
    text = (
        'for '
        + 'a' * 500
        + ' substitute something where it refers to the end '
        + 'b' * 500
    )
    t0 = time.perf_counter()
    for _ in range(89):
        _looks_like_referent_qualified_text_substitution(text)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    # Ceiling: 89 * 100 ms = 8900 ms (very generous)
    ceiling = 89 * _CEILING_MS
    assert elapsed_ms < ceiling, (
        f'89-call batch took {elapsed_ms:.1f} ms (ceiling {ceiling} ms); '
        'catastrophic backtracking may have regressed'
    )

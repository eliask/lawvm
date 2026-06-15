"""NZ oracle-divergence classifier: types each candidate-vs-oracle text divergence.

Purpose
-------
When the NZ dry-run replay applies an amendment op and compares the result
against the official consolidated text (the "oracle"), many disagreements are
NOT substantive legal differences — they are the oracle's own editorial
normalizations (digit↔word, capitalization, trailing punctuation, stray U+FEFF /
zero-width characters).  This module classifies each such divergence so the
partition functions can route it correctly instead of emitting false positives.

Integration point
-----------------
Call ``classify_oracle_divergence(candidate_text, oracle_text)`` from the
partition functions in ``lawvm.new_zealand.dry_run`` — specifically from
``_oracle_partition_text``, ``_oracle_partition_replace``, and
``_oracle_partition_insert`` — when ``oracle_match != "agrees"``.  The returned
``NZDivergenceClass`` carries an ``is_editorial`` flag that callers can use to
suppress or de-prioritize purely editorial residuals.

Sub-family precedence (highest → lowest)
-----------------------------------------
When a divergence matches multiple editorial classes, the *most specific / lowest
false-positive* class wins.  Checks are ordered from cheapest and most
frequently encountered to most expensive and broadest:

1. ``agrees_after_normalization`` — strings are identical (degenerate case, not
   a divergence at all; included for completeness).
2. ``editorial_bom_zero_width``  — after stripping U+FEFF / zero-width chars the
   strings agree.  Checked first because BOM characters are invisible and
   confound all downstream string comparisons.
3. ``editorial_capitalization`` — strings agree after ``.lower()`` (no other
   normalization).  Checked before trailing-punctuation and
   punct_whitespace so that a pure case difference is not swallowed by the
   broader folds.
4. ``editorial_trailing_punctuation`` — the only difference is the presence or
   absence of a single trailing period.  Checked before the broader
   ``punct_whitespace`` fold because it is a more specific signal.
5. ``editorial_punctuation_whitespace`` — after stripping leading/trailing
   punctuation (.,;:) and collapsing interior whitespace the strings agree
   (case-insensitively).  Broader than trailing-punctuation; catches mixed
   interior/leading punctuation and whitespace normalization.
6. ``editorial_digit_word_numeral`` — after mapping digit-tokens ("1", "2", …)
   to English word-equivalents ("one", "two", …) the tokenised streams agree.
7. ``structural`` — token count ratio exceeds 2× (whole-Part non-commensurable
   replace/insert) or one side is empty.
8. ``substantive`` — the difference survives all folds; genuine content
   divergence.

``is_editorial`` is ``True`` for sub-families 1–6 inclusive.

Notes
-----
- All functions are pure (no I/O, no state, no randomness): same inputs → same
  output.
- Raises ``TypeError`` on non-str or ``None`` inputs (fail-loud).
- Unicode-correct: U+FEFF (BOM), U+200B (zero-width space), U+200C (ZWNJ),
  U+200D (ZWJ), U+FEFF (zero-width no-break space / BOM) are handled
  explicitly via ``_ZERO_WIDTH_CHARS``.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum

__all__ = [
    "NZDivergenceSubFamily",
    "NZDivergenceClass",
    "classify_oracle_divergence",
    # individual fold predicates (separately testable)
    "_strip_zero_width",
    "_fold_punct_whitespace",
    "_fold_numerals",
    "_strip_trailing_period",
    "_tokenize_words",
]


# ---------------------------------------------------------------------------
# Zero-width / BOM character set
# ---------------------------------------------------------------------------

_ZERO_WIDTH_CHARS: frozenset[str] = frozenset(
    [
        "﻿",  # BOM / zero-width no-break space
        "​",  # zero-width space
        "‌",  # zero-width non-joiner
        "‍",  # zero-width joiner
        "­",  # soft hyphen (invisible)
    ]
)


# ---------------------------------------------------------------------------
# Digit → English-word mapping (0–12 covers all NZ statute occurrences found
# in the PoC sweep; digits above 12 are left as digits, which is correct
# because the oracle never word-expands large numerals).
# ---------------------------------------------------------------------------

_DIGIT_WORDS: dict[str, str] = {
    "0": "zero",
    "1": "one",
    "2": "two",
    "3": "three",
    "4": "four",
    "5": "five",
    "6": "six",
    "7": "seven",
    "8": "eight",
    "9": "nine",
    "10": "ten",
    "11": "eleven",
    "12": "twelve",
}


# ---------------------------------------------------------------------------
# Sub-family enum
# ---------------------------------------------------------------------------


class NZDivergenceSubFamily(str, Enum):
    """Classification of a candidate-vs-oracle text divergence."""

    agrees_after_normalization = "agrees_after_normalization"
    editorial_bom_zero_width = "editorial_bom_zero_width"
    editorial_punctuation_whitespace = "editorial_punctuation_whitespace"
    editorial_capitalization = "editorial_capitalization"
    editorial_digit_word_numeral = "editorial_digit_word_numeral"
    editorial_trailing_punctuation = "editorial_trailing_punctuation"
    structural = "structural"
    substantive = "substantive"


_EDITORIAL_SUBFAMILIES: frozenset[NZDivergenceSubFamily] = frozenset(
    [
        NZDivergenceSubFamily.agrees_after_normalization,
        NZDivergenceSubFamily.editorial_bom_zero_width,
        NZDivergenceSubFamily.editorial_punctuation_whitespace,
        NZDivergenceSubFamily.editorial_capitalization,
        NZDivergenceSubFamily.editorial_digit_word_numeral,
        NZDivergenceSubFamily.editorial_trailing_punctuation,
    ]
)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NZDivergenceClass:
    """Result of classifying a candidate-vs-oracle text pair.

    Attributes
    ----------
    sub_family:
        The dominant editorial class (or ``substantive`` / ``structural``).
    is_editorial:
        ``True`` when ``sub_family`` is any ``editorial_*`` or
        ``agrees_after_normalization`` value; ``False`` for
        ``substantive`` / ``structural``.
    normalized_candidate:
        The normalized candidate string at the classification level (for
        diagnostics; may equal ``candidate_text`` when no fold was applied).
    normalized_oracle:
        The normalized oracle string at the classification level.
    reason:
        Short human-readable explanation of how the class was determined.
    """

    sub_family: NZDivergenceSubFamily
    is_editorial: bool
    normalized_candidate: str
    normalized_oracle: str
    reason: str


# ---------------------------------------------------------------------------
# Individual fold predicates (separately testable)
# ---------------------------------------------------------------------------


def _strip_zero_width(text: str) -> str:
    """Remove all zero-width / BOM characters from *text*."""
    return "".join(ch for ch in text if ch not in _ZERO_WIDTH_CHARS)


def _fold_punct_whitespace(text: str) -> str:
    """Strip leading/trailing punctuation-and-whitespace, collapse interior
    whitespace to a single space, then lower-case.

    'Punctuation' here means the characters most frequently used as oracle
    editorial additions: ``.``, ``,``, ``;``, ``:`` together with whitespace.
    """
    stripped = text.strip(" \t\n\r.,;:")
    collapsed = re.sub(r"\s+", " ", stripped)
    return collapsed.lower()


def _tokenize_words(text: str) -> list[str]:
    """Return the lower-cased word tokens from *text* (``\\w+`` definition)."""
    return re.findall(r"\w+", text.lower())


def _fold_numerals(tokens: list[str]) -> list[str]:
    """Map digit-only tokens to English word equivalents using ``_DIGIT_WORDS``.

    Tokens not in the mapping are returned unchanged.
    """
    return [_DIGIT_WORDS.get(tok, tok) for tok in tokens]


def _strip_trailing_period(text: str) -> str:
    """Remove exactly one trailing period (and surrounding whitespace) from *text*.

    If *text* ends with ``"."`` after right-stripping whitespace, that single
    period is removed and the result is right-stripped again.  If there is no
    trailing period, *text* is returned right-stripped.
    """
    stripped = text.rstrip()
    if stripped.endswith("."):
        return stripped[:-1].rstrip()
    return stripped


# ---------------------------------------------------------------------------
# Public classifier
# ---------------------------------------------------------------------------


def classify_oracle_divergence(
    candidate_text: str,
    oracle_text: str,
) -> NZDivergenceClass:
    """Classify the divergence between *candidate_text* and *oracle_text*.

    Parameters
    ----------
    candidate_text:
        The text produced by our replay (the candidate).
    oracle_text:
        The text from the official consolidated oracle.

    Returns
    -------
    NZDivergenceClass
        A frozen dataclass describing the dominant divergence class, an
        ``is_editorial`` flag, the normalized forms used for classification,
        and a short reason string.

    Raises
    ------
    TypeError
        If either argument is not a ``str`` (including ``None``).
    """
    if not isinstance(candidate_text, str):
        raise TypeError(
            f"classify_oracle_divergence: candidate_text must be str, "
            f"got {type(candidate_text).__name__!r}"
        )
    if not isinstance(oracle_text, str):
        raise TypeError(
            f"classify_oracle_divergence: oracle_text must be str, "
            f"got {type(oracle_text).__name__!r}"
        )

    def _make(
        sub_family: NZDivergenceSubFamily,
        norm_c: str,
        norm_o: str,
        reason: str,
    ) -> NZDivergenceClass:
        return NZDivergenceClass(
            sub_family=sub_family,
            is_editorial=(sub_family in _EDITORIAL_SUBFAMILIES),
            normalized_candidate=norm_c,
            normalized_oracle=norm_o,
            reason=reason,
        )

    # 1. Exact agreement (degenerate case)
    if candidate_text == oracle_text:
        return _make(
            NZDivergenceSubFamily.agrees_after_normalization,
            candidate_text,
            oracle_text,
            "strings are identical",
        )

    # 2. BOM / zero-width characters only
    c_zw = _strip_zero_width(candidate_text)
    o_zw = _strip_zero_width(oracle_text)
    if c_zw == o_zw:
        return _make(
            NZDivergenceSubFamily.editorial_bom_zero_width,
            c_zw,
            o_zw,
            "divergence eliminated by stripping U+FEFF / zero-width characters",
        )

    # 3. Capitalization only (lower-case comparison, no other normalization)
    if c_zw.lower() == o_zw.lower():
        return _make(
            NZDivergenceSubFamily.editorial_capitalization,
            c_zw.lower(),
            o_zw.lower(),
            "divergence eliminated by lower-casing (capitalization difference only)",
        )

    # 4. Trailing punctuation only (a single trailing period, more specific than
    #    the broader punct_whitespace fold checked next)
    c_ntp = _strip_trailing_period(c_zw)
    o_ntp = _strip_trailing_period(o_zw)
    if c_ntp == o_ntp:
        return _make(
            NZDivergenceSubFamily.editorial_trailing_punctuation,
            c_ntp,
            o_ntp,
            "divergence eliminated by stripping trailing period",
        )

    # 5. Punctuation / whitespace normalization (case-insensitive, broader fold)
    c_pw = _fold_punct_whitespace(c_zw)
    o_pw = _fold_punct_whitespace(o_zw)
    if c_pw == o_pw:
        return _make(
            NZDivergenceSubFamily.editorial_punctuation_whitespace,
            c_pw,
            o_pw,
            "divergence eliminated by stripping punctuation/whitespace and lower-casing",
        )

    # 6. Digit ↔ word numeral normalization
    c_tokens = _tokenize_words(c_zw)
    o_tokens = _tokenize_words(o_zw)
    if _fold_numerals(c_tokens) == _fold_numerals(o_tokens):
        return _make(
            NZDivergenceSubFamily.editorial_digit_word_numeral,
            " ".join(_fold_numerals(c_tokens)),
            " ".join(_fold_numerals(o_tokens)),
            "divergence eliminated by mapping digit-tokens to English words",
        )

    # 7. Structural: token / word count differs substantially
    #    Heuristic: if the token-count ratio is > 2× either direction, classify
    #    as structural rather than substantive so callers can filter whole-Part
    #    non-commensurable comparisons separately.
    if c_tokens and o_tokens:
        ratio = max(len(c_tokens), len(o_tokens)) / min(len(c_tokens), len(o_tokens))
        if ratio > 2.0:
            return _make(
                NZDivergenceSubFamily.structural,
                candidate_text,
                oracle_text,
                f"token-count ratio {ratio:.1f} exceeds 2× threshold "
                f"(candidate={len(c_tokens)} tokens, oracle={len(o_tokens)} tokens)",
            )
    elif len(c_tokens) != len(o_tokens):
        # One is empty
        return _make(
            NZDivergenceSubFamily.structural,
            candidate_text,
            oracle_text,
            f"one side is empty (candidate={len(c_tokens)} tokens, "
            f"oracle={len(o_tokens)} tokens)",
        )

    # 8. Substantive: the diff survives all editorial folds
    return _make(
        NZDivergenceSubFamily.substantive,
        candidate_text,
        oracle_text,
        "divergence survives all editorial folds; genuine content difference",
    )

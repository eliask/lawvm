"""Universal Unicode-category codepoint sets — shared across all jurisdictions.

These three ``frozenset[int]`` literals enumerate every codepoint in the
corresponding Unicode general category.  They are committed as static literals
rather than being scanned at import time (scanning all ~1.1M codepoints via
``unicodedata.category()`` per set costs ~0.3–0.75 s each, paid on every
process startup).

Drift guard
-----------
``tests/test_unicode_folds.py`` regenerates each set from the running CPython
``unicodedata`` module and asserts equality with the committed literals.  If a
CPython Unicode-version bump adds or removes a codepoint in any category, that
test fails with a delta message — regenerate the affected literal here.

Why these three categories?
----------------------------
``Zs`` (Space Separator)
    All horizontal-whitespace variants that are *not* U+0020 (the fold target).
    Used to normalise typographic space variants (NBSP, thin space, narrow NBSP,
    ideographic space, en/em spaces, etc.) to ordinary space before structural
    parsing.  A static literal is exhaustive against all Unicode spaces,
    including future additions; a hand-written list would silently miss new ones.

``Cf`` (Format)
    Invisible control characters that carry no lexical meaning in legislative
    XML but silently corrupt regex and PEG matches if left in place.  Confirmed
    real-world occurrence: U+200D ZERO WIDTH JOINER appears in 2020/818 johtolause
    ("3\\u200D\\u200D §:n") and causes the PEG parser to silently fail the
    section-number match.  Deleting all Cf characters (zero-width joiners,
    non-joiners, soft hyphens, directional overrides, BOM, etc.) is the correct
    defensive policy.

``Pd`` (Dash Punctuation)
    All Unicode dash characters.  Used by the Estonian normaliser to fold every
    dash variant to en-dash (U+2013) — Estonian amendment text uses several dash
    variants to separate range endpoints and the normaliser needs them all.

Notes
-----
- U+0020 SPACE is intentionally excluded from ``ZS_NON_ASCII_SPACE_CPS``
  (it is the fold target, not a source).
- The ``Cf`` and ``Pd`` sets are written as they appear in Unicode 15 / CPython
  3.12+.  Earlier CPython versions may have a smaller ``Cf`` count (e.g.
  CPython 3.11 has 170 Cf codepoints; 3.12 may differ slightly).
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Zs — Space Separator (non-ASCII only; U+0020 excluded)
# ---------------------------------------------------------------------------
ZS_NON_ASCII_SPACE_CPS: frozenset[int] = frozenset({
    0x00A0, 0x1680, 0x2000, 0x2001, 0x2002, 0x2003, 0x2004, 0x2005, 0x2006,
    0x2007, 0x2008, 0x2009, 0x200A, 0x202F, 0x205F, 0x3000,
})

# ---------------------------------------------------------------------------
# Cf — Format characters (invisible control chars; all deleted from parse text)
# ---------------------------------------------------------------------------
CF_FORMAT_CPS: frozenset[int] = frozenset({
    0x00AD, 0x0600, 0x0601, 0x0602, 0x0603, 0x0604, 0x0605, 0x061C, 0x06DD,
    0x070F, 0x0890, 0x0891, 0x08E2, 0x180E, 0x200B, 0x200C, 0x200D, 0x200E,
    0x200F, 0x202A, 0x202B, 0x202C, 0x202D, 0x202E, 0x2060, 0x2061, 0x2062,
    0x2063, 0x2064, 0x2066, 0x2067, 0x2068, 0x2069, 0x206A, 0x206B, 0x206C,
    0x206D, 0x206E, 0x206F, 0xFEFF, 0xFFF9, 0xFFFA, 0xFFFB, 0x110BD, 0x110CD,
    0x13430, 0x13431, 0x13432, 0x13433, 0x13434, 0x13435, 0x13436, 0x13437,
    0x13438, 0x13439, 0x1343A, 0x1343B, 0x1343C, 0x1343D, 0x1343E, 0x1343F,
    0x1BCA0, 0x1BCA1, 0x1BCA2, 0x1BCA3, 0x1D173, 0x1D174, 0x1D175, 0x1D176,
    0x1D177, 0x1D178, 0x1D179, 0x1D17A, 0xE0001, 0xE0020, 0xE0021, 0xE0022,
    0xE0023, 0xE0024, 0xE0025, 0xE0026, 0xE0027, 0xE0028, 0xE0029, 0xE002A,
    0xE002B, 0xE002C, 0xE002D, 0xE002E, 0xE002F, 0xE0030, 0xE0031, 0xE0032,
    0xE0033, 0xE0034, 0xE0035, 0xE0036, 0xE0037, 0xE0038, 0xE0039, 0xE003A,
    0xE003B, 0xE003C, 0xE003D, 0xE003E, 0xE003F, 0xE0040, 0xE0041, 0xE0042,
    0xE0043, 0xE0044, 0xE0045, 0xE0046, 0xE0047, 0xE0048, 0xE0049, 0xE004A,
    0xE004B, 0xE004C, 0xE004D, 0xE004E, 0xE004F, 0xE0050, 0xE0051, 0xE0052,
    0xE0053, 0xE0054, 0xE0055, 0xE0056, 0xE0057, 0xE0058, 0xE0059, 0xE005A,
    0xE005B, 0xE005C, 0xE005D, 0xE005E, 0xE005F, 0xE0060, 0xE0061, 0xE0062,
    0xE0063, 0xE0064, 0xE0065, 0xE0066, 0xE0067, 0xE0068, 0xE0069, 0xE006A,
    0xE006B, 0xE006C, 0xE006D, 0xE006E, 0xE006F, 0xE0070, 0xE0071, 0xE0072,
    0xE0073, 0xE0074, 0xE0075, 0xE0076, 0xE0077, 0xE0078, 0xE0079, 0xE007A,
    0xE007B, 0xE007C, 0xE007D, 0xE007E, 0xE007F,
})

# ---------------------------------------------------------------------------
# Pd — Dash Punctuation (26 codepoints as of Unicode 15)
# ---------------------------------------------------------------------------
PD_DASH_CPS: frozenset[int] = frozenset({
    0x002D,   # HYPHEN-MINUS
    0x058A,   # ARMENIAN HYPHEN
    0x05BE,   # HEBREW PUNCTUATION MAQAF
    0x1400,   # CANADIAN SYLLABICS HYPHEN
    0x1806,   # MONGOLIAN TODO SOFT HYPHEN
    0x2010,   # HYPHEN
    0x2011,   # NON-BREAKING HYPHEN
    0x2012,   # FIGURE DASH
    0x2013,   # EN DASH
    0x2014,   # EM DASH
    0x2015,   # HORIZONTAL BAR
    0x2E17,   # DOUBLE OBLIQUE HYPHEN
    0x2E1A,   # HYPHEN WITH DIAERESIS
    0x2E3A,   # TWO-EM DASH
    0x2E3B,   # THREE-EM DASH
    0x2E40,   # DOUBLE HYPHEN
    0x2E5D,   # OBLIQUE HYPHEN
    0x301C,   # WAVE DASH
    0x3030,   # WAVY DASH
    0x30A0,   # KATAKANA-HIRAGANA DOUBLE HYPHEN
    0xFE31,   # PRESENTATION FORM FOR VERTICAL EM DASH
    0xFE32,   # PRESENTATION FORM FOR VERTICAL EN DASH
    0xFE58,   # SMALL EM DASH
    0xFE63,   # SMALL HYPHEN-MINUS
    0xFF0D,   # FULLWIDTH HYPHEN-MINUS
    0x10D6E,  # GARAY HYPHEN (Unicode 16.0; bundled with Python 3.14)
    0x10EAD,  # YEZIDI HYPHENATION MARK
})

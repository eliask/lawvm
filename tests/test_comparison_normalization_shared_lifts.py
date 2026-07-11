"""Byte-identity lock for the shared comparison-normalization lifts.

Two jurisdiction-agnostic comparison-normalization primitives that were
previously open-coded per jurisdiction now live in the shared typed registry
(``lawvm.core.comparison_normalization``), and one FI-specific provenance-tail
primitive that was duplicated across the Finland frontend now lives in
``lawvm.finland.provenance_tail``. This test freezes the re-home as
BEHAVIOR-PRESERVING: the shared definition must reproduce the pre-lift
open-coded output byte-for-byte over a battery of inputs (including a random
fuzz), so a future edit to a shared rule cannot silently shift an exactness
verdict in US / UK / FI.
"""

from __future__ import annotations

import random
import re

from lawvm.core.comparison_normalization import (
    DASH_CANONICAL_EM,
    DASH_VARIANT_CHARS,
    EDITORIAL_HYPHEN_WRAP_SPACE_RULE,
    normalize_comparison_text,
)
from lawvm.finland.provenance_tail import (
    HISTORY_MARKER_RE,
    strip_source_provenance_tail,
)
from lawvm.uk_legislation.source_text_normalization import normalize_uk_parser_text


# --------------------------------------------------------------------------- #
# Lift 1a: US editorial hyphen-wrap space (was us_federal.dry_run._EDITORIAL_   #
# HYPHEN_WRAP_SPACE_RE, applied via ``.sub("", ...)``).                         #
# --------------------------------------------------------------------------- #

_OLD_US_HYPHEN_WRAP_RE = re.compile(r"(?<=\w-)\s+(?=\w)")


def _old_us_hyphen_wrap(text: str) -> str:
    return _OLD_US_HYPHEN_WRAP_RE.sub("", text)


def _new_us_hyphen_wrap(text: str) -> str:
    return normalize_comparison_text(text, (EDITORIAL_HYPHEN_WRAP_SPACE_RULE,)).text


def test_us_hyphen_wrap_rule_matches_old_regex() -> None:
    cases = [
        "the non- Federal cost share",
        "a-b",
        "non-  Federal",
        "x- y z- w",
        "-- introducer",
        "word- word- word",
        "",
        "a- 1",
        "1- a",
        "no hyphen here",
        "em—dash after",
        "trailing- ",
        " -leading",
        "multi\n-\nline",
    ]
    for s in cases:
        assert _old_us_hyphen_wrap(s) == _new_us_hyphen_wrap(s), s


def test_us_hyphen_wrap_rule_matches_old_regex_fuzz() -> None:
    rng = random.Random(1234)
    frags = ["word", "-", " ", "\t", "Federal", "non", "a", "1", "—", "\n", "-b"]
    for _ in range(3000):
        s = "".join(rng.choice(frags) for _ in range(rng.randint(0, 14)))
        assert _old_us_hyphen_wrap(s) == _new_us_hyphen_wrap(s), s


# --------------------------------------------------------------------------- #
# Lift 1b/c: UK parser-view normalizer (dash-variant fold + outside-quote       #
# token joins). The pre-lift implementation is reconstructed verbatim and       #
# compared against the current (registry-backed) function.                      #
# --------------------------------------------------------------------------- #

_OLD_UK_DASH_VARIANTS = {
    "‐",
    "‑",
    "‒",
    "–",
    "—",
    "―",
    "−",
}
_OLD_UK_OPEN_TO_CLOSE_QUOTE = {
    "“": "”",
    "‘": "’",
    '"': '"',
    "'": "'",
}
_OLD_UK_TOKEN_JOIN_REPAIRS = (
    ("thereshall", "there shall"),
    ("thereis", "there is"),
    ("thereare", "there are"),
    ("beomitted", "be omitted"),
    ("berepealed", "be repealed"),
)


def _old_uk_word_apostrophe(text: str, index: int) -> bool:
    if text[index] != "'":
        return False
    previous_char = text[index - 1] if index > 0 else ""
    next_char = text[index + 1] if index + 1 < len(text) else ""
    return previous_char.isalnum() and next_char.isalnum()


def _old_uk_repair_token_joins(segment: str) -> str:
    repaired = segment
    for joined, replacement in _OLD_UK_TOKEN_JOIN_REPAIRS:
        repaired = repaired.replace(joined, replacement)
        repaired = repaired.replace(joined.capitalize(), replacement.capitalize())
    return repaired


def _old_normalize_uk_parser_text(text: str) -> str:
    collapsed = " ".join(str(text or "").split())
    out: list[str] = []
    outside_segment: list[str] = []
    quote_stack: list[str] = []

    def flush_outside_segment() -> None:
        if outside_segment:
            out.append(_old_uk_repair_token_joins("".join(outside_segment)))
            outside_segment.clear()

    for index, char in enumerate(collapsed):
        if quote_stack:
            flush_outside_segment()
            out.append(char)
            if char == quote_stack[-1] and not _old_uk_word_apostrophe(collapsed, index):
                quote_stack.pop()
            continue
        if _old_uk_word_apostrophe(collapsed, index):
            outside_segment.append(char)
            continue
        closing_quote = _OLD_UK_OPEN_TO_CLOSE_QUOTE.get(char)
        if closing_quote is not None:
            flush_outside_segment()
            quote_stack.append(closing_quote)
            out.append(char)
        elif char in _OLD_UK_DASH_VARIANTS:
            outside_segment.append("—")
        else:
            outside_segment.append(char)
    flush_outside_segment()
    return "".join(out).strip()


def test_uk_dash_variant_set_and_target_unchanged() -> None:
    assert set(DASH_VARIANT_CHARS) == _OLD_UK_DASH_VARIANTS
    assert DASH_CANONICAL_EM == "—"


def test_uk_parser_text_matches_old_impl() -> None:
    cases = [
        "there shall be omitted",
        "thereshall be repealed",
        "Thereshall be omitted",
        "a–b—c−d",
        '"keep-this A-B-C" outside- dash',
        'text thereis a "quoted thereis stays" thereare end',
        "it's a word apostrophe thereshall",
        "beomitted berepealed thereare thereis",
        "‘smart’ quote thereshall",
        "Beomitted at start",
        "",
        'mixed – dash "inside – quote keeps" outside – folds',
        "nested \"a 'b' c\" thereshall",
        "multiple    spaces   thereis",
        "Thereare Thereis Thereshall Beomitted Berepealed",
    ]
    for s in cases:
        assert _old_normalize_uk_parser_text(s) == normalize_uk_parser_text(s), s


def test_uk_parser_text_matches_old_impl_fuzz() -> None:
    rng = random.Random(99)
    frags = [
        "there", "shall", "is", "are", "be", "omitted", "repealed",
        "thereshall", "thereis", "thereare", "beomitted", "berepealed",
        "-", "–", "—", "−", '"', "'", "“", "”",
        "A", "B", "word", " ",
    ]
    for _ in range(4000):
        s = "".join(rng.choice(frags) for _ in range(rng.randint(0, 12)))
        assert _old_normalize_uk_parser_text(s) == normalize_uk_parser_text(s), s


# --------------------------------------------------------------------------- #
# Lift 2: FI provenance-tail primitive (history marker + kumotaan strip).        #
# --------------------------------------------------------------------------- #

_OLD_FI_HISTORY_MARKER_RE = re.compile(r"sellais(?:ena|ina)\s+kuin", re.IGNORECASE)


def _old_fi_strip(text: str) -> str:
    return re.split(r",\s*sellais[a-zäöå\s]*kuin\b", text, maxsplit=1, flags=re.I)[0]


def test_fi_history_marker_matches_old_regex() -> None:
    assert HISTORY_MARKER_RE.pattern == _OLD_FI_HISTORY_MARKER_RE.pattern
    assert HISTORY_MARKER_RE.flags == _OLD_FI_HISTORY_MARKER_RE.flags


def test_fi_provenance_tail_matches_old_impls() -> None:
    cases = [
        "7 §, sellaisena kuin se on laissa 668/2013",
        "ei mitään",
        "Sellaisina  Kuin ne ovat",
        "kumotaan 5 §, sellaisina kuin ne ovat asetuksessa 1282/2000; sekä 2) 6 §",
        "1 § ja (668/2013), 80 § sellaisena kuin",
        "selaisena kuin",
        "sellaisena kuinX",
        "",
    ]
    for s in cases:
        assert [m.span() for m in _OLD_FI_HISTORY_MARKER_RE.finditer(s)] == [
            m.span() for m in HISTORY_MARKER_RE.finditer(s)
        ], s
        assert _old_fi_strip(s) == strip_source_provenance_tail(s), s

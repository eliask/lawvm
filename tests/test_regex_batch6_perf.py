"""Performance regression tests for A19 regex landmine fixes.

Actuator 19 batch 6 (2026-05-29): bounded-regex + BRANCH-ordering fixes for
Sensor H findings across sweden/grafter.py and uk_legislation/source_parent_payloads.py.

Sites fixed:
  SE-1  grafter._CHAPTER_RE / _ITEM_RE
        \\s+ → bounded \\s{1,5}; payload .* → .{0,500}.
  SE-2  grafter._APPENDIX_RE
        (?:\\*\\s*)? nested quantifier → (\\s{0,5}\\*\\s{0,5}|\\s{1,5}|) BRANCH.
  SE-3  grafter._SE_REPLACE_CLAUSE_RE / _SE_REPEAL_CLAUSE_RE
        lookahead \\b\\s+att\\b → literal 'dels att' (no nested \\s+ in lookahead);
        outer \\s+ between anchors → \\s{1,50}.
  SE-4  grafter._SE_RENUMBER_CLAUSE_RE
        \\s+ → \\s{1,50}; inner \\s* → \\s{0,10}.
  UK-1  source_parent_payloads._UK_SOURCE_CARRIED_PARENT_QUOTED_CHILD_SUBSTITUTION_RE
        '( of [^,]+)?' → '( of [^,]{1,80}|)' BRANCH; space before dash handled.
  UK-2  source_parent_payloads._UK_SOURCE_CARRIED_STRUCTURED_TAIL_SUBSTITUTION_PARENT_RE
        '( of section [A-Za-z0-9]+\\b[^,]*)?' → '( of [^,]{1,120}|)' BRANCH;
        covers long contexts like 'of section 86 of the 1990 Act (period ...)'.

Each site has:
  1. Positive: known-good input matches.
  2. Negative: known-non-matching input returns no match quickly.
  3. Adversarial: long string that would have triggered catastrophic backtracking
     on the old unbounded pattern completes in < 100 ms.
"""
from __future__ import annotations

import time

_CEILING_MS = 100  # generous ceiling; old patterns: >1 s on adversarial inputs


# ---------------------------------------------------------------------------
# SE-3 — grafter._SE_REPLACE_CLAUSE_RE / _SE_REPEAL_CLAUSE_RE
# (highest risk: nested quantifier in lookahead + outer unbounded gap)
# ---------------------------------------------------------------------------

from lawvm.sweden.grafter import (  # type: ignore[attr-defined]
    _SE_REPLACE_CLAUSE_RE,
    _SE_REPEAL_CLAUSE_RE,
    _SE_RENUMBER_CLAUSE_RE,
)


def test_se_replace_clause_positive() -> None:
    text = "dels att 3 § ska ha följande lydelse"
    assert _SE_REPLACE_CLAUSE_RE.search(text) is not None


def test_se_replace_clause_negative_no_lydelse() -> None:
    text = "dels att 3 § ska upphöra att gälla"
    assert _SE_REPLACE_CLAUSE_RE.search(text) is None


def test_se_replace_clause_adversarial_long_no_lydelse_is_fast() -> None:
    """Long text with 'dels att' and 'ska' but no 'ha följande lydelse'.

    Old pattern: unbounded .{0,400}? between 'dels att' lookahead anchors with
    \\s+ on both sides would backtrack catastrophically.
    New: bounded gap + literal lookahead must complete in < 100 ms.
    """
    text = "dels att " + "x " * 500 + "ska inte matcha detta"
    t0 = time.perf_counter()
    result = _SE_REPLACE_CLAUSE_RE.search(text)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert result is None
    assert elapsed_ms < _CEILING_MS, (
        f"adversarial no-lydelse took {elapsed_ms:.1f} ms (ceiling {_CEILING_MS} ms); "
        "bounded-regex regression suspected in _SE_REPLACE_CLAUSE_RE"
    )


def test_se_replace_clause_adversarial_overlong_gap_is_fast() -> None:
    """Gap between 'att' and 'ska' exceeds .{0,400}? bound — must fail fast."""
    filler = "x " * 300  # >400 chars in gap
    text = f"dels att {filler} ska ha följande lydelse"
    t0 = time.perf_counter()
    result = _SE_REPLACE_CLAUSE_RE.search(text)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    # Gap exceeds bound — no match expected, but timing is the assertion.
    assert elapsed_ms < _CEILING_MS, (
        f"adversarial overlong-gap took {elapsed_ms:.1f} ms (ceiling {_CEILING_MS} ms); "
        "bounded-regex regression suspected in _SE_REPLACE_CLAUSE_RE"
    )


def test_se_repeal_clause_positive() -> None:
    text = "att 4 § ska upphöra att gälla"
    assert _SE_REPEAL_CLAUSE_RE.search(text) is not None


def test_se_repeal_clause_negative_no_upphöra() -> None:
    text = "att 4 § ska ha följande lydelse"
    assert _SE_REPEAL_CLAUSE_RE.search(text) is None


def test_se_repeal_clause_adversarial_long_no_upphöra_is_fast() -> None:
    """Long text without 'upphöra att gälla' — must fail fast."""
    text = "dels att " + "y " * 500 + "ska tillämpas"
    t0 = time.perf_counter()
    result = _SE_REPEAL_CLAUSE_RE.search(text)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert result is None
    assert elapsed_ms < _CEILING_MS, (
        f"adversarial no-upphöra took {elapsed_ms:.1f} ms (ceiling {_CEILING_MS} ms); "
        "bounded-regex regression suspected in _SE_REPEAL_CLAUSE_RE"
    )


# ---------------------------------------------------------------------------
# SE-4 — grafter._SE_RENUMBER_CLAUSE_RE
# ---------------------------------------------------------------------------


def test_se_renumber_clause_positive() -> None:
    text = "nuvarande 3 § ska betecknas 4 §"
    assert _SE_RENUMBER_CLAUSE_RE.search(text) is not None


def test_se_renumber_clause_negative_no_betecknas() -> None:
    text = "nuvarande 3 § ska upphöra"
    assert _SE_RENUMBER_CLAUSE_RE.search(text) is None


def test_se_renumber_clause_adversarial_long_no_betecknas_is_fast() -> None:
    text = "nuvarande " + "a " * 300 + "§ ska ersättas"
    t0 = time.perf_counter()
    result = _SE_RENUMBER_CLAUSE_RE.search(text)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert result is None
    assert elapsed_ms < _CEILING_MS, (
        f"adversarial no-betecknas took {elapsed_ms:.1f} ms (ceiling {_CEILING_MS} ms); "
        "bounded-regex regression suspected in _SE_RENUMBER_CLAUSE_RE"
    )


# ---------------------------------------------------------------------------
# SE-2 — grafter._APPENDIX_RE
# (BRANCH ordering: star variant tried before empty)
# ---------------------------------------------------------------------------

from lawvm.sweden.grafter import _APPENDIX_RE  # type: ignore[attr-defined]


def test_appendix_re_simple_positive() -> None:
    text = "Bilaga 1 Förordning om avgifter"
    m = _APPENDIX_RE.match(text)
    assert m is not None
    assert m.group("label") == "1"


def test_appendix_re_star_positive() -> None:
    """Bilaga* prefix (OCR artifact) must be parsed: label captures digit."""
    text = "Bilaga*1 Titel"
    m = _APPENDIX_RE.match(text)
    assert m is not None
    assert m.group("label") == "1"


def test_appendix_re_no_label_positive() -> None:
    text = "Bilaga Allmänna bestämmelser"
    m = _APPENDIX_RE.match(text)
    assert m is not None


def test_appendix_re_no_match_returns_none() -> None:
    text = "Kapitel 1 Inledande bestämmelser"
    assert _APPENDIX_RE.match(text) is None


def test_appendix_re_adversarial_long_no_bilaga_is_fast() -> None:
    """Long string with no 'Bilaga' prefix — must fail immediately at anchor."""
    text = "Avsnitt " + "x" * 5000
    t0 = time.perf_counter()
    result = _APPENDIX_RE.match(text)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert result is None
    assert elapsed_ms < _CEILING_MS, (
        f"adversarial no-Bilaga took {elapsed_ms:.1f} ms (ceiling {_CEILING_MS} ms); "
        "anchor fix regression suspected in _APPENDIX_RE"
    )


# ---------------------------------------------------------------------------
# SE-1 — grafter._CHAPTER_RE / _ITEM_RE
# ---------------------------------------------------------------------------

from lawvm.sweden.grafter import _CHAPTER_RE, _ITEM_RE  # type: ignore[attr-defined]


def test_chapter_re_positive() -> None:
    text = "1 kap. Allmänna bestämmelser"
    m = _CHAPTER_RE.match(text)
    assert m is not None
    assert m.group("label") == "1"


def test_chapter_re_roman_numeral_positive() -> None:
    text = "III kap. Tredje kapitlet"
    m = _CHAPTER_RE.match(text)
    assert m is not None
    assert m.group("label") == "III"


def test_chapter_re_negative_no_kap() -> None:
    text = "1 § Allmänna bestämmelser"
    assert _CHAPTER_RE.match(text) is None


def test_chapter_re_adversarial_long_no_kap_is_fast() -> None:
    """Long line with no 'kap.' — bounded \\s{1,5} ensures fast failure."""
    text = "1 " + "x" * 5000
    t0 = time.perf_counter()
    result = _CHAPTER_RE.match(text)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert result is None
    assert elapsed_ms < _CEILING_MS, (
        f"adversarial no-kap took {elapsed_ms:.1f} ms (ceiling {_CEILING_MS} ms); "
        "bounded-regex regression suspected in _CHAPTER_RE"
    )


def test_item_re_positive() -> None:
    text = "a. första stycket"
    m = _ITEM_RE.match(text)
    assert m is not None
    assert m.group("label") == "a"


def test_item_re_numeric_positive() -> None:
    text = "1. En skyldighet att lämna uppgifter"
    m = _ITEM_RE.match(text)
    assert m is not None


def test_item_re_negative_no_separator() -> None:
    text = "abc text without separator"
    assert _ITEM_RE.match(text) is None


# ---------------------------------------------------------------------------
# UK-1 — source_parent_payloads._UK_SOURCE_CARRIED_PARENT_QUOTED_CHILD_SUBSTITUTION_RE
# ---------------------------------------------------------------------------

from lawvm.uk_legislation.source_parent_payloads import (  # type: ignore[attr-defined]
    _UK_SOURCE_CARRIED_PARENT_QUOTED_CHILD_SUBSTITUTION_RE,
)


def test_uk_quoted_child_sub_positive_with_space_before_dash() -> None:
    """Canonical case from failing test: 'substitute - a where...'."""
    text = (
        '8 In section 130(3) of AFA 2006, for "if the charge is amended after referral." '
        "substitute - a where the charge is amended after referral; b to any charge."
    )
    normalized = " ".join(text.split()).strip()
    m = _UK_SOURCE_CARRIED_PARENT_QUOTED_CHILD_SUBSTITUTION_RE.match(normalized)
    assert m is not None
    assert m.group("section") == "130"
    assert m.group("subsection") == "3"
    assert "if the charge" in m.group("anchor")


def test_uk_quoted_child_sub_positive_em_dash() -> None:
    """Em-dash variant: 'substitute—'."""
    text = (
        "In section 5(1) of SCA 2007, for “old text” substitute—"
        "a first; b second."
    )
    normalized = " ".join(text.split()).strip()
    m = _UK_SOURCE_CARRIED_PARENT_QUOTED_CHILD_SUBSTITUTION_RE.match(normalized)
    assert m is not None


def test_uk_quoted_child_sub_negative_no_for_clause() -> None:
    text = "In section 5(1) of SCA 2007, substitute— a first; b second."
    normalized = " ".join(text.split()).strip()
    assert _UK_SOURCE_CARRIED_PARENT_QUOTED_CHILD_SUBSTITUTION_RE.match(normalized) is None


def test_uk_quoted_child_sub_adversarial_long_no_section_is_fast() -> None:
    """Long text without 'in section' — anchor guard fires immediately."""
    text = "In paragraph " + "x" * 5000 + " substitute a first; b second."
    normalized = " ".join(text.split()).strip()
    t0 = time.perf_counter()
    result = _UK_SOURCE_CARRIED_PARENT_QUOTED_CHILD_SUBSTITUTION_RE.match(normalized)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert result is None
    assert elapsed_ms < _CEILING_MS, (
        f"adversarial no-section took {elapsed_ms:.1f} ms (ceiling {_CEILING_MS} ms); "
        "bounded-regex regression in _UK_SOURCE_CARRIED_PARENT_QUOTED_CHILD_SUBSTITUTION_RE"
    )


def test_uk_quoted_child_sub_adversarial_long_of_context_is_fast() -> None:
    """Long 'of [^,]{1,80}' context that exceeds the bound — must fail fast."""
    long_of = "of " + "AFA " * 30  # >80 chars
    text = f"8 In section 130(3) {long_of}, for \"old text\" substitute - a first."
    normalized = " ".join(text.split()).strip()
    t0 = time.perf_counter()
    result = _UK_SOURCE_CARRIED_PARENT_QUOTED_CHILD_SUBSTITUTION_RE.match(normalized)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    # May or may not match (the [^,]{1,80} stops at 80 chars); timing is the assertion.
    assert elapsed_ms < _CEILING_MS, (
        f"adversarial long-of-context took {elapsed_ms:.1f} ms (ceiling {_CEILING_MS} ms); "
        "bounded-regex regression in _UK_SOURCE_CARRIED_PARENT_QUOTED_CHILD_SUBSTITUTION_RE"
    )


# ---------------------------------------------------------------------------
# UK-2 — source_parent_payloads._UK_SOURCE_CARRIED_STRUCTURED_TAIL_SUBSTITUTION_PARENT_RE
# (covers long 'of section N of the YEAR Act (description)' contexts)
# ---------------------------------------------------------------------------

from lawvm.uk_legislation.source_parent_payloads import (  # type: ignore[attr-defined]
    _UK_SOURCE_CARRIED_STRUCTURED_TAIL_SUBSTITUTION_PARENT_RE,
)


def test_uk_structured_tail_parent_simple_positive() -> None:
    text = (
        "In subsection (1), for the words from “for such period” "
        "onwards there shall be substituted"
    )
    normalized = " ".join(text.split()).strip()
    assert _UK_SOURCE_CARRIED_STRUCTURED_TAIL_SUBSTITUTION_PARENT_RE.search(normalized) is not None


def test_uk_structured_tail_parent_long_act_context_positive() -> None:
    """Full context: 'of section 86 of the 1990 Act (period of licences)'."""
    text = (
        "In subsection (1) of section 86 of the 1990 Act (period of licences), "
        "for the words from “for such period” onwards there shall be substituted"
    )
    normalized = " ".join(text.split()).strip()
    assert _UK_SOURCE_CARRIED_STRUCTURED_TAIL_SUBSTITUTION_PARENT_RE.search(normalized) is not None


def test_uk_structured_tail_parent_negative_no_onwards() -> None:
    text = (
        "In subsection (1) of section 86, for the words from “for such period” "
        "there shall be substituted"
    )
    normalized = " ".join(text.split()).strip()
    assert _UK_SOURCE_CARRIED_STRUCTURED_TAIL_SUBSTITUTION_PARENT_RE.search(normalized) is None


def test_uk_structured_tail_parent_adversarial_long_no_for_words_from_is_fast() -> None:
    """Long subsection text without 'for the words from' — must fail fast."""
    text = (
        "In subsection (1) of section 86 of the 1990 Act, "
        + "x " * 1000
        + "there shall be substituted"
    )
    normalized = " ".join(text.split()).strip()
    t0 = time.perf_counter()
    result = _UK_SOURCE_CARRIED_STRUCTURED_TAIL_SUBSTITUTION_PARENT_RE.search(normalized)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert result is None
    assert elapsed_ms < _CEILING_MS, (
        f"adversarial no-for-words-from took {elapsed_ms:.1f} ms (ceiling {_CEILING_MS} ms); "
        "bounded-regex regression in _UK_SOURCE_CARRIED_STRUCTURED_TAIL_SUBSTITUTION_PARENT_RE"
    )


def test_uk_structured_tail_parent_adversarial_long_of_context_is_fast() -> None:
    """'of [^,]{1,120}' context that exceeds the bound — must fail fast or match quickly."""
    long_context = "of section 86 " + "of the Act " * 15  # >120 chars
    text = (
        f"In subsection (1) {long_context}, "
        "for the words from “for such period” onwards there shall be substituted"
    )
    normalized = " ".join(text.split()).strip()
    t0 = time.perf_counter()
    _UK_SOURCE_CARRIED_STRUCTURED_TAIL_SUBSTITUTION_PARENT_RE.search(normalized)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert elapsed_ms < _CEILING_MS, (
        f"adversarial long-of-context took {elapsed_ms:.1f} ms (ceiling {_CEILING_MS} ms); "
        "bounded-regex regression in _UK_SOURCE_CARRIED_STRUCTURED_TAIL_SUBSTITUTION_PARENT_RE"
    )

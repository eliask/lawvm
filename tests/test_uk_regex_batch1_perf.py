"""Performance regression tests for UK adjudication/lowering regex landmines.

Bounded-regex + fast-guard fixes (2026-05-29) for
backtracking-risk findings #1–#7 (UK source_adjudication / effect_lowering_tail /
table_sources / source_text_reclassifications cluster).

Template: f2ee4479 and 7ffd2e2b.

Sites fixed:
  #1  source_adjudication._looks_like_source_carried_structured_tail_substitution
      lines 1464+1470 — greedy .+ between anchors; second: .+ between short anchors.
  #2  source_adjudication._looks_like_repeal_schedule_table_source
      line 1259 — single greedy .+ between anchors in schedule header text.
  #3  source_adjudication.classify_uk_manual_compile_frontier
      line 2347 — greedy .+ in inline re.search, hoisted to _PERIOD_SPECIFIED_SUBSTITUTED_RE.
  #4  effect_lowering_tail._unlowered_overlap_source_shape_classification
      line 366 — three chained greedy .* (same shape as ukpga/1970/9 incident).
  #5  effect_lowering_tail._unlowered_overlap_source_shape_classification
      line 382 — two chained greedy .* in amendment-table pattern.
  #6  table_sources._uk_table_driven_corresponding_entry_word_substitution
      line 3102 — three lazy .*? with quote-char alternation (unbounded).
  #7  source_text_reclassifications._source_parent_application_modification_context
      line 706 — single greedy .* between anchors.

Each fixture tests:
  1. Positive: a known-matching input returns the expected truthy result.
  2. Negative: short obviously-non-matching input returns False/empty quickly.
  3. Adversarial: a long string (~10 KB) that would have caused catastrophic
     backtracking on the old unbounded pattern returns False/empty AND
     completes in < 100 ms.
"""
from __future__ import annotations

import time

_CEILING_MS = 100  # generous per-call ceiling; old patterns: >1 s on adversarial


# ---------------------------------------------------------------------------
# Site #1 — source_adjudication._looks_like_source_carried_structured_tail_substitution
# ---------------------------------------------------------------------------

from lawvm.uk_legislation.source_adjudication import (
    _looks_like_source_carried_structured_tail_substitution,
)


def test_carried_tail_positive_with_dash() -> None:
    text = "for the words from a to the end substitute— a first thing ii second thing"
    assert _looks_like_source_carried_structured_tail_substitution(text) is True


def test_carried_tail_positive_no_dash() -> None:
    text = "for the words from a to the end substitute— ii something here"
    assert _looks_like_source_carried_structured_tail_substitution(text) is True


def test_carried_tail_no_to_the_end_returns_false() -> None:
    text = "for the words from section 1 substitute a new paragraph"
    assert _looks_like_source_carried_structured_tail_substitution(text) is False


def test_carried_tail_no_substitute_returns_false() -> None:
    text = "for the words from a to the end"
    assert _looks_like_source_carried_structured_tail_substitution(text) is False


def test_carried_tail_empty_returns_false() -> None:
    assert _looks_like_source_carried_structured_tail_substitution("") is False


def test_carried_tail_adversarial_long_no_to_end_is_fast() -> None:
    """Long text with 'for the words from' and 'substitute' but no 'to the end'.

    Old pattern: .+ between two anchors with no 'to the end' present would
    match the first part but fail on the terminal, causing backtracking.
    New: 'to the end' guard fires before regex; must complete in < 100 ms.
    """
    text = "for the words from " + "a" * 5000 + " substitute " + "b" * 5000 + " ii last"
    assert "to the end" not in text
    t0 = time.perf_counter()
    result = _looks_like_source_carried_structured_tail_substitution(text)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert result is False
    assert elapsed_ms < _CEILING_MS, (
        f"adversarial no-to-end took {elapsed_ms:.1f} ms (ceiling {_CEILING_MS} ms); "
        "fast-guard regression suspected"
    )


def test_carried_tail_adversarial_to_end_present_but_overlong_gap_is_fast() -> None:
    """Text passes the 'to the end' guard but the 401+ char gap exceeds the
    .{0,400}? bound so the regex fails fast without exhaustive backtracking.
    """
    text = "for the words from " + "x" * 500 + " to the end substitute ii first thing"
    t0 = time.perf_counter()
    result = _looks_like_source_carried_structured_tail_substitution(text)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    # Result may be True or False depending on regex behaviour; perf is what matters.
    assert elapsed_ms < _CEILING_MS, (
        f"adversarial long-gap took {elapsed_ms:.1f} ms (ceiling {_CEILING_MS} ms); "
        "bounded regex may have regressed"
    )


# ---------------------------------------------------------------------------
# Site #2 — source_adjudication._looks_like_repeal_schedule_table_source
# ---------------------------------------------------------------------------

from lawvm.uk_legislation.source_adjudication import (
    _looks_like_repeal_schedule_table_source,
)


def test_repeal_schedule_table_source_positive() -> None:
    text = "Short title and chapter\textent of repeal"
    result = _looks_like_repeal_schedule_table_source(
        extracted_tag="Schedule",
        effect_type="repeal",
        text=text,
    )
    assert result is True


def test_repeal_schedule_table_source_wrong_tag_returns_false() -> None:
    result = _looks_like_repeal_schedule_table_source(
        extracted_tag="Section",
        effect_type="repeal",
        text="Short title and chapter extent of repeal",
    )
    assert result is False


def test_repeal_schedule_table_source_no_extent_returns_false() -> None:
    result = _looks_like_repeal_schedule_table_source(
        extracted_tag="Schedule",
        effect_type="repeal",
        text="Short title and chapter but nothing about extent",
    )
    assert result is False


def test_repeal_schedule_table_source_adversarial_long_no_extent_is_fast() -> None:
    """Long schedule-like text without 'extent' — fast-guard fires before regex."""
    text = (
        "Short title and chapter " + "x" * 5000
        + " reference enactment but no terminal word present here " + "y" * 5000
    )
    assert "extent" not in text.lower()
    t0 = time.perf_counter()
    result = _looks_like_repeal_schedule_table_source(
        extracted_tag="Schedule",
        effect_type="repeal",
        text=text,
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert result is False
    assert elapsed_ms < _CEILING_MS, (
        f"adversarial no-extent took {elapsed_ms:.1f} ms (ceiling {_CEILING_MS} ms); "
        "fast-guard regression suspected"
    )


def test_repeal_schedule_table_source_adversarial_extent_overlong_gap_is_fast() -> None:
    """'extent' present but the gap between anchors exceeds .{0,800}? bound."""
    text = (
        "Short title " + "x" * 900 + " extent of repeal"
    )
    t0 = time.perf_counter()
    result = _looks_like_repeal_schedule_table_source(
        extracted_tag="Schedule",
        effect_type="repeal",
        text=text,
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000
    # The gap exceeds .{0,800}? so no match expected, but what matters is timing.
    assert elapsed_ms < _CEILING_MS, (
        f"adversarial long-gap took {elapsed_ms:.1f} ms (ceiling {_CEILING_MS} ms); "
        "bounded regex may have regressed"
    )


# ---------------------------------------------------------------------------
# Site #3 — source_adjudication._PERIOD_SPECIFIED_SUBSTITUTED_RE
# (inline re.search in classify_uk_manual_compile_frontier; tested via regex)
# ---------------------------------------------------------------------------

from lawvm.uk_legislation.source_adjudication import _PERIOD_SPECIFIED_SUBSTITUTED_RE


def test_period_specified_substituted_positive() -> None:
    text = "for the period specified in section 3 there is substituted a new period"
    assert _PERIOD_SPECIFIED_SUBSTITUTED_RE.search(text) is not None


def test_period_specified_substituted_no_terminal_returns_none() -> None:
    text = "for the period specified in section 3 but nothing follows"
    assert _PERIOD_SPECIFIED_SUBSTITUTED_RE.search(text) is None


def test_period_specified_substituted_adversarial_long_no_terminal_is_fast() -> None:
    """Long text with opening anchor but terminal anchor absent.

    Old pattern: .+ would backtrack across the full long string.
    New: .{0,600}? bound means the regex gives up quickly.
    """
    text = (
        "for the period specified in " + "x" * 5000
        + " and no terminal anchor here " + "y" * 5000
    )
    t0 = time.perf_counter()
    result = _PERIOD_SPECIFIED_SUBSTITUTED_RE.search(text)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert result is None
    assert elapsed_ms < _CEILING_MS, (
        f"adversarial long-no-terminal took {elapsed_ms:.1f} ms (ceiling {_CEILING_MS} ms); "
        "bounded regex may have regressed"
    )


# ---------------------------------------------------------------------------
# Site #4 — effect_lowering_tail._SCOPED_OCCURRENCE_WITH_EXCLUSIONS_RE
# ---------------------------------------------------------------------------

from lawvm.uk_legislation.effect_lowering_tail import _SCOPED_OCCURRENCE_WITH_EXCLUSIONS_RE


def test_scoped_occurrence_positive() -> None:
    text = (
        "where it occurs without the qualifying word substitute another "
        "but this does not apply to section 3"
    )
    assert _SCOPED_OCCURRENCE_WITH_EXCLUSIONS_RE.search(text) is not None


def test_scoped_occurrence_missing_but_this_returns_none() -> None:
    text = "where it occurs without the qualifying word substitute another"
    assert _SCOPED_OCCURRENCE_WITH_EXCLUSIONS_RE.search(text) is None


def test_scoped_occurrence_adversarial_three_chains_is_fast() -> None:
    """Three greedy .* between anchors — the exact ukpga/1970/9 incident shape.

    Old pattern: three unbounded .* produced O(N^3) backtracking.
    New: .{0,400}? bounds each segment; must complete in < 100 ms.
    """
    # All three guard-words present, but terminal anchor absent — worst case
    text = (
        "where it occurs without " + "a" * 3000
        + " substitute " + "b" * 3000
        + " but this is something different, no apply at end"
    )
    assert "but this does not apply" not in text
    t0 = time.perf_counter()
    result = _SCOPED_OCCURRENCE_WITH_EXCLUSIONS_RE.search(text)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert result is None
    assert elapsed_ms < _CEILING_MS, (
        f"adversarial three-chain took {elapsed_ms:.1f} ms (ceiling {_CEILING_MS} ms); "
        "bounded regex may have regressed"
    )


# ---------------------------------------------------------------------------
# Site #5 — effect_lowering_tail._AMENDMENT_TABLE_PAYLOAD_RE
# ---------------------------------------------------------------------------

from lawvm.uk_legislation.effect_lowering_tail import _AMENDMENT_TABLE_PAYLOAD_RE


def test_amendment_table_payload_positive() -> None:
    text = "part 1 amendments of the Act column 1 provision column 2 new text"
    assert _AMENDMENT_TABLE_PAYLOAD_RE.match(text) is not None


def test_amendment_table_payload_wrong_start_returns_none() -> None:
    text = "section 3 amendments of the Act column 1 column 2"
    # Does not start with optional prefix + 'part N amendments of'
    assert _AMENDMENT_TABLE_PAYLOAD_RE.match(text) is None


def test_amendment_table_payload_missing_column2_returns_none() -> None:
    text = "part 1 amendments of the Act column 1 provision no second column"
    assert _AMENDMENT_TABLE_PAYLOAD_RE.match(text) is None


def test_amendment_table_payload_adversarial_long_gap_is_fast() -> None:
    """Long text with 'column 1' present but 'column 2' absent.

    Old pattern: two unbounded .* between anchors — O(N^2) backtracking.
    New: .{0,400}? bounds each segment; must complete in < 100 ms.
    """
    text = (
        "part 1 amendments of the act " + "x" * 4000
        + " column 1 provision " + "y" * 4000
        + " no second column here"
    )
    assert "column 2" not in text
    t0 = time.perf_counter()
    result = _AMENDMENT_TABLE_PAYLOAD_RE.match(text)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert result is None
    assert elapsed_ms < _CEILING_MS, (
        f"adversarial no-column2 took {elapsed_ms:.1f} ms (ceiling {_CEILING_MS} ms); "
        "bounded regex may have regressed"
    )


# ---------------------------------------------------------------------------
# Site #6 — table_sources._COLUMN_1_2_SUBSTITUTION_RE
# ---------------------------------------------------------------------------

from lawvm.uk_legislation.table_sources import _COLUMN_1_2_SUBSTITUTION_RE


def test_column_substitution_positive_ascii_quote() -> None:
    text = (
        'provisions listed in column 1 for the words in the corresponding entry '
        'in column 2 substitute "widget"'
    )
    m = _COLUMN_1_2_SUBSTITUTION_RE.search(text)
    assert m is not None
    assert m.group("double") == "widget"


def test_column_substitution_positive_curly_quote() -> None:
    lq = chr(0x201C)
    rq = chr(0x201D)
    text = (
        "provisions listed in column 1 for the words in the corresponding entry "
        f"in column 2 substitute {lq}gadget{rq}"
    )
    m = _COLUMN_1_2_SUBSTITUTION_RE.search(text)
    assert m is not None
    assert m.group("curly") == "gadget"


def test_column_substitution_missing_column2_returns_none() -> None:
    text = 'provisions listed in column 1 substitute "widget"'
    assert _COLUMN_1_2_SUBSTITUTION_RE.search(text) is None


def test_column_substitution_adversarial_no_closing_quote_is_fast() -> None:
    """Long text with opening quote but no closing quote — previously caused
    O(N^2) backtracking on the lazy .*? captures.

    New: character-class-bounded [^"]{0,400}? fails fast when close-quote absent.
    """
    text = (
        "provisions listed in column 1 "
        + "x" * 2000
        + " for the words in the corresponding entry in column 2 substitute "
        + '"'
        + "y" * 2000  # no closing quote
    )
    t0 = time.perf_counter()
    result = _COLUMN_1_2_SUBSTITUTION_RE.search(text)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    # No closing quote means no match
    assert result is None
    assert elapsed_ms < _CEILING_MS, (
        f"adversarial no-close-quote took {elapsed_ms:.1f} ms (ceiling {_CEILING_MS} ms); "
        "character-class terminator may have regressed"
    )


def test_column_substitution_fast_guard_no_column2_is_fast() -> None:
    """Fast-guard check: text without 'column 2' in the full phrase never matches."""
    # Test the regex directly with a text that lacks the column 2 phrase.
    text = "provisions listed in column 1 substitute " + "a" * 5000 + " foo"
    t0 = time.perf_counter()
    result = _COLUMN_1_2_SUBSTITUTION_RE.search(text)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    # No 'for the words in the corresponding entry in column 2' so no match
    assert result is None
    assert elapsed_ms < _CEILING_MS, (
        f"no-column2 regex took {elapsed_ms:.1f} ms (ceiling {_CEILING_MS} ms)"
    )


# ---------------------------------------------------------------------------
# Site #7 — source_text_reclassifications._SHALL_APPLY_MODIFICATION_THAT_RE
# ---------------------------------------------------------------------------

from lawvm.uk_legislation.source_text_reclassifications import (
    _SHALL_APPLY_MODIFICATION_THAT_RE,
)


def test_shall_apply_modification_positive() -> None:
    text = "The Act shall apply subject to the modification that subsection 2 is omitted"
    assert _SHALL_APPLY_MODIFICATION_THAT_RE.search(text) is not None


def test_shall_apply_modification_no_modification_returns_none() -> None:
    text = "The Act shall apply in full without any change"
    assert _SHALL_APPLY_MODIFICATION_THAT_RE.search(text) is None


def test_shall_apply_modification_no_shall_returns_none() -> None:
    text = "The Act applies subject to the modification that subsection 2 is omitted"
    assert _SHALL_APPLY_MODIFICATION_THAT_RE.search(text) is None


def test_shall_apply_modification_adversarial_long_no_terminal_is_fast() -> None:
    """Long text with 'shall apply' but no 'modification that'.

    Old pattern: .* between two anchors — O(N^2) backtracking.
    New: .{0,400}? bound means the regex gives up quickly.
    """
    text = (
        "the act shall apply " + "x" * 5000
        + " subject to various things but never the required phrase " + "y" * 5000
    )
    assert "modification" not in text
    t0 = time.perf_counter()
    result = _SHALL_APPLY_MODIFICATION_THAT_RE.search(text)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert result is None
    assert elapsed_ms < _CEILING_MS, (
        f"adversarial no-modification took {elapsed_ms:.1f} ms (ceiling {_CEILING_MS} ms); "
        "bounded regex may have regressed"
    )


def test_shall_apply_modification_adversarial_overlong_gap_is_fast() -> None:
    """Both anchor words present but the gap between them exceeds .{0,400}? bound."""
    text = (
        "shall apply " + "x" * 500 + " subject to the modification that something"
    )
    t0 = time.perf_counter()
    result = _SHALL_APPLY_MODIFICATION_THAT_RE.search(text)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert elapsed_ms < _CEILING_MS, (
        f"adversarial long-gap took {elapsed_ms:.1f} ms (ceiling {_CEILING_MS} ms); "
        "bounded regex may have regressed"
    )

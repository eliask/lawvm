from __future__ import annotations

from lawvm.new_zealand.text_comparison import (
    normalize_nz_inline_comparison_text,
    normalized_nz_inline_contains,
    normalized_nz_inline_occurrence_count,
)


def test_nz_inline_comparison_normalizes_whitespace_and_inline_punctuation() -> None:
    # Display spaces are removed both after an opening paren and before a closing
    # paren, so "(  a )" collapses to "(a)" (symmetric paren-spacing cleanup).
    assert normalize_nz_inline_comparison_text("  old \n text  , (  a )  ") == "old text, (a)"


def test_nz_inline_comparison_removes_space_before_closing_paren_or_bracket() -> None:
    # A consolidated body renders an inline cross-reference followed by a closing
    # paren as "section 197 )" while an amending payload renders "section 197)";
    # the comparison-normalized forms must be equal.
    assert normalize_nz_inline_comparison_text("section 197 )") == "section 197)"
    assert normalize_nz_inline_comparison_text("item 4 ]") == "item 4]"


def test_nz_inline_occurrence_count_uses_shared_comparison_normalization() -> None:
    assert normalized_nz_inline_occurrence_count("old \n text, and old text ,", " old text, ") == 2
    assert normalized_nz_inline_occurrence_count("anything", " \n\t ") == 0
    assert normalized_nz_inline_contains("new text and old text", " old  text ")

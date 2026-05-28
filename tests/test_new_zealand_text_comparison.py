from __future__ import annotations

from lawvm.new_zealand.text_comparison import (
    normalize_nz_inline_comparison_text,
    normalized_nz_inline_contains,
    normalized_nz_inline_occurrence_count,
)


def test_nz_inline_comparison_normalizes_whitespace_and_inline_punctuation() -> None:
    assert normalize_nz_inline_comparison_text("  old \n text  , (  a )  ") == "old text, (a )"


def test_nz_inline_occurrence_count_uses_shared_comparison_normalization() -> None:
    assert normalized_nz_inline_occurrence_count("old \n text, and old text ,", " old text, ") == 2
    assert normalized_nz_inline_occurrence_count("anything", " \n\t ") == 0
    assert normalized_nz_inline_contains("new text and old text", " old  text ")

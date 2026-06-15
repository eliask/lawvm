"""Unit tests for the NZ oracle-divergence classifier.

Table-driven tests covering every sub-family, the PoC worked examples from
NZ_CONSOLIDATION_ERROR_SCOPE.md §4, edge cases, and fail-loud behaviour.
"""

from __future__ import annotations

import pytest

from lawvm.new_zealand.nz_oracle_normalization import (
    NZDivergenceClass,
    NZDivergenceSubFamily,
    _fold_numerals,
    _fold_punct_whitespace,
    _strip_trailing_period,
    _strip_zero_width,
    _tokenize_words,
    classify_oracle_divergence,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cls(candidate: str, oracle: str) -> NZDivergenceClass:
    return classify_oracle_divergence(candidate, oracle)


# ---------------------------------------------------------------------------
# Fold predicates — individually testable
# ---------------------------------------------------------------------------


class TestStripZeroWidth:
    def test_removes_bom(self) -> None:
        # U+FEFF zero-width no-break space / BOM
        assert _strip_zero_width("abc﻿def") == "abcdef"

    def test_removes_zero_width_space(self) -> None:
        assert _strip_zero_width("a​b") == "ab"

    def test_removes_zwnj(self) -> None:
        assert _strip_zero_width("a‌b") == "ab"

    def test_removes_zwj(self) -> None:
        assert _strip_zero_width("a‍b") == "ab"

    def test_removes_soft_hyphen(self) -> None:
        assert _strip_zero_width("a­b") == "ab"

    def test_plain_text_unchanged(self) -> None:
        assert _strip_zero_width("hello world") == "hello world"

    def test_empty_string(self) -> None:
        assert _strip_zero_width("") == ""


class TestFoldPunctWhitespace:
    def test_strips_trailing_period(self) -> None:
        assert _fold_punct_whitespace("hello.") == "hello"

    def test_strips_leading_comma(self) -> None:
        assert _fold_punct_whitespace(",hello") == "hello"

    def test_collapses_interior_whitespace(self) -> None:
        assert _fold_punct_whitespace("hello  world") == "hello world"

    def test_lower_cases(self) -> None:
        assert _fold_punct_whitespace("Hello World") == "hello world"


class TestFoldNumerals:
    def test_single_digit(self) -> None:
        assert _fold_numerals(["1"]) == ["one"]

    def test_multiple_digits(self) -> None:
        assert _fold_numerals(["1", "2", "3"]) == ["one", "two", "three"]

    def test_unknown_token_unchanged(self) -> None:
        assert _fold_numerals(["hello"]) == ["hello"]

    def test_mixed(self) -> None:
        assert _fold_numerals(["person", "of", "1", "of", "the"]) == [
            "person",
            "of",
            "one",
            "of",
            "the",
        ]

    def test_large_number_unchanged(self) -> None:
        # digits above 12 are not in the mapping
        assert _fold_numerals(["99"]) == ["99"]


class TestStripTrailingPeriod:
    def test_removes_single_trailing_period(self) -> None:
        assert _strip_trailing_period("this Act.") == "this Act"

    def test_no_period_unchanged(self) -> None:
        assert _strip_trailing_period("this Act") == "this Act"

    def test_strips_whitespace_before_period(self) -> None:
        assert _strip_trailing_period("this Act .  ") == "this Act"

    def test_only_one_period_removed(self) -> None:
        # Double period should lose only the last
        assert _strip_trailing_period("end..") == "end."


class TestTokenizeWords:
    def test_basic(self) -> None:
        assert _tokenize_words("Hello World") == ["hello", "world"]

    def test_punctuation_stripped(self) -> None:
        assert _tokenize_words("this Act.") == ["this", "act"]

    def test_empty(self) -> None:
        assert _tokenize_words("") == []


# ---------------------------------------------------------------------------
# PoC worked examples from NZ_CONSOLIDATION_ERROR_SCOPE.md §4
# ---------------------------------------------------------------------------


class TestPoCWorkedExamples:
    def test_digit_word_numeral(self) -> None:
        """act_public_1981_23 §14AB (insert): "person of 1 of the" vs "person of one of the"."""
        c = "...person of 1 of the..."
        o = "...person of one of the..."
        result = _cls(c, o)
        assert result.sub_family == NZDivergenceSubFamily.editorial_digit_word_numeral
        assert result.is_editorial is True

    def test_capitalization(self) -> None:
        """act_public_1955_37 §31: "the secretary of the Board" vs "the Secretary of the Board"."""
        c = "the secretary of the Board"
        o = "the Secretary of the Board"
        result = _cls(c, o)
        assert result.sub_family == NZDivergenceSubFamily.editorial_capitalization
        assert result.is_editorial is True

    def test_bom_zero_width(self) -> None:
        """act_public_1992_122 §79/§80: "Subsection (1)(e)" vs "Subsection (1)\\ufeff(e)"."""
        c = "Subsection (1)(e)"
        o = "Subsection (1)﻿(e)"
        result = _cls(c, o)
        assert result.sub_family == NZDivergenceSubFamily.editorial_bom_zero_width
        assert result.is_editorial is True

    def test_trailing_period(self) -> None:
        """act_public_1955_37 def Minister: "...this Act" vs "...this Act."."""
        c = "...this Act"
        o = "...this Act."
        result = _cls(c, o)
        assert result.sub_family == NZDivergenceSubFamily.editorial_trailing_punctuation
        assert result.is_editorial is True

    def test_word_spacing_motorcycle(self) -> None:
        """act_public_2001_49 §214: "moped or motor cycle" vs "moped or motorcycle"."""
        c = "A registered owner of a motor vehicle that is a moped or motor cycle must pay"
        o = "A registered owner of a motor vehicle that is a moped or motorcycle must pay"
        result = _cls(c, o)
        assert result.sub_family == NZDivergenceSubFamily.editorial_word_spacing
        assert result.is_editorial is True

    def test_word_spacing_with_bom_and_spacing(self) -> None:
        """act_public_2001_49 §213: BOM + "motor cycle" → folds to word_spacing."""
        c = "moped and motor cycle riders from the levy referred to in subsection (2)﻿(d)."
        o = "moped and motorcycle riders from the levy referred to in subsection (2)(d)."
        result = _cls(c, o)
        assert result.is_editorial is True
        assert result.sub_family == NZDivergenceSubFamily.editorial_word_spacing

    def test_word_spacing_does_not_fold_genuine_difference(self) -> None:
        """Removing spaces must NOT collapse a genuine multi-word content difference."""
        c = "sections 23E to 23H showing the amount"
        o = "sections 23E to 23H, 23J, and 23K showing the amount"
        result = _cls(c, o)
        assert result.is_editorial is False
        assert result.sub_family != NZDivergenceSubFamily.editorial_word_spacing

    def test_genuine_substantive_change(self) -> None:
        """A genuine content change that survives all folds."""
        c = "The Minister may by notice in writing"
        o = "The Commissioner may by notice in writing"
        result = _cls(c, o)
        assert result.sub_family == NZDivergenceSubFamily.substantive
        assert result.is_editorial is False

    def test_identical_strings(self) -> None:
        """Identical strings → agrees_after_normalization."""
        c = "This is the same text."
        o = "This is the same text."
        result = _cls(c, o)
        assert result.sub_family == NZDivergenceSubFamily.agrees_after_normalization
        assert result.is_editorial is True


# ---------------------------------------------------------------------------
# Additional sub-family coverage
# ---------------------------------------------------------------------------


class TestSubFamilyCoverage:
    def test_editorial_punctuation_whitespace(self) -> None:
        # Extra whitespace and trailing comma
        c = "the  Act"
        o = "the Act,"
        result = _cls(c, o)
        assert result.sub_family == NZDivergenceSubFamily.editorial_punctuation_whitespace
        assert result.is_editorial is True

    def test_structural_large_ratio(self) -> None:
        # Candidate is a full paragraph; oracle is just a short heading — ratio > 2×
        c = "Part 9 " + " ".join(["word"] * 50)
        o = "Part 9"
        result = _cls(c, o)
        assert result.sub_family == NZDivergenceSubFamily.structural
        assert result.is_editorial is False

    def test_structural_empty_oracle(self) -> None:
        # Oracle is empty → structural (one side has zero tokens)
        c = "some content here"
        o = ""
        result = _cls(c, o)
        assert result.sub_family == NZDivergenceSubFamily.structural
        assert result.is_editorial is False

    def test_structural_empty_candidate(self) -> None:
        c = ""
        o = "some content here"
        result = _cls(c, o)
        assert result.sub_family == NZDivergenceSubFamily.structural
        assert result.is_editorial is False


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_whitespace_only_diff(self) -> None:
        # Differ only by whitespace → punctuation_whitespace (collapsed + lowercased)
        c = "the  Act"
        o = "the Act"
        result = _cls(c, o)
        assert result.sub_family == NZDivergenceSubFamily.editorial_punctuation_whitespace
        assert result.is_editorial is True

    def test_combined_bom_and_capitalization(self) -> None:
        # BOM takes precedence over capitalization
        c = "hello﻿ world"
        o = "Hello World"
        # After stripping BOM from c: "hello world" != "Hello World",
        # so bom check fails; capitalization check: "hello world" == "hello world" → yes
        result = _cls(c, o)
        # BOM-strip does not resolve (case still differs), so capitalization wins
        assert result.sub_family == NZDivergenceSubFamily.editorial_capitalization
        assert result.is_editorial is True

    def test_empty_strings_both_empty(self) -> None:
        result = _cls("", "")
        assert result.sub_family == NZDivergenceSubFamily.agrees_after_normalization
        assert result.is_editorial is True

    def test_only_bom_differs_in_oracle(self) -> None:
        # Oracle has a BOM at the start, candidate doesn't
        c = "Section 42"
        o = "﻿Section 42"
        result = _cls(c, o)
        assert result.sub_family == NZDivergenceSubFamily.editorial_bom_zero_width
        assert result.is_editorial is True

    def test_numeral_fold_does_not_match_on_different_content(self) -> None:
        # "1 person" vs "two people" — digit becomes "one", not "two"
        c = "1 person"
        o = "two people"
        result = _cls(c, o)
        # "one person" != "two people" → substantive
        assert result.sub_family == NZDivergenceSubFamily.substantive
        assert result.is_editorial is False

    def test_trailing_period_both_have_same_trailing(self) -> None:
        # Both end in period — identical, so agrees
        c = "this Act."
        o = "this Act."
        result = _cls(c, o)
        assert result.sub_family == NZDivergenceSubFamily.agrees_after_normalization

    def test_multiline_whitespace_diff(self) -> None:
        c = "section\n4"
        o = "section 4"
        result = _cls(c, o)
        # After fold_punct_whitespace: both become "section 4"
        assert result.sub_family == NZDivergenceSubFamily.editorial_punctuation_whitespace
        assert result.is_editorial is True


# ---------------------------------------------------------------------------
# Fail-loud on invalid input
# ---------------------------------------------------------------------------


class TestFailLoud:
    def test_candidate_none_raises_type_error(self) -> None:
        with pytest.raises(TypeError, match="candidate_text must be str"):
            classify_oracle_divergence(None, "oracle")  # type: ignore[arg-type]

    def test_oracle_none_raises_type_error(self) -> None:
        with pytest.raises(TypeError, match="oracle_text must be str"):
            classify_oracle_divergence("candidate", None)  # type: ignore[arg-type]

    def test_candidate_int_raises_type_error(self) -> None:
        with pytest.raises(TypeError, match="candidate_text must be str"):
            classify_oracle_divergence(42, "oracle")  # type: ignore[arg-type]

    def test_oracle_int_raises_type_error(self) -> None:
        with pytest.raises(TypeError, match="oracle_text must be str"):
            classify_oracle_divergence("candidate", 42)  # type: ignore[arg-type]

    def test_both_none_raises_type_error(self) -> None:
        with pytest.raises(TypeError, match="candidate_text must be str"):
            classify_oracle_divergence(None, None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Return-type contracts
# ---------------------------------------------------------------------------


class TestReturnTypeContracts:
    def test_returns_frozen_dataclass(self) -> None:
        result = _cls("hello", "world")
        assert isinstance(result, NZDivergenceClass)
        # frozen: mutation must raise
        with pytest.raises(Exception):
            result.sub_family = NZDivergenceSubFamily.substantive  # type: ignore[misc]

    def test_is_editorial_consistent_with_sub_family(self) -> None:
        """is_editorial must match the sub_family enum value in every case."""
        cases = [
            ("a", "a"),  # agrees_after_normalization
            ("a﻿", "a"),  # editorial_bom_zero_width
            ("Hello", "hello"),  # editorial_capitalization
            ("this Act", "this Act."),  # editorial_trailing_punctuation
            ("1 item", "one item"),  # editorial_digit_word_numeral
            ("The Minister may act", "The Commissioner may act"),  # substantive
        ]
        for c, o in cases:
            result = _cls(c, o)
            expected_editorial = result.sub_family in {
                NZDivergenceSubFamily.agrees_after_normalization,
                NZDivergenceSubFamily.editorial_bom_zero_width,
                NZDivergenceSubFamily.editorial_punctuation_whitespace,
                NZDivergenceSubFamily.editorial_capitalization,
                NZDivergenceSubFamily.editorial_digit_word_numeral,
                NZDivergenceSubFamily.editorial_trailing_punctuation,
            }
            assert result.is_editorial == expected_editorial, (
                f"is_editorial mismatch for {c!r} vs {o!r}: "
                f"sub_family={result.sub_family}, is_editorial={result.is_editorial}"
            )

    def test_normalized_forms_are_strings(self) -> None:
        result = _cls("foo", "bar")
        assert isinstance(result.normalized_candidate, str)
        assert isinstance(result.normalized_oracle, str)

    def test_reason_is_non_empty_string(self) -> None:
        result = _cls("foo", "bar")
        assert isinstance(result.reason, str)
        assert result.reason

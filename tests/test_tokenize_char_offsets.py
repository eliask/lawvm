"""Tests for character offset tracking in peg3.tokenize().

Each Token produced by tokenize() should carry char_start and char_end
positions into the normalized input string (after whitespace collapse).
"""
from __future__ import annotations

import pytest

from lawvm.finland.johtolause.peg3 import tokenize, witness_char_span
from lawvm.core.parse_witness import ParseWitness


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _text_at(text: str, tok) -> str:
    """Extract the substring of normalized text covered by a token's char span."""
    import re
    normed = re.sub(r"\s+", " ", text).strip()
    if tok.char_start < 0 or tok.char_end < 0:
        return "<no-span>"
    return normed[tok.char_start:tok.char_end]


# ---------------------------------------------------------------------------
# Basic offset tests
# ---------------------------------------------------------------------------

class TestTokenizeCharOffsets:

    def test_all_tokens_have_offsets(self):
        """Every token produced by tokenize() should have non-negative char offsets."""
        text = "muutetaan 3 § seuraavasti:"
        tokens = tokenize(text)
        assert tokens, "Expected at least one token"
        for tok in tokens:
            assert tok.char_start >= 0, f"Missing char_start on {tok}"
            assert tok.char_end >= 0, f"Missing char_end on {tok}"
            assert tok.char_end > tok.char_start, f"Empty span on {tok}"

    def test_offsets_are_within_normalized_text(self):
        """All char offsets must lie within the normalized text bounds."""
        import re
        text = "  muutetaan  3 §  ja  5 §  seuraavasti:  "
        normed = re.sub(r"\s+", " ", text).strip()
        tokens = tokenize(text)
        for tok in tokens:
            assert 0 <= tok.char_start <= len(normed), f"char_start out of bounds: {tok}"
            assert 0 <= tok.char_end <= len(normed), f"char_end out of bounds: {tok}"

    def test_token_text_matches_normalized_substring(self):
        """For simple single-character and single-word tokens, the substring
        at the char span should match or contain the token's surface text."""
        import re
        text = "muutetaan 3 § seuraavasti:"
        normed = re.sub(r"\s+", " ", text).strip()
        tokens = tokenize(text)
        for tok in tokens:
            if tok.char_start >= 0 and tok.char_end >= 0:
                substring = normed[tok.char_start:tok.char_end]
                assert substring, f"Empty substring for {tok}"
                # The substring should contain (or equal) the token's text
                assert tok.text.lower() in substring.lower() or substring.lower() in tok.text.lower(), \
                    f"Token text {tok.text!r} not recoverable from span [{tok.char_start}:{tok.char_end}] = {substring!r}"

    def test_simple_verb_at_start(self):
        text = "muutetaan 3 §"
        tokens = tokenize(text)
        verb = tokens[0]
        assert verb.cat == "VERB"
        assert verb.char_start == 0
        assert verb.char_end == len("muutetaan")

    def test_section_sign_offset(self):
        """The § token should have the right char position."""
        import re
        text = "muutetaan 3 §"
        normed = re.sub(r"\s+", " ", text).strip()
        tokens = tokenize(text)
        pyk = next(t for t in tokens if t.cat == "PYKALA")
        assert normed[pyk.char_start:pyk.char_end] == "§"

    def test_number_offset(self):
        import re
        text = "muutetaan 3 §"
        normed = re.sub(r"\s+", " ", text).strip()
        tokens = tokenize(text)
        num = next(t for t in tokens if t.cat == "NUM")
        assert normed[num.char_start:num.char_end] == "3"

    def test_multiple_sections(self):
        """Check offsets for a list of sections: 3, 5 ja 7 §."""
        import re
        text = "muutetaan 3, 5 ja 7 §"
        normed = re.sub(r"\s+", " ", text).strip()
        tokens = tokenize(text)
        nums = [t for t in tokens if t.cat == "NUM"]
        assert len(nums) == 3
        for num in nums:
            sub = normed[num.char_start:num.char_end]
            assert sub.isdigit(), f"Expected digit at span, got {sub!r}"

    def test_compound_npykala_offsets(self):
        """'20§:n' splits into NUM + PYKALA; both should have correct offsets."""
        import re
        text = "muutetaan 20§:n"
        normed = re.sub(r"\s+", " ", text).strip()
        tokens = tokenize(text)
        num = next(t for t in tokens if t.cat == "NUM")
        pyk = next(t for t in tokens if t.cat == "PYKALA")
        assert normed[num.char_start:num.char_end] == "20"
        assert "§" in normed[pyk.char_start:pyk.char_end]

    def test_compound_num_letter_offsets(self):
        """'14a' splits into NUM + LETTER; offsets should be contiguous."""
        import re
        text = "muutetaan 14a §"
        normed = re.sub(r"\s+", " ", text).strip()
        tokens = tokenize(text)
        nums = [t for t in tokens if t.cat == "NUM"]
        letters = [t for t in tokens if t.cat == "LETTER"]
        assert nums, "Expected NUM token"
        assert letters, "Expected LETTER token"
        num = nums[0]
        let = letters[0]
        assert normed[num.char_start:num.char_end] == "14"
        assert normed[let.char_start:let.char_end] == "a"
        # They should be adjacent (no gap)
        assert num.char_end == let.char_start

    def test_whitespace_normalization_preserved_in_offsets(self):
        """Extra whitespace is collapsed before offsets are assigned."""
        import re
        text = "muutetaan    3   §"
        normed = re.sub(r"\s+", " ", text).strip()
        tokens = tokenize(text)
        # All token texts should be findable in the normalized form
        for tok in tokens:
            if tok.char_start >= 0:
                sub = normed[tok.char_start:tok.char_end]
                assert sub, f"Empty span for token {tok}"

    def test_range_token_offsets(self):
        """'21–23' splits into NUM + DASH + NUM; all should span the original fragment."""
        import re
        text = "muutetaan 21–23 §"
        normed = re.sub(r"\s+", " ", text).strip()
        tokens = tokenize(text)
        nums = [t for t in tokens if t.cat == "NUM"]
        assert len(nums) == 2
        # First NUM should start at position of '21'
        frag_start = normed.index("21–")
        assert nums[0].char_start == frag_start
        # Second NUM should end at the end of '23'
        assert nums[1].char_end == frag_start + len("21–23")


# ---------------------------------------------------------------------------
# witness_char_span tests
# ---------------------------------------------------------------------------

class TestWitnessCharSpan:

    def test_returns_none_for_absent_source_span(self):
        tokens = tokenize("muutetaan 3 §")
        w = ParseWitness(rule_id="target.section_ref", source_span=None)
        assert witness_char_span(w, tokens) is None

    def test_returns_span_for_single_token(self):
        import re
        text = "muutetaan 3 §"
        normed = re.sub(r"\s+", " ", text).strip()
        tokens = tokenize(text)
        # Find the NUM token index
        num_idx = next(i for i, t in enumerate(tokens) if t.cat == "NUM")
        w = ParseWitness(rule_id="target.section_ref", source_span=(num_idx, num_idx + 1))
        span = witness_char_span(w, tokens)
        assert span is not None
        assert span[0] >= 0
        assert span[1] > span[0]
        assert normed[span[0]:span[1]] == "3"

    def test_returns_span_covering_multiple_tokens(self):
        import re
        text = "muutetaan 3 §"
        normed = re.sub(r"\s+", " ", text).strip()
        tokens = tokenize(text)
        num_idx = next(i for i, t in enumerate(tokens) if t.cat == "NUM")
        pyk_idx = next(i for i, t in enumerate(tokens) if t.cat == "PYKALA")
        # Span from NUM to after PYKALA
        end_idx = pyk_idx + 1
        w = ParseWitness(rule_id="target.section_ref", source_span=(num_idx, end_idx))
        span = witness_char_span(w, tokens)
        assert span is not None
        covered = normed[span[0]:span[1]]
        assert "3" in covered
        assert "§" in covered

    def test_returns_none_for_out_of_range_span(self):
        tokens = tokenize("muutetaan 3 §")
        w = ParseWitness(rule_id="x", source_span=(0, 100))
        assert witness_char_span(w, tokens) is None

    def test_returns_none_for_empty_span(self):
        with pytest.raises(ValueError, match="source_span must be a non-empty half-open token span"):
            ParseWitness(rule_id="x", source_span=(2, 2))

    def test_char_span_on_filtered_stream(self):
        """witness_char_span works on the filtered stream from apply_annotations."""
        import re
        from lawvm.finland.johtolause.scan import apply_annotations

        text = "muutetaan 3 § seuraavasti:"
        normed = re.sub(r"\s+", " ", text).strip()
        raw_tokens = tokenize(text)
        filtered = apply_annotations(raw_tokens)

        # Find NUM and PYKALA in filtered stream
        num_idx = next(i for i, t in enumerate(filtered) if t.cat == "NUM")
        pyk_idx = next(i for i, t in enumerate(filtered) if t.cat == "PYKALA")
        w = ParseWitness(rule_id="target.section_ref", source_span=(num_idx, pyk_idx + 1))
        span = witness_char_span(w, filtered)
        assert span is not None
        covered = normed[span[0]:span[1]]
        assert "3" in covered
        assert "§" in covered

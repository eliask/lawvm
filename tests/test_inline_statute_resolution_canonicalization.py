"""Tests for Finnish statute ID canonicalization (legacy 2-digit-year form).

Covers:
  - _canonicalize_finnish_statute_id helper (Piece 1)
  - entailment validator accepting canonicalized legacy form (Piece 2)
  - stored claim has canonical resolved_statute_id after normalization (Piece 2)
"""
from __future__ import annotations

import hashlib


import lawvm.finland.claim_kinds  # noqa: F401 — activate claim kind registry

from lawvm.finland.claim_kinds.inline_statute_resolution import (
    _canonicalize_finnish_statute_id,
    _make_entailment_validator,
    _validate_entailment,
    validate_entailment,
)

# Validator without corpus check for canonicalization unit tests.
# These tests exercise citation-pattern parsing and year expansion, not corpus lookup.
_validate_entailment_no_corpus = _make_entailment_validator(check_corpus_existence=False)


# ---------------------------------------------------------------------------
# _canonicalize_finnish_statute_id
# ---------------------------------------------------------------------------


def test_canonicalize_4_digit_year_passes_through():
    assert _canonicalize_finnish_statute_id("587/2011") == "587/2011"


def test_canonicalize_4_digit_year_short_number_passes_through():
    assert _canonicalize_finnish_statute_id("1/2020") == "1/2020"


def test_canonicalize_2_digit_year_expands_to_1900s():
    assert _canonicalize_finnish_statute_id("361/72") == "361/1972"


def test_canonicalize_2_digit_year_low_number():
    assert _canonicalize_finnish_statute_id("71/23") == "71/1923"


def test_canonicalize_2_digit_number_2_digit_year():
    assert _canonicalize_finnish_statute_id("46/70") == "46/1970"


def test_canonicalize_non_finnish_returns_none():
    assert _canonicalize_finnish_statute_id("EU/2016/679") is None


def test_canonicalize_malformed_abc_returns_none():
    assert _canonicalize_finnish_statute_id("abc") is None


def test_canonicalize_malformed_bare_number_returns_none():
    assert _canonicalize_finnish_statute_id("1234") is None


def test_canonicalize_malformed_leading_slash_returns_none():
    assert _canonicalize_finnish_statute_id("/2011") is None


# ---------------------------------------------------------------------------
# Entailment validator: legacy form in validator via validate_entailment wrapper
# ---------------------------------------------------------------------------


class _FakeClaim:
    """Minimal stand-in for ManualCompilationClaim for validator testing."""

    def __init__(
        self,
        resolved_statute_id: str,
        citation_form: str,
        span_bytes: bytes,
    ) -> None:
        start = 0
        end = len(span_bytes)
        self.cited_source_span = (start, end)
        self.cited_source_hash = hashlib.sha256(span_bytes).hexdigest()
        self.value = (
            ("resolved_statute_id", resolved_statute_id),
            ("citation_form", citation_form),
        )
        self._span_bytes = span_bytes


def test_entailment_validator_accepts_canonicalized_legacy_form():
    """Span contains '(361/72)', LLM proposes resolved_statute_id='361/72'.
    Validator canonicalizes and accepts; citation-pattern check uses canonical form.
    Uses no-corpus-check variant because this test exercises canonicalization only.
    """
    span_text = b"Luonnonsuojelulain (361/72) mukaisesti..."
    claim = _FakeClaim(
        resolved_statute_id="361/72",
        citation_form="(361/72)",
        span_bytes=span_text,
    )
    result = _validate_entailment_no_corpus(claim, span_text)
    assert result.passed, f"Expected pass but got: {result.reason}"
    assert result.validator_name == "entailment_verified"


def test_entailment_validator_accepts_already_canonical():
    """Span contains '(587/2011)', LLM proposes resolved_statute_id='587/2011'.
    Validator accepts unchanged. Uses no-corpus-check variant (citation-pattern test only).
    """
    span_text = b"Viitaten lakiin (587/2011) pykala 3..."
    claim = _FakeClaim(
        resolved_statute_id="587/2011",
        citation_form="(587/2011)",
        span_bytes=span_text,
    )
    result = _validate_entailment_no_corpus(claim, span_text)
    assert result.passed, f"Expected pass but got: {result.reason}"


def test_entailment_validator_rejects_non_finnish_id():
    """Non-Finnish ID returns parse failure, not shape mismatch."""
    span_text = b"some text EU/2016/679 more text"
    claim = _FakeClaim(
        resolved_statute_id="EU/2016/679",
        citation_form="EU/2016/679",
        span_bytes=span_text,
    )
    result = validate_entailment(claim, span_text)
    assert not result.passed
    assert "does not parse" in result.reason


def test_entailment_validator_legacy_year_mismatch_fails():
    """Span has '(71/23)' but LLM proposes year '1924' — should fail."""
    span_text = b"Mukaisesti (71/23) paatettiin..."
    claim = _FakeClaim(
        resolved_statute_id="71/1924",
        citation_form="(71/23)",
        span_bytes=span_text,
    )
    result = validate_entailment(claim, span_text)
    assert not result.passed


# ---------------------------------------------------------------------------
# _validate_entailment: legacy form handled via canonical resolved_statute_id
# ---------------------------------------------------------------------------


def test_validate_entailment_canonical_form_matches_legacy_citation():
    """_validate_entailment receives already-canonical ID + legacy citation_form.
    _extract_year_number now handles 2-digit years so the year comparison passes.
    """
    result = _validate_entailment(
        resolved_statute_id="71/1923",
        citation_form="(71/23)",
        span_text="Luonnonsuojelulain (71/23) mukaan...",
    )
    assert result.passed, f"Expected pass: {result.reason}"


def test_validate_entailment_canonical_form_bare_legacy_citation():
    result = _validate_entailment(
        resolved_statute_id="361/1972",
        citation_form="361/72",
        span_text="Ks. myos 361/72 pykala 5...",
    )
    assert result.passed, f"Expected pass: {result.reason}"

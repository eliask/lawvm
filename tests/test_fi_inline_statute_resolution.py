"""Tests for fi.v1.INLINE_STATUTE_RESOLUTION claim kind.

Covers:
  - test_inline_statute_resolution_span_validator (happy + tamper path)
  - test_inline_statute_resolution_entailment_validator (3 citation styles + adversarial)
  - Registration in core registry
"""
from __future__ import annotations

import hashlib
import importlib


# Activate Finland claim kinds
importlib.import_module("lawvm.finland.claim_kinds")

from lawvm.core.manual_claims.kind_registry import get_claim_kind_spec
from lawvm.finland.claim_kinds.inline_statute_resolution import (
    _extract_year_number,
    _validate_entailment,
    _validate_span,
)


# ---------------------------------------------------------------------------
# Test: kind is registered
# ---------------------------------------------------------------------------


def test_inline_statute_resolution_is_registered():
    spec = get_claim_kind_spec("fi.v1.INLINE_STATUTE_RESOLUTION")
    assert spec is not None
    assert spec.claim_kind == "fi.v1.INLINE_STATUTE_RESOLUTION"
    assert spec.layer == "extraction"
    assert spec.jurisdiction == "fi"
    assert spec.span_validator is not None
    assert spec.entailment_validator is not None
    assert "resolved_statute_id" in spec.value_fields
    assert "citation_form" in spec.value_fields


# ---------------------------------------------------------------------------
# Test: span validator — happy path + tamper path
# ---------------------------------------------------------------------------


class TestSpanValidator:
    def _make_source(self, content: bytes = b"some text lain 1234/2020 more text") -> bytes:
        return content

    def _hash(self, source: bytes, start: int, end: int) -> str:
        return hashlib.sha256(source[start:end]).hexdigest()

    def test_span_validated_happy_path(self):
        source = b"prefix text lain 1234/2020 suffix text"
        start, end = 12, 26
        h = self._hash(source, start, end)
        result = _validate_span(
            claim_target=None,
            claim_value=None,
            source_bytes=source,
            cited_span=(start, end),
            cited_hash=h,
        )
        assert result.passed, f"Expected pass, got: {result.reason}"
        assert result.validator_name == "span_verified"

    def test_span_hash_mismatch_fails(self):
        source = b"prefix text lain 1234/2020 suffix text"
        start, end = 12, 26
        # Tamper: use wrong hash
        wrong_hash = "b" * 64
        result = _validate_span(
            claim_target=None,
            claim_value=None,
            source_bytes=source,
            cited_span=(start, end),
            cited_hash=wrong_hash,
        )
        assert not result.passed
        assert "mismatch" in result.reason

    def test_span_out_of_range_fails(self):
        source = b"short"
        result = _validate_span(
            claim_target=None,
            claim_value=None,
            source_bytes=source,
            cited_span=(0, 100),
            cited_hash="a" * 64,
        )
        assert not result.passed
        assert "out of range" in result.reason

    def test_span_empty_bytes_fails(self):
        """start >= end is invalid."""
        source = b"prefix text lain 1234/2020 suffix"
        result = _validate_span(
            claim_target=None,
            claim_value=None,
            source_bytes=source,
            cited_span=(10, 5),
            cited_hash="a" * 64,
        )
        assert not result.passed


# ---------------------------------------------------------------------------
# Test: entailment validator — 3 citation styles + adversarial
# ---------------------------------------------------------------------------


class TestEntailmentValidator:

    def test_citation_style_1_parenthesized(self):
        """Pattern: (1234/2020)"""
        span_text = "Asiasta säädetään myös laissa (1234/2020) jonka mukaan..."
        result = _validate_entailment(
            resolved_statute_id="1234/2020",
            citation_form="(1234/2020)",
            span_text=span_text,
        )
        assert result.passed, f"Expected pass: {result.reason}"

    def test_citation_style_2_lain_prefix(self):
        """Pattern: lain 1234/2020"""
        span_text = "Tähän sovelletaan lain 1234/2020 säännöksiä."
        result = _validate_entailment(
            resolved_statute_id="1234/2020",
            citation_form="lain 1234/2020",
            span_text=span_text,
        )
        assert result.passed, f"Expected pass: {result.reason}"

    def test_citation_style_3_vuoden_lain(self):
        """Pattern: vuoden YYYY lain N:o NNNN"""
        span_text = "Noudatetaan vuoden 2020 lain N:o 1234 mukaisia sääntöjä."
        result = _validate_entailment(
            resolved_statute_id="1234/2020",
            citation_form="vuoden 2020 lain N:o 1234",
            span_text=span_text,
        )
        assert result.passed, f"Expected pass: {result.reason}"

    def test_year_mismatch_fails(self):
        """Citation present but year mismatched → entailment fails."""
        span_text = "Viitaten lakiin lain 1234/2019 momenttiin."
        result = _validate_entailment(
            resolved_statute_id="1234/2020",
            citation_form="lain 1234/2019",
            span_text=span_text,
        )
        assert not result.passed
        assert "1234/2019" in result.reason or "1234/2020" in result.reason

    def test_number_mismatch_fails(self):
        """Citation year matches but number does not."""
        span_text = "Sovellettavaksi tulee lain 9999/2020 pykälä."
        result = _validate_entailment(
            resolved_statute_id="1234/2020",
            citation_form="lain 9999/2020",
            span_text=span_text,
        )
        assert not result.passed

    def test_citation_form_not_in_span_fails(self):
        """citation_form not present in span → fails."""
        span_text = "Tässä pykälässä ei viitata kyseiseen lakiin."
        result = _validate_entailment(
            resolved_statute_id="1234/2020",
            citation_form="lain 1234/2020",
            span_text=span_text,
        )
        assert not result.passed
        assert "not found" in result.reason

    def test_bad_resolved_id_shape_fails(self):
        """resolved_statute_id with wrong shape → fails."""
        span_text = "Kts. lain 1234/2020 määräykset."
        result = _validate_entailment(
            resolved_statute_id="not-a-valid-id",
            citation_form="lain 1234/2020",
            span_text=span_text,
        )
        assert not result.passed
        assert "NNNN/YYYY" in result.reason

    def test_unresolvable_citation_form_returns_unvalidated(self):
        """If citation_form is present in span but structurally unresolvable → UNVALIDATED."""
        span_text = "Kts. jokin muu asia here laki-viittaus more text."
        result = _validate_entailment(
            resolved_statute_id="1234/2020",
            citation_form="jokin muu asia here laki-viittaus",
            span_text=span_text,
        )
        # Still fails (passed=False) because it can't verify, but with specific detail
        assert not result.passed
        assert result.details == "unresolvable_citation_form"

    def test_slash_only_pattern(self):
        """Pattern: bare YYYY/NNNN in text."""
        span_text = "Mukaan 1234/2020 pykälät soveltuvat tähän tilanteeseen."
        result = _validate_entailment(
            resolved_statute_id="1234/2020",
            citation_form="1234/2020",
            span_text=span_text,
        )
        assert result.passed, f"Expected pass: {result.reason}"


# ---------------------------------------------------------------------------
# Test: _extract_year_number patterns
# ---------------------------------------------------------------------------


class TestExtractYearNumber:
    """_extract_year_number returns (year, statute_number).

    Finnish statute IDs are NNNN/YYYY (statute_number/year).
    e.g. 1234/2020 = statute 1234 of year 2020 → (year=2020, number=1234).
    """

    def test_parenthesized(self):
        assert _extract_year_number("(1234/2020)") == (2020, 1234)

    def test_lain_prefix(self):
        # lain 1234/2020 → statute 1234 of year 2020 → (2020, 1234)
        assert _extract_year_number("lain 1234/2020") == (2020, 1234)

    def test_vuoden_lain(self):
        assert _extract_year_number("vuoden 2020 lain N:o 1234") == (2020, 1234)

    def test_bare_slash(self):
        # 1234/2020 → statute 1234 of year 2020 → (2020, 1234)
        assert _extract_year_number("1234/2020") == (2020, 1234)

    def test_no_pattern_returns_none(self):
        assert _extract_year_number("ei viittausta tässä") is None

    def test_no_digit_year_returns_none(self):
        assert _extract_year_number("laki ilman numeroa") is None

    # -------------------------------------------------------------------
    # Date-decorated citation forms (real-corpus regression 2026-06-04)
    # Qwen extracts citation forms like "11.12.2014/1055" and "(29.1.1999/77)"
    # where the date (DD.MM.YYYY) precedes the statute number after the slash.
    # Without the _CITE_DATE_DECORATED_RE guard, _CITE_SLASHONLY_RE would
    # match "2014/1055" → (year=1055, number=2014) — inverting year/number.
    # -------------------------------------------------------------------

    def test_date_decorated_form_dd_mm_yyyy_slash_number(self):
        """'11.12.2014/1055' → statute 1055 of year 2014 → (2014, 1055)."""
        result = _extract_year_number("11.12.2014/1055")
        assert result == (2014, 1055), f"Expected (2014, 1055), got {result}"

    def test_date_decorated_form_parenthesized(self):
        """'(29.1.1999/77)' → statute 77 of year 1999 → (1999, 77)."""
        result = _extract_year_number("(29.1.1999/77)")
        assert result == (1999, 77), f"Expected (1999, 77), got {result}"

    def test_date_decorated_form_single_digit_month(self):
        """'5.3.2000/123' → statute 123 of year 2000 → (2000, 123)."""
        result = _extract_year_number("5.3.2000/123")
        assert result == (2000, 123), f"Expected (2000, 123), got {result}"

    def test_date_decorated_entailment_validator_passes(self):
        """End-to-end: date-decorated citation form passes entailment validation."""
        span_text = "Sovellettavaksi tulee 11.12.2014/1055 mukainen sääntely."
        result = _validate_entailment(
            resolved_statute_id="1055/2014",
            citation_form="11.12.2014/1055",
            span_text=span_text,
        )
        assert result.passed, f"Expected pass for date-decorated form: {result.reason}"

    def test_date_decorated_paren_entailment_validator_passes(self):
        """End-to-end: parenthesized date-decorated citation form passes."""
        span_text = "Luonnonsuojelulain (29.1.1999/77) mukaiseksi erityiseksi suojelualueeksi."
        result = _validate_entailment(
            resolved_statute_id="77/1999",
            citation_form="(29.1.1999/77)",
            span_text=span_text,
        )
        assert result.passed, f"Expected pass for parenthesized date form: {result.reason}"

    def test_plain_parenthesized_not_broken(self):
        """'(71/23)' still works correctly after adding date-decorated pattern."""
        result = _extract_year_number("(71/23)")
        assert result == (1923, 71), f"Expected (1923, 71), got {result}"

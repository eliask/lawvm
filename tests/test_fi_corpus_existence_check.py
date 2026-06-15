"""Tests for Bug C fix: entailment validator corpus-existence check.

Covers:
  1. test_entailment_rejects_eu_regulation_misparsed_as_finnish
  2. test_entailment_accepts_real_finnish_statute_via_canonicalization
  3. test_corpus_existence_check_caches (multiple validations, one parquet read)
  4. test_entailment_skips_corpus_check_when_parquet_unavailable (soft failure)
  5. test_number_year_to_year_number_conversion
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from lawvm.finland.claim_kinds.inline_statute_resolution import (
    _make_entailment_validator,
    _number_year_to_year_number,
    _validate_entailment,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeClaim:
    """Minimal stand-in for ManualCompilationClaim for validator testing."""

    def __init__(
        self,
        resolved_statute_id: str,
        citation_form: str,
        span_bytes: bytes,
    ) -> None:
        self.cited_source_span = (0, len(span_bytes))
        self.cited_source_hash = hashlib.sha256(span_bytes).hexdigest()
        self.value = (
            ("resolved_statute_id", resolved_statute_id),
            ("citation_form", citation_form),
        )


def _make_parquet_with_ids(tmp_path: Path, statute_ids_year_number: list[str]) -> str:
    """Create a minimal statutes.parquet with the given statute_ids (YYYY/N format).

    Falls back to JSONL simulation: since _load_statute_ids_from_parquet reads
    parquet, we create a real parquet file using pyarrow.
    """
    import importlib.util
    has_pyarrow = importlib.util.find_spec("pyarrow") is not None
    if not has_pyarrow:
        pytest.skip("pyarrow not available")

    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pa.table({"statute_id": statute_ids_year_number})
    path = str(tmp_path / "statutes.parquet")
    pq.write_table(table, path)
    return path


# ---------------------------------------------------------------------------
# Test 1: EU regulation misparsed as Finnish statute is rejected
# ---------------------------------------------------------------------------


def test_entailment_rejects_eu_regulation_misparsed_as_finnish(tmp_path):
    """(EU) N:o 1210/2010 → LLM extracts resolved_statute_id='1210/2010'.

    The shape check passes (1210/2010 matches NNNN/YYYY) and the citation_form
    check passes (1210/2010 is in the span and parses to year=2010, number=1210).
    The corpus-existence check must reject it because 2010/1210 is NOT in the
    Finnish statute corpus.
    """
    # Create a synthetic corpus parquet WITHOUT 2010/1210
    parquet_path = _make_parquet_with_ids(tmp_path, ["2003/434", "2011/587", "1999/621"])

    # Invalidate the lru_cache so our synthetic parquet is loaded
    from lawvm.finland.claim_kinds.inline_statute_resolution import _load_statute_ids_from_parquet
    _load_statute_ids_from_parquet.cache_clear()

    span_text = "Soveltamisala kattaa asetuksen (EU) N:o 1210/2010 mukaisen toiminnan."
    # LLM correctly extracts 1210/2010 (shape matches Finnish statute ID)
    result = _validate_entailment(
        resolved_statute_id="1210/2010",
        citation_form="1210/2010",
        span_text=span_text,
        check_corpus_existence=True,
        statutes_parquet=parquet_path,
    )

    assert not result.passed, (
        "Expected rejection: 1210/2010 maps to 2010/1210 which is not in the corpus"
    )
    assert result.details == "not_in_corpus", f"Expected 'not_in_corpus', got: {result.details!r}"
    assert "not present in Finnish statute corpus" in result.reason

    _load_statute_ids_from_parquet.cache_clear()


# ---------------------------------------------------------------------------
# Test 2: Real Finnish statute passes corpus check
# ---------------------------------------------------------------------------


def test_entailment_accepts_real_finnish_statute_via_canonicalization(tmp_path):
    """(587/2011) → resolved_statute_id='587/2011'.

    587/2011 maps to 2011/587 in the corpus (year/number format).
    With 2011/587 in the parquet, the check passes.
    """
    parquet_path = _make_parquet_with_ids(tmp_path, ["2011/587", "2003/434", "1999/621"])

    from lawvm.finland.claim_kinds.inline_statute_resolution import _load_statute_ids_from_parquet
    _load_statute_ids_from_parquet.cache_clear()

    span_text = "Viitaten vesilakiin (587/2011) pykala 3..."
    result = _validate_entailment(
        resolved_statute_id="587/2011",
        citation_form="(587/2011)",
        span_text=span_text,
        check_corpus_existence=True,
        statutes_parquet=parquet_path,
    )

    assert result.passed, f"Expected pass for real Finnish statute 587/2011: {result.reason}"

    _load_statute_ids_from_parquet.cache_clear()


# ---------------------------------------------------------------------------
# Test 3: Corpus check caches after first load
# ---------------------------------------------------------------------------


def test_corpus_existence_check_caches(tmp_path):
    """Multiple validations do not re-read parquet (lru_cache check)."""
    parquet_path = _make_parquet_with_ids(tmp_path, ["2011/587"])

    from lawvm.finland.claim_kinds.inline_statute_resolution import _load_statute_ids_from_parquet
    _load_statute_ids_from_parquet.cache_clear()

    span_text = "Viitaten lakiin (587/2011) pykala 3..."

    for _ in range(5):
        _validate_entailment(
            resolved_statute_id="587/2011",
            citation_form="(587/2011)",
            span_text=span_text,
            check_corpus_existence=True,
            statutes_parquet=parquet_path,
        )

    # lru_cache info: after 5 calls to the same parquet, hits >= 4
    info = _load_statute_ids_from_parquet.cache_info()
    assert info.hits >= 4, (
        f"Expected ≥4 cache hits after 5 validations, got {info.hits} hits, "
        f"{info.misses} misses"
    )

    _load_statute_ids_from_parquet.cache_clear()


# ---------------------------------------------------------------------------
# Test 4: Soft failure when parquet is unavailable
# ---------------------------------------------------------------------------


def test_entailment_skips_corpus_check_when_parquet_unavailable():
    """When statutes.parquet does not exist, corpus check is skipped (not rejected).

    The check must not reject claims just because the corpus is absent.
    """
    from lawvm.finland.claim_kinds.inline_statute_resolution import _load_statute_ids_from_parquet
    _load_statute_ids_from_parquet.cache_clear()

    span_text = "Viitaten lakiin (587/2011) pykala 3..."
    result = _validate_entailment(
        resolved_statute_id="587/2011",
        citation_form="(587/2011)",
        span_text=span_text,
        check_corpus_existence=True,
        statutes_parquet="/nonexistent/path/statutes.parquet",
    )

    # corpus unavailable → check skipped → passes on shape + citation form
    assert result.passed, (
        f"Expected pass when corpus parquet unavailable: {result.reason}"
    )

    _load_statute_ids_from_parquet.cache_clear()


# ---------------------------------------------------------------------------
# Test 5: _number_year_to_year_number conversion
# ---------------------------------------------------------------------------


def test_number_year_to_year_number_canonical():
    """434/2003 → 2003/434."""
    assert _number_year_to_year_number("434/2003") == "2003/434"


def test_number_year_to_year_number_short():
    """1/2000 → 2000/1."""
    assert _number_year_to_year_number("1/2000") == "2000/1"


def test_number_year_to_year_number_invalid():
    """Non-statute ID → None."""
    assert _number_year_to_year_number("EU/2016/679") is None
    assert _number_year_to_year_number("abc") is None


# ---------------------------------------------------------------------------
# Test 6: _make_entailment_validator factory — corpus check OFF
# ---------------------------------------------------------------------------


def test_make_entailment_validator_no_corpus_check():
    """_make_entailment_validator(check_corpus_existence=False) skips corpus check."""
    validator = _make_entailment_validator(check_corpus_existence=False)

    span_bytes = b"Viitaten lakiin (9999/9999) pykala 3..."
    claim = _FakeClaim(
        resolved_statute_id="9999/9999",
        citation_form="(9999/9999)",
        span_bytes=span_bytes,
    )
    result = validator(claim, span_bytes)

    # 9999/9999 does not exist in any real corpus, but corpus check is OFF
    # → result depends only on shape + citation form checks, which pass
    assert result.passed, (
        f"Expected pass with corpus check OFF for synthetic ID 9999/9999: {result.reason}"
    )

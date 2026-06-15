"""Tests for Bug A fix: _scan_frontier_from_parquet now uses fi_inline_citations.parquet.

Covers:
  1. JSONL fallback: NULL canonical_id rows produce ExtractionFrontierRow with citation_text
  2. Non-NULL rows are skipped
  3. Real-corpus regression: non-zero frontier rows from fi_inline_citations.parquet
  4. fi_refs source: NULL target_statute_id rows (legacy path)
  5. Default source is inline_citations (not fi_refs)
  6. citation_text is carried on ExtractionFrontierRow from inline_citations
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lawvm.tools.cmd_propose_claims import (
    _FRONTIER_SOURCE_FI_REFS,
    _FRONTIER_SOURCE_INLINE_CITATIONS,
    _scan_frontier_from_parquet,
)


# ---------------------------------------------------------------------------
# Test 1: JSONL fallback — NULL canonical_id rows produce ExtractionFrontierRow
# ---------------------------------------------------------------------------


def test_scan_inline_citations_jsonl_null_rows_produce_frontier(tmp_path: Path):
    """JSONL with NULL canonical_id rows → ExtractionFrontierRow with citation_text."""
    rows = [
        {
            "source_doc_id": "1734/3-000",
            "source_doc_kind": "statute",
            "source_provision_ref": "",
            "kind": "old_committee",
            "canonical_id": None,
            "raw_text": "lvk.miet. 4/82",
            "case_year": None,
            "case_number": None,
            "context": "enacted_statute_body",
            "source_span_file": None,
            "source_span_byte_offset": None,
            "source_span_byte_len": None,
        },
        {
            "source_doc_id": "1734/3-000",
            "source_doc_kind": "statute",
            "source_provision_ref": "",
            "kind": "old_committee",
            "canonical_id": None,
            "raw_text": "svk.miet. 106/82",
            "case_year": None,
            "case_number": None,
            "context": "enacted_statute_body",
            "source_span_file": None,
            "source_span_byte_offset": None,
            "source_span_byte_len": None,
        },
    ]
    jsonl_path = tmp_path / "fi_inline_citations.jsonl"
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")

    result = _scan_frontier_from_parquet(
        str(tmp_path),
        "fi.v1.INLINE_STATUTE_RESOLUTION",
        frontier_source=_FRONTIER_SOURCE_INLINE_CITATIONS,
    )

    assert len(result) == 2, f"Expected 2 frontier rows, got {len(result)}"
    row0 = result[0]
    assert row0.statute_id == "1734/3-000"
    assert row0.slot == "canonical_id"
    assert row0.citation_text == "lvk.miet. 4/82"
    assert row0.claim_kind == "fi.v1.INLINE_STATUTE_RESOLUTION"


# ---------------------------------------------------------------------------
# Test 2: Non-NULL canonical_id rows are skipped
# ---------------------------------------------------------------------------


def test_scan_inline_citations_non_null_rows_skipped(tmp_path: Path):
    """Rows with canonical_id populated are NOT included in frontier."""
    rows = [
        {
            "source_doc_id": "1996/1091",
            "source_doc_kind": "statute",
            "source_provision_ref": "",
            "kind": "statute_inline",
            "canonical_id": "554/1995",
            "raw_text": "kiinteistönmuodostamislain (554/1995)",
            "case_year": 1995,
            "case_number": 554,
            "context": "enacted_statute_body",
            "source_span_file": None,
            "source_span_byte_offset": None,
            "source_span_byte_len": None,
        },
        {
            "source_doc_id": "1734/3-000",
            "source_doc_kind": "statute",
            "source_provision_ref": "",
            "kind": "old_committee",
            "canonical_id": None,
            "raw_text": "lvk.miet. 4/82",
            "case_year": None,
            "case_number": None,
            "context": "enacted_statute_body",
            "source_span_file": None,
            "source_span_byte_offset": None,
            "source_span_byte_len": None,
        },
    ]
    jsonl_path = tmp_path / "fi_inline_citations.jsonl"
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")

    result = _scan_frontier_from_parquet(
        str(tmp_path),
        "fi.v1.INLINE_STATUTE_RESOLUTION",
        frontier_source=_FRONTIER_SOURCE_INLINE_CITATIONS,
    )

    assert len(result) == 1, f"Expected 1 frontier row (non-NULL skipped), got {len(result)}"
    assert result[0].statute_id == "1734/3-000"
    assert result[0].citation_text == "lvk.miet. 4/82"


# ---------------------------------------------------------------------------
# Test 3: fi_refs legacy path — NULL target_statute_id rows
# ---------------------------------------------------------------------------


def test_scan_fi_refs_null_target_rows(tmp_path: Path):
    """fi_refs JSONL with NULL target_statute_id → ExtractionFrontierRow."""
    rows = [
        {
            "source_statute_id": "1996/1091",
            "source_provision_ref_str": "1996/1091/3",
            "target_statute_id": None,
            "target_provision_ref_str": None,
            "cite_kind": "cross_statute",
            "cite_confidence": "unresolved",
            "edge_subtype": "CITES",
            "phrase_lemma": "ref_element",
            "source_span_byte_offset": 100,
            "source_span_len": 30,
        },
        {
            "source_statute_id": "1996/1091",
            "source_provision_ref_str": "1996/1091/5",
            "target_statute_id": "554/1995",  # non-NULL — should be skipped
            "target_provision_ref_str": "554/1995",
            "cite_kind": "cross_statute",
            "cite_confidence": "exact",
            "edge_subtype": "CITES",
            "phrase_lemma": "ref_element",
            "source_span_byte_offset": 200,
            "source_span_len": 20,
        },
    ]
    jsonl_path = tmp_path / "fi_refs.jsonl"
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")

    result = _scan_frontier_from_parquet(
        str(tmp_path),
        "fi.v1.INLINE_STATUTE_RESOLUTION",
        frontier_source=_FRONTIER_SOURCE_FI_REFS,
    )

    assert len(result) == 1, f"Expected 1 frontier row (non-NULL skipped), got {len(result)}"
    assert result[0].statute_id == "1996/1091"
    assert result[0].slot == "target_statute_id"
    assert result[0].citation_text is None  # fi_refs rows carry no citation_text


# ---------------------------------------------------------------------------
# Test 4: Default frontier source is inline_citations
# ---------------------------------------------------------------------------


def test_scan_default_source_is_inline_citations(tmp_path: Path):
    """Without explicit frontier_source, defaults to inline_citations."""
    rows = [
        {
            "source_doc_id": "1734/3-000",
            "source_doc_kind": "statute",
            "source_provision_ref": "",
            "kind": "old_committee",
            "canonical_id": None,
            "raw_text": "lvk.miet. 4/82",
            "case_year": None,
            "case_number": None,
            "context": "enacted_statute_body",
            "source_span_file": None,
            "source_span_byte_offset": None,
            "source_span_byte_len": None,
        },
    ]
    jsonl_path = tmp_path / "fi_inline_citations.jsonl"
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")

    # No fi_refs.jsonl — if the default was fi_refs this would produce 0 rows
    result = _scan_frontier_from_parquet(
        str(tmp_path),
        "fi.v1.INLINE_STATUTE_RESOLUTION",
    )

    assert len(result) == 1, (
        f"Expected 1 row from default inline_citations source, got {len(result)}"
    )


# ---------------------------------------------------------------------------
# Test 5: citation_text field carried on ExtractionFrontierRow
# ---------------------------------------------------------------------------


def test_frontier_row_carries_citation_text(tmp_path: Path):
    """ExtractionFrontierRow.citation_text carries the raw_text from the parquet."""
    rows = [
        {
            "source_doc_id": "1996/1091",
            "source_doc_kind": "statute",
            "source_provision_ref": "section:3",
            "kind": "old_committee",
            "canonical_id": None,
            "raw_text": "svk.miet. 106/82",
            "case_year": None,
            "case_number": None,
            "context": "enacted_statute_body",
            "source_span_file": None,
            "source_span_byte_offset": None,
            "source_span_byte_len": None,
        },
    ]
    jsonl_path = tmp_path / "fi_inline_citations.jsonl"
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")

    result = _scan_frontier_from_parquet(
        str(tmp_path),
        "fi.v1.INLINE_STATUTE_RESOLUTION",
        frontier_source=_FRONTIER_SOURCE_INLINE_CITATIONS,
    )

    assert len(result) == 1
    fr = result[0]
    assert fr.citation_text == "svk.miet. 106/82"
    assert fr.provision_ref == "section:3"


# ---------------------------------------------------------------------------
# Test 6: Real-corpus regression — non-zero rows from fi_inline_citations.parquet
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_real_corpus_inline_citations_frontier_nonzero():
    """Real-corpus regression: fi_inline_citations.parquet yields non-zero frontier rows.

    Marked @pytest.mark.slow — requires data/fi/v1/fi_inline_citations.parquet.
    The real corpus has 2,378 NULL canonical_id rows (all old_committee kind).
    """
    from pathlib import Path

    parquet_path = Path("data/fi/v1/fi_inline_citations.parquet")
    if not parquet_path.exists():
        pytest.skip("data/fi/v1/fi_inline_citations.parquet not present — real-corpus test skipped")

    result = _scan_frontier_from_parquet(
        "data/fi/v1",
        "fi.v1.INLINE_STATUTE_RESOLUTION",
        frontier_source=_FRONTIER_SOURCE_INLINE_CITATIONS,
    )

    assert len(result) > 0, (
        "Expected non-zero frontier rows from fi_inline_citations.parquet; "
        "got 0 — check that canonical_id column has NULL values in the parquet"
    )
    # All frontier rows should have citation_text populated
    for fr in result[:10]:
        assert fr.citation_text is not None, (
            f"Expected citation_text on frontier row for {fr.statute_id!r}, got None"
        )

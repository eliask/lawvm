"""Tests for the Finnish PDF-blob store-boundary guard + junk-scan report.

The guard (``lawvm.finland.pdf_blob_guard``) prevents an HTTP-error body from
being archived as if it were a PDF artifact — the bug where a fetch of a Finlex
``.../{lang}@/main.pdf`` endpoint stored the 18-byte ``HTTP 404 Not Found`` body
in place of the PDF.  These tests are hermetic (a temp Farchive; no network).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lawvm.finland.pdf_blob_guard import (
    REJECT_EMPTY,
    REJECT_HTML,
    REJECT_HTTP_ERROR,
    REJECT_NOT_PDF,
    classify_pdf_blob,
    format_junk_pdf_report,
    is_real_pdf_blob,
    scan_farchive_for_junk_pdf_blobs,
)

# A minimal-but-real PDF header (what a genuine artifact starts with).
_REAL_PDF = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<< >>\nendobj\n"
# The exact junk the buggy fetcher archived.
_HTTP_404 = b"HTTP 404 Not Found"
_HTML_404 = b"<!DOCTYPE html><html><body>404 Not Found</body></html>"


# ---------------------------------------------------------------------------
# Pure predicate: is_real_pdf_blob / classify_pdf_blob
# ---------------------------------------------------------------------------

def test_predicate_accepts_real_pdf() -> None:
    assert is_real_pdf_blob(_REAL_PDF) is True
    assert is_real_pdf_blob(b"%PDF") is True  # bare magic still admitted


def test_predicate_rejects_http_error_body() -> None:
    assert is_real_pdf_blob(_HTTP_404) is False
    assert is_real_pdf_blob(b"HTTP 404") is False


def test_predicate_rejects_html_and_empty() -> None:
    assert is_real_pdf_blob(_HTML_404) is False
    assert is_real_pdf_blob(b"") is False
    assert is_real_pdf_blob(b"<html>...") is False


def test_classify_accepts_real_pdf_with_empty_reason() -> None:
    v = classify_pdf_blob(_REAL_PDF)
    assert v.is_pdf is True
    assert v.reject_reason == ""
    assert v.size == len(_REAL_PDF)
    assert v.head_bytes.startswith(b"%PDF")


def test_classify_names_http_error_reason() -> None:
    v = classify_pdf_blob(_HTTP_404)
    assert v.is_pdf is False
    assert v.reject_reason == REJECT_HTTP_ERROR
    assert v.size == len(_HTTP_404)
    assert v.head_bytes == _HTTP_404[:24]


def test_classify_names_html_and_empty_and_other_reasons() -> None:
    assert classify_pdf_blob(_HTML_404).reject_reason == REJECT_HTML
    assert classify_pdf_blob(b"").reject_reason == REJECT_EMPTY
    assert classify_pdf_blob(b"just some bytes").reject_reason == REJECT_NOT_PDF


# ---------------------------------------------------------------------------
# Junk-scan over a hermetic temp Farchive
# ---------------------------------------------------------------------------

def _temp_archive(tmp_path: Path):
    from farchive import Farchive

    return Farchive(tmp_path / "test.farchive")


def test_scan_finds_planted_junk_and_passes_good_pdf(tmp_path: Path) -> None:
    fa = _temp_archive(tmp_path)
    good_loc = (
        "https://opendata.finlex.fi/finlex/avoindata/v1/akn/fi/act/"
        "statute/2018/1121/fin@/main.pdf"
    )
    bad_loc = (
        "https://opendata.finlex.fi/finlex/avoindata/v1/akn/fi/act/"
        "statute-consolidated/1889/39/fin@/main.pdf"
    )
    # A non-.pdf locator that also lacks %PDF must NOT be flagged (scan is
    # scoped to .pdf locators only).
    xml_loc = "finlex://sd/2018/1121/fin/main.xml"

    fa.store(good_loc, _REAL_PDF, storage_class="pdf")
    fa.store(bad_loc, _HTTP_404, storage_class="pdf")
    fa.store(xml_loc, b"<akomaNtoso/>", storage_class="xml")

    findings = scan_farchive_for_junk_pdf_blobs(fa)

    assert [f.locator for f in findings] == [bad_loc]
    (junk,) = findings
    assert junk.size == len(_HTTP_404)
    assert junk.reject_reason == REJECT_HTTP_ERROR
    assert junk.head_bytes == _HTTP_404[:24]


def test_scan_clean_archive_returns_empty(tmp_path: Path) -> None:
    fa = _temp_archive(tmp_path)
    fa.store("x/y/main.pdf", _REAL_PDF, storage_class="pdf")
    assert scan_farchive_for_junk_pdf_blobs(fa) == []
    assert "no junk" in format_junk_pdf_report([])


def test_scan_glob_restricts_scope(tmp_path: Path) -> None:
    fa = _temp_archive(tmp_path)
    fa.store("https://a/main.pdf", _HTTP_404, storage_class="pdf")
    fa.store("finlex://b/main.pdf", _HTTP_404, storage_class="pdf")

    findings = scan_farchive_for_junk_pdf_blobs(fa, locator_glob="https://%")
    assert [f.locator for f in findings] == ["https://a/main.pdf"]


def test_report_lists_findings_and_flags_report_only() -> None:
    v = classify_pdf_blob(_HTTP_404)
    from lawvm.finland.pdf_blob_guard import JunkPdfBlob

    report = format_junk_pdf_report(
        [
            JunkPdfBlob(
                locator="https://x/main.pdf",
                size=v.size,
                head_bytes=v.head_bytes,
                reject_reason=v.reject_reason,
            )
        ]
    )
    assert "1 junk" in report
    assert "report only, not deleted" in report
    assert "https://x/main.pdf" in report


# ---------------------------------------------------------------------------
# Store-boundary guard in the import_zip lane
# ---------------------------------------------------------------------------

def test_import_zip_store_rejects_non_pdf_pdf_blob(tmp_path: Path) -> None:
    from lawvm.tools.import_zip import ImportReport, _store_zip_entry

    fa = _temp_archive(tmp_path)
    report = ImportReport()
    loc = "akn/fi/act/statute-consolidated/1889/39/media/corrigenda/sk1.pdf"
    _store_zip_entry(
        farchive=fa,
        locator=loc,
        data=_HTTP_404,
        observed_at=None,
        storage_class="pdf",
        metadata={},
        source_label="test.zip",
        zip_entry_name="entry.pdf",
        seen_locators={},
        skip_existing=False,
        dry_run=False,
        report=report,
    )
    # The junk was NOT archived; a typed skip was recorded instead.
    assert fa.resolve(loc) is None
    assert report.total_imported == 0
    assert report.total_skipped == 1
    (skip,) = report.skipped_entries
    assert skip["rule_id"] == "finlex_import_pdf_blob_not_pdf"
    assert skip["reject_reason"] == REJECT_HTTP_ERROR


def test_import_zip_store_accepts_real_pdf(tmp_path: Path) -> None:
    from lawvm.tools.import_zip import ImportReport, _store_zip_entry

    fa = _temp_archive(tmp_path)
    report = ImportReport()
    loc = "akn/fi/act/statute-consolidated/1889/39/media/corrigenda/sk1.pdf"
    _store_zip_entry(
        farchive=fa,
        locator=loc,
        data=_REAL_PDF,
        observed_at=None,
        storage_class="pdf",
        metadata={},
        source_label="test.zip",
        zip_entry_name="entry.pdf",
        seen_locators={},
        skip_existing=False,
        dry_run=False,
        report=report,
    )
    span = fa.resolve(loc)
    assert span is not None
    assert fa.read(span.digest) == _REAL_PDF
    assert report.total_imported == 1
    assert report.total_skipped == 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))

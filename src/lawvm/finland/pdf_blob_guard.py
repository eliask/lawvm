"""pdf_blob_guard.py — reject HTTP-error bodies masquerading as PDF blobs.

Data-integrity guard for the Finnish corpus acquisition lanes.

Motivation
----------
An http fetch of the Finlex Open Data ``.../{lang}@/main.pdf`` endpoints once
archived the HTTP *error response body* (the 18-byte ASCII string
``HTTP 404 Not Found``) as if it were the PDF artifact.  ``pypdfium2`` then
raises "Data format error" on those blobs downstream, and the junk locator
silently occupies the slot a real PDF should hold.

A real PDF begins with the ``%PDF`` magic bytes (per ISO 32000).  An HTTP-error
body — or an HTML error page, or an empty response — does not.  This module
provides:

  * :func:`is_real_pdf_blob` — the pure predicate (``%PDF`` magic check);
  * :func:`classify_pdf_blob` — a typed :class:`PdfBlobVerdict` that also names
    *why* a rejected blob was rejected (http-error / html / empty / other),
    for a legible acquisition-skip receipt;
  * :func:`scan_farchive_for_junk_pdf_blobs` — a read-only scan that reports
    every ``.pdf`` locator whose stored bytes lack the ``%PDF`` magic.

The predicate is placed at the *store boundary* of each acquirer so the junk is
never archived in the first place.  The scan is a report-only auditor for
archives that already contain junk: it NEVER deletes (deletion via
``Farchive.purge`` is the operator's call).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator

# ISO 32000: a conforming PDF file begins with "%PDF-".  We match the shorter
# "%PDF" prefix so a truncated-but-real header is still admitted; an HTTP-error
# body or HTML page never starts this way.
_PDF_MAGIC: bytes = b"%PDF"

# Rejected-blob reason families (stable strings for typed skip receipts).
REJECT_EMPTY: str = "empty"
REJECT_HTTP_ERROR: str = "http_error_body"
REJECT_HTML: str = "html_error_page"
REJECT_NOT_PDF: str = "not_pdf_magic"

# An HTTP-error body archived by a naive fetcher looks like ``HTTP 404 ...``.
_HTTP_ERROR_PREFIX: bytes = b"HTTP "

# HTML error pages (some endpoints serve a styled 404) start with a doctype or
# an opening tag.  Detected only for a legible reason label — any of these is
# already rejected by the magic-bytes check.
_HTML_PREFIXES: tuple[bytes, ...] = (b"<!DOCTYPE", b"<!doctype", b"<html", b"<HTML", b"<?xml")


def is_real_pdf_blob(data: bytes) -> bool:
    """Return True iff ``data`` begins with the ``%PDF`` magic bytes.

    This is the pure store-boundary predicate: a real PDF passes, an HTTP-error
    body (``HTTP 404 Not Found``), an HTML error page, and empty bytes all fail.
    """
    return data[:4] == _PDF_MAGIC


def _reject_reason(data: bytes) -> str:
    """Classify *why* a non-PDF blob is not a PDF (for the skip receipt)."""
    if not data:
        return REJECT_EMPTY
    if data[: len(_HTTP_ERROR_PREFIX)] == _HTTP_ERROR_PREFIX:
        return REJECT_HTTP_ERROR
    head = data.lstrip()[:16]
    if any(head.startswith(prefix) for prefix in _HTML_PREFIXES):
        return REJECT_HTML
    return REJECT_NOT_PDF


@dataclass(frozen=True, slots=True)
class PdfBlobVerdict:
    """Typed verdict for a would-be PDF blob at the store boundary.

    ``is_pdf`` is the accept/reject decision (``%PDF`` magic present).  On
    rejection, ``reject_reason`` names the family (one of the ``REJECT_*``
    constants) and ``head_bytes`` carries the first bytes for the diagnostic.
    On acceptance, ``reject_reason`` is the empty string.
    """

    is_pdf: bool
    reject_reason: str
    size: int
    head_bytes: bytes


def classify_pdf_blob(data: bytes, *, head_len: int = 24) -> PdfBlobVerdict:
    """Classify a would-be PDF blob into a typed :class:`PdfBlobVerdict`.

    Accepts a real PDF (``%PDF`` magic); rejects everything else with a named
    reason.  ``head_len`` bounds the diagnostic prefix retained.
    """
    if is_real_pdf_blob(data):
        return PdfBlobVerdict(
            is_pdf=True, reject_reason="", size=len(data), head_bytes=data[:head_len]
        )
    return PdfBlobVerdict(
        is_pdf=False,
        reject_reason=_reject_reason(data),
        size=len(data),
        head_bytes=data[:head_len],
    )


@dataclass(frozen=True, slots=True)
class JunkPdfBlob:
    """One report-only finding: a ``.pdf`` locator whose bytes are not a PDF."""

    locator: str
    size: int
    head_bytes: bytes
    reject_reason: str


def _looks_like_pdf_locator(locator: str) -> bool:
    return locator.endswith(".pdf")


def scan_farchive_for_junk_pdf_blobs(
    archive: Any,
    *,
    locator_glob: str | None = None,
) -> list[JunkPdfBlob]:
    """Scan a farchive for ``.pdf`` locators whose stored bytes are not a PDF.

    Read-only: this REPORTS junk, it never deletes.  Deletion (via
    ``Farchive.purge``) is the operator's call after reviewing the report.

    Args:
        archive: a Farchive (or compatible) exposing ``locators()``,
            ``resolve(locator)`` (→ span with ``.digest``) and ``read(digest)``.
        locator_glob: optional locator filter passed to ``archive.locators``;
            when ``None`` every locator is scanned and ``.pdf`` ones checked.

    Returns:
        the list of :class:`JunkPdfBlob` findings (empty when the archive is
        clean), in locator order.
    """
    findings: list[JunkPdfBlob] = []
    for locator in _iter_locators(archive, locator_glob):
        if not _looks_like_pdf_locator(locator):
            continue
        span = archive.resolve(locator)
        if span is None:
            continue
        data = archive.read(span.digest)
        if data is None:
            continue
        verdict = classify_pdf_blob(data)
        if verdict.is_pdf:
            continue
        findings.append(
            JunkPdfBlob(
                locator=locator,
                size=verdict.size,
                head_bytes=verdict.head_bytes,
                reject_reason=verdict.reject_reason,
            )
        )
    findings.sort(key=lambda f: f.locator)
    return findings


def _iter_locators(archive: Any, locator_glob: str | None) -> Iterator[str]:
    if locator_glob is not None:
        yield from archive.locators(locator_glob)
    else:
        yield from archive.locators()


def format_junk_pdf_report(findings: list[JunkPdfBlob]) -> str:
    """Render a human-readable junk-PDF report (report-only; no deletion)."""
    if not findings:
        return "pdf-blob scan: no junk .pdf blobs found (all .pdf locators carry %PDF magic)"
    lines = [f"pdf-blob scan: {len(findings)} junk .pdf blob(s) found (report only, not deleted):"]
    for f in findings:
        lines.append(
            f"  [{f.reject_reason}] {f.size:>10,} B  {f.head_bytes!r}  {f.locator}"
        )
    lines.append(
        "  → these are NOT real PDFs (missing %PDF magic). Delete with Farchive.purge "
        "after review; the guard prevents new ones at the store boundary."
    )
    return "\n".join(lines)

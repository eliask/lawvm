"""Shared ``pdftotext`` wrapper for Finlex PDFs.

Extracts visible-text from PDF bytes by shelling out to ``pdftotext``
(present on the system). Used by:
  - corrigendum PDF text extraction (triage, reextract)
  - attachment PDF → IR parsing pipeline (attachment_ir.py)

Single function, no caching — callers cache as appropriate per use-site.
"""
from __future__ import annotations

import subprocess


def pdf_to_text(pdf_bytes: bytes, max_pages: int = 5000) -> str | None:
    """Return visible-text of ``pdf_bytes`` via ``pdftotext``.

    Returns ``None`` when the command fails or the PDF is unparseable.
    ``max_pages`` caps the page count as a zip-bomb guard — Finlex
    attachment PDFs can be hundreds of pages (technical annexes, fee
    schedules). 5000 is a generous ceiling; real attachment PDFs are
    typically 1-200 pages.
    """
    if not pdf_bytes:
        return None
    try:
        result = subprocess.run(
            ["pdftotext", "-l", str(max_pages), "-", "-"],
            input=pdf_bytes,
            capture_output=True,
            check=True,
            timeout=120,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return None
    return result.stdout.decode("utf-8", errors="replace")

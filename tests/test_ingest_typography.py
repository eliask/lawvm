"""Hermetic tests for the meta.v2 typography char lane (pdfplumber → PageLine).

Covers the deterministic, pdfplumber-free machinery: font-name → family / bold /
italic parsing, document-adaptive size_class (relative to the page median), the
pdfplumber-span → pypdfium2-PageLine geometry alignment (fake spans), graceful
absence when a line cannot be aligned, the char-grouping collapse, and the
typo.* keys' round-trip through the NodeMetadata codec + into the lowered node.

A single live 1-page real-PDF test exercises the true pdfplumber extraction; it
skips when the ``pdf`` extra is absent (never a hard dep in the test gate).
"""
from __future__ import annotations

import pytest

from lawvm.core.source_document.anchors import BBox
from lawvm.ingest.metadata import decode_metadata, encode_metadata
from lawvm.ingest.page_elements import (
    PageElementProducer,
    PageLine,
    TypographySpan,
    _font_family,
    _font_is_bold,
    _font_is_italic,
    _spans_from_chars,
    align_typography_to_lines,
    size_class_for,
)
from lawvm.ingest.page_level import _metadata_for_text


# --------------------------------------------------------------------------- #
# Font-name → family / bold / italic (pure string parsers).                    #
# --------------------------------------------------------------------------- #


def test_font_family_strips_subset_tag_and_style_suffix() -> None:
    assert _font_family("ABCDEF+TimesNewRoman-BoldItalic") == "TimesNewRoman"
    assert _font_family("Helvetica-Bold") == "Helvetica"
    assert _font_family("Arial,Italic") == "Arial"
    # No subset tag, no style suffix → passthrough.
    assert _font_family("Georgia") == "Georgia"
    # A real hyphenated family whose tail is NOT a style word is kept intact.
    assert _font_family("Helvetica-Neue") == "Helvetica-Neue"


def test_bold_italic_detected_from_font_name() -> None:
    assert _font_is_bold("ABCDEF+TimesNewRoman-Bold") is True
    assert _font_is_bold("Arial-Black") is True
    assert _font_is_bold("TimesNewRoman") is False
    assert _font_is_italic("Georgia-Italic") is True
    assert _font_is_italic("Minion-Oblique") is True
    assert _font_is_italic("Georgia") is False


# --------------------------------------------------------------------------- #
# Document-adaptive size_class (relative to the page median).                  #
# --------------------------------------------------------------------------- #


def test_size_class_is_relative_to_the_page_median() -> None:
    median = 10.0
    assert size_class_for(14.0, median) == "heading"  # 1.4x → heading
    assert size_class_for(10.0, median) == "body"  # at median → body
    assert size_class_for(8.0, median) == "caption"  # 0.8x → caption
    # No median / non-positive → no honest class (absent, never guessed).
    assert size_class_for(12.0, None) is None
    assert size_class_for(12.0, 0.0) is None
    assert size_class_for(0.0, 10.0) is None


# --------------------------------------------------------------------------- #
# Span → PageLine geometry alignment (fake spans, no pdfplumber).              #
# --------------------------------------------------------------------------- #


def _span(font: str, size: float, bbox: BBox, *, bold=False, italic=False) -> TypographySpan:
    return TypographySpan(text="x", bbox=bbox, font=font, size=size, bold=bold, italic=italic)


def test_span_aligns_to_the_overlapping_pageline_by_geometry() -> None:
    # Two lines stacked vertically; each aligns to the span sharing its y-band.
    lines = (
        PageLine(text="HEADING", y_order=0, bbox=BBox(72, 700, 300, 720), band="body"),
        PageLine(text="body text", y_order=1, bbox=BBox(72, 400, 500, 414), band="body"),
    )
    # Spans arrive already family-collapsed from _collapse_row (font stripped);
    # alignment copies them verbatim onto the geometrically-overlapping line.
    spans = (
        _span("Arial", 18.0, BBox(72, 701, 290, 719), bold=True),
        _span("Times", 10.0, BBox(72, 401, 480, 413)),
    )
    out = align_typography_to_lines(lines, spans)
    # median of {18,10} = 14 → 18/14=1.29 heading, 10/14=0.71 caption
    assert out[0].font == "Arial" and out[0].bold is True and out[0].size_class == "heading"
    assert out[1].font == "Times" and out[1].bold is False and out[1].size_class == "caption"


def test_unalignable_line_leaves_typography_absent() -> None:
    # A line whose y-band overlaps NO span keeps typo.* absent (never guessed).
    lines = (PageLine(text="orphan", y_order=0, bbox=BBox(72, 100, 300, 114), band="body"),)
    spans = (_span("Times", 10.0, BBox(72, 700, 300, 714)),)  # far away in y
    out = align_typography_to_lines(lines, spans)
    assert out[0].font is None and out[0].size_class is None
    assert out[0].bold is False and out[0].italic is False


def test_line_without_bbox_is_never_aligned() -> None:
    lines = (PageLine(text="no geometry", y_order=0, bbox=None),)
    spans = (_span("Times", 10.0, BBox(72, 700, 300, 714)),)
    out = align_typography_to_lines(lines, spans)
    assert out[0].font is None and out[0].bbox is None


def test_alignment_picks_the_greatest_overlap_when_two_spans_compete() -> None:
    line = PageLine(text="row", y_order=0, bbox=BBox(72, 400, 500, 414), band="body")
    # A well-overlapping body span vs a barely-touching one; the former wins.
    good = _span("Georgia", 11.0, BBox(72, 401, 490, 413))
    poor = _span("Courier", 9.0, BBox(480, 401, 900, 413))  # small x overlap
    out = align_typography_to_lines((line,), (poor, good))
    assert out[0].font == "Georgia"


# --------------------------------------------------------------------------- #
# Char grouping → per-line spans (fake pdfplumber char dicts).                 #
# --------------------------------------------------------------------------- #


def _char(text, x0, x1, y0, y1, font, size):
    return {"text": text, "x0": x0, "x1": x1, "y0": y0, "y1": y1, "fontname": font, "size": size}


def test_spans_from_chars_groups_lines_and_takes_dominant_font() -> None:
    chars = [
        # line 1 (y~700): mostly Bold, one stray Regular → dominant Bold
        _char("H", 72, 82, 700, 712, "Arial-Bold", 12.0),
        _char("i", 82, 88, 700, 712, "Arial-Bold", 12.0),
        _char("!", 88, 92, 700, 712, "Arial", 12.0),
        # line 2 (y~400): all Times
        _char("a", 72, 80, 400, 410, "Times", 10.0),
        _char("b", 80, 88, 400, 410, "Times", 10.0),
    ]
    spans = _spans_from_chars(chars, page_h=800.0)
    assert len(spans) == 2
    # Top of page (larger y) first.
    assert spans[0].bbox.y0 == 700 and spans[0].font == "Arial" and spans[0].bold is True
    assert spans[1].font == "Times" and spans[1].size == 10.0
    assert spans[0].text == "Hi!"


def test_spans_from_chars_skips_whitespace_only_chars() -> None:
    chars = [_char(" ", 72, 76, 400, 410, "Times", 10.0)]
    assert _spans_from_chars(chars, page_h=800.0) == ()


# --------------------------------------------------------------------------- #
# End-to-end through the metadata codec + the node lowering.                   #
# --------------------------------------------------------------------------- #


def test_typography_round_trips_through_encode_decode_metadata() -> None:
    line = PageLine(
        text="A HEADING",
        y_order=0,
        bbox=BBox(72, 700, 300, 720),
        band="body",
        font="Times New Roman",
        size_class="heading",
        bold=True,
        italic=False,
    )
    meta = _metadata_for_text(
        "A HEADING",
        line=line,
        y_order=0,
        band_count=None,
        furniture=False,
        freeform_reason=None,
        converged=False,
    )
    assert meta.font == "Times New Roman" and meta.size_class == "heading"
    assert meta.bold is True and meta.italic is False
    attrs = encode_metadata(meta)
    assert attrs["typo.font"] == "Times New Roman"
    assert attrs["typo.size_class"] == "heading"
    assert attrs["typo.bold"] == "1"
    assert "typo.italic" not in attrs  # false flag not emitted
    assert decode_metadata(attrs) == meta


def test_absent_typography_stays_absent_in_the_encoded_attrs() -> None:
    # A line with no aligned span → no typo.* keys on the node (Level 2 optional).
    line = PageLine(text="body", y_order=0, bbox=BBox(72, 400, 500, 414), band="body")
    meta = _metadata_for_text(
        "body", line=line, y_order=0, band_count=None, furniture=False,
        freeform_reason=None, converged=False,
    )
    attrs = encode_metadata(meta)
    assert "typo.font" not in attrs and "typo.size_class" not in attrs
    assert "typo.bold" not in attrs and "typo.italic" not in attrs


# --------------------------------------------------------------------------- #
# Graceful degradation: producer without pdfplumber → typo.* absent + a note.  #
# --------------------------------------------------------------------------- #


def test_attach_typography_degrades_when_no_spans() -> None:
    # _attach_typography passes lines through untouched when the char lane yields
    # nothing (here: an empty pdf_bytes read → no spans).
    prod = PageElementProducer()
    lines = (PageLine(text="x", y_order=0, bbox=BBox(72, 400, 500, 414), band="body"),)
    out, notes = prod._attach_typography(b"not a pdf", 1, lines)
    assert out == lines  # unchanged (typo.* absent)
    assert out[0].font is None
    assert notes  # a typed note, never a crash


# --------------------------------------------------------------------------- #
# Live 1-page real-PDF extraction (skips when the pdf extra is absent).        #
# --------------------------------------------------------------------------- #


def _minimal_pdf(basefont: str, size: int, text: str) -> bytes:
    """A hand-rolled 1-page PDF (pdfminer rebuilds the xref from ``startxref 0``)."""
    content = f"BT /F1 {size} Tf 20 100 Td ({text}) Tj ET".encode("latin-1")
    obj4 = (
        b"4 0 obj<</Length "
        + str(len(content)).encode("ascii")
        + b">>stream\n"
        + content
        + b"\nendstream endobj\n"
    )
    obj5 = (
        b"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/"
        + basefont.encode("latin-1")
        + b">>endobj\n"
    )
    return (
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]"
        b"/Resources<</Font<</F1 5 0 R>>>>/Contents 4 0 R>>endobj\n"
        + obj4
        + obj5
        + b"xref\n0 6\ntrailer<</Root 1 0 R/Size 6>>\nstartxref\n0\n%%EOF"
    )


def test_live_pdfplumber_extracts_real_font_and_bold() -> None:
    pytest.importorskip("pdfplumber")
    pytest.importorskip("pypdfium2")
    pdf = _minimal_pdf("Helvetica-Bold", 12, "Hello Bold")
    prod = PageElementProducer()
    spans, note = prod._typography_spans(pdf, 1)
    assert note is None
    assert spans, "expected at least one typography span from a real PDF"
    sp = spans[0]
    assert sp.font == "Helvetica" and sp.bold is True
    assert sp.size == pytest.approx(12.0, abs=0.5)
    # And the whole page_elements read attaches it to the aligned PageLine.
    pe = prod.page_elements(pdf, 1)
    fonts = {pl.font for pl in pe.page_lines if pl.font is not None}
    assert "Helvetica" in fonts or not pe.page_lines  # aligned (or geometry absent)

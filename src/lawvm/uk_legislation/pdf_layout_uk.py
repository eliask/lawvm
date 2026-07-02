"""pdf_layout_uk.py — C19 King's-Printer marginal-note x-coordinate segmentation.

Background
----------
Pre-1988-ish UK Acts (C19 / early C20) render a section's **marginal note**
(the "side-note": ``Definition of partnership``, ``Meaning of firm``, …) as a
small-font block in the **right-hand margin**, vertically aligned with the
section it heads.  ``pdftotext`` (and a naive line-join over pdfplumber lines)
interleaves those side-notes into the body's first physical line — the section
opener then reads ``"1. Partnership is the relation ... Definition of
partnership"`` with the side-note bled into the body.

The modern CLML XML for the *same* Act (where it exists) carries that side-note
as a ``P1group/Title`` which the XML loader lifts onto the section as an explicit
``heading`` child (``uk_grafter._attach_p1group_title_to_sole_section``).  So the
correct PDF behaviour is the same: detect the marginal-note column *by geometry*
and attach each note to its section **as the heading**, not as body text.

Approach (x-coordinate banding, geometry — not text heuristics)
--------------------------------------------------------------
The geometric layer is jurisdiction-neutral pdfplumber word extraction (the same
optional dependency Finland's ``pdf_layout`` imports dynamically).  Per document:

1. **Body font size** = the modal word height across substantive pages.
2. **Marginal band** = words whose ``x0`` sits in the right ``_MARGIN_BAND_FRAC``
   of the used page width AND whose height is at least ``_MARGIN_FONT_DROP``
   points below the body font.  The King's-Printer side-note column is both
   *right-set* and *set in a smaller face*; either signal alone is noisy (short
   body lines end in the right band; the enacting-formula small-caps are small),
   but their **conjunction** is the side-note column.
3. Marginal words are **removed from the body stream** (so the body line-join no
   longer bleeds them) and separately **clustered into notes by vertical run**
   (consecutive ``top`` within ``_MARGIN_LINE_GAP``), each note carrying its
   ``(page, y_top)`` anchor.
4. The grammar (``pdf_grammar``) then attaches each note to the section whose
   opener is vertically nearest at/above the note's anchor.

Where a document shows no marginal column (modern single-column PDFs, or a scan
whose OCR destroyed the column geometry) the band is empty and the body stream is
returned unchanged — this pass is a no-op, never a lossy transform.

Typed lossiness
---------------
When the band detector fires but a note cannot be bound to any section (no
section opener above it on the page), the grammar preserves the note as an
observation rather than silently dropping it — mirroring the corpus discipline
that a genuine PDF limitation is a *typed observation*, never a silent drop.
"""

from __future__ import annotations

import importlib
import io
from collections import Counter
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Detection constants (documented, not magic — validated on real C19 acts)
# ---------------------------------------------------------------------------

# A marginal-note word must start in the right ``_MARGIN_BAND_FRAC`` of the used
# width. On the validated acts the side-note column begins at x0 ~0.78·max_x1
# (1882/61: body max_x1 ~454, side-notes at x0 400+; 1890/39: max_x1 ~402,
# side-notes at x0 313+). 0.78 keeps body words (which reach the band only as a
# line's final token, i.e. never as a word *start* in a filled line) out.
_MARGIN_BAND_FRAC = 0.78

# A side-note is set in a smaller face than body. The drop is at least this many
# points below the modal body height. 1.0 pt cleanly separates the 8.5pt notes
# from 11pt body (1882) and 7pt notes from 9pt body (1890).
_MARGIN_FONT_DROP = 1.0

# Two marginal words belong to the same note if their line tops are within this
# many points (a note spans 1–4 tightly-set lines). A larger vertical gap starts
# a new note (i.e. the next section's side-note).
_MARGIN_LINE_GAP = 14.0

# Body lines are assembled by grouping words whose ``top`` is within this many
# points (one text line). Mirrors Finland's ``round(y0)`` line bucketing.
_BODY_LINE_TOL = 2.5

# A document must have at least this many detected marginal words before the
# band is trusted (guards against 1–2 stray small right-set tokens being read as
# a column on a modern single-column PDF).
_MIN_MARGIN_WORDS = 4

# A clustered note must carry at least this many alphabetic characters to be a
# real side-note (drops bare punctuation — ``;`` ``:`` ``’`` — and single stray
# letters that a filled body line pushed into the right band).
_MIN_NOTE_ALPHA = 3


@dataclass(frozen=True, slots=True)
class MarginalNote:
    """A section side-note recovered from the right-hand margin column."""

    text: str
    page_num: int
    y_top: float


@dataclass(frozen=True, slots=True)
class BodyLine:
    """A body text line with its ``(page, y_top)`` anchor.

    The anchor lets the grammar bind a marginal note to the section opener that
    is vertically nearest at/above the note (side-notes sit level with the
    section they head).
    """

    text: str
    page_num: int
    y_top: float


@dataclass(frozen=True, slots=True)
class UKPdfLayout:
    """Segmented UK PDF layout: body lines (side-notes removed) + marginal notes.

    ``body_lines`` is the ordered, marginal-free body text (ready for the UK
    grammar as a plain string sequence). ``positioned_body_lines`` carries the
    same lines with ``(page, y_top)`` anchors, and ``marginal_notes`` carry the
    same anchors, so the grammar can attach each note to its vertically-nearest
    section as a heading.
    """

    body_lines: Tuple[str, ...] = ()
    positioned_body_lines: Tuple[BodyLine, ...] = ()
    marginal_notes: Tuple[MarginalNote, ...] = ()
    body_font_size: float = 0.0
    margin_x_threshold: float = 0.0
    page_count: int = 0
    detected: bool = False


# ---------------------------------------------------------------------------
# Word-level geometry
# ---------------------------------------------------------------------------


def _modal_body_height(words: List[Dict[str, Any]]) -> float:
    """Modal (most common) word height rounded to the nearest point.

    The body face dominates the page, so its height is the mode. Side-notes,
    small-caps furniture, and running heads are minorities.
    """
    heights: Counter = Counter(round(w["height"]) for w in words if w.get("height"))
    if not heights:
        return 0.0
    return float(heights.most_common(1)[0][0])


def _cluster_marginal_notes(
    marginal_words: List[Dict[str, Any]],
) -> List[MarginalNote]:
    """Cluster right-margin words into per-section notes by vertical run.

    Words are grouped page-by-page; within a page, a vertical gap larger than
    ``_MARGIN_LINE_GAP`` starts a new note. Within a note, words are ordered by
    (top, x0) and joined — recovering "Meaning of firm" from its 3 stacked lines.
    """
    if not marginal_words:
        return []
    ordered = sorted(marginal_words, key=lambda w: (w["_page"], w["top"], w["x0"]))
    notes: List[MarginalNote] = []
    run: List[Dict[str, Any]] = []

    def _flush() -> None:
        if not run:
            return
        # Assemble the note text: order by line (top), then x0 within a line.
        by_line: Dict[float, List[Dict[str, Any]]] = {}
        for w in run:
            key = round(w["top"] / _BODY_LINE_TOL) * _BODY_LINE_TOL
            by_line.setdefault(key, []).append(w)
        parts: List[str] = []
        for key in sorted(by_line):
            line_words = sorted(by_line[key], key=lambda w: w["x0"])
            parts.append(" ".join(w["text"] for w in line_words))
        text = " ".join(parts).strip()
        if sum(c.isalpha() for c in text) >= _MIN_NOTE_ALPHA:
            notes.append(
                MarginalNote(
                    text=text,
                    page_num=run[0]["_page"],
                    y_top=min(w["top"] for w in run),
                )
            )

    for w in ordered:
        if run and (
            w["_page"] != run[-1]["_page"]
            or w["top"] - run[-1]["top"] > _MARGIN_LINE_GAP
        ):
            _flush()
            run = []
        run.append(w)
    _flush()
    return notes


def _assemble_body_lines(body_words: List[Dict[str, Any]]) -> List[BodyLine]:
    """Join non-marginal words back into ordered, y-anchored body text lines.

    Groups by (page, rounded top), orders words left-to-right within a line, and
    emits lines in reading order — the same shape ``pdf_grammar`` already
    consumes, but with the side-note column excised and each line carrying its
    ``(page, y_top)`` anchor for note binding.
    """
    if not body_words:
        return []
    buckets: Dict[Tuple[int, float], List[Dict[str, Any]]] = {}
    for w in body_words:
        key = (w["_page"], round(w["top"] / _BODY_LINE_TOL) * _BODY_LINE_TOL)
        buckets.setdefault(key, []).append(w)
    lines: List[BodyLine] = []
    for key in sorted(buckets):
        line_words = sorted(buckets[key], key=lambda w: w["x0"])
        text = " ".join(w["text"] for w in line_words).strip()
        if text:
            lines.append(
                BodyLine(
                    text=text,
                    page_num=key[0],
                    y_top=min(w["top"] for w in line_words),
                )
            )
    return lines


def segment_uk_pdf_layout(
    pdf_bytes: bytes,
    *,
    skip_leading_pages: int = 2,
    max_pages: int = 5000,
) -> Optional[UKPdfLayout]:
    """Segment a C19 UK PDF into marginal-free body lines + positioned side-notes.

    Returns ``None`` if pdfplumber is unavailable or the PDF cannot be opened
    (the caller then falls back to the plain ``pdftotext`` grammar path).

    ``skip_leading_pages`` skips the cover / long-title furniture pages when
    computing the body-font mode and the marginal band (they carry large-face
    titles that would skew the mode). Their body text is still emitted.
    """
    try:
        pdfplumber = importlib.import_module("pdfplumber")
    except ModuleNotFoundError:
        return None

    try:
        pdf = pdfplumber.open(io.BytesIO(pdf_bytes))
    except Exception:  # lawvm-failloud: optional layout extraction degrades to text path
        return None

    all_words: List[Dict[str, Any]] = []
    substantive_words: List[Dict[str, Any]] = []
    try:
        page_count = len(pdf.pages)
        for page_idx, page in enumerate(pdf.pages[:max_pages]):
            try:
                words = page.extract_words()
            except Exception:  # lawvm-failloud: per-page word extraction may fail on malformed pages
                continue
            for w in words:
                w["_page"] = page_idx
                all_words.append(w)
                if page_idx >= skip_leading_pages:
                    substantive_words.append(w)
    finally:
        pdf.close()

    if not all_words:
        return None

    sample = substantive_words or all_words
    body_h = _modal_body_height(sample)
    max_x1 = max((w["x1"] for w in sample), default=0.0)
    x_threshold = max_x1 * _MARGIN_BAND_FRAC

    # A word is marginal iff it is BOTH right-set (x0 past the band threshold)
    # AND set smaller than body. The conjunction is what identifies the column.
    # Leading cover / long-title pages carry no sections, so their large-face
    # furniture must never be read as a side-note column — restrict marginal
    # detection to substantive pages (their body text is still emitted below).
    marginal_words = [
        w
        for w in all_words
        if body_h > 0.0
        and w["_page"] >= skip_leading_pages
        and w["x0"] > x_threshold
        and w.get("height", body_h) <= body_h - _MARGIN_FONT_DROP
    ]

    detected = len(marginal_words) >= _MIN_MARGIN_WORDS
    if not detected:
        # No trustworthy marginal column — return the body unchanged (no-op).
        body_lines = _assemble_body_lines(all_words)
        return UKPdfLayout(
            body_lines=tuple(b.text for b in body_lines),
            positioned_body_lines=tuple(body_lines),
            marginal_notes=(),
            body_font_size=body_h,
            margin_x_threshold=x_threshold,
            page_count=page_count,
            detected=False,
        )

    marginal_ids = {id(w) for w in marginal_words}
    body_words = [w for w in all_words if id(w) not in marginal_ids]

    body_lines = _assemble_body_lines(body_words)
    marginal_notes = _cluster_marginal_notes(marginal_words)

    return UKPdfLayout(
        body_lines=tuple(b.text for b in body_lines),
        positioned_body_lines=tuple(body_lines),
        marginal_notes=tuple(marginal_notes),
        body_font_size=body_h,
        margin_x_threshold=x_threshold,
        page_count=page_count,
        detected=True,
    )

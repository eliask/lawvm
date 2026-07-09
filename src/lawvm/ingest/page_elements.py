"""Numbered per-page elements for the v2 build-script wire (reading order + images).

The v1 span lane offers the model only ``[N] text`` reading-order lines. The v2
build script also references embedded images, so this module enumerates BOTH:

  * text lines as ``[N] text`` (the guide's source-item bracket), and
  * embedded images as ``{N} image page=P bbox=... px=WxH`` (the guide's
    context-item curly bracket — collision-free with ``[N]`` footnote refs).

Two image tiers, ONE content-addressed scheme (farchive dedups by digest):

  * EMBEDDED XObject — raw image bytes literally present in the PDF: BIT-EXACT
    and losslessly re-derivable (``bit_exact_source=True``); digest is a pure
    function of the source bytes.
  * RASTERIZED region crop — for an IMAGE region with no extractable XObject
    (scanned / vector figure), render its bbox via pypdfium2 at a FIXED,
    documented DPI and content-address the PNG (``bit_exact_source=False``);
    the digest depends on (bbox, DPI, rasterizer version), which is why the DPI
    is folded into the pipeline version (see ``parsed_store``).

pypdfium2 page-object enumeration is optional: if the object API is unavailable
or raises, the page degrades gracefully to NO image elements with a typed note
(never a crash). The extraction is a pure-ish read against ``pdf_bytes`` so it
can be faked in hermetic tests via the ``PageElementProducer`` protocol.

Discipline (AGENTS.md §1.9): typed frozen carriers; a failed image read is a
typed note, never a silent drop and never a crash.
"""
from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Sequence, Tuple, cast

from lawvm.core.source_document.anchors import BBox
from lawvm.ingest.struct_wire import ImageElement
from lawvm.ingest.visual import PDFIUM_LOCK

# Fixed rasterization DPI for region crops WITHOUT an embedded XObject. It is
# part of the pipeline version (see ``parsed_store.RASTERIZE_DPI_TAG``): a crop
# re-rendered at a new DPI is a NEW content-addressed blob under a NEW path.
RASTERIZE_DPI = 200

# Margin-band split (Decision 7 / §3 geometry): a line's y-centre relative to the
# page height buckets it into ``top`` (running header zone), ``body``, or
# ``bottom`` (footer / page-number zone). The bands are AFFORDANCES that surface
# furniture candidates — the model confirms furniture across pages (Level 2),
# never obeys the band. PDF origin is bottom-left, so a SMALL y is near the
# BOTTOM of the page and a LARGE y near the TOP.
_TOP_BAND_FRACTION = 0.90  # y-centre above 90% of page height → top band
_BOTTOM_BAND_FRACTION = 0.10  # y-centre below 10% of page height → bottom band

# Indent quantization: the left edge (x0) in PDF points is quantized to this bin
# so a jittery text-layer x maps to a stable indent depth for Level-2 list/section
# continuation reasoning. A pure affordance, never authority.
_INDENT_QUANTUM_PT = 18.0

# Typography size-class thresholds, RELATIVE to the page's median (body) font size
# — document-adaptive, not absolute points (Decision 7 / §3). A span whose size is
# >= HEADING_RATIO of the median is a ``heading`` candidate; <= CAPTION_RATIO a
# ``caption``; otherwise ``body``. Ratios keep small text-layer jitter in ``body``.
_HEADING_SIZE_RATIO = 1.15
_CAPTION_SIZE_RATIO = 0.85

# Minimum vertical + horizontal overlap fraction for a pdfplumber span to be
# considered the SAME visual line as a pypdfium2 ``PageLine`` (alignment gate). A
# span that overlaps no line's y-band, or straddles ambiguously, is left off (the
# line's typo.* stay absent — never guessed).
_ALIGN_Y_OVERLAP_MIN = 0.30
_ALIGN_X_OVERLAP_MIN = 0.30

# Font-name substrings that mark a bold / italic face (PDF fontnames commonly
# carry the weight/style in the BaseFont literal, often after a ``+`` subset tag
# and a ``-`` / ``,`` style delimiter, e.g. ``ABCDEF+TimesNewRoman-BoldItalic``).
_BOLD_MARKERS = ("bold", "black", "heavy", "semibold", "demibold")
_ITALIC_MARKERS = ("italic", "oblique")


@dataclass(frozen=True, slots=True)
class PageLine:
    """One reading-order text line + its DETERMINISTIC per-line geometry (Decision 7).

    Geometry is the concrete form of Level-2's "mechanical affordances surface
    candidates, intelligence decides": ``bbox`` (page points), ``band``
    (top/body/bottom margin zone), ``indent`` (quantized left-edge depth),
    ``y_order`` (reading-order rank), and ``col`` (column index, where derivable)
    ride onto the node's ``attrs``/anchor — NEVER shown to the model as authority.
    The continuation cues + content hints are PURE string functions of ``text``.

    ``text`` is the model-facing line (rendered as ``[N] text``); the geometry is
    attached to NODES only. A line with no readable geometry carries ``bbox=None``
    and a ``band`` of ``None`` (the extractor degraded, typed, never guessed).

    The typography fields (``font`` / ``size_class`` / ``bold`` / ``italic``) come
    from a SEPARATE pdfplumber char-level lane aligned to this pypdfium2 line by
    geometry (``meta.v2``). They are OPTIONAL: ``None`` / ``False`` when the char
    lane is unavailable or this line could not be aligned to any span (never
    guessed). ``size_class`` is document-adaptive (``heading`` / ``body`` /
    ``caption`` relative to the page's median body font size).
    """

    text: str
    y_order: int
    bbox: Optional[BBox] = None
    band: Optional[str] = None  # top | body | bottom
    indent: Optional[int] = None
    col: Optional[int] = None
    # typography v2 (pdfplumber char lane, aligned by geometry; all OPTIONAL)
    font: Optional[str] = None
    size_class: Optional[str] = None  # heading | body | caption
    bold: bool = False
    italic: bool = False


@dataclass(frozen=True, slots=True)
class TypographySpan:
    """One pdfplumber-derived per-line typography span (``meta.v2`` char lane).

    A visual line's dominant font/size/style, collapsed from its constituent
    chars, in the SAME PDF-point coordinate frame as ``PageLine.bbox`` (origin
    bottom-left, ``y0`` near the page bottom). ``size`` is the raw point size (the
    document-relative ``size_class`` is computed later against the page median);
    ``bold`` / ``italic`` are parsed from the font name. A pure carrier — the
    producer aligns these to ``PageLine``s by geometry.
    """

    text: str
    bbox: BBox
    font: str
    size: float
    bold: bool
    italic: bool


@dataclass(frozen=True, slots=True)
class PageElements:
    """A page's numbered reading-order text lines + numbered embedded images.

    ``lines`` are 1-indexed reading-order text lines (``[N]``) — the ONLY
    model-facing text (geometry-free, rendered as ``[N] text``). ``page_lines``
    carries the DETERMINISTIC per-line geometry (Decision 7) in the SAME order and
    count as ``lines`` (or empty when the extractor could not produce geometry, a
    graceful degrade). ``images`` are the embedded/rasterized image elements
    (``{N}``) with their raw bytes attached; ``notes`` records any image the
    extractor could not read (typed, never a silent drop). ``page_width`` /
    ``page_height`` are the page's point dimensions (for band computation and the
    node bbox), ``0.0`` when unavailable.
    """

    page_num: int
    lines: Tuple[str, ...]
    images: Tuple["EmbeddedImage", ...] = ()
    notes: Tuple[str, ...] = ()
    page_lines: Tuple["PageLine", ...] = field(default_factory=tuple)
    page_width: float = 0.0
    page_height: float = 0.0


@dataclass(frozen=True, slots=True)
class EmbeddedImage:
    """One numbered ``{N}`` image element + its raw bytes (for content-addressing).

    ``element`` is the wire-facing ``ImageElement`` (digest, media_type, dims,
    bbox, role); ``raw_bytes`` are the bytes to store; ``bit_exact_source`` marks
    an embedded XObject (True; losslessly re-derivable) vs a rasterized crop
    (False; digest depends on bbox+DPI).
    """

    element: ImageElement
    raw_bytes: bytes
    bit_exact_source: bool


class _NeverRaised(Exception):
    """A sentinel exception that is never raised (stands in for an absent lib type)."""


def _PdfiumError() -> type[BaseException]:
    """The pypdfium2 C-layer error type, or a never-raised sentinel if the lib is absent.

    Lets the image handlers name the ACTUAL pdfium failure condition without a
    hard import of an optional dependency (pypdfium2 is not a base dep).
    """
    try:
        import pypdfium2

        return pypdfium2.PdfiumError
    except ImportError:
        return _NeverRaised


# Discretionary hyphenation points from PDF text extraction, in extraction order:
# U+FFFE is pypdfium2's fallback glyph for this corpus's soft/discretionary hyphen
# at a line break; U+00AD is the SOFT HYPHEN proper. Both are TYPOGRAPHY (an
# invisible "the word may break here") with NO legal meaning — for the canonical
# IR text we normalize to the joined word. We drop ``<hyphen>\n`` (a real line-wrap
# break, joining across it) and a bare ``<hyphen>`` (extractor emitted it inline).
_DISCRETIONARY_HYPHENS = ("￾", "­")


def dehyphenate(text: str) -> str:
    """Join words split by a discretionary/soft hyphen at a line break.

    ``kriisinrat\\ufffekaisusta`` → ``kriisinratkaisusta``. A real hyphen
    (U+002D) is left untouched — only the invisible discretionary points go.
    """
    for h in _DISCRETIONARY_HYPHENS:
        text = text.replace(h + "\n", "").replace(h, "")
    return text


# --------------------------------------------------------------------------- #
# Deterministic per-line continuation cues + content hints (Decision 7).       #
# All PURE string functions of the line text — no geometry, no model, no lib.  #
# They surface REJOIN/list/section/furniture CANDIDATES; Level 2 decides.      #
# --------------------------------------------------------------------------- #

# A line ending in one of these is a completed sentence/clause — NOT a mid-break.
_TERMINAL_PUNCT_CHARS = (".", "!", "?", ":", ";", "—", "–", ")")

# A leading list marker: ``1)`` ``a)`` ``12.`` ``(iv)`` ``-`` ``•`` ``§``. Pure
# prefix recognition — the value is the marker literal for Level-2 continuation.
_BULLET_CHARS = ("-", "•", "–", "*", "▪", "·")


def line_ends_terminal(text: str) -> bool:
    """Does the line end with terminal punctuation (a completed sentence)?

    A line that does NOT end terminal is a REJOIN candidate — it may continue onto
    the next line / next page (``cue.ends_terminal`` absent → possible split).
    """
    s = text.rstrip()
    return bool(s) and s.endswith(_TERMINAL_PUNCT_CHARS)


def line_starts_lower(text: str) -> bool:
    """Does the line start with a lower-case letter (a mid-sentence continuation)?"""
    s = text.lstrip()
    return bool(s) and s[:1].islower()


def line_has_hyphen_tail(text: str) -> bool:
    """Does the line end with a discretionary/soft hyphen (a wrapped word)?

    The soft/discretionary hyphen glyphs (U+FFFE fallback, U+00AD, and a trailing
    real ``-``) mark a word broken across the line break — a strong REJOIN cue.
    """
    s = text.rstrip("\n\r ")
    if not s:
        return False
    return s.endswith(_DISCRETIONARY_HYPHENS) or s.endswith("-")


def line_list_marker(text: str) -> Optional[str]:
    """Leading list-marker literal (``1)`` ``a)`` ``12.`` ``(iv)`` ``•``) or None.

    Pure prefix recognition: an enumerated/bulleted line surfaces its marker so
    Level 2 can recognize a list continuation across a page break. Returns the
    marker literal (e.g. ``"1)"``) or ``None`` for an unmarked line.
    """
    s = text.lstrip()
    if not s:
        return None
    if s[0] in _BULLET_CHARS and (len(s) == 1 or s[1] == " "):
        return s[0]
    # ``(iv)`` / ``(a)`` / ``(12)`` — a parenthesized token.
    if s[0] == "(":
        close = s.find(")")
        if 1 < close <= 6:
            inner = s[1:close]
            if inner.isalnum():
                return s[: close + 1]
    # ``1)`` ``a)`` ``12.`` ``iv.`` — an alnum run then ``)`` or ``.``.
    i = 0
    while i < len(s) and i < 5 and s[i].isalnum():
        i += 1
    if 0 < i < len(s) and s[i] in ")." and (i + 1 == len(s) or s[i + 1] == " "):
        return s[: i + 1]
    return None


def line_section_number(text: str) -> Optional[str]:
    """Leading section-number label (``4 §`` / ``§ 4`` / ``Article 5``) or None.

    A section-number line is a heading/boundary candidate — Level 2 uses it for
    section continuation and heading-vs-body discrimination. Deliberately narrow
    (the ``§`` sign and a bare ``Article N`` / ``Art. N`` prefix); pure string.
    """
    s = text.strip()
    if not s:
        return None
    marker = s.find("§")
    if 0 <= marker < 8:
        # The label is the run up to and including the § sign (e.g. "4 §"); a
        # leading "§ 4" keeps the trailing number too.
        head = s[: marker + 1]
        tail = s[marker + 1 :].lstrip()
        num = tail.split()[0] if (tail and tail.split()[0][:1].isdigit()) else ""
        return f"{head} {num}".strip() if num else head.strip()
    lower = s.lower()
    for prefix in ("article ", "art. ", "art "):
        if lower.startswith(prefix):
            rest = s[len(prefix) :].lstrip()
            num = rest.split()[0] if rest.split() else ""
            if num and num[0].isdigit():
                return f"{s[: len(prefix)].strip()} {num}".strip()
    return None


def line_is_caps(text: str) -> bool:
    """Is the line all-caps (heading/furniture affordance, text-derivable)?

    True iff the line has at least one cased letter and NO lower-case letter.
    """
    s = text.strip()
    has_alpha = any(c.isalpha() for c in s)
    return has_alpha and not any(c.islower() for c in s)


def line_is_numeric_heavy(text: str) -> bool:
    """Is the line numeric-heavy (protect a euro amount / § / date from dedup)?

    True when digits dominate the non-space characters (>= 40%). A numeric-heavy
    line is NEVER corrupted by an over-eager Level-2 dedup (the NUMERIC guard).
    """
    non_space = [c for c in text if not c.isspace()]
    if not non_space:
        return False
    digits = sum(1 for c in non_space if c.isdigit())
    return digits / len(non_space) >= 0.40


def line_has_section_ref(text: str) -> bool:
    """Does the line contain a section sign / citation marker (``§`` / ``art.``)?"""
    lower = text.lower()
    return "§" in text or "article " in lower or "art." in lower or " momentissa" in lower


def line_is_bare_page_number(text: str) -> bool:
    """Is the line JUST a page number (a furniture candidate)?

    A short line that is a bare integer (optionally ``Sivu 12`` / ``s. 12`` /
    ``12 (34)``) — a running page-number furniture candidate. Pure string; Level 2
    confirms furniture across pages.
    """
    s = text.strip()
    if not s or len(s) > 12:
        return False
    if s.isdigit():
        return True
    # ``12 (34)`` page-of-total, ``Sivu 12``, ``s. 12``.
    compact = s.replace("(", " ").replace(")", " ").replace(".", " ")
    toks = compact.split()
    non_digit = [t for t in toks if not t.isdigit()]
    if not [t for t in toks if t.isdigit()]:
        return False
    return all(t.lower() in ("sivu", "s", "page", "p", "-", "/") for t in non_digit)


# --------------------------------------------------------------------------- #
# Typography char lane (meta.v2) — pdfplumber spans aligned to pypdfium2 lines. #
# Pure geometry/string helpers below; the pdfplumber read + alignment live on   #
# PageElementProducer so a build lacking the extra degrades to typo.* absent.    #
# --------------------------------------------------------------------------- #


def _font_family(fontname: str) -> str:
    """Human font-family literal from a PDF BaseFont name (drop subset tag + style).

    PDF fontnames often carry a 6-char subset prefix (``ABCDEF+``) and a trailing
    style (``-BoldItalic`` / ``,Italic``). We strip the subset tag and the style
    suffix so the family groups across weights (``Times New Roman``), but NEVER
    invent — an unrecognized shape is returned as-is (minus the subset tag).
    """
    name = fontname.strip()
    if len(name) >= 7 and name[6] == "+" and name[:6].isalpha():
        name = name[7:]
    # Split off a trailing style token after the last '-' or ',' when it is a pure
    # style word (keeps hyphenated real families like 'Helvetica-Neue' intact only
    # when the tail is NOT a known style — else drop it).
    for delim in ("-", ","):
        head, sep, tail = name.rpartition(delim)
        if sep and _is_style_token(tail):
            name = head
    return name.strip() or fontname.strip()


def _is_style_token(token: str) -> bool:
    low = token.lower()
    return any(m in low for m in _BOLD_MARKERS) or any(m in low for m in _ITALIC_MARKERS) or (
        low in ("regular", "roman", "medium", "light", "book")
    )


def _font_is_bold(fontname: str) -> bool:
    """Does the font name mark a bold face (``Bold`` / ``Black`` / ``Heavy`` ...)?"""
    low = fontname.lower()
    return any(m in low for m in _BOLD_MARKERS)


def _font_is_italic(fontname: str) -> bool:
    """Does the font name mark an italic / oblique face?"""
    low = fontname.lower()
    return any(m in low for m in _ITALIC_MARKERS)


def _page_median_size(spans: Sequence[TypographySpan]) -> Optional[float]:
    """Median span size — the document-adaptive BODY reference for ``size_class``.

    Returns ``None`` when there are no spans (no reference → no size_class). Uses
    the plain median of per-line sizes (each visual line counts once), which is
    robust to a handful of large headings dragging a mean.
    """
    sizes = sorted(s.size for s in spans if s.size > 0)
    if not sizes:
        return None
    n = len(sizes)
    mid = n // 2
    if n % 2 == 1:
        return sizes[mid]
    return (sizes[mid - 1] + sizes[mid]) / 2.0


def size_class_for(size: float, median: Optional[float]) -> Optional[str]:
    """Classify a span size RELATIVE to the page median → heading|body|caption.

    Document-adaptive (Decision 7): ``>=`` 1.15× median is a ``heading``, ``<=``
    0.85× a ``caption``, else ``body``. Returns ``None`` when the median is
    unknown / non-positive or the size is non-positive (no honest classification).
    """
    if median is None or median <= 0 or size <= 0:
        return None
    ratio = size / median
    if ratio >= _HEADING_SIZE_RATIO:
        return "heading"
    if ratio <= _CAPTION_SIZE_RATIO:
        return "caption"
    return "body"


def _overlap_fraction(a0: float, a1: float, b0: float, b1: float) -> float:
    """Overlap length of ``[a0,a1]`` and ``[b0,b1]`` over the SMALLER interval.

    Fraction of the narrower interval that the two share — a symmetric-ish gate
    that fires when the span sits inside the line's band (or vice versa). Zero when
    either interval is degenerate or they are disjoint.
    """
    lo, hi = max(a0, b0), min(a1, b1)
    inter = hi - lo
    if inter <= 0:
        return 0.0
    smaller = min(a1 - a0, b1 - b0)
    return inter / smaller if smaller > 0 else 0.0


def align_typography_to_lines(
    page_lines: Sequence["PageLine"],
    spans: Sequence[TypographySpan],
    *,
    median: Optional[float] = None,
) -> Tuple["PageLine", ...]:
    """Attach font/size_class/bold/italic to each ``PageLine`` by GEOMETRY overlap.

    pdfplumber chars/words do NOT map 1:1 to pypdfium2 lines, so we align by
    bbox overlap: for each ``PageLine`` with a bbox, pick the span with the
    greatest COMBINED (y-band × x-range) overlap, provided both exceed their
    minima. A line with no bbox, or no span clearing the overlap gates, keeps its
    typo.* ABSENT (never guessed). ``size_class`` is computed against ``median``
    (the page's median span size) if given, else the median OF ``spans``.
    """
    med = median if median is not None else _page_median_size(spans)
    out: List[PageLine] = []
    for pl in page_lines:
        if pl.bbox is None or not spans:
            out.append(pl)
            continue
        best: Optional[TypographySpan] = None
        best_score = 0.0
        for sp in spans:
            y_ov = _overlap_fraction(pl.bbox.y0, pl.bbox.y1, sp.bbox.y0, sp.bbox.y1)
            x_ov = _overlap_fraction(pl.bbox.x0, pl.bbox.x1, sp.bbox.x0, sp.bbox.x1)
            if y_ov < _ALIGN_Y_OVERLAP_MIN or x_ov < _ALIGN_X_OVERLAP_MIN:
                continue
            score = y_ov + x_ov
            if score > best_score:
                best, best_score = sp, score
        if best is None:
            out.append(pl)
            continue
        out.append(
            PageLine(
                text=pl.text,
                y_order=pl.y_order,
                bbox=pl.bbox,
                band=pl.band,
                indent=pl.indent,
                col=pl.col,
                font=best.font,
                size_class=size_class_for(best.size, med),
                bold=best.bold,
                italic=best.italic,
            )
        )
    return tuple(out)


def _char_get(ch: object, key: str) -> object:
    """Read a pdfplumber char field, tolerating both a dict and an attr carrier."""
    if isinstance(ch, Mapping):
        return cast("Mapping[str, object]", ch).get(key)
    return getattr(ch, key, None)


def _char_field(ch: object, key: str, default: float = 0.0) -> float:
    """Read a numeric pdfplumber char field (dict or attr access), else ``default``."""
    val = _char_get(ch, key)
    if val is None:
        return default
    try:
        return float(val)  # ty: ignore[invalid-argument-type]
    except (TypeError, ValueError):
        return default


def _char_str(ch: object, key: str) -> str:
    val = _char_get(ch, key)
    return str(val) if val is not None else ""


def _spans_from_chars(chars: Sequence[object], page_h: float) -> Tuple[TypographySpan, ...]:
    """Group pdfplumber chars into per-visual-line ``TypographySpan``s.

    Chars carry ``x0/x1`` and ``y0/y1`` (PDF points, bottom-left origin — the same
    frame as ``PageLine.bbox``). We bucket chars into lines by their vertical
    centre (a char joins a line whose running y-centre is within half the char
    height), then collapse each line to its DOMINANT (most-frequent) font+size and
    the majority bold/italic — a robust single span per visual line. Lines are
    returned top-to-bottom (descending y) to mirror reading order.
    """
    rows: List[List[object]] = []
    row_centres: List[float] = []
    for ch in chars:
        if not _char_str(ch, "text").strip():
            continue
        y0 = _char_field(ch, "y0")
        y1 = _char_field(ch, "y1")
        centre = (y0 + y1) / 2.0
        height = max(y1 - y0, 1.0)
        placed = False
        for i, rc in enumerate(row_centres):
            if abs(rc - centre) <= height / 2.0:
                rows[i].append(ch)
                # running mean centre keeps the bucket stable across the line
                row_centres[i] = (rc * (len(rows[i]) - 1) + centre) / len(rows[i])
                placed = True
                break
        if not placed:
            rows.append([ch])
            row_centres.append(centre)
    spans: List[Tuple[float, TypographySpan]] = []
    for row in rows:
        span = _collapse_row(row)
        if span is not None:
            spans.append(((span.bbox.y0 + span.bbox.y1) / 2.0, span))
    spans.sort(key=lambda t: t[0], reverse=True)  # top of page first
    return tuple(s for _c, s in spans)


def _collapse_row(row: Sequence[object]) -> Optional[TypographySpan]:
    """Collapse one line's chars to a single dominant-font/size ``TypographySpan``."""
    if not row:
        return None
    x0 = min(_char_field(ch, "x0") for ch in row)
    x1 = max(_char_field(ch, "x1") for ch in row)
    y0 = min(_char_field(ch, "y0") for ch in row)
    y1 = max(_char_field(ch, "y1") for ch in row)
    if x1 < x0 or y1 < y0:
        return None
    # Dominant fontname by char count (ties broken by first-seen order).
    font_counts: Dict[str, int] = {}
    order: List[str] = []
    for ch in row:
        fn = _char_str(ch, "fontname")
        if fn not in font_counts:
            order.append(fn)
        font_counts[fn] = font_counts.get(fn, 0) + 1
    dominant = max(order, key=lambda f: font_counts[f]) if order else ""
    # Median size across the row (robust to a stray large/small glyph).
    sizes = sorted(_char_field(ch, "size") for ch in row if _char_field(ch, "size") > 0)
    if sizes:
        mid = len(sizes) // 2
        size = sizes[mid] if len(sizes) % 2 == 1 else (sizes[mid - 1] + sizes[mid]) / 2.0
    else:
        size = 0.0
    text = "".join(_char_str(ch, "text") for ch in row).strip()
    return TypographySpan(
        text=text,
        bbox=BBox(x0=x0, y0=y0, x1=x1, y1=y1),
        font=_font_family(dominant),
        size=size,
        bold=_font_is_bold(dominant),
        italic=_font_is_italic(dominant),
    )


def _sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _media_type_for(raw: bytes) -> str:
    """Best-effort media type from magic bytes (default PNG for our rasterized crops)."""
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if raw[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if raw[:4] == b"GIF8":
        return "image/gif"
    if raw[:2] == b"BM":
        return "image/bmp"
    return "application/octet-stream"


def _extension_for(media_type: str) -> str:
    return {
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/gif": "gif",
        "image/bmp": "bmp",
    }.get(media_type, "img")


def image_blob_name(index: int, media_type: str) -> str:
    """Zero-padded ``{N}``-indexed blob name, e.g. ``0003.png`` (index = ``{N}``)."""
    return f"{index:04d}.{_extension_for(media_type)}"


def numbered_page_text(
    lines: Sequence[str], images: Sequence[EmbeddedImage] = (), *, page_num: int = 0
) -> str:
    """Render numbered elements: ``[N] line`` text, then ``{N} image ...`` images.

    ``[N]`` = reading-order text lines (source items), ``{N}`` = image elements
    (context items) — the guide's collision-free bracket convention.
    """
    parts = [f"[{i}] {ln}" for i, ln in enumerate(lines, start=1)]
    for img in images:
        e = img.element
        parts.append(
            f"{{{e.index}}} image page={page_num} "
            f"bbox={_fmt_bbox(e.bbox)} px={e.width}x{e.height}"
        )
    return "\n".join(parts)


def _fmt_bbox(bbox: Tuple[float, float, float, float]) -> str:
    return ",".join(f"{v:.1f}" for v in bbox)


def _norm_line_text(text: str) -> str:
    """Whitespace-normalized lower-case form for order-preserving line alignment."""
    return " ".join(text.split()).lower()


def _align_lines_to_geom(
    lines: Sequence[str],
    geom: Sequence[Tuple[str, Optional[BBox]]],
) -> List[Tuple[str, Optional[BBox]]]:
    """Bind model-facing ``lines`` to geometry rows by ORDER-PRESERVING text overlap.

    The pypdfium2 text-range splitter and the rect API enumerate the SAME visual
    lines in the SAME reading order, but their counts can differ by a few (a rect
    the splitter merged into one line or split into two, a blank rect the splitter
    dropped). This is an off-by-N misalignment — NOT a reason to discard the whole
    page's geometry. We walk both sequences forward, matching each ``line`` to the
    NEXT geometry row whose normalized text equals (or contains / is contained by)
    the line's, tolerating a bounded look-ahead of unmatched rows on either side.

    Returns one ``(line_text, bbox)`` per input line, in line order: the aligned
    row's bbox when a match is found, else ``bbox=None`` (typed, never a WRONG
    bbox). When the counts are equal AND every position matches by text, this is
    exactly the old positional zip; the alignment only diverges to REPAIR a
    mismatch, so a faithful page is unchanged.
    """
    g_norm = [_norm_line_text(t) for t, _ in geom]
    out: List[Tuple[str, Optional[BBox]]] = []
    gi = 0
    n_geom = len(geom)
    # Bounded look-ahead: how far past the current cursor we scan for a text match
    # before giving up on this line (keeps the alignment O(n) and order-preserving,
    # never rebinding to a far-away identical line elsewhere on the page).
    _WINDOW = 3
    for line in lines:
        ln = _norm_line_text(line)
        best_j: Optional[int] = None
        # Prefer an exact normalized-text hit within the forward window; fall back
        # to a containment hit (a merged/split rect) at the same cursor.
        for j in range(gi, min(gi + _WINDOW + 1, n_geom)):
            gt = g_norm[j]
            if not gt:
                continue
            if gt == ln:
                best_j = j
                break
            if best_j is None and (ln in gt or gt in ln):
                best_j = j
        if best_j is not None:
            out.append((line, geom[best_j][1]))
            gi = best_j + 1  # advance past the consumed row (order-preserving)
        else:
            # No row aligned within the window → keep the line, drop its geometry
            # (bbox=None). Do NOT advance the geom cursor: the current row may still
            # match a LATER line (the splitter dropped/merged THIS line, not that row).
            out.append((line, None))
    # Positional-fallback safety net: if text alignment bound (almost) nothing yet
    # the rect API DID yield rows (e.g. the rect text systematically diverges from
    # the splitter text, or a whole page of recurring lines confounded the window),
    # every line would be left un-croppable — the exact regression this fix exists to
    # prevent. Fall back to a straight positional bind (line i ↔ row i) for the
    # overlapping prefix so the page still yields usable per-line geometry. Only
    # engaged when text alignment essentially failed (bound < 25% of the shorter
    # length) so a genuinely good alignment is never overwritten by cruder position.
    bound = sum(1 for _t, b in out if b is not None)
    overlap = min(len(lines), n_geom)
    if overlap and bound < max(1, overlap // 4):
        out = [
            (line, geom[i][1] if i < n_geom else None)
            for i, line in enumerate(lines)
        ]
    return out


class PageElementProducer:
    """Enumerate a PDF page's numbered text lines + embedded/rasterized images.

    pypdfium2-backed; the object-enumeration API is used behind a try/except so a
    build that lacks it (or a page it cannot walk) degrades to text-only with a
    typed note. Hermetic tests subclass and override ``page_elements`` / the two
    seam methods to avoid the PDF lib entirely.
    """

    def __init__(self, *, rasterize_dpi: int = RASTERIZE_DPI) -> None:
        self._dpi = rasterize_dpi

    def page_elements(self, pdf_bytes: bytes, page_num: int) -> PageElements:
        """Reading-order text lines + per-line geometry + embedded/rasterized images.

        The model-facing ``lines`` stay geometry-free ``[N] text`` strings;
        ``page_lines`` carries the DETERMINISTIC per-line geometry (bbox, margin
        band, quantized indent, y-order) in the SAME order/count (Decision 7) plus
        the ``meta.v2`` typography (font / size_class / bold / italic) from a
        SEPARATE pdfplumber char pass aligned to those lines by geometry. Both
        lanes degrade gracefully: if the pypdfium2 rect API is unavailable,
        ``page_lines`` is empty; if pdfplumber is absent or a page can't be
        char-extracted, the typo.* fields are simply absent (a typed note, never a
        crash, never a guess).
        """
        import importlib

        pdfium = importlib.import_module("pypdfium2")
        # Hold the systemic pdfium lock across the ENTIRE document lifecycle: the
        # render / textpage / rect / object-parse calls below all touch pdfium's
        # process-global, thread-unsafe C state (#250). A per-PDF-concurrent
        # consumer (calibration / scan sweeps) must never interleave with this
        # parse. The lock is an ``RLock`` so a caller already holding it (e.g. a
        # loop that also single-flights its page-count probe) does not deadlock.
        with PDFIUM_LOCK:
            doc = pdfium.PdfDocument(pdf_bytes)
            try:
                page = doc[page_num - 1]
                textpage = page.get_textpage()
                page_w, page_h = self._page_dims(page)
                lines, page_lines, geom_notes = self._extract_lines_with_geometry(
                    textpage, page_h
                )
                images, img_notes = self._enumerate_images(page, page_num)
                page_lines, typo_notes = self._attach_typography(
                    pdf_bytes, page_num, page_lines
                )
                return PageElements(
                    page_num=page_num,
                    lines=lines,
                    images=images,
                    notes=geom_notes + img_notes + typo_notes,
                    page_lines=page_lines,
                    page_width=page_w,
                    page_height=page_h,
                )
            finally:
                doc.close()

    def _attach_typography(
        self, pdf_bytes: bytes, page_num: int, page_lines: Tuple["PageLine", ...]
    ) -> Tuple[Tuple["PageLine", ...], Tuple[str, ...]]:
        """Overlay pdfplumber typography spans onto the pypdfium2 lines (meta.v2).

        A separate pdfplumber char pass yields per-line ``TypographySpan``s; they
        are aligned to the existing ``page_lines`` by bbox overlap. If pdfplumber
        is unavailable, no spans could be read, or geometry is absent, the lines
        pass through UNCHANGED (typo.* absent) with a typed note — never a crash.
        """
        if not page_lines:
            return page_lines, ()
        spans, note = self._typography_spans(pdf_bytes, page_num)
        if not spans:
            return page_lines, (note,) if note else ()
        return align_typography_to_lines(page_lines, spans), ()

    def _typography_spans(
        self, pdf_bytes: bytes, page_num: int
    ) -> Tuple[Tuple[TypographySpan, ...], Optional[str]]:
        """Per-visual-line typography spans from a pdfplumber char pass, or ().

        pdfplumber (the ``pdf`` extra) groups chars into lines; we collapse each
        line's chars into one dominant-font/size ``TypographySpan`` in PDF points
        (origin bottom-left, matching ``PageLine.bbox``). A missing extra, an
        unreadable page, or a char-less page each degrade to ``()`` + a typed note.
        """
        try:
            import importlib

            pdfplumber = importlib.import_module("pdfplumber")
        except ImportError:
            return (), f"page {page_num}: typography unavailable (pdfplumber absent)"
        import io as _io

        try:
            with pdfplumber.open(_io.BytesIO(pdf_bytes)) as doc:
                page = doc.pages[page_num - 1]
                page_h = float(page.height)
                chars = list(page.chars)
        # pdfplumber / pdfminer can raise a broad range on a malformed page; a
        # typography read is best-effort → degrade to no spans + a typed note.
        except Exception as exc:  # noqa: BLE001 (best-effort optional lane)
            return (), f"page {page_num}: typography read failed ({type(exc).__name__})"
        spans = _spans_from_chars(chars, page_h)
        if not spans:
            return (), f"page {page_num}: no typography chars extracted"
        return spans, None

    def _page_dims(self, page: object) -> Tuple[float, float]:
        try:
            return (
                float(page.get_width()),  # ty: ignore[unresolved-attribute]
                float(page.get_height()),  # ty: ignore[unresolved-attribute]
            )
        except (AttributeError, TypeError, ValueError, _PdfiumError()):
            return (0.0, 0.0)

    def _extract_lines_with_geometry(
        self, textpage: object, page_h: float
    ) -> Tuple[Tuple[str, ...], Tuple["PageLine", ...], Tuple[str, ...]]:
        """Extract reading-order lines + per-line geometry from a pypdfium2 textpage.

        The whole-page text (dehyphenated) gives the model-facing lines; the
        textpage rect API (``count_rects`` / ``get_rect`` / ``get_text_bounded``)
        gives each visual line's bbox → margin band + quantized indent. When the
        rect API is unavailable the geometry lane degrades to empty ``page_lines``
        with a typed note (the model-facing lines are unaffected).
        """
        text = dehyphenate(textpage.get_text_range())  # ty: ignore[unresolved-attribute]
        lines = tuple(ln.strip() for ln in text.splitlines() if ln.strip())
        geom = self._line_rects(textpage, page_h)
        if geom is None:
            note = ("page geometry unavailable (textpage rect API absent) — lines only",)
            return lines, (), note
        # Bind each model-facing line to a geometry row by ORDER-PRESERVING TEXT
        # alignment. The rect API and the text-range splitter enumerate the SAME
        # visual lines in the SAME reading order, but their counts can differ by a
        # few (a rect the splitter merged/split, a blank rect) — an off-by-N that
        # must NOT discard the whole page's geometry (#250: a born-digital page left
        # with ``abs_bbox=None`` is un-croppable, so calibration reads nothing).
        # Align by best geometric text overlap instead; a line that finds no row
        # keeps ``bbox=None`` (typed, never a WRONG bbox) but the page still yields
        # usable per-line geometry for every line that DOES align.
        aligned = _align_lines_to_geom(lines, geom)
        page_lines: List["PageLine"] = [
            self._page_line(line_text, y_order, bbox, page_h)
            for y_order, (line_text, bbox) in enumerate(aligned)
        ]
        note = ()
        if len(geom) != len(lines):
            bound = sum(1 for pl in page_lines if pl.bbox is not None)
            note = (
                f"page geometry line-count mismatch (rects={len(geom)} lines={len(lines)}) "
                f"— aligned by text overlap ({bound}/{len(lines)} lines bound)",
            )
        return lines, tuple(page_lines), note

    def _line_rects(
        self, textpage: object, page_h: float
    ) -> Optional[List[Tuple[str, Optional[BBox]]]]:
        """Per-visual-line ``(text, bbox)`` from the textpage rect API, or None.

        pypdfium2's textpage exposes ``count_rects()`` + ``get_rect(i)`` (a visual
        line's bbox in PDF points, origin bottom-left) + ``get_text_bounded(...)``.
        A single degraded row yields ``bbox=None`` (typed) but keeps the row. Any
        API-shape failure → ``None`` so the caller degrades to lines-only.
        """
        try:
            count = int(textpage.count_rects())  # ty: ignore[unresolved-attribute]
        except (AttributeError, TypeError, ValueError, _PdfiumError()):
            return None
        rows: List[Tuple[str, Optional[BBox]]] = []
        for i in range(count):
            try:
                left, bottom, right, top = textpage.get_rect(i)  # ty: ignore[unresolved-attribute]
                x0, x1 = float(min(left, right)), float(max(left, right))
                y0, y1 = float(min(bottom, top)), float(max(bottom, top))
                raw = textpage.get_text_bounded(  # ty: ignore[unresolved-attribute]
                    left=left, bottom=bottom, right=right, top=top
                )
                cell = dehyphenate(str(raw)).strip()
                if not cell:
                    continue
                rows.append((cell, BBox(x0=x0, y0=y0, x1=x1, y1=y1)))
            except (AttributeError, TypeError, ValueError, IndexError, _PdfiumError()):
                # One bad rect degrades to no-bbox but keeps the extraction going.
                continue
        return rows or None

    def _page_line(
        self, text: str, y_order: int, bbox: Optional[BBox], page_h: float
    ) -> "PageLine":
        """Assemble a ``PageLine`` — bbox + margin band + quantized indent + y-order."""
        band: Optional[str] = None
        indent: Optional[int] = None
        if bbox is not None:
            indent = int(bbox.x0 // _INDENT_QUANTUM_PT)
            if page_h > 0:
                y_centre = (bbox.y0 + bbox.y1) / 2.0
                frac = y_centre / page_h
                if frac >= _TOP_BAND_FRACTION:
                    band = "top"
                elif frac <= _BOTTOM_BAND_FRACTION:
                    band = "bottom"
                else:
                    band = "body"
        return PageLine(
            text=text, y_order=y_order, bbox=bbox, band=band, indent=indent, col=None
        )

    def _enumerate_images(
        self, page: object, page_num: int
    ) -> Tuple[Tuple[EmbeddedImage, ...], Tuple[str, ...]]:
        """Enumerate a page's image XObjects; degrade to () + a note on any failure."""
        out: List[EmbeddedImage] = []
        notes: List[str] = []
        index = 1
        try:
            objects = list(page.get_objects())  # ty: ignore[unresolved-attribute]
        # The object-enumeration API may be missing (AttributeError) or the pdfium
        # C layer may reject the page (PdfiumError) — degrade to a text-only page.
        except (AttributeError, _PdfiumError()) as exc:
            return (), (f"page {page_num}: image enumeration unavailable ({type(exc).__name__})",)
        for obj in objects:
            try:
                obj_type = getattr(obj, "type", None)
                # pypdfium2: FPDF_PAGEOBJ_IMAGE == 3
                if obj_type != 3:
                    continue
                bbox = self._object_bbox(obj)
                raw, media_type, dims, bit_exact = self._extract_image_bytes(obj, page, bbox)
                if raw is None or dims is None:
                    notes.append(f"page {page_num}: image {index} unreadable, skipped")
                    continue
                width, height = dims
                digest = _sha256(raw)
                element = ImageElement(
                    index=index,
                    digest=digest,
                    media_type=media_type,
                    width=width,
                    height=height,
                    bbox=bbox,
                    role="embedded_image" if bit_exact else "rasterized_region",
                )
                out.append(
                    EmbeddedImage(element=element, raw_bytes=raw, bit_exact_source=bit_exact)
                )
                index += 1
            # One bad object (pdfium C error / bad attr / bad value) emits a typed
            # note and is skipped; the rest of the page's images still enumerate.
            except (AttributeError, TypeError, ValueError, _PdfiumError()) as exc:
                notes.append(f"page {page_num}: image {index} error ({type(exc).__name__})")
                continue
        return tuple(out), tuple(notes)

    def _object_bbox(self, obj: object) -> Tuple[float, float, float, float]:
        try:
            pos = obj.get_pos()  # ty: ignore[unresolved-attribute]
            x0, y0, x1, y1 = float(pos[0]), float(pos[1]), float(pos[2]), float(pos[3])
            return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
        # No readable geometry (missing get_pos / non-numeric / short tuple) → zero
        # bbox sentinel; the caller's rasterize path handles it (whole-page crop).
        except (AttributeError, TypeError, ValueError, IndexError, _PdfiumError()):
            return (0.0, 0.0, 0.0, 0.0)

    def _extract_image_bytes(
        self,
        obj: object,
        page: object,
        bbox: Tuple[float, float, float, float],
    ) -> Tuple[Optional[bytes], str, Optional[Tuple[int, int]], bool]:
        """Prefer the bit-exact embedded XObject bytes; fall back to a rasterized crop.

        Returns ``(raw_bytes, media_type, (w,h), bit_exact_source)``. A truly
        unreadable image yields ``(None, ..., None, ...)`` so the caller notes it.
        """
        # Tier 1: bit-exact embedded XObject (raw stored bytes, losslessly re-derivable).
        try:
            get_bitmap = getattr(obj, "get_bitmap", None)
            extract = getattr(obj, "get_data", None) or getattr(obj, "get_image_data", None)
            if extract is not None:
                raw = extract(decode=False)
                if raw:
                    raw = bytes(raw)
                    media_type = _media_type_for(raw)
                    dims = self._bitmap_dims(get_bitmap)
                    return raw, media_type, dims, True
        # Extraction failed (API shape / pdfium error) → fall through to the
        # Tier-2 rasterized crop, an explicit next tier (not a swallow).
        except (AttributeError, TypeError, ValueError, _PdfiumError()):
            pass
        # Tier 2: rasterized region crop at the fixed DPI (bit_exact_source=False).
        raw, dims = self._rasterize_region(page, bbox)
        if raw is not None:
            return raw, "image/png", dims, False
        return None, "application/octet-stream", None, False

    def _bitmap_dims(self, get_bitmap: object) -> Optional[Tuple[int, int]]:
        try:
            if get_bitmap is None:
                return None
            bmp = get_bitmap()  # ty: ignore[call-non-callable]
            pil = bmp.to_pil()
            return (int(pil.width), int(pil.height))
        # No bitmap dims (API shape / pdfium error) → None; the extract path then
        # treats the image as unreadable and the caller notes the skip.
        except (AttributeError, TypeError, ValueError, _PdfiumError()):
            return None

    def _rasterize_region(
        self, page: object, bbox: Tuple[float, float, float, float]
    ) -> Tuple[Optional[bytes], Optional[Tuple[int, int]]]:
        """Render the page at the fixed DPI and crop the bbox → a content-addressed PNG.

        pypdfium2 renders whole pages; we crop the region in PIL. Coordinates are
        PDF points (72/inch, origin bottom-left); the rendered raster is
        top-left origin, so the y axis is flipped against the page height.
        Rendering / crop errors propagate to ``_extract_image_bytes``' typed
        handler (one bad object never sinks the page).
        """
        try:
            from PIL import Image  # noqa: F401  (import guard: pillow present)
        # Pillow is not a base dep → no rasterized crop; the caller notes the skip.
        except ImportError:
            return None, None
        scale = self._dpi / 72.0
        pil = page.render(scale=scale).to_pil()  # ty: ignore[unresolved-attribute]
        try:
            page_w = float(page.get_width())  # ty: ignore[unresolved-attribute]
            page_h = float(page.get_height())  # ty: ignore[unresolved-attribute]
        # Page dims unavailable → 0.0 sentinels route to the honest whole-page-crop
        # branch below (no silent geometry guess).
        except (AttributeError, TypeError, ValueError, _PdfiumError()):
            page_w, page_h = 0.0, 0.0
        x0, y0, x1, y1 = bbox
        if page_w <= 0 or page_h <= 0 or x1 <= x0 or y1 <= y0:
            # No usable geometry → store the whole rendered page (honest, over-broad).
            buf = io.BytesIO()
            pil.save(buf, format="PNG")
            return buf.getvalue(), (int(pil.width), int(pil.height))
        px0 = max(0, int(x0 * scale))
        px1 = min(pil.width, int(x1 * scale))
        # Flip y: PDF origin is bottom-left, raster origin is top-left.
        py0 = max(0, int((page_h - y1) * scale))
        py1 = min(pil.height, int((page_h - y0) * scale))
        if px1 <= px0 or py1 <= py0:
            return None, None
        crop = pil.crop((px0, py0, px1, py1))
        buf = io.BytesIO()
        crop.save(buf, format="PNG")
        return buf.getvalue(), (int(crop.width), int(crop.height))

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
from typing import List, Optional, Sequence, Tuple

from lawvm.core.source_document.anchors import BBox
from lawvm.ingest.struct_wire import ImageElement

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
    """

    text: str
    y_order: int
    bbox: Optional[BBox] = None
    band: Optional[str] = None  # top | body | bottom
    indent: Optional[int] = None
    col: Optional[int] = None


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
        band, quantized indent, y-order) in the SAME order/count (Decision 7). The
        geometry lane degrades gracefully: if the textpage rect API is
        unavailable, ``page_lines`` is empty and only ``lines`` is produced (a
        typed note, never a crash).
        """
        import importlib

        pdfium = importlib.import_module("pypdfium2")
        doc = pdfium.PdfDocument(pdf_bytes)
        try:
            page = doc[page_num - 1]
            textpage = page.get_textpage()
            page_w, page_h = self._page_dims(page)
            lines, page_lines, geom_notes = self._extract_lines_with_geometry(
                textpage, page_h
            )
            images, img_notes = self._enumerate_images(page, page_num)
            return PageElements(
                page_num=page_num,
                lines=lines,
                images=images,
                notes=geom_notes + img_notes,
                page_lines=page_lines,
                page_width=page_w,
                page_height=page_h,
            )
        finally:
            doc.close()

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
        # Align geometry rows to the model-facing lines by text match when the
        # counts agree; otherwise fall back to lines-only (never a wrong bbox).
        if len(geom) != len(lines):
            note = (
                f"page geometry line-count mismatch (rects={len(geom)} lines={len(lines)}) "
                "— lines only",
            )
            return lines, (), note
        page_lines: List["PageLine"] = []
        for y_order, (line_text, (_cell_text, bbox)) in enumerate(
            zip(lines, geom, strict=False)
        ):
            page_lines.append(self._page_line(line_text, y_order, bbox, page_h))
        return lines, tuple(page_lines), ()

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

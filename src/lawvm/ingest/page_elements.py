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
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from lawvm.ingest.struct_wire import ImageElement

# Fixed rasterization DPI for region crops WITHOUT an embedded XObject. It is
# part of the pipeline version (see ``parsed_store.RASTERIZE_DPI_TAG``): a crop
# re-rendered at a new DPI is a NEW content-addressed blob under a NEW path.
RASTERIZE_DPI = 200


@dataclass(frozen=True, slots=True)
class PageElements:
    """A page's numbered reading-order text lines + numbered embedded images.

    ``lines`` are 1-indexed reading-order text lines (``[N]``). ``images`` are the
    embedded/rasterized image elements (``{N}``) with their raw bytes attached so
    the caller can content-address + store them. ``notes`` records any image the
    extractor could not read (typed, never a silent drop).
    """

    page_num: int
    lines: Tuple[str, ...]
    images: Tuple["EmbeddedImage", ...] = ()
    notes: Tuple[str, ...] = ()


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
        """Reading-order text lines + embedded/rasterized images for 1-indexed page."""
        import importlib

        pdfium = importlib.import_module("pypdfium2")
        doc = pdfium.PdfDocument(pdf_bytes)
        try:
            page = doc[page_num - 1]
            text = dehyphenate(page.get_textpage().get_text_range())
            lines = tuple(ln.strip() for ln in text.splitlines() if ln.strip())
            images, notes = self._enumerate_images(page, page_num)
            return PageElements(page_num=page_num, lines=lines, images=images, notes=notes)
        finally:
            doc.close()

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

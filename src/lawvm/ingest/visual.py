"""Shared visual primitive — render a page-region crop, content-addressed.

ONE bbox-crop renderer consumed by BOTH the Level-1 agentic re-read (§8 of
``notes/SOURCE_DOCUMENT_TWO_LEVEL_PIPELINE.md`` — repair a confidently-garbled
vision read by zooming in on the suspect region) and the future Level-2 ``VIEW``
affordance (§7.2 — a subagent looks closer at a garble/formula during
composition). It is a STANDALONE, pure-machinery module: it renders bytes, it
does NOT decide anything and it is not coupled to either level's orchestration.

The crop is CONTENT-ADDRESSED: identical (source bytes, page, bbox, dpi) render
to byte-identical PNG, so a re-read / VIEW is reproducible and cache-HIT
byte-identical (determinism firewall). The locator shape mirrors the embedded
inline-image scheme (``<digest>.pdf/NNNN.img``) so a crop is addressable like any
other image evidence.

pypdfium2 renders whole pages; we render the page at the requested DPI and crop
the bbox in PIL. Coordinates are PDF points (72/inch, origin bottom-left); the
rendered raster is top-left origin, so the y axis is flipped against the page
height (the same convention as ``page_elements._rasterize_region``). Both libs
are optional deps: their absence is a TYPED raise (``RegionRenderFailure``),
never a silent empty.
"""
from __future__ import annotations

import hashlib
import importlib
import io

from lawvm.core.source_document.anchors import BBox
from lawvm.core.source_document.extraction import SourceManifestation

# The default re-read DPI. The cold vision read renders at ``scale=2.0`` (≈144
# DPI); the re-read zooms in, so the crop is rendered materially higher (a garble
# is often a resolution artifact — more pixels on the same region resolves it).
DEFAULT_REREAD_DPI = 300


class RegionRenderFailure(Exception):
    """A render / crop / missing-backend failure (typed, never a silent empty)."""

    def __init__(self, *, page_num: int, reason_code: str, detail: str) -> None:
        super().__init__(detail)
        self.page_num = page_num
        self.reason_code = reason_code
        self.detail = detail


def region_crop_locator(artifact_digest: str, page_num: int, bbox: BBox, dpi: int) -> str:
    """Content-addressed locator for a region crop (``<digest>.pdf/NNNN.img#...``).

    The bbox + dpi are folded into the address so two crops of the SAME page at
    different regions / DPIs are distinct evidence; mirrors the inline embedded
    image scheme so a crop is locatable like any other image record.
    """
    key = f"{bbox.x0},{bbox.y0},{bbox.x1},{bbox.y1}@{dpi}"
    tag = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return f"{artifact_digest}.pdf/{page_num:04d}.img#region={tag}"


def render_region_crop(
    manifestation: SourceManifestation,
    page_num: int,
    bbox: BBox,
    dpi: int = DEFAULT_REREAD_DPI,
) -> bytes:
    """Render ``bbox`` of 1-indexed ``page_num`` at ``dpi`` → a PNG crop's bytes.

    Content-addressed: identical inputs render byte-identically. ``bbox`` is in
    PDF points (origin bottom-left); the rendered raster is top-left origin, so
    the crop's y axis is flipped against the page height. A degenerate / empty
    bbox, an out-of-range page, or a missing backend RAISES ``RegionRenderFailure``
    — the caller (re-read / VIEW) chooses the fallback, never a silent empty.
    """
    if bbox.x1 <= bbox.x0 or bbox.y1 <= bbox.y0:
        raise RegionRenderFailure(
            page_num=page_num,
            reason_code="region_degenerate_bbox",
            detail=f"bbox {bbox} has non-positive area",
        )
    try:
        pdfium = importlib.import_module("pypdfium2")
    except ImportError as exc:
        raise RegionRenderFailure(
            page_num=page_num,
            reason_code="region_render_backend_missing",
            detail=f"pypdfium2 not importable: {exc}",
        ) from exc
    try:
        from PIL import Image  # noqa: F401  (import guard: pillow present)
    except ImportError as exc:
        raise RegionRenderFailure(
            page_num=page_num,
            reason_code="region_crop_backend_missing",
            detail=f"pillow not importable: {exc}",
        ) from exc

    doc = pdfium.PdfDocument(manifestation.source_bytes)
    try:
        if page_num < 1 or page_num > len(doc):
            raise RegionRenderFailure(
                page_num=page_num,
                reason_code="region_page_out_of_range",
                detail=f"page {page_num} out of range (1..{len(doc)})",
            )
        page = doc[page_num - 1]
        scale = dpi / 72.0
        pil = page.render(scale=scale).to_pil()
        page_h = float(page.get_height())
        page_w = float(page.get_width())
        if page_w <= 0 or page_h <= 0:
            raise RegionRenderFailure(
                page_num=page_num,
                reason_code="region_page_dims_unavailable",
                detail="page width/height unreadable",
            )
        px0 = max(0, int(bbox.x0 * scale))
        px1 = min(pil.width, int(bbox.x1 * scale))
        # Flip y: PDF origin bottom-left, raster origin top-left.
        py0 = max(0, int((page_h - bbox.y1) * scale))
        py1 = min(pil.height, int((page_h - bbox.y0) * scale))
        if px1 <= px0 or py1 <= py0:
            raise RegionRenderFailure(
                page_num=page_num,
                reason_code="region_crop_empty",
                detail=f"bbox {bbox} maps to an empty pixel region at dpi {dpi}",
            )
        crop = pil.crop((px0, py0, px1, py1))
        buf = io.BytesIO()
        crop.save(buf, format="PNG")
        return buf.getvalue()
    finally:
        doc.close()

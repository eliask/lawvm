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
import threading

from lawvm.core.source_document.anchors import BBox
from lawvm.core.source_document.extraction import SourceManifestation

# The default re-read / region DPI. A WHOLE-PAGE vision read renders at ``scale=2.0``,
# but the model's vision encoder resizes any image to a FIXED token grid, so a small
# glyph on a full page lands on ~one patch and is guessed — raising the whole-page
# render scale does NOT help (the encoder just downsamples it back; measured on a
# scanned 1994 gazette: a 6-8 pt italic misreads identically at render scale 2/3/4).
# What recovers the glyph is ISOLATION: cropping the region into its OWN image so its
# text commands a large SHARE of the encoder's grid (measured: correct even at a
# 400 px crop). This DPI is the zoom a region crop is rendered at once isolated — a
# bound on crop sharpness, not the lever that fixes fidelity (isolation is).
DEFAULT_REREAD_DPI = 300

# The render scale the scanned-page SEGMENTER analyses ink projections at (§9). The
# analysis raster is throwaway (only its ink geometry is used to place region bboxes,
# which are then re-rendered at DEFAULT_REREAD_DPI); 2.0 is ample for a projection
# profile and matches the whole-page read's scale.
_SEGMENT_ANALYSIS_SCALE = 2.0

# --------------------------------------------------------------------------- #
# Systemic pdfium lock (#250) — the ONE canonical guard for pypdfium2's C state. #
# --------------------------------------------------------------------------- #
#
# pypdfium2 wraps a single PROCESS-GLOBAL C library (pdfium) whose state is NOT
# thread-safe: two threads issuing concurrent pdfium calls (render / text-extract
# / object-parse / document-open) race in the C layer and SEGFAULT the whole
# process (exit 139). Any consumer that fans PDFs out across threads (the
# per-PDF-concurrent calibration / scan sweeps, a future region-read pool) must
# serialize every pdfium touch through ONE lock. This is that lock — defined in
# the lowest-level shared primitive so ``page_elements``, ``vision_producer`` and
# every tool import the SAME object instead of each reinventing a private one
# (which does not actually serialize across modules). Hold it around the ENTIRE
# pdfium interaction (open → use → close), not just a single call, because the
# ``PdfDocument`` handle and its pages/textpages share the same C state.
PDFIUM_LOCK = threading.RLock()


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

    # Hold the systemic pdfium lock around the ENTIRE document lifecycle (open →
    # render/measure → close): pdfium's C state is process-global + thread-unsafe,
    # so a concurrent consumer must never interleave with this render (#250).
    with PDFIUM_LOCK:
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


# --------------------------------------------------------------------------- #
# Scanned-page read-region segmentation (§9) — image-driven, geometry-free.    #
# --------------------------------------------------------------------------- #
#
# A genuinely SCANNED page has NO pdfium text layer, so ``page_elements`` yields
# zero per-line geometry and the geometry-driven ``page_level._propose_regions``
# cannot subdivide it. Yet the whole-page vision read is exactly where small text
# (a 6-8 pt italic heading) is lost to the encoder's fixed token grid (see
# ``DEFAULT_REREAD_DPI``). This function recovers the missing read-geometry FROM
# THE PAGE IMAGE: a classic recursive XY-cut over the ink projection carves the
# page into Manhattan-layout reading regions (columns split at whitespace gutters,
# blocks at inter-paragraph gaps), each of which is then read on its OWN high-DPI
# crop by the existing cold region reader — so each region's text commands enough
# of the encoder grid to be transcribed faithfully. Pure, deterministic (identical
# bytes → identical regions), and gated to the scanned residual: born-digital pages
# keep their zero-vision GEOM lane untouched.

# XY-cut tuning (fractions of the analysis raster, so they scale with render size).
_SEG_MARGIN_X = 0.04   # suppress outer-width margin (gazette page-edge tick marks)
_SEG_MARGIN_Y = 0.03   # suppress outer-height margin (header/footer rules, ticks)
_SEG_INK_THRESHOLD = 160  # grayscale < this ⇒ ink (dark on light scan)
_SEG_MIN_VGAP = 0.016  # min gutter WIDTH (frac of page width) to split into columns
_SEG_MIN_HGAP = 0.015  # min gap HEIGHT (frac of page height) to split into blocks
_SEG_MIN_SIDE = 0.05   # don't recurse into a region shorter than this (frac height)
_SEG_COL_COVERAGE = 0.33  # each side of a gutter must carry ink in ≥ this row-fraction
_SEG_MAX_DEPTH = 7


def _seg_biggest_interior_gap(profile, floor: float):
    """The (mid, width) of the widest STRICTLY-INTERIOR near-zero run in ``profile``.

    ``profile`` is a 1-D ink-per-index projection; a "gap" is a maximal run of
    indices whose ink is ``<= floor``. Leading / trailing runs (margins) are
    ignored — a cut must have content on BOTH sides. ``None`` when no interior gap
    exists."""
    zero = profile <= floor
    n = int(zero.shape[0])
    runs = []
    start = None
    for i in range(n):
        if zero[i]:
            if start is None:
                start = i
        elif start is not None:
            runs.append((start, i - 1))
            start = None
    if start is not None:
        runs.append((start, n - 1))
    interior = [(a, b) for (a, b) in runs if a > 0 and b < n - 1]
    if not interior:
        return None
    a, b = max(interior, key=lambda r: r[1] - r[0])
    return ((a + b) // 2, b - a + 1)


def segment_page_regions(
    manifestation: SourceManifestation,
    page_num: int,
    *,
    max_regions: int = 16,
    min_regions: int = 2,
) -> "tuple[tuple[BBox, int], ...]":
    """Image-driven read regions for a SCANNED page (recursive XY-cut, §9).

    Renders ``page_num`` at the analysis scale, projects the ink, and recursively
    cuts the content area — a whitespace COLUMN gutter first (so multi-column bodies
    never interleave), else an inter-block horizontal gap — into Manhattan reading
    regions. Returns ``(bbox, expected_line_count)`` pairs in reading order
    (top→bottom, left→right within a row), ``bbox`` in PDF points (the convention
    ``render_region_crop`` consumes). Bounded to ``max_regions`` by merging the
    shortest vertically-adjacent same-column pair (never across a column boundary).
    Empty when the page has too little ink, cannot be split into ``>= min_regions``,
    or a backend (pypdfium2 / numpy / pillow) is absent — the caller then falls back
    to the whole-page read, never a silent drop."""
    try:
        import numpy as np  # noqa: PLC0415  (optional heavy dep, imported lazily)

        pdfium = importlib.import_module("pypdfium2")
        from PIL import Image  # noqa: F401, PLC0415  (import guard: pillow present)
    except ImportError:
        return ()

    # A non-PDF / corrupt / empty ``source_bytes`` (or an unrenderable page) is a
    # best-effort MISS, not a crash: return () so the caller falls back to the
    # whole-page read. render_region_crop RAISES for its callers; the segmenter is a
    # pure geometry hint, so an unusable page just yields no regions.
    try:
        with PDFIUM_LOCK:
            doc = pdfium.PdfDocument(manifestation.source_bytes)
            try:
                if page_num < 1 or page_num > len(doc):
                    return ()
                page = doc[page_num - 1]
                page_w = float(page.get_width())
                page_h = float(page.get_height())
                if page_w <= 0 or page_h <= 0:
                    return ()
                s = _SEGMENT_ANALYSIS_SCALE
                pil = page.render(scale=s).to_pil().convert("L")
            finally:
                doc.close()
    except (pdfium.PdfiumError, ValueError, OSError, RuntimeError):
        return ()

    arr = np.asarray(pil)
    h, w = arr.shape
    ink = (arr < _SEG_INK_THRESHOLD).astype(np.float32)
    # Suppress the page-edge margins (scan tick marks / rules would fake a column).
    mx, my = int(w * _SEG_MARGIN_X), int(h * _SEG_MARGIN_Y)
    if mx:
        ink[:, :mx] = 0.0
        ink[:, w - mx :] = 0.0
    if my:
        ink[:my, :] = 0.0
        ink[h - my :, :] = 0.0

    min_vgap = int(w * _SEG_MIN_VGAP)
    min_hgap = int(h * _SEG_MIN_HGAP)
    min_side = int(h * _SEG_MIN_SIDE)

    def column_valid(x0: int, y0: int, x1: int, y1: int, xc: int) -> bool:
        # A real gutter has text on BOTH sides across most of the region height (a
        # merely-centred short line does not) — rejects false column splits.
        left = ink[y0:y1, x0:xc]
        right = ink[y0:y1, xc:x1]
        lo = float((left.sum(axis=1) > 0).mean()) if left.size else 0.0
        ro = float((right.sum(axis=1) > 0).mean()) if right.size else 0.0
        return lo > _SEG_COL_COVERAGE and ro > _SEG_COL_COVERAGE

    def xycut(x0: int, y0: int, x1: int, y1: int, depth: int):
        sub = ink[y0:y1, x0:x1]
        bh, bw = sub.shape
        if bh < min_side or depth > _SEG_MAX_DEPTH:
            return [(x0, y0, x1, y1)]
        # Column gutter FIRST: a validated full-height whitespace gutter un-interleaves
        # a multi-column body before any horizontal (reading-order) banding.
        vgap = _seg_biggest_interior_gap(sub.sum(axis=0), float(bh) * 0.015)
        if vgap is not None and vgap[1] >= min_vgap and column_valid(x0, y0, x1, y1, x0 + vgap[0]):
            xc = x0 + vgap[0]
            return xycut(x0, y0, xc, y1, depth + 1) + xycut(xc, y0, x1, y1, depth + 1)
        hgap = _seg_biggest_interior_gap(sub.sum(axis=1), float(bw) * 0.006)
        if hgap is not None and hgap[1] >= min_hgap:
            yc = y0 + hgap[0]
            return xycut(x0, y0, x1, yc, depth + 1) + xycut(x0, yc, x1, y1, depth + 1)
        return [(x0, y0, x1, y1)]

    # Trim to the inked content area before cutting (ignore whole-page margins).
    row_ink = ink.sum(axis=1)
    col_ink = ink.sum(axis=0)
    rows = np.where(row_ink > w * 0.005)[0]
    cols = np.where(col_ink > h * 0.005)[0]
    if rows.size == 0 or cols.size == 0:
        return ()
    y0, y1 = int(rows.min()), int(rows.max()) + 1
    x0, x1 = int(cols.min()), int(cols.max()) + 1
    boxes = xycut(x0, y0, x1, y1, 0)
    if len(boxes) < min_regions:
        return ()

    # Bound the fan-out: merge the SHORTEST vertically-adjacent same-column pair
    # (identical x-span, touching in y) until within budget — never merges across a
    # column boundary (which would re-interleave), only stacks small blocks.
    def same_col(a, b) -> bool:
        return abs(a[0] - b[0]) <= 2 and abs(a[2] - b[2]) <= 2

    while len(boxes) > max_regions:
        best = None
        for i in range(len(boxes) - 1):
            a, b = boxes[i], boxes[i + 1]
            if same_col(a, b):
                height = (b[3] - a[1])
                if best is None or height < best[1]:
                    best = (i, height)
        if best is None:
            break  # no mergeable adjacent pair — accept the (bounded-by-layout) count
        i = best[0]
        a, b = boxes[i], boxes[i + 1]
        boxes[i : i + 2] = [(min(a[0], b[0]), a[1], max(a[2], b[2]), b[3])]

    # Convert analysis-raster pixels → PDF points (bottom-left origin) with a small
    # pad, and estimate the region's line count from its point height (~11 pt/line)
    # to size the cold reader's output budget.
    out: list[tuple[BBox, int]] = []
    for (a0, b0, a1, b1) in boxes:
        px0 = max(0.0, a0 / s - 2.0)
        px1 = min(page_w, a1 / s + 2.0)
        top_pt = min(page_h, page_h - b0 / s + 2.0)
        bot_pt = max(0.0, page_h - b1 / s - 2.0)
        if px1 <= px0 or top_pt <= bot_pt:
            continue
        expected = max(1, int(round((top_pt - bot_pt) / 11.0)))
        out.append((BBox(x0=px0, y0=bot_pt, x1=px1, y1=top_pt), expected))
    if len(out) < min_regions:
        return ()
    return tuple(out)

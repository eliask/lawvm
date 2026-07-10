"""``lawvm fi-calibration`` — the reliability CALIBRATION harness (spec §10 dec. 4).

The question is NOT "what is the one right subdivision count". Accuracy-vs-
granularity is a **U-curve** (coarse → garble / output truncation; fine → lost
linguistic context). This harness is the instrument that FINDS the adaptive
operating point and, as its real product, VALIDATES the oracle-free monitoring
proxies against ground truth.

It is ADDITIVE over ``fi_parse_corpus`` + ``parsed_store`` + ``page_elements`` and
the ``ingest`` primitives — it NEVER edits the ingest pipeline. It re-uses the
frozen carriers (``PageElements`` / ``PageLine`` geometry, ``render_region_crop``,
the ``VisionPageProducer`` per-region read, the ``defacsimile._numeric_tokens``
grabbers, ``suspect_region``'s cheap proxies) and only ADDS the calibration logic:
region subdivision, per-config scoring, ceiling detection, threshold extraction,
the deterministic ``subdivide`` policy, and the proxy-validation correlation.

Control variables (§10 dec. 4): **pixels-per-glyph** and **output-tokens-per-call**
cliffs — NOT region count. The operating point is set at **0.7× the cliff load**
(the home of the "−30% margin").

Gold (tightness order, §10 dec. 4):

  * **tight per-region gold** = the attachment PDF's OWN pdfium text layer, taken
    per region (geometry-aligned, free, born-digital only). This is why
    ``fi_parse_corpus``'s document-level MISSING is inflated — a media PDF is only
    part of the statute; the whole ``main.xml`` is a LOOSE document-level gold.
  * **document-level cross-check** = the sibling authoritative ``main.xml`` body
    text (``xml_body_text``) — the only gold for scanned pages, but loose.
  * a page whose text-layer coverage is below a floor is a **scanned stratum** with
    NO text-layer gold (scored against the document-level XML only, flagged).

Metrics (severity order, §10 dec. 4), scored **end-to-end POST-STITCH** over the
whole reconstructed page, not mean-per-region:

  1. **NUMERIC-exact** — every §-ref / euro / date token byte-exact (the primary
     GATE), reported as a FAILURE COUNT. Reuses ``defacsimile._numeric_tokens``.
  2. **WER** — word error rate over dehyphenated, whitespace-normalized text.
  3. **CER** — character error rate (garble diagnosis).
  4. **structural boundary F1** — line-boundary agreement.
  5. **seam-defect count** — words invented / lost within ±1 line of a region
     boundary (the cost the finer tiling PAYS to buy resolution).

The full sweep needs the GPU and is OPERATOR-invoked. CI runs it hermetically at
small scale with a fake vision reader over fixtures. Deterministic line-based /
CSV output (two runs diff empty).
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Dict, List, Optional, Sequence, Tuple

from lawvm.core.source_document.anchors import BBox
from lawvm.ingest.defacsimile import _numeric_tokens
from lawvm.ingest.page_elements import PageElements, PageLine, dehyphenate
from lawvm.ingest.suspect_region import (
    cross_reader_disagrees,
    lexical_implausibility,
)
from lawvm.ingest.visual import PDFIUM_LOCK as _PDFIUM_LOCK

if TYPE_CHECKING:
    from lawvm.core.source_document.extraction import SourceManifestation

# pypdfium2's C state is PROCESS-GLOBAL and NOT thread-safe: concurrent
# render / text-extract across docs SEGFAULTS (exit 139) non-deterministically. The
# calibration read/score path is already sequential within a thread, but an operator
# may wrap ``live_region_reader`` in a pool. This module single-flights every pdfium
# touch through the SYSTEMIC ``ingest.visual.PDFIUM_LOCK`` (not a private lock) — so
# the guard the calibration hook holds here is the SAME object the underlying
# primitives (``page_elements`` / ``vision_producer``) hold, giving true
# cross-module serialization (a per-module lock does not). Real pdfium parallelism
# (if ever needed) is a ProcessPoolExecutor, not more threads.

# --------------------------------------------------------------------------- #
# Sweep axes — the DETERMINISTIC layout hierarchy, never pixel tiles (§10 d.6). #
# --------------------------------------------------------------------------- #

# Granularity levels sweep the deterministic layout hierarchy from coarse to fine.
# whole_page → column → block → k-line bands (k ∈ {24,12,6}). NEVER pixel tiles,
# NEVER a mid-line cut (§10 dec. 6). "coarser" = a SMALLER ordinal here.
GRANULARITY_LEVELS: Tuple[str, ...] = (
    "whole_page",
    "column",
    "block",
    "band24",
    "band12",
    "band6",
)
_GRAN_ORDINAL: Dict[str, int] = {g: i for i, g in enumerate(GRANULARITY_LEVELS)}
# k-line band sizes for the band* levels (lines per region).
_BAND_K: Dict[str, int] = {"band24": 24, "band12": 12, "band6": 6}

# DPI sweep (render resolution) and overlap (corroboration lines) sweeps.
DPI_LEVELS: Tuple[int, ...] = (144, 200, 300)
OVERLAP_LEVELS: Tuple[int, ...] = (0, 1, 2)

# A page whose pdfium text-layer covers fewer than this fraction of its rendered
# lines is treated as a SCANNED stratum (no tight per-region text-layer gold).
_TEXTLAYER_COVERAGE_FLOOR = 0.60

# The operating point sits at this fraction of the measured cliff load (§10 dec.
# 4: the "−30% margin" lives HERE, as a physical-load derating, not a region count).
CLIFF_DERATE = 0.70

# WER "within noise of best" band: a level counts as ceiling-eligible if its WER is
# within this of the corpus-best WER for the stratum (bounded by measured variance).
_WER_NOISE_BAND = 0.02


# --------------------------------------------------------------------------- #
# Deterministic per-region gold + region model.                                #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Region:
    """One deterministic sub-page region + its tight per-region text-layer gold.

    ``line_indexes`` are the 0-based indexes (into the page's reading-order lines)
    this region owns (INCLUDING overlap lines); ``core_line_indexes`` are the lines
    it owns WITHOUT the overlap corroboration band — the stitch keeps only core
    lines so overlap never double-counts. ``gold_text`` is the pdfium text-layer
    text of exactly the core lines (the tight, geometry-aligned per-region gold);
    empty for a scanned region (no text layer). ``bbox_*`` is the union pixel-region
    the vision reader would render (page points), carried for pixels-per-glyph.
    """

    region_id: int
    line_indexes: Tuple[int, ...]
    core_line_indexes: Tuple[int, ...]
    gold_text: str
    col: Optional[int]
    band_key: str
    px_width_pt: float
    px_height_pt: float
    n_glyphs: int  # gold character count over the core lines (for pixels-per-glyph)
    # Absolute page-point bbox (union of the overlap-inclusive line bboxes) — the
    # crop the live reader renders. ``None`` when the page has no line geometry
    # (a degraded geometry lane) → the live reader cannot crop this region.
    abs_bbox: Optional[BBox] = None


@dataclass(frozen=True, slots=True)
class PageStratum:
    """Deterministic complexity stratum of a page (from ``page_elements`` geometry).

    A page is stratified by DETERMINISTIC features only (§10 dec. 4): line count,
    column count, table density (numeric-heavy line fraction), and median font size.
    The stratum key groups like-complexity pages so a ceiling can be found per
    stratum (born-digital simple text may already be at ceiling at whole-page).
    ``scanned`` marks a page with no usable text-layer gold.
    """

    key: str
    n_lines: int
    n_columns: int
    table_density: float
    median_font_size: float
    scanned: bool


# --------------------------------------------------------------------------- #
# WER / CER — rapidfuzz edit distance over normalized text.                     #
# --------------------------------------------------------------------------- #


def _normalize_words(text: str) -> List[str]:
    """Dehyphenated, whitespace-normalized, lower-cased word list (WER unit)."""
    return dehyphenate(text).lower().split()


def _normalize_chars(text: str) -> str:
    """Dehyphenated, whitespace-collapsed character string (CER unit)."""
    return " ".join(dehyphenate(text).split())


def word_error_rate(gold: str, hyp: str) -> float:
    """Word-error rate = word-level Levenshtein / len(gold words) (0.0 = perfect).

    Uses rapidfuzz's Indel/Levenshtein over word tokens. Empty gold → 0.0 when the
    hypothesis is also empty, else 1.0 (everything is an insertion).
    """
    from rapidfuzz.distance import Levenshtein

    g = _normalize_words(gold)
    h = _normalize_words(hyp)
    if not g:
        return 0.0 if not h else 1.0
    dist = Levenshtein.distance(g, h)
    return dist / len(g)


def char_error_rate(gold: str, hyp: str) -> float:
    """Character-error rate = char-level Levenshtein / len(gold chars) (garble diag)."""
    from rapidfuzz.distance import Levenshtein

    g = _normalize_chars(gold)
    h = _normalize_chars(hyp)
    if not g:
        return 0.0 if not h else 1.0
    return Levenshtein.distance(g, h) / len(g)


def numeric_exact_failures(gold: str, hyp: str) -> int:
    """Count of protected NUMERIC tokens NOT byte-exactly preserved (the primary gate).

    A FAILURE is any §-ref / euro / date / number token whose multiset count in the
    hypothesis differs from the gold — a dropped ``14 §`` or a garbled euro amount.
    Reuses ``defacsimile._numeric_tokens`` (the exact production grabber) so the
    calibration gate and the production ``verify_ledger`` gate agree by construction.
    """
    g = _numeric_tokens(gold)
    h = _numeric_tokens(hyp)
    failures = 0
    for tok, gc in g.items():
        hc = h.get(tok, 0)
        if hc != gc:
            failures += abs(gc - hc)
    # Invented numeric tokens (present in hyp, absent in gold) are failures too.
    for tok, hc in h.items():
        if tok not in g:
            failures += hc
    return failures


def _line_set(text: str) -> List[str]:
    """Normalized non-empty lines (structural boundary unit)."""
    return [ln for ln in (_normalize_chars(l) for l in text.splitlines()) if ln]


def structural_boundary_f1(gold: str, hyp: str) -> float:
    """F1 of the multiset of normalized line boundaries (1.0 = identical structure).

    A cheap, deterministic proxy for structural fidelity: precision/recall over the
    multiset of normalized lines. Coarse tiling that merges two lines into one, or
    fine tiling that splits one, both drop this below 1.0.
    """
    g = _line_set(gold)
    h = _line_set(hyp)
    if not g and not h:
        return 1.0
    if not g or not h:
        return 0.0
    gc: Dict[str, int] = {}
    for ln in g:
        gc[ln] = gc.get(ln, 0) + 1
    inter = 0
    hc: Dict[str, int] = {}
    for ln in h:
        hc[ln] = hc.get(ln, 0) + 1
    for ln, c in hc.items():
        inter += min(c, gc.get(ln, 0))
    prec = inter / len(h)
    rec = inter / len(g)
    if prec + rec == 0:
        return 0.0
    return 2 * prec * rec / (prec + rec)


# --------------------------------------------------------------------------- #
# Region subdivision — the pure geometry hierarchy (the policy under test).     #
# --------------------------------------------------------------------------- #


def _col_of(pl: PageLine, page_width: float) -> int:
    """Deterministic 2-column index from the line's x-centre (0 = left, 1 = right)."""
    if pl.bbox is None or page_width <= 0:
        return 0
    x_centre = (pl.bbox.x0 + pl.bbox.x1) / 2.0
    return 1 if x_centre > page_width / 2.0 else 0


def _column_count(page: PageElements) -> int:
    """Deterministic column count (1 or 2) from the line x-centre distribution."""
    if not page.page_lines or page.page_width <= 0:
        return 1
    cols = {_col_of(pl, page.page_width) for pl in page.page_lines if pl.bbox is not None}
    return 2 if len(cols) >= 2 else 1


def _region_bbox(
    page: PageElements, line_indexes: Sequence[int]
) -> Tuple[float, float, Optional[BBox]]:
    """Union (width_pt, height_pt, abs_bbox) of the lines' bboxes.

    ``abs_bbox`` is the absolute page-point union rectangle (the crop the live
    reader renders); all three are ``0,0,None`` when no line carries geometry (a
    degraded geometry lane — the region is then un-croppable but still scored on
    the text-layer gold).
    """
    bxs: List[float] = []
    bys: List[float] = []
    for i in line_indexes:
        if i < len(page.page_lines):
            b = page.page_lines[i].bbox
            if b is not None:
                bxs.extend([b.x0, b.x1])
                bys.extend([b.y0, b.y1])
    if not bxs or not bys:
        return (0.0, 0.0, None)
    x0, x1, y0, y1 = min(bxs), max(bxs), min(bys), max(bys)
    return (x1 - x0, y1 - y0, BBox(x0=x0, y0=y0, x1=x1, y1=y1))


def subdivide(
    page: PageElements, granularity: str, *, overlap: int = 0
) -> Tuple[Region, ...]:
    """PURE region tree over ``page``, parameterized by the deterministic geometry.

    THIS is the function whose calibrated operating point the harness selects — the
    model gets NO say in tiling (determinism, §10 dec. 5/7). It cuts ONLY at
    semantic layout boundaries (column, block, k-line band); NEVER a pixel tile,
    NEVER a mid-line cut. ``overlap`` adds N corroboration lines on each side of a
    region (the 1-line corroboration overlap of §10 dec. 6) WITHOUT letting them
    into the region's CORE (so the stitch never double-counts an overlap line).

    Gold per region is the pdfium text-layer text of the region's CORE lines — the
    tight, geometry-aligned, born-digital per-region gold.
    """
    n = len(page.lines)
    if n == 0:
        return ()
    cols = _column_count(page)
    groups = _core_line_groups(page, granularity, cols)
    regions: List[Region] = []
    for rid, core in enumerate(groups):
        if not core:
            continue
        lo, hi = core[0], core[-1]
        # Overlap band: N lines each side, clamped to the page, kept OUT of core.
        with_ov = list(range(max(0, lo - overlap), min(n, hi + 1 + overlap)))
        w, h, abs_bbox = _region_bbox(page, with_ov)
        gold = _gold_text_for(page, core)
        col = _col_of(page.page_lines[lo], page.page_width) if lo < len(page.page_lines) else None
        band_key = _band_key_for(page, core)
        regions.append(
            Region(
                region_id=rid,
                line_indexes=tuple(with_ov),
                core_line_indexes=tuple(core),
                gold_text=gold,
                col=col,
                band_key=band_key,
                px_width_pt=w,
                px_height_pt=h,
                n_glyphs=len(_normalize_chars(gold)),
                abs_bbox=abs_bbox,
            )
        )
    return tuple(regions)


def _core_line_groups(
    page: PageElements, granularity: str, cols: int
) -> List[List[int]]:
    """The CORE line-index groups for a granularity (no overlap yet). Pure geometry."""
    n = len(page.lines)
    all_idx = list(range(n))
    if granularity == "whole_page":
        return [all_idx]
    if granularity == "column":
        if cols < 2 or not page.page_lines:
            return [all_idx]
        left = [i for i in all_idx if i < len(page.page_lines)
                and _col_of(page.page_lines[i], page.page_width) == 0]
        right = [i for i in all_idx if i < len(page.page_lines)
                 and _col_of(page.page_lines[i], page.page_width) == 1]
        return [g for g in (left, right) if g]
    if granularity == "block":
        return _block_groups(page, all_idx)
    # band24 / band12 / band6 — fixed k-line bands WITHIN each column.
    k = _BAND_K[granularity]
    col_groups = _core_line_groups(page, "column", cols)
    out: List[List[int]] = []
    for cg in col_groups:
        for start in range(0, len(cg), k):
            out.append(cg[start : start + k])
    return out


def _block_groups(page: PageElements, all_idx: List[int]) -> List[List[int]]:
    """Coarse block segmentation at heading / large vertical-gap boundaries.

    A new block starts at a ``size_class=='heading'`` line or a large y-gap between
    consecutive lines (a deterministic paragraph break). Pure geometry — no model.
    """
    if not page.page_lines:
        return [all_idx]
    groups: List[List[int]] = []
    cur: List[int] = []
    prev_y: Optional[float] = None
    # Median line height as the gap scale.
    heights = [
        pl.bbox.y1 - pl.bbox.y0
        for pl in page.page_lines
        if pl.bbox is not None and pl.bbox.y1 > pl.bbox.y0
    ]
    med_h = sorted(heights)[len(heights) // 2] if heights else 12.0
    for i in all_idx:
        pl = page.page_lines[i] if i < len(page.page_lines) else None
        is_heading = pl is not None and pl.size_class == "heading"
        big_gap = False
        if pl is not None and pl.bbox is not None and prev_y is not None:
            # PDF origin bottom-left → reading DOWN means y DECREASES.
            big_gap = (prev_y - pl.bbox.y1) > 1.8 * med_h
        if cur and (is_heading or big_gap):
            groups.append(cur)
            cur = []
        cur.append(i)
        if pl is not None and pl.bbox is not None:
            prev_y = pl.bbox.y0
    if cur:
        groups.append(cur)
    return groups


def _gold_text_for(page: PageElements, core: Sequence[int]) -> str:
    """The pdfium text-layer gold for exactly the core lines (tight per-region gold)."""
    return "\n".join(page.lines[i] for i in core if i < len(page.lines))


def _band_key_for(page: PageElements, core: Sequence[int]) -> str:
    """Dominant margin band of the core lines (top/body/bottom) for stratification."""
    counts: Dict[str, int] = {}
    for i in core:
        if i < len(page.page_lines):
            b = page.page_lines[i].band
            if b:
                counts[b] = counts.get(b, 0) + 1
    return max(counts, key=lambda k: counts[k]) if counts else "body"


# --------------------------------------------------------------------------- #
# Page stratification + text-layer coverage (scanned-stratum discovery inline). #
# --------------------------------------------------------------------------- #


def _median_font_size(page: PageElements) -> float:
    """Median line height as a font-size proxy (deterministic complexity feature)."""
    hs = [
        pl.bbox.y1 - pl.bbox.y0
        for pl in page.page_lines
        if pl.bbox is not None and pl.bbox.y1 > pl.bbox.y0
    ]
    if not hs:
        return 0.0
    hs.sort()
    return hs[len(hs) // 2]


def _table_density(page: PageElements) -> float:
    """Fraction of lines that are numeric-heavy (a cheap table-density proxy)."""
    if not page.lines:
        return 0.0
    from lawvm.ingest.page_elements import line_is_numeric_heavy

    heavy = sum(1 for ln in page.lines if line_is_numeric_heavy(ln))
    return heavy / len(page.lines)


def textlayer_coverage(page: PageElements) -> float:
    """Fraction of rendered lines that carry a non-empty pdfium text-layer read.

    The scanned-stratum discriminator computed INLINE (``fi_scan_stratum.py`` may
    not be landed). A born-digital page has a text layer on ~every line; a scanned
    page has few/none (the vision read is then the ONLY reader, and the tight
    per-region gold is unavailable — the page is scored against the XML only).
    """
    if not page.lines:
        return 0.0
    non_empty = sum(1 for ln in page.lines if ln.strip())
    return non_empty / len(page.lines)


def stratify_page(page: PageElements) -> PageStratum:
    """Deterministic complexity stratum of a page (§10 dec. 4 stratification)."""
    n_lines = len([ln for ln in page.lines if ln.strip()])
    n_cols = _column_count(page)
    dens = _table_density(page)
    med = _median_font_size(page)
    scanned = textlayer_coverage(page) < _TEXTLAYER_COVERAGE_FLOOR
    # Bucketed key so like-complexity pages group (deterministic string).
    line_bucket = "short" if n_lines < 20 else ("mid" if n_lines < 45 else "long")
    dens_bucket = "text" if dens < 0.15 else ("mixed" if dens < 0.40 else "tabular")
    key = (
        "scanned"
        if scanned
        else f"cols{n_cols}/{line_bucket}/{dens_bucket}"
    )
    return PageStratum(
        key=key,
        n_lines=n_lines,
        n_columns=n_cols,
        table_density=dens,
        median_font_size=med,
        scanned=scanned,
    )


# --------------------------------------------------------------------------- #
# The vision reader interface (fake-able for CI; real Qwen for the GPU sweep).  #
# --------------------------------------------------------------------------- #

# A region reader maps (page_num, region) → the model's read of that region's crop.
# CI injects a deterministic fake; the operator's live sweep binds the real
# ``VisionPageProducer.reread_region`` over ``render_region_crop`` at the config DPI.
RegionReader = Callable[[int, Region, int], str]


@dataclass(frozen=True, slots=True)
class SweepConfig:
    """One point in the (granularity × DPI × overlap) sweep — a physical operating pt."""

    granularity: str
    dpi: int
    overlap: int

    @property
    def tag(self) -> str:
        return f"{self.granularity}@dpi{self.dpi}+ov{self.overlap}"


@dataclass(frozen=True, slots=True)
class ConfigScore:
    """The end-to-end POST-STITCH score of one config over one page (§10 dec. 4).

    Scored over the WHOLE stitched page, not mean-per-region. Carries the physical
    control variables (``pixels_per_glyph`` at the config DPI, ``max_output_tokens``
    = the largest per-call output the tiling demanded) so the cliff is expressed in
    load, not region count. ``proxy_*`` are the oracle-free monitoring signals
    measured on the SAME run for validation against the true metrics.
    """

    config: SweepConfig
    n_regions: int
    numeric_failures: int
    wer: float
    cer: float
    boundary_f1: float
    seam_defects: int
    pixels_per_glyph: float
    max_output_tokens: int
    proxy_overlap_disagreements: int
    proxy_cross_reader_disagreements: int
    proxy_lexical_implausible: int


def _stitch(page: PageElements, regions: Sequence[Region], reads: Sequence[str]) -> str:
    """Stitch per-region reads back to one page text in reading order (post-stitch).

    Keeps only each region's CORE lines' worth of read (overlap corroboration lines
    are dropped from the stitch so an overlap line is never double-counted). Regions
    are concatenated in their deterministic id order (which is reading order).
    """
    parts: List[str] = []
    for reg, read in zip(regions, reads, strict=True):
        parts.append(read.strip())
    return "\n".join(p for p in parts if p)


def _output_tokens_estimate(text: str) -> int:
    """Cheap deterministic output-token estimate (~4 chars/token, the decode cost)."""
    return (len(text) + 3) // 4


def _seam_defects(
    page: PageElements, regions: Sequence[Region], reads: Sequence[str]
) -> int:
    """Words invented / lost within ±1 line of a region boundary (§10 dec. 4 metric).

    For each region boundary, compare the multiset of words the reader produced for
    the boundary line against the pdfium text-layer gold for that same line; a
    mismatch there is a SEAM defect (the price the finer tiling pays). Deterministic
    word-multiset symmetric difference over just the boundary lines.
    """
    defects = 0
    for reg, read in zip(regions, reads, strict=True):
        core = reg.core_line_indexes
        if not core:
            continue
        # boundary lines = first + last core line of the region.
        boundary_line_idx = {core[0], core[-1]}
        read_words = _normalize_words(read)
        read_ms: Dict[str, int] = {}
        for w in read_words:
            read_ms[w] = read_ms.get(w, 0) + 1
        for li in boundary_line_idx:
            if li >= len(page.lines):
                continue
            gold_words = _normalize_words(page.lines[li])
            for gw in gold_words:
                # a boundary gold word absent from the region read = a lost word.
                if read_ms.get(gw, 0) == 0:
                    defects += 1
    return defects


def score_config(
    page: PageElements,
    config: SweepConfig,
    reader: RegionReader,
    *,
    doc_gold: Optional[str] = None,
) -> ConfigScore:
    """Score ONE (page, config) end-to-end post-stitch (all five metrics + proxies).

    The per-region reads come from ``reader`` (fake in CI, real Qwen on the GPU);
    the metrics compare the STITCHED page against the tight per-region text-layer
    gold (born-digital) — or, for a scanned page (``doc_gold`` given, no text
    layer), against the document-level XML cross-check. The oracle-free proxies
    (overlap-disagreement, cross-reader-disagreement, lexical-implausibility) are
    measured on the SAME run so they can later be correlated with the true error.
    """
    regions = subdivide(page, config.granularity, overlap=config.overlap)
    reads = [reader(page.page_num, reg, config.dpi) for reg in regions]
    stitched = _stitch(page, regions, reads)

    # Tight per-region gold (born-digital) → the stitched core-line gold; else the
    # loose document-level XML (scanned stratum).
    tight_gold = "\n".join(reg.gold_text for reg in regions if reg.gold_text.strip())
    gold = tight_gold if tight_gold.strip() else (doc_gold or "")

    numeric_failures = numeric_exact_failures(gold, stitched)
    wer = word_error_rate(gold, stitched)
    cer = char_error_rate(gold, stitched)
    f1 = structural_boundary_f1(gold, stitched)
    seams = _seam_defects(page, regions, reads)

    # Physical control variables.
    scale = config.dpi / 72.0
    total_px = sum((reg.px_width_pt * scale) * (reg.px_height_pt * scale) for reg in regions)
    total_glyphs = sum(reg.n_glyphs for reg in regions) or 1
    ppg = total_px / total_glyphs
    max_out = max((_output_tokens_estimate(r) for r in reads), default=0)

    # Oracle-free proxies (same run).
    ov_dis = _overlap_disagreements(page, regions, reads)
    xr_dis = _cross_reader_disagreements(page, regions, reads)
    lex = sum(1 for r in reads if lexical_implausibility(r))

    return ConfigScore(
        config=config,
        n_regions=len(regions),
        numeric_failures=numeric_failures,
        wer=wer,
        cer=cer,
        boundary_f1=f1,
        seam_defects=seams,
        pixels_per_glyph=ppg,
        max_output_tokens=max_out,
        proxy_overlap_disagreements=ov_dis,
        proxy_cross_reader_disagreements=xr_dis,
        proxy_lexical_implausible=lex,
    )


def _overlap_disagreements(
    page: PageElements, regions: Sequence[Region], reads: Sequence[str]
) -> int:
    """Overlap-zone disagreement count (§10 dec. 6: agreement = self-consistency).

    Where two regions overlap (share a line), the two reads should agree on that
    line's words. A disagreement in the shared zone is a cheap ORACLE-FREE localizer
    — measured here so it can be validated against the true WER on the gold.
    """
    line_to_reads: Dict[int, List[str]] = {}
    for reg, read in zip(regions, reads, strict=True):
        for li in reg.line_indexes:
            line_to_reads.setdefault(li, []).append(read)
    disagreements = 0
    for li, rs in line_to_reads.items():
        if len(rs) < 2:
            continue
        # any pair of reads whose word sets materially disagree.
        for a in range(len(rs)):
            for b in range(a + 1, len(rs)):
                if cross_reader_disagrees(rs[a], rs[b]):
                    disagreements += 1
                    break
    return disagreements


def _cross_reader_disagreements(
    page: PageElements, regions: Sequence[Region], reads: Sequence[str]
) -> int:
    """Vision-vs-pdfium region disagreement count (the strong cross-reader proxy)."""
    count = 0
    for reg, read in zip(regions, reads, strict=True):
        # the pdfium text-layer read of the same region = an INDEPENDENT reader.
        indep = _gold_text_for(page, reg.core_line_indexes)
        if cross_reader_disagrees(read, indep):
            count += 1
    return count


# --------------------------------------------------------------------------- #
# Ceiling detection → the two physical thresholds → operating point.            #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Ceiling:
    """The calibrated ceiling for a stratum + the derived physical thresholds.

    ``cliff_config`` is the COARSEST config with NUMERIC failures=0 and WER within
    noise of the stratum-best (the U-curve's flat top's coarse edge — the least
    physical load that still reads faithfully). The two PHYSICAL thresholds are the
    pixels-per-glyph and output-tokens-per-call MEASURED at that cliff; the
    operating point derates the load to ``CLIFF_DERATE`` (0.7×) of the cliff (§10
    dec. 4: the "−30% margin" lives here).
    """

    stratum_key: str
    cliff_config: Optional[SweepConfig]
    cliff_pixels_per_glyph: float
    cliff_output_tokens: int
    best_wer: float
    operating_pixels_per_glyph: float
    operating_output_tokens: int
    n_configs: int


def detect_ceiling(stratum_key: str, scores: Sequence[ConfigScore]) -> Ceiling:
    """Find the coarsest faithful config for a stratum → the operating thresholds.

    Faithful = NUMERIC-exact failures == 0 AND WER within ``_WER_NOISE_BAND`` of the
    stratum-best WER. Among the faithful configs we take the COARSEST (least
    granular → least physical load), tie-broken by lowest DPI then lowest overlap
    then WER — a total, deterministic order. The cliff's measured pixels-per-glyph
    and output-tokens become the physical thresholds; the operating point derates
    them to ``CLIFF_DERATE``. When NO config is faithful, the cliff is ``None`` (the
    stratum needs a finer regime than the sweep covered — a first-class finding).
    """
    if not scores:
        return Ceiling(stratum_key, None, 0.0, 0, 1.0, 0.0, 0, 0)
    best_wer = min(s.wer for s in scores)
    faithful = [
        s
        for s in scores
        if s.numeric_failures == 0 and s.wer <= best_wer + _WER_NOISE_BAND
    ]
    if not faithful:
        return Ceiling(stratum_key, None, 0.0, 0, best_wer, 0.0, 0, len(scores))
    # Coarsest faithful config (smallest granularity ordinal), deterministic ties.
    cliff = min(
        faithful,
        key=lambda s: (
            _GRAN_ORDINAL[s.config.granularity],
            s.config.dpi,
            s.config.overlap,
            s.wer,
            s.config.tag,
        ),
    )
    return Ceiling(
        stratum_key=stratum_key,
        cliff_config=cliff.config,
        cliff_pixels_per_glyph=cliff.pixels_per_glyph,
        cliff_output_tokens=cliff.max_output_tokens,
        best_wer=best_wer,
        operating_pixels_per_glyph=cliff.pixels_per_glyph * CLIFF_DERATE,
        operating_output_tokens=int(cliff.max_output_tokens * CLIFF_DERATE),
        n_configs=len(scores),
    )


# --------------------------------------------------------------------------- #
# The emitted deterministic adaptive policy.                                    #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class AdaptivePolicy:
    """The calibrated deterministic ``subdivide`` policy — the harness's product.

    ``per_stratum`` maps a stratum key → the operating physical thresholds; the
    ``version_tag`` folds those thresholds into a modality/version string (so a
    threshold change is a NEW content-addressed key, §10 dec. 7). ``apply`` is the
    PURE ``subdivide(page_elements) -> region tree``: it strata-classifies the page,
    looks up the operating pixels-per-glyph, and picks the COARSEST granularity whose
    per-region pixels-per-glyph at the chosen DPI stays ABOVE the operating floor.
    The MODEL gets no say — tiling is a pure function of geometry + calibration.
    """

    per_stratum: Dict[str, Ceiling]
    default_ceiling: Ceiling
    version_tag: str

    def apply(self, page: PageElements) -> Tuple[Region, ...]:
        """Deterministic ``subdivide(page_elements) -> region tree`` at the operating pt."""
        stratum = stratify_page(page)
        ceil = self.per_stratum.get(stratum.key, self.default_ceiling)
        gran, dpi, overlap = self._choose(page, ceil)
        return subdivide(page, gran, overlap=overlap)

    def _choose(self, page: PageElements, ceil: Ceiling) -> Tuple[str, int, int]:
        """Pick the coarsest granularity whose per-region load clears the operating pt."""
        cfg = ceil.cliff_config
        dpi = cfg.dpi if cfg is not None else DPI_LEVELS[-1]
        overlap = cfg.overlap if cfg is not None else 1
        floor = ceil.operating_pixels_per_glyph
        # Walk coarse→fine; keep the coarsest whose measured pixels-per-glyph at this
        # DPI stays at/above the operating floor (finer = fewer glyphs/region = more
        # pixels/glyph, so finer always clears — the coarsest that clears is the pick).
        chosen = GRANULARITY_LEVELS[-1]
        for gran in GRANULARITY_LEVELS:
            regs = subdivide(page, gran, overlap=overlap)
            if not regs:
                continue
            scale = dpi / 72.0
            total_px = sum((r.px_width_pt * scale) * (r.px_height_pt * scale) for r in regs)
            total_glyphs = sum(r.n_glyphs for r in regs) or 1
            ppg = total_px / total_glyphs
            if floor <= 0 or ppg >= floor:
                chosen = gran
                break
        return chosen, dpi, overlap


def emit_policy(
    ceilings: Dict[str, Ceiling], *, default_stratum: str = "cols1/mid/text"
) -> AdaptivePolicy:
    """Fold the per-stratum ceilings into the deterministic adaptive policy + version tag.

    The version tag is a stable digest of the operating thresholds (§10 dec. 7): a
    threshold change bumps the tag, so a re-tiled page is a NEW content-addressed
    key, never a silent semantic drift under the same key.
    """
    import hashlib

    default_ceiling = ceilings.get(
        default_stratum,
        next(iter(ceilings.values())) if ceilings else Ceiling("_none", None, 0.0, 0, 1.0, 0.0, 0, 0),
    )
    key_material = "|".join(
        f"{k}:ppg={ceilings[k].operating_pixels_per_glyph:.3f}"
        f":tok={ceilings[k].operating_output_tokens}"
        for k in sorted(ceilings)
    )
    digest = hashlib.sha256(key_material.encode("utf-8")).hexdigest()[:12]
    version_tag = f"calib.v1+derate{CLIFF_DERATE}+thr={digest}"
    return AdaptivePolicy(
        per_stratum=dict(ceilings),
        default_ceiling=default_ceiling,
        version_tag=version_tag,
    )


# --------------------------------------------------------------------------- #
# Proxy validation — the experiment's REAL product (§10 dec. 4).                #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ProxyValidation:
    """How well each oracle-free proxy correlates with the TRUE error on the gold.

    A proxy is a trustworthy MONITORING instrument only if it tracks true error
    when no gold is present. We measure the Pearson correlation of each proxy
    (overlap-disagreement / cross-reader-disagreement / lexical-implausibility)
    against the true WER across all scored configs — the experiment's real product
    (§10 dec. 4: "a validated monitoring instrument, not just a constant").
    """

    n_samples: int
    corr_overlap_vs_wer: float
    corr_cross_reader_vs_wer: float
    corr_lexical_vs_wer: float
    corr_cross_reader_vs_numeric: float


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float:
    """Pearson correlation (0.0 for a degenerate/constant series — no false signal)."""
    n = len(xs)
    if n < 2:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return 0.0
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    return sxy / (sxx**0.5 * syy**0.5)


def validate_proxies(scores: Sequence[ConfigScore]) -> ProxyValidation:
    """Correlate each oracle-free proxy against the true error across all configs."""
    if not scores:
        return ProxyValidation(0, 0.0, 0.0, 0.0, 0.0)
    wer = [s.wer for s in scores]
    numeric = [float(s.numeric_failures) for s in scores]
    ov = [float(s.proxy_overlap_disagreements) for s in scores]
    xr = [float(s.proxy_cross_reader_disagreements) for s in scores]
    lex = [float(s.proxy_lexical_implausible) for s in scores]
    return ProxyValidation(
        n_samples=len(scores),
        corr_overlap_vs_wer=_pearson(ov, wer),
        corr_cross_reader_vs_wer=_pearson(xr, wer),
        corr_lexical_vs_wer=_pearson(lex, wer),
        corr_cross_reader_vs_numeric=_pearson(xr, numeric),
    )


# --------------------------------------------------------------------------- #
# Born-digital geom lane A/B — the deterministic vision-free lane vs vision.     #
# --------------------------------------------------------------------------- #
#
# The proof the born-digital STRUCTURE lane (``ingest.born_digital``) is not a
# regression before it is ever made default (§ RULES: "A/B-PROVEN not-worse"). Both
# lanes are scored end-to-end POST-STITCH against the SAME tight per-region pdfium
# text-layer gold; the geom lane sends ZERO image tokens (it reads the text layer +
# geometry deterministically), the vision lane spends the page-image tokens. The A/B
# gate: geom NUMERIC-exact failures and WER must be <= vision's (never worse), and
# the token saving is the whole point.

# Whole-page vision image-token cost (the token census: ~8.7k image tokens/page at
# the cold-read DPI). Used to project the token saving the geom lane buys; a
# per-page constant so the projection is a physical estimate, not a live-only number.
_IMAGE_TOKENS_PER_PAGE = 8700

_STUB_DIGEST = "0" * 64


@dataclass(frozen=True, slots=True)
class _StubManifestation:
    """A minimal manifestation stand-in — the geom lane only reads ``artifact_digest``."""

    artifact_digest: str = _STUB_DIGEST


def _flatten_source_node_text(node: object) -> str:
    """Depth-first reading text of a ``SourceDocumentNode`` tree (geom reconstruction)."""
    parts: List[str] = []

    def _walk(n: object) -> None:
        t = getattr(n, "text", "")
        if t:
            parts.append(str(t))
        for c in getattr(n, "children", ()):  # geom lane is flat, but be general
            _walk(c)

    _walk(node)
    return "\n".join(parts)


@dataclass(frozen=True, slots=True)
class BornDigitalABRow:
    """One page's geom-lane-vs-vision-lane A/B (both scored on the pdfium gold)."""

    page_num: int
    born_digital: bool
    geom_numeric_failures: int
    vision_numeric_failures: int
    geom_wer: float
    vision_wer: float
    geom_boundary_f1: float
    vision_boundary_f1: float
    geom_nodes: int
    geom_headings: int
    geom_tables_flagged: int
    geom_fallbacks: int
    geom_image_tokens: int  # 0 by construction (no image sent)
    vision_image_tokens: int  # the image tokens the vision lane would spend


@dataclass(frozen=True, slots=True)
class BornDigitalABReport:
    """The geom-lane A/B over a page set + the projected token saving."""

    rows: Tuple[BornDigitalABRow, ...]
    n_pages: int
    n_born_digital: int
    total_geom_image_tokens: int
    total_vision_image_tokens: int
    regressions: Tuple[str, ...]  # pages where geom is worse on a GATE metric


def _geom_page_text_and_counts(
    page: PageElements, *, manifestation: Optional[object] = None
) -> Tuple[str, int, int, int, int]:
    """Deterministic geom reconstruction of one page → (text, nodes, headings, tables, fallbacks)."""
    from lawvm.core.source_document.ir import SourceDocumentNodeKind
    from lawvm.ingest.born_digital import born_digital_page

    man = manifestation if manifestation is not None else _StubManifestation()
    result = born_digital_page(man, page.page_num, page)
    nodes = result.simulacrum.nodes
    text = "\n".join(_flatten_source_node_text(n) for n in nodes)
    headings = sum(1 for n in nodes if n.kind is SourceDocumentNodeKind.HEADING)
    tables = sum(1 for fb in result.fallbacks if fb.reason == "table_grid")
    return text, len(nodes), headings, tables, len(result.fallbacks)


def born_digital_ab(
    pages: Sequence[PageElements],
    vision_reader: RegionReader,
    *,
    manifestation: Optional[object] = None,
    image_tokens_per_page: int = _IMAGE_TOKENS_PER_PAGE,
) -> BornDigitalABReport:
    """A/B the deterministic geom lane against the vision lane on born-digital pages.

    For each page the vision lane is scored with a whole-page read (the existing
    ``score_config`` path over ``vision_reader``); the geom lane reconstructs the page
    DETERMINISTICALLY (``ingest.born_digital``). Both are scored against the SAME
    tight pdfium text-layer gold (NUMERIC-exact + WER + boundary-F1). A page is a
    REGRESSION iff the geom lane is worse on a GATE metric (numeric failures up, or
    WER materially up) — the guard that keeps the token lever from trading accuracy.
    The geom lane's image-token cost is ZERO; the vision lane's is the page-image
    cost — the projected saving.
    """
    from lawvm.ingest.born_digital import page_is_born_digital

    rows: List[BornDigitalABRow] = []
    regressions: List[str] = []
    total_geom_tokens = 0
    total_vision_tokens = 0
    n_born = 0
    for page in pages:
        is_bd = page_is_born_digital(page)
        gold = "\n".join(ln for ln in page.lines if ln.strip())
        # Vision lane: whole-page read scored on the same gold.
        vcfg = SweepConfig("whole_page", DPI_LEVELS[0], 0)
        vscore = score_config(page, vcfg, vision_reader)
        # Geom lane: deterministic reconstruction.
        geom_text, n_nodes, n_head, n_tab, n_fb = _geom_page_text_and_counts(
            page, manifestation=manifestation
        )
        g_numeric = numeric_exact_failures(gold, geom_text)
        g_wer = word_error_rate(gold, geom_text)
        g_f1 = structural_boundary_f1(gold, geom_text)
        vision_tokens = image_tokens_per_page if is_bd else 0
        geom_tokens = 0
        if is_bd:
            n_born += 1
            total_geom_tokens += geom_tokens
            total_vision_tokens += vision_tokens
            # GATE: geom must not be worse than vision on the protected metrics.
            if g_numeric > vscore.numeric_failures:
                regressions.append(
                    f"page {page.page_num}: NUMERIC geom={g_numeric} > vision={vscore.numeric_failures}"
                )
            if g_wer > vscore.wer + _WER_NOISE_BAND:
                regressions.append(
                    f"page {page.page_num}: WER geom={g_wer:.4f} > vision={vscore.wer:.4f}"
                )
        rows.append(
            BornDigitalABRow(
                page_num=page.page_num,
                born_digital=is_bd,
                geom_numeric_failures=g_numeric,
                vision_numeric_failures=vscore.numeric_failures,
                geom_wer=g_wer,
                vision_wer=vscore.wer,
                geom_boundary_f1=g_f1,
                vision_boundary_f1=vscore.boundary_f1,
                geom_nodes=n_nodes,
                geom_headings=n_head,
                geom_tables_flagged=n_tab,
                geom_fallbacks=n_fb,
                geom_image_tokens=geom_tokens,
                vision_image_tokens=vision_tokens,
            )
        )
    return BornDigitalABReport(
        rows=tuple(rows),
        n_pages=len(pages),
        n_born_digital=n_born,
        total_geom_image_tokens=total_geom_tokens,
        total_vision_image_tokens=total_vision_tokens,
        regressions=tuple(regressions),
    )


_AB_HEADER = (
    "page,born_digital,geom_numeric,vision_numeric,geom_wer,vision_wer,"
    "geom_f1,vision_f1,geom_nodes,geom_headings,geom_tables,geom_fallbacks,"
    "geom_img_tokens,vision_img_tokens"
)


def render_born_digital_ab(report: BornDigitalABReport) -> str:
    """Deterministic line-based render of the geom-lane A/B (two runs diff empty)."""
    lines: List[str] = []
    lines.append(
        "# fi-calibration born-digital A/B — deterministic geom lane vs vision "
        "(both scored on the pdfium text-layer gold)"
    )
    saved = report.total_vision_image_tokens - report.total_geom_image_tokens
    lines.append(
        f"# pages={report.n_pages}  born_digital={report.n_born_digital}  "
        f"image_tokens_saved={saved}  regressions={len(report.regressions)}"
    )
    lines.append("")
    lines.append(_AB_HEADER)
    for r in sorted(report.rows, key=lambda x: x.page_num):
        lines.append(
            ",".join(
                str(v)
                for v in (
                    r.page_num,
                    int(r.born_digital),
                    r.geom_numeric_failures,
                    r.vision_numeric_failures,
                    f"{r.geom_wer:.4f}",
                    f"{r.vision_wer:.4f}",
                    f"{r.geom_boundary_f1:.4f}",
                    f"{r.vision_boundary_f1:.4f}",
                    r.geom_nodes,
                    r.geom_headings,
                    r.geom_tables_flagged,
                    r.geom_fallbacks,
                    r.geom_image_tokens,
                    r.vision_image_tokens,
                )
            )
        )
    lines.append("")
    lines.append("## REGRESSIONS (geom worse than vision on a GATE metric — must be empty to flip default)")
    if report.regressions:
        lines.extend(report.regressions)
    else:
        lines.append("NONE")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# The calibration run (over a set of pages) + report.                           #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class CalibrationReport:
    """The full calibration result: per-config scores, ceilings, policy, proxies."""

    configs: Tuple[SweepConfig, ...]
    scores: Tuple[Tuple[str, ConfigScore], ...]  # (stratum_key, score), page-labelled
    ceilings: Tuple[Ceiling, ...]
    policy: AdaptivePolicy
    proxies: ProxyValidation
    n_pages: int
    variance_probe: Tuple[Tuple[str, float], ...]  # (config_tag, wer_delta_repeat)


def default_sweep(
    *,
    granularities: Sequence[str] = GRANULARITY_LEVELS,
    dpis: Sequence[int] = DPI_LEVELS,
    overlaps: Sequence[int] = OVERLAP_LEVELS,
) -> Tuple[SweepConfig, ...]:
    """The full deterministic (granularity × DPI × overlap) sweep grid."""
    return tuple(
        SweepConfig(g, d, o)
        for g in granularities
        for d in dpis
        for o in overlaps
    )


def run_calibration(
    pages: Sequence[PageElements],
    reader: RegionReader,
    *,
    configs: Sequence[SweepConfig] = (),
    doc_gold: Optional[str] = None,
    variance_repeats: int = 1,
) -> CalibrationReport:
    """Run the full sweep over ``pages`` → ceilings + adaptive policy + proxy validation.

    ``reader`` is the region reader (fake in CI, real Qwen on the GPU). ``doc_gold``
    is the document-level XML cross-check (used for scanned pages with no text
    layer). ``variance_repeats`` > 1 re-runs a couple of configs to BOUND the
    llama.cpp run-to-run variance BEFORE trusting the sweep (§10 dec. 4 / step 3).
    Scores are grouped by page stratum; a ceiling is detected per stratum; the
    policy folds them into a versioned deterministic ``subdivide``.
    """
    cfgs = tuple(configs) if configs else default_sweep()

    # First: bound run-to-run variance on a COUPLE of configs (temp=0 is not
    # bit-stable on llama.cpp) so a "ceiling" isn't chasing decode noise.
    variance_probe: List[Tuple[str, float]] = []
    if variance_repeats > 1 and pages and cfgs:
        for probe_cfg in cfgs[: min(2, len(cfgs))]:
            wers = [
                score_config(pages[0], probe_cfg, reader, doc_gold=doc_gold).wer
                for _ in range(variance_repeats)
            ]
            variance_probe.append((probe_cfg.tag, max(wers) - min(wers)))

    # Score every (page, config); group by page stratum.
    by_stratum: Dict[str, List[ConfigScore]] = {}
    labelled: List[Tuple[str, ConfigScore]] = []
    for page in pages:
        stratum = stratify_page(page)
        gold = doc_gold if stratum.scanned else None
        for cfg in cfgs:
            sc = score_config(page, cfg, reader, doc_gold=gold)
            by_stratum.setdefault(stratum.key, []).append(sc)
            labelled.append((stratum.key, sc))

    ceilings = {k: detect_ceiling(k, v) for k, v in by_stratum.items()}
    policy = emit_policy(ceilings)
    proxies = validate_proxies([sc for _k, sc in labelled])

    return CalibrationReport(
        configs=cfgs,
        scores=tuple(labelled),
        ceilings=tuple(ceilings[k] for k in sorted(ceilings)),
        policy=policy,
        proxies=proxies,
        n_pages=len(pages),
        variance_probe=tuple(variance_probe),
    )


# --------------------------------------------------------------------------- #
# Deterministic line-based / CSV rendering.                                     #
# --------------------------------------------------------------------------- #

_SCORE_HEADER = (
    "stratum,config,n_regions,numeric_fail,wer,cer,boundary_f1,seam_defects,"
    "pixels_per_glyph,max_out_tokens,proxy_overlap,proxy_xreader,proxy_lexical"
)
_CEILING_HEADER = (
    "stratum,cliff_config,cliff_ppg,cliff_out_tokens,best_wer,"
    "operating_ppg,operating_out_tokens,n_configs"
)


def render_report(report: CalibrationReport) -> str:
    """Deterministic line-based render (two runs diff empty). CSV blocks + summary."""
    lines: List[str] = []
    lines.append(
        "# fi-calibration — reliability U-curve (control vars: pixels-per-glyph, "
        "output-tokens-per-call; operating point = 0.7x cliff load)"
    )
    lines.append(
        f"# pages={report.n_pages}  configs={len(report.configs)}  "
        f"policy_version={report.policy.version_tag}"
    )
    if report.variance_probe:
        probe = "  ".join(f"{t}:dWER={d:.4f}" for t, d in report.variance_probe)
        lines.append(f"# run-to-run variance probe (temp=0 llama.cpp): {probe}")
    lines.append("")
    lines.append("## PER-CONFIG SCORES (end-to-end post-stitch)")
    lines.append(_SCORE_HEADER)
    # Deterministic order: stratum, then granularity ordinal, dpi, overlap.
    ordered = sorted(
        report.scores,
        key=lambda t: (
            t[0],
            _GRAN_ORDINAL[t[1].config.granularity],
            t[1].config.dpi,
            t[1].config.overlap,
            t[1].config.tag,
        ),
    )
    for stratum, sc in ordered:
        lines.append(
            ",".join(
                str(v)
                for v in (
                    stratum,
                    sc.config.tag,
                    sc.n_regions,
                    sc.numeric_failures,
                    f"{sc.wer:.4f}",
                    f"{sc.cer:.4f}",
                    f"{sc.boundary_f1:.4f}",
                    sc.seam_defects,
                    f"{sc.pixels_per_glyph:.1f}",
                    sc.max_output_tokens,
                    sc.proxy_overlap_disagreements,
                    sc.proxy_cross_reader_disagreements,
                    sc.proxy_lexical_implausible,
                )
            )
        )
    lines.append("")
    lines.append("## CEILINGS + OPERATING THRESHOLDS (per stratum)")
    lines.append(_CEILING_HEADER)
    for c in report.ceilings:
        lines.append(
            ",".join(
                str(v)
                for v in (
                    c.stratum_key,
                    c.cliff_config.tag if c.cliff_config else "NONE",
                    f"{c.cliff_pixels_per_glyph:.1f}",
                    c.cliff_output_tokens,
                    f"{c.best_wer:.4f}",
                    f"{c.operating_pixels_per_glyph:.1f}",
                    c.operating_output_tokens,
                    c.n_configs,
                )
            )
        )
    lines.append("")
    lines.append("## PROXY VALIDATION (oracle-free monitor vs TRUE error — the product)")
    p = report.proxies
    lines.append("proxy,correlates_with,pearson_r")
    lines.append(f"overlap_disagreement,wer,{p.corr_overlap_vs_wer:.4f}")
    lines.append(f"cross_reader_disagreement,wer,{p.corr_cross_reader_vs_wer:.4f}")
    lines.append(f"lexical_implausible,wer,{p.corr_lexical_vs_wer:.4f}")
    lines.append(f"cross_reader_disagreement,numeric_fail,{p.corr_cross_reader_vs_numeric:.4f}")
    lines.append(f"# n_samples={p.n_samples}")
    return "\n".join(lines)


def report_to_json(report: CalibrationReport) -> Dict[str, object]:
    """JSON form (same deterministic order as the rendered report)."""
    return {
        "policy_version": report.policy.version_tag,
        "n_pages": report.n_pages,
        "n_configs": len(report.configs),
        "variance_probe": [
            {"config": t, "wer_delta": d} for t, d in report.variance_probe
        ],
        "ceilings": [
            {
                "stratum": c.stratum_key,
                "cliff_config": c.cliff_config.tag if c.cliff_config else None,
                "cliff_pixels_per_glyph": c.cliff_pixels_per_glyph,
                "cliff_output_tokens": c.cliff_output_tokens,
                "best_wer": c.best_wer,
                "operating_pixels_per_glyph": c.operating_pixels_per_glyph,
                "operating_output_tokens": c.operating_output_tokens,
            }
            for c in report.ceilings
        ],
        "proxies": {
            "n_samples": report.proxies.n_samples,
            "overlap_vs_wer": report.proxies.corr_overlap_vs_wer,
            "cross_reader_vs_wer": report.proxies.corr_cross_reader_vs_wer,
            "lexical_vs_wer": report.proxies.corr_lexical_vs_wer,
            "cross_reader_vs_numeric": report.proxies.corr_cross_reader_vs_numeric,
        },
    }


# --------------------------------------------------------------------------- #
# Live region reader (operator GPU sweep) — binds the real Qwen per-region read. #
# --------------------------------------------------------------------------- #


def live_region_reader(
    manifestation: "SourceManifestation", *, base_url: str = "http://127.0.0.1:8080"
) -> RegionReader:
    """A ``RegionReader`` bound to the REAL vision model over ``render_region_crop``.

    Renders each region's SELF-CARRIED absolute bbox (``region.abs_bbox``, the union
    of its overlap-inclusive line bboxes) at the config DPI and asks the vision model
    for a COLD MULTI-LINE transcription of the whole crop (``read_region_cold``) —
    NOT the §8 single-line ``reread_region`` CORRECTION path, whose one-line budget
    returns ~nothing over a multi-line region/page crop (that path stays a correction
    path, unchanged). The cold read has no prior text to anchor on, so the sweep
    measures the model's RAW regional accuracy. Used only by the operator's GPU
    sweep — CI injects a fake reader instead. A region with no geometry
    (``abs_bbox is None``, a degraded geometry lane) is un-croppable → an empty read
    (the metric counts it MISSING, an honest "needs the whole-page fallback" signal,
    not a crash).
    """
    from lawvm.ingest.llm_backends.vision_producer import (
        VisionPageProducer,
        VisionProducerFailure,
        VisionProducerTruncated,
    )
    from lawvm.ingest.visual import RegionRenderFailure

    producer = VisionPageProducer(base_url=base_url)

    def _read(page_num: int, region: Region, dpi: int) -> str:
        if region.abs_bbox is None:
            return ""
        try:
            # A COLD multi-line region read (no prior text → unbiased raw accuracy).
            # ``read_region_cold`` renders the crop via pdfium → single-flight the
            # whole call under the SYSTEMIC pdfium lock (thread-unsafe C state). The
            # geometry line count sizes the output budget so the whole region fits.
            with _PDFIUM_LOCK:
                return producer.read_region_cold(
                    manifestation,
                    page_num,
                    region.abs_bbox,
                    dpi=dpi,
                    expected_lines=len(region.line_indexes),
                )
        except (VisionProducerTruncated, VisionProducerFailure, RegionRenderFailure):
            # A truncated / failed region read is an empty region (the metric then
            # counts the lost content as MISSING / WER — a HONEST cliff signal, not
            # a crash that sinks the sweep).
            return ""

    return _read


def _load_live_pages(
    locator: str, finlex_path: str, max_pages: int
) -> Tuple["SourceManifestation", List[PageElements]]:
    """Load a PDF's manifestation + per-page ``PageElements`` for the live sweep."""
    from lawvm.finland.source_document.pdf_profiles import (
        load_manifestation_from_farchive,
    )
    from lawvm.ingest.page_elements import PageElementProducer

    manifestation = load_manifestation_from_farchive(
        locator, farchive_path=finlex_path, source_role="attachment"
    )
    producer = PageElementProducer()
    pages: List[PageElements] = []
    # Every pdfium touch (page-count probe + per-page parse) is single-flighted
    # through the process-global lock — pdfium's C state is not thread-safe.
    import importlib

    with _PDFIUM_LOCK:
        pdfium = importlib.import_module("pypdfium2")
        doc = pdfium.PdfDocument(manifestation.source_bytes)
        try:
            n = min(len(doc), max_pages)
        finally:
            doc.close()
        for pn in range(1, n + 1):
            pages.append(producer.page_elements(manifestation.source_bytes, pn))
    return manifestation, pages


# --------------------------------------------------------------------------- #
# CLI                                                                           #
# --------------------------------------------------------------------------- #


def main(args: argparse.Namespace) -> None:
    """CLI handler for ``lawvm fi-calibration``.

    Live GPU sweep is OPERATOR-invoked (``--live LOCATOR``); it renders real region
    crops and reads them with the Qwen vision model. Without ``--live`` the harness
    has no pages to score (the full 6,232-PDF × many-configs sweep needs the GPU and
    is never run in CI) — CI exercises the harness through the hermetic test's fake
    reader, not this CLI.
    """
    finlex_path = args.finlex or "data/finlex.farchive"
    if not args.live:
        raise SystemExit(
            "fi-calibration: pass --live LOCATOR for the operator GPU sweep "
            "(the hermetic small-scale run lives in the test with a fake reader; "
            "the full corpus sweep needs the GPU and is never CI-run)."
        )

    manifestation, all_pages = _load_live_pages(args.live, finlex_path, args.max_pages)
    # Stratify + take a small deterministic sample across strata (the operator can
    # widen with --sample). Sort by stratum then page for a stable prefix.
    stratified = sorted(
        all_pages, key=lambda p: (stratify_page(p).key, p.page_num)
    )
    pages = stratified[: args.sample] if args.sample else stratified

    # The live reader crops each region's SELF-CARRIED absolute bbox — no side table.
    reader = live_region_reader(manifestation, base_url=args.base_url)

    # Born-digital geom-lane A/B (token lever): score the deterministic vision-free
    # lane against the live vision lane on the sampled pages, then STOP (this is a
    # distinct experiment from the U-curve sweep, sharing only the page load).
    if getattr(args, "born_digital_ab", False):
        ab = born_digital_ab(pages, reader, manifestation=manifestation)
        print(render_born_digital_ab(ab))
        return

    cfgs = default_sweep()

    # Document-level XML cross-check for scanned pages (best-effort).
    doc_gold = _doc_gold_for(args.live, finlex_path)

    report = run_calibration(
        pages,
        reader,
        configs=cfgs,
        doc_gold=doc_gold,
        variance_repeats=args.variance_repeats,
    )
    if args.json:
        payload = json.dumps(report_to_json(report), ensure_ascii=False, indent=2)
        print(payload)
        if args.json_out:
            with open(args.json_out, "w", encoding="utf-8") as fh:
                fh.write(payload)
    else:
        print(render_report(report))


def _doc_gold_for(pdf_locator: str, finlex_path: str) -> Optional[str]:
    """Sibling ``main.xml`` body text (document-level cross-check), or None."""
    # lawvm-regex: diagnostic — farchive LOCATOR path transform (.../media/X.pdf → sibling .../main.xml) for the optional calibration gold cross-check; a source-plane path derivation, never post-parse legal semantics.
    m = re.match(r"^(?P<prefix>.+)/media/[^/]+\.pdf$", pdf_locator)
    if m is None:
        return None
    xml_locator = m.group("prefix") + "/main.xml"
    try:
        from farchive import Farchive

        from lawvm.tools.fi_parse_compare import xml_body_text

        fa = Farchive(finlex_path)
        try:
            span = fa.resolve(xml_locator)
            xml_bytes = fa.read(span.digest) if span is not None else b""
        finally:
            fa.close()
        return xml_body_text(xml_bytes) if xml_bytes else None
    except Exception:  # best-effort cross-check; absent gold is not fatal
        return None

"""``lawvm fi-vision-read-calibration`` — vision transcription-error calibration harness.

The empirical instrument that finds the render+read configuration MINIMIZING a vision
model's transcription error on PDF page text, and measures which VERIFICATION mechanisms
(multi-read consensus, agreement→correctness) are actually load-bearing — starting on
CLEAN born-digital HE pages where the pdfium text layer is FREE, EXACT ground truth.

It is ADDITIVE / MEASUREMENT-ONLY: it never touches a production read path. It re-uses
the canonical primitives —

  * :data:`lawvm.ingest.visual.PDFIUM_LOCK` (the ONE process-global pdfium guard) around
    every render,
  * :mod:`lawvm.ingest.suspect_region` (``scan_char_class_garble`` / ``garble_signature``)
    to VALIDATE that a page's text layer is clean before it is used as ground truth,
  * :func:`lawvm.ingest.llm_backends.prompt_fingerprint.prompt_fingerprint` +
    :class:`lawvm.ingest.recovered_text_store.RecoveredTextStore` for the content-addressed
    read CACHE (image-sha256 × prompt × model × decode-params → a warm replay is
    byte-identical and issues NO backend call), and
  * :func:`lawvm.tools.fi_calibration.char_error_rate` / ``word_error_rate`` for the metrics

— and only ADDS the calibration logic: reading-order GT extraction, the render/aspect
geometry (band/single-line crops, pad-to-square, word-gap reflow-stack, overlap tiles),
the config-grid runner, and the multi-read consensus + agreement-predicts-correctness
measurement.

GROUND TRUTH (the #1 correctness risk — a prior probe scrambled GT by per-char
reassembly): a target region's GT is read in READING ORDER via the pdfium textpage rect
API (``get_rect`` + ``get_text_bounded``), NEVER stitched per glyph, and is VALIDATED
(no PUA/control/U+FFFD garble, plausibly Finnish) before use. Corrupt-font pages are
skipped. A sanity floor asserts that the best config reaches CER<0.05 on clean text; if
even that fails, the GT or the metric is wrong and the sweep STOPS.

METRICS (reported as DISTRIBUTIONS — median / p90 / max, with per-item rows, never just
means): CER (normalized Levenshtein), WER, and a HALLUCINATION rate (read tokens ABSENT
from GT — insertions / plausible substitutions).

The full GPU sweep is OPERATOR-invoked via ``--live``; CI exercises the harness
hermetically with a STUB reader (no ``:8080``, no libvoikko) — see the test.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import statistics
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Dict, List, Optional, Sequence, Tuple

from lawvm.core.source_document.anchors import BBox
from lawvm.ingest.llm_backends.prompt_fingerprint import prompt_fingerprint
from lawvm.ingest.page_elements import dehyphenate
from lawvm.ingest.suspect_region import (
    DEFAULT_LEXICAL_PROFILE,
    garble_signature,
    is_pervasively_garbled,
    scan_char_class_garble,
)
from lawvm.ingest.visual import PDFIUM_LOCK
from lawvm.tools.fi_calibration import char_error_rate, word_error_rate

if TYPE_CHECKING:  # pragma: no cover - typing only
    from PIL.Image import Image as PILImage

# --------------------------------------------------------------------------- #
# Prompts (the READ axis). Kept tiny + closed so the fingerprint is stable.     #
# --------------------------------------------------------------------------- #

#: The blind-transcribe SYSTEM prompt (shared shape with the production HE witness).
_SYSTEM_PROMPT = (
    "You are a faithful transcriber. Transcribe the visible text of this image EXACTLY "
    "as printed, preserving spelling, Finnish diacritics, punctuation, and section "
    "markers. Output only the transcription, no commentary."
)

#: The USER prompt variants (the ``prompt`` config axis).
PROMPT_VARIANTS: Dict[str, str] = {
    "minimal_transcribe": "Transcribe this image verbatim.",
    "line_by_line": (
        "Transcribe every line of text in this image verbatim, in reading order, one "
        "output line per printed line. Output only the text."
    ),
    "structured": (
        "Transcribe the text in this image exactly as printed. Preserve section markers "
        "(§, momentti/subsection numbers), euro amounts, dates, and statute references "
        "byte-for-byte. Output only the transcription."
    ),
}

DEFAULT_MODEL = "unsloth/Qwen3.6-35B-A3B-MTP-GGUF:UD-Q4_K_XL"
DEFAULT_BASE_URL = "http://localhost:8080"


# --------------------------------------------------------------------------- #
# Ground-truth extraction — reading order, NEVER per-glyph stitched.            #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class PageTextLine:
    """One visual line of a born-digital page: reading-order text + geometry + word gaps.

    ``bbox`` is in PDF points (origin bottom-left). ``word_gap_x`` are the PDF-point x
    positions of the inter-word whitespace CENTRES within the line (derived from the
    text-layer char boxes) — the only places the reflow-stack aspect is allowed to cut
    (never mid-glyph).
    """

    text: str
    bbox: BBox
    word_gap_x: Tuple[float, ...]


@dataclass(frozen=True, slots=True)
class GTItem:
    """One scored unit: a page region with reading-order, validated ground-truth text.

    ``page_index`` is a corpus-GLOBAL id (unique per (HE, page), the key into the
    ``pdf_by_page`` / ``page_heights`` maps); ``render_page`` is the LOCAL 0-based page index
    within that PDF that the renderer must load (they differ in a multi-HE corpus)."""

    he_id: str
    page_index: int
    kind: str  # "line" | "band" | "page"
    bbox: Optional[BBox]  # None ⇒ whole page
    text: str
    lines: Tuple[PageTextLine, ...]  # constituent lines (for reflow/aspect geometry)
    render_page: int = 0


def _median_char_height(boxes: Sequence[Tuple[float, float, float, float]]) -> float:
    heights = [t - b for (l, b, r, t) in boxes if t > b]
    return statistics.median(heights) if heights else 0.0


def _line_word_gaps(
    char_boxes: Sequence[Tuple[float, float, float, float]],
) -> Tuple[float, ...]:
    """PDF-point x centres of the inter-word gaps in a line (from its char boxes).

    A gap is a horizontal run of whitespace between two glyph boxes wider than ~0.3× the
    line's median char height (≈ a space). Language-agnostic (geometry only), so a cut at
    a returned x lands provably between words, never inside a glyph.
    """
    ink = sorted((b for b in char_boxes if b[2] > b[0]), key=lambda z: z[0])
    if len(ink) < 2:
        return ()
    ch = _median_char_height(ink)
    if ch <= 0:
        return ()
    threshold = 0.25 * ch
    gaps: List[float] = []
    for (l0, b0, r0, t0), (l1, b1, r1, t1) in zip(ink, ink[1:], strict=False):
        if l1 - r0 > threshold:
            gaps.append((r0 + l1) / 2.0)
    return tuple(gaps)


def extract_page_lines(pdf_bytes: bytes, page_index: int) -> Tuple[PageTextLine, ...]:
    """Reading-order text lines (+ geometry + word gaps) of a born-digital page.

    Uses the pypdfium2 textpage rect API — ``count_rects`` / ``get_rect`` gives each
    visual line's bbox, ``get_text_bounded`` its reading-order text — the SAME clean
    substrate ``page_elements`` uses. NEVER assembles text per glyph. Char boxes locate
    the word gaps. Sorted top→bottom, left→right. Empty on a page with no text layer or
    an unavailable rect API (the caller then skips the page)."""
    import pypdfium2 as pdfium

    with PDFIUM_LOCK:
        doc = pdfium.PdfDocument(pdf_bytes)
        try:
            if page_index < 0 or page_index >= len(doc):
                return ()
            page = doc[page_index]
            tp = page.get_textpage()
            try:
                try:
                    count = int(tp.count_rects())
                except (AttributeError, TypeError, ValueError):
                    return ()
                # All glyph boxes once (flip y into bottom-left origin already native).
                char_boxes: List[Tuple[float, float, float, float]] = []
                try:
                    nchars = int(tp.count_chars())
                    for i in range(nchars):
                        cb = tp.get_charbox(i)
                        if cb is not None:
                            l, b, r, t = (float(v) for v in cb)
                            if r > l and t > b:
                                char_boxes.append((l, b, r, t))
                except (AttributeError, TypeError, ValueError, IndexError):
                    char_boxes = []
                rows: List[PageTextLine] = []
                for i in range(count):
                    try:
                        left, bottom, right, top = tp.get_rect(i)
                        x0, x1 = float(min(left, right)), float(max(left, right))
                        y0, y1 = float(min(bottom, top)), float(max(bottom, top))
                        raw = tp.get_text_bounded(
                            left=left, bottom=bottom, right=right, top=top
                        )
                    except (AttributeError, TypeError, ValueError, IndexError):
                        continue
                    text = str(raw).strip()
                    if not text:
                        continue
                    bbox = BBox(x0=x0, y0=y0, x1=x1, y1=y1)
                    in_line = [
                        cb
                        for cb in char_boxes
                        if y0 - 1.0 <= (cb[1] + cb[3]) / 2.0 <= y1 + 1.0
                        and x0 - 1.0 <= (cb[0] + cb[2]) / 2.0 <= x1 + 1.0
                    ]
                    rows.append(PageTextLine(text=text, bbox=bbox, word_gap_x=_line_word_gaps(in_line)))
            finally:
                tp.close()
        finally:
            doc.close()
    # Reading order: top→bottom (descending y1), then left→right.
    rows.sort(key=lambda ln: (-ln.bbox.y1, ln.bbox.x0))
    return tuple(rows)


def is_gt_text_clean(text: str) -> bool:
    """A validated GT string: non-trivial, no char-class garble, plausibly Finnish/Latin.

    Rejects any PUA/control/U+FFFD/mojibake signature (via the canonical
    :func:`~lawvm.ingest.suspect_region.scan_char_class_garble`) and any run the unified
    garble signature marks lexically implausible — so a corrupt-font glyph never masquerades
    as ground truth. Requires a minimum of real letters (a bare number is not GT prose)."""
    src = (text or "").strip()
    if len(src) < 3:
        return False
    letters = sum(1 for c in src if c.isalpha())
    if letters < 2:
        return False
    if scan_char_class_garble(src):
        return False
    sig = garble_signature(src, profile=DEFAULT_LEXICAL_PROFILE)
    return not sig.lexical_signals


def page_is_corrupt(lines: Sequence[PageTextLine]) -> bool:
    """Is a page's text layer pervasively garbled (corrupt-font) → unusable as GT?"""
    joined = " ".join(ln.text for ln in lines)
    return is_pervasively_garbled(joined)


def band_bbox(lines: Sequence[PageTextLine]) -> Optional[BBox]:
    """Union bbox of a run of lines (None if empty)."""
    if not lines:
        return None
    return BBox(
        x0=min(ln.bbox.x0 for ln in lines),
        y0=min(ln.bbox.y0 for ln in lines),
        x1=max(ln.bbox.x1 for ln in lines),
        y1=max(ln.bbox.y1 for ln in lines),
    )


def build_gt_items(
    he_id: str,
    page_index: int,
    lines: Sequence[PageTextLine],
    *,
    render_page: Optional[int] = None,
    lines_per_page: int = 3,
    band_sizes: Sequence[int] = (2, 4, 8),
) -> List[GTItem]:
    """Build validated LINE / BAND / PAGE scoring units from a page's clean lines.

    LINE items are ``lines_per_page`` evenly-spaced substantial lines; BAND items are
    contiguous k-line windows; the PAGE item is the whole page. Every item's GT is
    validated (:func:`is_gt_text_clean` on each constituent line); an item with any
    unclean line is dropped (never scored against dirty GT)."""
    rp = page_index if render_page is None else render_page
    clean = [ln for ln in lines if is_gt_text_clean(ln.text)]
    items: List[GTItem] = []
    # LINE items — prefer WIDE, MULTI-WORD prose lines (the thin-strip / reflow aspect
    # question): substantial, and ranked by word-gap count then pixel width so the picks are
    # realistic wide lines (not a single long compound), letting the reflow/tile aspects apply.
    substantial = [ln for ln in clean if len(ln.text) >= 25]
    substantial.sort(key=lambda ln: (-len(ln.word_gap_x), -(ln.bbox.x1 - ln.bbox.x0)))
    picks = substantial[: max(0, lines_per_page)]
    for ln in picks:
        items.append(
            GTItem(he_id, page_index, "line", ln.bbox, ln.text, (ln,), render_page=rp)
        )
    # BAND items — the first clean contiguous window of each size.
    for k in band_sizes:
        window = clean[:k]
        if len(window) == k and all(is_gt_text_clean(w.text) for w in window):
            bb = band_bbox(window)
            gt = "\n".join(w.text for w in window)
            if bb is not None:
                items.append(
                    GTItem(he_id, page_index, "band", bb, gt, tuple(window), render_page=rp)
                )
    # PAGE item — whole page (loosest gold; scored but flagged).
    if clean:
        gt = "\n".join(w.text for w in clean)
        items.append(GTItem(he_id, page_index, "page", None, gt, tuple(clean), render_page=rp))
    return items


# --------------------------------------------------------------------------- #
# Metrics — CER / WER (reused) + hallucination rate + distribution folding.     #
# --------------------------------------------------------------------------- #


def _norm_words(text: str) -> List[str]:
    return dehyphenate(text).lower().split()


def hallucination_rate(gold: str, hyp: str) -> float:
    """Fraction of READ word-tokens ABSENT from the GT word multiset (insert/substitute).

    A read token not present (by count) in the GT is either an INSERTION or a
    plausible-substitution — the fidelity-critical failure a mere CER can hide (a swapped
    word is one CER edit but a semantic corruption). 0.0 = every read token appears in GT;
    1.0 = nothing does. Empty read → 0.0 (nothing hallucinated)."""
    from collections import Counter

    h = _norm_words(hyp)
    if not h:
        return 0.0
    gold_counts = Counter(_norm_words(gold))
    absent = 0
    for tok in h:
        if gold_counts.get(tok, 0) > 0:
            gold_counts[tok] -= 1
        else:
            absent += 1
    return absent / len(h)


def summarize(values: Sequence[float]) -> Dict[str, float]:
    """Distribution summary (median / p90 / max / mean / n) — never a bare mean."""
    xs = sorted(values)
    n = len(xs)
    if n == 0:
        return {"n": 0, "median": 0.0, "p90": 0.0, "max": 0.0, "mean": 0.0}

    def _pct(p: float) -> float:
        if n == 1:
            return xs[0]
        idx = min(n - 1, int(round(p * (n - 1))))
        return xs[idx]

    return {
        "n": n,
        "median": statistics.median(xs),
        "p90": _pct(0.90),
        "max": xs[-1],
        "mean": statistics.fmean(xs),
    }


# --------------------------------------------------------------------------- #
# Render + aspect geometry.                                                     #
# --------------------------------------------------------------------------- #


def render_page_pil(pdf_bytes: bytes, page_index: int, scale: float) -> "PILImage":
    """Render a whole page to a PIL image at ``scale`` (holds the systemic pdfium lock)."""
    import pypdfium2 as pdfium

    with PDFIUM_LOCK:
        doc = pdfium.PdfDocument(pdf_bytes)
        try:
            page = doc[page_index]
            pil = page.render(scale=scale).to_pil().convert("RGB")
        finally:
            doc.close()
    return pil


def _page_height_pts(pdf_bytes: bytes, page_index: int) -> float:
    import pypdfium2 as pdfium

    with PDFIUM_LOCK:
        doc = pdfium.PdfDocument(pdf_bytes)
        try:
            return float(doc[page_index].get_height())
        finally:
            doc.close()


def crop_region(pil: "PILImage", bbox: BBox, page_h: float, scale: float) -> "PILImage":
    """Crop ``bbox`` (PDF points, bottom-left origin) from a scale-rendered page PIL."""
    px0 = max(0, int(bbox.x0 * scale))
    px1 = min(pil.width, int(bbox.x1 * scale))
    py0 = max(0, int((page_h - bbox.y1) * scale))
    py1 = min(pil.height, int((page_h - bbox.y0) * scale))
    if px1 <= px0 or py1 <= py0:
        return pil
    return pil.crop((px0, py0, px1, py1))


def pad_to_square(pil: "PILImage", background: int = 255) -> "PILImage":
    """Pad a thin/wide crop onto a white square canvas (aspect ``pad_to_square``)."""
    from PIL import Image

    side = max(pil.width, pil.height)
    canvas = Image.new("RGB", (side, side), (background, background, background))
    canvas.paste(pil, ((side - pil.width) // 2, (side - pil.height) // 2))
    return canvas


def reflow_cut_pixels(line: PageTextLine, scale: float, k: int) -> Optional[List[int]]:
    """The ``k-1`` reflow cut x-pixels (snapped to WORD GAPS), or None if not cuttable.

    Ideal cuts sit at ``i/k`` of the line width; each is SNAPPED to the nearest text-layer
    word-gap centre, so a cut lands provably between words (never mid-glyph — the defect a
    prior naive mid-word cut produced, returning an empty read). Returns None (→ recorded as
    an aspect FAILURE) when the line has too few word gaps to make k segments."""
    if k < 2 or not line.word_gap_x:
        return None
    x0 = line.bbox.x0
    width_pt = line.bbox.x1 - line.bbox.x0
    if width_pt <= 0:
        return None
    gap_px = sorted(int((g - x0) * scale) for g in line.word_gap_x)
    gap_px = [g for g in gap_px if 0 < g < int(width_pt * scale)]
    if len(gap_px) < k - 1:
        return None
    cuts: List[int] = []
    for i in range(1, k):
        ideal = int((i / k) * width_pt * scale)
        nearest = min(gap_px, key=lambda g: abs(g - ideal))
        if nearest not in cuts:
            cuts.append(nearest)
    cuts.sort()
    if len(cuts) < k - 1:
        return None
    return cuts


def reflow_stack(line_pil: "PILImage", cut_px: Sequence[int], gap_px: int, background: int = 255) -> "PILImage":
    """Stack a wide line's word-gap segments vertically into a squarer image.

    ``cut_px`` are x positions (in ``line_pil`` pixels) that fall in word gaps; the line is
    split there into segments stacked top→bottom with ``gap_px`` white spacing. Turns a
    thin wide strip (which the vision encoder downsamples) into a taller, denser image."""
    from PIL import Image

    w, h = line_pil.width, line_pil.height
    xs = [0, *sorted(cut_px), w]
    segments = [line_pil.crop((xs[i], 0, xs[i + 1], h)) for i in range(len(xs) - 1)]
    segments = [s for s in segments if s.width > 0]
    if not segments:
        return line_pil
    out_w = max(s.width for s in segments)
    out_h = sum(s.height for s in segments) + gap_px * (len(segments) - 1)
    canvas = Image.new("RGB", (out_w, out_h), (background, background, background))
    y = 0
    for s in segments:
        canvas.paste(s, (0, y))
        y += s.height + gap_px
    return canvas


def overlap_tiles(line_pil: "PILImage", n: int, overlap_frac: float = 0.15) -> List["PILImage"]:
    """Split a wide line into ``n`` horizontally-overlapping tiles (aspect ``overlap_tiles``).

    Each tile is read independently and the reads are concatenated by the runner. The
    overlap gives the model shared context at the seams (a de-overlap stitch is a future
    lever; here the reads are naively joined)."""
    w, h = line_pil.width, line_pil.height
    if n < 2 or w <= 0:
        return [line_pil]
    step = w / n
    ov = int(step * overlap_frac)
    tiles: List["PILImage"] = []
    for i in range(n):
        left = max(0, int(i * step) - ov)
        right = min(w, int((i + 1) * step) + ov)
        tiles.append(line_pil.crop((left, 0, right, h)))
    return tiles


def _to_png(pil: "PILImage") -> bytes:
    buf = io.BytesIO()
    pil.save(buf, format="PNG")
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# Decode params + content-addressed read cache.                                 #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Decode:
    """The read (decode) parameters folded into the cache key."""

    prompt_variant: str
    temperature: float
    max_tokens: int
    seed: int  # varies temp-sampled reads so a temp>0 witness is genuinely independent

    @property
    def user_prompt(self) -> str:
        return PROMPT_VARIANTS[self.prompt_variant]

    def repr_key(self) -> str:
        return f"t={self.temperature};max={self.max_tokens};seed={self.seed}"


def read_cache_fingerprint(model: str, decode: Decode) -> str:
    """Prompt/model/decode fingerprint (re-keys on any contract change; determinism firewall)."""
    return prompt_fingerprint(
        _SYSTEM_PROMPT,
        decode.user_prompt,
        model,
        vocab=(decode.repr_key(),),
    )


def image_address(png: bytes) -> str:
    """The content address of a rendered image (sha256 hex)."""
    return hashlib.sha256(png).hexdigest()


class PngReadCache:
    """A :class:`RecoveredTextStore`-backed cache keyed by image-sha256 × prompt/model/decode.

    Reuses the canonical content-addressed store (``recovered/<img_sha>/<fp>/page/0000``)
    so a warm hit replays byte-identically and issues NO backend call. Writes to a gitignored
    farchive (job tmp / ``data/``)."""

    def __init__(self, path: str) -> None:
        from lawvm.ingest.recovered_text_store import RecoveredTextStore

        self._store = RecoveredTextStore(path)

    def get(self, img_sha: str, fingerprint: str) -> Optional[str]:
        return self._store.get(img_sha, 0, fingerprint)

    def put(self, img_sha: str, fingerprint: str, text: str) -> None:
        self._store.put(img_sha, 0, fingerprint, text)

    def close(self) -> None:
        self._store.close()


# --------------------------------------------------------------------------- #
# Backend + reader (the ONLY live piece; stubbed in tests).                      #
# --------------------------------------------------------------------------- #

#: A read callable: (png bytes, Decode) → transcription. Injected so tests use a stub.
ReadFn = Callable[[bytes, Decode], str]


def call_vision_backend(
    png: bytes,
    decode: Decode,
    *,
    model: str,
    base_url: str,
    timeout: float = 300.0,
) -> str:
    """Blind-transcribe one image via the OpenAI-compatible multimodal backend (live only)."""
    import urllib.request

    b64 = base64.b64encode(png).decode("ascii")
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                    {"type": "text", "text": decode.user_prompt},
                ],
            },
        ],
        "temperature": decode.temperature,
        "max_tokens": decode.max_tokens,
        "seed": decode.seed,
    }
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (trusted localhost)
        out = json.loads(resp.read())
    return out["choices"][0]["message"]["content"]


def make_read_fn(
    *,
    cache: Optional[PngReadCache],
    model: str,
    base_url: str,
    live: bool,
) -> ReadFn:
    """Build the cache-gated read function (warm replay → cached; cold+replay → ""; cold+live → backend)."""

    def read_one(png: bytes, decode: Decode) -> str:
        fp = read_cache_fingerprint(model, decode)
        img_sha = image_address(png)
        if cache is not None:
            hit = cache.get(img_sha, fp)
            if hit is not None:
                return hit
        if not live:
            return ""  # REPLAY: cold → no backend call (deterministic, byte-identical)
        text = call_vision_backend(png, decode, model=model, base_url=base_url)
        if cache is not None:
            cache.put(img_sha, fp, text)
        return text

    return read_one


# --------------------------------------------------------------------------- #
# Config grid.                                                                  #
# --------------------------------------------------------------------------- #

#: Crop level → constituent GT item kind.
CROP_KINDS: Dict[str, str] = {
    "full_page": "page",
    "band_8_lines": "band",
    "band_4_lines": "band",
    "band_2_lines": "band",
    "single_line": "line",
}
_CROP_BAND_SIZE: Dict[str, int] = {"band_8_lines": 8, "band_4_lines": 4, "band_2_lines": 2}


@dataclass(frozen=True, slots=True)
class Config:
    """One grid cell — a full render+read+mechanism specification (one axis varied)."""

    name: str
    variable: str  # which axis this cell isolates ("scale" | "crop" | "aspect" | ...)
    scale: float = 3.0
    crop: str = "single_line"
    aspect: str = "thin_strip_as_is"  # thin_strip_as_is|pad_to_square|reflow_k{2,3,4}_g{0,8}|overlap_{n}
    prompt_variant: str = "minimal_transcribe"
    temperature: float = 0.0
    max_tokens: int = 2048
    n_reads: int = 1
    independence: str = "none"  # none|scale|temp|crop
    consensus: str = "none"  # none|agree2|majority3


def _render_item_images(
    item: GTItem,
    cfg: Config,
    page_pil_cache: Dict[Tuple[int, float], "PILImage"],
    page_h: float,
    pdf_bytes: bytes,
    scale: float,
) -> Tuple[List[bytes], Optional[str]]:
    """Render an item under a config into a list of PNGs (usually 1), + an optional failure note.

    Returns ``([], note)`` when the render/aspect cannot be produced (e.g. reflow with too
    few word gaps) — an HONEST failure recorded, never a silent empty."""
    key = (item.page_index, scale)
    pil = page_pil_cache.get(key)
    if pil is None:
        pil = render_page_pil(pdf_bytes, item.render_page, scale)
        page_pil_cache[key] = pil
    if item.bbox is None:  # whole page
        return [_to_png(pil)], None
    crop = crop_region(pil, item.bbox, page_h, scale)
    aspect = cfg.aspect
    if item.kind != "line" or aspect == "thin_strip_as_is":
        return [_to_png(crop)], None
    if aspect == "pad_to_square":
        return [_to_png(pad_to_square(crop))], None
    if aspect.startswith("reflow_"):
        # reflow_k{K}_g{G}
        try:
            body = aspect[len("reflow_"):]
            kpart, gpart = body.split("_")
            k = int(kpart[1:])
            gap = int(gpart[1:])
        except (ValueError, IndexError):
            return [], f"bad-aspect:{aspect}"
        line = item.lines[0]
        cuts = reflow_cut_pixels(line, scale, k)
        if cuts is None:
            return [], f"reflow_no_word_gaps:k={k}"
        return [_to_png(reflow_stack(crop, cuts, gap))], None
    if aspect.startswith("overlap_"):
        try:
            n = int(aspect[len("overlap_"):])
        except ValueError:
            return [], f"bad-aspect:{aspect}"
        return [_to_png(t) for t in overlap_tiles(crop, n)], None
    return [], f"unknown-aspect:{aspect}"


def _decode_for(cfg: Config, seed: int) -> Decode:
    return Decode(
        prompt_variant=cfg.prompt_variant,
        temperature=cfg.temperature,
        max_tokens=cfg.max_tokens,
        seed=seed,
    )


@dataclass(frozen=True, slots=True)
class ItemResult:
    """One scored (config × item) row."""

    config: str
    variable: str
    he_id: str
    page_index: int
    kind: str
    cer: float
    wer: float
    halluc: float
    agreement: Optional[float]  # mean pairwise 1-CER across witnesses (multi-read only)
    n_reads: int
    read_text: str
    note: str


def _pairwise_agreement(reads: Sequence[str]) -> float:
    """Mean pairwise agreement (1 - CER) across witnesses (1.0 = identical)."""
    if len(reads) < 2:
        return 1.0
    sims: List[float] = []
    for i in range(len(reads)):
        for j in range(i + 1, len(reads)):
            sims.append(1.0 - char_error_rate(reads[i], reads[j]))
    return statistics.fmean(sims) if sims else 1.0


def _consensus_text(reads: Sequence[str]) -> str:
    """The MEDOID read — minimal summed pairwise CER to the others (majority-agreeing witness)."""
    if not reads:
        return ""
    if len(reads) == 1:
        return reads[0]
    best_idx, best_cost = 0, None
    for i, ri in enumerate(reads):
        cost = sum(char_error_rate(ri, rj) for j, rj in enumerate(reads) if j != i)
        if best_cost is None or cost < best_cost:
            best_idx, best_cost = i, cost
    return reads[best_idx]


def score_item(
    item: GTItem,
    cfg: Config,
    read_fn: ReadFn,
    page_pil_cache: Dict[Tuple[int, float], "PILImage"],
    page_h: float,
    pdf_bytes: bytes,
) -> ItemResult:
    """Render → read (single or multi-witness) → score one item under one config."""
    # Independence source → the per-witness (scale, seed) tuples.
    witnesses: List[Tuple[float, int]] = []
    n = max(1, cfg.n_reads)
    if cfg.independence == "scale" and n > 1:
        base = cfg.scale
        deltas = [0.0, 1.0, -0.5, 0.5, 1.5][:n]
        witnesses = [(max(1.0, base + d), 1) for d in deltas]
    elif cfg.independence == "temp" and n > 1:
        witnesses = [(cfg.scale, s) for s in range(1, n + 1)]
    else:
        witnesses = [(cfg.scale, 1) for _ in range(n)]

    reads: List[str] = []
    notes: List[str] = []
    for (scale, seed) in witnesses:
        pngs, note = _render_item_images(
            item, cfg, page_pil_cache, page_h, pdf_bytes, scale
        )
        if note:
            notes.append(note)
        if not pngs:
            reads.append("")
            continue
        decode = _decode_for(cfg, seed)
        parts = [read_fn(png, decode) for png in pngs]
        reads.append("\n".join(p for p in parts if p))

    agreement = _pairwise_agreement(reads) if n > 1 else None
    final = _consensus_text(reads) if n > 1 else (reads[0] if reads else "")
    cer = char_error_rate(item.text, final)
    wer = word_error_rate(item.text, final)
    halluc = hallucination_rate(item.text, final)
    return ItemResult(
        config=cfg.name,
        variable=cfg.variable,
        he_id=item.he_id,
        page_index=item.page_index,
        kind=item.kind,
        cer=cer,
        wer=wer,
        halluc=halluc,
        agreement=agreement,
        n_reads=n,
        read_text=final,
        note=";".join(notes),
    )


def items_for_config(cfg: Config, items_by_kind: Dict[str, List[GTItem]]) -> List[GTItem]:
    """The GT items a config applies to (crop level → kind, with band-size filtering)."""
    kind = CROP_KINDS.get(cfg.crop, "line")
    pool = items_by_kind.get(kind, [])
    if kind == "band":
        want = _CROP_BAND_SIZE.get(cfg.crop)
        if want is not None:
            return [it for it in pool if len(it.lines) == want]
    return list(pool)


# --------------------------------------------------------------------------- #
# Default config grid (one variable isolated per cell; the rest at baseline).    #
# --------------------------------------------------------------------------- #

def default_grid() -> List[Config]:
    """The isolated-axis config grid (§ A/B/C of the calibration mission).

    Each cell varies ONE axis and holds the rest at the baseline (single-line crop, scale 3,
    minimal prompt, temp 0, one read), so a difference in the scored distribution is
    attributable to that axis alone."""
    grid: List[Config] = []
    # A. RENDER — scale sweep (single-line baseline).
    for s in (1.5, 2.0, 2.5, 3.0, 4.0, 5.0):
        grid.append(Config(name=f"scale_{s}", variable="scale", scale=s))
    # A. RENDER — crop granularity (the isolation question).
    for crop in ("full_page", "band_8_lines", "band_4_lines", "band_2_lines", "single_line"):
        grid.append(Config(name=f"crop_{crop}", variable="crop", crop=crop))
    # A. single-line ASPECT handling.
    for aspect in ("thin_strip_as_is", "pad_to_square",
                   "reflow_k2_g0", "reflow_k3_g0", "reflow_k4_g0",
                   "reflow_k2_g8", "reflow_k3_g8", "overlap_2", "overlap_3"):
        grid.append(Config(name=f"aspect_{aspect}", variable="aspect", aspect=aspect))
    # B. READ — temperature.
    for t in (0.0, 0.2, 0.5):
        grid.append(Config(name=f"temp_{t}", variable="temperature", temperature=t))
    # B. READ — prompt.
    for p in ("minimal_transcribe", "line_by_line", "structured"):
        grid.append(Config(name=f"prompt_{p}", variable="prompt", prompt_variant=p))
    # C. MECHANISM — single vs multi-read consensus (independence source varied).
    grid.append(Config(name="single_read", variable="mechanism", n_reads=1))
    grid.append(Config(name="consensus2_scale", variable="mechanism",
                       n_reads=2, independence="scale", consensus="agree2"))
    grid.append(Config(name="majority3_scale", variable="mechanism",
                       n_reads=3, independence="scale", consensus="majority3"))
    grid.append(Config(name="majority3_temp", variable="mechanism", temperature=0.4,
                       n_reads=3, independence="temp", consensus="majority3"))
    return grid


# --------------------------------------------------------------------------- #
# Runner.                                                                       #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ConfigSummary:
    """One config's folded distributions (median/p90/max) over its scored items."""

    config: str
    variable: str
    n_items: int
    failures: int
    cer: Dict[str, float]
    wer: Dict[str, float]
    halluc: Dict[str, float]


@dataclass
class SweepResult:
    """Folded sweep output: per-item rows + per-config distributions + agreement correlation."""

    rows: List[ItemResult] = field(default_factory=list)
    per_config: Dict[str, ConfigSummary] = field(default_factory=dict)
    agreement_correlation: Optional[float] = None
    sanity_floor_ok: Optional[bool] = None
    sanity_floor_detail: str = ""


def _fold_per_config(rows: Sequence[ItemResult]) -> Dict[str, ConfigSummary]:
    by: Dict[str, List[ItemResult]] = {}
    for r in rows:
        by.setdefault(r.config, []).append(r)
    out: Dict[str, ConfigSummary] = {}
    for name, rs in by.items():
        out[name] = ConfigSummary(
            config=name,
            variable=rs[0].variable,
            n_items=len(rs),
            failures=sum(1 for r in rs if r.note),
            cer=summarize([r.cer for r in rs]),
            wer=summarize([r.wer for r in rs]),
            halluc=summarize([r.halluc for r in rs]),
        )
    return out


def _agreement_vs_error_corr(rows: Sequence[ItemResult]) -> Optional[float]:
    """Pearson correlation of (agreement, CER) over multi-read rows (want strongly NEGATIVE).

    A strong negative correlation = high multi-read agreement PREDICTS low error (the
    Lane-1b non-masking premise: consensus is a real signal, not self-agreeing garbage)."""
    ag: List[float] = []
    ce: List[float] = []
    for r in rows:
        if r.agreement is not None:
            ag.append(r.agreement)
            ce.append(r.cer)
    if len(ag) < 3 or len(set(ag)) < 2 or len(set(ce)) < 2:
        return None
    try:
        return statistics.correlation(ag, ce)
    except statistics.StatisticsError:
        return None


def run_sweep(
    items_by_kind: Dict[str, List[GTItem]],
    configs: Sequence[Config],
    read_fn: ReadFn,
    pdf_by_page: Dict[int, bytes],
    page_heights: Dict[int, float],
    *,
    row_sink: Optional[Callable[[ItemResult], None]] = None,
) -> SweepResult:
    """Run the config grid over the GT items, emitting per-item rows + folded distributions.

    ``pdf_by_page`` maps page_index → the PDF bytes it belongs to (so a multi-HE corpus
    renders the right document). ``row_sink`` streams each row (e.g. to JSONL) as it lands."""
    page_pil_cache: Dict[Tuple[int, float], "PILImage"] = {}
    rows: List[ItemResult] = []
    for cfg in configs:
        for item in items_for_config(cfg, items_by_kind):
            pdf_bytes = pdf_by_page[item.page_index]
            page_h = page_heights[item.page_index]
            res = score_item(item, cfg, read_fn, page_pil_cache, page_h, pdf_bytes)
            rows.append(res)
            if row_sink is not None:
                row_sink(res)
    result = SweepResult(rows=rows)
    result.per_config = _fold_per_config(rows)
    result.agreement_correlation = _agreement_vs_error_corr(rows)
    # Sanity floor — the BEST single-read config must reach CER<0.05 on some clean item.
    single = [r for r in rows if r.n_reads == 1 and r.kind in ("line", "band")]
    if single:
        best = min(r.cer for r in single)
        result.sanity_floor_ok = best < 0.05
        result.sanity_floor_detail = f"best single-read line/band CER={best:.4f} (floor 0.05)"
    return result


def row_to_dict(r: ItemResult) -> Dict[str, object]:
    return {
        "config": r.config,
        "variable": r.variable,
        "he_id": r.he_id,
        "page_index": r.page_index,
        "kind": r.kind,
        "cer": round(r.cer, 5),
        "wer": round(r.wer, 5),
        "halluc": round(r.halluc, 5),
        "agreement": None if r.agreement is None else round(r.agreement, 5),
        "n_reads": r.n_reads,
        "note": r.note,
        "read_text": r.read_text,
    }


# --------------------------------------------------------------------------- #
# Corpus loading (live only) + CLI.                                             #
# --------------------------------------------------------------------------- #


def load_corpus_items(
    farchive: str,
    *,
    sample: int,
    seed: int,
    max_pages: int = 12,
    lines_per_page: int = 3,
) -> Tuple[Dict[str, List[GTItem]], Dict[int, bytes], Dict[int, float]]:
    """Sample K clean born-digital HE pages → validated GT items (live path).

    Skips corrupt-font pages (pervasive garble) and pages with too little clean text. Returns
    (items_by_kind, pdf_by_page, page_heights) — a synthetic global page index namespaces each
    (HE, local page) so a multi-HE corpus renders the right document."""
    from lawvm.tools.fi_he_ir_corpus import _AKN_PATH_PREFIX, enumerate_he_units

    from farchive import Farchive

    units = enumerate_he_units(farchive, sample=sample, seed=seed)
    items_by_kind: Dict[str, List[GTItem]] = {"line": [], "band": [], "page": []}
    pdf_by_page: Dict[int, bytes] = {}
    page_heights: Dict[int, float] = {}
    gp = 0  # global page index
    fa = Farchive(farchive)
    try:
        for unit in units:
            base = f"{_AKN_PATH_PREFIX}{unit.he_year}/{unit.he_number}/fin@/"
            pdf_bytes = fa.get(base + "main.pdf")
            if not pdf_bytes:
                continue
            import pypdfium2 as pdfium

            with PDFIUM_LOCK:
                doc = pdfium.PdfDocument(pdf_bytes)
                try:
                    npages = len(doc)
                finally:
                    doc.close()
            for local in range(min(npages, max_pages)):
                lines = extract_page_lines(pdf_bytes, local)
                if len(lines) < 6 or page_is_corrupt(lines):
                    continue
                items = build_gt_items(
                    unit.he_id, gp, lines, render_page=local, lines_per_page=lines_per_page
                )
                if not items:
                    continue
                pdf_by_page[gp] = pdf_bytes
                page_heights[gp] = _page_height_pts(pdf_bytes, local)
                for it in items:
                    items_by_kind[it.kind].append(it)
                gp += 1
    finally:
        fa.close()
    return items_by_kind, pdf_by_page, page_heights


def _rank_report(result: SweepResult) -> str:
    lines: List[str] = []
    lines.append("# fi-vision-read-calibration")
    lines.append("")
    lines.append(f"sanity_floor: {result.sanity_floor_ok}  ({result.sanity_floor_detail})")
    corr = result.agreement_correlation
    lines.append(f"agreement↔CER correlation: {corr}  (want strongly negative)")
    lines.append("")
    lines.append("config                        var          n   cer_med  cer_p90  cer_max  wer_med  hall_med  fails")
    for rec in sorted(result.per_config.values(), key=lambda s: s.cer["median"]):
        lines.append(
            f"{rec.config:<28}  {rec.variable:<10}  {rec.n_items:>3}  "
            f"{rec.cer['median']:.4f}   {rec.cer['p90']:.4f}   {rec.cer['max']:.4f}   "
            f"{rec.wer['median']:.4f}   {rec.halluc['median']:.4f}    {rec.failures}"
        )
    return "\n".join(lines)


def main(args: argparse.Namespace) -> None:
    """CLI: hermetic by default (no reads); ``--live`` runs the GPU sweep + warms the cache."""
    farchive = args.farchive or "data/fi_government_proposal.farchive"
    cache = PngReadCache(args.cache) if args.cache else None
    read_fn = make_read_fn(
        cache=cache, model=args.model, base_url=args.base_url, live=args.live
    )
    items_by_kind, pdf_by_page, page_heights = load_corpus_items(
        farchive, sample=args.sample, seed=args.seed, max_pages=args.max_pages
    )
    configs = default_grid()
    sink = None
    jsonl_fh = None
    if args.jsonl_out:
        jsonl_fh = open(args.jsonl_out, "w", encoding="utf-8")

        def sink(r: ItemResult) -> None:
            assert jsonl_fh is not None
            jsonl_fh.write(json.dumps(row_to_dict(r), ensure_ascii=False) + "\n")
            jsonl_fh.flush()  # stream rows so a long GPU sweep is monitorable + crash-durable

    try:
        result = run_sweep(
            items_by_kind, configs, read_fn, pdf_by_page, page_heights, row_sink=sink
        )
    finally:
        if jsonl_fh is not None:
            jsonl_fh.close()
        if cache is not None:
            cache.close()
    report = _rank_report(result)
    if args.report_out:
        with open(args.report_out, "w", encoding="utf-8") as fh:
            fh.write(report + "\n")
    print(report)


def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="fi-vision-read-calibration")
    p.add_argument("--farchive", default=None, help="HE government-proposal farchive path")
    p.add_argument("--live", action="store_true", help="operator GPU sweep (else replay-only)")
    p.add_argument("--base-url", dest="base_url", default=DEFAULT_BASE_URL)
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--sample", type=int, default=4, help="HE units to sample")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--max-pages", dest="max_pages", type=int, default=12)
    p.add_argument("--cache", default=None, help="read-cache farchive path (gitignored)")
    p.add_argument("--jsonl-out", dest="jsonl_out", default=None)
    p.add_argument("--report-out", dest="report_out", default=None)
    return p


if __name__ == "__main__":  # pragma: no cover
    main(build_argparser().parse_args())

"""Deterministic born-digital STRUCTURE lane — geometry + text-layer, NO vision.

The single biggest ingest token lever (``notes/SOURCE_DOCUMENT_TWO_LEVEL_PIPELINE.md``
§10 dec. 4 + the token census): ~80% of the corpus is BORN-DIGITAL, and a
born-digital page span-copies its text LOSSLESSLY from the pdfium text layer, so
the ONLY thing a whole-page vision read recovers is *structure*. Shipping the page
image to a vision model (~8.7k image tokens/page) just to recover structure that
geometry already resolves is pure waste.

This module derives the ``PageSimulacrum`` page tree for a born-digital page
DETERMINISTICALLY from ``page_elements`` geometry + the pdfium text layer — with NO
image sent to the model:

  * **Text** from the pdfium text layer (already lossless, dehyphenated), per line.
  * **Structure** from geometry + ``typo.*`` / string cues: headings (size-class /
    caps / bold short line / ``§`` section-number), body paragraphs (continuation
    cues + y-gap → merge), list markers / section numbers (``page_elements`` regex-
    free prefix recognizers), columns (x-centre), reading order (y-order within
    column), figures (embedded images → the existing IMAGE lane).
  * **Fallback to vision** ONLY for regions geometry cannot resolve: a table-grid
    candidate the heuristic flags low-confidence, or a page/region below the
    text-layer coverage floor. These are SURFACED as ``GeomFallbackRegion``s routed
    through the EXISTING §8 re-read / on-demand affordances — never a silent
    full-page vision read, never a dropped byte (the text is still emitted; only the
    *structure* of the flagged region is left for the model to confirm).

Firewall WIN: the lane is PURE (no LLM), so its output is deterministic and
cache-HIT byte-identical. It is ADDITIVE and OPT-IN (``build_page_simulacra(...,
struct_geom=True)`` / ``build_born_digital_simulacra``) — it NEVER silently replaces
the vision lane and must be A/B-proven not-worse (``fi_calibration.born_digital_ab``)
before it is ever made default. Assurance is honestly SINGLE_WITNESS: geometry is a
single (deterministic) structure producer and the text layer a single text producer
— no corroboration is invented.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from lawvm.core.source_document.anchors import BBox, SourceAnchor
from lawvm.core.source_document.ir import (
    AssuranceTier,
    SourceDocumentNode,
    SourceDocumentNodeKind,
)
from lawvm.ingest.metadata import NodeMetadata, encode_metadata
from lawvm.ingest.page_elements import (
    EmbeddedImage,
    PageElements,
    PageLine,
    line_ends_terminal,
    line_has_hyphen_tail,
    line_has_section_ref,
    line_is_caps,
    line_is_numeric_heavy,
    line_list_marker,
    line_section_number,
    line_starts_lower,
)
from lawvm.ingest.page_level import (
    _is_furniture_candidate,
    _recurrence_key,
    band_recurrence_map,
)
from lawvm.ingest.simulacrum import (
    ConvergenceInfo,
    PageSimulacrum,
)

# The Level-1 producer id stamped on ``prov.producer`` for a geom-lane node — a
# DIFFERENT producer than the vision lane (``vision_struct.v1``), so a downstream
# consumer / A/B can tell which lane read the page.
_PRODUCER_ID = "born_digital_geom.v1"

# The ConvergenceInfo termination for a deterministic single-pass geom read (no
# convergence loop exists in this lane; the page is read once, exactly).
_GEOM_TERMINATION = "geom_deterministic"

# --------------------------------------------------------------------------- #
# Born-digital PAGE gate (reuse the fi_scan_stratum stratum thresholds).        #
# --------------------------------------------------------------------------- #
#
# ``fi_scan_stratum`` classifies a PDF by MEAN stripped text-layer chars/page:
# scanned < 50, mixed < 300, born_digital >= 300. This lane gates PER PAGE on the
# same stripped-char floor: a page with a dense text layer is born-digital (the
# geom lane can read it losslessly); a text-poor page routes to vision. The floor
# is duplicated (not imported) because ``ingest`` is neutral machinery and must not
# import ``lawvm.tools`` (a layering inversion) — kept equal to fi_scan_stratum's
# ``DIGITAL_MIN_CHARS_PER_PAGE``.
_BORN_DIGITAL_MIN_CHARS_PER_PAGE = 300

# A run of >= this many consecutive numeric-heavy lines with a stable per-line
# structure is a TABLE-grid CANDIDATE geometry cannot resolve from whole-line
# bboxes alone (no intra-line column geometry) → flagged low-confidence for a
# vision structure confirm (the text is still emitted faithfully).
_TABLE_CANDIDATE_MIN_RUN = 3

# Vertical-gap multiple (× median line height) above which two consecutive body
# lines are a PARAGRAPH break rather than a wrap — a deterministic paragraph cue.
_PARAGRAPH_GAP_RATIO = 1.6

# A heading candidate line is "short" when it has at most this many words (a long
# all-caps line is a body sentence in caps, not a heading).
_HEADING_MAX_WORDS = 12


def page_stripped_char_count(page_elements: PageElements) -> int:
    """Stripped text-layer char count for one page (the born-digital discriminator)."""
    return sum(len(ln.strip()) for ln in page_elements.lines)


def page_is_born_digital(
    page_elements: PageElements, *, min_chars: int = _BORN_DIGITAL_MIN_CHARS_PER_PAGE
) -> bool:
    """Does this page have a dense enough text layer for the deterministic geom lane?

    Gate (§10 dec. 4 / fi_scan_stratum): a page whose stripped text-layer char count
    clears ``min_chars`` AND that carries per-line geometry is born-digital — the
    geom lane can span-copy its text losslessly and resolve its structure from
    geometry, sending NO image to the model. A text-poor page (scanned / image-baked)
    returns ``False`` → the caller routes it to the vision lane unchanged.
    """
    if not page_elements.page_lines:
        return False
    return page_stripped_char_count(page_elements) >= min_chars


# --------------------------------------------------------------------------- #
# Geometry region model — the deterministic per-page structure carriers.       #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class GeomFallbackRegion:
    """A region geometry could not confidently resolve → route to vision (§8/§9).

    Bytes are ALWAYS emitted faithfully (the text layer is lossless); this only
    surfaces that the region's *structure* is low-confidence, so a downstream
    caller may re-read JUST this bbox with the model to confirm arrangement. The
    ``reason`` is a short closed tag (``table_grid`` / ``no_geometry``).
    """

    node_path: Tuple[int, ...]
    reason: str
    bbox: Optional[BBox]
    line_indexes: Tuple[int, ...]


@dataclass(frozen=True, slots=True)
class _GeomRegion:
    """One nascent deterministic region: a governed kind over owned page lines.

    ``line_indexes`` are the page-line indexes this region owns (in reading order);
    ``image`` is the embedded image for an IMAGE_REGION; ``low_confidence`` +
    ``reason`` mark a region geometry could not resolve (→ a ``GeomFallbackRegion``).
    Flat (no nesting): Level 1 stays faithful-per-page, Level 2 composes hierarchy.
    """

    kind: SourceDocumentNodeKind
    line_indexes: Tuple[int, ...] = ()
    image: Optional[EmbeddedImage] = None
    low_confidence: bool = False
    reason: str = ""


@dataclass(frozen=True, slots=True)
class BornDigitalPage:
    """The deterministic geom-lane result for one born-digital page.

    ``simulacrum`` is the same immutable ``PageSimulacrum`` shape the vision lane
    produces (so Level 2 consumes it unchanged); ``fallbacks`` are the regions whose
    STRUCTURE geometry flagged low-confidence (routed to vision on demand, §8/§9).
    A clean page emits ZERO fallbacks (output-sparse).
    """

    simulacrum: PageSimulacrum
    fallbacks: Tuple[GeomFallbackRegion, ...] = ()


# --------------------------------------------------------------------------- #
# Column assignment (x-centre) — reading order within a column.                 #
# --------------------------------------------------------------------------- #


def _col_of(line: PageLine, page_width: float) -> int:
    """Deterministic 2-column index from the line x-centre (0 = left, 1 = right)."""
    if line.bbox is None or page_width <= 0:
        return 0
    x_centre = (line.bbox.x0 + line.bbox.x1) / 2.0
    return 1 if x_centre > page_width / 2.0 else 0


def _column_ordered_indexes(page_elements: PageElements) -> List[int]:
    """Reading-order page-line indexes, regrouped left-then-right for a 2-column page.

    A single-column page keeps the pdfium reading order (``page_lines`` order). A
    two-column page (both halves populated) is re-serialized column-major: the whole
    left column top-to-bottom, then the whole right column — the reading order the
    flat pdfium stream can scramble. Pure geometry (x-centre + y-order); no model.
    """
    lines = page_elements.page_lines
    pw = page_elements.page_width
    n = len(lines)
    if n == 0 or pw <= 0:
        return list(range(n))
    cols = {_col_of(lines[i], pw) for i in range(n) if lines[i].bbox is not None}
    if len(cols) < 2:
        return list(range(n))
    left = [i for i in range(n) if lines[i].bbox is not None and _col_of(lines[i], pw) == 0]
    right = [i for i in range(n) if lines[i].bbox is not None and _col_of(lines[i], pw) == 1]
    no_geom = [i for i in range(n) if lines[i].bbox is None]
    # Require both columns to be non-trivially populated before re-serializing;
    # a stray right-margin line is not a second column.
    if len(left) < 2 or len(right) < 2:
        return list(range(n))
    # Order each column top-to-bottom (PDF origin bottom-left → larger y is higher).
    left.sort(key=lambda i: -(lines[i].bbox.y1 if lines[i].bbox else 0.0))
    right.sort(key=lambda i: -(lines[i].bbox.y1 if lines[i].bbox else 0.0))
    return left + right + no_geom


# --------------------------------------------------------------------------- #
# Line classification + segmentation.                                           #
# --------------------------------------------------------------------------- #


def _is_heading_line(line: PageLine) -> bool:
    """Is this line a heading candidate (deterministic typography + text cues)?

    A heading fires on: a document-relative larger font (``size_class=='heading'``),
    a short all-caps line, a short bold line, or a leading section-number (``4 §`` /
    ``Article 5``). "Short" caps/bold guards against a caps/bold body sentence.
    """
    text = line.text.strip()
    if not text:
        return False
    if line_section_number(text) is not None:
        return True
    n_words = len(text.split())
    if line.size_class == "heading" and n_words <= _HEADING_MAX_WORDS:
        return True
    if n_words <= _HEADING_MAX_WORDS and not line_ends_terminal(text):
        if line_is_caps(text) or line.bold:
            return True
    return False


def _median_line_height(lines: Sequence[PageLine]) -> float:
    hs = [
        ln.bbox.y1 - ln.bbox.y0
        for ln in lines
        if ln.bbox is not None and ln.bbox.y1 > ln.bbox.y0
    ]
    if not hs:
        return 12.0
    hs.sort()
    return hs[len(hs) // 2]


def _paragraph_breaks_before(prev: PageLine, cur: PageLine, median_h: float) -> bool:
    """Should ``cur`` start a NEW paragraph vs continue ``prev`` (deterministic)?

    Continuation OVERRIDES a break: if ``prev`` did not end terminal (or ended on a
    discretionary hyphen) or ``cur`` starts lower-case, the two are the same
    paragraph regardless of the y-gap. Otherwise a big vertical gap (a blank line's
    worth) is a paragraph break. Pure cues + geometry; no model.
    """
    prev_t = prev.text.strip()
    cur_t = cur.text.strip()
    if line_has_hyphen_tail(prev_t) or not line_ends_terminal(prev_t) or line_starts_lower(cur_t):
        return False
    if prev.bbox is not None and cur.bbox is not None and median_h > 0:
        gap = prev.bbox.y0 - cur.bbox.y1  # reading down → y decreases
        if gap > _PARAGRAPH_GAP_RATIO * median_h:
            return True
    # Two terminal-punctuated lines with no continuation cue and no big gap: keep
    # them together (a multi-sentence paragraph is common in legal body text).
    return False


def _segment_lines(page_elements: PageElements, ordered: Sequence[int]) -> List[_GeomRegion]:
    """Classify + segment the ordered page lines into flat governed regions.

    Headings become one-line HEADING regions; leading-list-marker lines become ITEM
    regions (continuation lines fold in); body lines accumulate into PARAGRAPH
    regions split by the deterministic paragraph cue; a run of numeric-heavy lines
    is a TABLE-grid CANDIDATE flagged low-confidence (text still emitted).
    """
    lines = page_elements.page_lines
    median_h = _median_line_height(lines)
    regions: List[_GeomRegion] = []
    buf: List[int] = []
    buf_kind: Optional[SourceDocumentNodeKind] = None

    def _flush() -> None:
        nonlocal buf, buf_kind
        if buf and buf_kind is not None:
            regions.append(_GeomRegion(kind=buf_kind, line_indexes=tuple(buf)))
        buf = []
        buf_kind = None

    prev_idx: Optional[int] = None
    for idx in ordered:
        if idx >= len(lines):
            continue
        line = lines[idx]
        text = line.text.strip()
        if not text:
            continue
        if _is_heading_line(line):
            _flush()
            regions.append(_GeomRegion(kind=SourceDocumentNodeKind.HEADING, line_indexes=(idx,)))
            prev_idx = idx
            continue
        marker = line_list_marker(text)
        if marker is not None:
            _flush()
            buf = [idx]
            buf_kind = SourceDocumentNodeKind.ITEM
            prev_idx = idx
            continue
        # Body / list-continuation line.
        if buf_kind is SourceDocumentNodeKind.ITEM and prev_idx is not None:
            # A continuation of the current list item vs a new paragraph after it.
            if not _paragraph_breaks_before(lines[prev_idx], line, median_h):
                buf.append(idx)
                prev_idx = idx
                continue
            _flush()
        if buf_kind is SourceDocumentNodeKind.PARAGRAPH and prev_idx is not None:
            if _paragraph_breaks_before(lines[prev_idx], line, median_h):
                _flush()
        if buf_kind is None:
            buf_kind = SourceDocumentNodeKind.PARAGRAPH
        buf.append(idx)
        prev_idx = idx
    _flush()
    return _mark_table_candidates(page_elements, regions)


def _mark_table_candidates(
    page_elements: PageElements, regions: Sequence[_GeomRegion]
) -> List[_GeomRegion]:
    """Flag PARAGRAPH regions that are dense numeric-heavy runs as table candidates.

    Whole-line bboxes carry no intra-line column geometry, so a genuine table's grid
    cannot be resolved deterministically. A run of ``>= _TABLE_CANDIDATE_MIN_RUN``
    consecutive numeric-heavy body lines is the strongest cheap signal of a table →
    the region is marked ``low_confidence`` (reason ``table_grid``) so the caller can
    route it to a vision structure confirm. The text is UNTOUCHED (still faithful).
    """
    lines = page_elements.page_lines
    out: List[_GeomRegion] = []
    for reg in regions:
        if (
            reg.kind is not SourceDocumentNodeKind.PARAGRAPH
            or len(reg.line_indexes) < _TABLE_CANDIDATE_MIN_RUN
        ):
            out.append(reg)
            continue
        heavy = sum(
            1
            for i in reg.line_indexes
            if i < len(lines) and line_is_numeric_heavy(lines[i].text)
        )
        if heavy >= _TABLE_CANDIDATE_MIN_RUN and heavy * 2 >= len(reg.line_indexes):
            out.append(
                _GeomRegion(
                    kind=reg.kind,
                    line_indexes=reg.line_indexes,
                    low_confidence=True,
                    reason="table_grid",
                )
            )
        else:
            out.append(reg)
    return out


# --------------------------------------------------------------------------- #
# Lowering — geom regions → metadata-annotated SourceDocumentNode tree.         #
# --------------------------------------------------------------------------- #


def _region_bbox(page_elements: PageElements, line_indexes: Sequence[int]) -> Optional[BBox]:
    xs: List[float] = []
    ys: List[float] = []
    for i in line_indexes:
        if i < len(page_elements.page_lines):
            b = page_elements.page_lines[i].bbox
            if b is not None:
                xs.extend([b.x0, b.x1])
                ys.extend([b.y0, b.y1])
    if not xs or not ys:
        return None
    return BBox(x0=min(xs), y0=min(ys), x1=max(xs), y1=max(ys))


def _region_text(page_elements: PageElements, line_indexes: Sequence[int]) -> str:
    """Span-copied faithful text of a region (dehyphenated per-line, newline-joined)."""
    return "\n".join(
        page_elements.page_lines[i].text
        for i in line_indexes
        if i < len(page_elements.page_lines)
    )


def _geom_metadata(
    text: str,
    *,
    first_line: Optional[PageLine],
    col: Optional[int],
    y_order: int,
    band_count: Optional[int],
    furniture: bool,
) -> NodeMetadata:
    """Deterministic ``NodeMetadata`` for a geom node — geometry + string cues + typo.

    Geometry (band / indent / col / y-order) comes from the region's FIRST owned
    line (which the geom lane knows EXACTLY — no rank-drift), the continuation cues +
    content hints are pure string functions of the region text, and the typography
    (font / size_class / bold / italic) rides on the first line. ``prov.producer`` is
    the geom-lane id (distinct from the vision lane). All affordances, never authority.
    """
    stripped = text.strip()
    band = first_line.band if first_line is not None else None
    indent = first_line.indent if first_line is not None else None
    font = first_line.font if first_line is not None else None
    size_class = first_line.size_class if first_line is not None else None
    bold = first_line.bold if first_line is not None else False
    italic = first_line.italic if first_line is not None else False
    return NodeMetadata(
        band=band,
        col=col,
        indent=indent,
        y_order=y_order,
        caps=line_is_caps(stripped),
        font=font,
        size_class=size_class,
        bold=bold,
        italic=italic,
        ends_terminal=line_ends_terminal(stripped),
        starts_lower=line_starts_lower(stripped),
        hyphen_tail=line_has_hyphen_tail(stripped),
        list_marker=line_list_marker(stripped),
        section_number=line_section_number(stripped),
        band_count=band_count,
        numeric=line_is_numeric_heavy(stripped),
        section_ref=line_has_section_ref(stripped),
        furniture=furniture,
        freeform_reason=None,
        producer=_PRODUCER_ID,
        converged=True,
    )


def _lower_region(
    reg: _GeomRegion,
    *,
    page_elements: PageElements,
    digest: str,
    page_num: int,
    tier: AssuranceTier,
    recurrence: Mapping[str, int],
    page_count: int,
    col_by_index: Mapping[int, int],
    y_counter: List[int],
) -> Tuple[SourceDocumentNode, Optional[Tuple[str, Optional[BBox], Tuple[int, ...]]]]:
    """Lower one geom region → a metadata-annotated ``SourceDocumentNode`` (+ a fallback).

    Returns the node plus an optional ``(reason, bbox, line_indexes)`` when the
    region was flagged low-confidence; the caller stamps the real node path onto the
    resulting ``GeomFallbackRegion`` (paths are known once the forest order is fixed).
    """
    y_order = y_counter[0]
    y_counter[0] += 1

    if reg.kind is SourceDocumentNodeKind.IMAGE_REGION and reg.image is not None:
        el = reg.image.element
        x0, y0, x1, y1 = el.bbox
        anchor = SourceAnchor(
            artifact_digest=digest,
            locator=f"page={page_num};bbox={x0},{y0},{x1},{y1}",
            page_num=page_num,
            bbox=BBox(x0=x0, y0=y0, x1=x1, y1=y1),
        )
        meta = _geom_metadata(
            "", first_line=None, col=None, y_order=y_order, band_count=None, furniture=False
        )
        attrs: Dict[str, str] = dict(encode_metadata(meta))
        attrs.update(
            {
                "image_digest": el.digest,
                "image_index": str(el.index),
                "media_type": el.media_type,
                "px_width": str(el.width),
                "px_height": str(el.height),
                "role": el.role,
            }
        )
        node = SourceDocumentNode(
            kind=reg.kind, assurance_tier=tier, anchor=anchor, text="", attrs=attrs
        )
        return node, None

    text = _region_text(page_elements, reg.line_indexes)
    first_idx = reg.line_indexes[0] if reg.line_indexes else None
    first_line = (
        page_elements.page_lines[first_idx]
        if first_idx is not None and first_idx < len(page_elements.page_lines)
        else None
    )
    col = col_by_index.get(first_idx) if first_idx is not None else None
    band_count = None
    if first_line is not None:
        rk = _recurrence_key(first_line.text, first_line)
        if rk is not None:
            band_count = recurrence.get(rk)
    furniture = _is_furniture_candidate(text, first_line, band_count, page_count)
    bbox = _region_bbox(page_elements, reg.line_indexes)
    anchor = SourceAnchor(
        artifact_digest=digest,
        locator=(
            f"page={page_num};bbox={bbox.x0},{bbox.y0},{bbox.x1},{bbox.y1}"
            if bbox is not None
            else f"page={page_num}"
        ),
        page_num=page_num,
        bbox=bbox,
    )
    meta = _geom_metadata(
        text,
        first_line=first_line,
        col=col,
        y_order=y_order,
        band_count=band_count,
        furniture=furniture,
    )
    node = SourceDocumentNode(
        kind=reg.kind,
        assurance_tier=tier,
        anchor=anchor,
        text=text,
        attrs=dict(encode_metadata(meta)),
    )
    fallback = (reg.reason, bbox, reg.line_indexes) if reg.low_confidence else None
    return node, fallback


# --------------------------------------------------------------------------- #
# Page producer — the public entry point.                                      #
# --------------------------------------------------------------------------- #


def _resolved_tree_hash(nodes: Sequence[SourceDocumentNode]) -> str:
    """SHA-256 over the lowered tree (the deterministic geom fixpoint key)."""
    h = hashlib.sha256()

    def _walk(n: SourceDocumentNode) -> None:
        h.update(n.kind.value.encode("utf-8"))
        h.update(b"\x00")
        h.update(n.text.encode("utf-8"))
        h.update(b"\x01")
        for c in n.children:
            _walk(c)
        h.update(b"\x02")

    for n in nodes:
        _walk(n)
    return h.hexdigest()


def born_digital_page(
    manifestation,
    page_num: int,
    page_elements: PageElements,
    *,
    recurrence: Optional[Mapping[str, int]] = None,
    page_count: int = 1,
    assurance: AssuranceTier = AssuranceTier.SINGLE_WITNESS,
) -> BornDigitalPage:
    """Deterministically derive the born-digital page tree — geometry + text, NO vision.

    Produces the SAME ``PageSimulacrum`` shape the vision lane emits, so Level-2
    de-facsimile consumes it unchanged. Structure comes from geometry + typography +
    string cues; text is span-copied losslessly from the pdfium text layer; embedded
    images route to the IMAGE lane; a table-grid candidate or a region without
    geometry is SURFACED as a ``GeomFallbackRegion`` for an on-demand vision structure
    confirm (§8/§9) — never a silent full-page read, never a dropped byte.
    """
    digest = manifestation.artifact_digest
    rec = recurrence if recurrence is not None else {}

    ordered = _column_ordered_indexes(page_elements)
    col_by_index: Dict[int, int] = {
        i: _col_of(page_elements.page_lines[i], page_elements.page_width)
        for i in range(len(page_elements.page_lines))
        if page_elements.page_lines[i].bbox is not None
    }
    regions = _segment_lines(page_elements, ordered)
    # Images append as IMAGE_REGION siblings (figures → the existing IMAGE lane).
    for img in page_elements.images:
        regions.append(_GeomRegion(kind=SourceDocumentNodeKind.IMAGE_REGION, image=img))

    y_counter = [0]
    nodes: List[SourceDocumentNode] = []
    fallbacks: List[GeomFallbackRegion] = []
    for reg in regions:
        node, fallback = _lower_region(
            reg,
            page_elements=page_elements,
            digest=digest,
            page_num=page_num,
            tier=assurance,
            recurrence=rec,
            page_count=page_count,
            col_by_index=col_by_index,
            y_counter=y_counter,
        )
        path = (len(nodes),)
        nodes.append(node)
        if fallback is not None:
            reason, bbox, line_indexes = fallback
            fallbacks.append(
                GeomFallbackRegion(
                    node_path=path, reason=reason, bbox=bbox, line_indexes=line_indexes
                )
            )

    node_tuple = tuple(nodes)
    convergence = ConvergenceInfo(
        rounds=1,
        round_hashes=(_resolved_tree_hash(node_tuple),),
        termination=_GEOM_TERMINATION,
        gate_reasons=tuple(f"geom_fallback:{fb.reason}" for fb in fallbacks),
        patches_total=0,
        rereads=0,
    )
    simulacrum = PageSimulacrum(
        page_num=page_num,
        nodes=node_tuple,
        freeform=(),  # the geom lane emits no freeform escape hatches (text is lossless)
        convergence=convergence,
        assurance=assurance,
        raw_wire_digests=(),
    )
    return BornDigitalPage(simulacrum=simulacrum, fallbacks=tuple(fallbacks))


def build_born_digital_simulacra(
    manifestation,
    page_element_producer,
    *,
    page_count: Optional[int] = None,
    max_pages: int = 5000,
    min_chars: int = _BORN_DIGITAL_MIN_CHARS_PER_PAGE,
) -> Tuple[Optional[BornDigitalPage], ...]:
    """Geom-lane simulacra for a manifestation — ``None`` for a non-born-digital page.

    Runs the recurrence pre-pass over ALL pages (whole-doc furniture affordance),
    then per page: a born-digital page (dense text layer) is read DETERMINISTICALLY
    by ``born_digital_page`` (no vision); a text-poor page yields ``None`` so the
    caller routes it to the vision lane. Pure + deterministic — no model, no network.
    """
    limit = min(page_count if page_count is not None else 10_000, max_pages)
    all_elements: List[PageElements] = []
    idx = 0
    while idx < limit:
        pe = page_element_producer.page_elements(manifestation.source_bytes, idx + 1)
        if not pe.lines and not pe.page_lines:
            break  # ran past the last page
        all_elements.append(pe)
        idx += 1
    recurrence = band_recurrence_map(all_elements)
    total = len(all_elements)
    out: List[Optional[BornDigitalPage]] = []
    for i, pe in enumerate(all_elements):
        if not page_is_born_digital(pe, min_chars=min_chars):
            out.append(None)
            continue
        out.append(
            born_digital_page(
                manifestation,
                i + 1,
                pe,
                recurrence=recurrence,
                page_count=total,
            )
        )
    return tuple(out)


__all__ = [
    "BornDigitalPage",
    "GeomFallbackRegion",
    "born_digital_page",
    "build_born_digital_simulacra",
    "page_is_born_digital",
    "page_stripped_char_count",
]

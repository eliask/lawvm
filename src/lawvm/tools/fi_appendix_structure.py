"""``lawvm fi-appendix-structure`` — appendix/table PDF → structured cell-grid IR (Phase 3 prototype).

Phase 1 (statute op-equivalence) left a TYPE-DEFERRED stratum: amendments whose
REAL content is a TABLE / appendix that neither the section-keyed authoritative
XML nor the flat ``N §`` PDF segmenter can op-compare. Two shapes recur:

- ``appendix_only`` — the XML body is a thin frame carrying an ``<a href=…pdf>``
  attachment link (e.g. 2016/1422); the substance is a born-digital table in the
  media PDF.
- ``xml_frame_only`` — the XML ``statuteTextWrapper`` is an empty ``<p
  class="omission"/>`` plus entry-into-force / signatures, and the real payload
  (e.g. 2003/917's municipality × veroluokat forest-tax table) lives entirely in
  the PDF.

This deferred stratum is the biggest remaining lever toward 100% structured law.
This tool is the PROTOTYPE: it lowers those appendix tables into a structured
cell-grid IR (rows × cols × cell text + bbox geometry) via the Docling
TableFormer producer (``lawvm.ingest.llm_backends.docling_producer``), and — the
research question — MEASURES how faithfully it can, since XML op-gold is
thin/absent here.

VERIFICATION SIGNAL (no XML op-equivalence available). Fidelity is triangulated
from three producer-neutral witnesses, never from trusting Docling:

  1. NUMERIC COMPLETENESS (recall) — every decimal/number token in the PDF's own
     text layer must reappear in the structured cells. A dropped euro amount or
     tax rate is the legally-significant failure this catches.
  2. CROSS-WITNESS — Docling's cell numbers vs the independent pypdfium2 text
     layer (born-digital) over the same document. Agreement = high confidence;
     disagreement names exactly the cells to escalate to a vision re-read.
  3. STRUCTURAL SANITY — rectangular grid, a header row identified, and the
     KNOWN Docling failure mode flagged: side-by-side dual tables merged into one
     over-wide grid (detected by a repeated header label / anomalous column count
     — e.g. 2003/917's two-up municipality columns collapse "Lääni ja kunta" into
     the same row twice).

Determinism firewall (AGENTS.md §1.3): docling and pypdfium2 are imported LAZILY
(inside functions) so this module imports offline. The pure metric functions
(``number_tokens``, ``numeric_recall``, ``structural_sanity``, ``cross_witness``)
and the node→IR lowering (``structured_table_from_node``) take plain data and are
exercised hermetically with no PDF, no docling, no network. Docling runs on CPU
(``AcceleratorDevice.CPU``) so it never contends with the :8080 vision GPU.

READ-ONLY and ADDITIVE: it never touches the derived-IR replay store; it reads
media PDFs from the finlex farchive and emits a JSONL of structured tables plus a
per-statute fidelity report. A PDF Docling cannot read is a typed record, never a
crash.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import threading
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

from lawvm.core.source_document.ir import SourceDocumentNode, SourceDocumentNodeKind
from lawvm.finland.op_equivalence import EncodingFold, text_equivalence
from lawvm.ingest.corroboration import (
    CorroborationReceipt,
    EscalationKind,
    EscalationPending,
    corroborate,
)
from lawvm.ingest.llm_backends.prompt_fingerprint import prompt_fingerprint
from lawvm.tools.fi_appendix_vision_screen import (
    GestaltRegionReader,
    RoutedVerdict,
    ScreenRoute,
    scan_garble,
    screen_and_route,
)

_FINLEX_DEFAULT = "data/finlex.farchive"

# pypdfium2 wraps pdfium, whose C state is process-global and NOT thread-safe;
# serialise the critical section (mirrors fi_scan_stratum).
_PDFIUM_LOCK = threading.Lock()

# A grid this wide on a statute appendix is a strong prior for the Docling
# dual-table merge (two side-by-side municipality columns collapsed into one).
_DUAL_MERGE_COL_THRESHOLD = 9

# Mean pdfium text-layer chars/page below this = a sparse/scanned PDF: the deterministic
# text lanes (numeric_recall, cross_witness, the text-block lane) lean on a WEAK reference,
# so the honest reader is vision/OCR. Shared by the ``text_layer_sparse`` note and the
# text-block-lane gate so both draw the born-digital / scanned boundary at the same place.
_MIN_TEXT_LAYER_CHARS = 50.0


# --------------------------------------------------------------------------- #
# PURE metric helpers (hermetically testable — no docling, no PDF, no network) #
# --------------------------------------------------------------------------- #

# A number token: an integer or a decimal, Finnish decimal-comma OR dot. Grouped
# runs (``6,5`` / ``1.234``) are one token; a bare ``§`` or letters never match.
# FLAT quantifiers only (FW-07 / AGENTS.md §1.11): a single digit then a bounded
# digit/separator run — no quantified group (which would flag as nested-
# backtracking even when bounded). A run may capture a TRAILING separator
# (``12.`` from ``12.``) which ``number_tokens`` strips.
_NUMBER_RE = re.compile(r"\d[\d.,]{0,40}")


def number_tokens(text: str) -> Tuple[str, ...]:
    """Extract numeric tokens, normalizing the decimal comma to a dot.

    Finnish statute tables write the decimal separator as a comma (``6,5``) and a
    thousands separator as a space, so ``,``→``.`` makes ``6,5`` and ``6.5``
    compare equal without conflating a thousands group. Order-preserving; a
    multiset (repeats kept) so completeness is a true count, not a set cover.
    A trailing ``.``/``,`` (sentence punctuation after the figure) is stripped so
    ``12.`` and ``12`` count identically.
    """
    return tuple(
        m.group(0).rstrip(".,").replace(",", ".")
        for m in _NUMBER_RE.finditer(text or "")
    )


@dataclass(frozen=True, slots=True)
class NumericRecall:
    """Multiset recall of the reference text's number tokens into the cells."""

    n_reference: int
    n_recovered: int
    missing: Tuple[str, ...]

    @property
    def recall(self) -> float:
        """Recovered / reference; 1.0 when the reference has no numbers (vacuous)."""
        return 1.0 if self.n_reference == 0 else self.n_recovered / self.n_reference


def numeric_recall(reference_text: str, cell_texts: Sequence[str]) -> NumericRecall:
    """Multiset recall: how many of ``reference_text``'s numbers appear in cells.

    Reference is the PDF's OWN text (its number tokens are the completeness gold —
    no external oracle needed). ``cell_texts`` is the flattened structured-cell
    text. Multiset semantics: a rate that occurs 30× in the reference must occur
    30× across the cells to score full recall; the shortfall is listed in
    ``missing`` (sorted, with multiplicity) as the escalation set.
    """
    ref = Counter(number_tokens(reference_text))
    got: Counter[str] = Counter()
    for t in cell_texts:
        got.update(number_tokens(t))
    recovered = 0
    missing: List[str] = []
    for tok, cnt in ref.items():
        have = min(cnt, got.get(tok, 0))
        recovered += have
        if have < cnt:
            missing.extend([tok] * (cnt - have))
    return NumericRecall(
        n_reference=sum(ref.values()),
        n_recovered=recovered,
        missing=tuple(sorted(missing)),
    )


@dataclass(frozen=True, slots=True)
class CrossWitness:
    """Numeric-token agreement between the Docling cells and the pdfium layer."""

    n_shared: int
    n_docling_only: int
    n_layer_only: int
    docling_only: Tuple[str, ...]
    layer_only: Tuple[str, ...]

    @property
    def agreement(self) -> float:
        """Jaccard over the number-token multisets (1.0 = full agreement)."""
        union = self.n_shared + self.n_docling_only + self.n_layer_only
        return 1.0 if union == 0 else self.n_shared / union


def cross_witness(docling_text: Sequence[str], layer_text: str) -> CrossWitness:
    """Compare the two independent witnesses' NUMBER tokens (multiset Jaccard).

    Numbers are the robust comparand: OCR letter noise (ä/ö dropouts) does not
    perturb them, and they carry the legal payload. High agreement = the two
    producers corroborate; the ``*_only`` residuals name the exact figures a
    vision re-read should adjudicate.
    """
    dcount: Counter[str] = Counter()
    for t in docling_text:
        dcount.update(number_tokens(t))
    lcount = Counter(number_tokens(layer_text))
    shared = dcount & lcount
    d_only = dcount - lcount
    l_only = lcount - dcount
    return CrossWitness(
        n_shared=sum(shared.values()),
        n_docling_only=sum(d_only.values()),
        n_layer_only=sum(l_only.values()),
        docling_only=tuple(sorted(d_only.elements())),
        layer_only=tuple(sorted(l_only.elements())),
    )


# --------------------------------------------------------------------------- #
# Structured table IR (the deliverable representation) + structural sanity      #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class StructuredCell:
    """One grid cell: position, text, header flag, and normalized bbox (points)."""

    row: int
    col: int
    text: str
    is_header: bool
    bbox: Optional[Tuple[float, float, float, float]]


@dataclass(frozen=True, slots=True)
class StructuredTable:
    """A machine-readable appendix table: rectangular cell grid + geometry."""

    locator: str
    page_num: int
    table_index: int
    n_rows: int
    n_cols: int
    caption: str
    cells: Tuple[StructuredCell, ...]

    def cell_texts(self) -> Tuple[str, ...]:
        return tuple(c.text for c in self.cells)

    def to_jsonable(self) -> Dict[str, object]:
        return {
            "locator": self.locator,
            "page_num": self.page_num,
            "table_index": self.table_index,
            "n_rows": self.n_rows,
            "n_cols": self.n_cols,
            "caption": self.caption,
            "cells": [
                {
                    "row": c.row,
                    "col": c.col,
                    "text": c.text,
                    "is_header": c.is_header,
                    "bbox": list(c.bbox) if c.bbox is not None else None,
                }
                for c in self.cells
            ],
        }


# --------------------------------------------------------------------------- #
# EXACT cell verification (the phase-3 headline — not a coverage/recall score). #
# --------------------------------------------------------------------------- #
#
# The objective forbids fuzzy/coverage scores in the headline. So a structured cell
# is VERIFIED only when a SECOND, independent witness — the pdfium text layer read
# WITHIN that cell's own bbox — reproduces the Docling cell text EXACTLY, modulo the
# SAME legally-inert quotient the op-equivalence stages use
# (:mod:`lawvm.finland.op_equivalence`). Every cell is then either ``cell_exact`` or a
# TYPED ``TableCellDivergence`` (the exact cells to escalate to a vision re-read); a
# cell with no bbox is ``cell_no_witness`` (deferred, never forced). This is the
# table analog of phase-1/2 op-equivalence: exactness, not slop.


@dataclass(frozen=True, slots=True)
class TableCellDivergence:
    """One cell where the independent bbox witness did not reproduce the Docling text.

    ``descriptor`` is the vision model's short free-text reason (what it thinks is wrong / why it
    punted) when this divergence carries a vision read (the ``escalated`` / ``open`` tie-break
    buckets); it is empty for a deterministic-lane divergence (no model consulted).
    """

    row: int
    col: int
    docling_text: str
    witness_text: str
    descriptor: str = ""


@dataclass(frozen=True, slots=True)
class TableVerification:
    """Per-table EXACT cross-witness cell verdict (Docling cells vs pdfium-in-bbox)."""

    locator: str
    page_num: int
    table_index: int
    n_cells: int
    n_exact: int
    n_no_witness: int
    divergences: Tuple[TableCellDivergence, ...]

    @property
    def exact(self) -> bool:
        """True iff EVERY witnessable cell reproduced exactly (0 typed divergences)."""
        return not self.divergences

    def to_jsonable(self) -> Dict[str, object]:
        return {
            "locator": self.locator,
            "page_num": self.page_num,
            "table_index": self.table_index,
            "n_cells": self.n_cells,
            "n_exact": self.n_exact,
            "n_no_witness": self.n_no_witness,
            "exact": self.exact,
            "divergences": [
                {"row": d.row, "col": d.col, "docling": d.docling_text, "witness": d.witness_text}
                for d in self.divergences
            ],
        }


#: A cell/block whose two decoders AGREE (A≡B modulo the inert quotient) can still carry a
#: corruption signature in its agreed text — the shared-broken-CMap path where a broken ToUnicode
#: CMap maps ``ä``→``‰`` / a Private-Use-Area glyph and BOTH the Docling TableFormer decoder and the
#: pdfium text layer (reading the SAME broken font) emit the SAME garbled string, so
#: ``text_equivalence`` sees them equal and the unit would silently count EXACT. This
#: belt-and-suspenders scan (``fi_appendix_vision_screen.scan_garble``) runs on the AGREED
#: (Docling) text — the structured content kept for that unit — and DEMOTES it from exact to a
#: routed divergence with a garble note when it is not clean. It NEVER promotes.
#:
#: It scans ONLY the agreed Docling text, NOT the pdfium witness: a genuine shared-CMap corruption
#: corrupts the Docling text too (same broken font), so the Docling side alone catches it; scanning
#: the witness would additionally fire on BENIGN witness-only artifacts the inert quotient already
#: folds (e.g. a U+0002 soft-hyphen at a wrapped line break — ``yli\x02paine`` ≡ ``ylipaine``),
#: which are NOT corruption. Auditing confirms 0 garbled-agreed units on the born-digital corpus, so
#: this is non-regressive on real data — a pure safety net for the shared-CMap-corruption path.
def _agreed_text_garble_note(agreed_text: str) -> str:
    """Garble note for an A≡B agreed unit's AGREED (Docling) text, or ``""`` when clean (pure).

    Scans the agreed Docling text for a deterministic corruption signature (PUA / control / U+FFFD /
    mojibake). Returns a short ``garble[<kinds>]`` note (the demotion reason) if not clean, else ``""``.
    """
    report = scan_garble(agreed_text)
    if not report.clean:
        return "garble[" + ",".join(k.value for k in report.kinds) + "]"
    return ""


#: A cell whose Docling text and independent bbox witness are BOTH blank after the
#: inert quotient is vacuously exact (an empty spacer cell); no divergence is emitted.
def verify_table_exact(
    table: StructuredTable,
    bbox_witness: Callable[[int, Tuple[float, float, float, float]], str],
) -> TableVerification:
    """Verify each cell EXACTLY against an independent in-bbox text witness.

    ``bbox_witness(page_num, bbox) -> str`` returns the pdfium text-layer content inside
    the cell's bbox (injected so this is hermetically testable and the pdfium transport
    stays at the boundary). A cell is exact iff ``text_equivalence`` finds Docling's cell
    text and the witness text equal modulo the legally-inert quotient; otherwise it is a
    typed :class:`TableCellDivergence`. Cells without a bbox cannot be cross-verified and
    are counted ``no_witness`` (deferred, never a forced diff).
    """
    n_exact = 0
    n_no_witness = 0
    divergences: List[TableCellDivergence] = []
    for cell in table.cells:
        if cell.bbox is None:
            n_no_witness += 1
            continue
        witness = bbox_witness(table.page_num, cell.bbox)
        if text_equivalence(cell.text, witness).equal:
            # Belt-and-suspenders: an A≡B agreed cell whose agreed text still carries a shared-CMap
            # corruption signature is DEMOTED from exact to a routed divergence, never counted.
            garble_note = _agreed_text_garble_note(cell.text)
            if garble_note:
                divergences.append(
                    TableCellDivergence(
                        row=cell.row, col=cell.col, docling_text=cell.text,
                        witness_text=witness, descriptor=garble_note,
                    )
                )
            else:
                n_exact += 1
        else:
            divergences.append(
                TableCellDivergence(
                    row=cell.row, col=cell.col, docling_text=cell.text, witness_text=witness
                )
            )
    return TableVerification(
        locator=table.locator,
        page_num=table.page_num,
        table_index=table.table_index,
        n_cells=len(table.cells),
        n_exact=n_exact,
        n_no_witness=n_no_witness,
        divergences=tuple(divergences),
    )


# --------------------------------------------------------------------------- #
# GEOMETRY RECONCILIATION: page text-runs → (row,col) witness (the Fix-1 unlock). #
# --------------------------------------------------------------------------- #
#
# The dominant NON-font failure: Docling wraps a long line and routes the tail into the
# NEIGHBOURING column, drawing that cell's bbox too narrow. Reading each cell's OWN bbox
# then makes the true owner read empty while the neighbour over-reads — a segmentation
# defect, not a content one. The bboxes already threaded onto the grid carry enough
# geometry to repair it deterministically: a COLUMN's true x-band is the union of all its
# cells' bboxes (wider than any single mis-drawn cell), and likewise a ROW's y-band. So for a
# cell the simple per-bbox read MISSES (reads empty though Docling placed content), we gather
# the pdfium CHARACTERS whose centre lands in that (row,col) band and reconstruct its witness
# from them. Character (not ``get_rect`` line-run) granularity is essential: a pdfium rect
# spans a whole visual line across ALL columns, so assigning it by centre would dump an entire
# row into one column; a single char localises to exactly one column. NO fuzzy matching (each
# char lands in exactly one band or none). The reconciliation is applied ONLY to under-read
# cells and never overrides a per-bbox read that already found text — reconstructing a well-read
# cell from chars risks reading-order artifacts (a decimal comma sits below its digits), and the
# empirically-measured wrap defect is an EMPTY witness, so empty-rescue captures the structural
# unlock while staying strictly non-regressive. GUARD: if the column/row bands are not cleanly
# separable (degenerate/overlapping bboxes) a char could map ambiguously, so the whole table
# keeps the per-bbox read. Never crash, never drop a cell.


@dataclass(frozen=True, slots=True)
class TextRun:
    """One pdfium text-layer run: its text and TOP-LEFT-origin bbox (points).

    Same coordinate frame as :class:`StructuredCell` ``bbox`` so a run's centre lines up
    directly with the cell/column/row bands (no flip needed at the reconciliation layer —
    the pdfium bottom-left→top-left flip happens once where the runs are harvested).
    """

    text: str
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def center_x(self) -> float:
        return (self.x0 + self.x1) / 2.0

    @property
    def center_y(self) -> float:
        return (self.y0 + self.y1) / 2.0


#: Bands closer than this (points) are treated as touching, not overlapping — Docling draws
#: adjacent cell bboxes edge-to-edge (and occasionally a hair past), so a sub-point overlap is
#: an artifact, not the ambiguity the reconciliation guard is meant to catch.
_BAND_SEPARATION_TOL = 1.0

#: Vertical gap (points) between two chars' centres that starts a NEW line inside a cell — a
#: wrapped multi-line cell reconstructs with a ``\n`` at each line break (so a hyphen-at-break
#: can be de-hyphenated by the op-equivalence quotient). Comfortably below a single line's
#: advance and above within-line baseline jitter / sub/superscript wobble.
_LINE_Y_GAP = 3.0


def _reconstruct_cell_text(runs: List[TextRun]) -> str:
    """Join a cell's assigned char/fragment runs in reading order (top→bottom, left→right).

    Runs are clustered into visual LINES by a vertical-centre gap; within a line they are
    concatenated left-to-right (kept spaces reproduce the spacing), and lines are joined with
    ``\\n`` so a hyphen falling at a line break stays a de-hyphenatable ``"-\\n"``.
    """
    if not runs:
        return ""
    ordered = sorted(runs, key=lambda r: (r.center_y, r.x0))
    lines: List[List[TextRun]] = [[ordered[0]]]
    for r in ordered[1:]:
        if r.center_y - lines[-1][-1].center_y > _LINE_Y_GAP:
            lines.append([r])
        else:
            lines[-1].append(r)
    return "\n".join(
        "".join(rr.text for rr in sorted(line, key=lambda r: r.x0)) for line in lines
    )


def _axis_bands(
    cells: Sequence[StructuredCell], *, by_col: bool
) -> Dict[int, Tuple[float, float]]:
    """Per-column x-band (``by_col``) or per-row y-band: union of that group's cell bboxes."""
    bands: Dict[int, Tuple[float, float]] = {}
    for c in cells:
        if c.bbox is None:
            continue
        key = c.col if by_col else c.row
        a, b = (c.bbox[0], c.bbox[2]) if by_col else (c.bbox[1], c.bbox[3])
        lo, hi = (a, b) if a <= b else (b, a)
        cur = bands.get(key)
        bands[key] = (lo, hi) if cur is None else (min(cur[0], lo), max(cur[1], hi))
    return bands


def _bands_cleanly_separated(bands: Dict[int, Tuple[float, float]]) -> bool:
    """True iff no two bands OVERLAP by more than the touching tolerance (sweep by lo edge)."""
    ordered = sorted(bands.values())
    prev_hi: Optional[float] = None
    for lo, hi in ordered:
        if prev_hi is not None and prev_hi - lo > _BAND_SEPARATION_TOL:
            return False
        prev_hi = hi if prev_hi is None else max(prev_hi, hi)
    return True


def _band_of(center: float, bands: Dict[int, Tuple[float, float]]) -> Optional[int]:
    """The single band key containing ``center`` (None if zero or — impossibly, given the
    clean-separation guard — more than one)."""
    hit = [k for k, (lo, hi) in bands.items() if lo <= center <= hi]
    return hit[0] if len(hit) == 1 else None


def reconcile_table_witness(
    table: StructuredTable,
    runs: Sequence[TextRun],
    per_bbox_fallback: Callable[[Tuple[float, float, float, float]], str],
) -> Dict[Tuple[int, int], str]:
    """Build each cell's witness, rescuing UNDER-READ cells from page chars by x/y-band (pure).

    Returns a ``(row, col) -> witness_text`` map. The per-cell bbox read (``per_bbox_fallback``)
    is the default witness and is TRUSTED wherever it finds text — reconstructing a well-read
    cell from individual chars only risks reading-order artifacts (a decimal comma sits below
    its digits). Reconciliation is applied ONLY to a cell the per-bbox read returns EMPTY for
    while Docling placed content in it — the wrapped-tail defect, where the cell's glyphs were
    drawn under a neighbour's bbox. For such a cell, the chars whose centre lands in this column's
    x-band and this row's y-band (unions of the column/row cell bboxes) are gathered and joined
    in reading order (a ``"-\\n"`` line break survives so de-hyphenation can fuse it). This can
    only convert an existing empty-witness divergence into an exact match, so it is strictly
    non-regressive. If the bands are not cleanly separable (degenerate/overlapping bboxes → a
    char could map ambiguously) the whole table keeps the per-bbox read — never a crash, never a
    dropped cell.
    """
    col_bands = _axis_bands(table.cells, by_col=True)
    row_bands = _axis_bands(table.cells, by_col=False)
    if not (_bands_cleanly_separated(col_bands) and _bands_cleanly_separated(row_bands)):
        # Ambiguous geometry: keep the conservative per-bbox witness for this table.
        return {
            (c.row, c.col): per_bbox_fallback(c.bbox)
            for c in table.cells
            if c.bbox is not None
        }
    buckets: Dict[Tuple[int, int], List[TextRun]] = defaultdict(list)
    for run in runs:
        col = _band_of(run.center_x, col_bands)
        row = _band_of(run.center_y, row_bands)
        if col is None or row is None:
            continue  # a run outside the grid (page header/footer) — not a cell's content
        buckets[(row, col)].append(run)
    witness: Dict[Tuple[int, int], str] = {}
    for c in table.cells:
        if c.bbox is None:
            continue
        pb = per_bbox_fallback(c.bbox)
        if pb.strip():
            # The simple per-cell read already witnessed this cell — TRUST it. Reconstructing a
            # well-read cell from chars only risks re-ordering artifacts (a decimal comma sits on
            # a lower baseline than its digits, so naive line-clustering would split "0,05"), so
            # reconciliation is reserved for the cells the simple read MISSES.
            witness[(c.row, c.col)] = pb
            continue
        if c.text.strip():
            # UNDER-READ RESCUE: per-bbox read this cell EMPTY though Docling placed content here
            # — the wrapped-tail defect (the cell's glyphs got drawn under a neighbour's bbox).
            # Reconstruct it from the chars whose geometry lands in this (row,col) band. This can
            # only turn an existing empty-witness divergence into an exact match; it never touches
            # a cell the per-bbox read already handled, so it is strictly non-regressive.
            witness[(c.row, c.col)] = _reconstruct_cell_text(buckets.get((c.row, c.col), []))
        else:
            witness[(c.row, c.col)] = pb  # genuinely empty (spacer): both sides blank
    return witness


# --------------------------------------------------------------------------- #
# META-LEVEL escalation routing (NOT a corruption-repair heuristic).            #
# --------------------------------------------------------------------------- #
#
# The empirical lesson from the multi-column appendix stratum (e.g. 2003/917): a
# table that fails EXACT verification does so overwhelmingly because the FREE
# deterministic witness — the pdfium text layer — is itself corrupt for that PDF's
# font (a broken ToUnicode CMap maps ``ä``→``‰``, ``ö``→a C0 control char, ``§``→``ß``
# and drops the decimal comma), NOT because Docling read the cell wrong. That
# failure is orthogonal to Docling's side-by-side dual-table merge, and re-indexing
# the grid (a row/col re-split) cannot change a single per-cell bbox verdict.
#
# So the correct move is META, not a hand-written repair (which is doomed on
# arbitrary broken fonts): when the deterministic lane cannot SELF-VERIFY a table,
# route that table to a VISION second-witness. This function is that router — it
# names the routing verdict from the deterministic verdict; it does not attempt to
# reconstruct any cell. ``VISION_ESCALATE`` is the only verdict that spends tokens.

#: The deterministic lane verified every witnessable cell (0 divergences, ≥1 exact).
ROUTE_SELF_VERIFIED = "self_verified"
#: The deterministic witness could not verify ≥1 cell → send the table to vision.
ROUTE_VISION_ESCALATE = "vision_escalate"
#: No cell had a bbox witness at all → nothing for the deterministic lane to verify.
ROUTE_NO_WITNESS_DEFERRED = "no_witness_deferred"
#: STRUCTURAL MIS-ATTRIBUTION gate (Wire 2): an independent geometric re-grid disagrees with
#: Docling's topology on a dual-merge-suspected table (phantom column / duplicated spanning
#: header). Its cells' per-cell A≡B verdicts may all be exact yet the (row,col) binding is WRONG,
#: so the table must NOT count self_verified/exact and must NOT graduate topology-wrong — it is
#: routed to vision/human under this typed outcome. Overrides self_verified AND vision_escalate.
ROUTE_STRUCTURAL_DISAGREEMENT = "structural_disagreement"


# --------------------------------------------------------------------------- #
# EXACTNESS GRADE TAXONOMY — vision-graduated exactness is NOT the same guarantee #
# as a two-decoder A≡B agreement, so the two are TYPED APART and never merged.   #
# --------------------------------------------------------------------------- #
#
# A free-lane exact cell (``verify_table_exact`` ``n_exact``) is a TWO-DECODER agreement:
# Docling's TableFormer decoder and the pdfium text-layer decoder — two INDEPENDENT text
# pipelines — produced the same string modulo the inert quotient. That is the strongest
# guarantee this tool issues (``self_verified``).
#
# A VISION-graduated cell is exact-modulo-RENDER: an INDEPENDENT render+read witness agrees
# with one of the text decoders. Because the vision witness reads PIXELS, it is HOMOGLYPH-BLIND
# (U+2212 MINUS and U+002D HYPHEN render identically, as do many CMap-confusable glyphs), so a
# render-corroborated exact is a strictly WEAKER guarantee than a two-decoder A≡B. It gets its
# own grade (``exact_visual``) and is counted SEPARATELY — a vision graduation must never be
# silently folded into the free-lane ``self_verified`` exact total.

#: Free-lane exact: two INDEPENDENT text decoders (Docling ≡ pdfium-in-bbox) agree modulo the
#: inert quotient. The strongest grade; NOT homoglyph-blind (both read the encoded text layer).
GRADE_SELF_VERIFIED = "self_verified"
#: Vision-graduated exact: an independent RENDER+read witness corroborates a text decoder. Exact
#: modulo render — homoglyph-blind — so a DISTINCT, weaker grade than ``self_verified``, always
#: counted apart from it.
GRADE_EXACT_VISUAL = "exact_visual"

# WHICH TWO of the three independent witnesses {docling-text, pdfium-witness, vision-read} agreed
# (symmetric two-of-three graduation). The agreed pair is the cell's trusted content; the third is
# the outvoted dissenter. Recorded per graduation so the corrupt-font sub-case is auditable.
#: pdfium-witness ≡ vision-read (Docling the dissenter): the wrapped-tail / mis-segment sub-case;
#: trusted content = the pdfium reading.
VISION_PAIR_PDFIUM_VISION = "pdfium_vision"
#: docling-text ≡ vision-read (pdfium the dissenter): the CORRUPT-FONT sub-case (pdfium's text
#: layer is the odd one out); trusted content = the Docling reading. Unreachable before symmetric
#: graduation — this is exactly the cell a ``vision≡pdfium``-only rule could never graduate.
VISION_PAIR_DOCLING_VISION = "docling_vision"

# TERMINAL non-graduating vision outcomes (each distinct; each is a TYPED bucket, never a bare
# ``status`` field — VOCAB-02).
#: The vision model PUNTED (escalate/abstain) rather than force a blind transcription it is unsure
#: of → routed to the terminal adjudication tier, carrying the model's ``descriptor``. Distinct
#: from ``open``: the model declined to read, it did not read-and-disagree.
VISION_OUTCOME_ESCALATED = "escalated"
#: The vision read corroborated NEITHER text decoder (all three disagree / incoherent) → a
#: genuinely-open typed divergence for the adjudication tail.
VISION_OUTCOME_OPEN = "open"


def _route_from_verdict(*, has_divergence: bool, n_exact: int) -> str:
    """Shared meta-routing rule (same contract for the table and text-block lanes).

    A unit whose independent witness reproduced EVERY witnessable member exactly is
    self-verified; one with ≥1 typed divergence is escalated to a vision second-witness
    (never hand-repaired — the dominant failure is a corrupt text-layer witness); a unit
    with nothing witnessable is deferred.
    """
    if has_divergence:
        return ROUTE_VISION_ESCALATE
    if n_exact > 0:
        return ROUTE_SELF_VERIFIED
    return ROUTE_NO_WITNESS_DEFERRED


def table_escalation_route(verification: TableVerification) -> str:
    """Meta-level routing verdict for one table's deterministic-witness result.

    Pure. The phase-3 deterministic lane either SELF-VERIFIES a table (every
    witnessable cell reproduced exactly) or it does not — and when it does not, the
    honest response is to ESCALATE to a vision second-witness, never to hand-repair
    the cells (the dominant failure is a corrupt text-layer witness, unrepairable in
    the general case). A table with no witnessable cell is deferred.
    """
    return _route_from_verdict(
        has_divergence=bool(verification.divergences), n_exact=verification.n_exact
    )


def table_route_with_structure(
    verification: TableVerification, sanity: StructuralSanity
) -> str:
    """Meta route with the Wire-2 STRUCTURAL gate applied (pure).

    A table the geometric re-grid convicts of a topology error
    (:attr:`StructuralSanity.structural_disagreement`) is routed
    :data:`ROUTE_STRUCTURAL_DISAGREEMENT` REGARDLESS of its per-cell verdicts — overriding both
    ``self_verified`` (its all-exact cells are topology-wrong, so must NOT count) and
    ``vision_escalate`` (a badly-merged table must not graduate topology-wrong). Otherwise the
    ordinary :func:`table_escalation_route` verdict stands.
    """
    if sanity.structural_disagreement:
        return ROUTE_STRUCTURAL_DISAGREEMENT
    return table_escalation_route(verification)


# --------------------------------------------------------------------------- #
# VISION THIRD-WITNESS TIE-BREAK (the escalation target — an INDEPENDENT render). #
# --------------------------------------------------------------------------- #
#
# When the deterministic (pdfium text-layer) witness cannot self-verify a table it is
# routed ``vision_escalate``. On the BORN-DIGITAL appendix-table stratum the dominant
# cause is the OPPOSITE of the original corrupt-font hypothesis: the pdfium-in-bbox
# witness is the RELIABLE cell-text reader and Docling's TableFormer ``.text`` is the
# one that ERRS — it mis-segments a wrapped line-tail (e.g. Docling cell text
# ``'raikasta- 2 500 mg/kg'`` where the pdfium witness correctly reads ``'2 500 mg/kg'``)
# or drops a middle line. So a divergent cell is NOT evidence Docling is right; it is a
# two-witness disagreement to adjudicate by a THIRD, independent witness.
#
# THE TIE-BREAK. RENDER each divergent cell's own bbox region (region-isolation crop,
# never a whole-page downscale), read that pixel region back to text with the vision
# model, and GRADUATE the cell to EXACT iff the vision read reproduces the PDFIUM
# WITNESS modulo the inert op-equivalence quotient — i.e. two INDEPENDENT witnesses
# (pdfium + vision) agree and Docling is outvoted. This stays strictly within the
# exactness invariant (two independent witnesses agreeing modulo a legally-inert
# quotient); it is NOT a numeric-recall / coverage score — full-text quotient
# equivalence is required, never numeric-only agreement. The three outcomes:
#
#   - GRADUATED         vision ≡ pdfium witness → Docling outvoted, cell exact, the
#                       pdfium text becomes the trusted content.
#   - WITNESS_DISAGREE  vision ≡ Docling instead → the corrupt-text-layer sub-case (the
#                       pdfium witness is the odd one out); NOT graduated.
#   - OPEN              vision corroborates neither (all three disagree / vision
#                       incoherent) → a genuinely-open typed divergence (adjudication tail).
#
# SPARSE/SCANNED GUARD. A near-empty / image-baked text layer must NEVER graduate: there
# the pdfium witness is empty or garbled and the vision model HALLUCINATES (``'UN-ltja'``
# → invented ``'UNAUTHORIZED USE'``). Graduation therefore requires the PDF to be
# born-digital AND the pdfium witness to be a real (non-empty) reading.
#
# The witness is INJECTABLE (``region_reader(page_num, bbox) -> str``), exactly like
# ``verify_table_exact``'s ``bbox_witness``, so the seam (render → read → exact compare)
# is hermetically testable with a scripted reader — no model / PDF in CI.
# ``make_vision_region_reader`` is the production wiring (crop via
# ``ingest.visual.render_region_crop`` + read via the :8080 vision producer's
# ``read_region_cold``). Only the ROUTED (deterministically-divergent) cells are re-read:
# the deterministic lane already verified the rest, so vision spend is bounded to exactly
# the cells the free lane could not adjudicate.


@dataclass(frozen=True, slots=True)
class VisionRegionRead:
    """One BLIND vision transcription of a rendered region — the injectable third witness.

    A pass-1 BLIND read: the model transcribes ONLY the rendered pixels of the region and is NEVER
    shown the pdfium/Docling candidate texts (showing candidates would let the model ECHO one and
    destroy witness independence). The controller — not the model — compares this read to the
    candidates via :func:`text_equivalence`.

    - ``text`` — the blind transcription (empty when the model abstained / read nothing).
    - ``abstain`` — the model PUNTED (escalate/abstain) rather than force a transcription it is
      unsure of; such a read NEVER graduates and routes to the terminal tier as ``escalated``.
    - ``descriptor`` — the model's short free-text reason (what it thinks is wrong / why it
      punted), recorded on the divergence and in the determinism-cache evidence record.
    - ``model_id`` — the vision model id that produced this read (a model bump re-keys the cache).
    """

    text: str
    abstain: bool = False
    descriptor: str = ""
    model_id: str = ""


#: A region reader may return a plain ``str`` (a confident blind transcription — the hermetic
#: scripted readers and the simplest production path) OR a full :class:`VisionRegionRead` (to
#: signal an abstain / carry a descriptor + model id). The controller coerces via
#: :func:`_as_vision_read`, so both shapes flow through one graduation path.
VisionReadResult = Union[str, "VisionRegionRead"]


def _as_vision_read(result: VisionReadResult) -> VisionRegionRead:
    """Coerce a reader result to a :class:`VisionRegionRead` (a bare ``str`` = a confident read)."""
    if isinstance(result, VisionRegionRead):
        return result
    return VisionRegionRead(text=result)


#: Internal graduation outcome (a routed unit READ by the vision witness): ``graduated`` when two
#: of the three witnesses agree, else the terminal :data:`VISION_OUTCOME_ESCALATED` /
#: :data:`VISION_OUTCOME_OPEN`.
_VISION_OUTCOME_GRADUATED = "graduated"


@dataclass(frozen=True, slots=True)
class _WitnessAdjudication:
    """The three-witness verdict for ONE divergent unit (shared by the table + text-block lanes)."""

    outcome: str  # _VISION_OUTCOME_GRADUATED | VISION_OUTCOME_ESCALATED | VISION_OUTCOME_OPEN
    corroborated_text: str  # the agreed pair's content (graduated only)
    agreeing_pair: str  # VISION_PAIR_* (graduated only)
    vision_text: str
    descriptor: str


def _adjudicate_three_witnesses(
    *,
    docling_text: str,
    pdfium_witness: str,
    vision: VisionRegionRead,
    born_digital: bool,
) -> _WitnessAdjudication:
    """SYMMETRIC two-of-three graduation over the three INDEPENDENT witnesses (pure).

    The witnesses are {docling-text, pdfium-witness, vision-read}. The unit GRADUATES iff two of
    them agree modulo the inert :func:`text_equivalence` quotient — the third is the outvoted
    dissenter — and the agreed pair becomes the trusted content:

    - pdfium ≡ vision (Docling dissents, wrapped-tail mis-segment) → content = pdfium.
    - docling ≡ vision (pdfium dissents, CORRUPT FONT) → content = docling. This is the cell a
      ``vision≡pdfium``-only rule could never graduate: on a corrupt-font page pdfium's text layer
      is the odd one out, so graduating on pdfium≡vision is impossible, but docling≡vision recovers
      it. At most ONE pair can hold: the unit is a divergence (docling ≢ pdfium), so a vision read
      cannot be quotient-equal to BOTH.

    INDEPENDENCE is load-bearing: this is a genuine two-of-three vote ONLY because the three are
    independent readers — the vision read is a BLIND transcription of the pixels (it never saw the
    candidate texts), so agreement is corroboration, not echo. A vision read that had been shown the
    candidates would collapse to a single witness and MUST NOT count.

    A ``vision.abstain`` read (the model punted) is TERMINAL ``escalated`` — never graduated —
    carrying its descriptor. SPARSE/SCANNED GUARD: when ``born_digital`` is false NOTHING graduates
    (the text layer is a weak reference and vision hallucinates); such reads fall to ``open``. The
    ``.strip()`` gate additionally blocks graduating on an EMPTY agreed side (a lone witness).
    """
    if vision.abstain:
        return _WitnessAdjudication(
            outcome=VISION_OUTCOME_ESCALATED,
            corroborated_text="",
            agreeing_pair="",
            vision_text=vision.text,
            descriptor=vision.descriptor,
        )
    if born_digital:
        if pdfium_witness.strip() and text_equivalence(pdfium_witness, vision.text).equal:
            return _WitnessAdjudication(
                outcome=_VISION_OUTCOME_GRADUATED,
                corroborated_text=pdfium_witness,
                agreeing_pair=VISION_PAIR_PDFIUM_VISION,
                vision_text=vision.text,
                descriptor=vision.descriptor,
            )
        if docling_text.strip() and text_equivalence(docling_text, vision.text).equal:
            return _WitnessAdjudication(
                outcome=_VISION_OUTCOME_GRADUATED,
                corroborated_text=docling_text,
                agreeing_pair=VISION_PAIR_DOCLING_VISION,
                vision_text=vision.text,
                descriptor=vision.descriptor,
            )
    return _WitnessAdjudication(
        outcome=VISION_OUTCOME_OPEN,
        corroborated_text="",
        agreeing_pair="",
        vision_text=vision.text,
        descriptor=vision.descriptor,
    )


@dataclass(frozen=True, slots=True)
class TableCellGraduation:
    """A divergent cell the vision THIRD-WITNESS tie-break GRADUATED to exact (grade ``exact_visual``).

    Two of the three INDEPENDENT witnesses {docling-text, pdfium-witness, vision-read} agree modulo
    the inert op-equivalence quotient and the third is outvoted (symmetric two-of-three graduation):

    - ``agreeing_pair == pdfium_vision`` — the pdfium-in-bbox witness and the render-based vision
      read agree; Docling (a wrapped-tail mis-segment) is the dissenter. ``corroborated_text`` = the
      pdfium reading.
    - ``agreeing_pair == docling_vision`` — Docling and the vision read agree; the pdfium text layer
      (corrupt font) is the dissenter. ``corroborated_text`` = the Docling reading. This is the
      corrupt-font cell a ``vision≡pdfium``-only rule could NEVER graduate.

    ``grade`` is always :data:`GRADE_EXACT_VISUAL` — exact-modulo-render (homoglyph-blind), counted
    SEPARATELY from the free-lane ``self_verified`` two-decoder exact.
    """

    row: int
    col: int
    corroborated_text: str
    vision_text: str
    agreeing_pair: str
    grade: str = GRADE_EXACT_VISUAL


@dataclass(frozen=True, slots=True)
class TableVisionVerification:
    """Vision THIRD-WITNESS tie-break verdict for ONE deterministically-escalated table.

    ``n_routed`` is the deterministic escalation set (the cells the pdfium witness could
    not reconcile with Docling). Each is re-read by the render-based vision witness and
    adjudicated into exactly one of three buckets (symmetric two-of-three graduation):

    - ``graduated`` — TWO of {docling, pdfium, vision} agree modulo the quotient (grade
      ``exact_visual``); the third is outvoted. ``pdfium_vision`` = Docling outvoted (wrapped-tail
      mis-segment); ``docling_vision`` = pdfium outvoted (corrupt font).
    - ``escalated`` — the vision model PUNTED (abstain/escalate), carrying its ``descriptor``: a
      terminal-tier typed status, NEVER graduated, distinct from ``open`` (declined to read vs
      read-and-disagreed).
    - ``open_divergences`` — vision corroborates neither text decoder: a genuinely-open typed
      divergence (each carrying the vision read + descriptor) for the adjudication tail.
    """

    locator: str
    page_num: int
    table_index: int
    n_routed: int
    graduated: Tuple[TableCellGraduation, ...]
    escalated: Tuple[TableCellDivergence, ...]
    open_divergences: Tuple[TableCellDivergence, ...]

    @property
    def n_graduated(self) -> int:
        """Routed cells graduated to exact (grade ``exact_visual``; two of three witnesses agree)."""
        return len(self.graduated)

    @property
    def n_read(self) -> int:
        """Routed cells the vision witness actually re-read (all three buckets).

        Equals ``n_routed`` when unbudgeted; less when a ``max_cells`` cap curtailed the
        render spend (the un-read routed cells fall in none of the three buckets)."""
        return (
            len(self.graduated)
            + len(self.escalated)
            + len(self.open_divergences)
        )

    @property
    def all_graduated(self) -> bool:
        """True iff every routed cell the witness READ graduated (0 escalated, 0 open reads)."""
        return not self.escalated and not self.open_divergences

    def to_jsonable(self) -> Dict[str, object]:
        return {
            "locator": self.locator,
            "page_num": self.page_num,
            "table_index": self.table_index,
            "n_routed": self.n_routed,
            "n_read": self.n_read,
            "n_graduated": self.n_graduated,
            "graduated": [
                {
                    "row": g.row,
                    "col": g.col,
                    "corroborated_text": g.corroborated_text,
                    "vision": g.vision_text,
                    "agreeing_pair": g.agreeing_pair,
                    "grade": g.grade,
                    "tiebreak_status": "vision_corroborated_exact",
                }
                for g in self.graduated
            ],
            "escalated": [
                {
                    "row": d.row,
                    "col": d.col,
                    "docling": d.docling_text,
                    "vision": d.witness_text,
                    "descriptor": d.descriptor,
                    "outcome": VISION_OUTCOME_ESCALATED,
                }
                for d in self.escalated
            ],
            "open_divergences": [
                {
                    "row": d.row,
                    "col": d.col,
                    "docling": d.docling_text,
                    "vision": d.witness_text,
                    "descriptor": d.descriptor,
                    "outcome": VISION_OUTCOME_OPEN,
                }
                for d in self.open_divergences
            ],
        }


def verify_tables_vision(
    tables: Sequence[StructuredTable],
    det_verifications: Sequence[TableVerification],
    region_reader: Callable[[int, Tuple[float, float, float, float]], VisionReadResult],
    *,
    born_digital: bool = True,
    max_cells: Optional[int] = None,
    structural_disagreement: Optional[Sequence[bool]] = None,
) -> Tuple[TableVisionVerification, ...]:
    """Vision THIRD-WITNESS tie-break over the ``vision_escalate`` stratum (injectable reader).

    For every table the deterministic lane routed ``vision_escalate``, re-read ONLY its
    divergent cells' bbox regions through ``region_reader`` (the render-based BLIND witness;
    injected so this is hermetically testable) and adjudicate each by SYMMETRIC two-of-three
    graduation (:func:`_adjudicate_three_witnesses`) over {docling-text, pdfium-witness,
    vision-read}, using the identical exactness check ``verify_table_exact`` uses
    (``text_equivalence`` modulo the inert op-equivalence quotient):

    - the cell GRADUATES to exact (grade ``exact_visual``) iff TWO of the three witnesses agree —
      ``pdfium≡vision`` (Docling the wrapped-tail dissenter, content = pdfium) OR ``docling≡vision``
      (pdfium the CORRUPT-FONT dissenter, content = Docling). A single witness never graduates.
    - a vision ABSTAIN (the model punted) is TERMINAL ``escalated`` (never graduated), carrying its
      descriptor for the adjudication tier — distinct from ``open``.
    - else it is an ``open_divergence`` (vision corroborates neither text decoder).

    The graduation is a genuine two-of-three vote ONLY because the three readers are INDEPENDENT:
    the vision read is BLIND (it never saw the candidate texts), so agreement is corroboration, not
    echo — a reader shown the candidates would collapse to one witness and must not count.

    SPARSE/SCANNED GUARD: graduation requires ``born_digital`` AND a non-empty agreed side — a
    near-empty / image-baked text layer never graduates (the text witnesses are weak and vision
    hallucinates there); such cells fall to ``open``. Tables not routed to vision are skipped.

    ``region_reader`` may return a plain ``str`` (a confident blind read) or a full
    :class:`VisionRegionRead` (to abstain / carry a descriptor + model id). ``max_cells`` caps the
    TOTAL routed cells re-read (a vision-spend budget); ``None`` is unbounded. Under a cap the
    un-read routed cells stay in ``n_routed`` but appear in no bucket, so ``n_read`` (< ``n_routed``)
    is the sampled base. Pure apart from the injected reader.

    ``structural_disagreement`` (optional, per-table, aligned with ``tables``): when True for a table,
    it is SKIPPED (never re-read, never graduated) — the Wire-2 structural gate, so a topology-wrong
    dual-merge table cannot graduate topology-wrong. Omit (``None``) to disable the gate.
    """
    out: List[TableVisionVerification] = []
    budget = max_cells
    for idx, (table, det) in enumerate(zip(tables, det_verifications, strict=True)):
        # WIRE 2 gate: a table the geometric re-grid convicts of a topology error must NOT graduate
        # (a badly-merged table can't be allowed to graduate topology-wrong) — it is skipped here
        # and typed ROUTE_STRUCTURAL_DISAGREEMENT at the report level instead.
        if structural_disagreement is not None and structural_disagreement[idx]:
            continue
        if table_escalation_route(det) != ROUTE_VISION_ESCALATE:
            continue
        cells_by_pos = {(c.row, c.col): c for c in table.cells}
        graduated: List[TableCellGraduation] = []
        escalated: List[TableCellDivergence] = []
        open_divergences: List[TableCellDivergence] = []
        for d in det.divergences:
            if budget is not None and budget <= 0:
                break  # vision-spend budget exhausted; leave the rest un-read
            cell = cells_by_pos.get((d.row, d.col))
            if cell is None or cell.bbox is None:  # divergent ⇒ had a bbox; defensive
                continue
            vision = _as_vision_read(region_reader(table.page_num, cell.bbox))
            if budget is not None:
                budget -= 1
            adj = _adjudicate_three_witnesses(
                docling_text=cell.text,
                pdfium_witness=d.witness_text,
                vision=vision,
                born_digital=born_digital,
            )
            if adj.outcome == _VISION_OUTCOME_GRADUATED:
                # Two of {docling, pdfium, vision} agree (the third outvoted): EXACT modulo render.
                graduated.append(
                    TableCellGraduation(
                        row=d.row,
                        col=d.col,
                        corroborated_text=adj.corroborated_text,
                        vision_text=adj.vision_text,
                        agreeing_pair=adj.agreeing_pair,
                    )
                )
            elif adj.outcome == VISION_OUTCOME_ESCALATED:
                # The model PUNTED: terminal escalated, never graduated (descriptor recorded).
                escalated.append(
                    TableCellDivergence(
                        row=d.row, col=d.col, docling_text=cell.text,
                        witness_text=adj.vision_text, descriptor=adj.descriptor,
                    )
                )
            else:
                # vision corroborated neither text decoder: genuinely open.
                open_divergences.append(
                    TableCellDivergence(
                        row=d.row, col=d.col, docling_text=cell.text,
                        witness_text=adj.vision_text, descriptor=adj.descriptor,
                    )
                )
        out.append(
            TableVisionVerification(
                locator=table.locator,
                page_num=table.page_num,
                table_index=table.table_index,
                n_routed=len(det.divergences),
                graduated=tuple(graduated),
                escalated=tuple(escalated),
                open_divergences=tuple(open_divergences),
            )
        )
    return tuple(out)


# --------------------------------------------------------------------------- #
# TEXT-BLOCK LANE: a 0-grid appendix → ordered verbatim text blocks (Phase 3).  #
# --------------------------------------------------------------------------- #
#
# Half the deferred appendix stratum is NOT a grid table: laskuperusteet / formula-
# prose / short textual annexes that Docling yields ZERO ``TABLE`` nodes from. Before
# this lane those PDFs fell through ``structure_statute_pdf`` with an empty table set —
# silently DROPPED, neither verified nor typed. This lane structures such a page's own
# Docling PARAGRAPH/HEADING/FOOTNOTE blocks (each already carrying its bbox from the
# enhanced adapter) as an ORDERED sequence of verbatim text blocks — line structure is
# PRESERVED verbatim, math is NEVER parsed — and verifies each block against exactly the
# same two-witness EXACT contract the table lane uses: an independent pdfium read WITHIN
# the block's own bbox must reproduce the Docling text modulo the legally-inert
# op-equivalence quotient (:mod:`lawvm.finland.op_equivalence`), else a TYPED divergence.
# The self-consistency of two independent PDF reads is the reference (there is no trusted
# appendix XML oracle here — the statute XML body is the thin ``appendix_only`` frame),
# identical to ``verify_table_exact``. Routing reuses ``_route_from_verdict``: a 0-grid
# appendix becomes ONE verified-or-typed unit (self_verified / vision_escalate /
# no_witness_deferred), never a silent drop. Sparse/scanned PDFs (near-empty text layer)
# keep their existing ``text_layer_sparse`` typed status — those need vision/OCR, so this
# text-layer lane does not run on them.

#: The Docling block kinds this lane treats as verbatim text blocks (everything the
#: adapter emits that is not a TABLE/TABLE_ROW/TABLE_CELL and carries text + geometry).
_TEXT_BLOCK_KINDS = frozenset(
    {
        SourceDocumentNodeKind.PARAGRAPH,
        SourceDocumentNodeKind.HEADING,
        SourceDocumentNodeKind.FOOTNOTE,
    }
)


@dataclass(frozen=True, slots=True)
class StructuredTextBlock:
    """One verbatim appendix text block: reading-order index, kind, text, and bbox.

    ``text`` preserves the block's line structure verbatim (no math parsing); ``kind`` is
    the Docling role (``paragraph``/``heading``/``footnote``). ``bbox`` is the normalized
    top-left-origin region (points) the independent pdfium witness is read within — the
    text-block analog of :class:`StructuredCell`.
    """

    locator: str
    page_num: int
    block_index: int
    kind: str
    text: str
    bbox: Optional[Tuple[float, float, float, float]]

    def to_jsonable(self) -> Dict[str, object]:
        return {
            "locator": self.locator,
            "page_num": self.page_num,
            "block_index": self.block_index,
            "kind": self.kind,
            "text": self.text,
            "bbox": list(self.bbox) if self.bbox is not None else None,
        }


@dataclass(frozen=True, slots=True)
class TextBlockDivergence:
    """One block where the independent bbox witness did not reproduce the Docling text.

    ``descriptor`` carries the vision model's short free-text reason when this divergence holds a
    vision read (the ``escalated`` / ``open`` tie-break buckets); empty for a deterministic-lane
    divergence.
    """

    block_index: int
    page_num: int
    kind: str
    docling_text: str
    witness_text: str
    descriptor: str = ""


@dataclass(frozen=True, slots=True)
class TextBlockVerification:
    """Per-appendix EXACT cross-witness verdict over the ordered text blocks.

    The text-block analog of :class:`TableVerification`: blocks are the members that
    :func:`verify_text_blocks_exact` cross-verifies (a block is the text-lane counterpart
    of a table cell). ``.divergences`` and ``.n_exact`` give it the same duck-typed shape
    :func:`_route_from_verdict` routes on, so a 0-grid appendix is one verified-or-typed
    unit.
    """

    locator: str
    n_blocks: int
    n_exact: int
    n_no_witness: int
    divergences: Tuple[TextBlockDivergence, ...]

    @property
    def exact(self) -> bool:
        """True iff EVERY witnessable block reproduced exactly (0 typed divergences)."""
        return not self.divergences

    @property
    def n_witnessed(self) -> int:
        """Blocks that had a bbox to cross-verify (exact + divergent; excludes no_witness)."""
        return self.n_exact + len(self.divergences)

    def to_jsonable(self) -> Dict[str, object]:
        return {
            "locator": self.locator,
            "n_blocks": self.n_blocks,
            "n_exact": self.n_exact,
            "n_no_witness": self.n_no_witness,
            "n_witnessed": self.n_witnessed,
            "exact": self.exact,
            "divergences": [
                {
                    "block_index": d.block_index,
                    "page_num": d.page_num,
                    "kind": d.kind,
                    "docling": d.docling_text,
                    "witness": d.witness_text,
                }
                for d in self.divergences
            ],
        }


def text_block_escalation_route(verification: TextBlockVerification) -> str:
    """Meta-level routing verdict for a 0-grid appendix's text-block set (mirrors tables).

    Same honest contract as :func:`table_escalation_route`: every witnessable block
    reproduced exactly → ``self_verified``; ≥1 typed divergence → ``vision_escalate`` (the
    text layer is sent to a vision second-witness, never hand-repaired); nothing
    witnessable → ``no_witness_deferred``.
    """
    return _route_from_verdict(
        has_divergence=bool(verification.divergences), n_exact=verification.n_exact
    )


def should_run_text_block_lane(*, n_tables: int, mean_text_chars: float) -> bool:
    """Gate the text-block lane: ONLY a 0-grid appendix with a real (born-digital) text layer.

    Pure. False when the PDF yielded ≥1 grid table (the table lane owns it) OR the text layer
    is sparse/scanned (``mean_text_chars`` below the born-digital floor) — a sparse page keeps
    its existing ``text_layer_sparse`` typed status and is routed to vision/OCR, never
    self-verified off a near-empty text layer.
    """
    return n_tables == 0 and mean_text_chars >= _MIN_TEXT_LAYER_CHARS


def structured_text_block_from_node(
    node: SourceDocumentNode, *, locator: str, block_index: int
) -> StructuredTextBlock:
    """Lower one Docling PARAGRAPH/HEADING/FOOTNOTE node into a verbatim text block.

    Reads the node text VERBATIM (line structure preserved; no math parsing) and the
    geometry the enhanced Docling adapter threads onto ``anchor.bbox`` (normalized
    top-left points) — the same bbox seam :func:`structured_table_from_node` reads.
    """
    bb = node.anchor.bbox
    return StructuredTextBlock(
        locator=locator,
        page_num=node.anchor.page_num or 0,
        block_index=block_index,
        kind=node.kind.value,
        text=node.text or "",
        bbox=(bb.x0, bb.y0, bb.x1, bb.y1) if bb is not None else None,
    )


def verify_text_blocks_exact(
    blocks: Sequence[StructuredTextBlock],
    bbox_witness: Callable[[int, Tuple[float, float, float, float]], str],
) -> TextBlockVerification:
    """Verify each text block EXACTLY against an independent in-bbox pdfium witness.

    Mirrors :func:`verify_table_exact` exactly, block-for-cell: ``bbox_witness(page_num,
    bbox) -> str`` returns the pdfium text-layer content inside the block's region
    (injected so this is hermetically testable and the pdfium transport stays at the
    boundary). A block is exact iff :func:`text_equivalence` finds the Docling block text
    and the witness equal modulo the legally-inert op-equivalence quotient (so a wrapped
    line ``"\\n"`` folding to a space, or a ``"— —"`` run, still verifies); otherwise it is
    a typed :class:`TextBlockDivergence`. A block with no bbox is ``no_witness`` (deferred,
    never a forced diff).
    """
    n_exact = 0
    n_no_witness = 0
    divergences: List[TextBlockDivergence] = []
    locator = blocks[0].locator if blocks else ""
    for block in blocks:
        if block.bbox is None:
            n_no_witness += 1
            continue
        witness = bbox_witness(block.page_num, block.bbox)
        if text_equivalence(block.text, witness).equal:
            # Belt-and-suspenders (mirrors the table lane): an A≡B agreed block whose agreed text
            # still carries a shared-CMap corruption signature is DEMOTED from exact to routed.
            garble_note = _agreed_text_garble_note(block.text)
            if garble_note:
                divergences.append(
                    TextBlockDivergence(
                        block_index=block.block_index,
                        page_num=block.page_num,
                        kind=block.kind,
                        docling_text=block.text,
                        witness_text=witness,
                        descriptor=garble_note,
                    )
                )
            else:
                n_exact += 1
        else:
            divergences.append(
                TextBlockDivergence(
                    block_index=block.block_index,
                    page_num=block.page_num,
                    kind=block.kind,
                    docling_text=block.text,
                    witness_text=witness,
                )
            )
    return TextBlockVerification(
        locator=locator,
        n_blocks=len(blocks),
        n_exact=n_exact,
        n_no_witness=n_no_witness,
        divergences=tuple(divergences),
    )


def _verify_text_blocks_against_pdfium(
    pdf_bytes: bytes, blocks: Sequence[StructuredTextBlock]
) -> TextBlockVerification:
    """Exact-verify every text block via a per-bbox pdfium witness (the production seam).

    Opens the PDF ONCE, harvests each page's textpage + height, and reads each block's own
    bbox region with the conservative per-cell reader (:func:`_make_per_bbox_reader`). Unlike
    the table lane, a paragraph bbox already encloses its whole (possibly multi-line) text —
    there is no wrapped-tail column defect to reconcile — so the plain per-bbox read is the
    right independent witness. The read text is handed to the unchanged
    :func:`verify_text_blocks_exact` exactness contract; any pdfium hiccup yields an empty
    witness (→ a typed divergence, never a crash).
    """
    import importlib

    pdfium = importlib.import_module("pypdfium2")
    with _PDFIUM_LOCK:
        doc = pdfium.PdfDocument(pdf_bytes)
        try:
            n = len(doc)
            heights = {i: doc[i].get_size()[1] for i in range(n)}
            textpages = {i: doc[i].get_textpage() for i in range(n)}
            try:
                readers = {
                    i: _make_per_bbox_reader(textpages[i], heights[i]) for i in range(n)
                }

                def bbox_witness(
                    page_num: int, bbox: Tuple[float, float, float, float]
                ) -> str:
                    reader = readers.get(page_num - 1)  # Docling 1-indexed → pdfium 0-indexed
                    return reader(bbox) if reader is not None else ""

                return verify_text_blocks_exact(blocks, bbox_witness)
            finally:
                for tp in textpages.values():
                    close = getattr(tp, "close", None)
                    if close is not None:
                        close()
        finally:
            doc.close()


# --------------------------------------------------------------------------- #
# TEXT-BLOCK VISION THIRD-WITNESS TIE-BREAK (mirror of the table tie-break).     #
# --------------------------------------------------------------------------- #
#
# The 0-grid text-block lane's ``vision_escalate`` stratum is the SAME defect class as the
# table lane's: on the born-digital appendix stratum the pdfium-in-bbox witness is the
# RELIABLE reader and Docling's block ``.text`` is the one that ERRS — it drops content (e.g.
# the list-enumerator ``'1)'``) or mis-segments a wrapped line. So a divergent block is NOT
# evidence Docling is right; it is a two-witness disagreement to adjudicate by a THIRD,
# independent witness. This is the byte-identical tie-break ``verify_tables_vision`` runs,
# block-for-cell: RENDER each escalated block's own bbox region (region-isolation crop, never
# a whole-page downscale), read that pixel region back with the vision model, and GRADUATE the
# block to EXACT iff the vision read reproduces the PDFIUM WITNESS modulo the inert
# op-equivalence quotient — two INDEPENDENT witnesses (pdfium + vision) agree and Docling is
# outvoted, the pdfium text becoming the trusted content. Three outcomes (as tables):
#
#   - GRADUATED         vision ≡ pdfium witness → Docling outvoted, block exact.
#   - WITNESS_DISAGREE  vision ≡ Docling instead → corrupt-text-layer sub-case; NOT graduated.
#   - OPEN              vision corroborates neither → a genuinely-open typed divergence.
#
# SPARSE/SCANNED GUARD (identical): a near-empty / image-baked text layer must NEVER graduate
# (there the pdfium witness is empty/garbled and vision HALLUCINATES) — graduation requires
# ``born_digital`` AND a non-empty pdfium witness. Only the ROUTED (deterministically-divergent)
# blocks are re-read, so vision spend stays bounded to exactly the escalation set; the
# ``max_cells`` budget cap mirrors the table lane's (and — since the table and text-block lanes
# are mutually exclusive per PDF — draws from the same ``vision_max_cells`` allowance).


@dataclass(frozen=True, slots=True)
class TextBlockGraduation:
    """A divergent text block the vision THIRD-WITNESS tie-break GRADUATED to exact (``exact_visual``).

    The text-block analog of :class:`TableCellGraduation`: SYMMETRIC two-of-three graduation over
    {docling-text, pdfium-witness, vision-read}. ``agreeing_pair == pdfium_vision`` = Docling
    outvoted (content = pdfium); ``agreeing_pair == docling_vision`` = pdfium the corrupt-font
    dissenter (content = Docling). ``grade`` is always :data:`GRADE_EXACT_VISUAL` (exact-modulo-
    render, counted apart from the free-lane ``self_verified`` two-decoder exact).
    """

    block_index: int
    page_num: int
    kind: str
    corroborated_text: str
    vision_text: str
    agreeing_pair: str
    grade: str = GRADE_EXACT_VISUAL


@dataclass(frozen=True, slots=True)
class TextBlockVisionVerification:
    """Vision THIRD-WITNESS tie-break verdict for ONE deterministically-escalated appendix.

    The text-block analog of :class:`TableVisionVerification`. ``n_routed`` is the deterministic
    escalation set (the blocks the pdfium witness could not reconcile with Docling). Each is
    re-read by the render-based BLIND vision witness and adjudicated by symmetric two-of-three
    graduation into exactly one of three buckets:

    - ``graduated`` — TWO of {docling, pdfium, vision} agree (grade ``exact_visual``);
      ``pdfium_vision`` = Docling outvoted, ``docling_vision`` = pdfium the corrupt-font dissenter.
    - ``escalated`` — the vision model PUNTED (abstain/escalate), carrying its ``descriptor``: a
      terminal-tier typed status, NEVER graduated, distinct from ``open``.
    - ``open_divergences`` — vision corroborates neither text decoder: a genuinely-open typed
      divergence (each carrying the vision read + descriptor) for the adjudication tail.
    """

    locator: str
    n_routed: int
    graduated: Tuple[TextBlockGraduation, ...]
    escalated: Tuple[TextBlockDivergence, ...]
    open_divergences: Tuple[TextBlockDivergence, ...]

    @property
    def n_graduated(self) -> int:
        """Routed blocks graduated to exact (grade ``exact_visual``; two of three witnesses agree)."""
        return len(self.graduated)

    @property
    def n_read(self) -> int:
        """Routed blocks the vision witness actually re-read (all three buckets).

        Equals ``n_routed`` when unbudgeted; less when a ``max_cells`` cap curtailed the render
        spend (the un-read routed blocks fall in none of the three buckets)."""
        return (
            len(self.graduated)
            + len(self.escalated)
            + len(self.open_divergences)
        )

    @property
    def all_graduated(self) -> bool:
        """True iff every routed block the witness READ graduated (0 escalated, 0 open reads)."""
        return not self.escalated and not self.open_divergences

    def to_jsonable(self) -> Dict[str, object]:
        return {
            "locator": self.locator,
            "n_routed": self.n_routed,
            "n_read": self.n_read,
            "n_graduated": self.n_graduated,
            "graduated": [
                {
                    "block_index": g.block_index,
                    "page_num": g.page_num,
                    "kind": g.kind,
                    "corroborated_text": g.corroborated_text,
                    "vision": g.vision_text,
                    "agreeing_pair": g.agreeing_pair,
                    "grade": g.grade,
                    "tiebreak_status": "vision_corroborated_exact",
                }
                for g in self.graduated
            ],
            "escalated": [
                {
                    "block_index": d.block_index,
                    "page_num": d.page_num,
                    "kind": d.kind,
                    "docling": d.docling_text,
                    "vision": d.witness_text,
                    "descriptor": d.descriptor,
                    "outcome": VISION_OUTCOME_ESCALATED,
                }
                for d in self.escalated
            ],
            "open_divergences": [
                {
                    "block_index": d.block_index,
                    "page_num": d.page_num,
                    "kind": d.kind,
                    "docling": d.docling_text,
                    "vision": d.witness_text,
                    "descriptor": d.descriptor,
                    "outcome": VISION_OUTCOME_OPEN,
                }
                for d in self.open_divergences
            ],
        }


def verify_text_blocks_vision(
    blocks: Sequence[StructuredTextBlock],
    verification: TextBlockVerification,
    region_reader: Callable[[int, Tuple[float, float, float, float]], VisionReadResult],
    *,
    born_digital: bool = True,
    max_cells: Optional[int] = None,
) -> Optional[TextBlockVisionVerification]:
    """Vision THIRD-WITNESS tie-break over a 0-grid appendix's ``vision_escalate`` blocks.

    The block-for-cell mirror of :func:`verify_tables_vision`. When the deterministic lane
    routed this appendix ``vision_escalate`` (≥1 typed block divergence), re-read ONLY its
    divergent blocks' bbox regions through ``region_reader`` (the render-based BLIND witness;
    injected so this is hermetically testable) and adjudicate each by SYMMETRIC two-of-three
    graduation (:func:`_adjudicate_three_witnesses`) over {docling-text, pdfium-witness,
    vision-read}:

    - the block GRADUATES to exact (grade ``exact_visual``) iff TWO of the three witnesses agree —
      ``pdfium≡vision`` (Docling outvoted, content = pdfium) OR ``docling≡vision`` (pdfium the
      CORRUPT-FONT dissenter, content = Docling). A single witness never graduates.
    - a vision ABSTAIN is TERMINAL ``escalated`` (never graduated), carrying its descriptor.
    - else it is an ``open_divergence`` (vision corroborates neither text decoder).

    The two-of-three vote holds ONLY because the three readers are INDEPENDENT: the vision read is
    BLIND (never shown the candidate texts). SPARSE/SCANNED GUARD (identical to the table lane):
    graduation requires ``born_digital`` AND a non-empty agreed side. An appendix NOT routed to
    vision (self-verified / deferred) returns ``None`` (nothing to spend on). ``region_reader`` may
    return a plain ``str`` or a full :class:`VisionRegionRead`. ``max_cells`` caps the routed blocks
    re-read (``None`` unbounded); under a cap the un-read routed blocks stay in ``n_routed`` but
    appear in no bucket, so ``n_read`` (< ``n_routed``) is the sampled base. Pure apart from the
    injected reader.
    """
    if text_block_escalation_route(verification) != ROUTE_VISION_ESCALATE:
        return None
    blocks_by_index = {b.block_index: b for b in blocks}
    graduated: List[TextBlockGraduation] = []
    escalated: List[TextBlockDivergence] = []
    open_divergences: List[TextBlockDivergence] = []
    budget = max_cells
    for d in verification.divergences:
        if budget is not None and budget <= 0:
            break  # vision-spend budget exhausted; leave the rest un-read
        block = blocks_by_index.get(d.block_index)
        if block is None or block.bbox is None:  # divergent ⇒ had a bbox; defensive
            continue
        vision = _as_vision_read(region_reader(d.page_num, block.bbox))
        if budget is not None:
            budget -= 1
        adj = _adjudicate_three_witnesses(
            docling_text=d.docling_text,
            pdfium_witness=d.witness_text,
            vision=vision,
            born_digital=born_digital,
        )
        if adj.outcome == _VISION_OUTCOME_GRADUATED:
            # Two of {docling, pdfium, vision} agree (the third outvoted): EXACT modulo render.
            graduated.append(
                TextBlockGraduation(
                    block_index=d.block_index,
                    page_num=d.page_num,
                    kind=d.kind,
                    corroborated_text=adj.corroborated_text,
                    vision_text=adj.vision_text,
                    agreeing_pair=adj.agreeing_pair,
                )
            )
        elif adj.outcome == VISION_OUTCOME_ESCALATED:
            # The model PUNTED: terminal escalated, never graduated (descriptor recorded).
            escalated.append(
                TextBlockDivergence(
                    block_index=d.block_index,
                    page_num=d.page_num,
                    kind=d.kind,
                    docling_text=d.docling_text,
                    witness_text=adj.vision_text,
                    descriptor=adj.descriptor,
                )
            )
        else:
            # vision corroborated neither text decoder: genuinely open.
            open_divergences.append(
                TextBlockDivergence(
                    block_index=d.block_index,
                    page_num=d.page_num,
                    kind=d.kind,
                    docling_text=d.docling_text,
                    witness_text=adj.vision_text,
                    descriptor=adj.descriptor,
                )
            )
    return TextBlockVisionVerification(
        locator=verification.locator,
        n_routed=len(verification.divergences),
        graduated=tuple(graduated),
        escalated=tuple(escalated),
        open_divergences=tuple(open_divergences),
    )


def structured_table_from_node(
    node: SourceDocumentNode, *, locator: str, table_index: int
) -> StructuredTable:
    """Lower a Docling ``TABLE`` node (TABLE_ROW/TABLE_CELL children) into IR.

    Reads the cell text, the ``is_header`` attr, and the geometry the enhanced
    Docling adapter now threads onto ``anchor.bbox`` (normalized top-left points).
    ``n_cols`` is the widest row (ragged rows are possible before sanity checks).
    """
    row_nodes = tuple(
        c for c in node.children if c.kind is SourceDocumentNodeKind.TABLE_ROW
    )
    cells: List[StructuredCell] = []
    n_cols = 0
    for r_idx, row in enumerate(row_nodes):
        cell_nodes = tuple(
            c for c in row.children if c.kind is SourceDocumentNodeKind.TABLE_CELL
        )
        n_cols = max(n_cols, len(cell_nodes))
        for c_idx, cell in enumerate(cell_nodes):
            bb = cell.anchor.bbox
            cells.append(
                StructuredCell(
                    row=r_idx,
                    col=c_idx,
                    text=cell.text,
                    is_header=(cell.attrs or {}).get("is_header") == "1",
                    bbox=(bb.x0, bb.y0, bb.x1, bb.y1) if bb is not None else None,
                )
            )
    return StructuredTable(
        locator=locator,
        page_num=node.anchor.page_num or 0,
        table_index=table_index,
        n_rows=len(row_nodes),
        n_cols=n_cols,
        caption=node.text or "",
        cells=tuple(cells),
    )


# --------------------------------------------------------------------------- #
# INDEPENDENT GEOMETRIC RE-GRID WITNESS (Wire 2 — the structural mis-attribution gate). #
# --------------------------------------------------------------------------- #
#
# Docling's dual-table merge / duplicated-spanning-header failure produces a grid whose per-cell
# A≡B verdicts can ALL be exact (each phantom-column cell's own bbox reads its own text) while the
# TOPOLOGY — the (row,col) binding that carries the key→value meaning — is WRONG. The existing
# ``dual_table_merge_suspected`` flag catches these but is diagnostic-only. This independent witness
# re-derives the grid's (rows, cols) from the CELL BBOX GEOMETRY ALONE — clustering the cells' y- and
# x-intervals by single-linkage overlap, IGNORING Docling's own row/col labels — so a phantom column
# Docling counted is exposed as a geometry-vs-Docling count disagreement. (This mirrors the audit's
# G1 re-grid, which gates 9/9 real dual-merge topology errors and leaves the 1 geometry-concordant
# dual-merge false-alarm ungated.)


def _cluster_interval_count(
    intervals: Sequence[Tuple[float, float]],
    *,
    tol: float = 1.0,
    min_overlap_frac: float = 0.20,
) -> int:
    """Count geometric clusters of 1-D ``[lo, hi]`` intervals by single-linkage overlap (pure).

    Two intervals LINK iff they overlap by more than ``max(tol, min_overlap_frac * min_extent)``;
    the number of connected components is the independent geometric row (y-intervals) or column
    (x-intervals) count. Sorted-by-lo sweep with an early break (a later interval whose lo is past
    this one's hi + tol cannot overlap it). Faithful to the audit's G1 re-grid clusterer so the gate
    reproduces its witness exactly.
    """
    n = len(intervals)
    if n == 0:
        return 0
    parent = list(range(n))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    order = sorted(range(n), key=lambda i: intervals[i][0])
    for ii in range(n):
        a = order[ii]
        lo_a, hi_a = intervals[a]
        for jj in range(ii + 1, n):
            b = order[jj]
            lo_b, hi_b = intervals[b]
            if lo_b > hi_a + tol:
                break  # sorted by lo: no later interval can overlap this one
            overlap = min(hi_a, hi_b) - max(lo_a, lo_b)
            ext = min(hi_a - lo_a, hi_b - lo_b)
            thr = max(tol, min_overlap_frac * ext) if ext > 0 else tol
            if overlap > thr:
                union(a, b)
    return len({find(i) for i in range(n)})


def independent_grid_counts(table: StructuredTable) -> Tuple[int, int]:
    """Independent geometric ``(geo_rows, geo_cols)`` witness for a table (pure).

    Clusters the table's CELL BBOXES into geometric rows (y-interval single-linkage) and columns
    (x-interval single-linkage), deliberately IGNORING Docling's own ``(row, col)`` labels — that
    independence is the whole point (contrast :func:`_axis_bands`, which keys the bands ON those
    labels). A table with fewer than two bbox'd cells has no usable geometry, so it returns Docling's
    own ``(n_rows, n_cols)`` — geometry-concordant by construction, hence never gated.
    """
    y_intervals: List[Tuple[float, float]] = []
    x_intervals: List[Tuple[float, float]] = []
    for c in table.cells:
        bb = c.bbox
        if bb is None:
            continue
        y_intervals.append((min(bb[1], bb[3]), max(bb[1], bb[3])))
        x_intervals.append((min(bb[0], bb[2]), max(bb[0], bb[2])))
    if len(y_intervals) < 2:
        return (table.n_rows, table.n_cols)
    return (_cluster_interval_count(y_intervals), _cluster_interval_count(x_intervals))


@dataclass(frozen=True, slots=True)
class StructuralSanity:
    """Structural verdict on one table (rectangularity, header, dual-merge, geometric re-grid).

    ``geo_rows`` / ``geo_cols`` are the INDEPENDENT geometric-witness counts
    (:func:`independent_grid_counts`); ``structural_disagreement`` is the Wire-2 GATE: True iff the
    table is dual-merge-suspected AND that geometry disagrees with Docling's ``(n_rows, n_cols)`` —
    the phantom-column / duplicated-header topology error. A structurally-disagreeing table must NOT
    count self_verified/exact and must NOT graduate topology-wrong (it routes to vision/human).
    """

    rectangular: bool
    header_row_found: bool
    dual_table_merge_suspected: bool
    repeated_header_labels: Tuple[str, ...]
    n_bbox_cells: int
    n_cells: int
    detail: str
    geo_rows: int = 0
    geo_cols: int = 0
    structural_disagreement: bool = False


def structural_sanity(table: StructuredTable) -> StructuralSanity:
    """Structural fidelity checks over a structured table (pure).

    - RECTANGULAR: every row has the same non-zero cell count.
    - HEADER: at least one cell carries the header flag.
    - DUAL-TABLE MERGE (Docling's known failure): the header row's non-empty
      labels REPEAT (two side-by-side sub-tables share column headers), or the
      grid is anomalously wide. The repeated labels are surfaced as evidence.
    """
    per_row: Counter[int] = Counter()
    for c in table.cells:
        per_row[c.row] += 1
    counts = set(per_row.values())
    rectangular = table.n_rows > 0 and len(counts) == 1

    header_cells = [c for c in table.cells if c.is_header]
    header_row_found = bool(header_cells)

    # First (top) row's non-empty texts — the header labels if any, else row 0.
    top_row = min((c.row for c in table.cells), default=0)
    top_texts = [c.text.strip() for c in table.cells if c.row == top_row and c.text.strip()]
    seen: Counter[str] = Counter(top_texts)
    repeated = tuple(sorted(t for t, n in seen.items() if n >= 2))

    dual = bool(repeated) or table.n_cols >= _DUAL_MERGE_COL_THRESHOLD

    # WIRE 2 — independent geometric re-grid witness. Cluster the cell bboxes into geometric rows /
    # columns and gate a table whose DUAL-MERGE suspicion is CORROBORATED by a geometry-vs-Docling
    # count disagreement (the phantom column / duplicated spanning header). Geometry-concordant
    # dual-merge false-alarms (geo counts match Docling) are NOT gated.
    geo_rows, geo_cols = independent_grid_counts(table)
    structural_disagreement = dual and (
        geo_rows != table.n_rows or geo_cols != table.n_cols
    )

    n_bbox = sum(1 for c in table.cells if c.bbox is not None)
    detail_parts = [
        f"rows={table.n_rows}",
        f"cols={table.n_cols}",
        f"cells={len(table.cells)}",
        f"bbox_cells={n_bbox}",
        f"geo={geo_rows}x{geo_cols}",
    ]
    if repeated:
        detail_parts.append("repeated_header=" + "|".join(repeated))
    if structural_disagreement:
        detail_parts.append("structural_disagreement")
    return StructuralSanity(
        rectangular=rectangular,
        header_row_found=header_row_found,
        dual_table_merge_suspected=dual,
        repeated_header_labels=repeated,
        n_bbox_cells=n_bbox,
        n_cells=len(table.cells),
        detail="; ".join(detail_parts),
        geo_rows=geo_rows,
        geo_cols=geo_cols,
        structural_disagreement=structural_disagreement,
    )


# --------------------------------------------------------------------------- #
# Per-statute measurement (lazy docling / pdfium seam)                          #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class StatuteTableReport:
    """One statute's structured tables + the triangulated fidelity metrics."""

    locator: str
    artifact_digest: str
    n_pages: int
    mean_text_chars_per_page: float
    tables: Tuple[StructuredTable, ...]
    sanities: Tuple[StructuralSanity, ...]
    numeric: NumericRecall
    crosswitness: CrossWitness
    note: str = ""
    #: EXACT per-table cell verdicts (Docling cell ≡ pdfium-in-bbox, modulo op_equivalence).
    #: This is the HEADLINE — a table is verified only when every witnessable cell is exact.
    verifications: Tuple[TableVerification, ...] = ()
    #: VISION third-witness tie-break verdicts over the ``vision_escalate`` stratum (empty
    #: when the vision witness was not run): render-based graduation of routed cells whose
    #: independent vision read reproduces the pdfium witness (Docling outvoted).
    vision_verifications: Tuple[TableVisionVerification, ...] = ()
    #: TEXT-BLOCK LANE: for a 0-grid appendix with a text layer, the ordered verbatim text
    #: blocks (paragraphs / labelled formula lines) this PDF was structured into. Empty when
    #: the PDF yielded ≥1 grid table (the table lane owns it) or the text layer was sparse.
    text_blocks: Tuple[StructuredTextBlock, ...] = ()
    #: The single EXACT cross-witness verdict over ``text_blocks`` (None when the lane did
    #: not run): the 0-grid appendix as one verified-or-typed unit.
    text_block_verification: Optional[TextBlockVerification] = None
    #: VISION third-witness tie-break over the text-block ``vision_escalate`` stratum (None when
    #: the vision witness was not run or the appendix self-verified): render-based graduation of
    #: routed blocks whose independent vision read reproduces the pdfium witness (Docling outvoted).
    text_block_vision_verification: Optional[TextBlockVisionVerification] = None
    #: CORROBORATE-edge receipts for cells where the vision read materially disagreed with the
    #: deterministic grid (an open divergence) or a recall-screen suspect was confronted — each a
    #: jurisdiction-neutral :class:`CorroborationReceipt` (agreed / verdict_changed). Empty on the
    #: default (no-vision) path.
    corroboration_receipts: Tuple[CorroborationReceipt, ...] = ()
    #: RECALL-screen suspects: self-verified cells the gestalt screen flagged (clipped / incomplete
    #: / implausible) as candidates to escalate. Empty unless a gestalt reader was injected.
    screen_suspects: Tuple[RoutedVerdict, ...] = ()

    @property
    def text_block_route(self) -> Optional[str]:
        """Meta route for the 0-grid text-block appendix (None when the lane did not run)."""
        if self.text_block_verification is None:
            return None
        return text_block_escalation_route(self.text_block_verification)

    @property
    def n_text_blocks_exact(self) -> int:
        v = self.text_block_verification
        return v.n_exact if v is not None else 0

    @property
    def n_text_blocks_witnessed(self) -> int:
        v = self.text_block_verification
        return v.n_witnessed if v is not None else 0

    @property
    def n_text_blocks_routed_to_vision(self) -> int:
        """Deterministically-divergent text blocks handed to the vision second-witness."""
        v = self.text_block_vision_verification
        return v.n_routed if v is not None else 0

    @property
    def n_text_blocks_vision_graduated(self) -> int:
        """Routed text blocks the vision third-witness GRADUATED to exact (vision ≡ pdfium)."""
        v = self.text_block_vision_verification
        return v.n_graduated if v is not None else 0

    @property
    def n_text_blocks_exact_after_vision(self) -> int:
        """Block-exact count after the vision tie-break: deterministic exact + graduated."""
        return self.n_text_blocks_exact + self.n_text_blocks_vision_graduated

    @property
    def n_cells_verified(self) -> int:
        # WIRE 2: a structurally-disagreeing table's per-cell exact verdicts are topology-wrong,
        # so its cells do NOT count self_verified/exact (the gate demotes the whole table).
        return sum(
            v.n_exact
            for v, s in zip(self.verifications, self.sanities, strict=True)
            if not s.structural_disagreement
        )

    @property
    def n_cells_structural_disagreement(self) -> int:
        """Cells DEMOTED from exact by the Wire-2 structural gate (topology-wrong dual-merge)."""
        return sum(
            v.n_exact
            for v, s in zip(self.verifications, self.sanities, strict=True)
            if s.structural_disagreement
        )

    @property
    def n_cells_routed_to_vision(self) -> int:
        """Deterministically-divergent cells handed to the vision second-witness."""
        return sum(v.n_routed for v in self.vision_verifications)

    @property
    def n_cells_vision_graduated(self) -> int:
        """Routed cells the vision third-witness GRADUATED to exact (vision ≡ pdfium witness)."""
        return sum(v.n_graduated for v in self.vision_verifications)

    @property
    def n_cells_exact_after_vision(self) -> int:
        """Cell-exact count after the vision tie-break: deterministic exact + graduated."""
        return self.n_cells_verified + self.n_cells_vision_graduated

    @property
    def n_cells_witnessed(self) -> int:
        """Cells that had a bbox to cross-verify (exact + divergent; excludes no_witness)."""
        return sum(v.n_exact + len(v.divergences) for v in self.verifications)

    @property
    def n_tables_exact(self) -> int:
        # A structurally-disagreeing table is NOT exact even with 0 per-cell divergences (its
        # topology is wrong), so the Wire-2 gate excludes it from the exact-table count.
        return sum(
            1
            for v, s in zip(self.verifications, self.sanities, strict=True)
            if v.exact and not s.structural_disagreement
        )

    @property
    def routes(self) -> Tuple[str, ...]:
        """Per-table meta-level routing verdict (structural_disagreement / self-verified / vision / deferred)."""
        return tuple(
            table_route_with_structure(v, s)
            for v, s in zip(self.verifications, self.sanities, strict=True)
        )

    @property
    def n_tables_vision_escalate(self) -> int:
        """Tables the deterministic lane could not verify → routed to a vision witness."""
        return sum(1 for r in self.routes if r == ROUTE_VISION_ESCALATE)

    @property
    def n_tables_structural_disagreement(self) -> int:
        """Tables the Wire-2 geometric re-grid convicted of a topology error (typed, not exact)."""
        return sum(1 for r in self.routes if r == ROUTE_STRUCTURAL_DISAGREEMENT)

    def to_jsonable(self) -> Dict[str, object]:
        payload: Dict[str, object] = {
            "locator": self.locator,
            "artifact_digest": self.artifact_digest,
            "n_pages": self.n_pages,
            "mean_text_chars_per_page": round(self.mean_text_chars_per_page, 1),
            "n_tables": len(self.tables),
            "tables": [
                {
                    "page_num": t.page_num,
                    "table_index": t.table_index,
                    "n_rows": t.n_rows,
                    "n_cols": t.n_cols,
                    "n_cells": len(t.cells),
                    "rectangular": s.rectangular,
                    "header_row_found": s.header_row_found,
                    "dual_table_merge_suspected": s.dual_table_merge_suspected,
                    "repeated_header_labels": list(s.repeated_header_labels),
                    "n_bbox_cells": s.n_bbox_cells,
                    "geo_rows": s.geo_rows,
                    "geo_cols": s.geo_cols,
                    "structural_disagreement": s.structural_disagreement,
                }
                for t, s in zip(self.tables, self.sanities, strict=True)
            ],
            "numeric_recall": {
                "n_reference": self.numeric.n_reference,
                "n_recovered": self.numeric.n_recovered,
                "recall": round(self.numeric.recall, 4),
                "n_missing": len(self.numeric.missing),
                "missing_sample": list(self.numeric.missing[:20]),
            },
            "cross_witness": {
                "agreement": round(self.crosswitness.agreement, 4),
                "n_shared": self.crosswitness.n_shared,
                "n_docling_only": self.crosswitness.n_docling_only,
                "n_layer_only": self.crosswitness.n_layer_only,
            },
            "exact_verification": {
                "n_tables_exact": self.n_tables_exact,
                "n_tables": len(self.tables),
                "n_cells_verified": self.n_cells_verified,
                "n_cells_witnessed": self.n_cells_witnessed,
                "n_tables_vision_escalate": self.n_tables_vision_escalate,
                "n_tables_structural_disagreement": self.n_tables_structural_disagreement,
                "n_cells_structural_disagreement": self.n_cells_structural_disagreement,
                "tables": [
                    {**v.to_jsonable(), "route": route}
                    for v, route in zip(self.verifications, self.routes, strict=True)
                ],
            },
            "vision_third_witness_tiebreak": {
                "ran": bool(self.vision_verifications),
                "n_cells_routed": self.n_cells_routed_to_vision,
                "n_cells_graduated": self.n_cells_vision_graduated,
                "n_cells_exact_after_vision": self.n_cells_exact_after_vision,
                "tables": [vv.to_jsonable() for vv in self.vision_verifications],
            },
            "text_block_lane": {
                "ran": self.text_block_verification is not None,
                "route": self.text_block_route,
                "n_blocks": len(self.text_blocks),
                "n_blocks_exact": self.n_text_blocks_exact,
                "n_blocks_witnessed": self.n_text_blocks_witnessed,
                "verification": (
                    self.text_block_verification.to_jsonable()
                    if self.text_block_verification is not None
                    else None
                ),
                "vision_third_witness_tiebreak": {
                    "ran": self.text_block_vision_verification is not None,
                    "n_blocks_routed": self.n_text_blocks_routed_to_vision,
                    "n_blocks_graduated": self.n_text_blocks_vision_graduated,
                    "n_blocks_exact_after_vision": self.n_text_blocks_exact_after_vision,
                    "verification": (
                        self.text_block_vision_verification.to_jsonable()
                        if self.text_block_vision_verification is not None
                        else None
                    ),
                },
            },
            "note": self.note,
        }
        # CONDITIONAL keys (byte-identical default path): only present when the vision/screen
        # lanes actually produced a receipt / suspect, so a no-vision run's JSON is unchanged.
        if self.corroboration_receipts:
            payload["corroboration"] = {
                "n_receipts": len(self.corroboration_receipts),
                "n_agreed": sum(1 for r in self.corroboration_receipts if r.agreed),
                "n_verdict_changed": sum(
                    1 for r in self.corroboration_receipts if r.verdict_changed
                ),
                "receipts": [r.to_json() for r in self.corroboration_receipts],
            }
        if self.screen_suspects:
            payload["recall_screen"] = {
                "n_suspects": len(self.screen_suspects),
                "suspects": [s.to_jsonable() for s in self.screen_suspects],
            }
        return payload


def _page_texts(pdf_bytes: bytes) -> List[str]:
    """Per-page pdfium text-layer strings (lazy import; pdfium critical section)."""
    import importlib

    pdfium = importlib.import_module("pypdfium2")
    with _PDFIUM_LOCK:
        doc = pdfium.PdfDocument(pdf_bytes)
        try:
            out: List[str] = []
            for i in range(len(doc)):
                textpage = doc[i].get_textpage()
                try:
                    out.append(textpage.get_text_range())
                finally:
                    close = getattr(textpage, "close", None)
                    if close is not None:
                        close()
            return out
        finally:
            doc.close()


#: Points to inset each Docling cell bbox on the PER-CELL FALLBACK pdfium read (only used
#: when the geometry-reconciliation path bails on ambiguous bboxes). Kept SUB-POINT so it
#: never clips a trailing glyph — the ``'Nimi'→'Nim'`` edge-clip was the 2 pt inset eating
#: the last character. The primary path (``reconcile_table_witness``) reads whole text-runs
#: with NO inset, so trailing glyphs are never clipped there; neighbour-bleed on the primary
#: path is handled by x-band assignment, not by an inset, which is why this can shrink.
_BBOX_INSET = 0.5

#: pypdfium2 renders an UNMAPPED soft/discretionary-hyphen glyph (a font layout hint with no
#: ToUnicode entry) as a NONCHARACTER placeholder. Its two text-extraction APIs disagree on
#: WHICH placeholder: ``get_text_range`` (the per-CHAR run harvest + every other pdfium read in
#: the pipeline) emits U+FFFE — the sentinel the canonical :func:`dehyphenate` already resolves,
#: FUSING a genuine soft break while PRESERVING a real compound ("työmaa-asunnot") via its
#: corroboration — whereas ``get_text_bounded`` (the per-cell bbox witness) emits U+0002 (STX)
#: for the SAME glyph. Left as-is, that U+0002 is not a soft-hyphen to ``dehyphenate``; it is
#: stripped as C0 control noise (``CONTROL_STRIP``) and the wrap collapses to a SPACE, splitting
#: a word both the source PDF and Docling's cell text join ("työmaa-asunnot" → "työmaa asunnot")
#: — a spurious cell divergence. Normalizing the bounded read's sentinel to the canonical U+FFFE
#: makes the two pdfium APIs consistent and lets the EXISTING dehyphenate handle it identically to
#: every other read — no new hyphen policy, no reinvented primitive. U+0002 never occurs as
#: legitimate text-layer content, so the map is unconditional (``dehyphenate``'s compound
#: corroboration still guards genuine hyphens); it can only turn a spuriously-split witness into
#: one that matches Docling, so it is strictly non-regressive on the EXACT verdict.
_PDFIUM_BOUNDED_SOFT_HYPHEN = "\u0002"
_PDFIUM_CANONICAL_SOFT_HYPHEN = "\uFFFE"


def _normalize_pdfium_bounded_sentinel(text: str) -> str:
    """Map ``get_text_bounded``'s U+0002 soft-hyphen sentinel to the canonical U+FFFE (pure).

    Makes the bounded per-cell witness consistent with every other pdfium read so the canonical
    :func:`~lawvm.ingest.page_elements.dehyphenate` (run by ``text_equivalence``) resolves a
    wrapped soft-hyphen instead of the C0-control strip collapsing it to a word-splitting space.
    """
    return text.replace(_PDFIUM_BOUNDED_SOFT_HYPHEN, _PDFIUM_CANONICAL_SOFT_HYPHEN)


def _page_text_runs(textpage: Any, page_height: float) -> List[TextRun]:
    """Harvest the page text layer as PER-CHARACTER :class:`TextRun`s in TOP-LEFT points.

    Deliberately CHARACTER granularity, not pdfium's ``get_rect`` runs: those rects span a
    whole visual LINE (all columns at once), so assigning one by its centre would dump an
    entire row into a single column. A character's own box localises it to exactly one
    (row,col) band, which is what makes the wrapped-tail reconciliation faithful. Each char's
    box (``get_charbox`` → bottom-left ``(left, bottom, right, top)``) is flipped to the
    StructuredCell top-left frame (``y := page_height - y``). Ordinary spaces are KEPT (they
    are real chars with a box, so cell text reconstructs with its spacing); newline/tab/other
    control chars — degenerate boxes, folded away downstream anyway — are dropped. Any pdfium
    hiccup on a char is skipped (never a crash).
    """
    runs: List[TextRun] = []
    try:
        n_chars = textpage.count_chars()
    except Exception:
        return runs
    for i in range(n_chars):
        try:
            ch = textpage.get_text_range(i, 1)
            left, bottom, right, top = textpage.get_charbox(i)
        except Exception:
            continue
        if not ch or (ch.isspace() and ch != " "):  # keep the space glyph; drop \r\n\t etc.
            continue
        runs.append(
            TextRun(
                text=ch,
                x0=left,
                y0=page_height - top,
                x1=right,
                y1=page_height - bottom,
            )
        )
    return runs


def _make_per_bbox_reader(
    textpage: Any, page_height: Optional[float]
) -> Callable[[Tuple[float, float, float, float]], str]:
    """Build the conservative per-cell bbox pdfium reader (the reconciliation FALLBACK).

    Reads the text inside a cell's OWN bbox (top-left points → pdfium bottom-left), with a
    sub-point inset that trims neighbour edge-bleed without clipping a trailing glyph. Only
    used when :func:`reconcile_table_witness` bails on ambiguous geometry; the primary path
    reconstructs from whole runs and never insets.
    """

    def read(bbox: Tuple[float, float, float, float]) -> str:
        if textpage is None or page_height is None:
            return ""
        x0, y0, x1, y1 = bbox
        lo_x, hi_x = min(x0, x1), max(x0, x1)
        lo_y, hi_y = min(y0, y1), max(y0, y1)
        mx = min(_BBOX_INSET, (hi_x - lo_x) / 3.0)
        my = min(_BBOX_INSET, (hi_y - lo_y) / 3.0)
        try:  # pdfium wants (left, bottom, right, top) in bottom-left origin
            raw = textpage.get_text_bounded(
                left=lo_x + mx, bottom=page_height - (hi_y - my),
                right=hi_x - mx, top=page_height - (lo_y + my),
            )
        except Exception:
            return ""
        # ``get_text_bounded`` renders an unmapped soft-hyphen glyph as U+0002; normalise it to the
        # canonical U+FFFE the pipeline's ``dehyphenate`` resolves (see the sentinel constant above).
        return _normalize_pdfium_bounded_sentinel(raw)

    return read


def _verify_tables_against_pdfium(
    pdf_bytes: bytes, tables: Sequence[StructuredTable]
) -> Tuple[TableVerification, ...]:
    """Exact-verify every table via GEOMETRY-RECONCILED pdfium witnesses (Fix-1 primary path).

    Opens the PDF ONCE, harvests every page's text-runs, and for each table reconstructs a
    ``(row,col)`` witness with :func:`reconcile_table_witness` (re-assigning wrapped-line
    tails to the column their geometry belongs to), falling back to the per-cell bbox read
    only where the table's bboxes are ambiguous. The reconciled/fallback text is handed to
    the unchanged :func:`verify_table_exact` exactness contract. Any pdfium hiccup yields an
    empty witness (→ a typed divergence, never a crash).
    """
    import importlib

    pdfium = importlib.import_module("pypdfium2")
    with _PDFIUM_LOCK:
        doc = pdfium.PdfDocument(pdf_bytes)
        try:
            heights = {i: doc[i].get_size()[1] for i in range(len(doc))}
            textpages = {i: doc[i].get_textpage() for i in range(len(doc))}
            try:
                runs_by_page = {
                    i: _page_text_runs(textpages[i], heights[i]) for i in range(len(doc))
                }
                results: List[TableVerification] = []
                for table in tables:
                    idx = table.page_num - 1  # Docling 1-indexed → pdfium 0-indexed
                    per_bbox = _make_per_bbox_reader(textpages.get(idx), heights.get(idx))
                    reconciled = reconcile_table_witness(
                        table, runs_by_page.get(idx, ()), per_bbox
                    )

                    def bbox_witness(
                        page_num: int,
                        bbox: Tuple[float, float, float, float],
                        _table: StructuredTable = table,
                        _recon: Dict[Tuple[int, int], str] = reconciled,
                        _per: Callable[[Tuple[float, float, float, float]], str] = per_bbox,
                    ) -> str:
                        # Resolve this bbox back to its (row,col) within the table so the
                        # reconciled witness is used; any bbox not reconciled (defensive)
                        # falls back to the conservative per-cell read.
                        for cell in _table.cells:
                            if cell.bbox == bbox:
                                got = _recon.get((cell.row, cell.col))
                                return got if got is not None else _per(bbox)
                        return _per(bbox)

                    results.append(verify_table_exact(table, bbox_witness))
                return tuple(results)
            finally:
                for tp in textpages.values():
                    close = getattr(tp, "close", None)
                    if close is not None:
                        close()
        finally:
            doc.close()


#: The DPI a routed cell's region crop is rendered at before the vision read. What
#: recovers a glyph a whole-page read drops is ISOLATION (the cell cropped into its
#: own image), not raw zoom — this is a sharpness bound on the isolated crop.
_VISION_REGION_DPI = 300


def _pdf_page_heights(pdf_bytes: bytes) -> Dict[int, float]:
    """1-indexed page_num → page height in points (for the top-left↔bottom-left flip).

    The StructuredCell bbox is top-left origin (points); ``render_region_crop`` wants
    bottom-left, so the vision region reader flips y against the page height. Reads
    the heights ONCE under the systemic pdfium lock (the render path uses the same
    canonical lock)."""
    import importlib

    from lawvm.ingest.visual import PDFIUM_LOCK

    pdfium = importlib.import_module("pypdfium2")
    with PDFIUM_LOCK:
        doc = pdfium.PdfDocument(pdf_bytes)
        try:
            return {i + 1: float(doc[i].get_size()[1]) for i in range(len(doc))}
        finally:
            doc.close()


#: The vision model's BLIND transcription came back empty (the ``UNREADABLE`` token or an empty
#: reply) — the model DECLINED to read. Mapped to an abstain (→ terminal ``escalated``), never a
#: silent empty that could look like a genuine reading.
_VISION_DECLINED_DESCRIPTOR = "vision_declined_unreadable"


def make_vision_region_reader(
    producer: Any,
    pdf_bytes: bytes,
    *,
    artifact_digest: str,
    locator: str,
    dpi: int = _VISION_REGION_DPI,
    expected_lines: int = 1,
) -> Callable[[int, Tuple[float, float, float, float]], VisionRegionRead]:
    """Build the production render-based region reader (the injectable BLIND vision witness).

    Returns a ``region_reader(page_num, bbox) -> VisionRegionRead``. It renders JUST the cell's
    bbox region (region-isolation crop via ``ingest.visual.render_region_crop``, invoked inside
    ``producer.read_region_cold``) and reads that pixel region back to text through the :8080
    vision producer — an INDEPENDENT, BLIND witness. PASS-1 IS BLIND BY CONSTRUCTION: only the
    rendered pixels + the fixed ``_COLD_REGION_SYSTEM_PROMPT`` (a pure transcription instruction)
    reach the model — the pdfium/Docling candidate texts are NEVER passed, so the read cannot echo
    a candidate and witness independence is preserved. The controller (not the model) compares the
    blind read to the candidates.

    The cell bbox is top-left points; ``render_region_crop`` is bottom-left, so y is flipped against
    the page height (read once up front). Outcomes map to :class:`VisionRegionRead`: a confident
    read → ``VisionRegionRead(text=...)``; the model DECLINING (empty / ``UNREADABLE``) → an ABSTAIN
    (→ terminal ``escalated``); a render/read FAILURE or degenerate box → an abstain carrying the
    failure reason as the descriptor — never a crash, never a silent empty that graduates. Temp is
    pinned to 0 at the producer and the model id is recorded on each read (cache-invalidation key).
    Vision imports stay lazy here (determinism firewall)."""
    from datetime import datetime, timezone

    from lawvm.core.source_document.anchors import BBox
    from lawvm.core.source_document.extraction import SourceManifestation
    from lawvm.ingest.llm_backends.vision_producer import (
        VisionProducerFailure,
        VisionProducerTruncated,
    )

    heights = _pdf_page_heights(pdf_bytes)
    model_id = _resolve_producer_model_id(producer)
    manifestation = SourceManifestation(
        artifact_digest=artifact_digest or ("0" * 64),
        source_bytes=pdf_bytes,
        locator=locator,
        source_role="statute",
        fetched_at=datetime.now(timezone.utc),
        media_type="application/pdf",
    )

    def region_reader(
        page_num: int, bbox: Tuple[float, float, float, float]
    ) -> VisionRegionRead:
        h = heights.get(page_num)
        if h is None:
            return VisionRegionRead(
                text="", abstain=True, descriptor="no_such_page", model_id=model_id
            )
        x0, y0, x1, y1 = bbox
        lo_x, hi_x = min(x0, x1), max(x0, x1)
        lo_y, hi_y = min(y0, y1), max(y0, y1)  # top-left: lo_y=top edge, hi_y=bottom edge
        try:  # flip to bottom-left origin for render_region_crop; BBox validates area
            bb = BBox(x0=lo_x, y0=h - hi_y, x1=hi_x, y1=h - lo_y)
        except ValueError:
            return VisionRegionRead(
                text="", abstain=True, descriptor="degenerate_bbox", model_id=model_id
            )
        try:
            text = producer.read_region_cold(
                manifestation, page_num, bb, dpi=dpi, expected_lines=expected_lines
            )
        except (VisionProducerFailure, VisionProducerTruncated) as exc:
            # A transport/render/truncation failure is a PUNT, not a reading → escalate.
            return VisionRegionRead(
                text="", abstain=True,
                descriptor=f"vision_producer_failure:{type(exc).__name__}",
                model_id=model_id,
            )
        if not text.strip():
            # The model DECLINED (empty / UNREADABLE): an abstain, never a graduating empty.
            return VisionRegionRead(
                text="", abstain=True,
                descriptor=_VISION_DECLINED_DESCRIPTOR, model_id=model_id,
            )
        return VisionRegionRead(text=text, abstain=False, descriptor="", model_id=model_id)

    return region_reader


def _resolve_producer_model_id(producer: Any) -> str:
    """Best-effort vision model id for the cache key (a model bump must re-key the cache).

    Prefers the producer's own ``_resolve_model`` (the id the server will actually serve); on any
    failure falls back to a stable sentinel so the reader never crashes at wiring time."""
    resolver = getattr(producer, "_resolve_model", None)
    if callable(resolver):
        try:
            return str(resolver())
        except Exception:
            pass
    return str(getattr(producer, "_model", None) or "unresolved-vision-model")


# --------------------------------------------------------------------------- #
# DETERMINISM-FIREWALL CACHE for the (non-deterministic) vision reads.          #
# --------------------------------------------------------------------------- #
#
# Vision is NOT byte-deterministic across runs, but the pipeline's replay / self-consistency
# invariants require that a re-run reproduce the SAME graduation verdicts. Per the ``parsed_store``
# discipline (an LLM-derived result may enter downstream consumers ONLY as a content-addressed
# evidence record carrying the producing model id), this is that record cache for a single BLIND
# region read — mirroring :mod:`lawvm.finland.he_payload_verdict_store`:
#
#   * A read is keyed by ``SHA-256(schema, model-id, prompt-fingerprint, render-params,
#     image-bytes-hash)``. Temp is pinned to 0 at the producer, so the ONLY run-to-run variance is
#     the model's own noise — which the cache freezes. A model UPGRADE (new id), a PROMPT edit (new
#     fingerprint), a DPI/render change, or a different crop (new image hash) all change the key and
#     write a NEW record without overwriting the old (versioned, auditable, no stale read).
#   * The materialized row carries the blind ``text`` PLUS the abstain flag and ``descriptor`` — the
#     model's punt/reason is part of the evidence, so a cached ``escalated`` replays identically.
#   * The store is a farchive (sibling to ``data/fi_parsed_ir.farchive``) exactly like the derived
#     IR store; the row is deterministic sorted-keys JSON. The reader stays INJECTED at the boundary
#     so the cache-through path is hermetically testable with a scripted reader + a tmp store.

#: Default sibling derived-store path (mirrors ``FI_PARSED_STORE`` / the verdict store).
FI_VISION_READ_STORE = "data/fi_vision_region_reads.farchive"

#: Bump when the row SHAPE or the KEY construction changes (independently of the prompt fingerprint)
#: so a superseded row layout never shadows a fresh read.
_VISION_READ_CACHE_SCHEMA = "vision_read.v1"


def render_params_fingerprint(dpi: int) -> str:
    """The render-params component of the cache key (crop pipeline id + DPI).

    A DPI change re-keys (a different rasterisation is a different image the model may read
    differently); the pipeline id pins the ``render_region_crop`` region-isolation crop.
    """
    return f"render_region_crop/dpi={dpi}"


def cold_region_prompt_fingerprint() -> str:
    """Fingerprint the FROZEN blind cold-region prompt (system + user instruction + temp pin).

    Folds the exact strings sent to the model so ANY prompt edit re-keys the cache. Lazily imports
    the prompt constant (determinism firewall — no import-time vision dependency)."""
    from lawvm.ingest.llm_backends.vision_producer import _COLD_REGION_SYSTEM_PROMPT

    h = hashlib.sha256()
    for part in (
        "cold_region.v1",
        _COLD_REGION_SYSTEM_PROMPT,
        "Transcribe every line of text in this cropped region.",
        "temperature=0",
    ):
        b = part.encode("utf-8")
        h.update(str(len(b)).encode("ascii"))
        h.update(b"\x00")
        h.update(b)
    return h.hexdigest()


def vision_read_cache_key(
    *, model_id: str, prompt_fingerprint: str, render_fingerprint: str, image_sha256: str
) -> str:
    """Content-address a blind vision read by (schema, model-id, prompt, render-params, image-hash).

    Each component is length-prefixed then NUL-joined so no two distinct component tuples can
    collide on one digest. Pure — the SAME inputs always yield the SAME key, so a re-run over the
    same crop under the same model + prompt is a HIT.
    """
    h = hashlib.sha256()
    for part in (
        _VISION_READ_CACHE_SCHEMA,
        model_id,
        prompt_fingerprint,
        render_fingerprint,
        image_sha256,
    ):
        b = part.encode("utf-8")
        h.update(str(len(b)).encode("ascii"))
        h.update(b"\x00")
        h.update(b)
    return h.hexdigest()


def vision_read_locator(key: str) -> str:
    """Content-addressed store locator for a vision-read key (per-digest record)."""
    return f"fi_vision_region_read/{key}"


@dataclass(frozen=True, slots=True)
class VisionReadRow:
    """The persisted, self-describing vision-read evidence record — the TYPED carrier crossing the
    store seam (a named record, never a bare ``dict``). Field names are exactly the persisted JSON
    keys, so serialization is a mechanical :func:`dataclasses.asdict` round-trip."""

    text: str
    abstain: bool
    descriptor: str
    model_id: str
    prompt_fingerprint: str
    render_fingerprint: str
    image_sha256: str
    schema_version: str
    created_at: str

    def to_vision_read(self) -> VisionRegionRead:
        """Rehydrate the cached blind read (byte-identical to the original on replay)."""
        return VisionRegionRead(
            text=self.text,
            abstain=self.abstain,
            descriptor=self.descriptor,
            model_id=self.model_id,
        )


class VisionReadStore:
    """A farchive of content-addressed blind vision reads (the vision determinism-firewall cache)."""

    def __init__(self, path: str = FI_VISION_READ_STORE) -> None:
        from farchive import Farchive

        self._fa = Farchive(path)
        self.path = path

    def get(self, key: str) -> Optional[VisionReadRow]:
        """Read a persisted vision-read row by key (``None`` on miss)."""
        span = self._fa.resolve(vision_read_locator(key))
        if span is None:
            return None
        data = self._fa.read(span.digest)
        if data is None:
            return None
        return VisionReadRow(**json.loads(data.decode("utf-8")))

    def put(self, key: str, row: VisionReadRow) -> str:
        """Persist one vision-read row (deterministic sorted-keys JSON); returns the blob digest."""
        from dataclasses import asdict

        return self._fa.store(
            vision_read_locator(key),
            json.dumps(asdict(row), ensure_ascii=False, sort_keys=True).encode("utf-8"),
            storage_class="fi_vision_region_read",
            metadata={"model_id": row.model_id, "abstain": str(row.abstain)},
        )

    def close(self) -> None:
        self._fa.close()


def _vision_read_row(
    read: VisionRegionRead,
    *,
    model_id: str,
    prompt_fingerprint: str,
    render_fingerprint: str,
    image_sha256: str,
) -> VisionReadRow:
    """Build the persisted vision-read evidence row (carries text + abstain + descriptor)."""
    from datetime import datetime, timezone

    return VisionReadRow(
        text=read.text,
        abstain=read.abstain,
        descriptor=read.descriptor,
        model_id=model_id,
        prompt_fingerprint=prompt_fingerprint,
        render_fingerprint=render_fingerprint,
        image_sha256=image_sha256,
        schema_version=_VISION_READ_CACHE_SCHEMA,
        created_at=datetime.now(tz=timezone.utc).isoformat(),
    )


def make_cached_region_reader(
    inner: Callable[[int, Tuple[float, float, float, float]], VisionReadResult],
    *,
    store: VisionReadStore,
    model_id: str,
    prompt_fingerprint: str,
    render_fingerprint: str,
    crop_digest: Callable[[int, Tuple[float, float, float, float]], Optional[str]],
) -> Callable[[int, Tuple[float, float, float, float]], VisionRegionRead]:
    """Wrap a region reader with the determinism-firewall cache (HIT = zero model calls).

    ``crop_digest(page_num, bbox) -> image-bytes-sha256`` is the deterministic hash of the rendered
    region crop (the image the model reads); it is the image-bytes-hash component of the key. On a
    HIT the stored row rehydrates the blind read WITHOUT invoking ``inner`` (so replay is
    byte-identical and free); on a MISS ``inner`` is run ONCE and the read — text, abstain flag AND
    descriptor — is persisted content-addressed. A model bump (``model_id``), prompt edit, render
    change, or different crop all re-key (a fresh MISS). If the crop cannot be rendered/hashed
    (``crop_digest`` returns ``None``) the read is passed through UNCACHED (``inner`` will itself
    abstain on the same failure), never a crash.
    """

    def cached(page_num: int, bbox: Tuple[float, float, float, float]) -> VisionRegionRead:
        image_sha256 = crop_digest(page_num, bbox)
        if image_sha256 is None:
            return _as_vision_read(inner(page_num, bbox))
        key = vision_read_cache_key(
            model_id=model_id,
            prompt_fingerprint=prompt_fingerprint,
            render_fingerprint=render_fingerprint,
            image_sha256=image_sha256,
        )
        row = store.get(key)
        if row is not None:
            return row.to_vision_read()
        read = _as_vision_read(inner(page_num, bbox))
        store.put(
            key,
            _vision_read_row(
                read,
                model_id=model_id,
                prompt_fingerprint=prompt_fingerprint,
                render_fingerprint=render_fingerprint,
                image_sha256=image_sha256,
            ),
        )
        return read

    return cached


def make_region_crop_digester(
    pdf_bytes: bytes,
    *,
    artifact_digest: str,
    locator: str,
    dpi: int = _VISION_REGION_DPI,
) -> Callable[[int, Tuple[float, float, float, float]], Optional[str]]:
    """Production image-bytes-hash source: render each region crop and SHA-256 its bytes.

    The crop is rendered by the SAME ``render_region_crop`` path the vision read uses (identical
    top-left→bottom-left flip + DPI), so the hash content-addresses exactly the image the model
    reads. Returns ``None`` on a render failure / degenerate box (the read is then passed through
    uncached). Vision/pdfium imports stay lazy (determinism firewall)."""
    from lawvm.core.source_document.anchors import BBox
    from lawvm.core.source_document.extraction import SourceManifestation
    from lawvm.ingest.visual import RegionRenderFailure, render_region_crop
    from datetime import datetime, timezone

    heights = _pdf_page_heights(pdf_bytes)
    manifestation = SourceManifestation(
        artifact_digest=artifact_digest or ("0" * 64),
        source_bytes=pdf_bytes,
        locator=locator,
        source_role="statute",
        fetched_at=datetime.now(timezone.utc),
        media_type="application/pdf",
    )

    def crop_digest(
        page_num: int, bbox: Tuple[float, float, float, float]
    ) -> Optional[str]:
        h = heights.get(page_num)
        if h is None:
            return None
        x0, y0, x1, y1 = bbox
        lo_x, hi_x = min(x0, x1), max(x0, x1)
        lo_y, hi_y = min(y0, y1), max(y0, y1)
        try:
            bb = BBox(x0=lo_x, y0=h - hi_y, x1=hi_x, y1=h - lo_y)
        except ValueError:
            return None
        try:
            crop = render_region_crop(manifestation, page_num, bb, dpi=dpi)
        except RegionRenderFailure:
            return None
        return hashlib.sha256(crop).hexdigest()

    return crop_digest


# --------------------------------------------------------------------------- #
# CONSUMED DERIVED-IR SINK for the verified structured appendix tables.         #
# --------------------------------------------------------------------------- #
#
# ``write_tables_jsonl`` is a REPORT dead-end — a flat dump nothing reads back. The verified
# structured-IR (every table the deterministic lane self-verified EXACT, or the vision third-
# witness GRADUATED to ``exact_visual``) is the actual phase-3 PRODUCT and must land in a
# CONSUMABLE, content-addressed store the structured-law corpus can surface, not a throwaway
# JSONL. This sink mirrors :class:`VisionReadStore` (a sibling farchive) and the ``parsed_store``
# determinism-firewall discipline: a derived table enters ONLY as a self-describing record keyed
# by ``(artifact_digest, table_index, CODE FINGERPRINT)``. The code fingerprint folds the
# exactness-quotient contract (the closed ``EncodingFold`` vocabulary — the folds that decide
# whether a cell VERIFIES) via the canonical :func:`prompt_fingerprint`, so a quotient edit (which
# could change WHICH tables verify) re-keys every record without overwriting the old — a versioned,
# auditable derived-IR store, never a stale verified table.

#: Default sibling derived-IR store path (gitignored; mirrors ``FI_VISION_READ_STORE``).
FI_DERIVED_IR_STORE = "data/fi_appendix_structured_ir.farchive"

#: Bump when the row SHAPE / key construction changes (independently of the quotient fingerprint).
_DERIVED_IR_SCHEMA = "appendix_structured_table.v1"


def derived_ir_fingerprint() -> str:
    """Fingerprint the derived-IR contract: the exactness quotient + this sink's grade vocabulary.

    Folds the closed ``EncodingFold`` quotient vocabulary (the legally-inert folds that decide
    whether a cell VERIFIES) and the grade vocabulary through the canonical
    :func:`prompt_fingerprint`, so any change to the quotient — which could change which tables
    graduate to exact — MECHANICALLY re-keys every derived record (no stale verified IR served
    under a superseded contract). Reinvents nothing (the determinism-firewall key discipline)."""
    return prompt_fingerprint(
        _DERIVED_IR_SCHEMA,
        GRADE_SELF_VERIFIED,
        GRADE_EXACT_VISUAL,
        vocab=tuple(f.value for f in EncodingFold),
    )


def derived_table_key(
    *, artifact_digest: str, table_index: int, code_fingerprint: str
) -> str:
    """Content-address a derived table by (schema, artifact-digest, table-index, code-fingerprint).

    Each component is length-prefixed then NUL-joined so no two distinct tuples collide on one
    digest. Pure — the SAME table under the SAME quotient contract always yields the SAME key (a
    re-run is a HIT, never a duplicate)."""
    h = hashlib.sha256()
    for part in (
        _DERIVED_IR_SCHEMA,
        artifact_digest or ("0" * 64),
        str(table_index),
        code_fingerprint,
    ):
        b = part.encode("utf-8")
        h.update(str(len(b)).encode("ascii"))
        h.update(b"\x00")
        h.update(b)
    return h.hexdigest()


def derived_table_locator(key: str) -> str:
    """Content-addressed store locator for a derived-table key (per-digest record)."""
    return f"fi_appendix_structured_table/{key}"


@dataclass(frozen=True, slots=True)
class DerivedTableRow:
    """The persisted VERIFIED structured-table record — the TYPED carrier crossing the sink seam.

    Field names are exactly the persisted JSON keys. ``grade`` is :data:`GRADE_SELF_VERIFIED`
    (deterministic exact) or :data:`GRADE_EXACT_VISUAL` (vision third-witness graduated).
    ``cells`` is the consumable rows×cols cell grid (geometry dropped — the structured content)."""

    artifact_digest: str
    locator: str
    page_num: int
    table_index: int
    n_rows: int
    n_cols: int
    grade: str
    caption: str
    cells: Tuple[Dict[str, object], ...]
    code_fingerprint: str
    schema_version: str
    created_at: str

    def to_json(self) -> Dict[str, object]:
        return {
            "artifact_digest": self.artifact_digest,
            "locator": self.locator,
            "page_num": self.page_num,
            "table_index": self.table_index,
            "n_rows": self.n_rows,
            "n_cols": self.n_cols,
            "grade": self.grade,
            "caption": self.caption,
            "cells": [dict(c) for c in self.cells],
            "code_fingerprint": self.code_fingerprint,
            "schema_version": self.schema_version,
            "created_at": self.created_at,
        }

    @staticmethod
    def from_json(obj: Any) -> "DerivedTableRow":
        raw_cells = obj.get("cells") or []
        return DerivedTableRow(
            artifact_digest=str(obj["artifact_digest"]),
            locator=str(obj["locator"]),
            page_num=int(obj["page_num"]),
            table_index=int(obj["table_index"]),
            n_rows=int(obj["n_rows"]),
            n_cols=int(obj["n_cols"]),
            grade=str(obj["grade"]),
            caption=str(obj["caption"]),
            cells=tuple(
                {
                    "row": int(c["row"]),
                    "col": int(c["col"]),
                    "text": str(c["text"]),
                    "is_header": bool(c["is_header"]),
                }
                for c in raw_cells
            ),
            code_fingerprint=str(obj["code_fingerprint"]),
            schema_version=str(obj["schema_version"]),
            created_at=str(obj["created_at"]),
        )


class DerivedTableStore:
    """A farchive of content-addressed VERIFIED structured appendix tables (the consumed sink).

    The reader side ``write_tables_jsonl`` never had: :meth:`read_all` surfaces the verified
    structured-IR back into the structured-law corpus. Sibling to ``data/fi_parsed_ir.farchive``
    / the vision-read store; the reader stays a plain method so the roundtrip is hermetically
    testable with a tmp store."""

    def __init__(self, path: str = FI_DERIVED_IR_STORE) -> None:
        from farchive import Farchive

        self._fa = Farchive(path)
        self.path = path

    def get(self, key: str) -> Optional[DerivedTableRow]:
        """Read a persisted derived-table row by key (``None`` on miss)."""
        span = self._fa.resolve(derived_table_locator(key))
        if span is None:
            return None
        data = self._fa.read(span.digest)
        if data is None:
            return None
        return DerivedTableRow.from_json(json.loads(data.decode("utf-8")))

    def put(self, key: str, row: DerivedTableRow) -> str:
        """Persist one derived-table row (deterministic sorted-keys JSON); returns the blob digest."""
        return self._fa.store(
            derived_table_locator(key),
            json.dumps(row.to_json(), ensure_ascii=False, sort_keys=True).encode("utf-8"),
            storage_class="fi_appendix_structured_table",
            metadata={"grade": row.grade, "artifact_digest": row.artifact_digest},
        )

    def read_all(self) -> Tuple[DerivedTableRow, ...]:
        """READER: surface every persisted verified table (the structured-law corpus view).

        Enumerates the sink's locators, rehydrates each row, and returns them sorted by
        ``(artifact_digest, table_index)`` for a deterministic corpus view — the consumed side
        that makes the verified structured-IR readable BACK, not a throwaway dump."""
        rows: List[DerivedTableRow] = []
        for loc in self._fa.locators():
            if not loc.startswith("fi_appendix_structured_table/"):
                continue
            span = self._fa.resolve(loc)
            if span is None:
                continue
            data = self._fa.read(span.digest)
            if data is None:
                continue
            rows.append(DerivedTableRow.from_json(json.loads(data.decode("utf-8"))))
        return tuple(sorted(rows, key=lambda r: (r.artifact_digest, r.table_index)))

    def close(self) -> None:
        self._fa.close()


def _derived_cells(table: StructuredTable) -> Tuple[Dict[str, object], ...]:
    """The consumable cell grid of a verified table (row/col/text/is_header; geometry dropped)."""
    return tuple(
        {"row": c.row, "col": c.col, "text": c.text, "is_header": c.is_header}
        for c in table.cells
    )


def eligible_derived_tables(
    report: "StatuteTableReport",
) -> Tuple[Tuple[StructuredTable, str], ...]:
    """The (table, grade) pairs eligible for the derived-IR sink.

    A ``self_verified`` table (deterministic exact, structurally sane) lands at grade
    :data:`GRADE_SELF_VERIFIED`; a ``vision_escalate`` table whose EVERY routed cell the witness
    read graduated to exact_visual (``all_graduated`` with the full routed set read) lands at
    :data:`GRADE_EXACT_VISUAL` — the whole table is now verified. A topology-wrong / open /
    deferred / partially-read table is NOT eligible (never a half-verified table into the corpus)."""
    vv_by_index = {vv.table_index: vv for vv in report.vision_verifications}
    out: List[Tuple[StructuredTable, str]] = []
    for table, route in zip(report.tables, report.routes, strict=True):
        if route == ROUTE_SELF_VERIFIED:
            out.append((table, GRADE_SELF_VERIFIED))
        elif route == ROUTE_VISION_ESCALATE:
            vv = vv_by_index.get(table.table_index)
            if (
                vv is not None
                and vv.n_read > 0
                and vv.n_read == vv.n_routed
                and vv.all_graduated
            ):
                out.append((table, GRADE_EXACT_VISUAL))
    return tuple(out)


def write_derived_tables(
    report: "StatuteTableReport", store: DerivedTableStore, *, code_fingerprint: str
) -> int:
    """Persist every SINK-ELIGIBLE verified table into the derived-IR store; returns the count.

    The consumed side ``write_tables_jsonl`` never had: self_verified + fully-vision-graduated
    tables land content-addressed (keyed by artifact_digest + table_index + the quotient-contract
    fingerprint) so the structured-law corpus reads verified IR BACK."""
    from datetime import datetime, timezone

    n = 0
    for table, grade in eligible_derived_tables(report):
        key = derived_table_key(
            artifact_digest=report.artifact_digest,
            table_index=table.table_index,
            code_fingerprint=code_fingerprint,
        )
        store.put(
            key,
            DerivedTableRow(
                artifact_digest=report.artifact_digest,
                locator=table.locator,
                page_num=table.page_num,
                table_index=table.table_index,
                n_rows=table.n_rows,
                n_cols=table.n_cols,
                grade=grade,
                caption=table.caption,
                cells=_derived_cells(table),
                code_fingerprint=code_fingerprint,
                schema_version=_DERIVED_IR_SCHEMA,
                created_at=datetime.now(tz=timezone.utc).isoformat(),
            ),
        )
        n += 1
    return n


# --------------------------------------------------------------------------- #
# CORROBORATE-EDGE tie-in: a disagreeing vision-verified cell → a typed RECEIPT. #
# --------------------------------------------------------------------------- #
#
# Where the vision third-witness READ a routed cell and corroborated NEITHER text decoder (an
# ``open_divergence``), the deterministic grid and the independent vision read materially disagree
# — exactly the CORROBORATE edge (#2). Rather than a bespoke appendix record, the disagreement
# rides the SAME jurisdiction-neutral ``ingest.corroboration`` types: each is an
# ``EscalationPending`` (kind PAYLOAD_DISPUTE — two witnesses proposed different body text for a
# matched cell) confronted against the ALREADY-obtained blind vision read via ``corroborate``,
# whose canonical primitives decide ``agreed`` / ``verdict_changed``. The receipt is the record; it
# never itself asserts a graduation.

#: Stable witness-prompt tag folded into every appendix corroboration receipt's fingerprint (a
#: contract edit re-keys via ``prompt_fingerprint``; the region-reader model id is folded in too).
_APPENDIX_VISION_WITNESS_TAG = "fi_appendix_vision_region_read"


def _cell_unit_id(locator: str, table_index: int, row: int, col: int) -> str:
    """Opaque per-cell unit id for a corroboration pending (the mechanism never parses it)."""
    return f"{locator}#t{table_index}r{row}c{col}"


def _corroboration_receipts(
    vision_verifications: Sequence[TableVisionVerification],
    screen_pendings: Sequence[Tuple[EscalationPending, str]],
    *,
    witness_model: str,
) -> Tuple[CorroborationReceipt, ...]:
    """Emit a corroboration RECEIPT for every disagreeing cell — the corroborate edge.

    Two disagreement sources ride ONE edge: (a) each vision ``open_divergence`` (the witness read
    the cell but corroborated neither text decoder) — its candidate is the Docling text and the
    confronting read is the already-obtained blind vision read; and (b) each recall-screen suspect
    ``(pending, vision_read)`` (a self-verified cell the gestalt screen flagged, confronted against
    a fresh vision read). Both are confronted through :func:`corroborate`, whose canonical
    primitives decide agreed/verdict_changed — no hand-rolled comparison. Returns the receipts (the
    statistics substrate the adjudicator consumes; an unreadable confront yields no receipt)."""
    receipts: List[CorroborationReceipt] = []
    pendings: List[Tuple[EscalationPending, str]] = []
    for vv in vision_verifications:
        for d in vv.open_divergences:
            pendings.append(
                (
                    EscalationPending(
                        unit_id=_cell_unit_id(vv.locator, vv.table_index, d.row, d.col),
                        kind=EscalationKind.PAYLOAD_DISPUTE,
                        reason=(
                            f"appendix_vision_open:{d.descriptor}"
                            if d.descriptor
                            else "appendix_vision_open"
                        ),
                        region=f"page={vv.page_num},table={vv.table_index},cell=({d.row},{d.col})",
                        candidate_text=d.docling_text,
                    ),
                    d.witness_text,  # the blind vision read already obtained (open bucket carries it)
                )
            )
    pendings.extend(screen_pendings)
    for pending, vision_read in pendings:
        receipt = corroborate(
            pending,
            vision_reader=lambda _p, _v=vision_read: _v,
            witness_prompt=_APPENDIX_VISION_WITNESS_TAG,
            witness_model=witness_model,
        )
        if receipt is not None:
            receipts.append(receipt)
    return tuple(receipts)


def _screen_recall_suspects(
    tables: Sequence[StructuredTable],
    verifications: Sequence[TableVerification],
    gestalt_region_reader: GestaltRegionReader,
    vision_region_reader: Optional[
        Callable[[int, Tuple[float, float, float, float]], VisionReadResult]
    ],
) -> Tuple[Tuple[RoutedVerdict, ...], Tuple[Tuple[EscalationPending, str], ...]]:
    """RECALL pre-filter: gestalt-screen the SELF-VERIFIED cells → suspects + corroborate pendings.

    The precision gate (:func:`verify_table_exact` + :func:`verify_tables_vision`) only re-reads
    DETERMINISTICALLY-divergent cells; a cell BOTH text witnesses agree on but that looks broken /
    incomplete (a clipped column, a dropped row — the gestalt-completeness axis the deterministic
    char-class scan cannot see) would silently pass. So over an INJECTED gestalt reader this screens
    each self-verified cell and returns the NON-CLEAN routed verdicts (the recall suspects), plus —
    when a transcribing ``vision_region_reader`` is available — a corroborate ``EscalationPending``
    per suspect paired with a fresh blind vision read, so the suspect ESCALATES onto the SAME
    corroborate edge. Opt-in: the default path injects no gestalt reader (byte-identical)."""
    suspects: List[RoutedVerdict] = []
    pendings: List[Tuple[EscalationPending, str]] = []
    for table, det in zip(tables, verifications, strict=True):
        divergent = {(d.row, d.col) for d in det.divergences}
        for cell in table.cells:
            if cell.bbox is None or (cell.row, cell.col) in divergent:
                continue  # only self-verified (exact) cells get the recall screen
            routed = screen_and_route(
                cell.text,
                locator=table.locator,
                unit_ref=f"t{table.table_index}r{cell.row}c{cell.col}",
                page_num=table.page_num,
                bbox=cell.bbox,
                gestalt_reader=gestalt_region_reader,
            )
            if routed.route is ScreenRoute.CLEAN:
                continue
            suspects.append(routed)
            if vision_region_reader is not None:
                read = _as_vision_read(vision_region_reader(table.page_num, cell.bbox))
                if not read.abstain:
                    pendings.append(
                        (
                            EscalationPending(
                                unit_id=_cell_unit_id(
                                    table.locator, table.table_index, cell.row, cell.col
                                ),
                                kind=EscalationKind.GARBLE_READ,
                                reason=f"appendix_screen:{routed.route.value}:{routed.descriptor}",
                                region=(
                                    f"page={table.page_num},table={table.table_index},"
                                    f"cell=({cell.row},{cell.col})"
                                ),
                                candidate_text=cell.text,
                            ),
                            read.text,
                        )
                    )
    return tuple(suspects), tuple(pendings)


def _docling_document(pdf_bytes: bytes, *, name: str) -> Any:
    """Convert PDF bytes → a ``DoclingDocument`` on CPU (lazy import).

    CPU-pinned (``AcceleratorDevice.CPU``) so the conversion never contends with
    the GPU-resident :8080 vision server.

    OCR is DISABLED (``do_ocr=False``): this tool's stratum is BORN-DIGITAL appendix
    PDFs (they carry a real text layer — scanned/sparse PDFs are routed out to their
    ``text_layer_sparse`` status and handled by the vision/OCR lane), so the layout +
    TableFormer producers read the existing text directly. Leaving OCR on makes docling's
    default pipeline pull an OCR/VLM model over the network at convert-time (an
    ``AutoModelForImageTextToText`` fetch from a remote hub) — a determinism-firewall
    violation (AGENTS.md §1.3: no network at ingest) that also adds nothing on a text-layer
    PDF. Disabling it keeps the conversion offline, deterministic, and CPU-cheap.
    """
    import importlib
    import io

    dc = importlib.import_module("docling.document_converter")
    base = importlib.import_module("docling.datamodel.base_models")
    popts = importlib.import_module("docling.datamodel.pipeline_options")

    pipeline = popts.PdfPipelineOptions()
    pipeline.do_ocr = False
    pipeline.accelerator_options = popts.AcceleratorOptions(
        device=popts.AcceleratorDevice.CPU
    )
    converter = dc.DocumentConverter(
        format_options={
            base.InputFormat.PDF: dc.PdfFormatOption(pipeline_options=pipeline)
        }
    )
    stream = base.DocumentStream(name=name, stream=io.BytesIO(pdf_bytes))
    return converter.convert(stream).document


def structure_statute_pdf(
    locator: str,
    pdf_bytes: bytes,
    artifact_digest: str,
    *,
    vision_region_reader: Optional[
        Callable[[int, Tuple[float, float, float, float]], VisionReadResult]
    ] = None,
    vision_max_cells: Optional[int] = None,
    gestalt_region_reader: Optional[GestaltRegionReader] = None,
    derived_store: Optional[DerivedTableStore] = None,
    vision_model: str = "",
) -> StatuteTableReport:
    """Docling-structure ONE appendix PDF into tables + fidelity metrics.

    Runs the Docling TableFormer producer (CPU) over the PDF, lowers each TABLE
    node into structured IR (carrying the bbox the enhanced adapter now
    preserves), then triangulates fidelity: numeric completeness vs the PDF's own
    text layer, cross-witness numeric agreement vs pypdfium2, and per-table
    structural sanity.

    This is the docling/pdfium PRODUCER seam; every verification / vision / sink /
    corroborate step is delegated to the PURE :func:`build_statute_report` (hermetically
    testable with synthetic tables + a scripted reader). When ``vision_region_reader`` is
    supplied (the injectable render-based witness), the ``vision_escalate`` stratum — tables
    the deterministic pdfium witness could not reconcile with Docling — is additionally
    adjudicated by the vision THIRD-WITNESS tie-break; when a ``derived_store`` is supplied the
    self_verified / exact_visual tables are persisted into the consumed derived-IR sink; when a
    ``gestalt_region_reader`` is supplied the self-verified cells are recall-screened.
    """
    from lawvm.ingest.llm_backends.docling_producer import (
        _docling_document_to_page_views,
        docling_document_to_nodes,
    )

    page_texts = _page_texts(pdf_bytes)
    n_pages = len(page_texts)
    mean_chars = (
        sum(len(t.strip()) for t in page_texts) / n_pages if n_pages else 0.0
    )

    doc = _docling_document(pdf_bytes, name=f"{artifact_digest[:16]}.pdf")
    page_views = _docling_document_to_page_views(doc)

    tables: List[StructuredTable] = []
    block_nodes: List[SourceDocumentNode] = []
    for page_num in sorted(page_views):
        nodes = docling_document_to_nodes(
            page_views[page_num], artifact_digest=artifact_digest, page_num=page_num
        )
        for node in nodes:
            if node.kind is SourceDocumentNodeKind.TABLE:
                tables.append(
                    structured_table_from_node(
                        node, locator=locator, table_index=len(tables)
                    )
                )
            elif node.kind in _TEXT_BLOCK_KINDS:
                block_nodes.append(node)  # reading order preserved (page, then in-page)

    # TEXT-BLOCK LANE: a 0-grid appendix with a real text layer is structured into ordered
    # verbatim text blocks and exact-verified — so it is no longer silently dropped. Sparse/
    # scanned PDFs keep their existing text_layer_sparse typed status (they need vision/OCR).
    # The DETERMINISTIC verification is done here (needs pdf_bytes); the vision tie-break is
    # delegated to build_statute_report.
    text_blocks: Tuple[StructuredTextBlock, ...] = ()
    text_block_verification: Optional[TextBlockVerification] = None
    if should_run_text_block_lane(n_tables=len(tables), mean_text_chars=mean_chars):
        text_blocks = tuple(
            structured_text_block_from_node(node, locator=locator, block_index=i)
            for i, node in enumerate(block_nodes)
        )
        text_block_verification = _verify_text_blocks_against_pdfium(pdf_bytes, text_blocks)

    verifications = _verify_tables_against_pdfium(pdf_bytes, tuple(tables))

    return build_statute_report(
        locator=locator,
        artifact_digest=artifact_digest,
        page_texts=page_texts,
        tables=tuple(tables),
        verifications=verifications,
        text_blocks=text_blocks,
        text_block_verification=text_block_verification,
        vision_region_reader=vision_region_reader,
        vision_max_cells=vision_max_cells,
        gestalt_region_reader=gestalt_region_reader,
        derived_store=derived_store,
        vision_model=vision_model,
    )


def build_statute_report(
    *,
    locator: str,
    artifact_digest: str,
    page_texts: Sequence[str],
    tables: Sequence[StructuredTable],
    verifications: Sequence[TableVerification],
    text_blocks: Sequence[StructuredTextBlock] = (),
    text_block_verification: Optional[TextBlockVerification] = None,
    vision_region_reader: Optional[
        Callable[[int, Tuple[float, float, float, float]], VisionReadResult]
    ] = None,
    vision_max_cells: Optional[int] = None,
    gestalt_region_reader: Optional[GestaltRegionReader] = None,
    derived_store: Optional[DerivedTableStore] = None,
    vision_model: str = "",
) -> StatuteTableReport:
    """Assemble a :class:`StatuteTableReport` from ALREADY-produced tables + deterministic
    verifications — the PURE verification / vision / sink / corroborate core of the appendix lane.

    Separated from the docling/pdfium producer (:func:`structure_statute_pdf`) so the whole vision
    + derived-IR-sink + corroborate-edge wiring is hermetically drivable with SYNTHETIC tables and
    a SCRIPTED region reader — no docling, no PDF, no live backend. Steps:

      * per-table structural sanity + the vision THIRD-WITNESS tie-break over the ``vision_escalate``
        stratum (when a ``vision_region_reader`` is injected), plus the text-block vision tie-break;
      * the RECALL screen over self-verified cells (when a ``gestalt_region_reader`` is injected):
        gestalt-flagged suspects that ESCALATE onto the corroborate edge;
      * the CORROBORATE-edge receipts for cells where the vision read disagreed with the grid (open
        divergences) or a screen suspect was confronted — the same ``ingest.corroboration`` types;
      * the CONSUMED derived-IR sink (when a ``derived_store`` is supplied): every self_verified /
        exact_visual table persisted content-addressed for the structured-law corpus.

    With NO readers / store this is byte-identically the old deterministic report."""
    n_pages = len(page_texts)
    mean_chars = (
        sum(len(t.strip()) for t in page_texts) / n_pages if n_pages else 0.0
    )
    born_digital = mean_chars >= _MIN_TEXT_LAYER_CHARS

    sanities = tuple(structural_sanity(t) for t in tables)
    vision_verifications: Tuple[TableVisionVerification, ...] = ()
    text_block_vision_verification: Optional[TextBlockVisionVerification] = None
    if vision_region_reader is not None:
        vision_verifications = verify_tables_vision(
            tables,
            verifications,
            vision_region_reader,
            born_digital=born_digital,
            max_cells=vision_max_cells,
            # WIRE 2: a topology-wrong dual-merge table is skipped by the vision tie-break so it
            # cannot graduate topology-wrong (it is typed structural_disagreement at report level).
            structural_disagreement=[s.structural_disagreement for s in sanities],
        )
        if text_block_verification is not None:
            # TEXT-BLOCK VISION third-witness tie-break: the SAME graduation over escalated blocks,
            # sharing the ``vision_max_cells`` budget (table + text-block lanes never both spend).
            text_block_vision_verification = verify_text_blocks_vision(
                text_blocks,
                text_block_verification,
                vision_region_reader,
                born_digital=born_digital,
                max_cells=vision_max_cells,
            )

    # RECALL pre-filter (opt-in over an injected gestalt reader): self-verified cells the gestalt
    # screen flags as suspect ESCALATE onto the corroborate edge as extra pendings.
    screen_suspects: Tuple[RoutedVerdict, ...] = ()
    screen_pendings: Tuple[Tuple[EscalationPending, str], ...] = ()
    if gestalt_region_reader is not None:
        screen_suspects, screen_pendings = _screen_recall_suspects(
            tables, verifications, gestalt_region_reader, vision_region_reader
        )

    # CORROBORATE edge: a disagreeing vision-verified cell (or a confronted screen suspect) rides
    # the shared receipt types. Empty unless the vision/screen lanes ran and disagreed.
    corroboration_receipts = _corroboration_receipts(
        vision_verifications, screen_pendings, witness_model=vision_model
    )

    all_cell_texts = tuple(txt for t in tables for txt in t.cell_texts())
    reference_text = "\n".join(page_texts)
    numeric = numeric_recall(reference_text, all_cell_texts)
    xwit = cross_witness(all_cell_texts, reference_text)

    note = ""
    if mean_chars < _MIN_TEXT_LAYER_CHARS:
        note = (
            "text_layer_sparse: pdfium reference is near-empty (image-baked/OCR-"
            "less), so numeric_recall/cross_witness lean on a WEAK reference — a "
            "vision witness is the appropriate second reader here."
        )
    report = StatuteTableReport(
        locator=locator,
        artifact_digest=artifact_digest,
        n_pages=n_pages,
        mean_text_chars_per_page=mean_chars,
        tables=tuple(tables),
        sanities=sanities,
        numeric=numeric,
        crosswitness=xwit,
        note=note,
        verifications=tuple(verifications),
        vision_verifications=vision_verifications,
        text_blocks=tuple(text_blocks),
        text_block_verification=text_block_verification,
        text_block_vision_verification=text_block_vision_verification,
        corroboration_receipts=corroboration_receipts,
        screen_suspects=screen_suspects,
    )

    # CONSUMED derived-IR sink: persist every self_verified / exact_visual table so the verified
    # structured-IR is readable BACK (the reader is DerivedTableStore.read_all).
    if derived_store is not None:
        write_derived_tables(
            report, derived_store, code_fingerprint=derived_ir_fingerprint()
        )
    return report


def _measure_one(
    locator: str,
    *,
    finlex_path: str,
    vision_producer: Any = None,
    vision_max_cells: Optional[int] = None,
    vision_read_store: Optional[VisionReadStore] = None,
    derived_store: Optional[DerivedTableStore] = None,
) -> StatuteTableReport:
    """Resolve + structure ONE media PDF locator; a bad PDF is a typed record.

    When ``vision_producer`` is supplied, the render-based vision third-witness is
    built for this PDF and the ``vision_escalate`` stratum is tie-broken by it
    (bounded to ``vision_max_cells`` routed-cell re-reads). When ``vision_read_store`` is
    also supplied the blind reads are cache-through (content-addressed by model-id +
    prompt + render-params + image-bytes-hash) so replay is byte-identical. When a
    ``derived_store`` is supplied the self_verified / exact_visual tables are persisted into
    the consumed derived-IR sink.
    """
    from farchive import Farchive

    fa = Farchive(finlex_path)
    try:
        span = fa.resolve(locator)
        if span is None:
            raise ValueError("locator not resolvable")
        pdf_bytes = fa.read(span.digest)
        if not pdf_bytes:
            raise ValueError("empty bytes")
        digest = getattr(span, "digest", "") or "0" * 64
        reader: Optional[
            Callable[[int, Tuple[float, float, float, float]], VisionReadResult]
        ] = None
        if vision_producer is not None:
            reader = make_vision_region_reader(
                vision_producer, pdf_bytes, artifact_digest=digest, locator=locator
            )
            if vision_read_store is not None:
                # Freeze the non-deterministic reads so replay/self-consistency is byte-identical.
                reader = make_cached_region_reader(
                    reader,
                    store=vision_read_store,
                    model_id=_resolve_producer_model_id(vision_producer),
                    prompt_fingerprint=cold_region_prompt_fingerprint(),
                    render_fingerprint=render_params_fingerprint(_VISION_REGION_DPI),
                    crop_digest=make_region_crop_digester(
                        pdf_bytes, artifact_digest=digest, locator=locator
                    ),
                )
        return structure_statute_pdf(
            locator,
            pdf_bytes,
            digest,
            vision_region_reader=reader,
            vision_max_cells=vision_max_cells,
            derived_store=derived_store,
            vision_model=(
                _resolve_producer_model_id(vision_producer)
                if vision_producer is not None
                else ""
            ),
        )
    except Exception as exc:  # a bad PDF is a typed record, not a pool-sinking crash
        return StatuteTableReport(
            locator=locator,
            artifact_digest="",
            n_pages=0,
            mean_text_chars_per_page=0.0,
            tables=(),
            sanities=(),
            numeric=NumericRecall(0, 0, ()),
            crosswitness=CrossWitness(0, 0, 0, (), ()),
            note=f"unreadable: {type(exc).__name__}: {exc}",
        )
    finally:
        fa.close()


# --------------------------------------------------------------------------- #
# Locator resolution (year/num → media PDF)                                     #
# --------------------------------------------------------------------------- #


def media_pdf_locators(statute: str, *, lang: str, finlex_path: str) -> List[str]:
    """Resolve a ``<year>/<num>`` statute id to its media PDF locators.

    Enumerates the farchive for ``finlex://sd/<year>/<num>/<lang>/media/*.pdf``.
    A statute may attach more than one PDF; all are returned (sorted).
    """
    from farchive import Farchive

    year, _, num = statute.partition("/")
    prefix = f"finlex://sd/{year}/{num}/{lang}/media/"
    fa = Farchive(finlex_path)
    try:
        locs = [
            loc
            for loc in fa.locators()
            if loc.startswith(prefix) and loc.endswith(".pdf")
        ]
    finally:
        fa.close()
    return sorted(locs)


# --------------------------------------------------------------------------- #
# Emit                                                                          #
# --------------------------------------------------------------------------- #


def render_report_text(reports: Sequence[StatuteTableReport]) -> str:
    """Human-readable per-statute fidelity summary (deterministic)."""
    lines: List[str] = []
    for r in reports:
        lines.append(f"=== {r.locator} (digest={r.artifact_digest[:12]}) ===")
        lines.append(
            f"  pages={r.n_pages} mean_text_chars/page={r.mean_text_chars_per_page:.0f} "
            f"tables={len(r.tables)}"
        )
        if r.note:
            lines.append(f"  NOTE: {r.note}")
        for t, s in zip(r.tables, r.sanities, strict=True):
            flags = []
            if not s.rectangular:
                flags.append("RAGGED")
            if not s.header_row_found:
                flags.append("NO-HEADER")
            if s.dual_table_merge_suspected:
                flags.append("DUAL-MERGE?")
            if s.structural_disagreement:
                flags.append(f"STRUCT-DISAGREE(geo={s.geo_rows}x{s.geo_cols})")
            flag_str = (" [" + ",".join(flags) + "]") if flags else ""
            lines.append(
                f"    table#{t.table_index} p{t.page_num} "
                f"{t.n_rows}x{t.n_cols} cells={len(t.cells)} "
                f"bbox={s.n_bbox_cells}/{s.n_cells}{flag_str}"
            )
        lines.append(
            f"  numeric_recall={r.numeric.recall:.3f} "
            f"({r.numeric.n_recovered}/{r.numeric.n_reference}, "
            f"missing={len(r.numeric.missing)})"
        )
        lines.append(
            f"  cross_witness_agreement={r.crosswitness.agreement:.3f} "
            f"(shared={r.crosswitness.n_shared} docling_only={r.crosswitness.n_docling_only} "
            f"layer_only={r.crosswitness.n_layer_only})"
        )
        # THE HEADLINE — exact cell verification (not a coverage score).
        lines.append(
            f"  EXACT: tables_exact={r.n_tables_exact}/{len(r.tables)} "
            f"cells_verified={r.n_cells_verified}/{r.n_cells_witnessed} witnessed "
            f"(cell divergences={r.n_cells_witnessed - r.n_cells_verified})"
        )
        # META-LEVEL routing: tables the deterministic lane could not self-verify are
        # sent to a vision witness (never hand-repaired). Self-verified = free & exact.
        lines.append(
            f"  ROUTE: self_verified={r.routes.count(ROUTE_SELF_VERIFIED)} "
            f"vision_escalate={r.n_tables_vision_escalate} "
            f"structural_disagreement={r.n_tables_structural_disagreement} "
            f"no_witness_deferred={r.routes.count(ROUTE_NO_WITNESS_DEFERRED)}"
        )
        # VISION third-witness tie-break: cells GRADUATED to exact (grade exact_visual) because two
        # of the three independent witnesses agreed (the third outvoted). Only emitted when ran.
        if r.vision_verifications:
            n_read = sum(vv.n_read for vv in r.vision_verifications)
            n_esc = sum(len(vv.escalated) for vv in r.vision_verifications)
            n_open = sum(len(vv.open_divergences) for vv in r.vision_verifications)
            lines.append(
                f"  VISION-TIEBREAK: exact_visual={r.n_cells_vision_graduated}/{n_read} read "
                f"(routed={r.n_cells_routed_to_vision}, "
                f"escalated={n_esc}, open={n_open}); "
                f"cells_exact(self_verified) {r.n_cells_verified}"
                f"→{r.n_cells_verified}+{r.n_cells_vision_graduated} exact_visual"
            )
        # TEXT-BLOCK LANE (0-grid appendix): the born-digital text annex structured into
        # verbatim blocks and exact-verified — one verified-or-typed unit, never dropped.
        if r.text_block_verification is not None:
            tv = r.text_block_verification
            lines.append(
                f"  TEXT-BLOCK: blocks={len(r.text_blocks)} "
                f"exact={tv.n_exact}/{tv.n_witnessed} witnessed "
                f"(divergences={len(tv.divergences)}, no_witness={tv.n_no_witness}) "
                f"route={r.text_block_route}"
            )
        # TEXT-BLOCK VISION third-witness tie-break: blocks GRADUATED to exact because an
        # independent render read reproduced the pdfium witness (Docling outvoted). Only when ran.
        if r.text_block_vision_verification is not None:
            bvv = r.text_block_vision_verification
            lines.append(
                f"  TEXT-BLOCK-VISION-TIEBREAK: "
                f"exact_visual={bvv.n_graduated}/{bvv.n_read} read "
                f"(routed={bvv.n_routed}, "
                f"escalated={len(bvv.escalated)}, "
                f"open={len(bvv.open_divergences)}); "
                f"blocks_exact(self_verified) {r.n_text_blocks_exact}"
                f"→{r.n_text_blocks_exact}+{r.n_text_blocks_vision_graduated} exact_visual"
            )
        # RECALL SCREEN suspects (self-verified cells the gestalt screen flagged). Only when ran.
        if r.screen_suspects:
            lines.append(f"  RECALL-SCREEN: suspects={len(r.screen_suspects)}")
        # CORROBORATE edge: receipts for cells where the vision read disagreed with the grid.
        if r.corroboration_receipts:
            n_agreed = sum(1 for x in r.corroboration_receipts if x.agreed)
            n_changed = sum(1 for x in r.corroboration_receipts if x.verdict_changed)
            lines.append(
                f"  CORROBORATE: receipts={len(r.corroboration_receipts)} "
                f"agreed={n_agreed} verdict_changed={n_changed}"
            )
        lines.append("")
    return "\n".join(lines) + "\n"


def write_tables_jsonl(reports: Sequence[StatuteTableReport], path: str) -> int:
    """Persist every structured table as one JSONL line; returns the line count."""
    n = 0
    with open(path, "w", encoding="utf-8") as fh:
        for r in reports:
            for t in r.tables:
                fh.write(json.dumps(t.to_jsonable(), ensure_ascii=False, sort_keys=True))
                fh.write("\n")
                n += 1
    return n


def main(args: argparse.Namespace) -> None:
    """CLI handler for ``lawvm fi-appendix-structure``."""
    finlex_path = args.finlex or _FINLEX_DEFAULT
    locators: List[str] = []
    for statute in args.statutes:
        if statute.startswith("finlex://"):
            locators.append(statute)
            continue
        found = media_pdf_locators(statute, lang=args.lang, finlex_path=finlex_path)
        if not found:
            print(f"# no media PDF for {statute} (lang={args.lang})")
        locators.extend(found)

    # Optional VISION third-witness tie-break over the vision_escalate stratum. Requested via
    # ``--vision`` (read forward-compatibly; the argparse flag lives in the CLI module,
    # owned elsewhere). The :8080 vision server is probed once; if absent we fall back
    # to the deterministic-only report rather than fail.
    vision_producer: Any = None
    vision_cap: Optional[int] = getattr(args, "vision_cap", None)
    if getattr(args, "vision", False):
        from lawvm.ingest.llm_backends.vision_producer import VisionPageProducer

        producer = VisionPageProducer()
        if producer.is_available():
            vision_producer = producer
        else:
            print("# --vision requested but the :8080 vision server is unavailable; "
                  "running deterministic-only")

    # DETERMINISM FIREWALL: whenever vision runs its non-deterministic reads are cache-through
    # (content-addressed) so replay / self-consistency is byte-identical. The store path is a
    # gitignored sibling farchive (never committed). The default deterministic path opens NO store.
    vision_read_store: Optional[VisionReadStore] = None
    if vision_producer is not None:
        vision_read_store = VisionReadStore(
            getattr(args, "vision_read_store", None) or FI_VISION_READ_STORE
        )

    # CONSUMED derived-IR sink: with ``--derived-store PATH`` the self_verified / exact_visual
    # tables are persisted content-addressed for the structured-law corpus (read back via
    # DerivedTableStore.read_all). Opt-in — the default path opens NO store, so it is a pure
    # side-store that never perturbs the report output.
    derived_store: Optional[DerivedTableStore] = None
    derived_store_path = getattr(args, "derived_store", None)
    if derived_store_path:
        derived_store = DerivedTableStore(derived_store_path)
    try:
        reports = [
            _measure_one(
                loc,
                finlex_path=finlex_path,
                vision_producer=vision_producer,
                vision_max_cells=vision_cap,
                vision_read_store=vision_read_store,
                derived_store=derived_store,
            )
            for loc in locators
        ]
    finally:
        if vision_read_store is not None:
            vision_read_store.close()
        if derived_store is not None:
            print(
                f"# derived-IR sink → {derived_store.path} "
                f"({len(derived_store.read_all())} verified tables total)"
            )
            derived_store.close()

    if args.json:
        payload = {"reports": [r.to_jsonable() for r in reports]}
        out = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    else:
        out = render_report_text(reports)

    if args.report_out:
        with open(args.report_out, "w", encoding="utf-8") as fh:
            fh.write(out)
        print(f"fi-appendix-structure report → {args.report_out}")
    else:
        print(out, end="")

    if args.jsonl_out:
        n = write_tables_jsonl(reports, args.jsonl_out)
        print(f"fi-appendix-structure tables → {args.jsonl_out} ({n} tables)")

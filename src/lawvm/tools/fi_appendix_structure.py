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
import json
import re
import threading
from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from lawvm.core.source_document.ir import SourceDocumentNode, SourceDocumentNodeKind
from lawvm.finland.op_equivalence import text_equivalence

_FINLEX_DEFAULT = "data/finlex.farchive"

# pypdfium2 wraps pdfium, whose C state is process-global and NOT thread-safe;
# serialise the critical section (mirrors fi_scan_stratum).
_PDFIUM_LOCK = threading.Lock()

# A grid this wide on a statute appendix is a strong prior for the Docling
# dual-table merge (two side-by-side municipality columns collapsed into one).
_DUAL_MERGE_COL_THRESHOLD = 9


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
    """One cell where the independent bbox witness did not reproduce the Docling text."""

    row: int
    col: int
    docling_text: str
    witness_text: str


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


def table_escalation_route(verification: TableVerification) -> str:
    """Meta-level routing verdict for one table's deterministic-witness result.

    Pure. The phase-3 deterministic lane either SELF-VERIFIES a table (every
    witnessable cell reproduced exactly) or it does not — and when it does not, the
    honest response is to ESCALATE to a vision second-witness, never to hand-repair
    the cells (the dominant failure is a corrupt text-layer witness, unrepairable in
    the general case). A table with no witnessable cell is deferred.
    """
    if verification.divergences:
        return ROUTE_VISION_ESCALATE
    if verification.n_exact > 0:
        return ROUTE_SELF_VERIFIED
    return ROUTE_NO_WITNESS_DEFERRED


# --------------------------------------------------------------------------- #
# VISION SECOND-WITNESS (the escalation target — an INDEPENDENT render read).    #
# --------------------------------------------------------------------------- #
#
# When the deterministic (pdfium text-layer) witness cannot self-verify a table it
# is routed ``vision_escalate`` — overwhelmingly because the text layer itself is
# corrupt for that PDF's font (broken ToUnicode CMap), NOT because Docling read the
# cell wrong. The honest resolution is a SECOND, independent witness that does not
# depend on the corrupt text layer: RENDER each escalated cell's own bbox region
# (region-isolation crop, never a whole-page downscale) and read that pixel region
# back to text, then apply the SAME EXACT contract as ``verify_table_exact`` —
# ``text_equivalence`` modulo the legally-inert op-equivalence quotient.
#
# The witness is INJECTABLE (``region_reader(page_num, bbox) -> str``), exactly like
# ``verify_table_exact``'s ``bbox_witness``, so the seam (render → read → exact
# compare) is hermetically testable with a scripted reader — no model / PDF in CI.
# ``make_vision_region_reader`` is the production wiring (crop via
# ``ingest.visual.render_region_crop`` + read via the :8080 vision producer's
# ``read_region_cold``). Only the ROUTED (deterministically-divergent) cells are
# re-read: the deterministic lane already verified the rest, so vision spend is
# bounded to exactly the cells the free lane could not adjudicate.


@dataclass(frozen=True, slots=True)
class TableVisionVerification:
    """Vision second-witness verdict for ONE deterministically-escalated table.

    ``n_routed`` is the deterministic escalation set (the cells the pdfium witness
    could not verify); ``n_corroborated`` is how many of those the render-based
    vision witness reproduces EXACTLY (agreeing with Docling where the corrupt text
    layer could not), and ``uncorroborated`` are the routed cells vision ALSO could
    not confirm — the genuinely-open divergences (each carrying the vision read).
    """

    locator: str
    page_num: int
    table_index: int
    n_routed: int
    n_corroborated: int
    corroborated: Tuple[Tuple[int, int], ...]
    uncorroborated: Tuple[TableCellDivergence, ...]

    @property
    def n_read(self) -> int:
        """Routed cells the vision witness actually re-read (corroborated + open).

        Equals ``n_routed`` when unbudgeted; less when a ``max_cells`` cap curtailed
        the render spend (the un-read routed cells are neither confirmed nor open)."""
        return self.n_corroborated + len(self.uncorroborated)

    @property
    def all_corroborated(self) -> bool:
        """True iff every routed cell the witness READ was confirmed (0 open reads)."""
        return not self.uncorroborated

    def to_jsonable(self) -> Dict[str, object]:
        return {
            "locator": self.locator,
            "page_num": self.page_num,
            "table_index": self.table_index,
            "n_routed": self.n_routed,
            "n_read": self.n_read,
            "n_corroborated": self.n_corroborated,
            "corroborated": [list(rc) for rc in self.corroborated],
            "uncorroborated": [
                {"row": d.row, "col": d.col, "docling": d.docling_text, "vision": d.witness_text}
                for d in self.uncorroborated
            ],
        }


def verify_tables_vision(
    tables: Sequence[StructuredTable],
    det_verifications: Sequence[TableVerification],
    region_reader: Callable[[int, Tuple[float, float, float, float]], str],
    *,
    max_cells: Optional[int] = None,
) -> Tuple[TableVisionVerification, ...]:
    """Vision second-witness over the ``vision_escalate`` stratum (injectable reader).

    For every table the deterministic lane routed ``vision_escalate``, re-read ONLY
    its divergent cells' bbox regions through ``region_reader`` (the render-based
    witness; injected so this is hermetically testable) and re-apply the identical
    exactness check ``verify_table_exact`` uses (``text_equivalence`` modulo the
    inert op-equivalence quotient). A routed cell whose vision read reproduces the
    Docling text is CORROBORATED; one that still differs is a genuinely-open
    divergence. Tables not routed to vision are skipped (nothing to spend on).

    ``max_cells`` caps the TOTAL number of routed cells re-read across the run (a
    vision-spend budget, mirroring the corpus lane's ``--vision-cap``); ``None`` is
    unbounded. Under a cap the un-read routed cells stay in ``n_routed`` (the honest
    escalation-set size) but are absent from ``corroborated``/``uncorroborated`` — so
    ``n_read`` (< ``n_routed``) is the sampled base. Pure apart from the injected reader.
    """
    out: List[TableVisionVerification] = []
    budget = max_cells
    for table, det in zip(tables, det_verifications, strict=True):
        if table_escalation_route(det) != ROUTE_VISION_ESCALATE:
            continue
        cells_by_pos = {(c.row, c.col): c for c in table.cells}
        corroborated: List[Tuple[int, int]] = []
        uncorroborated: List[TableCellDivergence] = []
        for d in det.divergences:
            if budget is not None and budget <= 0:
                break  # vision-spend budget exhausted; leave the rest un-read
            cell = cells_by_pos.get((d.row, d.col))
            if cell is None or cell.bbox is None:  # divergent ⇒ had a bbox; defensive
                continue
            vision_text = region_reader(table.page_num, cell.bbox)
            if budget is not None:
                budget -= 1
            if text_equivalence(cell.text, vision_text).equal:
                corroborated.append((d.row, d.col))
            else:
                uncorroborated.append(
                    TableCellDivergence(
                        row=d.row, col=d.col, docling_text=cell.text, witness_text=vision_text
                    )
                )
        out.append(
            TableVisionVerification(
                locator=table.locator,
                page_num=table.page_num,
                table_index=table.table_index,
                n_routed=len(det.divergences),
                n_corroborated=len(corroborated),
                corroborated=tuple(corroborated),
                uncorroborated=tuple(uncorroborated),
            )
        )
    return tuple(out)


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


@dataclass(frozen=True, slots=True)
class StructuralSanity:
    """Structural verdict on one table (rectangularity, header, dual-merge)."""

    rectangular: bool
    header_row_found: bool
    dual_table_merge_suspected: bool
    repeated_header_labels: Tuple[str, ...]
    n_bbox_cells: int
    n_cells: int
    detail: str


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

    n_bbox = sum(1 for c in table.cells if c.bbox is not None)
    detail_parts = [
        f"rows={table.n_rows}",
        f"cols={table.n_cols}",
        f"cells={len(table.cells)}",
        f"bbox_cells={n_bbox}",
    ]
    if repeated:
        detail_parts.append("repeated_header=" + "|".join(repeated))
    return StructuralSanity(
        rectangular=rectangular,
        header_row_found=header_row_found,
        dual_table_merge_suspected=dual,
        repeated_header_labels=repeated,
        n_bbox_cells=n_bbox,
        n_cells=len(table.cells),
        detail="; ".join(detail_parts),
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
    #: VISION second-witness verdicts over the ``vision_escalate`` stratum (empty when the
    #: vision witness was not run): render-based corroboration of the routed cells.
    vision_verifications: Tuple[TableVisionVerification, ...] = ()

    @property
    def n_cells_verified(self) -> int:
        return sum(v.n_exact for v in self.verifications)

    @property
    def n_cells_routed_to_vision(self) -> int:
        """Deterministically-divergent cells handed to the vision second-witness."""
        return sum(v.n_routed for v in self.vision_verifications)

    @property
    def n_cells_vision_corroborated(self) -> int:
        """Routed cells the render-based vision witness reproduced EXACTLY (≡ Docling)."""
        return sum(v.n_corroborated for v in self.vision_verifications)

    @property
    def n_cells_witnessed(self) -> int:
        """Cells that had a bbox to cross-verify (exact + divergent; excludes no_witness)."""
        return sum(v.n_exact + len(v.divergences) for v in self.verifications)

    @property
    def n_tables_exact(self) -> int:
        return sum(1 for v in self.verifications if v.exact)

    @property
    def routes(self) -> Tuple[str, ...]:
        """Per-table meta-level routing verdict (self-verified / vision / deferred)."""
        return tuple(table_escalation_route(v) for v in self.verifications)

    @property
    def n_tables_vision_escalate(self) -> int:
        """Tables the deterministic lane could not verify → routed to a vision witness."""
        return sum(1 for r in self.routes if r == ROUTE_VISION_ESCALATE)

    def to_jsonable(self) -> Dict[str, object]:
        return {
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
                "tables": [
                    {**v.to_jsonable(), "route": route}
                    for v, route in zip(self.verifications, self.routes, strict=True)
                ],
            },
            "vision_second_witness": {
                "ran": bool(self.vision_verifications),
                "n_cells_routed": self.n_cells_routed_to_vision,
                "n_cells_corroborated": self.n_cells_vision_corroborated,
                "tables": [vv.to_jsonable() for vv in self.vision_verifications],
            },
            "note": self.note,
        }


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


#: Points to inset each Docling cell bbox before the pdfium witness read, to drop the
#: stray edge glyph a touching neighbour cell bleeds in. Small (sub-character) so it
#: never clips real content.
_BBOX_INSET = 2.0


def _verify_tables_against_pdfium(
    pdf_bytes: bytes, tables: Sequence[StructuredTable]
) -> Tuple[TableVerification, ...]:
    """Exact-verify every table's cells against the pdfium text read WITHIN each cell bbox.

    Opens the PDF ONCE, reads each cell's own bbox via ``get_text_bounded`` (pdfium is
    bottom-left origin; the StructuredCell bbox is top-left points, so y is flipped against
    the page height), and defers to :func:`verify_table_exact`. Any pdfium hiccup yields an
    empty witness for that cell (→ a typed divergence, never a crash).
    """
    import importlib

    pdfium = importlib.import_module("pypdfium2")
    with _PDFIUM_LOCK:
        doc = pdfium.PdfDocument(pdf_bytes)
        try:
            heights = {i: doc[i].get_size()[1] for i in range(len(doc))}
            textpages = {i: doc[i].get_textpage() for i in range(len(doc))}
            try:

                def bbox_witness(page_num: int, bbox: Tuple[float, float, float, float]) -> str:
                    # Docling page_num is 1-indexed; pdfium pages are 0-indexed.
                    idx = page_num - 1
                    tp = textpages.get(idx)
                    h = heights.get(idx)
                    if tp is None or h is None:
                        return ""
                    x0, y0, x1, y1 = bbox
                    lo_x, hi_x = min(x0, x1), max(x0, x1)
                    lo_y, hi_y = min(y0, y1), max(y0, y1)
                    # Inset the box by a small margin so the read does not catch a stray
                    # glyph from the ADJACENT cell (Docling cell bboxes touch/slightly
                    # overlap their neighbours → edge bleed). Bounded so a thin cell never
                    # inverts.
                    mx = min(_BBOX_INSET, (hi_x - lo_x) / 3.0)
                    my = min(_BBOX_INSET, (hi_y - lo_y) / 3.0)
                    try:  # pdfium wants (left, bottom, right, top) in bottom-left origin
                        return tp.get_text_bounded(
                            left=lo_x + mx, bottom=h - (hi_y - my),
                            right=hi_x - mx, top=h - (lo_y + my),
                        )
                    except Exception:
                        return ""

                return tuple(verify_table_exact(t, bbox_witness) for t in tables)
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


def make_vision_region_reader(
    producer: Any,
    pdf_bytes: bytes,
    *,
    artifact_digest: str,
    locator: str,
    dpi: int = _VISION_REGION_DPI,
    expected_lines: int = 1,
) -> Callable[[int, Tuple[float, float, float, float]], str]:
    """Build the production render-based region reader (the injectable vision witness).

    Returns a ``region_reader(page_num, bbox) -> str`` with the SAME signature as
    ``verify_table_exact``'s ``bbox_witness``. It renders JUST the cell's bbox region
    (region-isolation crop via ``ingest.visual.render_region_crop``, invoked inside
    ``producer.read_region_cold``) and reads that pixel region back to text through
    the :8080 vision producer — an INDEPENDENT witness that never touches the corrupt
    text layer. The cell bbox is top-left points; ``render_region_crop`` is bottom-left,
    so y is flipped against the page height (read once up front). A render/read failure
    or a degenerate box yields an empty read → a typed uncorroborated divergence, never
    a crash. Vision imports stay lazy here (determinism firewall)."""
    from datetime import datetime, timezone

    from lawvm.core.source_document.anchors import BBox
    from lawvm.core.source_document.extraction import SourceManifestation
    from lawvm.ingest.llm_backends.vision_producer import (
        VisionProducerFailure,
        VisionProducerTruncated,
    )

    heights = _pdf_page_heights(pdf_bytes)
    manifestation = SourceManifestation(
        artifact_digest=artifact_digest or ("0" * 64),
        source_bytes=pdf_bytes,
        locator=locator,
        source_role="statute",
        fetched_at=datetime.now(timezone.utc),
        media_type="application/pdf",
    )

    def region_reader(page_num: int, bbox: Tuple[float, float, float, float]) -> str:
        h = heights.get(page_num)
        if h is None:
            return ""
        x0, y0, x1, y1 = bbox
        lo_x, hi_x = min(x0, x1), max(x0, x1)
        lo_y, hi_y = min(y0, y1), max(y0, y1)  # top-left: lo_y=top edge, hi_y=bottom edge
        try:  # flip to bottom-left origin for render_region_crop; BBox validates area
            bb = BBox(x0=lo_x, y0=h - hi_y, x1=hi_x, y1=h - lo_y)
        except ValueError:
            return ""
        try:
            return producer.read_region_cold(
                manifestation, page_num, bb, dpi=dpi, expected_lines=expected_lines
            )
        except (VisionProducerFailure, VisionProducerTruncated):
            return ""

    return region_reader


def _docling_document(pdf_bytes: bytes, *, name: str) -> Any:
    """Convert PDF bytes → a ``DoclingDocument`` on CPU (lazy import).

    CPU-pinned (``AcceleratorDevice.CPU``) so the conversion never contends with
    the GPU-resident :8080 vision server.
    """
    import importlib
    import io

    dc = importlib.import_module("docling.document_converter")
    base = importlib.import_module("docling.datamodel.base_models")
    popts = importlib.import_module("docling.datamodel.pipeline_options")

    pipeline = popts.PdfPipelineOptions()
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
        Callable[[int, Tuple[float, float, float, float]], str]
    ] = None,
    vision_max_cells: Optional[int] = None,
) -> StatuteTableReport:
    """Docling-structure ONE appendix PDF into tables + fidelity metrics.

    Runs the Docling TableFormer producer (CPU) over the PDF, lowers each TABLE
    node into structured IR (carrying the bbox the enhanced adapter now
    preserves), then triangulates fidelity: numeric completeness vs the PDF's own
    text layer, cross-witness numeric agreement vs pypdfium2, and per-table
    structural sanity.

    When ``vision_region_reader`` is supplied (the injectable render-based witness),
    the ``vision_escalate`` stratum — tables the deterministic pdfium witness could
    not self-verify — is additionally adjudicated by re-reading each routed cell's
    isolated bbox region and re-applying the exactness contract, corroborating
    Docling where the corrupt text layer could not.
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
    for page_num in sorted(page_views):
        nodes = docling_document_to_nodes(
            page_views[page_num], artifact_digest=artifact_digest, page_num=page_num
        )
        table_nodes = [n for n in nodes if n.kind is SourceDocumentNodeKind.TABLE]
        for node in table_nodes:
            tables.append(
                structured_table_from_node(
                    node, locator=locator, table_index=len(tables)
                )
            )

    sanities = tuple(structural_sanity(t) for t in tables)
    verifications = _verify_tables_against_pdfium(pdf_bytes, tables)
    vision_verifications: Tuple[TableVisionVerification, ...] = ()
    if vision_region_reader is not None:
        vision_verifications = verify_tables_vision(
            tables, verifications, vision_region_reader, max_cells=vision_max_cells
        )
    all_cell_texts = tuple(txt for t in tables for txt in t.cell_texts())
    reference_text = "\n".join(page_texts)
    numeric = numeric_recall(reference_text, all_cell_texts)
    xwit = cross_witness(all_cell_texts, reference_text)

    note = ""
    if mean_chars < 50.0:
        note = (
            "text_layer_sparse: pdfium reference is near-empty (image-baked/OCR-"
            "less), so numeric_recall/cross_witness lean on a WEAK reference — a "
            "vision witness is the appropriate second reader here."
        )
    return StatuteTableReport(
        locator=locator,
        artifact_digest=artifact_digest,
        n_pages=n_pages,
        mean_text_chars_per_page=mean_chars,
        tables=tuple(tables),
        sanities=sanities,
        numeric=numeric,
        crosswitness=xwit,
        note=note,
        verifications=verifications,
        vision_verifications=vision_verifications,
    )


def _measure_one(
    locator: str,
    *,
    finlex_path: str,
    vision_producer: Any = None,
    vision_max_cells: Optional[int] = None,
) -> StatuteTableReport:
    """Resolve + structure ONE media PDF locator; a bad PDF is a typed record.

    When ``vision_producer`` is supplied, the render-based vision second-witness is
    built for this PDF and the ``vision_escalate`` stratum is corroborated by it
    (bounded to ``vision_max_cells`` routed-cell re-reads).
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
        reader = None
        if vision_producer is not None:
            reader = make_vision_region_reader(
                vision_producer, pdf_bytes, artifact_digest=digest, locator=locator
            )
        return structure_statute_pdf(
            locator,
            pdf_bytes,
            digest,
            vision_region_reader=reader,
            vision_max_cells=vision_max_cells,
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
            f"no_witness_deferred={r.routes.count(ROUTE_NO_WITNESS_DEFERRED)}"
        )
        # VISION second-witness: render-based corroboration of the routed cells (the
        # cells the corrupt text layer could not verify). Only emitted when it ran.
        if r.vision_verifications:
            n_read = sum(vv.n_read for vv in r.vision_verifications)
            lines.append(
                f"  VISION: corroborated={r.n_cells_vision_corroborated}/{n_read} read "
                f"(routed={r.n_cells_routed_to_vision}, "
                f"open={n_read - r.n_cells_vision_corroborated})"
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

    # Optional VISION second-witness over the vision_escalate stratum. Requested via
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

    reports = [
        _measure_one(
            loc,
            finlex_path=finlex_path,
            vision_producer=vision_producer,
            vision_max_cells=vision_cap,
        )
        for loc in locators
    ]

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

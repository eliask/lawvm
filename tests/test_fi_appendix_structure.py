"""``fi-appendix-structure`` phase-3 prototype — pure metric + IR-lowering tests.

Hermetic: the numeric-completeness / cross-witness / structural-sanity metrics
and the Docling-node → structured-table IR lowering are exercised with plain
data — no docling, no PDF, no farchive, no network. The heavy docling/pypdfium
seam (``structure_statute_pdf``) is NOT driven here (it needs the extra + a real
PDF); it is measured by the tool's live report, not in CI.
"""
from __future__ import annotations

from lawvm.core.source_document.anchors import BBox, SourceAnchor
from lawvm.core.source_document.ir import (
    AssuranceTier,
    SourceDocumentNode,
    SourceDocumentNodeKind,
)
from lawvm.tools.fi_appendix_structure import (
    ROUTE_NO_WITNESS_DEFERRED,
    ROUTE_SELF_VERIFIED,
    ROUTE_VISION_ESCALATE,
    StructuredCell,
    StructuredTable,
    TableCellDivergence,
    TableVerification,
    TableVisionVerification,
    TextRun,
    _make_per_bbox_reader,
    cross_witness,
    make_vision_region_reader,
    number_tokens,
    numeric_recall,
    reconcile_table_witness,
    structural_sanity,
    structured_table_from_node,
    table_escalation_route,
    verify_table_exact,
    verify_tables_vision,
)


def _grid_table(cells: list[StructuredCell]) -> StructuredTable:
    return StructuredTable(
        locator="finlex://sd/2003/917/fin/media/x.pdf",
        page_num=1,
        table_index=0,
        n_rows=2,
        n_cols=1,
        caption="",
        cells=tuple(cells),
    )


def test_verify_table_exact_all_cells_reproduced_is_exact() -> None:
    # each cell's Docling text is reproduced EXACTLY by the independent bbox witness
    # (modulo the inert quotient: a "— — —" run and whitespace fold to equal).
    cells = [
        StructuredCell(0, 0, "Sähkö 1,2", is_header=False, bbox=(0.0, 0.0, 1.0, 1.0)),
        StructuredCell(1, 0, "Kaasu 3,4", is_header=False, bbox=(0.0, 1.0, 1.0, 2.0)),
    ]
    witness = {
        (0.0, 0.0, 1.0, 1.0): "Sähkö  1,2 — — —",  # inert-equal (dash run + spacing)
        (0.0, 1.0, 1.0, 2.0): "Kaasu 3,4",
    }
    v = verify_table_exact(_grid_table(cells), lambda _pn, bb: witness[bb])
    assert v.exact
    assert v.n_exact == 2 and not v.divergences


def test_verify_table_exact_flags_a_divergent_cell() -> None:
    cells = [
        StructuredCell(0, 0, "Sähkö 1,2", is_header=False, bbox=(0.0, 0.0, 1.0, 1.0)),
        StructuredCell(1, 0, "Kaasu 3,4", is_header=False, bbox=(0.0, 1.0, 1.0, 2.0)),
    ]
    witness = {(0.0, 0.0, 1.0, 1.0): "Sähkö 1,2", (0.0, 1.0, 1.0, 2.0): "Kaasu 9,9"}
    v = verify_table_exact(_grid_table(cells), lambda _pn, bb: witness[bb])
    assert not v.exact
    assert v.n_exact == 1 and len(v.divergences) == 1
    d = v.divergences[0]
    assert d.row == 1 and d.docling_text == "Kaasu 3,4" and d.witness_text == "Kaasu 9,9"


def test_verify_table_exact_bboxless_cell_is_deferred_not_forced() -> None:
    cells = [StructuredCell(0, 0, "x", is_header=False, bbox=None)]
    v = verify_table_exact(_grid_table(cells), lambda _pn, _bb: "")
    assert v.n_no_witness == 1 and v.exact and not v.divergences


def _verification(*, n_exact: int, n_no_witness: int, divergent: int) -> TableVerification:
    divs = tuple(
        TableCellDivergence(row=i, col=0, docling_text="ä", witness_text="‰")
        for i in range(divergent)
    )
    return TableVerification(
        locator="x", page_num=1, table_index=0,
        n_cells=n_exact + n_no_witness + divergent,
        n_exact=n_exact, n_no_witness=n_no_witness, divergences=divs,
    )


def test_route_self_verified_when_every_witnessable_cell_is_exact() -> None:
    # deterministic lane verified all witnessable cells -> no vision spend.
    v = _verification(n_exact=3, n_no_witness=1, divergent=0)
    assert v.exact
    assert table_escalation_route(v) == ROUTE_SELF_VERIFIED


def test_route_vision_escalate_on_any_divergence_not_repaired() -> None:
    # a corrupt-witness cell (ä->‰) is NOT hand-repaired; the table is routed to vision.
    v = _verification(n_exact=5, n_no_witness=0, divergent=2)
    assert not v.exact
    assert table_escalation_route(v) == ROUTE_VISION_ESCALATE


def test_route_deferred_when_no_cell_has_a_bbox_witness() -> None:
    # exact is vacuously True (0 divergences) but nothing was verified -> deferred.
    v = _verification(n_exact=0, n_no_witness=4, divergent=0)
    assert v.exact and table_escalation_route(v) == ROUTE_NO_WITNESS_DEFERRED

_DIGEST = "a" * 64


def test_number_tokens_normalizes_decimal_comma() -> None:
    toks = number_tokens("veroluokka 6,5 ja 4.6 sekä 3 §; 1 234")
    # '6,5' -> '6.5', dot decimal kept, integers kept, '§' ignored; '1 234' is two
    assert toks == ("6.5", "4.6", "3", "1", "234")


def test_numeric_recall_multiset_and_missing() -> None:
    # reference has 6,5 twice and 4,6 once; cells recover one 6,5 and the 4,6.
    rec = numeric_recall("6,5 4,6 6,5", ["6,5", "4,6"])
    assert rec.n_reference == 3
    assert rec.n_recovered == 2
    assert rec.missing == ("6.5",)
    assert abs(rec.recall - 2 / 3) < 1e-9


def test_numeric_recall_empty_reference_is_vacuously_full() -> None:
    rec = numeric_recall("no numbers here", ["also none"])
    assert rec.n_reference == 0
    assert rec.recall == 1.0


def test_cross_witness_numeric_agreement() -> None:
    # Docling cells and the pdfium layer share 6.5; each has one figure the other lacks.
    xw = cross_witness(["6,5", "9,9"], "6,5 1,1")
    assert xw.n_shared == 1
    assert xw.n_docling_only == 1  # 9.9
    assert xw.n_layer_only == 1  # 1.1
    assert xw.docling_only == ("9.9",)
    assert xw.layer_only == ("1.1",)
    assert abs(xw.agreement - 1 / 3) < 1e-9


def _cell(text: str, *, row: int, col: int, header: bool, bbox: BBox | None) -> SourceDocumentNode:
    return SourceDocumentNode(
        kind=SourceDocumentNodeKind.TABLE_CELL,
        assurance_tier=AssuranceTier.SINGLE_WITNESS,
        anchor=SourceAnchor(
            artifact_digest=_DIGEST,
            locator=f"docling:page=1;table;row={row};col={col}",
            page_num=1,
            bbox=bbox,
        ),
        text=text,
        attrs={"is_header": "1" if header else "0"},
    )


def _row(cells: list[SourceDocumentNode]) -> SourceDocumentNode:
    return SourceDocumentNode(
        kind=SourceDocumentNodeKind.TABLE_ROW,
        assurance_tier=AssuranceTier.SINGLE_WITNESS,
        anchor=SourceAnchor(artifact_digest=_DIGEST, locator="docling:page=1;table;row=0", page_num=1),
        children=tuple(cells),
    )


def _table(rows: list[SourceDocumentNode], caption: str = "cap") -> SourceDocumentNode:
    return SourceDocumentNode(
        kind=SourceDocumentNodeKind.TABLE,
        assurance_tier=AssuranceTier.SINGLE_WITNESS,
        anchor=SourceAnchor(artifact_digest=_DIGEST, locator="docling:page=1;table", page_num=1),
        text=caption,
        children=tuple(rows),
    )


def test_structured_table_from_node_carries_grid_and_bbox() -> None:
    bb = BBox(x0=1.0, y0=2.0, x1=3.0, y1=4.0)
    node = _table(
        [
            _row([
                _cell("Vuosi", row=0, col=0, header=True, bbox=None),
                _cell("Vero", row=0, col=1, header=True, bbox=None),
            ]),
            _row([
                _cell("2025", row=1, col=0, header=False, bbox=bb),
                _cell("6,5", row=1, col=1, header=False, bbox=None),
            ]),
        ]
    )
    table = structured_table_from_node(node, locator="finlex://x", table_index=0)
    assert table.n_rows == 2 and table.n_cols == 2
    assert table.caption == "cap"
    assert table.cell_texts() == ("Vuosi", "Vero", "2025", "6,5")
    # bbox threaded onto the one cell that carried geometry
    geo = [c for c in table.cells if c.bbox is not None]
    assert len(geo) == 1 and geo[0].bbox == (1.0, 2.0, 3.0, 4.0)


def test_structural_sanity_flags_rectangular_and_header() -> None:
    node = _table(
        [
            _row([_cell("h1", row=0, col=0, header=True, bbox=None), _cell("h2", row=0, col=1, header=True, bbox=None)]),
            _row([_cell("a", row=1, col=0, header=False, bbox=None), _cell("b", row=1, col=1, header=False, bbox=None)]),
        ]
    )
    s = structural_sanity(structured_table_from_node(node, locator="x", table_index=0))
    assert s.rectangular is True
    assert s.header_row_found is True
    assert s.dual_table_merge_suspected is False


def test_structural_sanity_flags_dual_table_merge_by_repeated_header() -> None:
    # Two side-by-side municipality columns merged: the header 'Lääni ja kunta'
    # repeats within the top row — the Docling dual-table failure fingerprint.
    node = _table(
        [
            _row([
                _cell("Lääni ja kunta", row=0, col=0, header=True, bbox=None),
                _cell("Veroluokka", row=0, col=1, header=True, bbox=None),
                _cell("Lääni ja kunta", row=0, col=2, header=True, bbox=None),
                _cell("Veroluokka", row=0, col=3, header=True, bbox=None),
            ]),
        ]
    )
    s = structural_sanity(structured_table_from_node(node, locator="x", table_index=0))
    assert s.dual_table_merge_suspected is True
    assert "Lääni ja kunta" in s.repeated_header_labels


def test_structural_sanity_flags_ragged_grid() -> None:
    node = _table(
        [
            _row([_cell("a", row=0, col=0, header=False, bbox=None), _cell("b", row=0, col=1, header=False, bbox=None)]),
            _row([_cell("c", row=1, col=0, header=False, bbox=None)]),
        ]
    )
    s = structural_sanity(structured_table_from_node(node, locator="x", table_index=0))
    assert s.rectangular is False
    assert s.header_row_found is False


# --------------------------------------------------------------------------- #
# GEOMETRY RECONCILIATION (Fix 1) — page text-runs → (row,col) witness.         #
# --------------------------------------------------------------------------- #
#
# The wrapped-tail defect: Docling routes a wrapped line's tail into the neighbouring column
# and draws the owning cell's bbox too narrow, so the per-bbox read of that cell comes back
# EMPTY. Reconciliation is empty-RESCUE: it trusts the per-bbox read wherever it found text
# (reconstructing a well-read cell from chars risks reading-order artifacts — a decimal comma
# sits below its digits) and reconstructs ONLY the under-read cells from the page chars whose
# x/y-band centre lands in that (row,col). All hermetic: chars + a scripted per-bbox read injected.


def _two_col_table() -> StructuredTable:
    # Two cleanly-separated columns: col0 x-band [0,10], col1 x-band [10,20]; one row.
    return StructuredTable(
        locator="finlex://sd/2003/917/fin/media/x.pdf",
        page_num=1,
        table_index=0,
        n_rows=1,
        n_cols=2,
        caption="",
        cells=(
            StructuredCell(0, 0, "Sähkön kulutus", is_header=False, bbox=(0.0, 0.0, 10.0, 10.0)),
            StructuredCell(0, 1, "1,2", is_header=False, bbox=(10.0, 0.0, 20.0, 10.0)),
        ),
    )


def test_reconcile_rescues_under_read_wrapped_tail_column() -> None:
    # col0's per-bbox read comes back EMPTY (the wrapped-tail defect) though Docling placed
    # "Sähkön kulutus" there; the wrapped label's chars — "Sähkön" on line 1, "kulutus" on the
    # tail line — both have their centre in col0's x-band, so reconciliation reconstructs the
    # cell (reading order, top line first, "-\n"-preserving). col1's per-bbox read found the
    # value, so it is TRUSTED (not reconstructed).
    table = _two_col_table()
    runs = [
        TextRun("Sähkön", x0=1.0, y0=0.0, x1=9.0, y1=4.0),   # col0, top line (centre_y=2)
        TextRun("kulutus", x0=1.0, y0=5.0, x1=9.0, y1=9.0),  # col0, wrapped tail (centre_y=7)
        TextRun("1,2", x0=11.0, y0=0.0, x1=19.0, y1=4.0),    # col1 (ignored — per-bbox found it)
    ]

    def per_bbox(bb: tuple[float, float, float, float]) -> str:
        return "" if bb[0] < 10 else "1,2"  # col0 under-read (empty); col1 read fine

    witness = reconcile_table_witness(table, runs, per_bbox)
    assert witness[(0, 0)] == "Sähkön\nkulutus"  # rescued from chars, tail re-united
    assert witness[(0, 1)] == "1,2"              # per-bbox read trusted
    # and the rescued witness makes the table verify EXACTLY (newline↔space folds equal)
    v = verify_table_exact(table, lambda _pn, bb: witness[(0, 0) if bb[0] < 10 else (0, 1)])
    assert v.exact


def test_reconcile_trusts_per_bbox_read_over_char_reconstruction() -> None:
    # STRICT NON-REGRESSION: where the per-bbox read already found text, it is kept verbatim —
    # reconciliation must never override a well-read cell (that is what would scramble a decimal
    # like "0,05", whose comma sits on a lower baseline). Even though a char run exists for col0,
    # the per-bbox "0,05" wins.
    table = _two_col_table()
    runs = [TextRun("garbage", x0=1.0, y0=0.0, x1=9.0, y1=4.0)]

    def per_bbox(bb: tuple[float, float, float, float]) -> str:
        return "0,05" if bb[0] < 10 else "1,2"

    witness = reconcile_table_witness(table, runs, per_bbox)
    assert witness[(0, 0)] == "0,05"  # per-bbox trusted, NOT the char reconstruction
    assert witness[(0, 1)] == "1,2"


def test_reconcile_falls_back_on_overlapping_bands_without_crashing() -> None:
    # Degenerate geometry: the two columns' x-bands OVERLAP (col0 [0,15], col1 [10,20]) by more
    # than the touching tolerance, so a char could map ambiguously → the whole table keeps the
    # per-cell bbox read (never a crash, never a dropped cell).
    table = StructuredTable(
        locator="x", page_num=1, table_index=0, n_rows=1, n_cols=2, caption="",
        cells=(
            StructuredCell(0, 0, "a", is_header=False, bbox=(0.0, 0.0, 15.0, 10.0)),
            StructuredCell(0, 1, "b", is_header=False, bbox=(10.0, 0.0, 20.0, 10.0)),
        ),
    )
    seen: list[tuple[float, float, float, float]] = []

    def fallback(bb: tuple[float, float, float, float]) -> str:
        seen.append(bb)
        return f"per-bbox:{bb[0]}"

    witness = reconcile_table_witness(table, [TextRun("noise", 1.0, 1.0, 2.0, 2.0)], fallback)
    assert witness == {(0, 0): "per-bbox:0.0", (0, 1): "per-bbox:10.0"}
    assert len(seen) >= 2  # every cell went through the fallback, none crashed/dropped


def test_reconcile_empty_cell_under_both_stays_empty() -> None:
    # A genuine spacer cell: Docling empty AND per-bbox empty → witness empty (no stray char is
    # invented for it — reconciliation only rescues cells Docling placed content in).
    table = StructuredTable(
        locator="x", page_num=1, table_index=0, n_rows=1, n_cols=2, caption="",
        cells=(
            StructuredCell(0, 0, "", is_header=False, bbox=(0.0, 0.0, 10.0, 10.0)),
            StructuredCell(0, 1, "v", is_header=False, bbox=(10.0, 0.0, 20.0, 10.0)),
        ),
    )
    # a stray char whose centre lands in col0 must NOT be invented into the empty spacer
    runs = [TextRun("x", x0=1.0, y0=1.0, x1=2.0, y1=2.0), TextRun("v", 11.0, 1.0, 12.0, 2.0)]
    witness = reconcile_table_witness(table, runs, lambda bb: "" if bb[0] < 10 else "v")
    assert witness[(0, 0)] == ""   # spacer stays empty
    assert witness[(0, 1)] == "v"


def test_reconcile_bboxless_cell_is_omitted_not_crashed() -> None:
    # A cell with no bbox contributes to no band and yields no witness entry (it is handled
    # upstream as no_witness) — reconciliation must not crash on it.
    table = StructuredTable(
        locator="x", page_num=1, table_index=0, n_rows=1, n_cols=2, caption="",
        cells=(
            StructuredCell(0, 0, "a", is_header=False, bbox=(0.0, 0.0, 10.0, 10.0)),
            StructuredCell(0, 1, "b", is_header=False, bbox=None),
        ),
    )
    witness = reconcile_table_witness(table, [TextRun("a", 1.0, 1.0, 9.0, 9.0)], lambda _bb: "")
    assert witness == {(0, 0): "a"}  # only the bbox'd cell; no crash on the bboxless one


def test_per_bbox_reader_inset_does_not_clip_trailing_glyph() -> None:
    # Fix 2's edge-clip case ('Nimi'→'Nim'): the per-bbox read now insets by a sub-point margin
    # (0.5 pt), not 2 pt, so the right edge is not pulled in far enough to drop the final glyph.
    # A fake textpage records the requested bounds and returns the full read only when the right
    # edge reaches the last glyph.
    class _FakeTextpage:
        def __init__(self) -> None:
            self.calls: list[tuple[float, float, float, float]] = []

        def get_text_bounded(self, *, left: float, bottom: float, right: float, top: float) -> str:
            self.calls.append((left, bottom, right, top))
            return "Nimi" if right >= 49.0 else "Nim"  # glyph 'i' sits at x∈[49,50]

    tp = _FakeTextpage()
    read = _make_per_bbox_reader(tp, 100.0)
    # cell bbox right edge x1=50 on a 100-pt page: a 2 pt inset → right=48 (clips 'i'); 0.5 → 49.5
    out = read((0.0, 0.0, 50.0, 10.0))
    assert out == "Nimi"                       # full trailing glyph preserved
    assert tp.calls[0][2] == 49.5              # right edge inset by only 0.5 pt, not 2.0


# --------------------------------------------------------------------------- #
# VISION second-witness seam (injected reader — no model / PDF).                #
# --------------------------------------------------------------------------- #
#
# The 2003/917 pathology: the deterministic pdfium witness is CORRUPT for the PDF's
# font (ä->‰ etc.), so it diverges on cells Docling in fact read correctly. The
# injected render-based vision reader reproduces the Docling text on those routed
# cells → corroboration. These drive the seam (route -> re-read routed cells ->
# exact-compare) with a scripted reader; the pdfium/vision transport is LIVE-only.

# bbox convention: top-left points; three cells in row 0.
_B00 = (0.0, 0.0, 1.0, 1.0)
_B01 = (1.0, 0.0, 2.0, 1.0)
_B02 = (2.0, 0.0, 3.0, 1.0)


def _escalated_table() -> StructuredTable:
    return StructuredTable(
        locator="finlex://sd/2003/917/fin/media/x.pdf",
        page_num=1,
        table_index=0,
        n_rows=1,
        n_cols=3,
        caption="",
        cells=(
            StructuredCell(0, 0, "Sähkö 1,2", is_header=False, bbox=_B00),
            StructuredCell(0, 1, "Kaasu 3,4", is_header=False, bbox=_B01),
            StructuredCell(0, 2, "9", is_header=False, bbox=_B02),
        ),
    )


def _det_verification(divergent_positions: list[tuple[int, int]]) -> TableVerification:
    # The deterministic (corrupt-witness) verdict: the given cells diverged; the rest
    # of the 3-cell table verified exact.
    text = {(0, 0): "Sähkö 1,2", (0, 1): "Kaasu 3,4", (0, 2): "9"}
    divs = tuple(
        TableCellDivergence(row=r, col=c, docling_text=text[(r, c)], witness_text="‰")
        for (r, c) in divergent_positions
    )
    return TableVerification(
        locator="finlex://sd/2003/917/fin/media/x.pdf",
        page_num=1,
        table_index=0,
        n_cells=3,
        n_exact=3 - len(divergent_positions),
        n_no_witness=0,
        divergences=divs,
    )


def test_vision_witness_corroborates_routed_cells() -> None:
    # The pdfium witness diverged on the two text cells (corrupt font); the render-based
    # vision reader reproduces the Docling text EXACTLY → both routed cells corroborated.
    table = _escalated_table()
    det = _det_verification([(0, 0), (0, 1)])
    reader_map = {_B00: "Sähkö  1,2", _B01: "Kaasu 3,4"}  # _B00 inert-equal (spacing)
    vvs = verify_tables_vision([table], [det], lambda _pn, bb: reader_map[bb])
    assert len(vvs) == 1
    vv = vvs[0]
    assert isinstance(vv, TableVisionVerification)
    assert vv.n_routed == 2
    assert vv.n_corroborated == 2
    assert vv.all_corroborated
    assert set(vv.corroborated) == {(0, 0), (0, 1)}
    assert not vv.uncorroborated


def test_vision_witness_leaves_genuine_divergence_open() -> None:
    # Vision confirms one routed cell but reads the other differently from Docling →
    # that cell stays an OPEN divergence carrying the vision read (never forced).
    table = _escalated_table()
    det = _det_verification([(0, 0), (0, 1)])
    reader_map = {_B00: "Sähkö 1,2", _B01: "Kaasu 9,9"}
    vvs = verify_tables_vision([table], [det], lambda _pn, bb: reader_map[bb])
    vv = vvs[0]
    assert vv.n_routed == 2 and vv.n_corroborated == 1
    assert vv.corroborated == ((0, 0),)
    assert len(vv.uncorroborated) == 1
    d = vv.uncorroborated[0]
    assert (d.row, d.col) == (0, 1)
    assert d.docling_text == "Kaasu 3,4" and d.witness_text == "Kaasu 9,9"
    assert not vv.all_corroborated


def test_vision_witness_only_reads_routed_cells_and_skips_self_verified() -> None:
    # A self-verified table (no divergences) spends NO vision reads and is not emitted;
    # for the escalated table, the reader is called on ONLY the routed cell (not (0,2)).
    escalated = _escalated_table()
    det_escalated = _det_verification([(0, 0)])
    clean = _escalated_table()
    det_clean = _det_verification([])  # 0 divergences -> self_verified, skipped

    seen: list[tuple[float, float, float, float]] = []

    def reader(_pn: int, bb: tuple[float, float, float, float]) -> str:
        seen.append(bb)
        return "Sähkö 1,2"

    vvs = verify_tables_vision([escalated, clean], [det_escalated, det_clean], reader)
    assert len(vvs) == 1  # only the escalated table
    assert vvs[0].table_index == 0
    assert seen == [_B00]  # only the routed cell was re-read; (0,1)/(0,2) untouched


def test_vision_witness_max_cells_caps_the_render_spend() -> None:
    # Two escalated tables, one routed cell each; a budget of 1 re-reads only the first
    # (n_read=1) while n_routed still reflects the true escalation-set size.
    t0, t1 = _escalated_table(), _escalated_table()
    d0, d1 = _det_verification([(0, 0)]), _det_verification([(0, 1)])
    calls: list[tuple[float, float, float, float]] = []

    def reader(_pn: int, bb: tuple[float, float, float, float]) -> str:
        calls.append(bb)
        return "Sähkö 1,2"  # corroborates cell (0,0); differs from (0,1)'s "Kaasu 3,4"

    vvs = verify_tables_vision([t0, t1], [d0, d1], reader, max_cells=1)
    assert len(calls) == 1  # budget honoured: exactly one render/read
    total_read = sum(vv.n_read for vv in vvs)
    assert total_read == 1
    assert vvs[0].n_routed == 1 and vvs[0].n_read == 1  # first table read
    assert vvs[1].n_read == 0 and vvs[1].n_routed == 1  # second left un-read


def test_make_vision_region_reader_flips_topleft_bbox_to_bottomleft(monkeypatch) -> None:
    # The production wiring must flip the top-left cell bbox to bottom-left for the
    # render crop (y := page_height - y). Stub the page-height read (no PDF) and record
    # the BBox the producer receives.
    import lawvm.tools.fi_appendix_structure as mod

    monkeypatch.setattr(mod, "_pdf_page_heights", lambda _b: {1: 100.0})

    captured: dict[str, BBox] = {}

    class _FakeProducer:
        def read_region_cold(
            self, manifestation: object, page_num: int, bbox: BBox, *,
            dpi: int, expected_lines: int,
        ) -> str:
            captured["bbox"] = bbox
            return "read-ok"

    reader = make_vision_region_reader(
        _FakeProducer(),
        b"%PDF-fake",
        artifact_digest="d" * 64,
        locator="finlex://sd/2003/917/fin/media/x.pdf",
    )
    # top-left cell bbox (10,20)-(30,40) on a 100-pt-tall page -> bottom-left (10,60)-(30,80)
    out = reader(1, (10.0, 20.0, 30.0, 40.0))
    assert out == "read-ok"
    bb = captured["bbox"]
    assert (bb.x0, bb.y0, bb.x1, bb.y1) == (10.0, 60.0, 30.0, 80.0)


def test_make_vision_region_reader_swallows_failure_to_empty(monkeypatch) -> None:
    # A render/read failure is an empty read (-> a typed open divergence), never a crash.
    import lawvm.tools.fi_appendix_structure as mod
    from lawvm.ingest.llm_backends.vision_producer import VisionProducerFailure

    monkeypatch.setattr(mod, "_pdf_page_heights", lambda _b: {1: 100.0})

    class _FailingProducer:
        def read_region_cold(self, *a, **k):
            raise VisionProducerFailure(page_num=1, reason_code="x", detail="boom")

    reader = make_vision_region_reader(
        _FailingProducer(), b"%PDF", artifact_digest="e" * 64, locator="loc://x"
    )
    assert reader(1, (10.0, 20.0, 30.0, 40.0)) == ""
    # a page with no known height also yields an empty read, not a KeyError
    assert reader(99, (10.0, 20.0, 30.0, 40.0)) == ""

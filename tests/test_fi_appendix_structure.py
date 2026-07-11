"""``fi-appendix-structure`` phase-3 prototype — pure metric + IR-lowering tests.

Hermetic: the numeric-completeness / cross-witness / structural-sanity metrics
and the Docling-node → structured-table IR lowering are exercised with plain
data — no docling, no PDF, no farchive, no network. The heavy docling/pypdfium
seam (``structure_statute_pdf``) is NOT driven here (it needs the extra + a real
PDF); it is measured by the tool's live report, not in CI.
"""
from __future__ import annotations

from typing import cast

from lawvm.core.source_document.anchors import BBox, SourceAnchor
from lawvm.core.source_document.ir import (
    AssuranceTier,
    SourceDocumentNode,
    SourceDocumentNodeKind,
)
from lawvm.tools.fi_appendix_structure import (
    GRADE_EXACT_VISUAL,
    GRADE_SELF_VERIFIED,
    ROUTE_NO_WITNESS_DEFERRED,
    ROUTE_SELF_VERIFIED,
    ROUTE_VISION_ESCALATE,
    VISION_OUTCOME_ESCALATED,
    VISION_OUTCOME_OPEN,
    VISION_PAIR_DOCLING_VISION,
    VISION_PAIR_PDFIUM_VISION,
    StructuredCell,
    StructuredTable,
    StructuredTextBlock,
    TableCellDivergence,
    TableCellGraduation,
    TableVerification,
    TableVisionVerification,
    TextBlockDivergence,
    TextBlockGraduation,
    TextBlockVerification,
    TextBlockVisionVerification,
    TextRun,
    VisionReadRow,
    VisionReadStore,
    VisionRegionRead,
    _make_per_bbox_reader,
    cold_region_prompt_fingerprint,
    cross_witness,
    make_cached_region_reader,
    make_vision_region_reader,
    number_tokens,
    numeric_recall,
    reconcile_table_witness,
    render_params_fingerprint,
    should_run_text_block_lane,
    structural_sanity,
    structured_table_from_node,
    structured_text_block_from_node,
    table_escalation_route,
    text_block_escalation_route,
    verify_table_exact,
    verify_tables_vision,
    verify_text_blocks_exact,
    verify_text_blocks_vision,
    vision_read_cache_key,
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
# VISION THIRD-WITNESS TIE-BREAK seam (injected reader — no model / PDF).        #
# --------------------------------------------------------------------------- #
#
# SYMMETRIC two-of-three graduation: the divergent cell is re-read by an INDEPENDENT BLIND vision
# witness and GRADUATES to exact (grade exact_visual) iff TWO of the three witnesses {docling,
# pdfium, vision} agree modulo the inert quotient — ``pdfium≡vision`` (Docling the wrapped-tail
# dissenter: Docling ``'raikasta- 2 500 mg/kg'`` vs pdfium ``'2 500 mg/kg'``) OR ``docling≡vision``
# (the 2003/917 corrupt-font pathology: pdfium ä->‰ garbled is the outvoted dissenter). A vision
# ABSTAIN is terminal ``escalated`` (never graduated), all-three-disagree is ``open``. These drive
# the seam (route -> re-read routed cells -> tie-break) with a scripted reader; transport is LIVE-only.

# bbox convention: top-left points; distinct per cell so the reader map keys are unique.
_B00 = (0.0, 0.0, 1.0, 1.0)
_B01 = (1.0, 0.0, 2.0, 1.0)
_B02 = (2.0, 0.0, 3.0, 1.0)


def _vision_case(
    specs: list[tuple[int, int, str, str, tuple[float, float, float, float]]],
) -> tuple[StructuredTable, TableVerification]:
    """Build a 1-row all-divergent table + its deterministic verdict.

    Each spec is ``(row, col, docling_text, pdfium_witness, bbox)``: the cell carries the
    Docling text; the matching deterministic divergence carries the pdfium witness_text
    (so every cell is routed to the vision tie-break).
    """
    cells = tuple(
        StructuredCell(r, c, doc, is_header=False, bbox=bb) for (r, c, doc, _p, bb) in specs
    )
    table = StructuredTable(
        locator="finlex://sd/1997/1217/fin/media/x.pdf",
        page_num=1,
        table_index=0,
        n_rows=1,
        n_cols=len(specs),
        caption="",
        cells=cells,
    )
    divs = tuple(
        TableCellDivergence(row=r, col=c, docling_text=doc, witness_text=pdf)
        for (r, c, doc, pdf, _bb) in specs
    )
    det = TableVerification(
        locator=table.locator,
        page_num=1,
        table_index=0,
        n_cells=len(specs),
        n_exact=0,
        n_no_witness=0,
        divergences=divs,
    )
    return table, det


def test_vision_tiebreak_graduates_cell_when_vision_corroborates_pdfium() -> None:
    # Born-digital wrapped-tail defect: Docling mis-segmented the cell, pdfium read it
    # right, and an INDEPENDENT vision read reproduces the pdfium text → GRADUATE to exact
    # (Docling outvoted), the pdfium text carried as the trusted corroborated content.
    table, det = _vision_case([(0, 0, "raikasta- 2 500 mg/kg", "2 500 mg/kg", _B00)])
    reader_map = {_B00: "2 500  mg/kg"}  # inert-equal to pdfium (spacing quotient)
    vvs = verify_tables_vision([table], [det], lambda _pn, bb: reader_map[bb])
    assert len(vvs) == 1
    vv = vvs[0]
    assert isinstance(vv, TableVisionVerification)
    assert vv.n_routed == 1 and vv.n_graduated == 1
    assert vv.all_graduated
    g = vv.graduated[0]
    assert isinstance(g, TableCellGraduation)
    assert (g.row, g.col) == (0, 0)
    assert g.corroborated_text == "2 500 mg/kg"  # pdfium reading, NOT Docling's text
    assert g.agreeing_pair == VISION_PAIR_PDFIUM_VISION  # pdfium≡vision, Docling outvoted
    assert g.grade == GRADE_EXACT_VISUAL  # exact-modulo-render, NOT free-lane self_verified
    assert not vv.escalated and not vv.open_divergences


def test_vision_tiebreak_corrupt_font_graduates_on_docling_vision_pair() -> None:
    # SYMMETRIC two-of-three: on a corrupt-font page the pdfium witness is garbled (ä->‰), so
    # a ``vision≡pdfium``-only rule could NEVER graduate this cell. Docling read it right and
    # the INDEPENDENT vision read reproduces DOCLING → GRADUATE as exact_visual with the Docling
    # reading as content (pdfium is the outvoted corrupt dissenter).
    table, det = _vision_case([(0, 0, "Sähkö 1,2", "S‰hk‰ 1,2", _B00)])
    reader_map = {_B00: "Sähkö  1,2"}  # inert-equal to Docling, differs from corrupt pdfium
    vvs = verify_tables_vision([table], [det], lambda _pn, bb: reader_map[bb])
    vv = vvs[0]
    assert vv.n_graduated == 1 and vv.all_graduated
    g = vv.graduated[0]
    assert (g.row, g.col) == (0, 0)
    assert g.agreeing_pair == VISION_PAIR_DOCLING_VISION  # corrupt-font sub-case
    assert g.corroborated_text == "Sähkö 1,2"  # Docling reading is the trusted content
    assert g.grade == GRADE_EXACT_VISUAL
    assert not vv.escalated and not vv.open_divergences


def test_vision_tiebreak_all_three_disagree_stays_open() -> None:
    # Vision corroborates neither pdfium nor Docling → a genuinely-open typed divergence
    # carrying the vision read (never graduated, never forced onto Docling).
    table, det = _vision_case([(0, 0, "Kaasu 3,4", "Kaasu 5,6", _B00)])
    reader_map = {_B00: "Kaasu 9,9"}
    vvs = verify_tables_vision([table], [det], lambda _pn, bb: reader_map[bb])
    vv = vvs[0]
    assert vv.n_graduated == 0 and not vv.escalated
    assert len(vv.open_divergences) == 1
    d = vv.open_divergences[0]
    assert d.docling_text == "Kaasu 3,4" and d.witness_text == "Kaasu 9,9"


def test_vision_tiebreak_sparse_scanned_never_graduates_even_if_vision_matches() -> None:
    # SPARSE/SCANNED guard: with born_digital=False the pdfium witness is untrustworthy and
    # vision hallucinates, so a cell NEVER graduates even when the (fake) vision read equals
    # the pdfium witness EXACTLY. It falls through to an open divergence, not to exact.
    table, det = _vision_case([(0, 0, "UN-ltja", "2 500 mg/kg", _B00)])
    reader_map = {_B00: "2 500 mg/kg"}  # equals pdfium exactly — would graduate if born-digital
    vvs = verify_tables_vision(
        [table], [det], lambda _pn, bb: reader_map[bb], born_digital=False
    )
    vv = vvs[0]
    assert vv.n_graduated == 0 and not vv.graduated
    assert len(vv.open_divergences) == 1  # not graduated; vision != Docling -> open

    # Sanity: the SAME reads DO graduate when the PDF is born-digital (the gate is the only
    # difference), proving the guard — not the data — is what blocks graduation.
    vvs_bd = verify_tables_vision([table], [det], lambda _pn, bb: reader_map[bb])
    assert vvs_bd[0].n_graduated == 1


def test_vision_tiebreak_requires_full_text_not_numeric_only() -> None:
    # Graduation demands FULL-TEXT quotient equivalence, not numeric-only agreement: a
    # vision read whose NUMBERS match the pdfium witness but whose letters differ does NOT
    # graduate (it would silently discard the legally-significant unit/label text).
    table, det = _vision_case([(0, 0, "raikasta- 2 500 mg/kg", "2 500 mg/kg", _B00)])
    reader_map = {_B00: "2 500 g/l"}  # same number 2500, different unit letters
    vvs = verify_tables_vision([table], [det], lambda _pn, bb: reader_map[bb])
    vv = vvs[0]
    assert vv.n_graduated == 0  # numeric-only agreement must NOT graduate
    assert len(vv.open_divergences) == 1


def test_vision_read_is_blind_no_candidates_reach_the_model(monkeypatch) -> None:
    # PASS-1 BLIND: the production wiring must transcribe ONLY the rendered pixels — the
    # pdfium/Docling candidate texts must NEVER be shown to the model (that would let it echo a
    # candidate and destroy witness independence). Record everything read_region_cold receives and
    # assert no candidate string is among the args; also assert the frozen prompt names no candidate.
    import lawvm.tools.fi_appendix_structure as mod
    from lawvm.ingest.llm_backends.vision_producer import _COLD_REGION_SYSTEM_PROMPT

    monkeypatch.setattr(mod, "_pdf_page_heights", lambda _b: {1: 100.0})
    seen: dict[str, object] = {}

    class _RecordingProducer:
        def read_region_cold(self, manifestation, page_num, bbox, *, dpi, expected_lines) -> str:
            seen["args"] = (manifestation, page_num, bbox, dpi, expected_lines)
            return "blindly-read"

    reader = make_vision_region_reader(
        _RecordingProducer(), b"%PDF", artifact_digest="a" * 64, locator="loc://x"
    )
    out = reader(1, (10.0, 20.0, 30.0, 40.0))
    assert out.text == "blindly-read"
    # No positional/keyword the model sees carries a candidate transcription — only geometry +
    # render params reach read_region_cold (which itself sends only the crop image + the prompt).
    flat = " ".join(str(a) for a in seen["args"])  # type: ignore[arg-type]
    for candidate_marker in ("raikasta", "2 500 mg/kg", "docling_candidate", "pdfium_candidate"):
        assert candidate_marker not in flat
    # The frozen blind prompt is a pure transcription instruction — it references no candidate text.
    low = _COLD_REGION_SYSTEM_PROMPT.lower()
    assert "candidate" not in low and "pdfium" not in low and "docling" not in low


def test_free_lane_exact_is_self_verified_grade_distinct_from_exact_visual() -> None:
    # A two-decoder A≡B cell (Docling ≡ pdfium-in-bbox) is free-lane exact: grade self_verified,
    # route self_verified, and it spends ZERO vision reads. The vision-graduated grade exact_visual
    # is a DISTINCT, weaker (homoglyph-blind) grade — the two must never be conflated.
    assert GRADE_SELF_VERIFIED != GRADE_EXACT_VISUAL
    cells = [StructuredCell(0, 0, "Sähkö 1,2", is_header=False, bbox=_B00)]
    table = StructuredTable(
        locator="finlex://sd/1997/1217/fin/media/x.pdf",
        page_num=1, table_index=0, n_rows=1, n_cols=1, caption="", cells=tuple(cells),
    )
    det = verify_table_exact(table, lambda _pn, _bb: "Sähkö  1,2")  # inert-equal → exact
    assert det.exact and det.n_exact == 1
    assert table_escalation_route(det) == ROUTE_SELF_VERIFIED

    seen: list[object] = []
    vvs = verify_tables_vision([table], [det], lambda _pn, bb: seen.append(bb) or "x")
    assert vvs == () and seen == []  # self_verified spends no vision reads; not graduated


def test_vision_tiebreak_single_witness_never_graduates() -> None:
    # A lone vision witness can NEVER graduate: with the pdfium witness EMPTY (dropped the cell)
    # and the vision read agreeing with NEITHER pdfium (empty) nor Docling, the cell stays open —
    # graduation always requires TWO of the three independent witnesses to agree.
    table, det = _vision_case([(0, 0, "Docling-A", "", _B00)])  # pdfium witness empty
    vvs = verify_tables_vision([table], [det], lambda _pn, bb: "Vision-Z")  # matches no one
    vv = vvs[0]
    assert vv.n_graduated == 0 and not vv.escalated
    assert len(vv.open_divergences) == 1  # single witness → open, never exact

    # And a BLANK pdfium witness the vision read happens to "match" (both empty-ish) does not
    # graduate either — the .strip() gate blocks graduating on an empty agreed side.
    table2, det2 = _vision_case([(0, 0, "Docling-A", "   ", _B00)])
    vvs2 = verify_tables_vision([table2], [det2], lambda _pn, bb: "   ")
    assert vvs2[0].n_graduated == 0 and len(vvs2[0].open_divergences) == 1


def test_vision_tiebreak_abstain_is_escalated_never_graduated() -> None:
    # ADDENDUM: a reader that ABSTAINS (the model punts rather than force an unsure blind read) is
    # a TERMINAL escalated status — NEVER graduated, distinct from open — and its descriptor is
    # recorded on the divergence record for downstream adjudication.
    table, det = _vision_case([(0, 0, "Sähkö 1,2", "S‰hk‰ 1,2", _B00)])
    punt = VisionRegionRead(text="", abstain=True, descriptor="glyph ambiguous under ‰ artifact")
    vvs = verify_tables_vision([table], [det], lambda _pn, _bb: punt)
    vv = vvs[0]
    assert vv.n_graduated == 0 and not vv.open_divergences
    assert len(vv.escalated) == 1 and not vv.all_graduated
    esc = vv.escalated[0]
    assert (esc.row, esc.col) == (0, 0)
    assert esc.descriptor == "glyph ambiguous under ‰ artifact"  # model reason recorded
    # the escalated bucket surfaces the distinct terminal outcome in the JSON (never "open").
    js = vv.to_jsonable()
    assert [e["outcome"] for e in js["escalated"]] == [VISION_OUTCOME_ESCALATED]  # type: ignore[index]
    assert js["open_divergences"] == []


def test_tb_vision_tiebreak_abstain_is_escalated_never_graduated() -> None:
    # The text-block lane honours the same abstain → escalated discipline (block-for-cell).
    blocks, v = _tb_vision_case([("kohde", "1) kohde", _TB0)])
    punt = VisionRegionRead(text="", abstain=True, descriptor="crop too faint to read")
    bvv = verify_text_blocks_vision(blocks, v, lambda _pn, _bb: punt)
    assert bvv is not None
    assert bvv.n_graduated == 0 and not bvv.open_divergences and len(bvv.escalated) == 1
    assert bvv.escalated[0].descriptor == "crop too faint to read"


def test_vision_tiebreak_born_digital_gate_holds_under_abstain_and_open() -> None:
    # Sanity that outcomes are exhaustive & disjoint: born_digital=False never graduates, and an
    # abstain still escalates (the sparse guard gates graduation, not the abstain routing).
    table, det = _vision_case([(0, 0, "A", "A-pdf", _B00)])
    punt = VisionRegionRead(text="", abstain=True, descriptor="punt")
    vvs = verify_tables_vision([table], [det], lambda _pn, _bb: punt, born_digital=False)
    assert vvs[0].n_graduated == 0 and len(vvs[0].escalated) == 1


# --------------------------------------------------------------------------- #
# DETERMINISM-FIREWALL CACHE for the non-deterministic blind vision reads.      #
# --------------------------------------------------------------------------- #
#
# Vision is not byte-deterministic; the pipeline's replay / self-consistency invariants require a
# content-addressed evidence record so a re-run reproduces the SAME reads (hence the SAME
# graduation verdicts). The reads are keyed by (model-id, prompt-fingerprint, render-params,
# image-bytes-hash): a HIT rehydrates without invoking the model; a model bump re-keys. All
# hermetic: a counting fake reader + a scripted crop-digest + a tmp-path store.


def test_vision_read_cache_key_rekeys_on_each_component() -> None:
    base = dict(
        model_id="m1", prompt_fingerprint="p1", render_fingerprint="r1", image_sha256="img1"
    )
    k0 = vision_read_cache_key(**base)
    assert k0 == vision_read_cache_key(**base)  # pure / stable
    # every component is load-bearing — flipping any one re-keys
    for field, other in [
        ("model_id", "m2"),
        ("prompt_fingerprint", "p2"),
        ("render_fingerprint", "r2"),
        ("image_sha256", "img2"),
    ]:
        assert vision_read_cache_key(**{**base, field: other}) != k0


def test_vision_read_store_cache_through_is_byte_identical_and_rekeys_on_model(tmp_path) -> None:
    store = VisionReadStore(str(tmp_path / "vr.farchive"))
    try:
        calls: list[tuple[int, tuple[float, float, float, float]]] = []

        def inner(page_num, bbox):
            calls.append((page_num, bbox))
            # a rich read: text + abstain + descriptor must ALL round-trip through the cache
            return VisionRegionRead(
                text="2 500 mg/kg", abstain=False, descriptor="", model_id="m1"
            )

        digest_map = {_B00: "imgHASHaaa"}
        cached = make_cached_region_reader(
            inner,
            store=store,
            model_id="m1",
            prompt_fingerprint="pf",
            render_fingerprint=render_params_fingerprint(300),
            crop_digest=lambda _pn, bb: digest_map[bb],
        )
        first = cached(1, _B00)
        assert first.text == "2 500 mg/kg" and len(calls) == 1  # MISS: model ran once

        second = cached(1, _B00)
        assert len(calls) == 1  # HIT: model did NOT run again
        assert (second.text, second.abstain, second.descriptor, second.model_id) == (
            first.text, first.abstain, first.descriptor, first.model_id
        )  # byte-identical replay

        # A MODEL BUMP re-keys → a fresh MISS (the cache must invalidate on model-id change).
        cached_m2 = make_cached_region_reader(
            inner,
            store=store,
            model_id="m2",  # bumped
            prompt_fingerprint="pf",
            render_fingerprint=render_params_fingerprint(300),
            crop_digest=lambda _pn, bb: digest_map[bb],
        )
        cached_m2(1, _B00)
        assert len(calls) == 2  # re-keyed under m2 → model ran again
    finally:
        store.close()


def test_vision_read_cache_persists_abstain_and_descriptor(tmp_path) -> None:
    # The abstain flag + descriptor are part of the materialized evidence record, so a cached
    # ESCALATED read replays identically (never silently downgraded to a confident empty).
    store = VisionReadStore(str(tmp_path / "vr.farchive"))
    try:
        calls: list[int] = []

        def inner(_pn, _bb):
            calls.append(1)
            return VisionRegionRead(
                text="", abstain=True, descriptor="unsure: minus vs hyphen", model_id="m1"
            )

        cached = make_cached_region_reader(
            inner,
            store=store,
            model_id="m1",
            prompt_fingerprint="pf",
            render_fingerprint="rf",
            crop_digest=lambda _pn, _bb: "imgX",
        )
        a = cached(1, _B00)
        b = cached(1, _B00)
        assert len(calls) == 1  # HIT on replay
        assert a.abstain is True and a.descriptor == "unsure: minus vs hyphen"
        assert (b.abstain, b.descriptor) == (a.abstain, a.descriptor)
        # the persisted row is the typed evidence carrier (not a bare dict)
        key = vision_read_cache_key(
            model_id="m1", prompt_fingerprint="pf", render_fingerprint="rf", image_sha256="imgX"
        )
        row = store.get(key)
        assert isinstance(row, VisionReadRow)
        assert row.abstain is True and row.descriptor == "unsure: minus vs hyphen"
    finally:
        store.close()


def test_vision_tiebreak_only_reads_routed_cells_and_skips_self_verified() -> None:
    # A self-verified table (no divergences) spends NO vision reads and is not emitted;
    # for the escalated table, the reader is called on ONLY its routed cells.
    escalated, det_escalated = _vision_case(
        [(0, 0, "raikasta- 2 500 mg/kg", "2 500 mg/kg", _B00)]
    )
    clean = StructuredTable(
        locator="finlex://sd/1997/1217/fin/media/x.pdf",
        page_num=1,
        table_index=1,
        n_rows=1,
        n_cols=1,
        caption="",
        cells=(StructuredCell(0, 0, "ok", is_header=False, bbox=_B02),),
    )
    det_clean = TableVerification(
        locator=clean.locator,
        page_num=1,
        table_index=1,
        n_cells=1,
        n_exact=1,
        n_no_witness=0,
        divergences=(),
    )  # 0 divergences -> self_verified, skipped

    seen: list[tuple[float, float, float, float]] = []

    def reader(_pn: int, bb: tuple[float, float, float, float]) -> str:
        seen.append(bb)
        return "2 500 mg/kg"

    vvs = verify_tables_vision([escalated, clean], [det_escalated, det_clean], reader)
    assert len(vvs) == 1  # only the escalated table
    assert vvs[0].table_index == 0
    assert seen == [_B00]  # only the routed cell was re-read; the clean table untouched


def test_vision_tiebreak_max_cells_caps_the_render_spend() -> None:
    # Two escalated tables, one routed cell each; a budget of 1 re-reads only the first
    # (n_read=1) while n_routed still reflects the true escalation-set size.
    t0, d0 = _vision_case([(0, 0, "raikasta- 2 500 mg/kg", "2 500 mg/kg", _B00)])
    t1, d1 = _vision_case([(0, 1, "ja - 2 000 mg/kg", "2 000 mg/kg", _B01)])
    calls: list[tuple[float, float, float, float]] = []

    def reader(_pn: int, bb: tuple[float, float, float, float]) -> str:
        calls.append(bb)
        return "2 500 mg/kg"  # graduates cell (0,0); differs from (0,1)'s pdfium "2 000 mg/kg"

    vvs = verify_tables_vision([t0, t1], [d0, d1], reader, max_cells=1)
    assert len(calls) == 1  # budget honoured: exactly one render/read
    total_read = sum(vv.n_read for vv in vvs)
    assert total_read == 1
    assert vvs[0].n_routed == 1 and vvs[0].n_read == 1  # first table read (graduated)
    assert vvs[0].n_graduated == 1
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
    assert isinstance(out, VisionRegionRead)
    assert out.text == "read-ok" and out.abstain is False
    bb = captured["bbox"]
    assert (bb.x0, bb.y0, bb.x1, bb.y1) == (10.0, 60.0, 30.0, 80.0)


def test_make_vision_region_reader_swallows_failure_to_abstain(monkeypatch) -> None:
    # A render/read FAILURE is a PUNT (abstain → terminal escalated), never a crash and never a
    # silent empty that could graduate. The failure reason rides on the descriptor.
    import lawvm.tools.fi_appendix_structure as mod
    from lawvm.ingest.llm_backends.vision_producer import VisionProducerFailure

    monkeypatch.setattr(mod, "_pdf_page_heights", lambda _b: {1: 100.0})

    class _FailingProducer:
        def read_region_cold(self, *a, **k):
            raise VisionProducerFailure(page_num=1, reason_code="x", detail="boom")

    reader = make_vision_region_reader(
        _FailingProducer(), b"%PDF", artifact_digest="e" * 64, locator="loc://x"
    )
    out = reader(1, (10.0, 20.0, 30.0, 40.0))
    assert out.text == "" and out.abstain is True
    assert "VisionProducerFailure" in out.descriptor
    # a page with no known height also abstains (never a KeyError)
    assert reader(99, (10.0, 20.0, 30.0, 40.0)).abstain is True


def test_make_vision_region_reader_declined_read_is_abstain(monkeypatch) -> None:
    # The model DECLINING (empty / UNREADABLE → read_region_cold returns "") is mapped to an
    # abstain, not a graduating empty string.
    import lawvm.tools.fi_appendix_structure as mod

    monkeypatch.setattr(mod, "_pdf_page_heights", lambda _b: {1: 100.0})

    class _DecliningProducer:
        def read_region_cold(self, *a, **k) -> str:
            return ""  # UNREADABLE / empty collapses to "" in read_region_cold

    reader = make_vision_region_reader(
        _DecliningProducer(), b"%PDF", artifact_digest="f" * 64, locator="loc://x"
    )
    out = reader(1, (10.0, 20.0, 30.0, 40.0))
    assert out.text == "" and out.abstain is True and out.descriptor


# --------------------------------------------------------------------------- #
# TEXT-BLOCK LANE (0-grid appendix → ordered verbatim text blocks).             #
# --------------------------------------------------------------------------- #
#
# For an appendix PDF Docling yields ZERO grid tables from (laskuperusteet / formula-prose /
# short textual annex) but that HAS a text layer, the lane structures the page's own
# PARAGRAPH/HEADING/FOOTNOTE blocks into ordered verbatim text blocks and verifies each one
# EXACTLY against an independent pdfium read of its bbox — the identical two-witness contract
# ``verify_table_exact`` uses (``text_equivalence`` modulo the inert op-equivalence quotient),
# never a fuzzy/coverage score. All hermetic: injected block text + a scripted bbox witness.

_LOC = "finlex://sd/1997/532/fin/media/x.pdf"


def _blocks(items: list[tuple[str, tuple[float, float, float, float] | None]]) -> list[StructuredTextBlock]:
    return [
        StructuredTextBlock(
            locator=_LOC, page_num=1, block_index=i, kind="paragraph", text=text, bbox=bbox
        )
        for i, (text, bbox) in enumerate(items)
    ]


def test_text_block_lane_clean_block_verifies_exact() -> None:
    # Each block's Docling text is reproduced EXACTLY by the independent bbox witness.
    blocks = _blocks([("Vakuutusmaksu lasketaan kaavalla P = k * S.", (0.0, 0.0, 10.0, 2.0))])
    witness = {(0.0, 0.0, 10.0, 2.0): "Vakuutusmaksu lasketaan kaavalla P = k * S."}
    v = verify_text_blocks_exact(blocks, lambda _pn, bb: witness[bb])
    assert v.exact and v.n_exact == 1 and not v.divergences
    assert text_block_escalation_route(v) == ROUTE_SELF_VERIFIED


def test_text_block_lane_inert_quotient_fold_still_verifies() -> None:
    # A block differing ONLY by legally-inert folds (a wrapped-line "\n" folding to a space
    # and a "— —" dash run) still verifies exact — mirrors the table lane's quotient.
    blocks = _blocks([("Perusteet:\nkerroin k ja summa S", (0.0, 0.0, 10.0, 4.0))])
    witness = {(0.0, 0.0, 10.0, 4.0): "Perusteet: — — kerroin  k ja summa S"}
    v = verify_text_blocks_exact(blocks, lambda _pn, bb: witness[bb])
    assert v.exact and v.n_exact == 1 and not v.divergences


def test_text_block_lane_genuine_difference_is_typed_divergence() -> None:
    # A block the witness does NOT reproduce (a real content difference) is a TYPED
    # divergence carrying both reads → the appendix routes to a vision second-witness.
    blocks = _blocks([("kerroin k = 1,5", (0.0, 0.0, 10.0, 2.0))])
    witness = {(0.0, 0.0, 10.0, 2.0): "kerroin k = 9,9"}
    v = verify_text_blocks_exact(blocks, lambda _pn, bb: witness[bb])
    assert not v.exact and v.n_exact == 0 and len(v.divergences) == 1
    d = v.divergences[0]
    assert d.docling_text == "kerroin k = 1,5" and d.witness_text == "kerroin k = 9,9"
    assert d.block_index == 0 and d.page_num == 1 and d.kind == "paragraph"
    assert text_block_escalation_route(v) == ROUTE_VISION_ESCALATE


def test_text_block_lane_bboxless_block_is_deferred_not_forced() -> None:
    # A block with no bbox cannot be cross-verified → no_witness (deferred), never forced.
    blocks = _blocks([("floating note", None)])
    v = verify_text_blocks_exact(blocks, lambda _pn, _bb: "")
    assert v.n_no_witness == 1 and v.exact and not v.divergences
    # nothing witnessable → deferred, NOT self-verified
    assert text_block_escalation_route(v) == ROUTE_NO_WITNESS_DEFERRED


def test_text_block_lane_mixed_blocks_route_to_vision_on_any_divergence() -> None:
    blocks = _blocks(
        [
            ("Liite 1", (0.0, 0.0, 10.0, 1.0)),
            ("kerroin k = 1,5", (0.0, 1.0, 10.0, 2.0)),
        ]
    )
    witness = {
        (0.0, 0.0, 10.0, 1.0): "Liite 1",
        (0.0, 1.0, 10.0, 2.0): "kerroin k = 2,5",  # corrupt/differs → divergence
    }
    v = verify_text_blocks_exact(blocks, lambda _pn, bb: witness[bb])
    assert v.n_exact == 1 and len(v.divergences) == 1 and v.n_witnessed == 2
    assert text_block_escalation_route(v) == ROUTE_VISION_ESCALATE


def test_should_run_text_block_lane_gates_on_zero_grid_and_born_digital() -> None:
    # 0 grid tables + a real (born-digital) text layer → run the lane.
    assert should_run_text_block_lane(n_tables=0, mean_text_chars=800.0) is True
    # ≥1 grid table → the table lane owns it, text-block lane does NOT run.
    assert should_run_text_block_lane(n_tables=2, mean_text_chars=800.0) is False


def test_should_run_text_block_lane_skips_sparse_scanned_page() -> None:
    # A sparse/scanned page (near-empty text layer) must NOT be self-verified off a weak
    # reference — it keeps its text_layer_sparse status and is routed to vision/OCR.
    assert should_run_text_block_lane(n_tables=0, mean_text_chars=12.0) is False


def test_structured_text_block_from_node_carries_kind_text_and_bbox() -> None:
    # Lowering a Docling PARAGRAPH node preserves its kind, verbatim text and geometry.
    node = SourceDocumentNode(
        kind=SourceDocumentNodeKind.PARAGRAPH,
        assurance_tier=AssuranceTier.SINGLE_WITNESS,
        anchor=SourceAnchor(
            artifact_digest=_DIGEST,
            locator="docling:page=1",
            page_num=1,
            bbox=BBox(x0=1.0, y0=2.0, x1=3.0, y1=4.0),
        ),
        text="Vakuutusmaksu lasketaan\nkaavalla",
    )
    b = structured_text_block_from_node(node, locator=_LOC, block_index=3)
    assert b.kind == "paragraph"
    assert b.text == "Vakuutusmaksu lasketaan\nkaavalla"  # line structure verbatim
    assert b.bbox == (1.0, 2.0, 3.0, 4.0)
    assert b.block_index == 3 and b.page_num == 1


def test_structured_text_block_from_node_without_bbox_is_no_witness_shaped() -> None:
    node = SourceDocumentNode(
        kind=SourceDocumentNodeKind.HEADING,
        assurance_tier=AssuranceTier.SINGLE_WITNESS,
        anchor=SourceAnchor(artifact_digest=_DIGEST, locator="docling:page=1", page_num=1),
        text="LIITE",
    )
    b = structured_text_block_from_node(node, locator=_LOC, block_index=0)
    assert b.kind == "heading" and b.bbox is None and b.text == "LIITE"


def test_text_block_verification_reuses_table_route_shape() -> None:
    # The text-block verdict is duck-compatible with the shared route rule (self-verified
    # when every witnessable block is exact and ≥1 was witnessed).
    v = TextBlockVerification(
        locator=_LOC, n_blocks=3, n_exact=2, n_no_witness=1, divergences=()
    )
    assert v.exact and v.n_witnessed == 2
    assert text_block_escalation_route(v) == ROUTE_SELF_VERIFIED


# --------------------------------------------------------------------------- #
# TEXT-BLOCK VISION THIRD-WITNESS TIE-BREAK seam (injected reader — no model).   #
# --------------------------------------------------------------------------- #
#
# The 0-grid text-block lane's escalated blocks are the SAME defect class as the table lane's
# escalated cells: Docling drops content (e.g. the list-enumerator ``'1)'``) while the pdfium
# bbox witness reads it right. The tie-break renders each divergent block and reads it with an
# INDEPENDENT vision witness, GRADUATING the block to exact iff vision ≡ the PDFIUM witness
# modulo the inert quotient (two witnesses agree, Docling outvoted). These drive the block seam
# (route -> re-read routed blocks -> tie-break) with a SCRIPTED reader; the pdfium/vision
# transport is LIVE-only. Byte-identical policy to the table tie-break, block-for-cell.

_TB0 = (0.0, 0.0, 10.0, 1.0)
_TB1 = (0.0, 1.0, 10.0, 2.0)


def _tb_vision_case(
    specs: list[tuple[str, str, tuple[float, float, float, float]]],
) -> tuple[list[StructuredTextBlock], TextBlockVerification]:
    """Build an all-divergent block set + its deterministic verdict (every block routed).

    Each spec is ``(docling_text, pdfium_witness, bbox)``: the block carries the Docling text;
    the matching deterministic divergence carries the pdfium witness_text.
    """
    blocks = [
        StructuredTextBlock(
            locator=_LOC, page_num=1, block_index=i, kind="paragraph", text=doc, bbox=bb
        )
        for i, (doc, _p, bb) in enumerate(specs)
    ]
    divs = tuple(
        TextBlockDivergence(
            block_index=i, page_num=1, kind="paragraph", docling_text=doc, witness_text=pdf
        )
        for i, (doc, pdf, _bb) in enumerate(specs)
    )
    v = TextBlockVerification(
        locator=_LOC, n_blocks=len(specs), n_exact=0, n_no_witness=0, divergences=divs
    )
    return blocks, v


def test_tb_vision_tiebreak_graduates_block_when_vision_corroborates_pdfium() -> None:
    # Docling dropped the list-enumerator ('1) ...'); pdfium read it right, and an INDEPENDENT
    # vision read reproduces the pdfium text → GRADUATE the block to exact (Docling outvoted).
    blocks, v = _tb_vision_case([("kohde, kun", "1) kohde, kun", _TB0)])
    reader_map = {_TB0: "1)  kohde, kun"}  # inert-equal to pdfium (spacing quotient)
    bvv = verify_text_blocks_vision(blocks, v, lambda _pn, bb: reader_map[bb])
    assert isinstance(bvv, TextBlockVisionVerification)
    assert bvv.n_routed == 1 and bvv.n_graduated == 1 and bvv.all_graduated
    g = bvv.graduated[0]
    assert isinstance(g, TextBlockGraduation)
    assert (g.block_index, g.page_num, g.kind) == (0, 1, "paragraph")
    assert g.corroborated_text == "1) kohde, kun"  # pdfium reading, NOT Docling's dropped text
    assert g.agreeing_pair == VISION_PAIR_PDFIUM_VISION and g.grade == GRADE_EXACT_VISUAL
    assert not bvv.escalated and not bvv.open_divergences


def test_tb_vision_tiebreak_corrupt_font_graduates_on_docling_vision_pair() -> None:
    # SYMMETRIC two-of-three (block-for-cell): the pdfium witness is garbled (ä->‰), Docling read
    # it right, and vision reproduces DOCLING → GRADUATE as exact_visual with the Docling reading
    # as content (pdfium the outvoted corrupt dissenter). A vision≡pdfium-only rule could not.
    blocks, v = _tb_vision_case([("Sähköä 1,2", "S‰hk‰‰ 1,2", _TB0)])
    reader_map = {_TB0: "Sähköä  1,2"}  # inert-equal to Docling, differs from corrupt pdfium
    bvv = verify_text_blocks_vision(blocks, v, lambda _pn, bb: reader_map[bb])
    assert bvv is not None
    assert bvv.n_graduated == 1 and bvv.all_graduated
    g = bvv.graduated[0]
    assert g.block_index == 0 and g.agreeing_pair == VISION_PAIR_DOCLING_VISION
    assert g.corroborated_text == "Sähköä 1,2" and g.grade == GRADE_EXACT_VISUAL
    assert not bvv.escalated and not bvv.open_divergences


def test_tb_vision_tiebreak_all_three_disagree_stays_open() -> None:
    # Vision corroborates neither pdfium nor Docling → a genuinely-open typed divergence.
    blocks, v = _tb_vision_case([("kerroin 3,4", "kerroin 5,6", _TB0)])
    reader_map = {_TB0: "kerroin 9,9"}
    bvv = verify_text_blocks_vision(blocks, v, lambda _pn, bb: reader_map[bb])
    assert bvv is not None
    assert bvv.n_graduated == 0 and not bvv.escalated
    assert len(bvv.open_divergences) == 1
    d = bvv.open_divergences[0]
    assert d.docling_text == "kerroin 3,4" and d.witness_text == "kerroin 9,9"


def test_tb_vision_tiebreak_sparse_scanned_never_graduates_even_if_vision_matches() -> None:
    # SPARSE/SCANNED guard: with born_digital=False a block NEVER graduates even when the (fake)
    # vision read equals the pdfium witness EXACTLY. It falls to open, not to exact.
    blocks, v = _tb_vision_case([("UN-ltja", "1) kohde", _TB0)])
    reader_map = {_TB0: "1) kohde"}  # equals pdfium exactly — would graduate if born-digital
    bvv = verify_text_blocks_vision(
        blocks, v, lambda _pn, bb: reader_map[bb], born_digital=False
    )
    assert bvv is not None
    assert bvv.n_graduated == 0 and not bvv.graduated
    assert len(bvv.open_divergences) == 1  # not graduated; vision != Docling -> open

    # Sanity: the SAME reads DO graduate when born-digital — the gate is the only difference.
    bvv_bd = verify_text_blocks_vision(blocks, v, lambda _pn, bb: reader_map[bb])
    assert bvv_bd is not None and bvv_bd.n_graduated == 1


def test_tb_vision_tiebreak_requires_full_text_not_substring_or_numeric() -> None:
    # Graduation demands FULL-TEXT quotient equivalence, not a substring/numeric match: a vision
    # read whose NUMBER matches the pdfium witness but whose letters differ does NOT graduate.
    blocks, v = _tb_vision_case([("k 2 500 mg", "1) k 2 500 mg/kg", _TB0)])
    reader_map = {_TB0: "1) k 2 500 g/l"}  # same number 2500, different unit letters
    bvv = verify_text_blocks_vision(blocks, v, lambda _pn, bb: reader_map[bb])
    assert bvv is not None
    assert bvv.n_graduated == 0
    assert len(bvv.open_divergences) == 1


def test_tb_vision_tiebreak_returns_none_for_self_verified_appendix() -> None:
    # A self-verified appendix (no divergences) is NOT routed to vision → None (no spend).
    blocks = _blocks([("Liite 1", _TB0)])
    v = TextBlockVerification(
        locator=_LOC, n_blocks=1, n_exact=1, n_no_witness=0, divergences=()
    )
    seen: list[object] = []
    bvv = verify_text_blocks_vision(blocks, v, lambda _pn, bb: seen.append(bb) or "x")
    assert bvv is None and seen == []  # no reader calls; nothing to spend on


def test_tb_vision_tiebreak_only_reads_routed_blocks() -> None:
    # A witnessed-exact block is NOT re-read; only the routed (divergent) block is.
    blocks = _blocks([("Liite 1", _TB0), ("kohde, kun", _TB1)])
    v = TextBlockVerification(
        locator=_LOC,
        n_blocks=2,
        n_exact=1,
        n_no_witness=0,
        divergences=(
            TextBlockDivergence(
                block_index=1, page_num=1, kind="paragraph",
                docling_text="kohde, kun", witness_text="1) kohde, kun",
            ),
        ),
    )
    seen: list[tuple[float, float, float, float]] = []

    def reader(_pn: int, bb: tuple[float, float, float, float]) -> str:
        seen.append(bb)
        return "1) kohde, kun"

    bvv = verify_text_blocks_vision(blocks, v, reader)
    assert bvv is not None and bvv.n_routed == 1 and bvv.n_graduated == 1
    assert seen == [_TB1]  # ONLY the routed block was re-read; the exact block untouched


def test_tb_vision_tiebreak_max_cells_caps_the_render_spend() -> None:
    # Two routed blocks, a budget of 1 re-reads only the first; n_routed still reflects the
    # true escalation-set size, n_read the sampled base.
    blocks, v = _tb_vision_case(
        [("kohde a", "1) kohde a", _TB0), ("kohde b", "2) kohde b", _TB1)]
    )
    calls: list[tuple[float, float, float, float]] = []

    def reader(_pn: int, bb: tuple[float, float, float, float]) -> str:
        calls.append(bb)
        return "1) kohde a"  # graduates block 0; differs from block 1's pdfium "2) kohde b"

    bvv = verify_text_blocks_vision(blocks, v, reader, max_cells=1)
    assert len(calls) == 1  # budget honoured: exactly one render/read
    assert bvv is not None
    assert bvv.n_routed == 2 and bvv.n_read == 1 and bvv.n_graduated == 1


def test_tb_vision_verification_jsonable_shape_surfaces_graduation() -> None:
    # Nothing graduates silently: the tie-break verdict serialises route + per-block outcomes.
    blocks, v = _tb_vision_case([("kohde, kun", "1) kohde, kun", _TB0)])
    reader_map = {_TB0: "1) kohde, kun"}
    bvv = verify_text_blocks_vision(blocks, v, lambda _pn, bb: reader_map[bb])
    assert bvv is not None
    js = bvv.to_jsonable()
    assert js["n_routed"] == 1 and js["n_read"] == 1 and js["n_graduated"] == 1
    grad = js["graduated"]
    assert isinstance(grad, list) and len(grad) == 1
    # the graduated entry surfaces the corroborated (pdfium) content + the tie-break status,
    # so nothing graduates silently in the emitted JSON.
    entry = cast("dict[str, object]", grad[0])
    assert entry["corroborated_text"] == "1) kohde, kun"
    assert entry["tiebreak_status"] == "vision_corroborated_exact"

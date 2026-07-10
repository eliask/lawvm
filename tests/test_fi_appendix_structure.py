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
    StructuredCell,
    StructuredTable,
    cross_witness,
    number_tokens,
    numeric_recall,
    structural_sanity,
    structured_table_from_node,
    verify_table_exact,
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

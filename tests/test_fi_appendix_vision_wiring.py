"""Phase-3 appendix VISION LANE end-to-end wiring — hermetic (no docling / PDF / backend).

Audit fix #7 wired the appendix vision lane from the CLI down: ``--vision`` runs the third-
witness tie-break, the ``self_verified`` / ``exact_visual`` tables land in a CONSUMED derived-IR
sink the corpus reads back, and a disagreeing cell rides the SAME ``ingest.corroboration``
edge. These tests drive that wiring with SYNTHETIC structured tables + a SCRIPTED region reader
(the pure core :func:`build_statute_report`, plus one monkeypatched ``structure_statute_pdf``
drive over a synthetic Docling doc) — proving:

  * the vision third-witness block RUNS when a reader is injected (and not otherwise);
  * a verified table REACHES the derived-IR sink and is READ BACK (roundtrip);
  * a disagreeing (open) cell produces a jurisdiction-neutral ``CorroborationReceipt``;
  * the recall SCREEN (``screen_and_route``) selects a self-verified cell to escalate onto the
    corroborate edge over an injected gestalt reader;
  * the determinism-firewall cache (``make_cached_region_reader``) roundtrips a real
    ``make_vision_region_reader`` read (a HIT replays without touching the model).

The committed 14-case ``fi_appendix_vision_canary`` fixture (test_fi_appendix_vision_canary) is
the distinct frozen DRIFT-canary (a false-graduation regression ratchet); THIS file is the
end-to-end wiring/behaviour proof.
"""
from __future__ import annotations

import io
from typing import Optional, Tuple

import pytest

from lawvm.tools.fi_appendix_structure import (
    GRADE_EXACT_VISUAL,
    GRADE_SELF_VERIFIED,
    ROUTE_SELF_VERIFIED,
    ROUTE_VISION_ESCALATE,
    DerivedTableRow,
    DerivedTableStore,
    StructuredCell,
    StructuredTable,
    TableCellDivergence,
    TableVerification,
    VisionRegionRead,
    build_statute_report,
    cold_region_prompt_fingerprint,
    derived_ir_fingerprint,
    derived_table_key,
    eligible_derived_tables,
    make_cached_region_reader,
    make_region_crop_digester,
    make_vision_region_reader,
    render_params_fingerprint,
    structure_statute_pdf,
    write_derived_tables,
    _VISION_REGION_DPI,
)

_LOC = "finlex://sd/2003/917/fin/media/x.pdf"
_PAGE_TEXTS = ["x" * 200]  # born-digital (mean chars/page >= 50)


# --------------------------------------------------------------------------- #
# Synthetic-table builders (no docling needed).                                 #
# --------------------------------------------------------------------------- #


def _clean_2x2() -> StructuredTable:
    """A structurally-sane 2×2 table with distinct header labels + clean geometry."""
    return StructuredTable(
        locator=_LOC,
        page_num=1,
        table_index=0,
        n_rows=2,
        n_cols=2,
        caption="cap",
        cells=(
            StructuredCell(0, 0, "Vuosi", is_header=True, bbox=(0.0, 0.0, 10.0, 5.0)),
            StructuredCell(0, 1, "Vero", is_header=True, bbox=(10.0, 0.0, 20.0, 5.0)),
            StructuredCell(1, 0, "2025", is_header=False, bbox=(0.0, 5.0, 10.0, 10.0)),
            StructuredCell(1, 1, "6.5", is_header=False, bbox=(10.0, 5.0, 20.0, 10.0)),
        ),
    )


def _verif(table: StructuredTable, *, n_exact: int, divergences=()) -> TableVerification:
    return TableVerification(
        locator=table.locator,
        page_num=table.page_num,
        table_index=table.table_index,
        n_cells=len(table.cells),
        n_exact=n_exact,
        n_no_witness=0,
        divergences=tuple(divergences),
    )


def _reader(mapping):
    """A scripted region reader: bbox → VisionRegionRead|str (constant fallback ``""`` = abstain)."""

    def read(_page_num: int, bbox: Tuple[float, float, float, float]):
        return mapping.get(bbox, "")

    return read


# --------------------------------------------------------------------------- #
# 1. The vision third-witness block runs ONLY when a reader is injected.        #
# --------------------------------------------------------------------------- #


def test_no_reader_is_byte_identical_deterministic() -> None:
    table = _clean_2x2()
    report = build_statute_report(
        locator=_LOC,
        artifact_digest="d" * 64,
        page_texts=_PAGE_TEXTS,
        tables=[table],
        verifications=[_verif(table, n_exact=4)],
    )
    assert report.vision_verifications == ()
    assert report.corroboration_receipts == ()
    assert report.screen_suspects == ()
    # no conditional keys leak into the JSON on the deterministic path
    js = report.to_jsonable()
    assert "corroboration" not in js and "recall_screen" not in js


def test_vision_block_runs_when_reader_injected() -> None:
    # one divergent cell whose vision read reproduces the pdfium witness → GRADUATES (exact_visual).
    table = _clean_2x2()
    div = TableCellDivergence(1, 1, "6.5", "6,5")  # docling "6.5" vs pdfium "6,5"
    report = build_statute_report(
        locator=_LOC,
        artifact_digest="d" * 64,
        page_texts=_PAGE_TEXTS,
        tables=[table],
        verifications=[_verif(table, n_exact=3, divergences=[div])],
        vision_region_reader=_reader({(10.0, 5.0, 20.0, 10.0): "6,5"}),
    )
    assert report.vision_verifications, "vision third-witness block did not run under an injected reader"
    assert report.n_cells_vision_graduated == 1
    assert report.routes == (ROUTE_VISION_ESCALATE,)


# --------------------------------------------------------------------------- #
# 2. A verified table reaches the derived-IR sink and is read back.             #
# --------------------------------------------------------------------------- #


def test_self_verified_table_roundtrips_through_sink(tmp_path) -> None:
    table = _clean_2x2()
    store = DerivedTableStore(str(tmp_path / "derived.farchive"))
    try:
        report = build_statute_report(
            locator=_LOC,
            artifact_digest="a" * 64,
            page_texts=_PAGE_TEXTS,
            tables=[table],
            verifications=[_verif(table, n_exact=4)],
            derived_store=store,
        )
        assert report.routes == (ROUTE_SELF_VERIFIED,)
        rows = store.read_all()
        assert len(rows) == 1
        row = rows[0]
        assert isinstance(row, DerivedTableRow)
        assert row.grade == GRADE_SELF_VERIFIED
        assert row.artifact_digest == "a" * 64
        assert row.n_rows == 2 and row.n_cols == 2
        # the consumable structured content is read back verbatim
        assert {(c["row"], c["col"], c["text"]) for c in row.cells} == {
            (0, 0, "Vuosi"), (0, 1, "Vero"), (1, 0, "2025"), (1, 1, "6.5")
        }
        # content-addressed by (artifact_digest, table_index, quotient fingerprint)
        key = derived_table_key(
            artifact_digest="a" * 64, table_index=0, code_fingerprint=derived_ir_fingerprint()
        )
        assert store.get(key) is not None
    finally:
        store.close()


def test_exact_visual_graduated_table_reaches_sink(tmp_path) -> None:
    table = _clean_2x2()
    div = TableCellDivergence(1, 1, "6.5", "6,5")
    store = DerivedTableStore(str(tmp_path / "derived.farchive"))
    try:
        report = build_statute_report(
            locator=_LOC,
            artifact_digest="b" * 64,
            page_texts=_PAGE_TEXTS,
            tables=[table],
            verifications=[_verif(table, n_exact=3, divergences=[div])],
            vision_region_reader=_reader({(10.0, 5.0, 20.0, 10.0): "6,5"}),
            derived_store=store,
        )
        assert eligible_derived_tables(report) and eligible_derived_tables(report)[0][1] == GRADE_EXACT_VISUAL
        rows = store.read_all()
        assert len(rows) == 1 and rows[0].grade == GRADE_EXACT_VISUAL
    finally:
        store.close()


def test_open_divergent_table_is_NOT_sink_eligible(tmp_path) -> None:
    # vision disagrees with BOTH decoders → open divergence → the table is NOT verified → not sunk.
    table = _clean_2x2()
    div = TableCellDivergence(1, 1, "6.5", "6,5")
    store = DerivedTableStore(str(tmp_path / "derived.farchive"))
    try:
        report = build_statute_report(
            locator=_LOC,
            artifact_digest="c" * 64,
            page_texts=_PAGE_TEXTS,
            tables=[table],
            verifications=[_verif(table, n_exact=3, divergences=[div])],
            vision_region_reader=_reader({(10.0, 5.0, 20.0, 10.0): "9,9"}),
            derived_store=store,
        )
        assert eligible_derived_tables(report) == ()
        assert store.read_all() == ()
    finally:
        store.close()


# --------------------------------------------------------------------------- #
# 3. A disagreeing (open) cell produces a corroboration receipt.                #
# --------------------------------------------------------------------------- #


def test_disagreeing_cell_emits_corroboration_receipt() -> None:
    table = _clean_2x2()
    div = TableCellDivergence(1, 1, "6.5", "6,5")
    report = build_statute_report(
        locator=_LOC,
        artifact_digest="d" * 64,
        page_texts=_PAGE_TEXTS,
        tables=[table],
        verifications=[_verif(table, n_exact=3, divergences=[div])],
        vision_region_reader=_reader({(10.0, 5.0, 20.0, 10.0): "9,9"}),
    )
    assert len(report.corroboration_receipts) == 1
    receipt = report.corroboration_receipts[0]
    assert receipt.candidate == "6.5" and receipt.vision_read == "9,9"
    assert receipt.agreed is False  # the reads materially disagree
    assert receipt.witness_fingerprint  # tied to the determinism firewall
    # surfaced in the JSON (only because a receipt was actually produced)
    js = report.to_jsonable()
    assert js["corroboration"]["n_receipts"] == 1  # type: ignore[index]


def test_agreeing_open_read_is_a_graduation_not_a_disagreement_receipt() -> None:
    # A vision read that reproduces the pdfium witness graduates (no open divergence), so there is
    # NO corroboration receipt — the receipt lane fires only on genuine disagreement.
    table = _clean_2x2()
    div = TableCellDivergence(1, 1, "6.5", "6,5")
    report = build_statute_report(
        locator=_LOC,
        artifact_digest="d" * 64,
        page_texts=_PAGE_TEXTS,
        tables=[table],
        verifications=[_verif(table, n_exact=3, divergences=[div])],
        vision_region_reader=_reader({(10.0, 5.0, 20.0, 10.0): "6,5"}),
    )
    assert report.corroboration_receipts == ()


# --------------------------------------------------------------------------- #
# 4. The recall SCREEN selects a self-verified cell to escalate (gestalt reader). #
# --------------------------------------------------------------------------- #


def test_screen_flags_self_verified_cell_and_rides_corroborate_edge() -> None:
    table = _clean_2x2()
    # a gestalt reader that says the (1,1) region looks INCOMPLETE (a clipped/dropped column) — a
    # suspicion the deterministic exact check could never raise (both decoders agreed on that cell).
    def gestalt(_page_num, bbox):
        if bbox == (10.0, 5.0, 20.0, 10.0):
            return "legible: yes\ncomplete: no\nplausible: yes\nobviously_wrong: no\ndescriptor: right column clipped"
        return "legible: yes\ncomplete: yes\nplausible: yes\nobviously_wrong: no\ndescriptor: ok"

    report = build_statute_report(
        locator=_LOC,
        artifact_digest="d" * 64,
        page_texts=_PAGE_TEXTS,
        tables=[table],
        verifications=[_verif(table, n_exact=4)],  # all self-verified
        gestalt_region_reader=gestalt,
        vision_region_reader=_reader({(10.0, 5.0, 20.0, 10.0): "6,5"}),
    )
    assert len(report.screen_suspects) == 1
    assert report.screen_suspects[0].route.value == "route_to_structural"
    # the screen suspect ESCALATED onto the SAME corroborate edge (confronted vs a fresh vision read)
    assert len(report.corroboration_receipts) == 1
    assert report.corroboration_receipts[0].candidate == "6.5"
    js = report.to_jsonable()
    assert js["recall_screen"]["n_suspects"] == 1  # type: ignore[index]


def test_screen_clean_when_no_gestalt_reader() -> None:
    table = _clean_2x2()
    report = build_statute_report(
        locator=_LOC,
        artifact_digest="d" * 64,
        page_texts=_PAGE_TEXTS,
        tables=[table],
        verifications=[_verif(table, n_exact=4)],
    )
    assert report.screen_suspects == ()


# --------------------------------------------------------------------------- #
# 5. Determinism-firewall cache canary over the REAL make_vision_region_reader.  #
# --------------------------------------------------------------------------- #


class _FakeVisionProducer:
    """A hermetic stand-in for the :8080 vision producer (no network)."""

    _model = "fake-vision-canary-1"

    def __init__(self, text: str) -> None:
        self._text = text
        self.calls = 0

    def _resolve_model(self) -> str:
        return self._model

    def read_region_cold(self, _manifestation, _page_num, _bbox, *, dpi, expected_lines) -> str:
        self.calls += 1
        return self._text


def _minimal_pdf() -> bytes:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument.new()
    pdf.new_page(200, 200)
    buf = io.BytesIO()
    pdf.save(buf)
    return buf.getvalue()


def test_cached_vision_region_reader_canary(tmp_path) -> None:
    pdf_bytes = _minimal_pdf()
    digest = "e" * 64
    producer = _FakeVisionProducer("2 500 mk")
    inner = make_vision_region_reader(producer, pdf_bytes, artifact_digest=digest, locator=_LOC)
    from lawvm.tools.fi_appendix_structure import VisionReadStore

    vstore = VisionReadStore(str(tmp_path / "vision.farchive"))
    try:
        cached = make_cached_region_reader(
            inner,
            store=vstore,
            model_id=producer._resolve_model(),
            prompt_fingerprint=cold_region_prompt_fingerprint(),
            render_fingerprint=render_params_fingerprint(_VISION_REGION_DPI),
            crop_digest=make_region_crop_digester(
                pdf_bytes, artifact_digest=digest, locator=_LOC
            ),
        )
        bbox = (10.0, 10.0, 100.0, 40.0)  # top-left points within the 200×200 page
        first = cached(1, bbox)
        assert isinstance(first, VisionRegionRead)
        assert first.text == "2 500 mk" and not first.abstain
        assert producer.calls == 1
        # HIT: the SAME crop replays from the store WITHOUT invoking the model again (firewall).
        second = cached(1, bbox)
        assert second.text == "2 500 mk"
        assert producer.calls == 1, "cache MISS re-invoked the model — determinism firewall leaked"
    finally:
        vstore.close()


def test_region_reader_declines_to_abstain() -> None:
    pdf_bytes = _minimal_pdf()
    producer = _FakeVisionProducer("")  # empty read = the model DECLINED
    inner = make_vision_region_reader(producer, pdf_bytes, artifact_digest="f" * 64, locator=_LOC)
    read = inner(1, (10.0, 10.0, 100.0, 40.0))
    assert read.abstain is True and read.text == ""


# --------------------------------------------------------------------------- #
# 6. Drive the REAL structure_statute_pdf lane over a SYNTHETIC docling doc.     #
# --------------------------------------------------------------------------- #


def _table_node():
    from lawvm.core.source_document.anchors import BBox, SourceAnchor
    from lawvm.core.source_document.ir import (
        AssuranceTier,
        SourceDocumentNode,
        SourceDocumentNodeKind,
    )

    def cell(row, col, text, bb, header=False):
        return SourceDocumentNode(
            kind=SourceDocumentNodeKind.TABLE_CELL,
            assurance_tier=AssuranceTier.SINGLE_WITNESS,
            anchor=SourceAnchor(
                artifact_digest="0" * 64,
                locator=f"docling:page=1;table;row={row};col={col}",
                page_num=1,
                bbox=bb,
            ),
            text=text,
            attrs={"is_header": "1" if header else "0"},
        )

    def row(cells):
        return SourceDocumentNode(
            kind=SourceDocumentNodeKind.TABLE_ROW,
            assurance_tier=AssuranceTier.SINGLE_WITNESS,
            anchor=SourceAnchor(artifact_digest="0" * 64, locator="docling:page=1;table;row", page_num=1),
            children=tuple(cells),
        )

    return SourceDocumentNode(
        kind=SourceDocumentNodeKind.TABLE,
        assurance_tier=AssuranceTier.SINGLE_WITNESS,
        anchor=SourceAnchor(artifact_digest="0" * 64, locator="docling:page=1;table", page_num=1),
        text="cap",
        children=(
            row([
                cell(0, 0, "Vuosi", BBox(x0=0.0, y0=0.0, x1=10.0, y1=5.0), header=True),
                cell(0, 1, "Vero", BBox(x0=10.0, y0=0.0, x1=20.0, y1=5.0), header=True),
            ]),
            row([
                cell(1, 0, "2025", BBox(x0=0.0, y0=5.0, x1=10.0, y1=10.0)),
                cell(1, 1, "6.5", BBox(x0=10.0, y0=5.0, x1=20.0, y1=10.0)),
            ]),
        ),
    )


def test_structure_statute_pdf_drives_vision_and_sink(monkeypatch, tmp_path) -> None:
    import lawvm.ingest.llm_backends.docling_producer as dp
    import lawvm.tools.fi_appendix_structure as m

    sentinel = object()
    node = _table_node()
    # A structured table with one divergent cell; the pdfium witness disagrees with Docling so the
    # table routes to vision, and the injected reader reproduces the witness → graduates.
    table = m.structured_table_from_node(node, locator=_LOC, table_index=0)
    det = _verif(table, n_exact=3, divergences=[TableCellDivergence(1, 1, "6.5", "6,5")])

    monkeypatch.setattr(m, "_page_texts", lambda _b: ["x" * 200])
    monkeypatch.setattr(m, "_docling_document", lambda _b, name: sentinel)
    monkeypatch.setattr(dp, "_docling_document_to_page_views", lambda _doc: {1: sentinel})
    monkeypatch.setattr(dp, "docling_document_to_nodes", lambda _pv, artifact_digest, page_num: [node])
    monkeypatch.setattr(m, "_verify_tables_against_pdfium", lambda _b, _tables: (det,))

    store = DerivedTableStore(str(tmp_path / "derived.farchive"))
    try:
        report = structure_statute_pdf(
            _LOC,
            b"%PDF-fake",
            "9" * 64,
            vision_region_reader=_reader({(10.0, 5.0, 20.0, 10.0): "6,5"}),
            derived_store=store,
            vision_model="fake-model",
        )
        assert report.routes == (ROUTE_VISION_ESCALATE,)
        assert report.n_cells_vision_graduated == 1
        rows = store.read_all()
        assert len(rows) == 1 and rows[0].grade == GRADE_EXACT_VISUAL
        assert rows[0].artifact_digest == "9" * 64
    finally:
        store.close()

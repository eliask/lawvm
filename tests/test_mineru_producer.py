"""MinerU firewalled table producer — pure HTML-parse / lowering / VERIFY-GATE tests.

Hermetic: the grid-occupancy HTML parse, the ``MineruTable`` → ``StructuredTable``
lowering, and the born-digital text-layer VERIFY GATE are exercised with plain data
and a STUB ``content_list`` — NO mineru subprocess, NO external py3.12 venv, NO PDF,
NO network. The subprocess seam (:meth:`MineruProducer._run_subprocess`) is the
firewall boundary and is never driven in CI; only the store-replay / cold-offline
control flow of ``propose_page`` is tested (with an in-memory / tmp store).

Covers the brief's G4 verify-gate assertions:
  (a) agreement → ``cell_exact``;
  (b) a glyph disagreement (``INGARSKILAÅN`` vs MinerU's ``INGARSKILAÄN``) → a TYPED
      divergence, NOT graduated;
  (c) a rowspan/colspan table lowers with faithful positions, a malformed span
      type-DEFERS (``UnrepresentableSpan``), never a faked grid;
  (d) no-store / offline → ``propose_page`` returns ``None`` and makes no subprocess call.
"""
from __future__ import annotations

import pytest

from lawvm.ingest.llm_backends.mineru_producer import (
    MINERU_TEXT_LAYER_ABSENT,
    DeferredMineruTable,
    MineruCell,
    MineruPins,
    MineruProducer,
    MineruTable,
    MineruTableStore,
    UnrepresentableSpan,
    lower_mineru_table,
    mineru_table_locator,
    mineru_tables_for_page,
    mineru_tables_from_content_list,
    parse_mineru_table_html,
    verify_mineru_table_textlayer,
)
from lawvm.tools.fi_appendix_structure import (
    ROUTE_NO_WITNESS_DEFERRED,
    ROUTE_SELF_VERIFIED,
    ROUTE_VISION_ESCALATE,
    StructuredTable,
    TableVerification,
    table_escalation_route,
)


# --------------------------------------------------------------------------- #
# HTML grid-occupancy parse (rowspan / colspan)                               #
# --------------------------------------------------------------------------- #


def test_flat_table_parses_to_row_major_cells() -> None:
    html = "<table><tr><td>a</td><td>b</td></tr><tr><td>1</td><td>2</td></tr></table>"
    cells, n_rows, n_cols = parse_mineru_table_html(html)
    assert (n_rows, n_cols) == (2, 2)
    assert [(c.row, c.col, c.text) for c in cells] == [
        (0, 0, "a"),
        (0, 1, "b"),
        (1, 0, "1"),
        (1, 1, "2"),
    ]
    assert all(c.rowspan == 1 and c.colspan == 1 for c in cells)


def test_nested_rowspan_colspan_lowers_with_faithful_positions() -> None:
    # The metsavero nested header shape: a rowspan=3 label, two colspan=3 groups, a rowspan=2.
    html = (
        "<table>"
        '<tr><td rowspan="3">Lääni ja kunta</td><td>Mänty-</td><td>Kuusi-</td>'
        '<td>Koivu-</td><td rowspan="2">Hukkapuu</td></tr>'
        '<tr><td colspan="3">tukkipuu</td></tr>'
        '<tr><td colspan="4">%</td></tr>'
        "</table>"
    )
    cells, n_rows, n_cols = parse_mineru_table_html(html)
    assert n_rows == 3
    by_text = {c.text: c for c in cells}
    # The rowspan=3 label sits at (0,0) and records its footprint, not duplicated.
    assert (by_text["Lääni ja kunta"].row, by_text["Lääni ja kunta"].col) == (0, 0)
    assert by_text["Lääni ja kunta"].rowspan == 3
    # The colspan=3 "tukkipuu" starts at row 1, col 1 (col 0 claimed by the rowspan above).
    assert (by_text["tukkipuu"].row, by_text["tukkipuu"].col) == (1, 1)
    assert by_text["tukkipuu"].colspan == 3
    # "Hukkapuu" (rowspan=2) placed at the last column of row 0.
    assert by_text["Hukkapuu"].rowspan == 2
    # The '%' colspan=4 on row 2 starts at col 1 (col 0 still under the rowspan=3 label).
    assert (by_text["%"].row, by_text["%"].col) == (2, 1)


def test_th_marks_header_cells() -> None:
    cells, _, _ = parse_mineru_table_html("<table><tr><th>H</th><td>v</td></tr></table>")
    assert cells[0].is_header is True
    assert cells[1].is_header is False


def test_malformed_span_type_defers_never_fakes() -> None:
    # colspan=0 and a negative rowspan are unrepresentable spans → typed defer, not a fake grid.
    with pytest.raises(UnrepresentableSpan):
        parse_mineru_table_html('<table><tr><td colspan="0">x</td></tr></table>')
    with pytest.raises(UnrepresentableSpan):
        parse_mineru_table_html('<table><tr><td rowspan="-1">x</td></tr></table>')
    with pytest.raises(UnrepresentableSpan):
        parse_mineru_table_html('<table><tr><td colspan="foo">x</td></tr></table>')


def test_overlapping_span_type_defers() -> None:
    # A rowspan from row 0 col 0 collides with an explicit cell placed at the same slot.
    html = '<table><tr><td rowspan="2">A</td></tr><tr></tr></table>'
    cells, _, _ = parse_mineru_table_html(html)  # this one is representable
    assert cells[0].rowspan == 2
    # A genuine overlap (a colspan FOOTPRINT overrunning a column a prior rowspan claimed)
    # raises: B's rowspan holds (1,1); C starts free at (1,0) but its colspan=3 fill hits (1,1).
    bad = (
        "<table>"
        '<tr><td>A</td><td rowspan="2">B</td></tr>'
        '<tr><td colspan="3">C</td></tr>'  # C at (1,0) col-fills into (1,1) held by B
        "</table>"
    )
    with pytest.raises(UnrepresentableSpan):
        parse_mineru_table_html(bad)


# --------------------------------------------------------------------------- #
# Lowering MinerU → StructuredTable IR                                        #
# --------------------------------------------------------------------------- #


def test_lower_mineru_table_places_cells_without_faking_bbox() -> None:
    mt = MineruTable(
        locator="finlex://x",
        page_num=3,
        table_index=0,
        n_rows=1,
        n_cols=4,
        caption="cap",
        cells=(
            MineruCell(row=0, col=0, rowspan=1, colspan=3, text="span", is_header=True),
            MineruCell(row=0, col=3, rowspan=1, colspan=1, text="tail", is_header=False),
        ),
    )
    st = lower_mineru_table(mt)
    assert isinstance(st, StructuredTable)
    assert st.page_num == 3 and st.n_cols == 4
    # Each logical cell placed ONCE at its top-left; NO per-cell bbox is invented.
    assert [(c.row, c.col, c.text, c.is_header) for c in st.cells] == [
        (0, 0, "span", True),
        (0, 3, "tail", False),
    ]
    assert all(c.bbox is None for c in st.cells)


# --------------------------------------------------------------------------- #
# THE VERIFY GATE                                                             #
# --------------------------------------------------------------------------- #


def _table(*texts: str) -> MineruTable:
    return MineruTable(
        locator="finlex://x",
        page_num=1,
        table_index=0,
        n_rows=1,
        n_cols=len(texts),
        caption="",
        cells=tuple(
            MineruCell(row=0, col=i, rowspan=1, colspan=1, text=t)
            for i, t in enumerate(texts)
        ),
    )


def test_verify_gate_agreement_graduates_cell_exact() -> None:
    # (a) every cell's content present in the born-digital text layer → all cell_exact.
    table = _table("PIELINEN 62 TÖRÖKARI", "04.411", "7025631")
    region = "Havaintopaikan nimi PIELINEN 62 TÖRÖKARI 04.411 7025631 3649386"
    verif = verify_mineru_table_textlayer(table, region)
    assert isinstance(verif, TableVerification)
    assert verif.n_exact == 3
    assert verif.divergences == ()
    assert verif.exact is True
    assert table_escalation_route(verif) == ROUTE_SELF_VERIFIED


def test_verify_gate_glyph_error_is_typed_divergence_not_graduated() -> None:
    # (b) MinerU's Å→Ä (INGARSKILAÄN) is ABSENT from a text layer that has INGARSKILAÅN
    #     → a TYPED divergence carrying the MinerU candidate; the cell does NOT graduate.
    table = _table("INGARSKILAÄN 0,4", "81.064")
    region = "17 INGARSKILAÅN 0,4 81.064 6666449 3342966"  # correct Å in the layer
    verif = verify_mineru_table_textlayer(table, region)
    assert verif.exact is False
    assert verif.n_exact == 1  # the "81.064" cell corroborated
    assert len(verif.divergences) == 1
    d = verif.divergences[0]
    assert d.docling_text == "INGARSKILAÄN 0,4"  # the MinerU candidate content
    assert d.descriptor == MINERU_TEXT_LAYER_ABSENT
    assert table_escalation_route(verif) == ROUTE_VISION_ESCALATE


def test_verify_gate_quotient_folds_case_and_whitespace() -> None:
    # A benign whitespace / decimal difference the inert quotient folds still graduates,
    # while a real glyph change would not (guarded by the case-sensitive quotient).
    table = _table("2 500", "raikasta")
    region = "raikasta 2 500 mg/kg"
    verif = verify_mineru_table_textlayer(table, region)
    assert verif.exact is True and verif.n_exact == 2


def test_verify_gate_sparse_page_defers_never_forces() -> None:
    # (scanned page: no born-digital tokens) → every cell no_witness, 0 forced divergences.
    table = _table("value", "42")
    verif = verify_mineru_table_textlayer(table, "   ")
    assert verif.n_no_witness == 2
    assert verif.n_exact == 0
    assert verif.divergences == ()
    assert table_escalation_route(verif) == ROUTE_NO_WITNESS_DEFERRED


def test_verify_gate_empty_cells_are_vacuously_exact() -> None:
    table = _table("", "13")
    verif = verify_mineru_table_textlayer(table, "row 13 here")
    assert verif.n_exact == 2 and verif.divergences == ()


# --------------------------------------------------------------------------- #
# content_list lowering + the additive per-page lane                          #
# --------------------------------------------------------------------------- #


def _content_list(table_body: str) -> list[dict[str, object]]:
    return [
        {"type": "text", "text": "heading", "bbox": [0, 0, 1, 1], "page_idx": 0},
        {
            "type": "table",
            "table_body": table_body,
            "table_caption": ["Liite 3"],
            "table_footnote": [],
            "bbox": [92, 161, 892, 806],
            "page_idx": 0,
        },
    ]


def test_content_list_lowering_and_page_offset() -> None:
    cl = _content_list("<table><tr><td>a</td><td>b</td></tr></table>")
    tables, deferred = mineru_tables_from_content_list(cl, locator="finlex://x")
    assert deferred == ()
    assert len(tables) == 1
    t = tables[0]
    assert t.page_num == 1  # 0-indexed page_idx + default offset 1
    assert t.caption == "Liite 3"
    assert t.bbox == (92.0, 161.0, 892.0, 806.0)


def test_content_list_defers_unrepresentable_table() -> None:
    cl = _content_list('<table><tr><td colspan="0">x</td></tr></table>')
    tables, deferred = mineru_tables_from_content_list(cl, locator="finlex://x")
    assert tables == ()
    assert len(deferred) == 1
    assert isinstance(deferred[0], DeferredMineruTable)
    assert "unrepresentable_span" in deferred[0].reason


def test_additive_lane_end_to_end_pure() -> None:
    cl = _content_list(
        "<table><tr><td>Nimi</td><td>Arvo</td></tr>"
        "<tr><td>ESIMERKKI 5</td><td>04.411</td></tr></table>"
    )
    region = "Nimi Arvo ESIMERKKI 5 04.411"
    structured, verifications, routes, deferred = mineru_tables_for_page(
        cl, region, locator="finlex://x"
    )
    assert deferred == ()
    assert len(structured) == 1 and isinstance(structured[0], StructuredTable)
    assert routes == (ROUTE_SELF_VERIFIED,)
    assert verifications[0].exact is True


# --------------------------------------------------------------------------- #
# Content-addressed store + offline/no-subprocess control flow                #
# --------------------------------------------------------------------------- #


def test_store_roundtrip_and_pin_rekeying(tmp_path) -> None:
    store = MineruTableStore(str(tmp_path / "mineru.farchive"))
    try:
        cl = _content_list("<table><tr><td>a</td></tr></table>")
        fp = MineruPins().fingerprint()
        assert store.get("digestA", 0, fp) is None  # cold
        store.put("digestA", 0, fp, cl)
        got = store.get("digestA", 0, fp)
        assert got is not None and got[1]["type"] == "table"
        # A pin change (different transformers version) re-keys → the old read is not served.
        fp2 = MineruPins(transformers_version="9.9.9").fingerprint()
        assert fp2 != fp
        assert store.get("digestA", 0, fp2) is None
    finally:
        store.close()


def test_locator_is_content_addressed_by_pins() -> None:
    loc = mineru_table_locator("deadbeef", 3, "PINFP")
    assert loc == "mineru/deadbeef/PINFP/page/0003"


def test_propose_page_cold_offline_returns_none_no_subprocess(tmp_path) -> None:
    # (d) no warm entry + live=False → None and NO subprocess (the firewall). We assert
    #     _run_subprocess is never reached by making it raise if called.
    store = MineruTableStore(str(tmp_path / "mineru.farchive"))
    producer = MineruProducer(store=store)

    def _boom(_pdf: bytes) -> list[object]:  # pragma: no cover - must not run
        raise AssertionError("subprocess must not be called on a cold offline propose")

    producer._run_subprocess = _boom  # type: ignore[method-assign]
    try:
        assert producer.propose_page(b"%PDF-1.4", 0, "digestZ", live=False) is None
    finally:
        store.close()


def test_propose_page_warm_store_replays_without_subprocess(tmp_path) -> None:
    store = MineruTableStore(str(tmp_path / "mineru.farchive"))
    producer = MineruProducer(store=store)
    cl = _content_list("<table><tr><td>a</td></tr></table>")
    store.put("digestW", 2, MineruPins().fingerprint(), cl)

    def _boom(_pdf: bytes) -> list[object]:  # pragma: no cover - must not run
        raise AssertionError("warm replay must not call the subprocess")

    producer._run_subprocess = _boom  # type: ignore[method-assign]
    try:
        got = producer.propose_page(b"%PDF", 2, "digestW", live=True)
        assert got is not None and got[1]["type"] == "table"
    finally:
        store.close()


def test_is_available_gates_on_external_venv(tmp_path) -> None:
    producer = MineruProducer(venv_path=str(tmp_path / "nope_env"))
    assert producer.is_available() is False

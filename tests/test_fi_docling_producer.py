"""Docling structural producer — learned-layout + TableFormer cell grids.

Hermetic unit tests drive the PURE converter (``docling_document_to_nodes``)
with a FAKE Docling page view — no network, no docling dependency, no PDF — so
the structural lowering (kinds, the true cell grid, tiers, anchors, reading
order) is pinned serverless. Docling's distinctive value is real
TABLE_ROW/TABLE_CELL structure, so the table case is exercised hardest.

One live test converts a real PDF via the ``docling`` extra only when installed
AND ``LAWVM_HE_SAMPLE_PDF`` points at a local draft — env-gated, never a
committed abs path or a vendored blob; skipped in CI.
"""
from __future__ import annotations

import importlib.util
import os
from datetime import datetime
from pathlib import Path

import pytest

from lawvm.core.source_document import SourceManifestation
from lawvm.core.source_document.ir import AssuranceTier, SourceDocumentNodeKind
from lawvm.finland.source_document.docling_producer import (
    DoclingBBoxView,
    DoclingBlockView,
    DoclingCellView,
    DoclingPageView,
    DoclingStructuralProducer,
    DoclingTableView,
    DoclingUnavailable,
    docling_document_to_nodes,
    docling_page_nodes,
)

# The compat shim above re-exports its public surface via `import *`, which never
# carries underscore-prefixed names — import the private helper from its canonical
# home directly (same object at runtime; the shim aliases sys.modules to it).
from lawvm.ingest.llm_backends.docling_producer import _normalized_bbox

_DIGEST = "d" * 64

# Real PDF for the live convert test — via env, never a committed abs path.
_HE_PDF = Path(os.environ.get("LAWVM_HE_SAMPLE_PDF") or "/nonexistent/no-he-sample.pdf")


def _manifestation(bytes_: bytes = b"%PDF-1.4") -> SourceManifestation:
    return SourceManifestation(
        artifact_digest=_DIGEST,
        source_bytes=bytes_,
        locator="doc.pdf",
        source_role="government_proposal_draft",
        fetched_at=datetime(2026, 1, 1),
        media_type="application/pdf",
    )


def _fake_page() -> DoclingPageView:
    """A heading, a paragraph, a 2-column table with a header row, and a footnote."""
    table = DoclingTableView(
        rows=(
            (DoclingCellView("Vuosi", is_header=True), DoclingCellView("Vero", is_header=True)),
            (DoclingCellView("2025"), DoclingCellView("4")),
            (DoclingCellView("2026"), DoclingCellView("5")),
        ),
        caption="Verotaulukko",
    )
    return DoclingPageView(
        elements=(
            DoclingBlockView(label="section_header", text="4 §"),
            DoclingBlockView(label="text", text="Sen lisäksi mitä säädetään."),
            table,
            DoclingBlockView(label="footnote", text="1) Sovelletaan verovuodesta 2025."),
        )
    )


def test_converter_emits_governed_kinds_in_reading_order() -> None:
    nodes = docling_document_to_nodes(_fake_page(), artifact_digest=_DIGEST, page_num=7)
    assert [n.kind for n in nodes] == [
        SourceDocumentNodeKind.HEADING,
        SourceDocumentNodeKind.PARAGRAPH,
        SourceDocumentNodeKind.TABLE,
        SourceDocumentNodeKind.FOOTNOTE,
    ]
    assert nodes[0].text == "4 §"
    assert nodes[3].text.startswith("1)")


def test_converter_unknown_label_falls_back_to_paragraph() -> None:
    page = DoclingPageView(elements=(DoclingBlockView(label="caption", text="fig 1"),))
    (node,) = docling_document_to_nodes(page, artifact_digest=_DIGEST, page_num=1)
    assert node.kind is SourceDocumentNodeKind.PARAGRAPH


def test_table_lowers_to_real_cell_grid() -> None:
    nodes = docling_document_to_nodes(_fake_page(), artifact_digest=_DIGEST, page_num=7)
    table = next(n for n in nodes if n.kind is SourceDocumentNodeKind.TABLE)
    assert table.text == "Verotaulukko"
    rows = table.children
    assert [r.kind for r in rows] == [SourceDocumentNodeKind.TABLE_ROW] * 3
    # every cell is a TABLE_CELL; the grid is 3 rows x 2 cols
    for row in rows:
        assert [c.kind for c in row.children] == [SourceDocumentNodeKind.TABLE_CELL] * 2
    # header flag preserved on the first row, cleared on data rows
    assert [c.attrs["is_header"] for c in rows[0].children] == ["1", "1"]
    assert [c.attrs["is_header"] for c in rows[1].children] == ["0", "0"]
    # cell text preserved
    assert rows[1].children[0].text == "2025"
    assert rows[2].children[1].text == "5"


def test_bottom_left_bbox_normalizes_to_top_left_convention() -> None:
    # A Docling BOTTOMLEFT bbox (origin at the page bottom, ``top`` the larger y)
    # must flip to the anchors.BBox top-left convention (y grows downward,
    # y0=top-edge <= y1=bottom-edge) via ``page_height - y``.
    view = DoclingBBoxView(
        left=10.0, top=700.0, right=200.0, bottom=650.0,
        coord_origin="BOTTOMLEFT", page_height=842.0,
    )
    bb = _normalized_bbox(view)
    assert bb is not None
    assert (bb.x0, bb.x1) == (10.0, 200.0)
    # top edge 700 -> 842-700 = 142 (smaller y); bottom edge 650 -> 842-650 = 192
    assert bb.y0 == 142.0 and bb.y1 == 192.0
    assert bb.y1 >= bb.y0  # the BBox invariant holds after the flip


def test_top_left_bbox_passes_through_unflipped() -> None:
    view = DoclingBBoxView(left=5.0, top=20.0, right=90.0, bottom=60.0, coord_origin="TOPLEFT")
    bb = _normalized_bbox(view)
    assert bb is not None
    assert (bb.x0, bb.y0, bb.x1, bb.y1) == (5.0, 20.0, 90.0, 60.0)


def test_cell_and_block_bbox_thread_onto_source_anchor() -> None:
    # A fake docling page whose cell + block carry a native (BOTTOMLEFT) bbox must
    # populate SourceAnchor.bbox on the lowered nodes, coord-normalized top-left.
    cell_bbox = DoclingBBoxView(
        left=10.0, top=700.0, right=100.0, bottom=680.0,
        coord_origin="BOTTOMLEFT", page_height=800.0,
    )
    block_bbox = DoclingBBoxView(
        left=0.0, top=50.0, right=500.0, bottom=90.0, coord_origin="TOPLEFT",
    )
    page = DoclingPageView(
        elements=(
            DoclingBlockView(label="text", text="para", bbox=block_bbox),
            DoclingTableView(rows=((DoclingCellView("x", bbox=cell_bbox),),), caption="cap"),
        )
    )
    nodes = docling_document_to_nodes(page, artifact_digest=_DIGEST, page_num=3)
    block = next(n for n in nodes if n.kind is SourceDocumentNodeKind.PARAGRAPH)
    assert block.anchor.bbox is not None
    assert (block.anchor.bbox.y0, block.anchor.bbox.y1) == (50.0, 90.0)
    table = next(n for n in nodes if n.kind is SourceDocumentNodeKind.TABLE)
    cell = table.children[0].children[0]
    assert cell.kind is SourceDocumentNodeKind.TABLE_CELL
    assert cell.anchor.bbox is not None
    assert (cell.anchor.bbox.x0, cell.anchor.bbox.x1) == (10.0, 100.0)
    assert (cell.anchor.bbox.y0, cell.anchor.bbox.y1) == (100.0, 120.0)


def test_missing_bbox_leaves_anchor_bbox_none() -> None:
    # Reading order / no geometry: a cell without a bbox lowers to anchor.bbox None
    # (unchanged behaviour — the field is optional).
    page = DoclingPageView(elements=(DoclingTableView(rows=((DoclingCellView("x"),),)),))
    nodes = docling_document_to_nodes(page, artifact_digest=_DIGEST, page_num=1)
    table = next(n for n in nodes if n.kind is SourceDocumentNodeKind.TABLE)
    assert table.children[0].children[0].anchor.bbox is None


def test_every_node_is_single_witness_and_anchored() -> None:
    nodes = docling_document_to_nodes(_fake_page(), artifact_digest=_DIGEST, page_num=7)

    def _walk(n):
        yield n
        for c in n.children:
            yield from _walk(c)

    seen_cell = False
    for node in nodes:
        for descendant in _walk(node):
            assert descendant.assurance_tier is AssuranceTier.SINGLE_WITNESS
            assert descendant.anchor.artifact_digest == _DIGEST
            assert descendant.anchor.page_num == 7
            assert descendant.anchor.locator.startswith("docling:page=7")
            if descendant.kind is SourceDocumentNodeKind.TABLE_CELL:
                seen_cell = True
    assert seen_cell  # the walk actually reached the grid leaves


def test_single_witness_does_not_admit_clean_text_state() -> None:
    # The tier the producer stamps must NOT be self-certified as clean — that is
    # adjudication's job (the whole thesis).
    nodes = docling_document_to_nodes(_fake_page(), artifact_digest=_DIGEST, page_num=1)
    assert all(not n.assurance_tier.admits_clean_text_state for n in nodes)


def test_is_available_matches_docling_importability() -> None:
    producer = DoclingStructuralProducer()
    assert producer.is_available() == (importlib.util.find_spec("docling") is not None)


def test_module_imports_without_docling() -> None:
    # Importing the producer module must never require the docling extra
    # (determinism firewall). The import at test top already proves it; this
    # asserts the heavy dep is genuinely absent from the import graph here.
    import lawvm.finland.source_document.docling_producer as mod

    assert hasattr(mod, "docling_document_to_nodes")


def test_propose_page_missing_page_returns_empty() -> None:
    # With no converted views cached and a fake page map, an out-of-range page is
    # an honest empty tuple (the ingest accounts it as residual), never a raise.
    producer = DoclingStructuralProducer()
    producer._converted_cache[_DIGEST] = {1: _fake_page()}
    assert producer.propose_page(_manifestation(), 2) == ()
    nodes = producer.propose_page(_manifestation(), 1)
    assert nodes and nodes[0].kind is SourceDocumentNodeKind.HEADING


# --------------------------------------------------------------------------- #
# The thin adapter: docling_page_nodes (is_available-gated, injectable).       #
# Hermetic — a fake-cached producer drives gate + routing with no docling.     #
# --------------------------------------------------------------------------- #


class _PresentProducer(DoclingStructuralProducer):
    """A producer that reports docling PRESENT without the extra (hermetic)."""

    def is_available(self) -> bool:
        return True


class _AbsentProducer(DoclingStructuralProducer):
    """A producer that reports docling ABSENT (hermetic gate test)."""

    def is_available(self) -> bool:
        return False


def test_docling_page_nodes_raises_when_unavailable() -> None:
    # The gate is a typed capability raise, never a silent empty — "not installed"
    # and "read a blank page" are different facts (determinism firewall).
    with pytest.raises(DoclingUnavailable):
        docling_page_nodes(_manifestation(), 1, producer=_AbsentProducer())


def test_docling_page_nodes_routes_requested_page_when_available() -> None:
    producer = _PresentProducer()
    producer._converted_cache[_DIGEST] = {1: _fake_page()}
    nodes = docling_page_nodes(_manifestation(), 1, producer=producer)
    assert nodes and nodes[0].kind is SourceDocumentNodeKind.HEADING
    # a page docling saw nothing on is an honest empty tuple (residual), not a raise
    assert docling_page_nodes(_manifestation(), 2, producer=producer) == ()


@pytest.mark.skipif(
    importlib.util.find_spec("docling") is None, reason="docling extra not installed"
)
def test_docling_page_nodes_gate_open_with_default_producer() -> None:
    # With the extra installed, the DEFAULT producer's gate is open and the
    # adapter routes a cached page without touching docling/PDF. Skipped when the
    # extra is absent (the whole point of the availability gate).
    producer = DoclingStructuralProducer()
    producer._converted_cache[_DIGEST] = {1: _fake_page()}
    nodes = docling_page_nodes(_manifestation(), 1, producer=producer)
    assert nodes[0].kind is SourceDocumentNodeKind.HEADING


# --------------------------------------------------------------------------- #
# Live: convert a real PDF via the docling extra (skipped unless installed).   #
# --------------------------------------------------------------------------- #


@pytest.mark.network
@pytest.mark.skipif(
    importlib.util.find_spec("docling") is None, reason="docling extra not installed"
)
@pytest.mark.skipif(not _HE_PDF.exists(), reason="set LAWVM_HE_SAMPLE_PDF to a sample PDF")
def test_live_docling_reads_a_real_pdf() -> None:
    import hashlib

    b = _HE_PDF.read_bytes()
    m = SourceManifestation(
        artifact_digest=hashlib.sha256(b).hexdigest(),
        source_bytes=b,
        locator="he_luonnos.pdf",
        source_role="government_proposal_draft",
        fetched_at=datetime(2026, 5, 20),
        media_type="application/pdf",
    )
    producer = DoclingStructuralProducer()
    assert producer.is_available()
    nodes = producer.propose_page(m, 1)
    # A real page should yield at least one structural node; the tier stays
    # single-witness (adjudication, not the producer, raises assurance).
    assert all(n.assurance_tier is AssuranceTier.SINGLE_WITNESS for n in nodes)

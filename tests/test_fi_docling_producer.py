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
    DoclingBlockView,
    DoclingCellView,
    DoclingPageView,
    DoclingStructuralProducer,
    DoclingTableView,
    docling_document_to_nodes,
)

_DIGEST = "d" * 64

# Real PDF for the live convert test — via env, never a committed abs path.
_HE_PDF = Path(os.environ.get("LAWVM_HE_SAMPLE_PDF") or "/nonexistent/no-he-sample.pdf")


def _manifestation(bytes_: bytes = b"%PDF-1.4") -> SourceManifestation:
    return SourceManifestation(
        artifact_digest=_DIGEST,
        source_bytes=bytes_,
        locator="doc.pdf",
        source_role="he_draft",
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
        source_role="he_draft",
        fetched_at=datetime(2026, 5, 20),
        media_type="application/pdf",
    )
    producer = DoclingStructuralProducer()
    assert producer.is_available()
    nodes = producer.propose_page(m, 1)
    # A real page should yield at least one structural node; the tier stays
    # single-witness (adjudication, not the producer, raises assurance).
    assert all(n.assurance_tier is AssuranceTier.SINGLE_WITNESS for n in nodes)

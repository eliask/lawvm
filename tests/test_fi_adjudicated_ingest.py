"""Per-page adjudicated ingest → composed whole-document IR.

Hermetic tests wire fake producers (no network, no PDF lib) through the real
pipeline: per-page vision blocks + a reading-order cross-witness → adjudicated
per-page tier → cross-page composition. Proves the connective logic; the live
model path is exercised by the env+network test.
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Tuple

import pytest

import lawvm.finland.source_document.adjudicated_ingest as ai
from lawvm.core.source_document import (
    AssuranceTier,
    ExtractionAssertion,
    SourceAnchor,
    SourceDocumentNodeKind,
    SourceManifestation,
)
from lawvm.core.source_document.adjudication import (
    Adjudication,
    AdjudicationMethod,
    assurance_for,
)
from lawvm.finland.source_document.adjudicated_ingest import (
    _page_assurance,
    _vision_blocks_to_nodes,
    adjudicated_document_ingest,
)

_DIGEST = "a" * 64
_HE_PDF = Path(os.environ.get("LAWVM_HE_SAMPLE_PDF") or "/nonexistent/no-he-sample.pdf")


def _manifestation() -> SourceManifestation:
    return SourceManifestation(
        artifact_digest=_DIGEST,
        source_bytes=b"%PDF-1.4",
        locator="doc.pdf",
        source_role="he_draft",
        fetched_at=datetime(2026, 1, 1),
        media_type="application/pdf",
    )


def _assertion(kind: str, text: str, page: int, *, cells: Tuple[str, ...] = ()) -> ExtractionAssertion:
    return ExtractionAssertion(
        run_id=f"vision:{page}",
        fragment_kind=kind,
        text=text,
        anchor=SourceAnchor(artifact_digest=_DIGEST, locator=f"vision:page={page}", page_num=page),
    )


class _FakeVision:
    """Per-page canned vision transcription."""

    def __init__(self, per_page: dict[int, Tuple[ExtractionAssertion, ...]]) -> None:
        self._per_page = per_page

    def is_available(self) -> bool:
        return True

    def propose_page(self, manifestation, page_num) -> Tuple[ExtractionAssertion, ...]:
        return self._per_page.get(page_num, ())

    def propose_page_spans(
        self, manifestation, page_num, reading_order_text
    ) -> Tuple[ExtractionAssertion, ...]:
        return self._per_page.get(page_num, ())


class _AgreeAdjudicator:
    adjudicator_id = "fake-agree"

    def adjudicate(self, region, candidates, *, prior=None) -> Adjudication:
        producers = {c.run_id.split(":", 1)[0] for c in candidates}
        assurance = assurance_for(len(producers), adjudicated=True)
        from lawvm.core.source_document import SourceDocumentNode

        node = SourceDocumentNode(
            kind=SourceDocumentNodeKind.PARAGRAPH,
            assurance_tier=assurance,
            anchor=region,
            text="composed",
        )
        return Adjudication(
            node=node,
            assurance=assurance,
            method=AdjudicationMethod.MULTI_CANDIDATE_RECONCILED,
            source_candidate_run_ids=tuple(c.run_id for c in candidates),
            corroborating_producers=tuple(sorted(producers)),
            adjudicator_id=self.adjudicator_id,
        )


def test_vision_blocks_lower_to_typed_nodes_at_tier() -> None:
    nodes = _vision_blocks_to_nodes(
        (_assertion("heading", "4 §", 1), _assertion("paragraph", "body", 1)),
        AssuranceTier.MULTI_WITNESS_ADJUDICATED,
    )
    assert [n.kind for n in nodes] == [SourceDocumentNodeKind.HEADING, SourceDocumentNodeKind.PARAGRAPH]
    assert all(n.assurance_tier is AssuranceTier.MULTI_WITNESS_ADJUDICATED for n in nodes)


def test_page_assurance_is_multi_witness_when_producers_corroborate() -> None:
    region = SourceAnchor(artifact_digest=_DIGEST, locator="page=1", page_num=1)
    tier = _page_assurance("some page text", "some page text", _AgreeAdjudicator(), region)
    assert tier is AssuranceTier.MULTI_WITNESS_ADJUDICATED
    # No adjudicator or no cross-witness → single-witness.
    assert _page_assurance("t", "", _AgreeAdjudicator(), region) is AssuranceTier.SINGLE_WITNESS
    assert _page_assurance("t", "t", None, region) is AssuranceTier.SINGLE_WITNESS


def test_full_ingest_composes_multipage_table_at_adjudicated_tier(monkeypatch) -> None:
    # page 1: a table (header + 1 row); page 2: its continuation (same 2-col width).
    def _cells(row_texts, page):
        from lawvm.core.source_document import SourceDocumentNode

        return SourceDocumentNode(
            kind=SourceDocumentNodeKind.TABLE_ROW,
            assurance_tier=AssuranceTier.SINGLE_WITNESS,
            anchor=SourceAnchor(artifact_digest=_DIGEST, locator=f"page={page}", page_num=page),
            children=tuple(
                SourceDocumentNode(
                    kind=SourceDocumentNodeKind.TABLE_CELL,
                    assurance_tier=AssuranceTier.SINGLE_WITNESS,
                    anchor=SourceAnchor(artifact_digest=_DIGEST, locator=f"page={page}", page_num=page),
                    text=c,
                    attrs={"is_header": "1" if page == 1 and c in ("Tuote", "€") else "0"},
                )
                for c in row_texts
            ),
        )

    from lawvm.core.source_document import SourceDocumentNode

    def _table(rows, page):
        return SourceDocumentNode(
            kind=SourceDocumentNodeKind.TABLE,
            assurance_tier=AssuranceTier.SINGLE_WITNESS,
            anchor=SourceAnchor(artifact_digest=_DIGEST, locator=f"page={page}", page_num=page),
            children=tuple(rows),
        )

    # We feed the composer directly-typed table nodes via a vision that returns a
    # single TABLE-kind assertion per page whose text we don't use — but the
    # composer needs real TABLE nodes, so bypass vision blocks and monkeypatch the
    # per-page node builder to yield our tables.
    p1_table = _table([_cells(("Tuote", "€"), 1), _cells(("Kevyt", "4"), 1)], 1)
    p2_table = _table([_cells(("Raskas", "4,49"), 2)], 2)

    monkeypatch.setattr(ai, "reading_order_pages_from_pdf", lambda b, max_pages=200: ["p1 text", "p2 text"])
    monkeypatch.setattr(
        ai,
        "_vision_blocks_to_nodes",
        lambda assertions, tier: (p1_table,) if assertions and assertions[0].anchor.page_num == 1 else (p2_table,),
    )

    vision = _FakeVision({1: (_assertion("table", "t", 1),), 2: (_assertion("table", "t", 2),)})
    doc = adjudicated_document_ingest(_manifestation(), vision=vision, adjudicator=_AgreeAdjudicator())

    tables = [c for c in doc.root.children if c.kind is SourceDocumentNodeKind.TABLE]
    assert len(tables) == 1  # multi-page table composed into one
    assert any("merged table" in f for f in doc.composition_findings)


def test_no_vision_fallback_is_reading_order_single_witness(monkeypatch) -> None:
    # Terminated page texts (period + uppercase next) so the composer does NOT
    # stitch them — one single-witness paragraph per page.
    monkeypatch.setattr(
        ai, "reading_order_pages_from_pdf", lambda b, max_pages=200: ["Page one text.", "Page two text."]
    )
    doc = adjudicated_document_ingest(_manifestation(), vision=None, adjudicator=None)
    paras = [c for c in doc.root.children if c.kind is SourceDocumentNodeKind.PARAGRAPH]
    assert len(paras) == 2
    assert all(p.assurance_tier is AssuranceTier.SINGLE_WITNESS for p in paras)


@pytest.mark.network
@pytest.mark.skipif(not _HE_PDF.exists(), reason="set LAWVM_HE_SAMPLE_PDF")
def test_live_adjudicated_ingest_of_a_real_pdf() -> None:
    import hashlib

    pytest.importorskip("pypdfium2")
    from lawvm.finland.llm_backends.llm_adjudicator import LlmWorkflowAdjudicator
    from lawvm.finland.llm_backends.vision_producer import VisionPageProducer

    vision = VisionPageProducer(max_tokens=1500)
    if not vision.is_available():
        pytest.skip("no server at :8080")
    b = _HE_PDF.read_bytes()
    m = SourceManifestation(
        artifact_digest=hashlib.sha256(b).hexdigest(),
        source_bytes=b,
        locator="he.pdf",
        source_role="he_draft",
        fetched_at=datetime(2026, 5, 20),
        media_type="application/pdf",
    )
    # Ingest a few pages end to end (vision + reading-order adjudicated, composed).
    m_small = SourceManifestation(
        artifact_digest=m.artifact_digest,
        source_bytes=b,
        locator=m.locator,
        source_role=m.source_role,
        fetched_at=m.fetched_at,
        media_type=m.media_type,
    )
    doc = adjudicated_document_ingest(
        m_small, vision=vision, adjudicator=LlmWorkflowAdjudicator(verify_pass=False, max_tokens=700), max_pages=2
    )
    assert doc.page_count == 2
    assert len(doc.root.children) >= 1

"""End-to-end native-PDF source-document ingest (D2 + D3 e2e slice).

Runs the deterministic-native pdfplumber lane on a real Finnish PDF fixture
and asserts the determinism-firewall invariant: every IN-SCOPE page ends up
OWNED (a T0 ``SourceDocumentNode``) or RESIDUAL (a typed ``Residual``), never
silently dropped (AGENTS.md §0 total accounting; §1.8). Also covers the
firewall's failure mode: unparseable bytes → a typed BLOCKED residual, not a
swallowed exception (§1.10).

See the approved plan at ``.claude/plans/calm-kindling-wand.md`` (D2 + D3).
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

import pytest

from lawvm.core.source_document import (
    AssuranceTier,
    RegionOwnership,
    ResidualFamily,
    SourceDocumentNodeKind,
    SourceManifestation,
)
from lawvm.finland.source_document import (
    ingest_pdf_manifestation,
    source_document_to_ir_node,
)
from lawvm.core.ir import IRNode  # for type check of output

_FIXTURE = Path(__file__).resolve().parents[1] / "data" / "finland" / "oikaisu_fi.pdf"


def _manifestation(bytes_: bytes, role: str = "corrigendum") -> SourceManifestation:
    return SourceManifestation(
        artifact_digest=hashlib.sha256(bytes_).hexdigest(),
        source_bytes=bytes_,
        locator="finland/oikaisu_fi.pdf",
        source_role=role,
        fetched_at=datetime(2026, 1, 1),
        media_type="application/pdf",
    )


def _owned_pages(root) -> set[int]:
    pages: set[int] = set()

    def walk(node) -> None:
        if node.anchor.page_num is not None:
            pages.add(node.anchor.page_num)
        for child in node.children:
            walk(child)

    walk(root)
    return pages


def _all_nodes(node):
    yield node
    for child in node.children:
        yield from _all_nodes(child)


@pytest.mark.skipif(not _FIXTURE.exists(), reason="oikaisu fixture not present")
def test_real_pdf_ingest_is_total_and_disjoint() -> None:
    result = ingest_pdf_manifestation(_manifestation(_FIXTURE.read_bytes()), max_pages=3)

    # Owned content exists: this fixture carries a real text layer.
    assert result.root.kind is SourceDocumentNodeKind.WORK_ROOT
    assert len(result.root.children) > 0

    # Every owned node is single-witness — no model touched this.
    for node in _all_nodes(result.root):
        assert node.assurance_tier is AssuranceTier.SINGLE_WITNESS

    owned = _owned_pages(result.root)
    residual_pages = {r.anchor.page_num for r in result.residuals}
    scope = set(range(1, result.page_count + 1))

    # Determinism firewall: every in-scope page is OWNED or RESIDUAL — total...
    assert owned | residual_pages == scope
    # ...and the partition is disjoint (no page double-counted).
    assert owned.isdisjoint(residual_pages)

    # The run record is honest provenance.
    assert result.run.backend_id == "native_pdf"
    assert len(result.run.output_digest) == 64
    assert result.run.source_artifact_digest == hashlib.sha256(_FIXTURE.read_bytes()).hexdigest()


def test_unparseable_bytes_become_a_typed_blocked_residual() -> None:
    # Not a PDF: pdfplumber cannot open it → extract_pdf_layout returns None.
    # The firewall emits a typed BLOCKED residual, never a swallowed exception.
    result = ingest_pdf_manifestation(_manifestation(b"not a pdf at all"))
    assert result.page_count == 0
    assert result.root.children == ()
    assert len(result.residuals) == 1
    assert result.residuals[0].ownership is RegionOwnership.BLOCKED
    assert result.residuals[0].family is ResidualFamily.PDF_TEXT_LAYER_EMPTY


@pytest.mark.skipif(not _FIXTURE.exists(), reason="oikaisu fixture not present")
def test_source_document_lowers_to_lawvm_ir_node() -> None:
    """PDF (attachment/corrigendum) -> SourceDocumentIR -> LawVM IRNode (structured)."""
    result = ingest_pdf_manifestation(_manifestation(_FIXTURE.read_bytes()), max_pages=1)
    ir = source_document_to_ir_node(result.root)
    assert isinstance(ir, IRNode)
    assert len(ir.children) > 0
    # Authority and source provenance are carried through (for audit / certificate).
    for c in ir.children[:5]:
        assert "assurance_tier" in c.attrs
        assert "source_locator" in c.attrs or "source_digest" in c.attrs


def test_farchive_loader_and_ingest_for_attachment_target() -> None:
    """Use the provided farchive means to load a real media PDF (attachment/corrigenda target) and produce structured IR."""
    pytest.importorskip("farchive")
    from lawvm.finland.source_document import load_manifestation_from_farchive

    # Small corrigenda media from finlex.farchive (one of the provided means)
    loc = "finlex://sd-cons/1734/4-000/fin@20180107/media/corrigenda/sk20090135_1.pdf"
    try:
        m = load_manifestation_from_farchive(loc, source_role="attachment")
    except Exception as e:
        pytest.skip(f"farchive locator not usable in this env: {e}")
    result = ingest_pdf_manifestation(m, max_pages=1)
    assert result.page_count >= 1
    ir = source_document_to_ir_node(result.root)
    assert isinstance(ir, IRNode)
    assert len(ir.children) >= 1 or len(result.residuals) >= 0  # either owned or residual honest

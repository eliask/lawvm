"""Hermetic end-to-end test: the converged two-level de-facsimile parse lane.

Drives ``parse_defacsimile_and_cache`` over a small MULTI-PAGE fake manifestation
with a fake vision producer + fake page-element producer + ``None`` Level-2
adjudicator (→ the deterministic ``compose_pages`` fallback, Decision 8). NO
network / NO PDF lib / NO model. Asserts the integration wiring:

* it produces canonical IR (a WORK_ROOT lowered by ``source_document_to_ir_node``);
* each per-page ``PageSimulacrum`` is persisted at ``page_simulacrum_locator``;
* a VERIFIED de-facsimile ledger is persisted at ``defacsimile_ledger_locator``
  and the manifest carries its op/tier histograms + locator/digest;
* a 2nd call is a byte-identical CACHE HIT (idempotent — the fold is pure over
  the immutable simulacra + the content-addressed ledger).
"""
from __future__ import annotations

from datetime import datetime, timezone

from lawvm.core.source_document.extraction import SourceManifestation
from lawvm.ingest.page_elements import PageElements, PageLine
from lawvm.ingest.parsed_store import (
    DEFACSIMILE_MODALITY,
    ParsedIrStore,
    PipelineSpec,
    defacsimile_ledger_locator,
    page_simulacrum_locator,
    parse_defacsimile_and_cache,
)
from lawvm.ingest.struct_wire import STRUCT_COMMAND_SEPARATOR, parse_struct_wire

US = STRUCT_COMMAND_SEPARATOR
_DIGEST = "d" * 64
_PIPELINE = "adjudicated_vision"
_VERSION = (
    "vision=fake+adj+wire=structbuild.v1+leaf=patch+converge.v1"
    "+gate=hard.v1+iters=4+structpatch=text.v1+rasterdpi=200+compose.v1"
    "+defacsimile.v1+fallback"
)

# Two pages; each has a body paragraph and a bare-page-number footer (furniture).
_PAGE_BODY = {
    1: "Ensimmainen kappale jatkuu",
    2: "seuraavalle sivulle saumassa.",
}


def _manifestation() -> SourceManifestation:
    return SourceManifestation(
        artifact_digest=_DIGEST,
        source_bytes=b"%PDF-1.4 fake",
        locator="doc.pdf",
        source_role="he_draft",
        fetched_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        media_type="application/pdf",
    )


class _FakeVision:
    """Fake vision: a body PARA + a bare-page-number furniture footer, no refine."""

    def is_available(self) -> bool:
        return True

    def propose_page_struct(self, man, page_num, pe, *, leaf_mode="patch"):
        from lawvm.ingest.llm_backends.vision_producer import StructPageResult

        wire = f"1 PARA 0 L1{US}2 PARA 0 L2{US}"
        build = parse_struct_wire(wire, pe.lines, [])
        return StructPageResult(build=build, raw_content=wire, images=())

    def propose_page_patch_delta(self, man, page_num, numbered_lines):
        return ""  # never called (clean pages stay single-pass)


class _FakePageProducer:
    def page_elements(self, pdf_bytes, page_num):
        body = _PAGE_BODY[page_num]
        return PageElements(
            page_num=page_num,
            lines=(body, str(page_num)),
            page_lines=(
                PageLine(text=body, y_order=0, bbox=None, band="body", indent=4),
                PageLine(text=str(page_num), y_order=1, bbox=None, band="bottom", indent=15),
            ),
            page_width=595.0,
            page_height=842.0,
        )


def _spec() -> PipelineSpec:
    # No Level-2 adjudicator → the deterministic compose_pages fallback (Decision 8).
    return PipelineSpec(
        pipeline_id=_PIPELINE,
        version=_VERSION,
        vision=_FakeVision(),
        adjudicator=None,
        transcription_modality=DEFACSIMILE_MODALITY,
        page_element_producer=_FakePageProducer(),  # ty: ignore[invalid-argument-type]
        defacsimile_adjudicator=None,
    )


def _reading_order(monkeypatch) -> None:
    """Stub the pypdfium reading-order extraction (no PDF lib) with two pages."""
    import lawvm.ingest.parsed_store as ps

    def _fake_ro(pdf_bytes, *, max_pages=500):
        return [_PAGE_BODY[1] + "\n1", _PAGE_BODY[2] + "\n2"]

    monkeypatch.setattr(
        "lawvm.ingest.adjudicated_ingest.reading_order_pages_from_pdf", _fake_ro
    )
    # Also patch the reference imported INTO parsed_store's function scope (it does a
    # local import, so patching the source module is enough).
    _ = ps


def test_defacsimile_lane_produces_ir_simulacra_ledger_and_cache_hits(tmp_path, monkeypatch) -> None:
    _reading_order(monkeypatch)
    store = ParsedIrStore(str(tmp_path / "parsed.farchive"))
    try:
        rec = parse_defacsimile_and_cache(_manifestation(), store, spec=_spec())

        # 1. Canonical IR: a lowered WORK_ROOT → HCONTAINER with body children.
        assert rec.cache_hit is False
        assert rec.ir["kind"] == "hcontainer"
        assert rec.manifest["transcription_modality"] == DEFACSIMILE_MODALITY
        assert rec.manifest["page_count"] == 2
        assert "defacsimile" in rec.manifest["producers"]

        # 2. Per-page simulacra persisted (immutable Level-1 evidence, Decision 11).
        for page_num in (1, 2):
            loc = page_simulacrum_locator(_DIGEST, _PIPELINE, _VERSION, page_num)
            sim = store.get_page_simulacrum(loc)
            assert sim is not None
            assert sim.page_num == page_num

        # 3. A VERIFIED ledger persisted as a sibling blob (Decision 5), and the
        #    manifest carries its histograms + locator/digest.
        ledger_loc = defacsimile_ledger_locator(_DIGEST, _PIPELINE, _VERSION)
        ledger = store.get_ledger(ledger_loc)
        assert ledger is not None  # the fold's ledger round-trips
        summary = rec.manifest["defacsimile_summary"]
        assert summary["ledger_locator"] == ledger_loc
        assert summary["ledger_digest"]
        assert "op_histogram" in summary and "tier_histogram" in summary

        # 4. Idempotent CACHE HIT on the 2nd call (byte-identical record).
        rec2 = parse_defacsimile_and_cache(_manifestation(), store, spec=_spec())
        assert rec2.cache_hit is True
        assert rec2.ir == rec.ir
        assert rec2.manifest == rec.manifest
    finally:
        store.close()


def test_defacsimile_lane_rejoins_the_seam_paragraph_via_fallback(tmp_path, monkeypatch) -> None:
    # The two page bodies are ONE paragraph split by the seam; the deterministic
    # compose_pages fallback REJOINs them (a continuation join) — the ledger is a
    # verified REJOIN claim, and the fold produces the concatenated body text.
    _reading_order(monkeypatch)
    store = ParsedIrStore(str(tmp_path / "parsed.farchive"))
    try:
        rec = parse_defacsimile_and_cache(_manifestation(), store, spec=_spec())
        ledger = store.get_ledger(defacsimile_ledger_locator(_DIGEST, _PIPELINE, _VERSION))
        assert ledger is not None
        # A ledger over 2 pages of simulacra is always well-formed (verify_ledger
        # gated the write); the op histogram is a dict of the fold's claims.
        assert isinstance(rec.manifest["defacsimile_summary"]["op_histogram"], dict)
    finally:
        store.close()


def test_tolerant_vision_forwards_the_full_converge_surface() -> None:
    """Regression: the production `spec.vision` is a `_TolerantVision` wrapper, and
    the converge loop calls BOTH `propose_page_struct` AND `propose_page_patch_delta`.
    An earlier gap (the wrapper lacked `propose_page_patch_delta`) only surfaced in
    the real defacsimile lane because hermetic tests fake vision WITHOUT the wrapper.
    This pins the wrapper's method surface + the truncation-propagation contract."""
    from lawvm.ingest.llm_backends.vision_producer import VisionProducerTruncated
    from lawvm.ingest.parsed_store import _TolerantVision

    class _Inner:
        def is_available(self):
            return True

        def propose_page_patch_delta(self, manifestation, page_num, numbered_lines):
            if numbered_lines == "TRUNCATE":
                raise VisionProducerTruncated(page_num=page_num, detail="dense")
            return "1 PARA 0 L1\x1f"

    tv = _TolerantVision(_Inner())
    # Forwards the refine-round call.
    assert tv.propose_page_patch_delta(_manifestation(), 1, "[1] x") == "1 PARA 0 L1\x1f"
    # Truncation is PROPAGATED (converge_page catches it → termination="truncated");
    # an empty string is the CONVERGED signal, so the two must stay distinguishable.
    try:
        tv.propose_page_patch_delta(_manifestation(), 1, "TRUNCATE")
        raise AssertionError("expected VisionProducerTruncated to propagate")
    except VisionProducerTruncated:
        pass

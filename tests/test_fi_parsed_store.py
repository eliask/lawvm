"""Derived-IR store: content-addressed cache of LawVM IR parsed from source PDFs.

Hermetic tests exercise the key scheme, serialization round-trip, assurance
summary, and the miss→hit cache logic with a fake store + monkeypatched parser
(no farchive, no PDF lib). The real finlex-attachment path is exercised by
``lawvm fi-parse-attachments`` against the corpus.
"""
from __future__ import annotations

from datetime import datetime

from lawvm.core.source_document import (
    AssuranceTier,
    SourceAnchor,
    SourceDocumentNode,
    SourceDocumentNodeKind,
)
from lawvm.core.source_document.extraction import SourceManifestation
from lawvm.finland.source_document import parsed_store as ps
from lawvm.finland.source_document.parsed_store import (
    ParseBackendUnavailable,
    ParsedRecord,
    PipelineSpec,
    _serialize_parsed_record,
    parse_struct_and_cache,
    parsed_ir_locator,
    resolve_pipeline,
)
from lawvm.tools.fi_parse_attachments import _classify

_DIGEST = "a" * 64
_SPEC = PipelineSpec(
    pipeline_id="test", version="v0", vision=None, adjudicator=None,
    transcription_modality="struct_span",
)


def _manifestation() -> SourceManifestation:
    return SourceManifestation(
        artifact_digest=_DIGEST,
        source_bytes=b"%PDF",
        locator="finlex://sd-cons/1/media/corrigenda/x.pdf",
        source_role="corrigendum",
        fetched_at=datetime(2026, 1, 1),
        media_type="application/pdf",
    )


def test_locator_is_content_addressed_by_source_and_pipeline() -> None:
    a = parsed_ir_locator(_DIGEST, "native_pdf", "v1")
    assert a == f"parsed/{_DIGEST}/native_pdf@v1"
    # Different pipeline / version → different key (versioned, no collision).
    assert parsed_ir_locator(_DIGEST, "native_pdf", "v2") != a
    assert parsed_ir_locator(_DIGEST, "adjudicated_vision", "v1") != a


def test_serialize_roundtrip_is_deterministic() -> None:
    ir = {"kind": "hcontainer", "children": [{"kind": "p", "text": "x"}]}
    manifest = {"source_digest": _DIGEST, "pipeline_id": "native_pdf"}
    b1 = _serialize_parsed_record(ir, manifest)
    b2 = _serialize_parsed_record(dict(ir), dict(manifest))
    assert b1 == b2  # sorted keys → stable bytes
    import json

    assert json.loads(b1)["ir"]["kind"] == "hcontainer"


def test_assurance_summary_histograms_tiers() -> None:
    anchor = SourceAnchor(artifact_digest=_DIGEST, locator="p")
    root = SourceDocumentNode(
        kind=SourceDocumentNodeKind.WORK_ROOT,
        assurance_tier=AssuranceTier.SINGLE_WITNESS,
        anchor=anchor,
        children=(
            SourceDocumentNode(kind=SourceDocumentNodeKind.PARAGRAPH, assurance_tier=AssuranceTier.SINGLE_WITNESS, anchor=anchor),
            SourceDocumentNode(kind=SourceDocumentNodeKind.PARAGRAPH, assurance_tier=AssuranceTier.MULTI_WITNESS_ADJUDICATED, anchor=anchor),
        ),
    )
    summary = ps._assurance_summary(root)
    assert summary["SINGLE_WITNESS"] == 2  # root + 1 child
    assert summary["MULTI_WITNESS_ADJUDICATED"] == 1


class _FakeStore:
    def __init__(self) -> None:
        self._d: dict[str, ParsedRecord] = {}

    def get(self, loc):
        r = self._d.get(loc)
        return None if r is None else {"ir": r.ir, "manifest": r.manifest}

    def put(self, loc, record):
        self._d[loc] = record
        return "digest"


def test_parse_struct_and_cache_miss_then_hit(monkeypatch) -> None:
    calls = {"n": 0}

    def _fake_parse(manifestation, spec, store, *, max_pages=5000, parsed_at=None):
        calls["n"] += 1
        return ParsedRecord(ir={"kind": "hcontainer"}, manifest={"source_digest": manifestation.artifact_digest}, cache_hit=False)

    monkeypatch.setattr(ps, "parse_struct_pdf_to_ir", _fake_parse)
    store = _FakeStore()
    r1 = parse_struct_and_cache(_manifestation(), store, spec=_SPEC)  # ty: ignore[invalid-argument-type]
    assert r1.cache_hit is False and calls["n"] == 1
    r2 = parse_struct_and_cache(_manifestation(), store, spec=_SPEC)
    assert r2.cache_hit is True and calls["n"] == 1  # not re-parsed
    assert r2.ir == r1.ir


def test_force_reparses_even_on_hit(monkeypatch) -> None:
    calls = {"n": 0}

    def _fake_parse(manifestation, spec, store, *, max_pages=5000, parsed_at=None):
        calls["n"] += 1
        return ParsedRecord(ir={"kind": "hcontainer"}, manifest={}, cache_hit=False)

    monkeypatch.setattr(ps, "parse_struct_pdf_to_ir", _fake_parse)
    store = _FakeStore()
    parse_struct_and_cache(_manifestation(), store, spec=_SPEC)  # ty: ignore[invalid-argument-type]
    parse_struct_and_cache(_manifestation(), store, spec=_SPEC, force=True)  # ty: ignore[invalid-argument-type]
    assert calls["n"] == 2


def test_resolve_pipeline_fails_loud_when_backend_down(monkeypatch) -> None:
    # No deterministic fallback: an unreachable LLM backend is a fail-loud error.
    import pytest

    from lawvm.finland.llm_backends import llm_adjudicator as la
    from lawvm.finland.llm_backends import vision_producer as vp

    monkeypatch.setattr(vp.VisionPageProducer, "is_available", lambda self: False)
    monkeypatch.setattr(la.LlmWorkflowAdjudicator, "is_available", lambda self: False)
    with pytest.raises(ParseBackendUnavailable):
        resolve_pipeline()


def test_classify_corrigendum_vs_attachment() -> None:
    assert _classify("finlex://sd-cons/1/media/corrigenda/x.pdf") == "corrigendum"
    assert _classify("finlex://sd-cons/1/media/img.pdf") == "attachment"

"""Span-copy transcription modality — structure from the model, text from the page.

Hermetic tests drive fake transports (``_chat_spans`` / ``_post_chat`` /
``_render_page_png`` overridden; no network, no PDF lib, no model): the model's
``KIND N-M`` span lines are span-copied from the numbered reading-order lines BY
CODE, ``TRANSCRIBE:`` passes literal text through, ``REPLACE N:`` corrects a
misread line at its address, and the ``auto`` per-page heuristic routes
text-native pages to span-copy and scanned pages to full transcription. One
env+network test compares span vs full modality on a real draft-HE PDF.
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

import pytest

import lawvm.finland.source_document.adjudicated_ingest as ai
from lawvm.core.source_document import (
    AssuranceTier,
    ExtractionAssertion,
    SourceDocumentNodeKind,
    SourceManifestation,
)
from lawvm.finland.llm_backends.vision_producer import (
    SPAN_COMMAND_SEPARATOR,
    VisionPageProducer,
    VisionProducerTruncated,
    _parse_span_blocks,
    _parse_span_ref,
    render_span_wire_for_debug,
)
from lawvm.finland.source_document.adjudicated_ingest import (
    SPAN_COPY_MIN_CHARS,
    adjudicated_document_ingest,
    resolve_page_modality,
)
from lawvm.finland.source_document.parsed_store import _TolerantVision

_HE_PDF = Path(os.environ.get("LAWVM_HE_SAMPLE_PDF") or "/nonexistent/no-he-sample.pdf")

_LINES = (
    "4 §",
    "Sen lisäksi, mitä 1 momentissa säädetään, hakijalle palautetaan",
    "valmisteveroa 4 senttiä litralta.",
    "1) Sovelletaan verovuodesta 2025.",
    "12",
)


def _manifestation() -> SourceManifestation:
    return SourceManifestation(
        artifact_digest="a" * 64,
        source_bytes=b"%PDF-1.4",
        locator="doc.pdf",
        source_role="he_draft",
        fetched_at=datetime(2026, 1, 1),
        media_type="application/pdf",
    )


# --------------------------------------------------------------------------- #
# Span reference + span-block parsing (pure, serverless)                       #
# --------------------------------------------------------------------------- #


def test_parse_span_ref_single_range_and_malformed() -> None:
    assert _parse_span_ref("7") == (7, 7)
    assert _parse_span_ref("2-5") == (2, 5)
    assert _parse_span_ref("5-2") is None  # reversed range is malformed, not fixed
    assert _parse_span_ref("x") is None
    assert _parse_span_ref("2-") is None
    assert _parse_span_ref("-5") is None


def test_span_blocks_copy_the_referenced_lines_verbatim() -> None:
    blocks = _parse_span_blocks("HEADING 1\nPARA 2-3\nFOOTNOTE 4", _LINES)
    assert blocks[0] == ("heading", "4 §")
    # The block text is span-COPIED from the input lines, byte-for-byte.
    assert blocks[1] == ("paragraph", _LINES[1] + "\n" + _LINES[2])
    assert blocks[2] == ("footnote", "1) Sovelletaan verovuodesta 2025.")


def test_span_blocks_drop_out_of_range_ungoverned_and_malformed() -> None:
    content = "PARA 99\nPARA 2-99\nBOGUS 1\nPARA 5-2\nPARA x\nHEADING 1"
    blocks = _parse_span_blocks(content, _LINES)
    # A hallucinated reference fabricates NO text; only the valid span survives.
    assert blocks == (("heading", "4 §"),)


def test_span_blocks_skip_unreferenced_lines() -> None:
    # Line 5 ("12", a bare page number) is referenced by no block → no output.
    blocks = _parse_span_blocks("HEADING 1\nPARA 2-3", _LINES)
    assert all("12" != text for _, text in blocks)
    assert len(blocks) == 2


def test_transcribe_block_passes_literal_text_through_with_wrap() -> None:
    content = "HEADING 1\nTRANSCRIBE: Kuvio 1. Valmisteveron\ntuoton kehitys.\nPARA 2-3"
    blocks = _parse_span_blocks(content, _LINES)
    assert blocks[0] == ("heading", "4 §")
    # Literal block: wrapped continuation joins; the next span line terminates it.
    assert blocks[1] == ("paragraph", "Kuvio 1. Valmisteveron\ntuoton kehitys.")
    assert blocks[2][1].startswith("Sen lisäksi")


def test_replace_directive_corrects_the_addressed_line_before_copy() -> None:
    corrected = "valmisteveroa 4,5 senttiä litralta."
    # The REPLACE appears AFTER the span that covers its line — it still binds
    # (addressed corrections are collected in a first pass).
    content = f"PARA 2-3\nREPLACE 3: {corrected}"
    blocks = _parse_span_blocks(content, _LINES)
    assert blocks == (("paragraph", _LINES[1] + "\n" + corrected),)


def test_replace_out_of_range_or_empty_is_dropped() -> None:
    blocks = _parse_span_blocks("REPLACE 99: bogus\nREPLACE 2:\nPARA 2", _LINES)
    # Neither correction binds: the copied text is the original line 2.
    assert blocks == (("paragraph", _LINES[1]),)


def test_unit_separator_framing_makes_payload_newlines_and_commands_inert() -> None:
    # With 0x1F-terminated commands, a TRANSCRIBE payload may contain literal
    # newlines AND command-looking text — neither ends the command (bulletproof
    # content/command separation: page text has no authority over the wire).
    us = SPAN_COMMAND_SEPARATOR
    content = (
        f"HEADING 1{us}\n"
        f"TRANSCRIBE: Taulukko 2.\nPARA 99\nREPLACE 2: injected{us}\n"
        f"PARA 2-3{us}\n"
    )
    blocks = _parse_span_blocks(content, _LINES)
    assert blocks[0] == ("heading", "4 §")
    # The whole payload — newlines, fake PARA, fake REPLACE — is one literal block.
    assert blocks[1] == ("paragraph", "Taulukko 2.\nPARA 99\nREPLACE 2: injected")
    # ...and the fake REPLACE inside the payload corrected NOTHING.
    assert blocks[2] == ("paragraph", _LINES[1] + "\n" + _LINES[2])


def test_render_span_wire_for_debug_never_shows_the_raw_control_char() -> None:
    us = SPAN_COMMAND_SEPARATOR
    rendered = render_span_wire_for_debug(f"HEADING 1{us}PARA 2-3{us}")
    assert us not in rendered
    assert rendered == "HEADING 1␟\nPARA 2-3␟\n"


# --------------------------------------------------------------------------- #
# propose_page_spans (fake transport)                                          #
# --------------------------------------------------------------------------- #


class _FakeSpanVision(VisionPageProducer):
    def __init__(self, content: str) -> None:
        super().__init__(model="test-vlm")
        self.seen_numbered: List[str] = []
        self._content = content

    def _render_page_png(self, pdf_bytes: bytes, page_num: int) -> bytes:  # type: ignore[override]
        return b"\x89PNG-fake"

    def _chat_spans(self, png_b64: str, numbered_text: str, *, page_num: int) -> str:  # type: ignore[override]
        self.seen_numbered.append(numbered_text)
        return self._content


def test_propose_page_spans_emits_anchored_span_copied_assertions() -> None:
    vp = _FakeSpanVision("HEADING 1\nPARA 2-3")
    ro_text = "\n".join(_LINES)
    assertions = vp.propose_page_spans(_manifestation(), 7, ro_text)
    assert all(isinstance(a, ExtractionAssertion) for a in assertions)
    assert [a.fragment_kind for a in assertions] == ["heading", "paragraph"]
    assert assertions[1].text == _LINES[1] + "\n" + _LINES[2]
    a = assertions[0]
    assert a.anchor.page_num == 7
    assert a.anchor.locator == "vision:page=7"
    # Modality + model recorded for provenance.
    assert a.run_id.startswith("vision-span@test-vlm:")
    # The reading-order lines went in NUMBERED, 1-indexed, empty lines skipped.
    assert vp.seen_numbered[0].splitlines()[0] == "[1] 4 §"
    assert vp.seen_numbered[0].splitlines()[1].startswith("[2] Sen lisäksi")


def test_propose_page_spans_scrubs_the_wire_separator_from_input_lines() -> None:
    vp = _FakeSpanVision("PARA 1")
    ro_text = f"garbled{SPAN_COMMAND_SEPARATOR}line\nsecond line"
    assertions = vp.propose_page_spans(_manifestation(), 1, ro_text)
    # The 0x1F char can never ride in via the text layer.
    assert SPAN_COMMAND_SEPARATOR not in vp.seen_numbered[0]
    assert assertions[0].text == "garbled line"


def test_chat_spans_budget_is_output_sparse_and_thinking_off() -> None:
    captured: dict = {}

    class _CapturingVision(VisionPageProducer):
        def _post_chat(self, payload, *, page_num: int) -> str:  # type: ignore[override]
            captured.update(payload)
            return "NONE"

    vp = _CapturingVision(model="test-vlm", max_tokens=4096)
    numbered = "\n".join(f"[{i}] line" for i in range(1, 41))
    vp._chat_spans("cGZha2U=", numbered, page_num=1)
    # Output budget scales with the block count (~lines), NOT the page text size.
    assert captured["max_tokens"] == 128 + 8 * 40
    assert captured["temperature"] == 0.0
    assert captured["chat_template_kwargs"] == {"enable_thinking": False}
    # The numbered lines ride the cheap INPUT side.
    user_parts = captured["messages"][1]["content"]
    assert any("[40] line" in p.get("text", "") for p in user_parts if isinstance(p, dict))


def test_tolerant_wrapper_degrades_truncated_span_page_to_no_witness() -> None:
    class _TruncatingInner:
        def is_available(self) -> bool:
            return True

        def propose_page_spans(self, manifestation, page_num, reading_order_text):
            raise VisionProducerTruncated(page_num=page_num, detail="length")

    wrapped = _TolerantVision(_TruncatingInner())
    assert wrapped.propose_page_spans(_manifestation(), 3, "text") == ()


# --------------------------------------------------------------------------- #
# Per-page modality resolution (the `auto` heuristic) + ingest routing         #
# --------------------------------------------------------------------------- #


def test_resolve_page_modality_auto_routes_by_text_layer_coverage() -> None:
    text_native = "x" * SPAN_COPY_MIN_CHARS
    assert resolve_page_modality("auto", text_native) == "span_copy"
    assert resolve_page_modality("auto", "12") == "full_transcription"  # bare page number
    assert resolve_page_modality("auto", "") == "full_transcription"  # scanned page
    # Explicit lanes: full is always full; span degrades only on NO text layer.
    assert resolve_page_modality("full_transcription", text_native) == "full_transcription"
    assert resolve_page_modality("span_copy", "12") == "span_copy"
    assert resolve_page_modality("span_copy", " \n ") == "full_transcription"
    with pytest.raises(ValueError):
        resolve_page_modality("bogus_lane", text_native)


class _RecordingVision:
    """Records which lane each page was read through."""

    def __init__(self) -> None:
        self.calls: List[Tuple[str, int]] = []

    def is_available(self) -> bool:
        return True

    def _assert(self, page_num: int, kind: str, text: str) -> Tuple[ExtractionAssertion, ...]:
        from lawvm.core.source_document import SourceAnchor

        return (
            ExtractionAssertion(
                run_id=f"vision:{page_num}",
                fragment_kind=kind,
                text=text,
                anchor=SourceAnchor(
                    artifact_digest="a" * 64, locator=f"vision:page={page_num}", page_num=page_num
                ),
            ),
        )

    def propose_page(self, manifestation, page_num) -> Tuple[ExtractionAssertion, ...]:
        self.calls.append(("full_transcription", page_num))
        return self._assert(page_num, "paragraph", f"Full page {page_num}.")

    def propose_page_spans(
        self, manifestation, page_num, reading_order_text
    ) -> Tuple[ExtractionAssertion, ...]:
        self.calls.append(("span_copy", page_num))
        return self._assert(page_num, "paragraph", reading_order_text.splitlines()[0])


def test_ingest_auto_routes_span_for_text_native_and_full_for_scanned(monkeypatch) -> None:
    text_native = "Sen lisäksi, mitä 1 momentissa säädetään. " * 8  # >= threshold
    monkeypatch.setattr(
        ai, "reading_order_pages_from_pdf", lambda b, max_pages=200: [text_native, "12"]
    )
    vision = _RecordingVision()
    doc = adjudicated_document_ingest(_manifestation(), vision=vision, adjudicator=None)
    assert vision.calls == [("span_copy", 1), ("full_transcription", 2)]
    # Same output shape as the full lane: composed paragraphs at a tier.
    paras = [c for c in doc.root.children if c.kind is SourceDocumentNodeKind.PARAGRAPH]
    assert len(paras) >= 1
    assert all(p.assurance_tier is AssuranceTier.SINGLE_WITNESS for p in paras)


def test_ingest_full_modality_never_calls_the_span_lane(monkeypatch) -> None:
    text_native = "Sen lisäksi, mitä 1 momentissa säädetään. " * 8
    monkeypatch.setattr(
        ai, "reading_order_pages_from_pdf", lambda b, max_pages=200: [text_native]
    )
    vision = _RecordingVision()
    adjudicated_document_ingest(
        _manifestation(), vision=vision, adjudicator=None,
        transcription_modality="full_transcription",
    )
    assert vision.calls == [("full_transcription", 1)]


def test_ingest_rejects_unknown_modality_before_any_read(monkeypatch) -> None:
    monkeypatch.setattr(ai, "reading_order_pages_from_pdf", lambda b, max_pages=200: ["t"])
    with pytest.raises(ValueError):
        adjudicated_document_ingest(
            _manifestation(), vision=None, adjudicator=None, transcription_modality="span"
        )


# --------------------------------------------------------------------------- #
# Pipeline spec: modality is a distinct content-addressed cache key            #
# --------------------------------------------------------------------------- #


def test_resolve_pipeline_folds_modality_into_the_version(monkeypatch) -> None:
    from lawvm.finland.llm_backends import llm_adjudicator as la
    from lawvm.finland.llm_backends import vision_producer as vp_mod
    from lawvm.finland.source_document.parsed_store import parsed_ir_locator, resolve_pipeline

    monkeypatch.setattr(vp_mod.VisionPageProducer, "is_available", lambda self: True)
    monkeypatch.setattr(vp_mod.VisionPageProducer, "_resolve_model", lambda self: "test-vlm")
    monkeypatch.setattr(la.LlmWorkflowAdjudicator, "is_available", lambda self: True)

    span = resolve_pipeline(transcription_modality="span_copy")
    auto = resolve_pipeline(transcription_modality="auto")
    full = resolve_pipeline(transcription_modality="full_transcription")
    assert "+modality=span+" in span.version
    assert "+modality=auto+" in auto.version
    # The full lane keeps the legacy (untagged) version → old records stay hits.
    assert "modality" not in full.version
    # The three lanes' records COEXIST: distinct content-addressed keys.
    keys = {parsed_ir_locator("d" * 64, s.pipeline_id, s.version) for s in (span, auto, full)}
    assert len(keys) == 3
    assert span.transcription_modality == "span_copy"
    with pytest.raises(ValueError):
        resolve_pipeline(transcription_modality="bogus")


# --------------------------------------------------------------------------- #
# Live: span vs full modality on a real draft-HE page (env + network)          #
# --------------------------------------------------------------------------- #


@pytest.mark.network
@pytest.mark.skipif(not _HE_PDF.exists(), reason="set LAWVM_HE_SAMPLE_PDF to a draft-HE PDF")
def test_live_span_copy_matches_full_transcription_at_a_fraction_of_output(monkeypatch) -> None:
    import hashlib

    pytest.importorskip("pypdfium2")
    from lawvm.finland.source_document.adjudicated_ingest import reading_order_pages_from_pdf

    vp = VisionPageProducer(max_tokens=2048)
    if not vp.is_available():
        pytest.skip("no llama.cpp server at :8080")
    b = _HE_PDF.read_bytes()
    m = SourceManifestation(
        artifact_digest=hashlib.sha256(b).hexdigest(),
        source_bytes=b,
        locator="he.pdf",
        source_role="he_draft",
        fetched_at=datetime(2026, 5, 20),
        media_type="application/pdf",
    )
    page = 10  # the LAKIEHDOTUS (bill text) page
    ro_text = reading_order_pages_from_pdf(b, max_pages=page)[page - 1]
    assert resolve_page_modality("auto", ro_text) == "span_copy"  # text-native page

    out_chars: List[int] = []
    orig_post = VisionPageProducer._post_chat

    def _counting_post(self, payload, *, page_num):
        content = orig_post(self, payload, page_num=page_num)
        out_chars.append(len(content))
        return content

    monkeypatch.setattr(VisionPageProducer, "_post_chat", _counting_post)
    span_assertions = vp.propose_page_spans(m, page, ro_text)
    full_assertions = vp.propose_page(m, page)
    span_out, full_out = out_chars  # call order: spans first, then full

    span_words = set(" ".join(a.text for a in span_assertions).lower().split())
    full_words = set(" ".join(a.text for a in full_assertions).lower().split())
    assert span_words and full_words
    overlap = len(span_words & full_words) / max(len(span_words | full_words), 1)
    assert overlap >= 0.6  # same page content through both lanes
    assert span_out < full_out / 3  # output-sparse: span response is a fraction of full

"""Vision page producer — compact-line transcription → anchored candidates.

Unit tests drive a fake transport (``_chat`` / ``_render`` overridden) so the
compact-format parse + assertion shape are pinned without a server or a render.
One ``network`` test renders a real page and reads it via the live :8080 model.
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pytest

from lawvm.core.source_document import ExtractionAssertion, SourceManifestation
from lawvm.finland.llm_backends.vision_producer import (
    VisionPageProducer,
    _parse_blocks,
)

# Draft-HE PDF for the live render+read test — via env, never a committed abs
# path or vendored blob. Set LAWVM_HE_SAMPLE_PDF to a local draft to run it.
_HE_PDF = Path(os.environ["LAWVM_HE_SAMPLE_PDF"]) if os.environ.get("LAWVM_HE_SAMPLE_PDF") else None


def _manifestation(bytes_: bytes = b"%PDF-1.4") -> SourceManifestation:
    return SourceManifestation(
        artifact_digest="a" * 64,
        source_bytes=bytes_,
        locator="doc.pdf",
        source_role="he_draft",
        fetched_at=datetime(2026, 1, 1),
        media_type="application/pdf",
    )


class _FakeVision(VisionPageProducer):
    def __init__(self, content: str) -> None:
        super().__init__(model="test-vlm")
        self._content = content

    def _render_page_png(self, pdf_bytes: bytes, page_num: int) -> bytes:  # type: ignore[override]
        return b"\x89PNG-fake"

    def _chat(self, png_b64: str, *, page_num: int) -> str:  # type: ignore[override]
        return self._content


def test_parse_blocks_compact_wrapped_lines() -> None:
    content = (
        "HEADING: 4 §\n"
        "PARA: Sen lisäksi, mitä 1 momentissa säädetään, hakijalle palautetaan\n"
        "valmisteveroa 4 senttiä litralta.\n"
        "PARA: Tämä laki tulee voimaan.\n"
    )
    blocks = _parse_blocks(content)
    assert blocks[0] == ("heading", "4 §")
    # the wrapped continuation line is joined into the same PARA block
    assert blocks[1][0] == "paragraph"
    assert "valmisteveroa 4 senttiä litralta." in blocks[1][1]
    assert blocks[2] == ("paragraph", "Tämä laki tulee voimaan.")


def test_parse_blocks_ignores_legal_colons() -> None:
    # "§:ään" carries a colon but its head is not a governed KIND → no new block.
    blocks = _parse_blocks("PARA: lisätään lain 4 §:ään uusi 5 momentti seuraavasti:")
    assert len(blocks) == 1
    assert "§:ään" in blocks[0][1]


def test_parse_blocks_drops_ungoverned_kind() -> None:
    blocks = _parse_blocks("BOGUS: nonsense\nPARA: real text")
    assert blocks == (("paragraph", "real text"),)


def test_propose_page_emits_anchored_assertions() -> None:
    vp = _FakeVision("HEADING: 4 §\nPARA: body text")
    assertions = vp.propose_page(_manifestation(), 10)
    assert all(isinstance(a, ExtractionAssertion) for a in assertions)
    assert [a.fragment_kind for a in assertions] == ["heading", "paragraph"]
    a = assertions[0]
    assert a.anchor.page_num == 10
    assert a.run_id.startswith("vision@test-vlm:")  # model recorded for provenance
    assert a.anchor.locator == "vision:page=10"


# --------------------------------------------------------------------------- #
# Live: render a real page and read it via the :8080 multimodal model          #
# --------------------------------------------------------------------------- #



@pytest.mark.network
@pytest.mark.skipif(not (_HE_PDF is not None and _HE_PDF.exists()), reason="set LAWVM_HE_SAMPLE_PDF to a draft-HE PDF")
def test_live_vision_reads_the_bill_page() -> None:
    import hashlib

    pytest.importorskip("pypdfium2")
    vp = VisionPageProducer(max_tokens=1024)
    if not vp.is_available():
        pytest.skip("no llama.cpp server at :8080")
    b = _HE_PDF.read_bytes()
    m = SourceManifestation(
        artifact_digest=hashlib.sha256(b).hexdigest(),
        source_bytes=b,
        locator="vm045/he_luonnos.pdf",
        source_role="he_draft",
        fetched_at=datetime(2026, 5, 20),
        media_type="application/pdf",
    )
    # Page 10 is the LAKIEHDOTUS page (bill text).
    assertions = vp.propose_page(m, 10)
    assert len(assertions) >= 1
    joined = " ".join(a.text for a in assertions).lower()
    # The bill page mentions the amended law / provision.
    assert "laki" in joined or "§" in joined

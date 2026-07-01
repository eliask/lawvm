"""Tests for the PDF-spine base-loader fallback (FI PDF spine Phase 1).

Pins :mod:`lawvm.finland.pdf_spine_base` — the hook that lets a statute whose
base ``main.xml`` body is an ``hcontainer``-only metadata wrapper load a
graftable base IR from its attachment PDF's ``N §`` spine (pilot ``2011/38``),
while a substantial XML base is a hard non-fire (never overridden).

Layers:
  1. Pure spine transform (``spine_base_ir_from_pdf_text``): spine-shaped text →
     ``body``-rooted SECTION IR tagged as the PDF-spine lane; non-spine → None.
  2. Substantiality predicate (``base_ir_is_substantial``).
  3. Store hook (``build_pdf_spine_base_ir`` / ``CorpusStore.load_spine_base_ir``)
     with a fake in-memory store: non-substantial base + spine PDF → spine base;
     substantial base → None (backward-compat gate).
  4. Corpus-gated integration on ``2011/38`` — 24 sections addressable by label;
     skips if the corpus / ``pdftotext`` is unavailable.
"""

from __future__ import annotations

import shutil
import textwrap

import pytest

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.tree_ops import build_label_index
from lawvm.finland.pdf_spine_base import (
    BASE_SOURCE_LANE_KEY,
    PDF_SPINE_LANE,
    base_ir_is_substantial,
    build_pdf_spine_base_ir,
    spine_base_ir_from_pdf_text,
)


# A minimal spine-shaped pdftotext extraction (the 2011/38 shape): bare ``N §``
# marks, per-§ Title-case heading, positional body, and numeric ``N)`` kohta.
_SPINE_TEXT = textwrap.dedent(
    """\
    Valtioneuvoston asetus ilmanlaadusta

    1 §
    Tarkoitus
    Tassa asetuksessa saadetaan ilmanlaadusta.

    2 §
    Maaritelmat
    Tassa asetuksessa tarkoitetaan:
    1) ilmalla ulkoilmaa;
    2) epapuhtaudella ainetta.

    3 §
    Voimaantulo
    Tama asetus tulee voimaan.
    """
)

# A non-spine appendix-shaped extraction (Liite + N. paragraphs + a) items):
# carries no bare ``N §`` line, so it must stay in appendix mode (no spine).
_APPENDIX_TEXT = textwrap.dedent(
    """\
    Liite 1

    1. Ensimmainen kohta tekstia.
    a) alakohta yksi
    b) alakohta kaksi

    2. Toinen kohta tekstia.
    """
)


def _iter(node: IRNode):
    yield node
    for child in node.children:
        yield from _iter(child)


def _section_labels(node: IRNode) -> list[str]:
    return [n.label for n in _iter(node) if n.kind is IRNodeKind.SECTION]


# ---------------------------------------------------------------------------
# 1. Pure spine transform
# ---------------------------------------------------------------------------


def test_spine_base_ir_from_spine_text_produces_body_rooted_sections() -> None:
    base = spine_base_ir_from_pdf_text(
        _SPINE_TEXT, source_ref="finlex://sd/2011/38/fin/media/5916.pdf", pdf_name="5916.pdf"
    )
    assert base is not None
    # Body-rooted so the shape matches the ordinary XML-derived base.
    assert base.kind is IRNodeKind.BODY
    assert _section_labels(base) == ["1", "2", "3"]
    # Lower-authority source lane tag + honest provenance.
    assert base.attrs.get(BASE_SOURCE_LANE_KEY) == PDF_SPINE_LANE
    assert base.attrs.get("base_source_pdf") == "5916.pdf"
    assert base.attrs.get("source_ref") == "finlex://sd/2011/38/fin/media/5916.pdf"


def test_spine_base_sections_addressable_by_label() -> None:
    base = spine_base_ir_from_pdf_text(_SPINE_TEXT, pdf_name="5916.pdf")
    assert base is not None
    idx = build_label_index(base)
    # The replay structure graft resolves a section target off this index.
    from lawvm.finland.scoped_section_resolver import section_paths_for_label

    for label in ("1", "2", "3"):
        paths = section_paths_for_label(idx, label)
        assert len(paths) == 1, f"section {label!r} not uniquely resolvable: {paths}"


def test_spine_base_ir_from_appendix_text_returns_none() -> None:
    # A non-spine attachment (no bare ``N §``) must NOT yield a spine base:
    # the recogniser stays in appendix mode and produces no SECTION, so the
    # caller keeps the (non-substantial) XML base untouched — no invented spine.
    assert spine_base_ir_from_pdf_text(_APPENDIX_TEXT, pdf_name="6448.pdf") is None


def test_spine_base_ir_from_empty_text_returns_none() -> None:
    assert spine_base_ir_from_pdf_text("", pdf_name="x.pdf") is None


# ---------------------------------------------------------------------------
# 2. Substantiality predicate
# ---------------------------------------------------------------------------


def _hcontainer_only_base() -> IRNode:
    # Metadata wrapper: body > hcontainer > content, no SECTION/PARAGRAPH.
    return IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.HCONTAINER,
                children=(IRNode(kind=IRNodeKind.CONTENT),),
            ),
        ),
    )


def _substantial_base() -> IRNode:
    return IRNode(
        kind=IRNodeKind.BODY,
        children=(IRNode(kind=IRNodeKind.SECTION, label="1"),),
    )


def test_base_ir_is_substantial_predicate() -> None:
    assert base_ir_is_substantial(_substantial_base()) is True
    assert base_ir_is_substantial(_hcontainer_only_base()) is False
    # A PARAGRAPH-only body is also substantial.
    para_base = IRNode(
        kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.PARAGRAPH, label="1"),)
    )
    assert base_ir_is_substantial(para_base) is True


# ---------------------------------------------------------------------------
# 3. Store hook (fake store) — the load-time gate
# ---------------------------------------------------------------------------


class _FakeStore:
    """Minimal store exposing just the attachment-media read used by the hook."""

    def __init__(self, pdf_text_by_name: dict[str, str]) -> None:
        # Store the *text* keyed by pdf name; pdf_to_text is monkeypatched to
        # return it so no real PDF bytes / pdftotext are needed.
        self._text = pdf_text_by_name
        self.read_media_calls = 0

    def read_attachment_media(self, sid: str, filename: str) -> bytes | None:
        # Return a non-empty sentinel so the hook proceeds to pdf_to_text.
        self.read_media_calls += 1
        return b"%PDF-fake" if filename in self._text else None


_BASE_XML_ONE_PDF = (
    b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
    b"<body><hcontainer name=\"attachments\"><hcontainer name=\"attachment\">"
    b'<content><p><a href="media/5916.pdf">Liitteet</a></p></content>'
    b"</hcontainer></hcontainer></body></akomaNtoso>"
)


def test_build_pdf_spine_base_fires_on_nonsubstantial_base(monkeypatch) -> None:
    store = _FakeStore({"5916.pdf": _SPINE_TEXT})
    monkeypatch.setattr(
        "lawvm.finland.pdf_text.pdf_to_text",
        lambda b, max_pages=5000: store._text.get("5916.pdf"),
    )
    spine = build_pdf_spine_base_ir(
        store, "2011/38", _hcontainer_only_base(), _BASE_XML_ONE_PDF
    )
    assert spine is not None
    assert spine.kind is IRNodeKind.BODY
    assert _section_labels(spine) == ["1", "2", "3"]
    assert spine.attrs.get(BASE_SOURCE_LANE_KEY) == PDF_SPINE_LANE


def test_build_pdf_spine_base_hard_nonfire_on_substantial_base(monkeypatch) -> None:
    # Even with a spine PDF present, a SUBSTANTIAL XML base is never overridden.
    store = _FakeStore({"5916.pdf": _SPINE_TEXT})
    pdf_calls = {"n": 0}

    def _spy_pdf_to_text(b, max_pages=5000):
        pdf_calls["n"] += 1
        return store._text.get("5916.pdf")

    monkeypatch.setattr("lawvm.finland.pdf_text.pdf_to_text", _spy_pdf_to_text)

    spine = build_pdf_spine_base_ir(
        store, "2011/38", _substantial_base(), _BASE_XML_ONE_PDF
    )
    assert spine is None
    # The gate short-circuits BEFORE any PDF fetch/extraction on a substantial
    # base — the common hot path pays nothing.
    assert pdf_calls["n"] == 0
    assert store.read_media_calls == 0


def test_build_pdf_spine_base_none_when_pdf_not_spine(monkeypatch) -> None:
    store = _FakeStore({"5916.pdf": _APPENDIX_TEXT})
    monkeypatch.setattr(
        "lawvm.finland.pdf_text.pdf_to_text",
        lambda b, max_pages=5000: store._text.get("5916.pdf"),
    )
    spine = build_pdf_spine_base_ir(
        store, "2011/38", _hcontainer_only_base(), _BASE_XML_ONE_PDF
    )
    assert spine is None


def test_build_pdf_spine_base_none_when_no_attachment_links() -> None:
    store = _FakeStore({})
    no_pdf_xml = (
        b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
        b"<body><hcontainer name=\"attachments\"><hcontainer name=\"attachment\">"
        b"<content><p>prose only</p></content></hcontainer></hcontainer></body>"
        b"</akomaNtoso>"
    )
    assert (
        build_pdf_spine_base_ir(store, "2011/38", _hcontainer_only_base(), no_pdf_xml)
        is None
    )


# ---------------------------------------------------------------------------
# 4. Corpus-gated integration — pilot 2011/38
# ---------------------------------------------------------------------------


def _corpus_or_skip():
    if shutil.which("pdftotext") is None:
        pytest.skip("pdftotext not installed")
    try:
        from lawvm.corpus_store import get_corpus_store

        return get_corpus_store(readonly=True)
    except Exception as exc:  # pragma: no cover - env-dependent
        pytest.skip(f"corpus unavailable: {exc}")


def test_pilot_2011_38_loads_graftable_spine_base() -> None:
    cs = _corpus_or_skip()
    raw = cs.read_source("2011/38")
    if raw is None:
        pytest.skip("2011/38 not in corpus")
    from lawvm.finland.statute import StatuteContext

    ctx = StatuteContext.from_xml(raw)
    # Base XML body is the hcontainer-only wrapper: no sections yet.
    assert base_ir_is_substantial(ctx.base_ir) is False
    spine = cs.load_spine_base_ir("2011/38", ctx.base_ir, raw)
    assert spine is not None, "2011/38 should materialise a PDF spine base"
    assert spine.attrs.get(BASE_SOURCE_LANE_KEY) == PDF_SPINE_LANE
    labels = _section_labels(spine)
    assert len(labels) == 24, f"expected 24 sections, got {len(labels)}: {labels}"
    # Every 1..24 section is uniquely addressable (replay graft target).
    idx = build_label_index(spine)
    from lawvm.finland.scoped_section_resolver import section_paths_for_label

    for n in range(1, 25):
        paths = section_paths_for_label(idx, str(n))
        assert len(paths) == 1, f"section {n} not uniquely resolvable"


def test_pilot_2011_38_replay_projects_spine_sections() -> None:
    cs = _corpus_or_skip()
    if cs.read_source("2011/38") is None:
        pytest.skip("2011/38 not in corpus")
    from lawvm.finland.replay_entrypoint import replay_xml
    from lawvm.finland.replay_request import ReplayXmlRequest

    res = replay_xml(
        request=ReplayXmlRequest(parent_id="2011/38", quiet=True, corpus=cs)
    )
    # The spine base flowed through the replay fold into the materialized tree.
    assert res.ctx.base_ir.attrs.get(BASE_SOURCE_LANE_KEY) == PDF_SPINE_LANE
    materialized = res.materialized_state.ir
    labels = _section_labels(materialized)
    assert sorted(int(x) for x in labels) == list(range(1, 25))

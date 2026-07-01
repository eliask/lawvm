"""Tests for AKN-XML serialisation of the PDF spine (FI PDF spine Phase 2, Option B).

Phase 1 made the attachment-PDF ``N §`` spine a graftable IRNode base resolved
by ``.label``. Phase 2 serialises that spine to AKN XML with the canonical
Finlex ``part_N__chp_N__sec_N`` eId scheme so the **XML**-based oracle / locator
path (:class:`lawvm.finland.section_resolver.FinnishAKNResolver` +
:mod:`lawvm.tools.oracle_text`) resolves against the PDF-derived base too — not
only the IRNode graft.

Layers:
  1. Pure serialiser (``spine_ir_to_akn_xml_bytes`` / ``spine_ir_to_akn_element``)
     — eId scheme, ``<num>`` heads, round-trip through the resolver (exact eId,
     ``<num>``-text ``resolve_raw``, ``section:N`` / ``chapter:M/section:N``
     locators). Bare-section and chapter-bearing shapes.
  2. Store hook (``CorpusStore.load_spine_base_xml``) with a fake store: fires on
     a non-substantial base with a spine PDF; hard non-fire (None) on a
     substantial base — the backward-compat gate.
  3. Corpus-gated integration: ``2011/38`` (pilot, 24 §) AND a second
     §-structured in-force PDF-only statute (``2008/721``, Metsähallitus fees)
     both serialise to resolver-addressable XML.
"""

from __future__ import annotations

import shutil
import textwrap

import lxml.etree as etree
import pytest

import lawvm.finland.section_resolver  # noqa: F401  — registers the "fi" resolver
from lawvm.core.ir import IRNode
from lawvm.core.locator import get_section_resolver, parse_locator_string
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.pdf_spine_base import (
    PDF_SPINE_LANE,
    spine_base_ir_from_pdf_text,
    spine_eid_of,
    spine_ir_to_akn_element,
    spine_ir_to_akn_xml_bytes,
)


# ---------------------------------------------------------------------------
# Fixtures — spine-shaped pdftotext extractions.
# ---------------------------------------------------------------------------

# Bare-section (chapterless) spine — the 2011/38 shape.
_SPINE_BARE = textwrap.dedent(
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

    24 §
    Voimaantulo
    Tama asetus tulee voimaan.
    """
)

# Chapter-bearing spine — exercises the chp_M__sec_N eId scheme.
_SPINE_CHAPTERS = textwrap.dedent(
    """\
    Testilaki

    1 luku
    Yleiset saannokset

    1 §
    Tarkoitus
    Tama on ensimmainen pykala.

    2 §
    Soveltamisala
    Tata sovelletaan kaikkeen.

    2 luku
    Erityissaannokset

    3 §
    Voimaantulo
    Tama tulee voimaan heti.
    """
)


def _resolver():
    return get_section_resolver("fi")


def _eids(root) -> set[str]:
    return {el.get("eId") for el in root.iter() if el.get("eId")}


def _num(el) -> str:
    """The ``<num>`` text of an AKN element (asserting it is present)."""
    assert el is not None
    num_el = el.find("{*}num")
    assert num_el is not None and num_el.text is not None
    return num_el.text


def _find(root, xpath):
    el = root.find(xpath)
    assert el is not None, f"no element for {xpath!r}"
    return el


# ---------------------------------------------------------------------------
# 1. Pure serialiser — bare-section spine
# ---------------------------------------------------------------------------


def test_bare_spine_serialises_to_sections_with_num_heads() -> None:
    spine = spine_base_ir_from_pdf_text(_SPINE_BARE, pdf_name="5916.pdf")
    assert spine is not None
    root = etree.fromstring(spine_ir_to_akn_xml_bytes(spine))

    secs = root.findall(".//{*}section")
    assert [_num(s) for s in secs] == ["1 §", "2 §", "24 §"]
    # Canonical bare-section eIds.
    assert {"sec_1", "sec_2", "sec_24"} <= _eids(root)


def test_bare_spine_eid_exact_match_resolves() -> None:
    spine = spine_base_ir_from_pdf_text(_SPINE_BARE, pdf_name="5916.pdf")
    assert spine is not None
    root = etree.fromstring(spine_ir_to_akn_xml_bytes(spine))
    # The oracle_text CLI edge matches an exact eId with .//*[@eId=...].
    for eid, num in (("sec_1", "1 §"), ("sec_2", "2 §"), ("sec_24", "24 §")):
        el = root.find(f'.//*[@eId="{eid}"]')
        assert el is not None, f"eId {eid} not found"
        assert _num(el) == num


def test_bare_spine_num_text_fallback_resolves() -> None:
    spine = spine_base_ir_from_pdf_text(_SPINE_BARE, pdf_name="5916.pdf")
    assert spine is not None
    root = etree.fromstring(spine_ir_to_akn_xml_bytes(spine))
    resolver = _resolver()
    # resolve_raw is the bare-label `2 §` num-text path.
    for raw, want_eid in (("2 §", "sec_2"), ("24 §", "sec_24")):
        el = resolver.resolve_raw(root, raw)
        assert el is not None and el.get("eId") == want_eid


def test_bare_spine_section_locator_resolves() -> None:
    spine = spine_base_ir_from_pdf_text(_SPINE_BARE, pdf_name="5916.pdf")
    assert spine is not None
    root = etree.fromstring(spine_ir_to_akn_xml_bytes(spine))
    resolver = _resolver()
    loc = parse_locator_string("section:24")
    el = resolver.resolve(root, loc)
    assert el is not None and el.get("eId") == "sec_24"


def test_bare_spine_subsection_and_item_eids() -> None:
    spine = spine_base_ir_from_pdf_text(_SPINE_BARE, pdf_name="5916.pdf")
    assert spine is not None
    root = etree.fromstring(spine_ir_to_akn_xml_bytes(spine))
    eids = _eids(root)
    # Positional subsection under sec_2, and its numeric kohta points.
    assert "sec_2__subsec_1" in eids
    assert "sec_2__subsec_1__list_1" in eids
    assert "sec_2__subsec_1__list_2" in eids
    # kohta <num> is the `N)` form the resolver's num-text path normalises.
    pts = root.findall(".//{*}point")
    assert [_num(p) for p in pts] == ["1)", "2)"]


# ---------------------------------------------------------------------------
# 2. Pure serialiser — chapter-bearing spine (chp_M__sec_N scheme)
# ---------------------------------------------------------------------------


def test_chapter_spine_nested_eid_scheme() -> None:
    spine = spine_base_ir_from_pdf_text(_SPINE_CHAPTERS, pdf_name="x.pdf")
    assert spine is not None
    root = etree.fromstring(spine_ir_to_akn_xml_bytes(spine))
    eids = _eids(root)
    # Chapters + nested sections in the canonical part_N__chp_N__sec_N grammar.
    assert {"chp_1", "chp_2", "chp_1__sec_1", "chp_1__sec_2", "chp_2__sec_3"} <= eids
    # Chapter num heads.
    chapters = root.findall(".//{*}chapter")
    assert [_num(c) for c in chapters] == ["1 luku", "2 luku"]


def test_chapter_section_locator_resolves() -> None:
    spine = spine_base_ir_from_pdf_text(_SPINE_CHAPTERS, pdf_name="x.pdf")
    assert spine is not None
    root = etree.fromstring(spine_ir_to_akn_xml_bytes(spine))
    resolver = _resolver()
    # chapter:2/section:3 → the eId prefix part; and section:3 suffix-matches.
    el = resolver.resolve(root, parse_locator_string("chapter:2/section:3"))
    assert el is not None and el.get("eId") == "chp_2__sec_3"
    el2 = resolver.resolve(root, parse_locator_string("section:3"))
    assert el2 is not None and el2.get("eId") == "chp_2__sec_3"


def test_serialised_body_carries_lane_provenance() -> None:
    spine = spine_base_ir_from_pdf_text(_SPINE_BARE, pdf_name="5916.pdf")
    assert spine is not None
    body = spine_ir_to_akn_element(spine)
    # The XML view stays honest about being the lower-authority PDF-spine lane.
    assert body.get("data-base-source-lane") == PDF_SPINE_LANE
    assert body.get("data-base-source-pdf") == "5916.pdf"


def test_spine_eid_of_reads_recorded_eid() -> None:
    spine = spine_base_ir_from_pdf_text(_SPINE_CHAPTERS, pdf_name="x.pdf")
    assert spine is not None

    def _it(n: IRNode):
        yield n
        for c in n.children:
            yield from _it(c)

    secs = [n for n in _it(spine) if n.kind is IRNodeKind.SECTION]
    assert spine_eid_of(secs[0]) == "chp_1__sec_1"
    assert spine_eid_of(secs[-1]) == "chp_2__sec_3"


# ---------------------------------------------------------------------------
# 3. Store hook — the load-time gate (fake store)
# ---------------------------------------------------------------------------


class _FakeStore:
    def __init__(self, pdf_text_by_name: dict[str, str]) -> None:
        self._text = pdf_text_by_name
        self.read_media_calls = 0

    def read_attachment_media(self, sid: str, filename: str) -> bytes | None:
        self.read_media_calls += 1
        return b"%PDF-fake" if filename in self._text else None


_BASE_XML_ONE_PDF = (
    b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
    b'<body><hcontainer name="attachments"><hcontainer name="attachment">'
    b'<content><p><a href="media/5916.pdf">Liitteet</a></p></content>'
    b"</hcontainer></hcontainer></body></akomaNtoso>"
)


def _hcontainer_only_base() -> IRNode:
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
    return IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1"),))


def _fake_corpus_store(pdf_text_by_name: dict[str, str]):
    # A real CorpusStore subclass instance is heavy; bind the base-class method
    # onto the light fake so we exercise the actual production hook code.
    from lawvm.corpus_store import CorpusStore

    store = _FakeStore(pdf_text_by_name)
    return store, CorpusStore.load_spine_base_xml


def test_load_spine_base_xml_fires_on_nonsubstantial_base(monkeypatch) -> None:
    store, method = _fake_corpus_store({"5916.pdf": _SPINE_BARE})
    monkeypatch.setattr(
        "lawvm.finland.pdf_text.pdf_to_text",
        lambda b, max_pages=5000: store._text.get("5916.pdf"),
    )
    xml = method(store, "2011/38", _hcontainer_only_base(), _BASE_XML_ONE_PDF)
    assert xml is not None
    root = etree.fromstring(xml)
    assert {"sec_1", "sec_2", "sec_24"} <= _eids(root)
    assert _find(root, ".//{*}body").get("data-base-source-lane") == PDF_SPINE_LANE


def test_load_spine_base_xml_hard_nonfire_on_substantial_base(monkeypatch) -> None:
    store, method = _fake_corpus_store({"5916.pdf": _SPINE_BARE})
    calls = {"n": 0}

    def _spy(b, max_pages=5000):
        calls["n"] += 1
        return store._text.get("5916.pdf")

    monkeypatch.setattr("lawvm.finland.pdf_text.pdf_to_text", _spy)
    xml = method(store, "2011/38", _substantial_base(), _BASE_XML_ONE_PDF)
    # A substantial XML base is NEVER given a synthetic spine view, and the gate
    # short-circuits before any PDF fetch/extraction — byte-identical off-path.
    assert xml is None
    assert calls["n"] == 0
    assert store.read_media_calls == 0


# ---------------------------------------------------------------------------
# 4. Corpus-gated integration — pilot 2011/38 + a second §-structured statute
# ---------------------------------------------------------------------------


def _corpus_or_skip():
    if shutil.which("pdftotext") is None:
        pytest.skip("pdftotext not installed")
    try:
        from lawvm.corpus_store import get_corpus_store

        return get_corpus_store(readonly=True)
    except Exception as exc:  # pragma: no cover - env-dependent
        pytest.skip(f"corpus unavailable: {exc}")


@pytest.mark.parametrize(
    "sid, min_sections, probe_eids",
    [
        ("2011/38", 24, ("sec_1", "sec_2", "sec_24")),
        # 2008/721 (MMM asetus Metsähallituksen … maksuista) — a second in-force
        # PDF-only decree whose attachment PDF is §-structured (Soveltamisala …).
        ("2008/721", 5, ("sec_1", "sec_2")),
    ],
)
def test_pdf_only_statute_serialises_resolver_addressable_xml(
    sid, min_sections, probe_eids
) -> None:
    cs = _corpus_or_skip()
    raw = cs.read_source(sid)
    if raw is None:
        pytest.skip(f"{sid} not in corpus")
    from lawvm.finland.pdf_spine_base import base_ir_is_substantial
    from lawvm.finland.statute import StatuteContext

    ctx = StatuteContext.from_xml(raw)
    # Precondition: the base XML is a non-substantial metadata wrapper.
    assert base_ir_is_substantial(ctx.base_ir) is False

    xml = cs.load_spine_base_xml(sid, ctx.base_ir, raw)
    assert xml is not None, f"{sid} should serialise a PDF spine to XML"
    root = etree.fromstring(xml)

    secs = root.findall(".//{*}section")
    assert len(secs) >= min_sections, f"{sid}: {len(secs)} sections < {min_sections}"
    # Each probed eId resolves via the XML resolver's exact-eId path.
    eids = _eids(root)
    for eid in probe_eids:
        assert eid in eids, f"{sid}: missing {eid} (have {sorted(eids)[:8]}…)"
    # And the num-text fallback resolves a bare `N §` too.
    resolver = _resolver()
    el = resolver.resolve_raw(root, "1 §")
    assert el is not None and el.get("eId") == "sec_1"


def test_substantial_statute_gets_no_spine_xml() -> None:
    # Backward-compat: a normal statute with a real XML body (2002/738 has 44
    # ops + real chapters/sections) is a hard non-fire — no synthetic spine XML.
    cs = _corpus_or_skip()
    raw = cs.read_source("2002/738")
    if raw is None:
        pytest.skip("2002/738 not in corpus")
    from lawvm.finland.pdf_spine_base import base_ir_is_substantial
    from lawvm.finland.statute import StatuteContext

    ctx = StatuteContext.from_xml(raw)
    assert base_ir_is_substantial(ctx.base_ir) is True
    assert cs.load_spine_base_xml("2002/738", ctx.base_ir, raw) is None

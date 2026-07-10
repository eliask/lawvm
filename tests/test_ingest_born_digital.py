"""Deterministic born-digital geom-lane hermetic tests (no backend / PDF lib / model).

Drives ``ingest.born_digital`` on SYNTHETIC ``PageElements`` fixtures whose per-line
geometry (bbox / band / size_class) is hand-built, covering: heading / list / column
/ table-candidate segmentation, text losslessness (span-copy from the text layer),
determinism (two runs byte-identical), the born-digital coverage gate, the
low-confidence table fallback routing to vision, and the OPT-IN ``struct_geom``
fast-path in ``build_page_simulacra`` (a born-digital page never calls the vision
model; a text-poor page still does).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List

from lawvm.core.source_document.anchors import BBox
from lawvm.core.source_document.extraction import SourceManifestation
from lawvm.core.source_document.ir import AssuranceTier, SourceDocumentNodeKind
from lawvm.ingest.born_digital import (
    born_digital_page,
    build_born_digital_simulacra,
    page_is_born_digital,
)
from lawvm.ingest.metadata import decode_metadata
from lawvm.ingest.page_elements import EmbeddedImage, PageElements, PageLine
from lawvm.ingest.page_level import (
    page_simulacrum_from_json,
    page_simulacrum_to_json,
)
from lawvm.ingest.struct_wire import ImageElement

PAGE_H = 800.0
PAGE_W = 500.0


def _man() -> SourceManifestation:
    return SourceManifestation(
        artifact_digest="a" * 64,
        source_bytes=b"%PDF-1.4",
        locator="doc.pdf",
        source_role="he_draft",
        fetched_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        media_type="application/pdf",
    )


def _pl(
    text: str,
    y_order: int,
    *,
    x0: float = 60.0,
    top: float,
    h: float = 12.0,
    w: float = 380.0,
    band: str = "body",
    size_class: str = "body",
    bold: bool = False,
) -> PageLine:
    """A synthetic ``PageLine`` with hand-built geometry (PDF origin bottom-left)."""
    return PageLine(
        text=text,
        y_order=y_order,
        bbox=BBox(x0=x0, y0=top - h, x1=x0 + w, y1=top),
        band=band,
        indent=int(x0 // 18),
        col=None,
        size_class=size_class,
        bold=bold,
    )


def _page(lines: List[PageLine], *, images=()) -> PageElements:
    return PageElements(
        page_num=1,
        lines=tuple(pl.text for pl in lines),
        images=tuple(images),
        page_lines=tuple(lines),
        page_width=PAGE_W,
        page_height=PAGE_H,
    )


def _clean_page() -> PageElements:
    """Heading + §-heading + a wrapped multi-line paragraph + page-number furniture.

    Deliberately dense enough (>300 stripped text-layer chars) to clear the
    born-digital coverage floor — a realistic born-digital body page.
    """
    return _page(
        [
            _pl("LAKI VALMISTEVEROSTA", 0, top=770, band="top"),
            _pl("4 §", 1, top=740),
            _pl("Sen lisäksi, mitä 1 momentissa säädetään, hakijalle", 2, top=720),
            _pl("palautetaan valmisteveroa 4 senttiä litralta sekä", 3, top=706),
            _pl("muita maksuja koskevien säännösten mukaisesti siten,", 4, top=692),
            _pl("että hakemus verohallinnolle tehdään kirjallisesti", 5, top=678),
            _pl("kuuden kuukauden kuluessa maksun suorittamisesta ja", 6, top=664),
            _pl("hakemukseen liitetään tarpeelliset selvitykset asiasta.", 7, top=650),
            _pl("12", 8, top=40, band="bottom"),
        ]
    )


# --------------------------------------------------------------------------- #
# Segmentation.                                                                 #
# --------------------------------------------------------------------------- #


def test_heading_and_paragraph_segmentation() -> None:
    sim = born_digital_page(_man(), 1, _clean_page()).simulacrum
    kinds = [n.kind for n in sim.nodes]
    # caps-heading, §-heading, one merged paragraph (2 wrapped lines), page-number.
    assert kinds[0] is SourceDocumentNodeKind.HEADING  # LAKI VALMISTEVEROSTA
    assert kinds[1] is SourceDocumentNodeKind.HEADING  # 4 §
    assert kinds[2] is SourceDocumentNodeKind.PARAGRAPH
    # The wrapped paragraph merged its two physical lines (continuation cue).
    assert "hakijalle" in sim.nodes[2].text and "litralta" in sim.nodes[2].text
    assert sim.assurance is AssuranceTier.SINGLE_WITNESS


def test_page_number_is_tagged_furniture_kept() -> None:
    sim = born_digital_page(_man(), 1, _clean_page()).simulacrum
    pageno = sim.nodes[-1]
    assert pageno.text.strip() == "12"
    meta = decode_metadata(pageno.attrs)
    assert meta.furniture is True  # kept + tagged, never dropped at Level 1


def test_list_markers_become_item_nodes() -> None:
    page = _page(
        [
            _pl("Tässä laissa tarkoitetaan:", 0, top=760),
            _pl("1) ensimmäinen kohta;", 1, top=740),
            _pl("2) toinen kohta;", 2, top=720),
            _pl("a) alakohta.", 3, top=700),
        ]
    )
    sim = born_digital_page(_man(), 1, page).simulacrum
    items = [n for n in sim.nodes if n.kind is SourceDocumentNodeKind.ITEM]
    assert len(items) == 3
    assert items[0].text.startswith("1)")
    # Each item carries its list marker cue.
    assert decode_metadata(items[0].attrs).list_marker == "1)"


def test_two_column_page_serializes_left_then_right() -> None:
    # Left column x~60, right column x~300 (page width 500); interleaved by y in the
    # raw stream, but the geom lane re-serializes column-major.
    page = _page(
        [
            _pl("Left line one continues", 0, x0=60, top=760),
            _pl("right line one continues", 1, x0=300, top=760),
            _pl("left line two ends here.", 2, x0=60, top=744),
            _pl("right line two ends here.", 3, x0=300, top=744),
        ]
    )
    sim = born_digital_page(_man(), 1, page).simulacrum
    text = "\n".join(n.text for n in sim.nodes)
    # The whole left column precedes the whole right column in reading order.
    assert text.index("Left line one") < text.index("left line two")
    assert text.index("left line two") < text.index("right line one")


def test_numeric_heavy_run_flagged_table_candidate_but_text_kept() -> None:
    page = _page(
        [
            _pl("Taulukko 1", 0, top=760, band="top"),
            _pl("2020 1 000 000 12,5", 1, top=740),
            _pl("2021 2 000 000 13,0", 2, top=724),
            _pl("2022 3 000 000 14,7", 3, top=708),
        ]
    )
    result = born_digital_page(_man(), 1, page)
    # A table-grid fallback is surfaced (routed to vision structure confirm)...
    assert any(fb.reason == "table_grid" for fb in result.fallbacks)
    assert result.simulacrum.convergence.gate_reasons  # geom_fallback:table_grid recorded
    # ...but every numeric byte is still present (text never dropped).
    joined = " ".join(n.text for n in result.simulacrum.nodes)
    for tok in ("1 000 000", "13,0", "3 000 000"):
        assert tok in joined


def test_embedded_image_routes_to_image_region() -> None:
    img = EmbeddedImage(
        element=ImageElement(
            index=1, digest="d" * 64, media_type="image/png", width=100, height=80,
            bbox=(10.0, 400.0, 110.0, 480.0), role="embedded_image",
        ),
        raw_bytes=b"\x89PNG",
        bit_exact_source=True,
    )
    page = _page([_pl("A caption for the figure below.", 0, top=760)], images=[img])
    sim = born_digital_page(_man(), 1, page).simulacrum
    imgs = [n for n in sim.nodes if n.kind is SourceDocumentNodeKind.IMAGE_REGION]
    assert len(imgs) == 1
    assert imgs[0].attrs["image_digest"] == "d" * 64


def test_zero_bbox_images_do_not_trigger_whole_page_encode() -> None:
    """Zero-bbox (no ``get_pos``) image objects must NOT whole-page-encode per object.

    A page with thousands of positionless image XObjects (e.g. ``2005/328`` p.3 has
    ~2,400) previously rendered + PNG-encoded the WHOLE page ONCE PER OBJECT — minutes
    of libpng CPU + hundreds of MB of transient PNGs (the "hang"). The render function
    must be called a BOUNDED number of times (here: zero), not O(N_images).
    """
    from lawvm.ingest.page_elements import PageElementProducer

    render_calls = {"n": 0}

    class _FakeImageObj:
        # FPDF_PAGEOBJ_IMAGE; NO get_pos (→ zero-bbox sentinel), NO get_data/get_bitmap
        # (→ Tier-1 bit-exact extraction yields nothing → falls to the rasterize path).
        type = 3

    class _FakePage:
        def get_objects(self):
            return [_FakeImageObj() for _ in range(2400)]

        def render(self, scale):  # noqa: ARG002 — spy: must never be reached
            render_calls["n"] += 1
            raise AssertionError("whole-page render reached for a zero-bbox image object")

        def get_width(self):
            return 595.0

        def get_height(self):
            return 842.0

    reader = PageElementProducer()
    images, notes = reader._enumerate_images(_FakePage(), 1)
    # Positionless + byteless objects recover NO image — but crucially the render
    # function is called O(1) (zero) times, not once per object.
    assert images == ()
    assert render_calls["n"] == 0
    # each object is a typed "unreadable, skipped" note, not a whole-page PNG
    assert len(notes) == 2400


# --------------------------------------------------------------------------- #
# Text losslessness + provenance + determinism.                                 #
# --------------------------------------------------------------------------- #


def test_text_is_lossless_span_copy_of_the_text_layer() -> None:
    page = _clean_page()
    sim = born_digital_page(_man(), 1, page).simulacrum
    recon_words = " ".join(n.text for n in sim.nodes).split()
    gold_words = " ".join(page.lines).split()
    # Every text-layer word is present in the reconstruction (span-copied, not read).
    assert sorted(recon_words) == sorted(gold_words)


def test_producer_stamp_is_the_geom_lane() -> None:
    sim = born_digital_page(_man(), 1, _clean_page()).simulacrum
    assert decode_metadata(sim.nodes[2].attrs).producer == "born_digital_geom.v1"
    assert sim.convergence.termination == "geom_deterministic"


def test_determinism_two_runs_byte_identical() -> None:
    page = _clean_page()
    a = page_simulacrum_to_json(born_digital_page(_man(), 1, page).simulacrum)
    b = page_simulacrum_to_json(born_digital_page(_man(), 1, page).simulacrum)
    assert a == b
    # And the persistence round-trip is exact.
    rt = page_simulacrum_to_json(page_simulacrum_from_json(a))
    assert rt == a


# --------------------------------------------------------------------------- #
# Coverage gate + non-born-digital fallback.                                    #
# --------------------------------------------------------------------------- #


def test_coverage_gate_rejects_text_poor_and_geometry_less_pages() -> None:
    # A text-poor (scanned) page: few chars → not born-digital.
    scanned = _page([_pl("12", 0, top=40, band="bottom")])
    assert page_is_born_digital(scanned) is False
    # A page with NO line geometry is never born-digital (can't resolve structure).
    no_geom = PageElements(page_num=1, lines=("x" * 400,), page_lines=())
    assert page_is_born_digital(no_geom) is False
    # The clean page clears the floor.
    assert page_is_born_digital(_clean_page()) is True


def test_build_born_digital_simulacra_returns_none_for_non_born_digital() -> None:
    clean = _clean_page()
    scanned = _page([_pl("7", 0, top=40, band="bottom")])
    pages = [clean, scanned]

    class _Producer:
        def page_elements(self, pdf_bytes, page_num):
            return pages[page_num - 1] if page_num <= len(pages) else PageElements(page_num, ())

    out = build_born_digital_simulacra(_man(), _Producer(), page_count=2)
    assert out[0] is not None and out[0].simulacrum.page_num == 1
    assert out[1] is None  # text-poor page → routed to vision by the caller


# --------------------------------------------------------------------------- #
# Opt-in fast-path in build_page_simulacra.                                     #
# --------------------------------------------------------------------------- #


class _ExplodingVision:
    """A vision backend that FAILS if the geom fast-path ever calls it on a clean page."""

    def is_available(self) -> bool:
        return True

    def propose_page_struct(self, man, page_num, pe, *, leaf_mode="patch"):
        raise AssertionError(f"vision called on born-digital page {page_num}")


def test_struct_geom_fast_path_skips_vision_on_born_digital_pages() -> None:
    from lawvm.ingest.page_level import build_page_simulacra

    clean = _clean_page()

    class _Producer:
        def page_elements(self, pdf_bytes, page_num):
            return clean

    sims = build_page_simulacra(
        _ExplodingVision(),
        _man(),
        _Producer(),
        reading_order_pages=["\n".join(clean.lines)],
        struct_geom=True,
    )
    assert len(sims) == 1
    # The geom lane produced it (vision never called → no AssertionError).
    assert decode_metadata(sims[0].nodes[2].attrs).producer == "born_digital_geom.v1"


def test_struct_geom_default_off_uses_vision_lane() -> None:
    # With struct_geom=False (default) the vision lane is used even for a born-digital
    # page — the geom lane NEVER silently replaces vision.
    from lawvm.ingest.page_level import build_page_simulacra
    from lawvm.ingest.struct_wire import STRUCT_COMMAND_SEPARATOR, parse_struct_wire

    clean = _clean_page()
    us = STRUCT_COMMAND_SEPARATOR

    class _Vision:
        def is_available(self) -> bool:
            return True

        def propose_page_struct(self, man, page_num, pe, *, leaf_mode="patch"):
            from lawvm.ingest.llm_backends.vision_producer import StructPageResult

            wire = f"1 PARA 0 L1{us}"
            build = parse_struct_wire(wire, pe.lines, [i.element for i in pe.images])
            return StructPageResult(build=build, raw_content=wire, images=pe.images)

        def propose_page_patch_delta(self, man, page_num, numbered):
            return ""

    class _Producer:
        def page_elements(self, pdf_bytes, page_num):
            return clean

    sims = build_page_simulacra(
        _Vision(),
        _man(),
        _Producer(),
        reading_order_pages=["\n".join(clean.lines)],
        struct_geom=False,
    )
    # Vision producer id, NOT the geom lane.
    prods = {decode_metadata(n.attrs).producer for n in sims[0].nodes if n.text.strip()}
    assert "born_digital_geom.v1" not in prods


# --------------------------------------------------------------------------- #
# A/B harness (hermetic — a fake vision reader).                                #
# --------------------------------------------------------------------------- #


def test_born_digital_ab_geom_is_lossless_and_saves_image_tokens() -> None:
    from lawvm.tools.fi_calibration import born_digital_ab

    page = _clean_page()

    def _garbled_vision_reader(page_num, region, dpi) -> str:
        # The vision lane mis-reads a euro/number token (a NUMERIC failure the geom
        # lane, span-copying the text layer, cannot make).
        return "\n".join(page.lines).replace("4 senttiä", "9 senttiä")

    report = born_digital_ab([page], _garbled_vision_reader, manifestation=_man())
    row = report.rows[0]
    assert row.born_digital is True
    # Geom is NUMERIC-exact (span-copy) while the vision reader corrupted a number.
    assert row.geom_numeric_failures == 0
    assert row.vision_numeric_failures >= 1
    assert row.geom_wer <= row.vision_wer
    # Zero image tokens for the geom lane; the vision lane spends them → a saving.
    assert row.geom_image_tokens == 0 and row.vision_image_tokens > 0
    assert report.total_vision_image_tokens > report.total_geom_image_tokens
    assert report.regressions == ()

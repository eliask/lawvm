"""Level-1 agentic re-read (§8) — suspect surfacing, gated re-read, determinism.

Hermetic: a FAKE vision producer returns a confidently-garbled leaf on the cold
read and a clean string on ``reread_region`` (no network / PDF lib / model). The
shared visual primitive (``render_region_crop``) is exercised separately for its
content-addressed locator + typed-raise contract. Covers:

* suspect detection FIRES on the garble and NOT on clean text (a & d);
* the re-read REPLACES the suspect leaf via the existing patch mechanism (b);
* the plausibility / agreement gate REJECTS a worse re-read (c);
* a fully-clean page does ZERO re-reads (d);
* determinism — same inputs → identical simulacrum, byte-identical JSON (e).
"""
from __future__ import annotations

from datetime import datetime, timezone

from lawvm.core.source_document.anchors import BBox
from lawvm.core.source_document.extraction import SourceManifestation
from lawvm.ingest.page_elements import PageElements, PageLine
from lawvm.ingest.page_level import (
    _detect_suspects,
    build_page_simulacrum,
    converge_page,
    page_simulacrum_from_json,
    page_simulacrum_to_json,
)
from lawvm.ingest.simulacrum import PageSimulacrum
from lawvm.ingest.struct_wire import STRUCT_COMMAND_SEPARATOR, parse_struct_wire
from lawvm.ingest.suspect_region import (
    cross_reader_disagrees,
    lexical_implausibility,
    more_plausible,
    pdfium_region_text,
)
from lawvm.ingest.visual import RegionRenderFailure, region_crop_locator, render_region_crop

US = STRUCT_COMMAND_SEPARATOR

# A real-world garble class (HE 2015/1 p4n11): a confidently-run-together OCR blob
# that is NOT flagged freeform, so it looks clean. This one is composed of
# plausible Finnish syllables run together — LEXICAL signals cannot catch it, so
# it is caught by CROSS-READER DISAGREEMENT (the pdfium text layer reads the same
# region correctly and disagrees), the primary signal.
_GARBLE = "sopimusekertaluonteestisaatavienvakuutusten"
_CLEAN = "sopimuksen ehtojen mukaisesti saatavien vakuutusten"

# A degenerate OCR sludge blob that the CHEAP lexical secondary signal catches on
# its own (implausible bigrams / vowel-degenerate) — no second reader needed.
_SLUDGE = "xqkwzptbvmnfghjklqwrtypmnbvcxzqwrtplkjhgfdsazxcv"


def _manifestation() -> SourceManifestation:
    return SourceManifestation(
        artifact_digest="a" * 64,
        source_bytes=b"%PDF-1.4",
        locator="doc.pdf",
        source_role="government_proposal_draft",
        fetched_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        media_type="application/pdf",
    )


# --------------------------------------------------------------------------- #
# Lexical implausibility + cross-reader signals (pure, no I/O).               #
# --------------------------------------------------------------------------- #


def test_lexical_implausibility_fires_on_sludge_and_not_on_real_text() -> None:
    assert lexical_implausibility(_SLUDGE)  # degenerate OCR sludge fires cheaply
    assert lexical_implausibility(_CLEAN) == ()  # ordinary spaced Finnish does not
    # LENGTH ALONE never fires: a REAL long Finnish compound stays clean (plausible
    # bigrams + normal vowel ratio), even though it is a long space-less run.
    assert lexical_implausibility("epäjärjestelmällistyttämättömyydellänsäkäänköhän") == ()
    assert lexical_implausibility("tarkoituksenmukaisuusharkinta") == ()
    assert lexical_implausibility("Sen lisäksi, mitä 1 momentissa säädetään.") == ()


def test_cross_reader_disagreement_fires_only_on_material_divergence() -> None:
    # Vision garble vs an independent clean read of the same region → disagree.
    assert cross_reader_disagrees(_GARBLE, _CLEAN) is True
    # Identical reads do NOT disagree; an EMPTY independent read never fires.
    assert cross_reader_disagrees(_CLEAN, _CLEAN) is False
    assert cross_reader_disagrees(_GARBLE, "") is False


def test_more_plausible_gate_prefers_the_cleaner_read() -> None:
    # ``more_plausible`` = fewer fired lexical signals. It catches a LEXICALLY
    # implausible incumbent (sludge); a syllable-plausible garble (_GARBLE) scores
    # 0 lexical signals, so it is caught by cross-reader agreement, not this gate.
    assert more_plausible(_CLEAN, _SLUDGE) is True  # clean beats sludge
    assert more_plausible(_SLUDGE, _CLEAN) is False  # a worse re-read is rejected
    assert more_plausible(_SLUDGE, _SLUDGE) is False  # a tie does not replace
    assert more_plausible("", _SLUDGE) is False  # empty is never an improvement
    assert more_plausible(_CLEAN, _GARBLE) is False  # syllable-garble: not this gate


def test_pdfium_region_text_reads_lines_overlapping_the_bbox() -> None:
    lines = (
        PageLine(text="header", y_order=0, bbox=BBox(0, 800, 100, 820), band="top"),
        PageLine(text=_CLEAN, y_order=1, bbox=BBox(72, 400, 500, 420), band="body"),
        PageLine(text="footer", y_order=2, bbox=BBox(0, 20, 100, 34), band="bottom"),
    )
    # A bbox over the body line reads only that line's text (independent read).
    got = pdfium_region_text(BBox(72, 398, 500, 422), lines)
    assert _CLEAN in got and "header" not in got and "footer" not in got
    assert pdfium_region_text(None, lines) == ""


# --------------------------------------------------------------------------- #
# render_region_crop — content-addressed locator + typed raise (no lib).       #
# --------------------------------------------------------------------------- #


def test_region_crop_locator_is_content_addressed_and_stable() -> None:
    b = BBox(10, 20, 110, 70)
    loc1 = region_crop_locator("a" * 64, 3, b, 300)
    loc2 = region_crop_locator("a" * 64, 3, b, 300)
    loc_other_dpi = region_crop_locator("a" * 64, 3, b, 144)
    loc_other_bbox = region_crop_locator("a" * 64, 3, BBox(10, 20, 110, 71), 300)
    assert loc1 == loc2  # identical inputs → identical locator
    assert loc1 != loc_other_dpi and loc1 != loc_other_bbox  # dpi/bbox fold in
    assert loc1.startswith("a" * 64 + ".pdf/0003.img#region=")


def test_render_region_crop_degenerate_bbox_raises_typed() -> None:
    try:
        render_region_crop(_manifestation(), 1, BBox(10, 10, 10, 10), dpi=300)
    except RegionRenderFailure as exc:
        assert exc.reason_code == "region_degenerate_bbox"
    else:  # pragma: no cover
        raise AssertionError("expected RegionRenderFailure on a zero-area bbox")


# --------------------------------------------------------------------------- #
# Fake vision producer: garble on cold read, clean on reread_region.           #
# --------------------------------------------------------------------------- #


class _FakeRereadVision:
    """Cold read emits a garbled leaf; ``reread_region`` returns a scripted string."""

    def __init__(self, round1_wire: str, reread_result: str = _CLEAN):
        self._round1 = round1_wire
        self._reread_result = reread_result
        self.reread_calls: list[tuple] = []

    def is_available(self) -> bool:
        return True

    def propose_page_struct(self, man, page_num, page_elements, *, leaf_mode="patch"):
        from lawvm.ingest.llm_backends.vision_producer import StructPageResult

        build = parse_struct_wire(
            self._round1, page_elements.lines, [i.element for i in page_elements.images]
        )
        return StructPageResult(build=build, raw_content=self._round1, images=page_elements.images)

    def propose_page_patch_delta(self, man, page_num, numbered_lines):
        return ""  # page-level refine converges immediately (empty patch)

    def reread_region(self, man, page_num, bbox, current_text, *, dpi=300):
        self.reread_calls.append((page_num, bbox, current_text, dpi))
        return self._reread_result


def _pe_with_leaf(text: str, *, pdfium_text: str | None = None) -> PageElements:
    """A one-line page (geometry for a bbox).

    ``text`` is the model-facing line; ``pdfium_text`` (defaults to ``text``) is
    the INDEPENDENT pdfium read carried on the ``PageLine`` — set it to the clean
    read to model a garbled vision leaf vs a correct text-layer read (cross-reader
    disagreement)."""
    pl_text = pdfium_text if pdfium_text is not None else text
    return PageElements(
        page_num=1,
        lines=(text,),
        page_lines=(
            PageLine(text=pl_text, y_order=0, bbox=BBox(72, 400, 500, 420), band="body", indent=4),
        ),
        page_width=595.0,
        page_height=842.0,
    )


# --------------------------------------------------------------------------- #
# (a) suspect detection fires on garble, not on clean.                         #
# --------------------------------------------------------------------------- #


def test_detect_suspects_fires_on_garble_via_cross_reader_disagreement() -> None:
    # The model read (node text) is garbled; the pdfium line reads the region
    # correctly → cross-reader disagreement surfaces the suspect (primary signal).
    pe = _pe_with_leaf(_GARBLE, pdfium_text=_CLEAN)
    r = parse_struct_wire(f"1 PARA 0 T: {_GARBLE}{US}", pe.lines, [])
    suspects = _detect_suspects(r.roots, pe)
    assert len(suspects) == 1
    assert "cross_reader_disagreement" in suspects[0].signals
    assert suspects[0].vision_text == _GARBLE
    assert suspects[0].cross_reader == _CLEAN
    assert suspects[0].bbox == BBox(72, 400, 500, 420)


def test_detect_suspects_fires_on_sludge_via_lexical_signal_alone() -> None:
    # No disagreeing reader (the pdfium line carries the same sludge); the cheap
    # lexical secondary signal still fires on the degenerate blob.
    pe = _pe_with_leaf(_SLUDGE)
    r = parse_struct_wire(f"1 PARA 0 T: {_SLUDGE}{US}", pe.lines, [])
    suspects = _detect_suspects(r.roots, pe)
    assert len(suspects) == 1
    assert "low_bigram_plausibility" in suspects[0].signals


def test_detect_suspects_is_empty_on_a_clean_leaf() -> None:
    pe = _pe_with_leaf(_CLEAN)
    r = parse_struct_wire(f"1 PARA 0 L1{US}", pe.lines, [])
    assert _detect_suspects(r.roots, pe) == ()


# --------------------------------------------------------------------------- #
# (b) the re-read replaces the suspect leaf via the patch mechanism.           #
# --------------------------------------------------------------------------- #


def test_reread_replaces_the_suspect_leaf_and_records_the_count() -> None:
    # Garbled vision read (L1 span-copies the garble line); the pdfium layer reads
    # the region correctly → cross-reader disagreement → suspect → re-read.
    pe = _pe_with_leaf(_GARBLE, pdfium_text=_CLEAN)
    ro = _CLEAN  # the reading-order witness carries the correct span
    vision = _FakeRereadVision(f"1 PARA 0 L1{US}", reread_result=_CLEAN)
    cp = converge_page(vision, _manifestation(), 1, pe, reading_order_text=ro)
    # The garble was re-read to the clean string via the gated substitution.
    assert cp.nodes[0].text == _CLEAN
    assert cp.convergence.rereads == 1
    assert "suspect_region" in cp.convergence.gate_reasons
    assert len(vision.reread_calls) == 1
    assert vision.reread_calls[0][2] == _GARBLE  # re-read got the incumbent text


# --------------------------------------------------------------------------- #
# (c) the plausibility / agreement gate rejects a worse re-read.               #
# --------------------------------------------------------------------------- #


def test_reread_gate_rejects_a_worse_reread() -> None:
    pe = _pe_with_leaf(_GARBLE, pdfium_text=_CLEAN)
    ro = _CLEAN
    # The re-read DISAGREES with the clean cross-reader and is not more plausible
    # than the incumbent → the gate REJECTS it (a different wrong run-together blob).
    worse = "toinenaivanyhtasekavajaepuhdaslukukelvotonrimpsu"
    vision = _FakeRereadVision(f"1 PARA 0 L1{US}", reread_result=worse)
    cp = converge_page(vision, _manifestation(), 1, pe, reading_order_text=ro)
    # The suspect fired the gate + a re-read was attempted, but it was REJECTED —
    # the incumbent garble is kept (never a worse read), rereads stays 0.
    assert cp.nodes[0].text == _GARBLE
    assert cp.convergence.rereads == 0
    assert len(vision.reread_calls) == 1  # attempted, then gate-rejected


# --------------------------------------------------------------------------- #
# (d) a fully-clean page does zero re-reads (output-sparse).                    #
# --------------------------------------------------------------------------- #


def test_clean_page_surfaces_no_suspects_and_does_zero_rereads() -> None:
    pe = _pe_with_leaf(_CLEAN)
    ro = _CLEAN
    vision = _FakeRereadVision(f"1 PARA 0 L1{US}", reread_result="should never be used")
    cp = converge_page(vision, _manifestation(), 1, pe, reading_order_text=ro)
    assert cp.convergence.rereads == 0
    assert "suspect_region" not in cp.convergence.gate_reasons
    assert vision.reread_calls == []  # reread_region was never called
    assert cp.nodes[0].text == _CLEAN


# --------------------------------------------------------------------------- #
# (e) determinism — same inputs → identical simulacrum + byte-identical JSON.   #
# --------------------------------------------------------------------------- #


def test_reread_is_deterministic_and_json_round_trips_byte_identically() -> None:
    import json

    pe = _pe_with_leaf(_GARBLE, pdfium_text=_CLEAN)
    ro = _CLEAN

    def _run() -> PageSimulacrum:
        vision = _FakeRereadVision(f"1 PARA 0 L1{US}", reread_result=_CLEAN)
        cp = converge_page(vision, _manifestation(), 1, pe, reading_order_text=ro)
        return build_page_simulacrum(cp, _manifestation(), 1, pe, reading_order_text=ro)

    sim_a = _run()
    sim_b = _run()
    js_a = json.dumps(page_simulacrum_to_json(sim_a), sort_keys=True, ensure_ascii=False)
    js_b = json.dumps(page_simulacrum_to_json(sim_b), sort_keys=True, ensure_ascii=False)
    assert js_a == js_b  # byte-identical across runs
    # Round-trips through the codec exactly, carrying the new ``rereads`` field.
    back = page_simulacrum_from_json(json.loads(js_a))
    assert back.convergence.rereads == 1
    assert back.convergence == sim_a.convergence

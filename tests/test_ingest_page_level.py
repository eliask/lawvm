"""Level-1 (Track B) hermetic tests — freeform wire, geometry, convergence, simulacra.

Drives the per-page faithful-simulacrum machinery WITHOUT a network / PDF lib /
model: a fake vision transport (like the struct-wire tests) returns canned wire
per round. Covers the freeform MATH/VERBATIM wire parse + lowering, per-line
geometry + metadata codec, the convergence gate + termination modes (empty-patch
/ fixpoint / oscillation / max_iters / gated-single-pass) incl. MATH-leaf PATCH
correction, the unwitnessed_content tripwire, furniture-kept-as-hint, and the
PageSimulacrum store round-trip.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import cast

from lawvm.core.source_document.anchors import BBox
from lawvm.core.source_document.extraction import SourceManifestation
from lawvm.core.source_document.ir import AssuranceTier, SourceDocumentNodeKind
from lawvm.ingest.metadata import decode_metadata
from lawvm.ingest.page_elements import (
    PageElements,
    PageLine,
    line_ends_terminal,
    line_has_hyphen_tail,
    line_is_bare_page_number,
    line_list_marker,
    line_section_number,
    numbered_page_text,
)
from lawvm.ingest.page_level import (
    band_recurrence_map,
    build_page_simulacra,
    build_page_simulacrum,
    converge_page,
    page_simulacrum_from_json,
    page_simulacrum_to_json,
)
from lawvm.ingest.simulacrum import PageSimulacrum
from lawvm.ingest.struct_wire import (
    STRUCT_COMMAND_SEPARATOR,
    parse_struct_wire,
)

US = STRUCT_COMMAND_SEPARATOR

_LINES = (
    "4 §",
    "Sen lisäksi, mitä 1 momentissa säädetään, hakijalle",
    "palautetaan valmisteveroa 4 senttiä litralta.",
    "12",
)


def _manifestation() -> SourceManifestation:
    return SourceManifestation(
        artifact_digest="a" * 64,
        source_bytes=b"%PDF-1.4",
        locator="doc.pdf",
        source_role="he_draft",
        fetched_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        media_type="application/pdf",
    )


# --------------------------------------------------------------------------- #
# Freeform wire: MATH / VERBATIM parse + lowering.                             #
# --------------------------------------------------------------------------- #


def test_math_freeform_wire_parses_bbox_reason_and_literal() -> None:
    wire = f"1 MATH 0 V40,300,300,360 #image_baked: E = m c^2{US}"
    r = parse_struct_wire(wire, _LINES)
    assert len(r.roots) == 1
    node = r.roots[0]
    assert node.kind is SourceDocumentNodeKind.MATH_REGION
    assert node.freeform is not None
    assert node.freeform.bbox == (40.0, 300.0, 300.0, 360.0)
    assert node.freeform.reason == "image_baked"
    assert node.text == "E = m c^2"  # the faithful literal
    assert r.findings == ()


def test_verbatim_freeform_defaults_reason_when_omitted() -> None:
    # A bare V-head (no #reason) still keeps the content — defaults to 'ambiguous'.
    wire = f"1 VERBATIM 0 V0,0,10,10: garbled ∑ text{US}"
    r = parse_struct_wire(wire, _LINES)
    node = r.roots[0]
    assert node.kind is SourceDocumentNodeKind.VERBATIM_REGION
    assert node.freeform is not None and node.freeform.reason == "ambiguous"
    assert node.text == "garbled ∑ text"


def test_freeform_malformed_bbox_is_dropped_with_a_finding() -> None:
    r = parse_struct_wire(f"1 MATH 0 Vnotabbox: x{US}2 PARA 0 L1{US}", _LINES)
    assert [n.kind for n in r.roots] == [SourceDocumentNodeKind.PARAGRAPH]
    assert any("malformed freeform" in f for f in r.findings)


def test_freeform_out_of_vocab_reason_is_dropped() -> None:
    r = parse_struct_wire(f"1 VERBATIM 0 V0,0,1,1 #scribbled: x{US}", _LINES)
    assert r.roots == ()
    assert any("out of vocab" in f for f in r.findings)


def test_reason_token_on_non_freeform_kind_is_dropped() -> None:
    # A #reason on a PARA (non-freeform) is malformed → dropped, never applied.
    r = parse_struct_wire(f"1 PARA 0 L1 #image_baked{US}2 PARA 0 L2{US}", _LINES)
    assert [n.text for n in r.roots] == [_LINES[1]]
    assert any("#reason on non-freeform" in f for f in r.findings)


def test_freeform_lowers_to_source_node_with_bbox_anchor_and_reason_attr() -> None:
    from lawvm.ingest.adjudicated_ingest import _struct_node_to_source_node
    from lawvm.core.source_document.anchors import SourceAnchor

    wire = f"1 MATH 0 V40,300,300,360 #image_baked: E=mc^2{US}"
    r = parse_struct_wire(wire, _LINES)
    region = SourceAnchor(artifact_digest="a" * 64, locator="page=3", page_num=3)
    node = _struct_node_to_source_node(
        r.roots[0], AssuranceTier.SINGLE_WITNESS, region, "a" * 64, 3
    )
    assert node.kind is SourceDocumentNodeKind.MATH_REGION
    assert node.text == "E=mc^2"
    assert node.attrs["freeform.reason"] == "image_baked"
    assert node.anchor.bbox == BBox(x0=40.0, y0=300.0, x1=300.0, y1=360.0)
    assert node.anchor.page_num == 3


# --------------------------------------------------------------------------- #
# Per-line geometry + pure string cues.                                        #
# --------------------------------------------------------------------------- #


def test_string_cue_functions_are_pure() -> None:
    assert line_ends_terminal("a completed sentence.") is True
    assert line_ends_terminal("a mid-sentence break") is False
    assert line_has_hyphen_tail("jäsen-") is True
    assert line_has_hyphen_tail("complete") is False
    assert line_list_marker("1) first item") == "1)"
    assert line_list_marker("a) sub item") == "a)"
    assert line_list_marker("(iv) roman") == "(iv)"
    assert line_list_marker("plain text") is None
    assert line_section_number("4 § text") == "4 §"
    assert line_section_number("Article 5 text") == "Article 5"
    assert line_is_bare_page_number("12") is True
    assert line_is_bare_page_number("12 (34)") is True
    assert line_is_bare_page_number("Sen lisäksi") is False


def test_page_line_geometry_bands_and_metadata_codec_round_trip() -> None:
    # A body-band line carries geometry that survives the NodeMetadata codec.
    pe = PageElements(
        page_num=1,
        lines=_LINES,
        page_lines=(
            PageLine(text="4 §", y_order=0, bbox=BBox(0, 780, 40, 800), band="top", indent=0),
            PageLine(
                text="Sen lisäksi, mitä 1 momentissa säädetään, hakijalle",
                y_order=1,
                bbox=BBox(72, 400, 500, 420),
                band="body",
                indent=4,
            ),
        ),
        page_width=595.0,
        page_height=842.0,
    )
    from lawvm.ingest.page_level import _line_index_by_text, _metadata_for_text

    idx = _line_index_by_text(pe.page_lines)
    meta = _metadata_for_text(
        "Sen lisäksi, mitä 1 momentissa säädetään, hakijalle",
        line=idx["Sen lisäksi, mitä 1 momentissa säädetään, hakijalle"],
        y_order=1,
        band_count=None,
        furniture=False,
        freeform_reason=None,
        converged=True,
    )
    assert meta.band == "body"
    assert meta.indent == 4
    assert meta.section_ref is True  # contains 'momentissa'
    assert meta.converged is True
    # Round-trips through the closed-vocab attrs codec.
    from lawvm.ingest.metadata import encode_metadata

    assert decode_metadata(encode_metadata(meta)) == meta


# --------------------------------------------------------------------------- #
# Recurrence pre-pass + furniture hint.                                        #
# --------------------------------------------------------------------------- #


def _pe_with_footer(page_num: int, footer_digit: str) -> PageElements:
    return PageElements(
        page_num=page_num,
        lines=("Chapter body text here.", footer_digit),
        page_lines=(
            PageLine(text="Chapter body text here.", y_order=0, bbox=BBox(72, 400, 500, 420), band="body", indent=4),
            PageLine(text=footer_digit, y_order=1, bbox=BBox(280, 20, 320, 34), band="bottom", indent=15),
        ),
        page_width=595.0,
        page_height=842.0,
    )


def test_band_recurrence_counts_page_number_footer_across_pages() -> None:
    pages = [_pe_with_footer(1, "1"), _pe_with_footer(2, "2"), _pe_with_footer(3, "3")]
    rec = band_recurrence_map(pages)
    # The bare-page-number footer recurs at the bottom band on all 3 pages,
    # counted by band (the digit varies), so the affordance survives 1/2/3.
    assert rec["bottom\x00#pageno"] == 3


# --------------------------------------------------------------------------- #
# Convergence loop: fake vision transport returning canned wire per round.     #
# --------------------------------------------------------------------------- #


class _FakeConvergeVision:
    """Fake vision: round-1 struct wire + a scripted list of refine PATCH deltas."""

    def __init__(self, round1_wire: str, deltas=()):
        self._round1 = round1_wire
        self._deltas = list(deltas)
        self._delta_i = 0
        self.numbered_seen: list[str] = []

    def is_available(self) -> bool:
        return True

    def propose_page_struct(self, man, page_num, page_elements, *, leaf_mode="patch"):
        from lawvm.ingest.llm_backends.vision_producer import StructPageResult

        build = parse_struct_wire(
            self._round1, page_elements.lines, [i.element for i in page_elements.images]
        )
        return StructPageResult(build=build, raw_content=self._round1, images=page_elements.images)

    def propose_page_patch_delta(self, man, page_num, numbered_lines):
        self.numbered_seen.append(numbered_lines)
        if self._delta_i < len(self._deltas):
            d = self._deltas[self._delta_i]
            self._delta_i += 1
            return d
        return ""  # no more deltas → empty (converged)


def _page_elements() -> PageElements:
    return PageElements(page_num=1, lines=_LINES)


class _CorroboratingAdjudicator:
    """Fake adjudicator: the two page reads corroborate → MULTI_WITNESS_ADJUDICATED."""

    adjudicator_id = "fake_adj"

    def adjudicate(self, region, candidates, *, prior=None):
        from lawvm.core.source_document.adjudication import (
            Adjudication,
            AdjudicationMethod,
        )
        from lawvm.core.source_document.ir import SourceDocumentNode, SourceDocumentNodeKind

        node = SourceDocumentNode(
            kind=SourceDocumentNodeKind.PARAGRAPH,
            assurance_tier=AssuranceTier.MULTI_WITNESS_ADJUDICATED,
            anchor=region,
        )
        return Adjudication(
            node=node,
            assurance=AssuranceTier.MULTI_WITNESS_ADJUDICATED,
            method=AdjudicationMethod.MULTI_CANDIDATE_RECONCILED,
            source_candidate_run_ids=tuple(c.run_id for c in candidates),
            corroborating_producers=("vision", "reading_order"),
            adjudicator_id=self.adjudicator_id,
        )


def test_clean_page_stays_single_pass_when_the_gate_does_not_fire() -> None:
    # A clean span-copy page: no freeform, no findings, no patches, terminator 100%,
    # AND the two reads corroborate (MULTI_WITNESS) → the gate fires NOTHING.
    ro = "\n".join(_LINES)
    vision = _FakeConvergeVision(f"1 HEADING 0 L1{US}2 PARA 0 L2-3{US}")
    cp = converge_page(
        vision,
        _manifestation(),
        1,
        _page_elements(),
        reading_order_text=ro,
        adjudicator=_CorroboratingAdjudicator(),
    )
    assert cp.convergence.termination == "gated_single_pass"
    assert cp.convergence.gate_reasons == ()
    assert cp.convergence.rounds == 1
    # No refine round was requested.
    assert vision.numbered_seen == []


def test_gate_fires_on_findings_then_empty_patch_terminates() -> None:
    # A dropped-node finding fires the gate; the first (only) refine returns an
    # empty delta → empty_patch termination after one refine round.
    ro = "\n".join(_LINES)
    vision = _FakeConvergeVision(f"1 PARA 0 L1{US}2 PARA 0 L99{US}", deltas=[""])
    cp = converge_page(vision, _manifestation(), 1, _page_elements(), reading_order_text=ro)
    assert "findings" in cp.convergence.gate_reasons
    assert cp.convergence.termination == "empty_patch"
    assert len(vision.numbered_seen) == 1


def test_gate_fires_on_freeform_region() -> None:
    ro = "\n".join(_LINES)
    vision = _FakeConvergeVision(
        f"1 PARA 0 L1{US}2 MATH 0 V0,0,1,1 #image_baked: x{US}", deltas=[""]
    )
    cp = converge_page(vision, _manifestation(), 1, _page_elements(), reading_order_text=ro)
    assert "freeform_region" in cp.convergence.gate_reasons
    assert len(cp.freeform) == 1 and cp.freeform[0].kind == "math"


def test_math_leaf_patch_correction_applies_across_a_refine_round() -> None:
    # Round 1 has a findings-triggered gate; the refine round PATCHES line 1's text.
    ro = "\n".join(_LINES)
    # Round-1 wire references a wrong line via L99 (finding → gate), plus a real
    # PARA on L1; the refine round corrects L1's text to a fixpoint.
    round1 = f"1 PARA 0 L1{US}2 PARA 0 L99{US}"
    delta = f"1 PATCH 0 L1: 5 §{US}"
    vision = _FakeConvergeVision(round1, deltas=[delta, ""])
    cp = converge_page(vision, _manifestation(), 1, _page_elements(), reading_order_text=ro)
    # The single text leaf was patched to '5 §'.
    assert cp.nodes[0].text == "5 §"
    assert cp.convergence.patches_total >= 1
    assert cp.convergence.termination == "empty_patch"


def test_fixpoint_terminates_when_a_patch_does_not_change_the_tree() -> None:
    # The delta re-states the SAME text (a no-op patch) → resolved-tree hash is
    # unchanged → fixpoint. (An out-of-range / identity patch yields count 0 →
    # here we PATCH to the identical text so count>0 but the tree is unchanged.)
    ro = "\n".join(_LINES)
    round1 = f"1 PARA 0 L1{US}2 PARA 0 L99{US}"
    # Patch line 1 to the SAME value it already has → tree hash unchanged.
    delta = f"1 PATCH 0 L1: 4 §{US}"
    vision = _FakeConvergeVision(round1, deltas=[delta])
    cp = converge_page(vision, _manifestation(), 1, _page_elements(), reading_order_text=ro)
    assert cp.convergence.termination == "fixpoint"


def test_oscillation_is_flagged_and_keeps_last_no_tier_effect() -> None:
    # Deltas ping-pong the leaf text A→B→A; the A re-entry is an earlier-round
    # hash → oscillation, keep-last, flagged.
    ro = "\n".join(_LINES)
    round1 = f"1 PARA 0 L1{US}2 PARA 0 L99{US}"  # leaf text starts '4 §'
    deltas = [f"1 PATCH 0 L1: B{US}", f"1 PATCH 0 L1: 4 §{US}"]  # 4 §→B→4 §
    vision = _FakeConvergeVision(round1, deltas=deltas)
    cp = converge_page(vision, _manifestation(), 1, _page_elements(), reading_order_text=ro)
    assert cp.convergence.termination == "oscillation"
    assert cp.nodes[0].text == "4 §"  # kept the last


def test_max_iters_caps_a_never_converging_page() -> None:
    ro = "\n".join(_LINES)
    round1 = f"1 PARA 0 L1{US}2 PARA 0 L99{US}"
    # Every delta changes the leaf to a fresh value → never repeats, never empty.
    deltas = [f"1 PATCH 0 L1: v{i}{US}" for i in range(10)]
    vision = _FakeConvergeVision(round1, deltas=deltas)
    cp = converge_page(
        vision, _manifestation(), 1, _page_elements(), reading_order_text=ro, max_iters=4
    )
    assert cp.convergence.termination == "max_iters"
    assert cp.convergence.rounds == 5  # 1 cold + 4 refine


def test_truncated_refine_terminates_as_truncated() -> None:
    from lawvm.ingest.llm_backends.vision_producer import VisionProducerTruncated

    ro = "\n".join(_LINES)

    class _TruncVision(_FakeConvergeVision):
        def propose_page_patch_delta(self, man, page_num, numbered_lines):
            raise VisionProducerTruncated(page_num=page_num, detail="len")

    vision = _TruncVision(f"1 PARA 0 L1{US}2 PARA 0 L99{US}")
    cp = converge_page(vision, _manifestation(), 1, _page_elements(), reading_order_text=ro)
    assert cp.convergence.termination == "truncated"


# --------------------------------------------------------------------------- #
# Faithfulness: unwitnessed_content tripwire (Decision 1).                     #
# --------------------------------------------------------------------------- #


def test_unwitnessed_governed_node_is_capped_at_unadjudicated_proposal() -> None:
    # A node whose text is in NEITHER the reading-order witness nor a freeform
    # region is capped at UNADJUDICATED_PROPOSAL (a hallucinated / duplicated node).
    ro = "the witness contains only these exact words"
    # Round-1: one witnessed PARA + one HALLUCINATED PARA (its words absent).
    round1 = f"1 PARA 0 T: these exact words{US}2 PARA 0 T: totally fabricated sentence{US}"
    vision = _FakeConvergeVision(round1, deltas=[""])
    cp = converge_page(vision, _manifestation(), 1, _page_elements(), reading_order_text=ro)
    sim = build_page_simulacrum(
        cp, _manifestation(), 1, _page_elements(), reading_order_text=ro, page_count=1
    )
    tiers = {n.text: n.assurance_tier for n in sim.nodes}
    assert tiers["these exact words"] is AssuranceTier.SINGLE_WITNESS
    assert tiers["totally fabricated sentence"] is AssuranceTier.UNADJUDICATED_PROPOSAL


def test_freeform_region_is_excluded_from_the_witness_tripwire() -> None:
    # A freeform MATH literal absent from the witness is NOT capped (freeform is
    # excluded from text-witness corroboration — default the page tier).
    ro = "ordinary body words only"
    round1 = f"1 MATH 0 V0,0,1,1 #image_baked: E = m c^2{US}"
    vision = _FakeConvergeVision(round1, deltas=[""])
    cp = converge_page(vision, _manifestation(), 1, _page_elements(), reading_order_text=ro)
    sim = build_page_simulacrum(
        cp, _manifestation(), 1, _page_elements(), reading_order_text=ro, page_count=1
    )
    assert sim.nodes[0].assurance_tier is not AssuranceTier.UNADJUDICATED_PROPOSAL


# --------------------------------------------------------------------------- #
# Furniture kept as hint.furniture (§1 reversal / §4).                         #
# --------------------------------------------------------------------------- #


def test_furniture_is_kept_as_a_node_tagged_hint_furniture() -> None:
    ro = "Chapter body text here."
    pe = _pe_with_footer(1, "12")
    # Round-1: the body PARA + a bare-page-number footer node (T: 12).
    round1 = f"1 PARA 0 T: Chapter body text here.{US}2 PARA 0 T: 12{US}"
    vision = _FakeConvergeVision(round1, deltas=[""])
    cp = converge_page(vision, _manifestation(), 1, pe, reading_order_text=ro)
    rec = band_recurrence_map([pe, pe])
    sim = build_page_simulacrum(
        cp, _manifestation(), 1, pe, reading_order_text=ro, recurrence=rec, page_count=2
    )
    footer = next(n for n in sim.nodes if n.text == "12")
    md = decode_metadata(footer.attrs)
    assert md.furniture is True  # tagged, KEPT (not dropped — that's Level 2)
    # And the node itself is still present (not dropped).
    assert any(n.text == "12" for n in sim.nodes)


# --------------------------------------------------------------------------- #
# PageSimulacrum store round-trip (Decision 11).                              #
# --------------------------------------------------------------------------- #


def test_page_simulacrum_json_round_trips_exactly() -> None:
    ro = "\n".join(_LINES)
    vision = _FakeConvergeVision(f"1 HEADING 0 L1{US}2 PARA 0 L2-3{US}")
    cp = converge_page(vision, _manifestation(), 1, _page_elements(), reading_order_text=ro)
    sim = build_page_simulacrum(cp, _manifestation(), 1, _page_elements(), reading_order_text=ro)
    back = page_simulacrum_from_json(page_simulacrum_to_json(sim))
    assert isinstance(back, PageSimulacrum)
    assert back.page_num == sim.page_num
    assert [n.text for n in back.nodes] == [n.text for n in sim.nodes]
    assert back.convergence == sim.convergence
    assert back.assurance == sim.assurance


def test_page_simulacrum_persists_and_reloads_through_the_store(tmp_path) -> None:
    from lawvm.ingest.parsed_store import ParsedIrStore, page_simulacrum_locator

    ro = "\n".join(_LINES)
    vision = _FakeConvergeVision(f"1 HEADING 0 L1{US}2 PARA 0 L2-3{US}")
    cp = converge_page(vision, _manifestation(), 1, _page_elements(), reading_order_text=ro)
    sim = build_page_simulacrum(cp, _manifestation(), 1, _page_elements(), reading_order_text=ro)
    loc = page_simulacrum_locator("a" * 64, "adjudicated_vision", "v1", 1)
    assert loc.endswith("/page/0001")
    store = ParsedIrStore(str(tmp_path / "sim.farchive"))
    try:
        store.put_page_simulacrum(loc, sim, source_digest="a" * 64)
        back = cast(PageSimulacrum, store.get_page_simulacrum(loc))
        assert back is not None
        assert back.page_num == 1
        assert [n.text for n in back.nodes] == [n.text for n in sim.nodes]
    finally:
        store.close()


def test_build_page_simulacra_runs_recurrence_prepass_and_converges_each_page() -> None:
    # End-to-end: two pages through the whole producer (recurrence pre-pass +
    # per-page convergence + simulacrum build).
    class _MultiPageVision:
        def is_available(self) -> bool:
            return True

        def propose_page_struct(self, man, page_num, pe, *, leaf_mode="patch"):
            from lawvm.ingest.llm_backends.vision_producer import StructPageResult

            wire = f"1 PARA 0 L1{US}"
            build = parse_struct_wire(wire, pe.lines, [])
            return StructPageResult(build=build, raw_content=wire, images=())

        def propose_page_patch_delta(self, man, page_num, numbered_lines):
            return ""

    class _FakePageProducer:
        def page_elements(self, pdf_bytes, page_num):
            return _pe_with_footer(page_num, str(page_num))

    sims = build_page_simulacra(
        _MultiPageVision(),
        _manifestation(),
        _FakePageProducer(),
        ["Chapter body text here.", "Chapter body text here."],
    )
    assert len(sims) == 2
    assert all(isinstance(s, PageSimulacrum) for s in sims)
    assert [s.page_num for s in sims] == [1, 2]


def test_numbered_page_text_still_geometry_free_for_the_model() -> None:
    # The model-facing render stays [N] text — geometry is NEVER shown as authority.
    pe = PageElements(
        page_num=1,
        lines=_LINES,
        page_lines=(PageLine(text="4 §", y_order=0, bbox=BBox(0, 780, 40, 800), band="top"),),
    )
    rendered = numbered_page_text(pe.lines, pe.images, page_num=1)
    assert rendered.splitlines()[0] == "[1] 4 §"
    assert "bbox" not in rendered  # no geometry leaks to the model

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
# Structural PATCH in the converge loop (milestone 2) — delete / relabel.       #
# --------------------------------------------------------------------------- #


def test_converge_node_delete_retracts_a_duplicated_node_and_reaches_fixpoint() -> None:
    # Round 1 emits a DUPLICATED paragraph (the annex-row-duplication class): the
    # body PARA appears twice (nodes 1 and 2). A findings-free gate wouldn't fire,
    # so we also reference L99 (finding → gate). The refine round DELETES the
    # duplicate (N2 = the 2nd text-leaf line) and the next round is empty → the
    # simulacrum converges onto the (de-duplicated) page.
    ro = "\n".join(_LINES)
    round1 = f"1 PARA 0 L2{US}2 PARA 0 L2{US}3 PARA 0 L99{US}"  # L99 → gate
    delta = f"1 PATCH 0 N2:{US}"  # delete the 2nd text-leaf node (the duplicate)
    vision = _FakeConvergeVision(round1, deltas=[delta, ""])
    cp = converge_page(vision, _manifestation(), 1, _page_elements(), reading_order_text=ro)
    # Exactly one copy of the paragraph remains.
    texts = [n.text for n in cp.nodes]
    assert texts.count(_LINES[1]) == 1
    assert cp.convergence.termination == "empty_patch"
    assert cp.convergence.patches_total >= 1


def test_converge_node_delete_removes_the_whole_subtree() -> None:
    ro = "\n".join(_LINES)
    # A text-bearing SECTION (line 1) with a nested text-bearing PARA child (line 2).
    # Deleting N1 (the SECTION line) removes the SECTION AND its nested PARA — the
    # whole subtree, not just the addressed node.
    round1 = (
        f"1 SECTION 0 L1{US}"
        f"2 PARA 1 L2{US}"      # nested under the SECTION
        f"3 PARA 0 L3{US}"      # a sibling that must survive
        f"4 PARA 0 L99{US}"     # → gate
    )
    delta = f"1 PATCH 0 N1:{US}"  # N1 = the SECTION (1st text leaf) → subtree gone
    vision = _FakeConvergeVision(round1, deltas=[delta, ""])
    cp = converge_page(vision, _manifestation(), 1, _page_elements(), reading_order_text=ro)
    all_texts = [n.text for n in cp.nodes]
    # The SECTION (L1) and its nested PARA (L2) are BOTH gone; the sibling L3 stays.
    assert _LINES[0] not in all_texts   # SECTION text gone
    assert _LINES[1] not in all_texts   # nested-child text gone with it
    assert _LINES[2] in all_texts       # sibling survives


def test_converge_node_relabel_changes_the_kind_across_a_round() -> None:
    ro = "\n".join(_LINES)
    round1 = f"1 PARA 0 L1{US}2 PARA 0 L99{US}"  # L99 → gate
    delta = f"1 PATCH 0 N1: HEADING{US}"  # relabel the 1st text leaf to HEADING
    vision = _FakeConvergeVision(round1, deltas=[delta, ""])
    cp = converge_page(vision, _manifestation(), 1, _page_elements(), reading_order_text=ro)
    assert cp.nodes[0].kind is SourceDocumentNodeKind.HEADING
    assert cp.nodes[0].text == _LINES[0]  # text preserved
    assert cp.convergence.termination == "empty_patch"


def test_converge_node_relabel_oscillation_terminates_keep_last_flagged() -> None:
    # The structural analog of the text A→B→A oscillation: relabel PARA→HEADING→PARA.
    # The PARA re-entry is an earlier-round resolved-tree hash → oscillation, keep
    # the LAST result, flagged, no spin.
    ro = "\n".join(_LINES)
    round1 = f"1 PARA 0 L1{US}2 PARA 0 L99{US}"
    deltas = [f"1 PATCH 0 N1: HEADING{US}", f"1 PATCH 0 N1: PARA{US}"]
    vision = _FakeConvergeVision(round1, deltas=deltas)
    cp = converge_page(vision, _manifestation(), 1, _page_elements(), reading_order_text=ro)
    assert cp.convergence.termination == "oscillation"
    assert cp.nodes[0].kind is SourceDocumentNodeKind.PARAGRAPH  # kept the last


def test_converge_node_relabel_to_ungoverned_kind_is_a_noop_not_a_crash() -> None:
    # A relabel to an un-governed kind is dropped upstream (no op reaches the tree);
    # the round then has count 0 → empty_patch, the kind is unchanged.
    ro = "\n".join(_LINES)
    round1 = f"1 PARA 0 L1{US}2 PARA 0 L99{US}"
    delta = f"1 PATCH 0 N1: BOGUS{US}"
    vision = _FakeConvergeVision(round1, deltas=[delta])
    cp = converge_page(vision, _manifestation(), 1, _page_elements(), reading_order_text=ro)
    assert cp.nodes[0].kind is SourceDocumentNodeKind.PARAGRAPH
    assert cp.convergence.termination == "empty_patch"


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


# --------------------------------------------------------------------------- #
# Geometry bridge (Decision 8): bind by SOURCE-LINE INDEX, not exact text.      #
# --------------------------------------------------------------------------- #


def _multi_line_page(texts, boxes) -> PageElements:
    """A born-digital page: model-facing lines + rank-aligned per-line geometry."""
    page_lines = tuple(
        PageLine(text=t, y_order=i, bbox=b, band="body", indent=int(b.x0 // 18))
        for i, (t, b) in enumerate(zip(texts, boxes, strict=True))
    )
    return PageElements(
        page_num=1,
        lines=tuple(texts),
        page_lines=page_lines,
        page_width=595.0,
        page_height=842.0,
    )


def test_corrected_leaf_keeps_its_bbox_via_source_line_index() -> None:
    # A leaf whose text the converge loop CORRECTED (so it no longer matches ANY
    # page line by string) must still keep its geometry — bound by rank, not text.
    texts = ["Alpha line one", "Beta line two", "Gamma line three"]
    boxes = [BBox(72, 800, 500, 812), BBox(72, 780, 500, 792), BBox(72, 760, 500, 772)]
    pe = _multi_line_page(texts, boxes)
    ro = "\n".join(texts)
    # Round-1 span-copies all three; the refine round PATCHes line 2's text to a
    # CORRECTED string present on NO page line (models a garble→clean re-read).
    round1 = f"1 PARA 0 L1{US}2 PARA 0 L2{US}3 PARA 0 L3{US}"
    deltas = [f"1 PATCH 0 L2: Beta CORRECTED two{US}"]
    vision = _FakeConvergeVision(round1, deltas=deltas)
    cp = converge_page(vision, _manifestation(), 1, pe, reading_order_text=ro)
    sim = build_page_simulacrum(cp, _manifestation(), 1, pe, reading_order_text=ro)
    corrected = next(n for n in sim.nodes if n.text == "Beta CORRECTED two")
    # Exact-text-first-wins would have LOST this bbox (no line reads that text);
    # rank binding keeps line-2's geometry attached to the corrected leaf.
    assert corrected.anchor.bbox == BBox(72, 780, 500, 792)
    assert decode_metadata(corrected.attrs).band == "body"


def test_recurring_identical_lines_each_bind_their_own_geometry() -> None:
    # Three identical lines at DIFFERENT y positions: exact-text-first-wins collapsed
    # all three onto the first occurrence's bbox; rank binding gives each its own.
    texts = ["Sama rivi", "Sama rivi", "Sama rivi"]
    boxes = [BBox(72, 800, 300, 812), BBox(72, 780, 300, 792), BBox(72, 760, 300, 772)]
    pe = _multi_line_page(texts, boxes)
    ro = "\n".join(texts)
    round1 = f"1 PARA 0 L1{US}2 PARA 0 L2{US}3 PARA 0 L3{US}"
    vision = _FakeConvergeVision(round1, deltas=[""])
    cp = converge_page(vision, _manifestation(), 1, pe, reading_order_text=ro)
    sim = build_page_simulacrum(cp, _manifestation(), 1, pe, reading_order_text=ro)
    bboxes = [n.anchor.bbox for n in sim.nodes]
    assert bboxes == boxes  # each occurrence keeps ITS OWN y, not the first's


# --------------------------------------------------------------------------- #
# rect ↔ line alignment (Task 1): a count mismatch must NOT discard geometry.   #
# --------------------------------------------------------------------------- #


class _FakeTextpage:
    """Minimal pypdfium2 textpage: a text-range splitter + a rect API.

    ``lines`` are the model-facing text lines (from ``get_text_range``); ``rects``
    are ``(text, (left, bottom, right, top))`` rows from the rect API (possibly a
    DIFFERENT count than ``lines`` — the born-digital off-by-N this fix tolerates)."""

    def __init__(self, lines, rects):
        self._text = "\n".join(lines)
        self._rects = list(rects)

    def get_text_range(self):
        return self._text

    def count_rects(self):
        return len(self._rects)

    def get_rect(self, i):
        return self._rects[i][1]

    def get_text_bounded(self, *, left, bottom, right, top):
        for text, (l, b, r, t) in self._rects:
            if (l, b, r, t) == (left, bottom, right, top):
                return text
        return ""


def test_rect_line_count_mismatch_preserves_per_line_geometry() -> None:
    # 3 rects vs 2 lines (a stray rect the splitter merged/dropped): the OLD code
    # discarded ALL geometry → un-croppable page. The fix aligns by text overlap so
    # every line that DOES appear in the rects keeps its bbox.
    from lawvm.ingest.page_elements import PageElementProducer

    lines = ["First body line", "Second body line"]
    rects = [
        ("First body line", (72.0, 780.0, 500.0, 792.0)),
        ("stray artifact rect", (0.0, 400.0, 10.0, 405.0)),
        ("Second body line", (72.0, 760.0, 500.0, 772.0)),
    ]
    tp = _FakeTextpage(lines, rects)
    prod = PageElementProducer()
    out_lines, page_lines, notes = prod._extract_lines_with_geometry(tp, page_h=842.0)
    assert out_lines == tuple(lines)
    # Every born-digital line yields usable per-line geometry (not None).
    assert len(page_lines) == 2
    assert all(pl.bbox is not None for pl in page_lines)
    assert page_lines[0].bbox == BBox(72.0, 780.0, 500.0, 792.0)
    assert page_lines[1].bbox == BBox(72.0, 760.0, 500.0, 772.0)
    # A typed note records the mismatch + how many lines bound (never silent).
    assert notes and "mismatch" in notes[0] and "2/2 lines bound" in notes[0]


def test_align_lines_to_geom_positional_identity_when_counts_and_text_match() -> None:
    # Exact count + exact text → identical to the old positional zip (no regression).
    from lawvm.ingest.page_elements import _align_lines_to_geom

    b = [BBox(0, 100, 50, 110), BBox(0, 90, 50, 100), BBox(0, 80, 50, 90)]
    geom = [("A", b[0]), ("B", b[1]), ("C", b[2])]
    out = _align_lines_to_geom(["A", "B", "C"], geom)
    assert [t for t, _ in out] == ["A", "B", "C"]
    assert [bb for _, bb in out] == b


def test_align_lines_to_geom_recurring_lines_each_get_own_row() -> None:
    from lawvm.ingest.page_elements import _align_lines_to_geom

    b = [BBox(0, 100, 50, 110), BBox(0, 90, 50, 100), BBox(0, 80, 50, 90)]
    geom = [("X", b[0]), ("X", b[1]), ("X", b[2])]
    out = _align_lines_to_geom(["X", "X", "X"], geom)
    assert [bb for _, bb in out] == b  # each occurrence → its own row, in order


# --------------------------------------------------------------------------- #
# Per-PDF page concurrency (§ pipeline concurrency): bounded ThreadPoolExecutor #
# over converge_page — GPU-saturating, INDEX-ORDERED determinism, contained     #
# failures, pdfium-lock-serialized renders.                                     #
# --------------------------------------------------------------------------- #

import json
import threading
import time


def _distinct_pages_producer():
    """A fake page producer: each page has DISTINCT content (so simulacra differ)."""

    class _DistinctPageProducer:
        def page_elements(self, pdf_bytes, page_num):
            # A body line unique per page + a recurring bottom-band page-number footer.
            return PageElements(
                page_num=page_num,
                lines=(f"Body of page {page_num} with content.", str(page_num)),
                page_lines=(
                    PageLine(
                        text=f"Body of page {page_num} with content.",
                        y_order=0,
                        bbox=BBox(72, 400, 500, 420),
                        band="body",
                        indent=4,
                    ),
                    PageLine(
                        text=str(page_num),
                        y_order=1,
                        bbox=BBox(280, 20, 320, 34),
                        band="bottom",
                        indent=15,
                    ),
                ),
                page_width=595.0,
                page_height=842.0,
            )

    return _DistinctPageProducer()


class _ProbeVision:
    """Instrumented fake vision: records concurrency + can fail a page + can gate a
    ``PDFIUM_LOCK``-guarded 'render' phase, all deterministic in OUTPUT.

    ``propose_page_struct`` span-copies both page lines (``1 PARA 0 L1``/``L2``) so
    each page's simulacrum is a pure function of its (distinct) page content — the
    OUTPUT never depends on scheduling. The instrumentation only observes timing.
    """

    def __init__(self, *, barrier=None, fail_on=(), guard_render=False):
        self._barrier = barrier
        self._fail_on = set(fail_on)
        self._guard_render = guard_render
        self._lock = threading.Lock()
        self._inflight = 0
        self.max_inflight = 0
        self._render_inflight = 0
        self.render_max_concurrency = 0
        self.completed: set[int] = set()
        self.barrier_broke = False

    def is_available(self) -> bool:
        return True

    def propose_page_struct(self, man, page_num, pe, *, leaf_mode="patch"):
        from lawvm.ingest.llm_backends.vision_producer import StructPageResult
        from lawvm.ingest.visual import PDFIUM_LOCK

        with self._lock:
            self._inflight += 1
            self.max_inflight = max(self.max_inflight, self._inflight)
        try:
            if page_num in self._fail_on:
                raise RuntimeError(f"boom on page {page_num}")
            # Inference phase: rendezvous proves >1 page is genuinely in-flight.
            if self._barrier is not None:
                try:
                    self._barrier.wait(timeout=10)
                except threading.BrokenBarrierError:
                    self.barrier_broke = True
            # Render phase: pdfium is process-global + thread-unsafe, so every touch
            # MUST serialize on the ONE shared lock even while inference parallelizes.
            if self._guard_render:
                with PDFIUM_LOCK:
                    with self._lock:
                        self._render_inflight += 1
                        self.render_max_concurrency = max(
                            self.render_max_concurrency, self._render_inflight
                        )
                    time.sleep(0.01)  # widen the overlap window a racy lock would lose
                    with self._lock:
                        self._render_inflight -= 1
            wire = f"1 PARA 0 L1{US}2 PARA 0 L2{US}"
            build = parse_struct_wire(wire, pe.lines, [])
            with self._lock:
                self.completed.add(page_num)
            return StructPageResult(build=build, raw_content=wire, images=())
        finally:
            with self._lock:
                self._inflight -= 1

    def propose_page_patch_delta(self, man, page_num, numbered_lines):
        return ""


def _sim_json(sim) -> str:
    return json.dumps(page_simulacrum_to_json(sim), sort_keys=True, ensure_ascii=False)


def test_pooled_build_is_byte_identical_to_serial_baseline() -> None:
    # (a) Order-independent determinism: the pooled build (8 workers) is BYTE-
    # IDENTICAL to the serial baseline (1 worker) over the same fake pages — the
    # simulacra are assembled by page index, never completion order.
    ro = [f"Body of page {i} with content.\n{i}" for i in range(1, 7)]
    serial = build_page_simulacra(
        _ProbeVision(), _manifestation(), _distinct_pages_producer(), ro, max_workers=1
    )
    pooled = build_page_simulacra(
        _ProbeVision(), _manifestation(), _distinct_pages_producer(), ro, max_workers=8
    )
    assert [s.page_num for s in pooled] == [1, 2, 3, 4, 5, 6]
    assert [_sim_json(s) for s in serial] == [_sim_json(s) for s in pooled]


def test_pooled_build_is_stable_across_repeated_runs() -> None:
    # Determinism under scheduling churn: many pooled runs all agree byte-for-byte.
    ro = [f"Body of page {i} with content.\n{i}" for i in range(1, 6)]
    runs = [
        [
            _sim_json(s)
            for s in build_page_simulacra(
                _ProbeVision(),
                _manifestation(),
                _distinct_pages_producer(),
                ro,
                max_workers=5,
            )
        ]
        for _ in range(5)
    ]
    assert all(r == runs[0] for r in runs)


def test_pages_converge_concurrently_not_serially() -> None:
    # (b) Concurrency actually happens: a barrier of width == page_count only
    # releases if every page's converge is in-flight SIMULTANEOUSLY. A serial loop
    # would hang here (each waits for the next that never starts) → the 10s barrier
    # timeout would break it. It does not.
    n = 4
    ro = [f"Body of page {i} with content.\n{i}" for i in range(1, n + 1)]
    vision = _ProbeVision(barrier=threading.Barrier(n))
    sims = build_page_simulacra(
        vision, _manifestation(), _distinct_pages_producer(), ro, max_workers=n
    )
    assert len(sims) == n
    assert vision.barrier_broke is False   # all n rendezvoused → all n concurrent
    assert vision.max_inflight == n


def test_pdfium_render_stays_serialized_under_the_page_pool() -> None:
    # (d) The shared PDFIUM_LOCK still serializes renders: even though inference runs
    # concurrently (max_inflight > 1), the lock-guarded 'render' phase is never
    # entered by two workers at once (render_max_concurrency == 1). This is the
    # invariant that keeps concurrent pdfium safe (a racy lock would segfault).
    n = 4
    ro = [f"Body of page {i} with content.\n{i}" for i in range(1, n + 1)]
    vision = _ProbeVision(guard_render=True)
    sims = build_page_simulacra(
        vision, _manifestation(), _distinct_pages_producer(), ro, max_workers=n
    )
    assert len(sims) == n
    assert vision.max_inflight > 1            # inference genuinely parallelized
    assert vision.render_max_concurrency == 1  # pdfium never overlapped


def test_pooled_build_shares_the_one_visual_pdfium_lock() -> None:
    # The pool relies on the SAME systemic lock the render primitive holds — not a
    # private reinvention (#250). Assert object identity across the two modules.
    from lawvm.ingest import page_elements as _pe
    from lawvm.ingest import visual as _visual

    assert _pe.PDFIUM_LOCK is _visual.PDFIUM_LOCK


def test_per_page_failure_is_contained_and_raised_at_lowest_index() -> None:
    # (c) A per-page failure is CONTAINED: pages 2 and 4 raise, but their sibling
    # pages still fully process (converge completed), and the batch re-raises the
    # LOWEST failing index (page 2) — matching the serial loop's fail-at-first-bad-
    # page order (fail-loud, never a silently dropped page).
    ro = [f"Body of page {i} with content.\n{i}" for i in range(1, 6)]
    vision = _ProbeVision(fail_on=(2, 4))
    import pytest

    with pytest.raises(RuntimeError, match="boom on page 2"):
        build_page_simulacra(
            vision, _manifestation(), _distinct_pages_producer(), ro, max_workers=5
        )
    # The good pages ran to completion despite the failing siblings (containment).
    assert {1, 3, 5} <= vision.completed
    assert 2 not in vision.completed and 4 not in vision.completed


def test_zero_pages_returns_empty_tuple() -> None:
    # Degenerate input: no pages → empty tuple, no pool spun up, no crash.
    out = build_page_simulacra(
        _ProbeVision(), _manifestation(), _distinct_pages_producer(), []
    )
    assert out == ()


def test_env_default_concurrency_is_read_and_bounded(monkeypatch) -> None:
    # The default worker count is env-overridable (LAWVM_INGEST_PAGE_CONCURRENCY) and
    # bounded to [1, page_count]; the RESULT is invariant to it (determinism knob).
    import importlib

    import lawvm.ingest.page_level as pl

    monkeypatch.setenv("LAWVM_INGEST_PAGE_CONCURRENCY", "3")
    reloaded = importlib.reload(pl)
    try:
        assert reloaded._DEFAULT_PAGE_CONCURRENCY == 3
        ro = [f"Body of page {i} with content.\n{i}" for i in range(1, 5)]
        env_default = reloaded.build_page_simulacra(
            _ProbeVision(), _manifestation(), _distinct_pages_producer(), ro
        )
        explicit1 = reloaded.build_page_simulacra(
            _ProbeVision(),
            _manifestation(),
            _distinct_pages_producer(),
            ro,
            max_workers=1,
        )
        assert [_sim_json(s) for s in env_default] == [_sim_json(s) for s in explicit1]
    finally:
        monkeypatch.delenv("LAWVM_INGEST_PAGE_CONCURRENCY", raising=False)
        importlib.reload(pl)


# --------------------------------------------------------------------------- #
# Appraise-first cold-read ladder (§ agentic, increment 1).                     #
# --------------------------------------------------------------------------- #


def _appraisal(has_content=True, kind="prose", lines="reliable"):
    from lawvm.ingest.llm_backends.vision_producer import PageAppraisal

    return PageAppraisal(has_content=has_content, kind=kind, lines=lines, raw="")


class _AppraiseLadderVision(_FakeConvergeVision):
    """Fake with ``appraise_page`` + a scripted per-cold-read wire sequence.

    Records the ``leaf_mode`` of each cold read so routing is assertable; the last
    wire repeats if the ladder asks for more reads than scripted."""

    def __init__(self, appraisal, wires):
        super().__init__(wires[0] if wires else "")
        self._appraisal = appraisal
        self._wires = list(wires)
        self._i = 0
        self.leaf_modes_seen: list[str] = []

    def appraise_page(self, man, page_num, page_elements):
        return self._appraisal

    def propose_page_struct(self, man, page_num, page_elements, *, leaf_mode="patch"):
        from lawvm.ingest.llm_backends.vision_producer import StructPageResult

        self.leaf_modes_seen.append(leaf_mode)
        wire = self._wires[min(self._i, len(self._wires) - 1)] if self._wires else ""
        self._i += 1
        build = parse_struct_wire(
            wire, page_elements.lines, [i.element for i in page_elements.images]
        )
        return StructPageResult(build=build, raw_content=wire, images=page_elements.images)


def test_appraised_blank_page_short_circuits_without_a_cold_read() -> None:
    v = _AppraiseLadderVision(_appraisal(has_content=False), wires=[f"1 PARA 0 L1{US}"])
    cp = converge_page(v, _manifestation(), 1, _page_elements(), reading_order_text="")
    assert cp.convergence.termination == "appraised_blank"
    assert cp.convergence.read_attempts == 0
    assert cp.nodes == ()
    assert v.leaf_modes_seen == []  # the model saw blank → never issued a cold read


def test_degenerate_empty_read_retries_and_recovers() -> None:
    ro = "\n".join(_LINES)
    # First cold read is EMPTY (degenerate vs has_content) → retry-identical recovers.
    v = _AppraiseLadderVision(
        _appraisal(has_content=True, lines="reliable"),
        wires=["", f"1 HEADING 0 L1{US}2 PARA 0 L2-3{US}"],
    )
    cp = converge_page(
        v, _manifestation(), 1, _page_elements(),
        reading_order_text=ro, adjudicator=_CorroboratingAdjudicator(),
    )
    assert cp.convergence.read_attempts == 2
    assert cp.convergence.termination != "unreadable_page"
    assert len(cp.nodes) >= 1


def test_all_rungs_degenerate_is_typed_unreadable_not_silent_empty() -> None:
    v = _AppraiseLadderVision(_appraisal(has_content=True), wires=[""])  # always empty
    cp = converge_page(v, _manifestation(), 1, _page_elements(), reading_order_text="x")
    assert cp.convergence.termination == "unreadable_page"
    assert "unreadable_page" in cp.convergence.gate_reasons
    assert cp.convergence.read_attempts == 3  # full ladder climbed (route, retry, switch)
    assert cp.nodes == ()


def test_untrustworthy_lines_route_first_cold_read_to_inline() -> None:
    v = _AppraiseLadderVision(
        _appraisal(has_content=True, lines="unreliable"), wires=[f"1 PARA 0 T: hello{US}"]
    )
    converge_page(v, _manifestation(), 1, _page_elements(), reading_order_text="hello")
    assert v.leaf_modes_seen[0] == "inline"


def test_trustworthy_lines_route_first_cold_read_to_span() -> None:
    v = _AppraiseLadderVision(
        _appraisal(has_content=True, lines="reliable"), wires=[f"1 HEADING 0 L1{US}"]
    )
    converge_page(v, _manifestation(), 1, _page_elements(), reading_order_text="\n".join(_LINES))
    assert v.leaf_modes_seen[0] == "span"

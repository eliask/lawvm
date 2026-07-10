"""v2 structural build-script wire — hermetic tests (pure, serverless).

The v2 span-copy lane graduates the flat block list to an EXPLICIT BUILD SCRIPT
(``<id> <kind> <parent> <src>``). These tests drive the pure wire parser +
tree assembler (``struct_wire.parse_struct_wire``), the numbered page-element
producer (``page_elements``), the fake-vision struct producer
(``VisionPageProducer.propose_page_struct``), the struct ingest + image
content-addressing (``struct_document_ingest`` / ``parse_struct_and_cache``), and
the shared-grammar span-vs-full leaf-content distinction — all without a network,
a PDF lib, or a model. One env+network test runs the real comparison harness.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Tuple, cast

import pytest

from lawvm.core.source_document import (
    AssuranceTier,
    SourceDocumentNodeKind,
    SourceManifestation,
)
from lawvm.finland.llm_backends.vision_producer import (
    StructPageResult,
    VisionPageProducer,
)
from lawvm.finland.source_document.page_elements import (
    EmbeddedImage,
    PageElements,
    image_blob_name,
    numbered_page_text,
)
from lawvm.finland.source_document.struct_wire import (
    STRUCT_COMMAND_SEPARATOR,
    ImageElement,
    parse_struct_wire,
    render_struct_wire_for_debug,
)

US = STRUCT_COMMAND_SEPARATOR

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
        source_role="government_proposal_draft",
        fetched_at=datetime(2026, 1, 1),
        media_type="application/pdf",
    )


def _img(index: int = 1, digest: str = "d" * 64) -> ImageElement:
    return ImageElement(
        index=index, digest=digest, media_type="image/png",
        width=100, height=50, bbox=(10.0, 20.0, 110.0, 70.0),
    )


# --------------------------------------------------------------------------- #
# Wire parse: span-copy leaves, hierarchy, tables                             #
# --------------------------------------------------------------------------- #


def test_span_leaves_are_copied_from_referenced_lines_verbatim() -> None:
    wire = f"1 HEADING 0 L1{US}2 PARA 0 L2-3{US}3 FOOTNOTE 0 L4{US}"
    r = parse_struct_wire(wire, _LINES)
    kinds = [n.kind for n in r.roots]
    assert kinds == [
        SourceDocumentNodeKind.HEADING,
        SourceDocumentNodeKind.PARAGRAPH,
        SourceDocumentNodeKind.FOOTNOTE,
    ]
    # Text span-COPIED from the input lines byte-for-byte (never re-typed).
    assert r.roots[1].text == _LINES[1] + "\n" + _LINES[2]
    assert r.roots[2].text == "1) Sovelletaan verovuodesta 2025."
    assert r.findings == ()


def test_arbitrary_depth_hierarchy_via_parent_links() -> None:
    # A TABLE with a ROW with two CELLs — three levels of nesting from parent ids.
    wire = f"1 TABLE 0 -{US}2 ROW 1 -{US}3 CELL 2 L1{US}4 CELL 2 L2{US}"
    r = parse_struct_wire(wire, _LINES)
    table = r.roots[0]
    assert table.kind is SourceDocumentNodeKind.TABLE
    row = table.children[0]
    assert row.kind is SourceDocumentNodeKind.TABLE_ROW
    assert [c.text for c in row.children] == [_LINES[0], _LINES[1]]


def test_free_reorder_by_emission_order_of_children() -> None:
    # Emit line 5 BEFORE line 4 → the corrected reading order is (line5, line4).
    wire = f"1 PARA 0 -{US}2 SUBSECTION 1 L5{US}3 SUBSECTION 1 L4{US}"
    r = parse_struct_wire(wire, _LINES)
    assert [c.text for c in r.roots[0].children] == [_LINES[4], _LINES[3]]


def test_sub_line_char_span_reorder() -> None:
    # L1.2-5 = chars 2..5 of "4 §..." → within-line reorder granularity.
    wire = f"1 PARA 0 L2.0-3{US}"
    r = parse_struct_wire(wire, _LINES)
    assert r.roots[0].text == _LINES[1][0:3]


# --------------------------------------------------------------------------- #
# struct_patch: addressed char-span / whole-line deltas over the numbered lines #
# --------------------------------------------------------------------------- #


def test_patch_char_span_corrects_a_line_before_span_copy() -> None:
    # Line 3 "valmisteveroa 4 senttiä litralta." — patch chars 0-13 (the first
    # token) to a corrected form; a PARA that references L3 reads the corrected text.
    wire = f"1 PATCH 0 L3.0-13: valmisteveroa{US}2 PARA 0 L3{US}"
    r = parse_struct_wire(wire, _LINES)
    assert r.patches_applied == 1
    assert r.roots[0].text == "valmisteveroa 4 senttiä litralta."
    # The PATCH itself is a delta, NOT a tree node — only the PARA is a root.
    assert len(r.roots) == 1 and r.roots[0].kind is SourceDocumentNodeKind.PARAGRAPH


def test_patch_whole_line_replaces_the_line() -> None:
    wire = f"1 PATCH 0 L5: 42{US}2 PARA 0 L5{US}"
    r = parse_struct_wire(wire, _LINES)
    assert r.patches_applied == 1
    assert r.roots[0].text == "42"


def test_multiple_char_patches_on_one_line_apply_right_to_left() -> None:
    # Two non-overlapping char patches on line 2; right-to-left application keeps
    # the earlier span's offsets valid even when the later span changes length.
    wire = (
        f"1 PATCH 0 L2.0-3: XXXX{US}"   # replace "Sen" (0-3) with longer "XXXX"
        f"2 PATCH 0 L2.4-11: YY{US}"    # replace "lisäksi" (4-11) with shorter "YY"
        f"3 PARA 0 L2{US}"
    )
    r = parse_struct_wire(wire, _LINES)
    assert r.patches_applied == 2
    assert r.roots[0].text == "XXXX YY, mitä 1 momentissa säädetään, hakijalle palautetaan"


def test_out_of_range_patch_is_a_finding_not_a_crash() -> None:
    wire = f"1 PATCH 0 L99: x{US}2 PATCH 0 L2.500-600: y{US}3 PARA 0 L2{US}"
    r = parse_struct_wire(wire, _LINES)
    assert r.patches_applied == 0
    assert any("out of range" in f for f in r.findings)
    assert r.roots[0].text == _LINES[1]  # untouched


# --------------------------------------------------------------------------- #
# Node-addressed structural PATCH — DELETE subtree / RELABEL kind (milestone 2) #
# --------------------------------------------------------------------------- #


def test_node_patch_deletes_a_node_and_its_whole_subtree() -> None:
    # A duplicated ROW: the model retracts node 2 (the dup) + its CELL child 3.
    wire = (
        f"1 TABLE 0 -{US}"
        f"2 ROW 1 -{US}"
        f"3 CELL 2 L1{US}"     # child of the deleted ROW → goes with it
        f"4 ROW 1 -{US}"
        f"5 CELL 4 L2{US}"
        f"6 PATCH 0 N2:{US}"   # delete node 2 (+ subtree 3)
    )
    r = parse_struct_wire(wire, _LINES)
    assert r.node_patches_applied == 1
    table = r.roots[0]
    assert table.kind is SourceDocumentNodeKind.TABLE
    # Only the SECOND row (node 4/5) survives.
    assert len(table.children) == 1
    row = table.children[0]
    assert row.children[0].text == _LINES[1]


def test_node_patch_relabels_a_node_to_a_governed_kind() -> None:
    wire = f"1 PARA 0 L1{US}2 PATCH 0 N1: HEADING{US}"
    r = parse_struct_wire(wire, _LINES)
    assert r.node_patches_applied == 1
    assert r.roots[0].kind is SourceDocumentNodeKind.HEADING
    assert r.roots[0].text == _LINES[0]  # text unchanged by a relabel


def test_node_patch_bad_id_is_a_finding_not_a_crash() -> None:
    wire = f"1 PARA 0 L1{US}2 PATCH 0 N99:{US}3 PATCH 0 N42: SECTION{US}"
    r = parse_struct_wire(wire, _LINES)
    assert r.node_patches_applied == 0
    assert any("N99" in f and "no such node" in f for f in r.findings)
    assert any("N42" in f and "no such node" in f for f in r.findings)
    assert r.roots[0].kind is SourceDocumentNodeKind.PARAGRAPH  # untouched


def test_node_patch_relabel_to_ungoverned_kind_is_dropped_with_a_finding() -> None:
    wire = f"1 PARA 0 L1{US}2 PATCH 0 N1: BOGUS{US}"
    r = parse_struct_wire(wire, _LINES)
    assert r.node_patches_applied == 0
    assert any("un-governed kind" in f for f in r.findings)
    assert r.roots[0].kind is SourceDocumentNodeKind.PARAGRAPH  # unchanged


def test_node_and_text_patch_coexist_in_one_wire() -> None:
    # A text PATCH (L) and a node PATCH (N) in the same wire are independent.
    wire = (
        f"1 PARA 0 L1{US}"
        f"2 PARA 0 L2{US}"
        f"3 PATCH 0 L1: 5 §{US}"   # text delta on line 1
        f"4 PATCH 0 N2:{US}"        # delete node 2
    )
    r = parse_struct_wire(wire, _LINES)
    assert r.patches_applied == 1
    assert r.node_patches_applied == 1
    assert [n.text for n in r.roots] == ["5 §"]


def test_dehyphenate_joins_discretionary_hyphens_not_real_ones() -> None:
    from lawvm.finland.source_document.page_elements import dehyphenate

    # pypdfium2 emits U+FFFE (and U+00AD) for a soft/discretionary hyphen; join it.
    assert dehyphenate("kriisinrat￾kaisusta") == "kriisinratkaisusta"
    assert dehyphenate("vä­hintään") == "vähintään"
    # Across an actual line-wrap (hyphen then newline) → joined.
    assert dehyphenate("jäsen￾\nvaltioiden") == "jäsenvaltioiden"
    # A REAL hyphen (U+002D) is legal content — never touched.
    assert dehyphenate("EU-jäsenvaltio") == "EU-jäsenvaltio"


def test_dehyphenate_preserves_real_compound_hyphens_at_a_line_break() -> None:
    """U+FFFE (and a real hyphen+newline) is AMBIGUOUS — a real compound hyphen that fell
    at the line break must be PRESERVED, not fused into a corrupted word."""
    from lawvm.finland.source_document.page_elements import dehyphenate

    # (a) ELLIPTICAL compound: hyphen + a bare conjunction whose left-member is
    # corroborated by a real hyphen elsewhere → keep the hyphen AND the space.
    assert (
        dehyphenate("sosiaali-ala sekä sosiaali￾ja terveys")
        == "sosiaali-ala sekä sosiaali- ja terveys"
    )
    # A REAL hyphen mid-line before a conjunction is already legal content, untouched.
    assert dehyphenate("sosiaali- ja terveys") == "sosiaali- ja terveys"
    # But an UN-corroborated "-ja" is a partitive/agent-noun ending, NOT elliptical → fuse.
    assert dehyphenate("puheenjohta￾ja valittiin") == "puheenjohtaja valittiin"
    assert dehyphenate("asiakir￾ja") == "asiakirja"

    # (b) LEXICAL compound, identical-vowel seam — kept, both glyph forms.
    assert dehyphenate("kauppa￾alusluettelo") == "kauppa-alusluettelo"
    assert dehyphenate("kauppa-\nalusluettelo") == "kauppa-alusluettelo"
    assert dehyphenate("laina￾aikaan") == "laina-aikaan"
    # A DIFFERENT-vowel seam is a closed compound (no hyphen) → still fuses.
    assert dehyphenate("työ￾elämä") == "työelämä"

    # (c) proper-noun / acronym seam — kept.
    assert dehyphenate("ETA￾sopimus") == "ETA-sopimus"
    assert dehyphenate("Saudi￾Arabian") == "Saudi-Arabian"

    # A GENUINE soft break (a bare mid-morpheme fragment) still fuses — both glyph forms
    # and the soft-hyphen U+00AD — so the SOFT_HYPHEN_JOIN fold keeps firing.
    assert dehyphenate("kriisinrat￾kaisusta") == "kriisinratkaisusta"
    assert dehyphenate("kriisinrat-\nkaisusta") == "kriisinratkaisusta"
    assert dehyphenate("kriisinrat­\nkaisusta") == "kriisinratkaisusta"


# --------------------------------------------------------------------------- #
# Wire parse: inline (full) leaves + images (same grammar)                    #
# --------------------------------------------------------------------------- #


def test_inline_full_leaves_carry_transcribed_text() -> None:
    # SAME grammar as span; the ONLY difference is a T: inline leaf vs an L-ref.
    wire = f"1 HEADING 0 T: 4 §{US}2 PARA 0 T: Sen lisäksi.{US}"
    r = parse_struct_wire(wire, _LINES)
    assert r.roots[0].text == "4 §"
    assert r.roots[1].text == "Sen lisäksi."


def test_image_node_is_content_addressed_never_pixel_copied() -> None:
    wire = f"1 IMAGE 0 I1{US}2 TRANSCRIBE 1 T: Kuvio 1.{US}"
    r = parse_struct_wire(wire, _LINES, [_img()])
    image = r.roots[0]
    assert image.kind is SourceDocumentNodeKind.IMAGE_REGION
    assert image.image is not None and image.image.digest == "d" * 64
    assert image.text == ""  # no pixels re-encoded; only a reference
    # Image-baked text is a nested TRANSCRIBE child (single witness).
    assert image.children[0].text == "Kuvio 1."


# --------------------------------------------------------------------------- #
# Wire parse: hygiene (drop, never clamp/relabel/reparent silently)           #
# --------------------------------------------------------------------------- #


def test_ungoverned_kind_dropped_never_relabeled() -> None:
    r = parse_struct_wire(f"1 BOGUS 0 L1{US}2 PARA 0 L2{US}", _LINES)
    assert [n.kind for n in r.roots] == [SourceDocumentNodeKind.PARAGRAPH]
    assert any("un-governed kind" in f for f in r.findings)


def test_out_of_range_refs_fabricate_no_text() -> None:
    r = parse_struct_wire(f"1 PARA 0 L99{US}2 PARA 0 L2{US}3 IMAGE 0 I9{US}", _LINES)
    assert [n.text for n in r.roots] == [_LINES[1]]
    assert any("line ref out of range" in f for f in r.findings)
    assert any("image ref out of range" in f for f in r.findings)


def test_missing_parent_reparents_to_root_with_a_finding() -> None:
    # Node 1 names parent 5 which was never emitted → re-parented to ROOT, noted.
    r = parse_struct_wire(f"1 PARA 5 L1{US}2 PARA 0 L2{US}", _LINES)
    assert len(r.roots) == 2
    assert any("re-parented to root (missing parent 5)" in f for f in r.findings)


def test_self_parent_reparents_to_root() -> None:
    r = parse_struct_wire(f"1 PARA 1 L1{US}", _LINES)
    assert len(r.roots) == 1
    assert any("self-parent" in f for f in r.findings)


def test_duplicate_id_dropped() -> None:
    r = parse_struct_wire(f"1 PARA 0 L1{US}1 PARA 0 L2{US}", _LINES)
    assert len(r.roots) == 1 and r.roots[0].text == _LINES[0]
    assert any("duplicate node id" in f for f in r.findings)


def test_malformed_build_line_dropped() -> None:
    r = parse_struct_wire(f"garbage not a command{US}1 PARA 0 L1{US}", _LINES)
    assert len(r.roots) == 1
    assert any("malformed build line" in f for f in r.findings)


# --------------------------------------------------------------------------- #
# Terminator framing + compliance stats                                       #
# --------------------------------------------------------------------------- #


def test_terminator_makes_inline_payload_newlines_and_commands_inert() -> None:
    # An inline T: payload may contain newlines AND a command-looking line — the
    # 0x1F terminator (not a newline) ends the node, so it is one literal leaf.
    wire = f"1 PARA 0 T: line one\n2 PARA 0 L1\ninjected{US}3 HEADING 0 L1{US}"
    r = parse_struct_wire(wire, _LINES)
    assert r.roots[0].text == "line one\n2 PARA 0 L1\ninjected"
    assert r.roots[1].kind is SourceDocumentNodeKind.HEADING


def test_terminator_compliance_stats_and_lenient_fallback() -> None:
    # All three commands 0x1F-terminated → 3/3 compliant.
    r = parse_struct_wire(f"1 HEADING 0 L1{US}2 PARA 0 L2{US}3 PARA 0 L3{US}", _LINES)
    assert r.terminator_used
    assert (r.terminated_command_lines, r.total_command_lines) == (3, 3)
    # No terminator at all → lenient newline framing; 0/2 compliant.
    r2 = parse_struct_wire("1 HEADING 0 L1\n2 PARA 0 L2", _LINES)
    assert not r2.terminator_used
    assert (r2.terminated_command_lines, r2.total_command_lines) == (0, 2)


def test_render_struct_wire_for_debug_never_shows_raw_control_char() -> None:
    rendered = render_struct_wire_for_debug(f"1 HEADING 0 L1{US}2 PARA 0 L2{US}")
    assert US not in rendered
    assert rendered == "1 HEADING 0 L1␟\n2 PARA 0 L2␟\n"


# --------------------------------------------------------------------------- #
# Numbered page elements (text [N] + images {N})                              #
# --------------------------------------------------------------------------- #


def test_numbered_page_text_uses_collision_free_brackets() -> None:
    ei = EmbeddedImage(element=_img(2), raw_bytes=b"\x89PNG", bit_exact_source=True)
    numbered = numbered_page_text(_LINES, [ei], page_num=7)
    lines = numbered.splitlines()
    assert lines[0] == "[1] 4 §"  # [N] text
    assert lines[-1].startswith("{2} image page=7 bbox=")  # {N} image
    assert "px=100x50" in lines[-1]


def test_image_blob_name_is_zero_padded_and_extensioned() -> None:
    assert image_blob_name(3, "image/png") == "0003.png"
    assert image_blob_name(12, "image/jpeg") == "0012.jpg"
    assert image_blob_name(1, "application/octet-stream") == "0001.img"


# --------------------------------------------------------------------------- #
# propose_page_struct (fake transport) — span vs inline leaf modes            #
# --------------------------------------------------------------------------- #


class _FakeStructVision(VisionPageProducer):
    def __init__(self, content: str) -> None:
        super().__init__(model="test-vlm")
        self.seen: List[Tuple[str, str]] = []
        self._content = content

    def _render_page_png(self, pdf_bytes: bytes, page_num: int) -> bytes:  # type: ignore[override]
        return b"\x89PNG-fake"

    def _chat_struct(self, png_b64, numbered_text, *, page_num, leaf_mode):  # type: ignore[override]
        self.seen.append((leaf_mode, numbered_text))
        return self._content


def _page_elements(images: Tuple[EmbeddedImage, ...] = ()) -> PageElements:
    return PageElements(page_num=1, lines=_LINES, images=images)


def test_propose_page_struct_span_lane_span_copies_and_numbers_lines() -> None:
    vp = _FakeStructVision(f"1 HEADING 0 L1{US}2 PARA 0 L2-3{US}")
    res = vp.propose_page_struct(_manifestation(), 7, _page_elements(), leaf_mode="span")
    assert isinstance(res, StructPageResult)
    assert res.build.roots[0].text == _LINES[0]
    assert res.build.roots[1].text == _LINES[1] + "\n" + _LINES[2]
    # Span lane sends the numbered reading-order lines.
    leaf_mode, numbered = vp.seen[0]
    assert leaf_mode == "span"
    assert numbered.splitlines()[0] == "[1] 4 §"


def test_propose_page_struct_inline_lane_omits_line_numbers() -> None:
    vp = _FakeStructVision(f"1 HEADING 0 T: 4 §{US}2 PARA 0 T: body{US}")
    res = vp.propose_page_struct(_manifestation(), 1, _page_elements(), leaf_mode="inline")
    assert res.build.roots[0].text == "4 §"
    # Inline lane sends NO numbered text lines (only images are addressed).
    leaf_mode, numbered = vp.seen[0]
    assert leaf_mode == "inline"
    assert "[1]" not in numbered


def test_propose_page_struct_scrubs_wire_separator_from_input_lines() -> None:
    vp = _FakeStructVision(f"1 PARA 0 L1{US}")
    pe = PageElements(page_num=1, lines=(f"gar{US}bled", "second"), images=())
    res = vp.propose_page_struct(_manifestation(), 1, pe, leaf_mode="span")
    _leaf_mode, numbered = vp.seen[0]
    assert US not in numbered
    assert res.build.roots[0].text == "gar bled"


# --------------------------------------------------------------------------- #
# struct_document_ingest — compose + image collection                         #
# --------------------------------------------------------------------------- #


class _RecordingStructVision:
    def __init__(self, per_page):
        self._per_page = per_page
        self.calls: List[Tuple[int, str]] = []

    def is_available(self) -> bool:
        return True

    def propose_page_struct(self, manifestation, page_num, page_elements, *, leaf_mode="span"):
        from lawvm.finland.source_document.struct_wire import parse_struct_wire

        self.calls.append((page_num, leaf_mode))
        wire, images = self._per_page[page_num - 1]
        build = parse_struct_wire(wire, page_elements.lines, [i.element for i in images])
        return StructPageResult(build=build, raw_content=wire, images=images)


class _FakePageProducer:
    def __init__(self, pages):
        self._pages = pages

    def page_elements(self, pdf_bytes, page_num):
        return self._pages[page_num - 1]


def test_struct_ingest_composes_pages_and_collects_images(monkeypatch) -> None:
    import lawvm.finland.source_document.adjudicated_ingest as ai

    ei = EmbeddedImage(element=_img(1), raw_bytes=b"\x89PNGdata", bit_exact_source=True)
    monkeypatch.setattr(
        ai, "reading_order_pages_from_pdf",
        lambda b, max_pages=200: ["\n".join(_LINES), "second page text"],
    )
    pages = [
        PageElements(page_num=1, lines=_LINES, images=(ei,)),
        PageElements(page_num=2, lines=("second page text",), images=()),
    ]
    vision = _RecordingStructVision([
        (f"1 SECTION 0 -{US}2 HEADING 1 L1{US}3 IMAGE 1 I1{US}", (ei,)),
        (f"1 PARA 0 L1{US}", ()),
    ])
    result = ai.struct_document_ingest(
        _manifestation(),
        vision=vision,
        page_element_producer=_FakePageProducer(pages),
        adjudicator=None,
        transcription_modality="struct_span",
    )
    assert vision.calls == [(1, "span"), (2, "span")]
    # The image blob was surfaced for content-addressing (deduped by digest).
    assert len(result.images) == 1
    surfaced = cast(EmbeddedImage, result.images[0])
    assert surfaced.element.digest == "d" * 64
    # Composed doc has the section subtree; every node single-witness (no adjudicator).
    root = result.document.root
    kinds = {n.kind for n in _walk(root)}
    assert SourceDocumentNodeKind.IMAGE_REGION in kinds
    assert all(n.assurance_tier is AssuranceTier.SINGLE_WITNESS for n in _walk(root))


def _walk(node):
    yield node
    for c in node.children:
        yield from _walk(c)


def test_struct_ingest_rejects_non_struct_modality() -> None:
    import lawvm.finland.source_document.adjudicated_ingest as ai

    with pytest.raises(ValueError):
        ai.struct_document_ingest(
            _manifestation(), vision=object(), page_element_producer=object(),
            transcription_modality="not_a_struct_lane",
        )


# --------------------------------------------------------------------------- #
# parse_struct_and_cache — image content-addressing + distinct cache keys     #
# --------------------------------------------------------------------------- #


def test_struct_modality_folds_into_a_distinct_coexisting_cache_key(monkeypatch) -> None:
    from lawvm.finland.llm_backends import llm_adjudicator as la
    from lawvm.finland.llm_backends import vision_producer as vp_mod
    from lawvm.finland.source_document.parsed_store import (
        parsed_ir_locator,
        resolve_pipeline,
    )

    monkeypatch.setattr(vp_mod.VisionPageProducer, "is_available", lambda self: True)
    monkeypatch.setattr(vp_mod.VisionPageProducer, "_resolve_model", lambda self: "test-vlm")
    monkeypatch.setattr(la.LlmWorkflowAdjudicator, "is_available", lambda self: True)

    s_span = resolve_pipeline(transcription_modality="struct_span")
    s_full = resolve_pipeline(transcription_modality="struct_full")
    s_patch = resolve_pipeline(transcription_modality="struct_patch")
    assert "+wire=structbuild.v1" in s_span.version and "+leaf=span" in s_span.version
    assert "+leaf=full" in s_full.version
    assert "+leaf=patch" in s_patch.version
    # Every struct lane gets a page-element producer (one shared grammar).
    assert s_span.page_element_producer is not None
    # Each leaf lane's key COEXISTS as a distinct content-addressed record.
    keys = {
        parsed_ir_locator("z" * 64, s.pipeline_id, s.version)
        for s in (s_span, s_full, s_patch)
    }
    assert len(keys) == 3


def test_image_locator_shares_the_ir_record_prefix() -> None:
    from lawvm.finland.source_document.parsed_store import (
        parsed_image_locator,
        parsed_ir_locator,
    )

    ir_loc = parsed_ir_locator("d" * 64, "adjudicated_vision", "v1")
    img_loc = parsed_image_locator("d" * 64, "adjudicated_vision", "v1", "0003.png")
    # The image blob lives under the SAME per-(source,pipeline,version) prefix.
    assert img_loc == ir_loc + "/0003.png"


# --------------------------------------------------------------------------- #
# Real image extraction + content-addressed storage (PIL-generated PDF)       #
# --------------------------------------------------------------------------- #


def _one_image_pdf() -> bytes:
    Image = pytest.importorskip("PIL.Image")
    import io as _io

    img = Image.new("RGB", (120, 80), (20, 120, 200))
    buf = _io.BytesIO()
    img.save(buf, format="PDF")  # embeds the raster as an image XObject
    return buf.getvalue()


def test_real_embedded_image_is_content_addressed_by_sha256_of_raw_bytes() -> None:
    import hashlib

    pytest.importorskip("pypdfium2")
    from lawvm.finland.source_document.page_elements import PageElementProducer

    pdf = _one_image_pdf()
    pe = PageElementProducer().page_elements(pdf, 1)
    assert len(pe.images) == 1
    img = pe.images[0]
    # The digest is a PURE function of the stored raw bytes (content-addressing).
    assert img.element.digest == hashlib.sha256(img.raw_bytes).hexdigest()
    assert img.element.width > 0 and img.element.height > 0
    assert img.element.media_type.startswith("image/")


def test_struct_ingest_stores_image_blobs_and_stitches_the_ir_locator(tmp_path) -> None:
    import hashlib
    from datetime import datetime as _dt, timezone

    pytest.importorskip("pypdfium2")
    from lawvm.finland.source_document.page_elements import PageElementProducer
    from lawvm.finland.source_document.parsed_store import (
        ParsedIrStore,
        PipelineSpec,
        parse_struct_pdf_to_ir,
    )

    pdf = _one_image_pdf()
    manifestation = SourceManifestation(
        artifact_digest=hashlib.sha256(pdf).hexdigest(),
        source_bytes=pdf,
        locator="syn.pdf",
        source_role="government_proposal_draft",
        fetched_at=_dt(2026, 1, 1, tzinfo=timezone.utc),
        media_type="application/pdf",
    )

    class _FakeStructVision:
        def is_available(self) -> bool:
            return True

        def propose_page_struct(self, man, pn, pe, *, leaf_mode="span"):
            wire = f"1 IMAGE 0 I1{US}2 TRANSCRIBE 1 T: baked caption{US}"
            build = parse_struct_wire(wire, pe.lines, [i.element for i in pe.images])
            return StructPageResult(build=build, raw_content=wire, images=pe.images)

    spec = PipelineSpec(
        pipeline_id="adjudicated_vision",
        version="vision=fake+adj+wire=structbuild.v1+rasterdpi=200+leaf=span+compose.v1",
        vision=_FakeStructVision(),
        adjudicator=None,
        transcription_modality="struct_span",
        page_element_producer=PageElementProducer(),
    )
    store = ParsedIrStore(str(tmp_path / "imgs.farchive"))
    try:
        record = parse_struct_pdf_to_ir(manifestation, spec, store)
        manifest_imgs = record.manifest["image_manifest"]
        assert len(manifest_imgs) == 1
        entry = manifest_imgs[0]
        # The blob is stored and byte-identical (content-addressed).
        blob = store.get_image(entry["locator"])
        assert blob is not None
        assert hashlib.sha256(blob).hexdigest() == entry["digest"]
        # The blob shares the IR record prefix and is {N}-indexed.
        assert entry["locator"].endswith("/0001.png")
        # The IR IMAGE node carries image_locator mapping I{1} → its blob 1:1.
        node = cast(dict, _find_ir_image(record.ir))
        assert node is not None
        assert node["attrs"]["image_locator"] == entry["locator"]
        # 0x1F terminator compliance is tracked (this fake emits it → 100%).
        assert record.manifest["terminator_compliance"]["rate"] == 1.0
    finally:
        store.close()


def _find_ir_image(node) -> object:
    if node.get("attrs", {}).get("image_digest"):
        return node
    for c in node.get("children", []):
        r = _find_ir_image(c)
        if r is not None:
            return r
    return None

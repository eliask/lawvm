"""C19 marginal-note x-coordinate segmentation + PDF replay-base admission.

Pins three net-new modules of the second UK-PDF increment (#177):

  1. ``pdf_layout_uk`` — the x-coordinate marginal-note segmenter: detects the
     King's-Printer right-hand side-note column by geometry (right x-band +
     smaller face), excises it from the body stream, and clusters it into
     per-section notes anchored by ``(page, y_top)``.
  2. ``pdf_grammar.uk_layout_to_ir`` — binds each segmented side-note onto its
     vertically-nearest section as a ``heading`` (the PDF analogue of the XML
     loader's ``P1group/Title`` heading carrier), returning any unbindable note
     as a typed residual.
  3. ``pdf_replay_base`` — re-shapes the PDF-derived body into a
     replay-admissible ``IRStatute`` (schedules lifted into ``supplements``) so
     UK replay consumes a PDF base identically to an XML base.

Golden equivalence (the #190 NZ-HTML pattern): a hand-authored Act expressed once
as CLML XML and once as a segmented PDF layout must yield the same
replay-normative statute shape.  A real-corpus smoke (env-gated on a local PDF
sample dir) proves the segmenter on the actual Bills of Exchange Act 1882.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from lawvm.core.ir import IRNode, IRStatute, LegalAddress, LegalOperation, OperationSource
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.uk_legislation.pdf_grammar import uk_layout_to_ir
from lawvm.uk_legislation.pdf_layout_uk import (
    BodyLine,
    MarginalNote,
    UKPdfLayout,
    _assemble_body_lines,
    _cluster_marginal_notes,
    _modal_body_height,
)
from lawvm.uk_legislation.pdf_replay_base import (
    _split_body_and_schedules,
    pdf_ir_to_replay_base,
    uk_pdf_layout_to_replay_base,
)


# ---------------------------------------------------------------------------
# Word-geometry helpers
# ---------------------------------------------------------------------------


def _word(text: str, x0: float, top: float, height: float, page: int = 0) -> dict:
    return {"text": text, "x0": x0, "x1": x0 + 5 * len(text),
            "top": top, "height": height, "_page": page}


def test_modal_body_height_is_the_dominant_face() -> None:
    words = [_word("a", 40, 100, 11.0) for _ in range(20)]
    words += [_word("note", 400, 100, 8.0) for _ in range(3)]
    assert _modal_body_height(words) == 11.0


def test_marginal_notes_cluster_by_vertical_run() -> None:
    # Two stacked side-notes at different y; each spans multiple lines.
    marginal = [
        _word("Definition", 400, 100, 8.0),
        _word("of", 400, 108, 8.0),
        _word("firm", 400, 116, 8.0),
        # a big vertical gap -> a new note
        _word("Meaning", 400, 300, 8.0),
        _word("of", 400, 308, 8.0),
        _word("term", 400, 316, 8.0),
    ]
    notes = _cluster_marginal_notes(marginal)
    assert [n.text for n in notes] == ["Definition of firm", "Meaning of term"]
    assert notes[0].y_top == 100 and notes[1].y_top == 300


def test_bare_punctuation_notes_are_dropped() -> None:
    marginal = [_word(";", 400, 100, 8.0), _word(":", 400, 300, 8.0)]
    assert _cluster_marginal_notes(marginal) == []


def test_assemble_body_lines_orders_by_page_then_y_then_x() -> None:
    words = [
        _word("world", 80, 100, 11.0, page=0),
        _word("Hello", 40, 100, 11.0, page=0),
        _word("next", 40, 120, 11.0, page=0),
        _word("later", 40, 100, 11.0, page=1),
    ]
    lines = _assemble_body_lines(words)
    assert [b.text for b in lines] == ["Hello world", "next", "later"]
    assert lines[-1].page_num == 1


# ---------------------------------------------------------------------------
# Marginal-note binding: side-note -> nearest section heading
# ---------------------------------------------------------------------------


def _layout_two_sections_with_notes() -> UKPdfLayout:
    return UKPdfLayout(
        body_lines=("1. A thing is defined here.", "2. Firm means the collective."),
        positioned_body_lines=(
            BodyLine("1. A thing is defined here.", 0, 100.0),
            BodyLine("2. Firm means the collective.", 0, 200.0),
        ),
        marginal_notes=(
            MarginalNote("Definition of thing", 0, 100.0),
            MarginalNote("Meaning of firm", 0, 200.0),
        ),
        detected=True,
    )


def test_marginal_note_binds_to_nearest_section_as_heading() -> None:
    body, unbound = uk_layout_to_ir(_layout_two_sections_with_notes())
    sections = [c for c in body.children if c.kind is IRNodeKind.SECTION]
    assert [s.label for s in sections] == ["1", "2"]
    headings = {
        s.label: next(c.text for c in s.children if c.kind is IRNodeKind.HEADING)
        for s in sections
    }
    assert headings == {"1": "Definition of thing", "2": "Meaning of firm"}
    assert unbound == []
    # The transient anchor attr must not leak into the result.
    assert all("_pdf_anchor" not in s.attrs for s in sections)


def test_unbindable_marginal_note_is_a_typed_residual_not_dropped() -> None:
    # A note far below any section opener (no section within the bind window).
    layout = UKPdfLayout(
        body_lines=("1. Body of section one.",),
        positioned_body_lines=(BodyLine("1. Body of section one.", 0, 100.0),),
        marginal_notes=(MarginalNote("Orphan side-note", 5, 700.0),),
        detected=True,
    )
    _, unbound = uk_layout_to_ir(layout)
    assert len(unbound) == 1
    assert unbound[0].text == "Orphan side-note"


def test_layout_without_notes_yields_no_headings() -> None:
    layout = UKPdfLayout(
        body_lines=("1. Section body.",),
        positioned_body_lines=(BodyLine("1. Section body.", 0, 100.0),),
        marginal_notes=(),
        detected=False,
    )
    body, unbound = uk_layout_to_ir(layout)
    section = next(c for c in body.children if c.kind is IRNodeKind.SECTION)
    assert not any(c.kind is IRNodeKind.HEADING for c in section.children)
    assert unbound == []


# ---------------------------------------------------------------------------
# Replay-base admission: schedules -> supplements; executor accepts
# ---------------------------------------------------------------------------


def test_split_body_and_schedules_lifts_top_level_schedules() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(kind=IRNodeKind.SECTION, label="1", text="One."),
            IRNode(kind=IRNodeKind.SCHEDULE, label="1", text="Sched."),
        ),
    )
    new_body, schedules = _split_body_and_schedules(body)
    assert [c.kind for c in new_body.children] == [IRNodeKind.SECTION]
    assert len(schedules) == 1 and schedules[0].kind is IRNodeKind.SCHEDULE


def test_pdf_ir_admitted_as_replay_base_shape_matches_xml_loader() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        attrs={"source_lane": "pdf"},
        children=(
            IRNode(kind=IRNodeKind.SECTION, label="1", text="One."),
            IRNode(kind=IRNodeKind.SECTION, label="2", text="Two."),
            IRNode(kind=IRNodeKind.SCHEDULE, label="1", text="Sched."),
        ),
    )
    base = pdf_ir_to_replay_base(body, statute_id="ukpga/test/1", title="T")
    assert isinstance(base, IRStatute)
    # Body free of schedules; schedules in supplements (XML-loader shape).
    assert all(c.kind is not IRNodeKind.SCHEDULE for c in base.body.children)
    assert len(base.supplements) == 1
    assert base.metadata["source_lane"] == "pdf"


def test_pdf_base_admitted_into_replay_and_op_applies() -> None:
    base = uk_pdf_layout_to_replay_base(
        _layout_two_sections_with_notes(),
        statute_id="ukpga/test/1",
        title="Example Act",
    )
    from lawvm.uk_legislation.replay_executor import replay_uk_ops

    op = LegalOperation(
        op_id="pdf-base-repeal-s1",
        action=StructuralAction.REPEAL,
        target=LegalAddress(path=(("section", "1"),)),
        source=OperationSource(statute_id="ukpga/2026/1", title="Amending"),
        sequence=1,
    )
    out = replay_uk_ops(base, [op])
    remaining = [c.label for c in out.body.children if c.kind is IRNodeKind.SECTION]
    assert "1" not in remaining
    assert "2" in remaining


# ---------------------------------------------------------------------------
# Golden equivalence: XML twin vs segmented PDF layout (the #190 pattern)
# ---------------------------------------------------------------------------

_ACT_XML = b"""<?xml version="1.0"?>
<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation"
             xmlns:dc="http://purl.org/dc/elements/1.1/">
<Metadata><dc:title>Example Act 1885</dc:title></Metadata>
<Primary><Body>
<P1group><Title>Definition of thing</Title>
  <P1><Pnumber>1</Pnumber><P1para><Text>A thing is defined here.</Text></P1para></P1>
</P1group>
<P1group><Title>Meaning of firm</Title>
  <P1><Pnumber>2</Pnumber><P1para><Text>Firm means the collective.</Text></P1para></P1>
</P1group>
</Body></Primary></Legislation>"""


def _normative_shape(statute: IRStatute):
    """Replay-normative projection: (section, label, heading, text, subsections).

    Structural wrappers (``p1group``/``crossheading``) that legitimately differ
    between the XML and PDF manifestations are transparent — only the fields
    downstream replay reads (section identity, its side-note-as-heading, its
    text, its subsection labels/text) are compared. Schedules project as
    ``(schedule, label)``.
    """
    out: list = []

    def walk(node: IRNode) -> None:
        if node.kind is IRNodeKind.SECTION:
            heading = next(
                (c.text for c in node.children if c.kind is IRNodeKind.HEADING), ""
            )
            subs = tuple(
                (c.label or "", c.text)
                for c in node.children
                if c.kind is IRNodeKind.SUBSECTION
            )
            out.append(("section", node.label, heading, node.text, subs))
        elif node.kind is IRNodeKind.SCHEDULE:
            out.append(("schedule", node.label or "", "", "", ()))
        for c in node.children:
            walk(c)

    walk(statute.body)
    for s in statute.supplements:
        walk(s)
    return out


def test_xml_and_pdf_manifestations_yield_equivalent_replay_base() -> None:
    from lawvm.uk_legislation.uk_grafter import parse_uk_statute_ir_bytes

    xml_base = parse_uk_statute_ir_bytes(_ACT_XML, statute_id="ukpga/test/1")
    pdf_base = uk_pdf_layout_to_replay_base(
        _layout_two_sections_with_notes(),
        statute_id="ukpga/test/1",
        title="Example Act 1885",
    )
    assert _normative_shape(pdf_base) == _normative_shape(xml_base)


# ---------------------------------------------------------------------------
# Real-corpus smoke (env-gated on a local UK PDF sample dir)
# ---------------------------------------------------------------------------

# The acquired C19 PDFs are not committed (bulk acquisition is deferred). Point
# ``LAWVM_UK_PDF_SAMPLE_DIR`` at a dir holding ``ukpga_18820061.pdf`` (Bills of
# Exchange Act 1882) to run the geometric segmenter on the real scan.
_SAMPLE_DIR = os.environ.get("LAWVM_UK_PDF_SAMPLE_DIR")
_SAMPLE_PDF = Path(_SAMPLE_DIR) / "ukpga_18820061.pdf" if _SAMPLE_DIR else None


@pytest.mark.skipif(
    not (_SAMPLE_PDF and _SAMPLE_PDF.exists()),
    reason="UK PDF sample not present (set LAWVM_UK_PDF_SAMPLE_DIR)",
)
def test_real_c19_marginal_column_is_segmented() -> None:
    pytest.importorskip("pdfplumber")
    from lawvm.uk_legislation.pdf_layout_uk import segment_uk_pdf_layout

    assert _SAMPLE_PDF is not None
    layout = segment_uk_pdf_layout(_SAMPLE_PDF.read_bytes())
    assert layout is not None
    # The C19 side-note column must be detected and yield real notes.
    assert layout.detected
    assert len(layout.marginal_notes) >= 20

    body, unbound = uk_layout_to_ir(layout, source_ref="ukpga/Vict/45-46/61")
    headings = {}

    def collect(node: IRNode) -> None:
        if node.kind is IRNodeKind.SECTION:
            h = next((c.text for c in node.children if c.kind is IRNodeKind.HEADING), "")
            if h:
                headings.setdefault(node.label, h)
        for c in node.children:
            collect(c)

    collect(body)
    # Known clean side-notes must land on the right sections (OCR-tolerant
    # substring match). These are exact XML headings for 1882/61.
    def _norm(s: str) -> str:
        return "".join(ch.lower() for ch in s if ch.isalnum())

    assert "drawee" in _norm(headings.get("6", ""))
    assert "delivery" in _norm(headings.get("21", ""))
    # And the base must be admissible to replay.
    base = uk_pdf_layout_to_replay_base(
        layout, statute_id="ukpga/Vict/45-46/61", title="Bills of Exchange Act 1882"
    )
    assert base.metadata["source_lane"] == "pdf"
    assert all(c.kind is not IRNodeKind.SCHEDULE for c in base.body.children)

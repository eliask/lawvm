"""Tests for the Finlex attachment-PDF IR parser (``lawvm.finland.attachment_ir``).

Four test classes per AGENTS.md §2.9 pinning the parser:

  1. :func:`test_synthetic_parse_structure` — a synthetic paragraph+items+table
     fixture exercising every recogniser branch in isolation.
  2. :func:`test_corpus_6448_structure` — the real Finlex attachment
     ``6448.pdf`` (extracted by the task's ``farchive extract`` recipe); skips
     if the PDF has not yet been extracted or ``pdftotext`` is missing.
  3. :func:`test_negative_empty_string` — empty input yields a typed
     empty ``HCONTAINER``, never an error.
  4. :func:`test_no_leak_6448` — every non-empty line in the pdftotext
     output is either a recognised skip pattern or present in the IR
     tree's text/attrs (§0 total-accounting; §1.10 no silent drop).

Plus :func:`test_synthetic_separator_marker_table` pinning the boundary-
marker TABLE behaviour and :func:`test_liite_without_osa_auto_creates_appendix`
pinning the §1.3 fallback.
"""

from __future__ import annotations

import shutil
import subprocess
import textwrap
from pathlib import Path
from typing import Any, List

import pytest

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.tree_ops import normalized_label_key
from lawvm.finland.attachment_ir import (
    _ITEM_ALPHA_RE,
    _KOHTA_NUM_RE,
    _LIITE_RE,
    _LUKU_RE,
    _OSA_RE,
    _SECTION_RE,
    _is_page_num_line,
    _looks_like_spine,
    _PARA_NUM_RE,
    _TABLE_SEP_RE,
    _TAULUKKO_RE,
    iter_tree,
    pdf_text_to_ir_node,
)


# ---------------------------------------------------------------------------
# Synthetic test fixture: exercises every recogniser branch once.
# ---------------------------------------------------------------------------

SYNTHETIC_PDFTOTEXT = textwrap.dedent(
    """\
    1
    219/2014

    I OSA

    Liite 1

    SYNTHETIC ALL-CAPS HEADING

    1. First paragraph with a continuation
    line of paragraph one.
    2. Second paragraph with items:
    a) first item;
    b) second item.
    3. Third paragraph (no continuation).

    Taulukko 1
    COLUMN A   COLUMN B
    value 1    value 2
    value 3    value 4
    –––––––––––
    """
)


def test_synthetic_parse_structure() -> None:
    root = pdf_text_to_ir_node(SYNTHETIC_PDFTOTEXT, source_ref="synthetic")

    # Root: HCONTAINER with source_ref carried through to attrs.
    assert root.kind == IRNodeKind.HCONTAINER
    assert root.attrs.get("source_ref") == "synthetic"

    # First (and only) APPENDIX at top-level.
    assert len(root.children) == 1
    appendix = root.children[0]
    assert appendix.kind == IRNodeKind.APPENDIX
    assert appendix.label == "osa_I"
    # Source text preserved so the no-leak invariant can verify it.
    assert appendix.attrs["source_text"] == "I OSA"

    # SCHEDULE under APPENDIX.
    assert len(appendix.children) == 1
    schedule = appendix.children[0]
    assert schedule.kind == IRNodeKind.SCHEDULE
    assert schedule.label == "liite_1"
    assert schedule.attrs["source_text"] == "Liite 1"

    # Direct children of schedule: HEADING, 3 PARAGRAPHs, 1 TABLE.
    sched_kinds = [c.kind for c in schedule.children]
    assert sched_kinds == [
        IRNodeKind.HEADING,
        IRNodeKind.PARAGRAPH,
        IRNodeKind.PARAGRAPH,
        IRNodeKind.PARAGRAPH,
        IRNodeKind.TABLE,
    ]

    heading = schedule.children[0]
    assert heading.text == "SYNTHETIC ALL-CAPS HEADING"

    # Paragraph 1: continuation lines joined into a single text payload.
    p1 = schedule.children[1]
    assert p1.label == "1"
    assert p1.attrs["label_kind"] == "numeric"
    assert p1.attrs["source_text"] == "1. "
    assert "First paragraph" in p1.text
    assert "continuation" in p1.text
    assert "line of paragraph one" in p1.text
    # Joined with single space (no newline preserved from pdftotext hard-wrap).
    assert "\n" not in p1.text

    # Paragraph 2 with items a) and b).
    p2 = schedule.children[2]
    assert p2.label == "2"
    assert p2.text == "Second paragraph with items:"
    items = [c for c in p2.children if c.kind == IRNodeKind.ITEM]
    assert [i.label for i in items] == ["a", "b"]
    assert items[0].text == "first item;"
    assert items[1].text == "second item."

    # Paragraph 3: single-line.
    p3 = schedule.children[3]
    assert p3.label == "3"
    assert p3.text == "Third paragraph (no continuation)."

    # Table: 1 caption HEADING + 3 ROWs; first row is HEADER_CELL.
    table = schedule.children[4]
    assert table.kind == IRNodeKind.TABLE
    assert table.label == "taulukko_1"
    assert table.attrs["caption"] == "Taulukko 1"

    table_children = [c for c in table.children]
    assert table_children[0].kind == IRNodeKind.HEADING
    assert table_children[0].text == "Taulukko 1"

    rows = [c for c in table_children if c.kind == IRNodeKind.ROW]
    assert len(rows) == 3

    header_cells = [c for c in rows[0].children if c.kind == IRNodeKind.HEADER_CELL]
    assert [c.text for c in header_cells] == ["COLUMN A", "COLUMN B"]
    # Sanity: HEADER_CELL is the only kind on row 0.
    assert all(c.kind == IRNodeKind.HEADER_CELL for c in rows[0].children)

    body_cells_row1 = [c for c in rows[1].children if c.kind == IRNodeKind.CELL]
    assert [c.text for c in body_cells_row1] == ["value 1", "value 2"]
    body_cells_row2 = [c for c in rows[2].children if c.kind == IRNodeKind.CELL]
    assert [c.text for c in body_cells_row2] == ["value 3", "value 4"]


def test_synthetic_separator_marker_table_when_only_separator() -> None:
    """A `–––` separator with no preceding table content emits an empty
    marker TABLE so the separator is owned in the IR (§0 total-accounting)."""
    fixture = textwrap.dedent(
        """\
        I OSA

        Liite 1

        HEADING

        1. A paragraph.

        –––––––––––––
        """
    )
    root = pdf_text_to_ir_node(fixture, source_ref="sep-marker")
    # Find the marker TABLE (in the schedule).
    tables = [
        n for n in iter_tree(root) if n.kind == IRNodeKind.TABLE
    ]
    assert len(tables) == 1
    marker = tables[0]
    assert marker.attrs.get("boundary_marker") is True
    assert marker.attrs["source_text"].startswith("–––")
    # No ROW children — the separator bounded no real table content.
    rows = [c for c in marker.children if c.kind == IRNodeKind.ROW]
    assert rows == []


def test_liite_without_osa_auto_creates_appendix() -> None:
    """A ``Liite`` appearing without a preceding ``I OSA`` heading is a real
    attachment shape (single-page attachments sometimes skip the ``osa``
    header). Per §1.3, an ITEM/child must not fall back to whole-node
    replacement of the schedule; we auto-create a typed APPENDIX parent with
    a witness.attrs entry so downstream audits can see the normalisation."""
    fixture = textwrap.dedent(
        """\
        Liite 1

        HEADING

        1. A paragraph.
        """
    )
    root = pdf_text_to_ir_node(fixture)
    assert root.kind == IRNodeKind.HCONTAINER
    appendix = root.children[0]
    assert appendix.kind == IRNodeKind.APPENDIX
    assert appendix.label == "osa_auto"
    assert appendix.attrs["auto_created"] == "no_osa_header_for_liite"
    schedule = appendix.children[0]
    assert schedule.kind == IRNodeKind.SCHEDULE
    assert schedule.label == "liite_1"


# ---------------------------------------------------------------------------
# Corpus test against 6448.pdf (extracted per task description).
# Skips if the PDF has not been extracted or pdftotext is missing.
# ---------------------------------------------------------------------------


_PDF_PATH = Path("/tmp/6448.pdf")


@pytest.fixture(scope="module")
def real_6448_pdftotext() -> str:
    """Yield the pdftotext output of 6448.pdf.

    Skips if /tmp/6448.pdf has not been extracted (per the task's
    ``uv run farchive extract`` recipe) or ``pdftotext`` is unavailable.
    """
    if not _PDF_PATH.exists():
        pytest.skip(
            f"corpus PDF {_PDF_PATH} not found; extract with: "
            "uv run farchive extract data/finlex.farchive "
            "\"finlex://sd-cons/2002/1248/fin@20141291/media/6448.pdf\" "
            f"-o {_PDF_PATH}"
        )
    if not shutil.which("pdftotext"):
        pytest.skip("pdftotext binary not on PATH")
    proc = subprocess.run(
        ["pdftotext", str(_PDF_PATH), "-"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert proc.stdout, "pdftotext returned empty output for 6448.pdf"
    return proc.stdout


def test_corpus_6448_structure(real_6448_pdftotext: str) -> None:
    text = real_6448_pdftotext
    root = pdf_text_to_ir_node(text, source_ref="6448.pdf")

    # Root: HCONTAINER (NOT APPENDIX — APPENDIX is the first child).
    assert root.kind == IRNodeKind.HCONTAINER
    assert root.attrs.get("source_ref") == "6448.pdf"

    appendix = next(
        (n for n in root.children if n.kind == IRNodeKind.APPENDIX), None
    )
    assert appendix is not None, "expected an APPENDIX child for 'I OSA'"
    assert appendix.label == "osa_I"

    schedule = next(
        (n for n in appendix.children if n.kind == IRNodeKind.SCHEDULE), None
    )
    assert schedule is not None, "expected a SCHEDULE child for 'Liite 1'"
    assert schedule.label == "liite_1"

    # Contains at least one HEADING (the ALL-CAPS vaatimukset line).
    headings = [
        n for n in iter_tree(schedule) if n.kind == IRNodeKind.HEADING
    ]
    assert headings, "expected at least one HEADING"
    assert any("VAATIMUKSET" in h.text for h in headings), (
        f"expected the ALL-CAPS vaatimukset heading; got {headings!r}"
    )

    # Numbered paragraphs 1-7 all present.
    paragraphs = [
        n for n in iter_tree(schedule) if n.kind == IRNodeKind.PARAGRAPH
    ]
    labels = {p.label for p in paragraphs if p.label and p.label.isdigit()}
    assert {"1", "2", "3", "4", "5", "6", "7"} <= labels, (
        f"expected paragraphs 1..7; found labels={sorted(labels)}"
    )

    # Paragraph 2 contains items a-d.
    p2 = next(p for p in paragraphs if p.label == "2")
    items = [n for n in iter_tree(p2) if n.kind == IRNodeKind.ITEM]
    assert [i.label for i in items] == ["a", "b", "c", "d"]

    # At least one TABLE node — 6448.pdf ends with a `–––` separator that
    # the parser owns as a boundary-marker TABLE so the no-leak invariant
    # holds (§0 total-accounting).
    tables = [n for n in iter_tree(schedule) if n.kind == IRNodeKind.TABLE]
    assert tables, "expected at least one TABLE (boundary marker for trailing separator)"
    # The trailing separator is owned via attrs["source_text"].
    assert any(
        t.attrs.get("boundary_marker") is True
        and t.attrs.get("source_text", "").startswith("–––")
        for t in tables
    ), "expected the trailing-separator TABLE node to be a boundary_marker"


# ---------------------------------------------------------------------------
# Negative test: empty input → empty HCONTAINER.
# ---------------------------------------------------------------------------


def test_negative_empty_string() -> None:
    root = pdf_text_to_ir_node("")
    assert root.kind == IRNodeKind.HCONTAINER
    # No attachment_label supplied → label is None (not the empty string).
    assert root.label is None
    assert root.text == ""
    assert root.children == ()
    # attrs is empty (FrozenDict by IRNode post-init).
    assert dict(root.attrs) == {}


# ---------------------------------------------------------------------------
# No-leak invariant on 6448.pdf (§0 total-accounting; §1.10 fail loud).
# ---------------------------------------------------------------------------


def _collect_tree_text_fragments(root: IRNode) -> List[str]:
    """Collect every string value the tree carries: node text AND every
    string-typed attr value (so structural/separator source_text fields
    also count toward "the line is owned in the IR")."""
    fragments: List[str] = []

    def walk(node: IRNode) -> None:
        if node.text:
            fragments.append(node.text)
        for value in node.attrs.values():
            if isinstance(value, str) and value:
                fragments.append(value)
        for child in node.children:
            walk(child)

    walk(root)
    return fragments


def test_no_leak_6448(real_6448_pdftotext: str) -> None:
    text = real_6448_pdftotext
    root = pdf_text_to_ir_node(text, source_ref="6448.pdf")
    fragments = _collect_tree_text_fragments(root)

    def is_owned(line: str) -> bool:
        """Return True if ``line`` is present as a substring of any tree
        fragment (a node's text content or a string attr value like
        ``source_text``)."""
        return any(line in frag for frag in fragments)

    structural_patterns = (
        _OSA_RE,
        _LIITE_RE,
        _TAULUKKO_RE,
        _PARA_NUM_RE,
        _ITEM_ALPHA_RE,
    )

    for raw in text.split("\n"):
        stripped = raw.replace("\f", "").strip()
        if not stripped:
            continue

        # Skip patterns: page number / running header.
        if _is_page_num_line(stripped):
            continue

        # Table separator: must be owned via attributes of the boundary-
        # marker TABLE node.
        if _TABLE_SEP_RE.match(stripped):
            assert is_owned(stripped), (
                f"no-leak violation: separator {stripped!r} not owned in IR"
            )
            continue

        # Structural header lines: their full text is owned in attrs via
        # ``source_text`` (OSA/Liite/Taulukko) or via the matched prefix
        # ``source_text`` (PARA_NUM '1. ' / ITEM_ALPHA 'a) ').
        matched_prefix = None
        for pat in structural_patterns:
            m = pat.match(stripped)
            if m:
                matched_prefix = m.group(0)
                break

        if matched_prefix is not None:
            remainder = stripped[len(matched_prefix):]
            # Either the full line is owned (OSA/Liite store source_text as
            # the full line; PARA/ITEM store the prefix only), OR the
            # remainder is owned as a node's text content.
            full_ok = is_owned(stripped)
            prefix_ok = is_owned(matched_prefix)
            remainder_ok = (not remainder) or is_owned(remainder)
            assert full_ok or (prefix_ok and remainder_ok), (
                f"no-leak violation: structural line {stripped!r} "
                f"not fully owned (full={full_ok}, prefix={prefix_ok}, "
                f"remainder={remainder_ok})"
            )
            continue

        # Plain free text or ALL-CAPS heading: must appear as a substring
        # of some node's text content (joined paragraph text preserves
        # each input line as a substring of the joined result).
        assert is_owned(stripped), (
            f"no-leak violation: line {stripped!r} not in tree text fragments"
        )


# ---------------------------------------------------------------------------
# Statute-spine recogniser (spine mode). Fixture derived from the ``2011/38``
# (Valtioneuvoston asetus ilmanlaadusta) pilot: a clean Regime-A PDF with a
# recoverable ``1 §``…``24 §`` spine, per-§ Title-case headings, and real
# Finnish ``N)`` kohta items.
# ---------------------------------------------------------------------------

# Trimmed to sections 1, 2, 3, 24 (the pilot's shape is uniform across the
# spine; four sections exercise the chapterless spine, per-§ heading absorb,
# numeric-kohta grouping, and non-contiguous labels 3 -> 24).
SPINE_2011_38_PDFTOTEXT = textwrap.dedent(
    """\
    Valtioneuvoston asetus ilmanlaadusta

    Valtioneuvoston paatoksen mukaisesti saadetaan.

    1 §

    Tarkoitus

    Talla asetuksella pannaan taytantoon ilmanlaadusta annettu direktiivi.

    2 §

    Maaritelmat

    Tassa asetuksessa tarkoitetaan:

    1) ilmansaasteella ilmassa olevaa ainetta;

    2) tasolla ilman epapuhtauden pitoisuutta;

    3) arvioinnilla mittausta ja laskentaa.

    3 §

    Rikkidioksidin raja-arvot

    Rikkidioksidin pitoisuudet eivat saa ylittaa raja-arvoja.

    24 §

    Voimaantulo

    Tama asetus tulee voimaan 1 paivana tammikuuta 2011.
    """
)


def test_spine_2011_38_section_spine() -> None:
    """The ``2011/38`` pilot parses into a real SECTION spine (fixes G6): the
    parser emits SECTION IRNodes (not APPENDIX/SCHEDULE/P) that replay's
    ``.label`` structure graft can target."""
    root = pdf_text_to_ir_node(SPINE_2011_38_PDFTOTEXT, source_ref="2011/38")

    # Auto-detected as a spine.
    assert root.kind == IRNodeKind.HCONTAINER
    assert root.attrs.get("spine_mode") is True
    assert root.attrs.get("source_ref") == "2011/38"

    sections = [n for n in iter_tree(root) if n.kind == IRNodeKind.SECTION]
    # Fixes G1: `N §` lines become SECTION nodes, not free-text P fallback.
    assert [s.label for s in sections] == ["1", "2", "3", "24"]

    # Labels are the bare number (no `§`), so normalized_label_key parity with
    # replay's structure graft holds (a live body section keyed `section:1`).
    assert [normalized_label_key(s.label) for s in sections] == ["1", "2", "3", "24"]

    # eId scheme: chapterless spine → `sec_N`.
    assert [s.attrs.get("eid") for s in sections] == [
        "sec_1",
        "sec_2",
        "sec_3",
        "sec_24",
    ]

    # Per-§ Title-case heading absorbed as a HEADING child of the section.
    def section_heading(sec: IRNode) -> str:
        headings = [c for c in sec.children if c.kind == IRNodeKind.HEADING]
        assert len(headings) == 1, f"expected 1 heading under {sec.label}"
        return headings[0].text

    assert section_heading(sections[0]) == "Tarkoitus"
    assert section_heading(sections[1]) == "Maaritelmat"
    assert section_heading(sections[2]) == "Rikkidioksidin raja-arvot"
    assert section_heading(sections[3]) == "Voimaantulo"

    # § 1 body: one positional SUBSECTION carrying the operative sentence.
    s1_subs = [c for c in sections[0].children if c.kind == IRNodeKind.SUBSECTION]
    assert [c.label for c in s1_subs] == ["1"]
    assert s1_subs[0].attrs.get("positional") is True
    assert "Talla asetuksella" in s1_subs[0].text


def test_spine_2011_38_numeric_kohta_items() -> None:
    """Fixes G5: real Finnish ``N)`` kohta items become ITEM nodes grouped in
    the intro subsection (not lost to the alpha-only / dot-only arms)."""
    root = pdf_text_to_ir_node(SPINE_2011_38_PDFTOTEXT, source_ref="2011/38")
    section_2 = next(
        s
        for s in iter_tree(root)
        if s.kind == IRNodeKind.SECTION and s.label == "2"
    )

    items = [n for n in iter_tree(section_2) if n.kind == IRNodeKind.ITEM]
    assert [i.label for i in items] == ["1", "2", "3"]
    assert all(i.attrs.get("label_kind") == "numeric_kohta" for i in items)
    assert items[0].text == "ilmansaasteella ilmassa olevaa ainetta;"
    assert items[2].text == "arvioinnilla mittausta ja laskentaa."

    # All three kohta items live under ONE subsection (the intro
    # "Tassa asetuksessa tarkoitetaan:"), not fragmented across three
    # subsections by the pdftotext blank lines between them.
    subs_with_items = [
        sub
        for sub in section_2.children
        if sub.kind == IRNodeKind.SUBSECTION
        and any(c.kind == IRNodeKind.ITEM for c in sub.children)
    ]
    assert len(subs_with_items) == 1
    assert "Tassa asetuksessa tarkoitetaan:" in subs_with_items[0].text
    assert len([c for c in subs_with_items[0].children if c.kind == IRNodeKind.ITEM]) == 3


def test_spine_chapter_luku() -> None:
    """A ``N luku`` chapter header becomes a CHAPTER node parenting its
    sections; the optional trailing title is absorbed as a HEADING child."""
    fixture = textwrap.dedent(
        """\
        1 luku Yleiset saannokset

        1 §

        Soveltamisala

        Tata lakia sovelletaan.

        2 luku

        2 §

        Toimivalta

        Viranomainen paattaa asiasta.
        """
    )
    root = pdf_text_to_ir_node(fixture)
    assert root.attrs.get("spine_mode") is True

    chapters = [c for c in root.children if c.kind == IRNodeKind.CHAPTER]
    assert [c.label for c in chapters] == ["1", "2"]
    assert chapters[0].attrs.get("eid") == "chp_1"

    # Chapter 1 trailing title absorbed as HEADING.
    c1_headings = [c for c in chapters[0].children if c.kind == IRNodeKind.HEADING]
    assert [h.text for h in c1_headings] == ["Yleiset saannokset"]
    # Chapter 2 has no trailing title → no chapter-level HEADING before its §.
    c2_pre_section_headings = []
    for child in chapters[1].children:
        if child.kind == IRNodeKind.SECTION:
            break
        if child.kind == IRNodeKind.HEADING:
            c2_pre_section_headings.append(child.text)
    assert c2_pre_section_headings == []

    # Sections nest under their chapter, and eId carries the chapter segment.
    c1_sections = [c for c in chapters[0].children if c.kind == IRNodeKind.SECTION]
    assert [s.label for s in c1_sections] == ["1"]
    assert c1_sections[0].attrs.get("eid") == "chp_1__sec_1"
    c2_sections = [c for c in chapters[1].children if c.kind == IRNodeKind.SECTION]
    assert [s.label for s in c2_sections] == ["2"]
    assert c2_sections[0].attrs.get("eid") == "chp_2__sec_2"


def test_spine_section_letter_suffix_label() -> None:
    """A ``4a §`` letter-suffixed section keeps the suffix in the label so
    ``4`` and ``4a`` are distinct graft targets."""
    fixture = textwrap.dedent(
        """\
        4 §

        Otsikko

        Body of section four.

        4a §

        Lisays

        Body of section four-a.
        """
    )
    root = pdf_text_to_ir_node(fixture)
    sections = [n for n in iter_tree(root) if n.kind == IRNodeKind.SECTION]
    assert [s.label for s in sections] == ["4", "4a"]
    assert [normalized_label_key(s.label) for s in sections] == ["4", "4a"]


def test_spine_no_leak() -> None:
    """§0 total-accounting in spine mode: every non-empty, non-skip input line
    is owned by some node's text or a structural ``source_text`` attr."""
    root = pdf_text_to_ir_node(SPINE_2011_38_PDFTOTEXT, source_ref="2011/38")
    fragments = _collect_tree_text_fragments(root)

    def is_owned(line: str) -> bool:
        return any(line in frag for frag in fragments)

    for raw in SPINE_2011_38_PDFTOTEXT.split("\n"):
        stripped = raw.replace("\f", "").strip()
        if not stripped or _is_page_num_line(stripped):
            continue
        # `N §` marks are owned via SECTION source_text; `N)` prefixes via ITEM
        # source_text (remainder owned as item text); everything else is body
        # or heading text.
        m = _SECTION_RE.match(stripped)
        if m:
            assert is_owned(stripped), f"section mark {stripped!r} not owned"
            continue
        m = _KOHTA_NUM_RE.match(stripped)
        if m:
            remainder = stripped[m.end():]
            assert is_owned(remainder), f"kohta remainder {remainder!r} not owned"
            continue
        assert is_owned(stripped), f"spine line {stripped!r} not owned in IR"


# ---------------------------------------------------------------------------
# Backward-compat: a non-§ table/prose attachment MUST still parse in the
# unchanged appendix mode. Auto-detect must not regress it.
# ---------------------------------------------------------------------------


def test_backward_compat_table_attachment_stays_appendix_mode() -> None:
    """A known non-§ table attachment (Liite + Taulukko + `–––`) has no
    ``N §`` marker, so ``_looks_like_spine`` is False and it parses through
    the identical appendix path — APPENDIX/SCHEDULE/TABLE, never SECTION."""
    # No `N §` line anywhere → must NOT trip spine mode.
    assert _looks_like_spine(SYNTHETIC_PDFTOTEXT) is False

    root = pdf_text_to_ir_node(SYNTHETIC_PDFTOTEXT, source_ref="synthetic")
    # spine_mode attr is absent — the appendix path was taken.
    assert "spine_mode" not in root.attrs

    kinds = {n.kind for n in iter_tree(root)}
    # Appendix structure present; no spine kinds leaked in.
    assert IRNodeKind.APPENDIX in kinds
    assert IRNodeKind.SCHEDULE in kinds
    assert IRNodeKind.TABLE in kinds
    assert IRNodeKind.SECTION not in kinds
    assert IRNodeKind.CHAPTER not in kinds

    # Byte-for-byte identical to the appendix-mode reference: the top-level
    # child is still the APPENDIX, unchanged by the new dispatch.
    assert [c.kind for c in root.children] == [IRNodeKind.APPENDIX]
    appendix = root.children[0]
    assert appendix.label == "osa_I"
    schedule = appendix.children[0]
    assert schedule.label == "liite_1"
    assert [c.kind for c in schedule.children] == [
        IRNodeKind.HEADING,
        IRNodeKind.PARAGRAPH,
        IRNodeKind.PARAGRAPH,
        IRNodeKind.PARAGRAPH,
        IRNodeKind.TABLE,
    ]


def test_looks_like_spine_detection() -> None:
    """The spine detector fires on ``N §`` marks and only on them."""
    assert _looks_like_spine("1 §\n\nTarkoitus\n") is True
    assert _looks_like_spine("intro\n24 §\nbody") is True
    # Appendix-shaped inputs (no bare `N §` line) do NOT trip it.
    assert _looks_like_spine("Liite 1\n\n1. Kohta\na) alpha") is False
    assert _looks_like_spine("Taulukko 1\nCOL A   COL B\nx   y") is False
    # A `§` embedded mid-prose (not a bare `N §` line) does not count.
    assert _looks_like_spine("viitataan 5 §:aan ja jatketaan") is False


def _group1(pattern: Any, s: str) -> str:
    """Match ``s`` against ``pattern`` and return group(1); assert non-None so
    the runtime pins the match. ``pattern`` is a compiled classifier
    (``re.Pattern`` or ``PrefilteredPattern``), typed ``Any`` so this helper
    stays agnostic to the wrapper distinction."""
    m = pattern.match(s)
    assert m is not None, f"expected {s!r} to match"
    return str(m.group(1))


def test_spine_marker_patterns_unit() -> None:
    """Unit-pin the three new recogniser arms in isolation."""
    assert _group1(_SECTION_RE, "1 §") == "1"
    assert _group1(_SECTION_RE, "24 §") == "24"
    assert _group1(_SECTION_RE, "4a §") == "4a"
    assert _group1(_SECTION_RE, "1 §.") == "1"  # trailing dot tolerated
    assert _SECTION_RE.match("5 §:aan") is None  # inflected reference, not a mark

    assert _group1(_LUKU_RE, "2 luku") == "2"
    assert _group1(_LUKU_RE, "1 LUKU") == "1"
    assert _LUKU_RE.match("2 lukuisia") is None  # word-boundary guard

    assert _group1(_KOHTA_NUM_RE, "1)") == "1"
    assert _group1(_KOHTA_NUM_RE, "12) foo") == "12"
    assert _KOHTA_NUM_RE.match("a) foo") is None  # alpha item, not numeric kohta

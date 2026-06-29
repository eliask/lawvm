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
from typing import List

import pytest

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.attachment_ir import (
    _ITEM_ALPHA_RE,
    _LIITE_RE,
    _OSA_RE,
    _is_page_num_line,
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

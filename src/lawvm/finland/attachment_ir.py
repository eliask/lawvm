"""Structured-text IR parser for Finlex attachment PDFs.

Parses ``pdftotext`` output of Finlex attachment PDFs into LawVM
:class:`~lawvm.core.ir.IRNode` trees. Attachments must become first-class
IR (structured text in IRNode trees) BEFORE corrigenda apply against them,
so this recogniser is the entry waist for the attachment-content plane.

Discipline (per AGENTS.md):

- **Total accounting (§0).** Every non-empty input line ends up *owned*:
  page numbers and running headers are recognised *skip patterns*
  (carrying no legal content); table separator lines (``–––``) are owned
  via a typed ``TABLE`` boundary-marker node whose ``attrs["source_text"]``
  preserves the literal separator; every other non-empty line becomes the
  text content of a typed node (``PARAGRAPH`` / ``ITEM`` / ``P`` /
  ``HEADING`` / ``CELL`` / ``HEADER_CELL``).
- **Pure transform.** :func:`pdf_text_to_ir_node` takes a string and
  returns an :class:`IRNode`; no I/O, no external state, no filesystem
  access. The :class:`_Builder` mutable intermediary is module-private
  and frozen at the boundary.
- **Regex discipline (§2.4).** Each module-scope pattern here is a single
  fixed-string predicate (one phrase shape with one capture), NOT a family
  of related patterns. A regex stays the right IR for that scoped job;
  no per-provision regex construction, no N-overlapping ``re.finditer``
  scans.

The recogniser is structural-only: it does NOT decide legal action, target
scope, lifecycle, or mutation authority (§1.11). It produces a typed
inert tree; downstream phases (corrigendum application, lower) decide
mutations against it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional

import lxml.etree as etree

from lawvm.core.ir import IRNode
from lawvm.core.regex_safety import compile_classifier_regex
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.xml_parse import parse_corpus_xml


# ---------------------------------------------------------------------------
# Module-scope compiled patterns (§2.4: compile-at-module-scope via
# ``compile_classifier_regex`` — never raw ``re.compile``; classifier
# patterns must go through the safety lint + sound required-literal
# prefilter).
# ---------------------------------------------------------------------------

# Top-level Finnish division header "I OSA" / "II OSA" / "III OSA".
# ("osa" = part; Finlex attachments are typically organised under one or
# more "osa" divisions before per-attachment "Liite" entries.)
_OSA_RE = compile_classifier_regex(
    r"^(I{1,3})\s+OSA\s*$", classifier_id="lawvm.finland.attachment_ir.osa"
)

# Schedule-entry header "Liite 1" / "Liite 2"; "liite" attachment/schedule.
_LIITE_RE = compile_classifier_regex(
    r"^Liite\s+(\d+)\s*$", classifier_id="lawvm.finland.attachment_ir.liite"
)

# Numbered paragraph "1. " / "12. " (digit(s) + dot + optional whitespace).
_PARA_NUM_RE = compile_classifier_regex(
    r"^(\d+)\.\s*", classifier_id="lawvm.finland.attachment_ir.para_num"
)

# Alpha item "a) " / "b) " (single lowercase letter + close paren).
_ITEM_ALPHA_RE = compile_classifier_regex(
    r"^([a-z])\)\s*", classifier_id="lawvm.finland.attachment_ir.item_alpha"
)

# --- Statute-spine markers (spine mode; see ``_looks_like_spine``) ----------
#
# These three arms recognise the Finnish operative-statute spine that Regime-A
# Finlex PDFs carry (e.g. ``2011/38`` Vna ilmanlaadusta) but that the appendix
# recogniser above flattens (design defects G1/G5/G6). They are ONLY consulted
# when ``_looks_like_spine`` fires; non-§ attachments never reach them, so the
# appendix path stays byte-for-byte unchanged (backward-compat requirement).

# Section mark "N §" / "24 §" / "4a §" / "1 §." — the canonical Finnish
# section shape. The label capture is the bare number (with optional single
# letter suffix, e.g. ``4a``), NEVER including ``§``, so
# ``normalized_label_key`` yields the same key replay's structure graft uses
# (mirrors ``profile/normalize._EMBEDDED_SECTION_MARK_RE``). Fixes G1: such
# lines currently fall through to the free-text ``P`` fallback.
_SECTION_RE = compile_classifier_regex(
    r"^(\d+[a-z]?)\s*§\.?\s*$",
    classifier_id="lawvm.finland.attachment_ir.section",
)

# Chapter header "N luku" / "1 LUKU" (case-insensitive on the ``luku`` token,
# word-boundary anchored so ``2 lukuisia`` never matches). Optional trailing
# chapter title is sliced from ``stripped[m.end():]`` by the caller. Fixes G6:
# emits a real ``CHAPTER`` IRNode that replay can target by ``.label``.
_LUKU_RE = compile_classifier_regex(
    r"^(\d+)\s+[Ll][Uu][Kk][Uu]\b",
    classifier_id="lawvm.finland.attachment_ir.luku",
)

# Numbered kohta "N)" — the real Finnish item form (``1)`` ``2)`` ``12)``),
# matched by NEITHER ``_ITEM_ALPHA_RE`` (letters only) NOR ``_PARA_NUM_RE``
# (needs a dot: ``N.``). Fixes G5: such kohta items currently fall through
# entirely.
_KOHTA_NUM_RE = compile_classifier_regex(
    r"^(\d+)\)\s*",
    classifier_id="lawvm.finland.attachment_ir.kohta_num",
)

# Em-dash separator "––––––––––––" (3+ em/en/ascii dashes). Marks a table
# boundary: either closes an open table block (rows already collected) or
# opens an empty marker TABLE node so the separator is owned in the IR
# (§0 total-accounting; not silently dropped).
_TABLE_SEP_RE = compile_classifier_regex(
    r"^[–—-]{3,}\s*$", classifier_id="lawvm.finland.attachment_ir.table_sep"
)

# Page number / running header: a bare digit or "219/2014"-style statute-id
# line carried by pdftotext as page furniture. Skipped (no IR node emitted).
# String-based check — avoids nested-backtracking quantifiers in regex form
# (``\d+(/\d+)?\s*$`` triggers the safety lint for variable-backtracking
# overlap; the check is trivially expressible as a string predicate).


def _is_page_num_line(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    # Either all digits, or "digits/digits".
    if "/" in s:
        left, _, right = s.partition("/")
        return left.isdigit() and right.isdigit()
    return s.isdigit()

# Table caption "Taulukko N" / "taulukko N" — opens a table block whose
# caption is captured as a HEADING child under the TABLE node.
_TAULUKKO_RE = compile_classifier_regex(
    r"^[Tt]aulukko\s+(\d+)\s*$", classifier_id="lawvm.finland.attachment_ir.taulukko"
)

# Row-cell splitter: 2+ spaces OR a tab. Used to split a table row line into
# cells. Reused at module scope (no per-loop compile).
_CELL_SPLIT_RE = compile_classifier_regex(
    r"\s{2,}|\t+", classifier_id="lawvm.finland.attachment_ir.cell_split"
)


# ---------------------------------------------------------------------------
# Mutable builder intermediary (IRNode is frozen; we accumulate during
# line-by-line parsing then freeze at the boundary via to_ir_node).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Builder:
    """Mutable builder for an :class:`IRNode` tree.

    IRNode is a frozen dataclass; parsing pdftotext text needs to append
    continuation lines onto the current leaf's text and grow children
    incrementally, so we fabricate the tree in this mutable intermediary
    and freeze once via :meth:`to_ir_node` at the end.

    ``text_parts`` accumulates continuation fragments; ``text`` joins them
    with single spaces so the final IR node's text reads as flowing prose
    (pdftotext introduces arbitrary hard wraps that carry no semantic
    meaning — joining them restores the paragraph as a unit).
    """

    kind: IRNodeKind
    label: Optional[str] = None
    text_parts: List[str] = field(default_factory=list)
    attrs: Dict[str, Any] = field(default_factory=dict)
    children: List["_Builder"] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(part for part in self.text_parts if part).strip()

    def append_text(self, segment: str) -> None:
        s = segment.strip()
        if s:
            self.text_parts.append(s)

    def to_ir_node(self) -> IRNode:
        return IRNode(
            kind=self.kind,
            label=self.label,
            text=self.text,
            attrs=dict(self.attrs) if self.attrs else {},
            children=tuple(child.to_ir_node() for child in self.children),
        )


# ---------------------------------------------------------------------------
# Predicates / classifiers (prefilters, per §1.11 — surface predicates
# only route into the typed builders; they do not authorise legal state).
# ---------------------------------------------------------------------------


def _is_caps_heading(stripped: str) -> bool:
    """Recognise an ALL-CAPS heading line.

    Accepts lines that have at least one cased alphabetic character, all of
    whose cased characters are upper-case, and are at least 3 characters
    long. Lines consisting solely of digits / punctuation never qualify
    (``"219/2014".isupper()`` is False because there are no cased chars;
    ``"--–-".isupper()`` is likewise False).
    """
    if len(stripped) < 3:
        return False
    if not stripped.isupper():
        return False
    return any(c.isalpha() for c in stripped)


def _split_row_cells(line: str) -> List[str]:
    """Split a table-row line into its cells (2+ spaces or tab separated)."""
    return [c.strip() for c in _CELL_SPLIT_RE.split(line) if c.strip()]


def _is_table_row(stripped: str) -> bool:
    """Recognise a table-row line: 2+ non-empty cells separated by 2+ spaces
    or a tab.

    Self-contained: belts-and-braces excludes lines already matched by a
    more specific structural / skip pattern, so a paragraph like
    ``70/157/ETY, 70/220/ETY, ...`` (single-space-separated, never tab or
    multi-space) is NOT misclassified as a table row.

    A cell-length guard (≤40 chars per cell) further rejects prose lines
    that happen to contain an accidental double-space — a real table cell
    is a short label/identifier/value, not a full prose fragment. This
    also classifies ALL-CAPS short-header rows (``COLUMN A   COLUMN B``)
    as table rows rather than ALL-CAPS headings, which is the correct
    behaviour for a table block opened by a ``Taulukko`` caption or
    bounded by ``–––`` separators.
    """
    if not stripped:
        return False
    if (
        _TABLE_SEP_RE.match(stripped)
        or _PARA_NUM_RE.match(stripped)
        or _ITEM_ALPHA_RE.match(stripped)
        or _OSA_RE.match(stripped)
        or _LIITE_RE.match(stripped)
        or _TAULUKKO_RE.match(stripped)
        or _is_page_num_line(stripped)
    ):
        return False
    cells = _split_row_cells(stripped)
    if len(cells) < 2:
        return False
    return all(len(c) <= 40 for c in cells)


def _is_header_row(cells: List[str]) -> bool:
    """Decide if a row should be emitted with ``HEADER_CELL`` children.

    Heuristic honouring Finlex attachment convention: header rows are short
    (≤40 chars per cell) and contain at least one ALL-CAPS cell. Covers
    e.g. ``NR`` / ``VAATIMUS`` / ``YKSIKKÖ``.
    """
    if not cells:
        return False
    if not all(len(c) <= 40 for c in cells):
        return False
    return any(c.isupper() and any(ch.isalpha() for ch in c) for c in cells)


# ---------------------------------------------------------------------------
# Spine detection + Title-case heading absorb (spine mode only)
# ---------------------------------------------------------------------------


def _looks_like_spine(text: str) -> bool:
    """Decide whether ``text`` is an operative-statute spine (spine mode) or a
    table/prose/appendix (appendix mode).

    A spine is recognised by the presence of at least one ``N §`` section
    mark on its own line. This is a *conservative* signal: non-§ attachments
    (tables, prose, ``Liite``/``Taulukko`` appendices) carry no bare ``N §``
    line and therefore never trip it, so they parse through the unchanged
    appendix path (the backward-compat requirement). The 6448.pdf-style
    ``VAATIMUKSET``/``N.``/``a)``/table attachments have no ``N §`` markers.

    Detection is line-based and side-effect-free; ``\\f`` page breaks are
    normalised the same way the parser normalises them, so a section mark on
    a page-break line still counts.
    """
    for raw in text.replace("\f", "\n").split("\n"):
        if _SECTION_RE.match(raw.strip()):
            return True
    return False


def _is_titlecase_heading(stripped: str) -> bool:
    """Recognise a per-§ Title-case heading line (spine mode).

    Finnish operative sections carry a short heading immediately under the
    ``N §`` mark (e.g. ``Lain tarkoitus`` under ``1 §``). Such a heading is a
    short line (Finnish convention: sentence-case, first word capitalised),
    NOT terminated by sentence punctuation, and NOT itself a structural
    marker. It is deliberately *not* ALL-CAPS (that is handled by the
    appendix ``_is_caps_heading`` arm, reused in spine mode for genuine
    ALL-CAPS chapter/part banners).
    """
    if not stripped or len(stripped) > 80:
        return False
    if stripped[-1] in ".:;":
        return False
    # First cased character must be upper-case (Title/sentence case).
    first_alpha = next((c for c in stripped if c.isalpha()), "")
    if not first_alpha or not first_alpha.isupper():
        return False
    # Exclude structural markers so a heading-shaped line that is really a
    # marker (``2 luku Otsikko`` would already be a CHAPTER) never absorbs.
    if (
        _SECTION_RE.match(stripped)
        or _LUKU_RE.match(stripped)
        or _KOHTA_NUM_RE.match(stripped)
        or _PARA_NUM_RE.match(stripped)
        or _ITEM_ALPHA_RE.match(stripped)
        or _OSA_RE.match(stripped)
        or _LIITE_RE.match(stripped)
        or _TAULUKKO_RE.match(stripped)
        or _is_page_num_line(stripped)
    ):
        return False
    # A heading is not a full prose sentence: reject long multi-clause lines
    # (they are body text, which becomes a positional SUBSECTION instead).
    return True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def pdf_text_to_ir_node(
    text: str,
    *,
    source_ref: str = "",
    attachment_label: str = "",
) -> IRNode:
    """Parse ``pdftotext`` output of a Finlex attachment PDF into an IR tree.

    Recognised patterns (in dispatch order):

      1. Top-level division ``I OSA`` / ``II OSA``  → ``APPENDIX``, ``label="osa_I"``
      2. Schedule entry ``Liite N``                  → ``SCHEDULE``,  ``label="liite_N"``
      3. Table caption ``Taulukko N``                → ``TABLE`` (with ``HEADING`` caption child)
      4. Table separator ``–––`` (3+ dashes)         → table boundary (closes or opens ``TABLE``)
      5. ALL-CAPS heading lines                      → ``HEADING``
      6. Numbered paragraphs ``N. <text>``           → ``PARAGRAPH``, ``label="N"``
      7. Alpha items ``a) <text>``                   → ``ITEM``,      ``label="a"``
      8. Table row (cells split by 2+ spaces / tab)  → ``ROW`` / ``CELL`` or ``HEADER_CELL``
      9. Page number / running header                → skipped (no IR node)
     10. Anything else (unrecognised non-empty)      → continuation text on the
         open leaf, OR a fresh ``P`` node if no leaf is open (§0 total-accounting;
         §1.10 fail loud never silent-fallback).

    Returns a single root ``IRNode`` of kind ``HCONTAINER`` containing the
    full attachment structure. Unrecognised lines become typed ``P`` nodes
    carrying their text — never silently dropped.

    The parser is pure: it takes a string and returns an ``IRNode``; no I/O,
    no external state, no filesystem access. pdftotext's page-break form
    feed ``\\f`` is normalised to a newline boundary at entry, so page
    boundaries close the current paragraph leaf and surface naturally as
    structural paragraph breaks in the IR.
    """
    # --- Auto-detect spine mode vs appendix mode ----------------------------
    # A statute spine (``N §`` marks) is parsed into real SECTION/CHAPTER IR
    # (spine mode). Everything else — tables, prose, ``Liite``/``Taulukko``
    # appendices — parses through the unchanged appendix path below. This
    # dispatch is the ONLY change appendix inputs see: they never satisfy
    # ``_looks_like_spine`` so their behaviour is byte-for-byte preserved.
    if _looks_like_spine(text):
        return _spine_text_to_ir_node(
            text, source_ref=source_ref, attachment_label=attachment_label
        )

    # Normalise page breaks: pdftotext emits ``\f`` as a page boundary;
    # treating it as a blank line closes the current paragraph leaf so a
    # new paragraph starts on the next page's content.
    text = text.replace("\f", "\n")

    root_attrs: Dict[str, Any] = {}
    if source_ref:
        root_attrs["source_ref"] = source_ref
    root = _Builder(
        IRNodeKind.HCONTAINER,
        label=attachment_label or None,
        attrs=root_attrs,
    )
    # Stack of open builders. Index 0 is root; deeper indices are nested.
    stack: List[_Builder] = [root]
    # The "current leaf" is whichever builder continuation-text should
    # append to (a ``PARAGRAPH``, ``ITEM``, or standalone ``P``).
    current_leaf: Optional[_Builder] = None
    # When non-None, we are inside a table block (a ``TABLE`` builder) whose
    # rows will be emitted as ``ROW``/``CELL`` children. Either:
    #  - started by a ``Taulukko N`` caption;
    #  - started by a ``–––`` separator (boundary-marker TABLE with 0 rows);
    #  - auto-started when a table-row line appears outside any block.
    table_block: Optional[_Builder] = None

    def close_paragraph() -> None:
        # Pop any open ``ITEM`` / ``PARAGRAPH`` off the stack so a sibling
        # ``PARAGRAPH`` or ``HEADING`` attaches at the right level. Leaves
        # ``APPENDIX`` / ``SCHEDULE`` / ``TABLE`` parents intact.
        nonlocal current_leaf
        while stack and stack[-1].kind in (IRNodeKind.PARAGRAPH, IRNodeKind.ITEM):
            stack.pop()
        current_leaf = None

    def close_table_block() -> None:
        nonlocal table_block
        table_block = None

    def current_paragraph() -> Optional[_Builder]:
        for b in reversed(stack):
            if b.kind == IRNodeKind.PARAGRAPH:
                return b
        return None

    for raw in text.split("\n"):
        stripped = raw.strip()
        if not stripped:
            # Blank line / page-break boundary: close the current leaf so
            # subsequent plain text forms a fresh ``P`` node (when no
            # structural marker follows). The builder remains on the stack;
            # only the leaf-pointer is cleared.
            current_leaf = None
            continue

        # --- Skip: page number / running header ----------------------------
        if _is_page_num_line(stripped):
            continue

        # --- Top-level division "I OSA" -----------------------------------
        m = _OSA_RE.match(stripped)
        if m:
            close_paragraph()
            close_table_block()
            # Pop everything back to root, then push the new APPENDIX.
            del stack[1:]
            appendix = _Builder(
                IRNodeKind.APPENDIX,
                label=f"osa_{m.group(1)}",
                attrs={"osa_label": m.group(1), "source_text": stripped},
            )
            root.children.append(appendix)
            stack.append(appendix)
            continue

        # --- Schedule entry "Liite N" -------------------------------------
        m = _LIITE_RE.match(stripped)
        if m:
            close_paragraph()
            close_table_block()
            # Find or auto-create the parent APPENDIX. A ``Liite`` without
            # a preceding ``I OSA`` is a real shape (one-page attachments
            # sometimes skip the ``osa`` header); we auto-create the parent
            # with a typed witness so §1.3 (no child op falls back to
            # whole-node replacement) holds.
            if not any(b.kind == IRNodeKind.APPENDIX for b in stack):
                auto_appendix = _Builder(
                    IRNodeKind.APPENDIX,
                    label="osa_auto",
                    attrs={"auto_created": "no_osa_header_for_liite"},
                )
                root.children.append(auto_appendix)
                del stack[1:]
                stack.append(auto_appendix)
            else:
                # Pop ITEM / PARAGRAPH / SCHEDULE siblings, keep APPENDIX.
                while stack[-1].kind in (
                    IRNodeKind.SCHEDULE,
                    IRNodeKind.PARAGRAPH,
                    IRNodeKind.ITEM,
                ):
                    stack.pop()
            schedule = _Builder(
                IRNodeKind.SCHEDULE,
                label=f"liite_{m.group(1)}",
                attrs={"liite_label": m.group(1), "source_text": stripped},
            )
            stack[-1].children.append(schedule)
            stack.append(schedule)
            continue

        # --- Table separator "–––" ----------------------------------------
        if _TABLE_SEP_RE.match(stripped):
            # Boundary marker. Either closes an open table block (if one
            # exists) or opens an empty marker TABLE node so the separator
            # itself is owned in the IR (§0 total-accounting: never silently
            # dropped). The literal separator text is preserved in
            # ``attrs["source_text"]``.
            close_paragraph()
            if table_block is not None:
                close_table_block()
            else:
                marker_table = _Builder(
                    IRNodeKind.TABLE,
                    attrs={"boundary_marker": True, "source_text": stripped},
                )
                stack[-1].children.append(marker_table)
                table_block = marker_table
            continue

        # --- Table caption "Taulukko N" -----------------------------------
        m = _TAULUKKO_RE.match(stripped)
        if m:
            close_paragraph()
            close_table_block()
            table_block = _Builder(
                IRNodeKind.TABLE,
                label=f"taulukko_{m.group(1)}",
                attrs={"caption": stripped},
            )
            stack[-1].children.append(table_block)
            # Caption goes in as a HEADING child under the TABLE so the
            # text-content ownership is explicit.
            table_block.children.append(
                _Builder(IRNodeKind.HEADING, text_parts=[stripped])
            )
            continue

        # --- Numbered paragraph "N. <text>" ------------------------------
        # Checked BEFORE _is_caps_heading so e.g. ``1. ABC`` starts a new
        # PARAGRAPH (label="1"), not a stray HEADING; ``_is_caps_heading``
        # without a numeric prefix still classifies genuine ALL-CAPS lines.
        m = _PARA_NUM_RE.match(stripped)
        if m:
            num = m.group(1)
            remainder = stripped[m.end():]
            close_paragraph()
            close_table_block()
            paragraph = _Builder(
                IRNodeKind.PARAGRAPH,
                label=num,
                attrs={"label_kind": "numeric", "source_text": m.group(0)},
            )
            paragraph.append_text(remainder)
            stack[-1].children.append(paragraph)
            stack.append(paragraph)
            current_leaf = paragraph
            continue

        # --- Alpha item "a) <text>" ---------------------------------------
        m = _ITEM_ALPHA_RE.match(stripped)
        if m:
            letter = m.group(1)
            remainder = stripped[m.end():]
            para = current_paragraph()
            if para is None:
                # No parent PARAGRAPH — auto-create one so the ITEM has its
                # typed parent per §1.3 (a child op must not fall back to
                # whole-node replacement of the schedule).
                auto_paragraph = _Builder(
                    IRNodeKind.PARAGRAPH,
                    label=None,
                    attrs={"auto_created": "no_paragraph_for_item"},
                )
                stack[-1].children.append(auto_paragraph)
                stack.append(auto_paragraph)
                para = auto_paragraph
            # Close any previously-opened ITEM under the paragraph so this
            # new ITEM is a sibling, not a child of the previous ITEM.
            while stack and stack[-1].kind == IRNodeKind.ITEM:
                stack.pop()
            item = _Builder(
                IRNodeKind.ITEM,
                label=letter,
                attrs={"label_kind": "alpha", "source_text": m.group(0)},
            )
            item.append_text(remainder)
            para.children.append(item)
            stack.append(item)
            current_leaf = item
            continue

        # --- Table row (multi-space / tab separated) ----------------------
        # Checked BEFORE _is_caps_heading so an ALL-CAPS short-header row
        # (``COLUMN A   COLUMN B``) becomes a ROW with HEADER_CELL children,
        # not a stray HEADING that would also close the open table block.
        if _is_table_row(stripped):
            # If no active table block, start one (a real table row appeared
            # outside of any separator/caption region).
            if table_block is None:
                table_block = _Builder(
                    IRNodeKind.TABLE,
                    attrs={"auto_opened": "row_outside_separator"},
                )
                stack[-1].children.append(table_block)
            cells = _split_row_cells(stripped)
            row = _Builder(IRNodeKind.ROW, attrs={"source_text": stripped})
            first_row = not any(
                c.kind == IRNodeKind.ROW for c in table_block.children
            )
            header_now = first_row and _is_header_row(cells)
            for cell_text in cells:
                cell_kind = IRNodeKind.HEADER_CELL if header_now else IRNodeKind.CELL
                row.children.append(_Builder(cell_kind, text_parts=[cell_text]))
            table_block.children.append(row)
            current_leaf = None
            continue

        # --- ALL-CAPS heading ---------------------------------------------
        # Last of the structural checks: a line that is genuinely ALL-CAPS,
        # longer than 3 chars, with at least one cased letter, and was not
        # matched by any of the patterns above (a PARAGRAPH ``N.`` prefix,
        # an ITEM ``a)`` prefix, or a multi-cell table row).
        if _is_caps_heading(stripped):
            close_paragraph()
            close_table_block()
            heading = _Builder(IRNodeKind.HEADING, text_parts=[stripped])
            stack[-1].children.append(heading)
            continue

        # --- Free text: continuation of current leaf OR a fresh P node ----
        # Continuation: when a paragraph / item / P was just opened, hard
        # wraps in the pdftotext output append to that leaf's text content
        # (joined with a single space at freeze time). When no leaf is open
        # (e.g. prose flowing directly under a SCHEDULE), the line becomes
        # a fresh standalone ``P`` node — §0 total-accounting forbids silent
        # drops; unrecognised non-empty input is typed.
        if current_leaf is not None:
            current_leaf.append_text(stripped)
        else:
            p_node = _Builder(IRNodeKind.P, text_parts=[stripped])
            stack[-1].children.append(p_node)
            current_leaf = p_node

    return root.to_ir_node()


# ---------------------------------------------------------------------------
# Spine-mode parser (operative statute spine → SECTION/CHAPTER IR).
# ---------------------------------------------------------------------------


def _spine_text_to_ir_node(
    text: str,
    *,
    source_ref: str = "",
    attachment_label: str = "",
) -> IRNode:
    """Parse an operative-statute spine into SECTION/CHAPTER IR.

    Emitted structure (fixes design defects G1/G5/G6):

      * ``N luku``  → ``CHAPTER``  ``label="N"`` (eId ``chp_N``); optional
        trailing Title-case chapter title absorbed as a ``HEADING`` child.
      * ``N §``     → ``SECTION``  ``label="N"`` (eId ``sec_N``); the label is
        the bare number (``normalized_label_key`` parity with replay's graft).
      * Per-§ Title-case heading line → ``HEADING`` child of the SECTION.
      * ``N)`` kohta → ``ITEM`` ``label="N"``, ``label_kind="numeric_kohta"``,
        child of the section's current SUBSECTION (or the section itself).
      * Positional body text → ``SUBSECTION`` (implicit ``1 mom.``); each
        blank-line-separated body block under a section opens the next
        positional SUBSECTION (``label="1"``, ``"2"`` …).
      * ALL-CAPS banner → ``HEADING`` (reuses the appendix ``_is_caps_heading``
        arm for OSA/part banners that a spine can still carry).

    Sections attach under the current CHAPTER when one is open, else directly
    under the root ``HCONTAINER`` (Regime-A chapterless statutes like the
    ``2011/38`` pilot). The eId scheme is ``part_N__chp_N__sec_N`` — recorded
    in ``attrs["eid"]`` on each structural node so Phase 1 (base-loader
    wiring) can graft without recomputation.
    """
    text = text.replace("\f", "\n")

    root_attrs: Dict[str, Any] = {"spine_mode": True}
    if source_ref:
        root_attrs["source_ref"] = source_ref
    root = _Builder(
        IRNodeKind.HCONTAINER,
        label=attachment_label or None,
        attrs=root_attrs,
    )

    current_chapter: Optional[_Builder] = None
    current_section: Optional[_Builder] = None
    current_subsection: Optional[_Builder] = None
    current_item: Optional[_Builder] = None
    # Positional subsection counter, reset per section.
    subsec_counter = 0
    # True right after a `N §` mark, so the very next Title-case line is
    # absorbed as the section HEADING rather than opening a SUBSECTION.
    heading_pending = False
    # True after a blank line: the next body (non-kohta) line opens a fresh
    # positional SUBSECTION. Kohta lines ignore it (they stay in the intro
    # subsection so a `1) 2) 3)` list is not fragmented by pdftotext blanks).
    paragraph_break = False

    def _eid(section: Optional[_Builder], chapter: Optional[_Builder], sec_label: str) -> str:
        parts: List[str] = []
        if chapter is not None and chapter.label:
            parts.append(f"chp_{chapter.label}")
        parts.append(f"sec_{sec_label}")
        return "__".join(parts)

    def _open_subsection() -> _Builder:
        """Open a fresh positional SUBSECTION under the current section."""
        nonlocal current_subsection, subsec_counter
        subsec_counter += 1
        current_subsection = _Builder(
            IRNodeKind.SUBSECTION,
            label=str(subsec_counter),
            attrs={"label_kind": "positional", "positional": True},
        )
        assert current_section is not None
        current_section.children.append(current_subsection)
        return current_subsection

    def _body_subsection() -> _Builder:
        """Return the SUBSECTION a body line attaches under, opening a new one
        on a paragraph break (or when the section has none yet)."""
        nonlocal paragraph_break
        if current_subsection is None or paragraph_break:
            paragraph_break = False
            return _open_subsection()
        return current_subsection

    def _kohta_subsection() -> _Builder:
        """Return the SUBSECTION a kohta item attaches under. Kohta items
        cling to the current (intro) subsection so a `1) 2) 3)` list stays a
        single subsection even when pdftotext double-spaces the items; only if
        no subsection is open yet does one get created."""
        nonlocal paragraph_break
        paragraph_break = False
        if current_subsection is None:
            return _open_subsection()
        return current_subsection

    for raw in text.split("\n"):
        stripped = raw.strip()
        if not stripped:
            # Blank line: close any open kohta-item continuation so the next
            # kohta / body line is a sibling, and mark a paragraph boundary so
            # the next non-kohta body line opens a fresh positional
            # SUBSECTION. ``heading_pending`` is deliberately kept alive across
            # blanks: pdftotext routinely inserts a blank line between the
            # ``N §`` mark and its Title-case heading, so the heading must
            # still be absorbable after the gap.
            current_item = None
            paragraph_break = True
            continue

        if _is_page_num_line(stripped):
            continue

        # --- Chapter "N luku" ---------------------------------------------
        m = _LUKU_RE.match(stripped)
        if m:
            num = m.group(1)
            title = stripped[m.end():].strip()
            chapter = _Builder(
                IRNodeKind.CHAPTER,
                label=num,
                attrs={
                    "label_kind": "numeric",
                    "source_text": stripped,
                    "eid": f"chp_{num}",
                },
            )
            root.children.append(chapter)
            if title:
                chapter.children.append(
                    _Builder(IRNodeKind.HEADING, text_parts=[title])
                )
            current_chapter = chapter
            current_section = None
            current_subsection = None
            current_item = None
            subsec_counter = 0
            heading_pending = False
            paragraph_break = False
            continue

        # --- Section "N §" ------------------------------------------------
        m = _SECTION_RE.match(stripped)
        if m:
            num = m.group(1)
            section = _Builder(
                IRNodeKind.SECTION,
                label=num,
                attrs={
                    "label_kind": "numeric",
                    "source_text": stripped,
                    "eid": _eid(None, current_chapter, num),
                },
            )
            parent = current_chapter if current_chapter is not None else root
            parent.children.append(section)
            current_section = section
            current_subsection = None
            current_item = None
            subsec_counter = 0
            heading_pending = True
            paragraph_break = False
            continue

        # --- Per-§ Title-case heading absorb ------------------------------
        if (
            heading_pending
            and current_section is not None
            and _is_titlecase_heading(stripped)
        ):
            current_section.children.append(
                _Builder(IRNodeKind.HEADING, text_parts=[stripped])
            )
            heading_pending = False
            continue
        heading_pending = False

        # --- ALL-CAPS banner (part/chapter banner a spine may carry) ------
        if _is_caps_heading(stripped):
            target = current_section or current_chapter or root
            target.children.append(
                _Builder(IRNodeKind.HEADING, text_parts=[stripped])
            )
            continue

        # --- Numbered kohta "N)" ------------------------------------------
        m = _KOHTA_NUM_RE.match(stripped)
        if m and current_section is not None:
            num = m.group(1)
            remainder = stripped[m.end():]
            body = _kohta_subsection()
            item = _Builder(
                IRNodeKind.ITEM,
                label=num,
                attrs={"label_kind": "numeric_kohta", "source_text": m.group(0)},
            )
            item.append_text(remainder)
            body.children.append(item)
            current_item = item
            continue

        # --- Body text ----------------------------------------------------
        if current_item is not None:
            # Continuation of an open kohta item (pdftotext hard wrap).
            current_item.append_text(stripped)
            continue
        if current_section is not None:
            body = _body_subsection()
            body.append_text(stripped)
            continue
        # Preamble text before the first section: fresh P node so §0
        # total-accounting holds (never silently dropped).
        p_node = _Builder(IRNodeKind.P, text_parts=[stripped])
        root.children.append(p_node)

    return root.to_ir_node()


# ---------------------------------------------------------------------------
# Tree-walking helpers (test-visible; small enough to keep here rather than
# reach across to a different module's helper).
# ---------------------------------------------------------------------------


def iter_tree(node: IRNode) -> Iterator[IRNode]:
    """Pre-order iteration over an IRNode tree (yields the node itself, then
    each child subtree)."""
    yield node
    for child in node.children:
        yield from iter_tree(child)


# ---------------------------------------------------------------------------
# Attachment PDF link extraction + IR supplement building
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class AttachmentPDFLink:
    """One attachment-PDF link found in the consolidated statute XML."""

    pdf_name: str
    """Bare filename, e.g. ``4484.pdf``."""
    href: str
    """Full href as stored in XML, e.g. ``media/4484.pdf``."""
    label: str
    """Human-readable label from the anchor text, e.g. ``Liitteet``."""


def extract_attachment_pdf_links(xml_bytes: bytes) -> "list[AttachmentPDFLink]":
    """Find ``<a href="media/N.pdf">`` links in consolidated XML.

    Walks ``<hcontainer name="attachments">`` → ``<hcontainer name="attachment">``
    → ``<content>`` → ``<p>`` → ``<a href="...">``. Returns one
    :class:`AttachmentPDFLink` per PDF-bearing anchor. Non-PDF media (.gif,
    .jpg) are transparently excluded — links with non-.pdf hrefs are skipped.
    """
    try:
        root = parse_corpus_xml(xml_bytes)
    except etree.XMLSyntaxError:
        return []
    links: list[AttachmentPDFLink] = []
    for anchor in root.iter():
        if _tag(anchor) != "a":
            continue
        href = str(anchor.get("href") or "")
        if not href:
            continue
        # Extract bare filename from href
        name = href.rsplit("/", 1)[-1] if "/" in href else href
        if not name.lower().endswith(".pdf"):
            continue
        label = _collapse_text(anchor).strip()
        links.append(AttachmentPDFLink(pdf_name=name, href=href, label=label))
    return links


@dataclass(frozen=True, slots=True)
class AttachmentIRSupplement:
    """Parsed attachment content as first-class LawVM IR.

    Built from ``read_attachment_media`` → ``pdf_to_text`` →
    ``pdf_text_to_ir_node``. The IR tree is the **pre-corrigendum**
    attachment content; corrigenda targeting attachment text apply against
    this IR via ``patch_ir_node_text`` (which rebuilds the frozen IRNode
    tree with corrected text nodes).
    """

    pdf_name: str
    ir: IRNode
    pdf_text_length: int
    """visible-text length of the pdftotext extraction."""
    source_ref: str
    """Locator for provenance, e.g. ``finlex://sd-cons/2002/1248/fin@20141291/media/6448.pdf``."""


def build_attachment_ir_supplements(
    *,
    cs: Any,
    sid: str,
    links: "list[AttachmentPDFLink]",
    source_ref_prefix: str = "",
) -> "list[AttachmentIRSupplement]":
    """Fetch and parse each attachment PDF into LawVM IR.

    For each link: read attachment bytes via ``cs.read_attachment_media``,
    extract layout via ``pdfplumber`` (tables/footnotes/body-separation),
    parse to IR via ``layout_to_ir_node``. Falls back to ``pdftotext`` +
    ``pdf_text_to_ir_node`` when pdfplumber fails — the pdftotext path
    doesn't produce TABLE/ROW/CELL structure but still builds a typed IR
    tree (§1.8 honest accounting: no invented sourcery).
    """
    from lawvm.finland.pdf_layout import extract_pdf_layout, layout_to_ir_node
    from lawvm.finland.pdf_text import pdf_to_text
    supplements: list[AttachmentIRSupplement] = []
    for link in links:
        pdf_bytes = cs.read_attachment_media(sid, link.pdf_name)
        if not pdf_bytes:
            continue
        source_ref = f"{source_ref_prefix}/{link.pdf_name}" if source_ref_prefix else link.pdf_name
        # Prefer deterministic pdfplumber extraction (tables/footnotes
        # separated as structured IR). Fall back to pdftotext when
        # pdfplumber fails (encrypted/scanned/corrupted PDFs).
        layout = extract_pdf_layout(pdf_bytes)
        if layout is not None and (layout.body_blocks or layout.tables):
            ir = layout_to_ir_node(layout, source_ref=source_ref)
            text_len = sum(len(b.text) for b in layout.body_blocks)
            supplements.append(AttachmentIRSupplement(
                pdf_name=link.pdf_name, ir=ir,
                pdf_text_length=text_len, source_ref=source_ref,
            ))
        else:
            text = pdf_to_text(pdf_bytes)
            if not text:
                continue
            ir = pdf_text_to_ir_node(text, source_ref=source_ref, attachment_label=link.label)
            supplements.append(AttachmentIRSupplement(
                pdf_name=link.pdf_name, ir=ir,
                pdf_text_length=len(text), source_ref=source_ref,
            ))
    return supplements


# ---------------------------------------------------------------------------
# Co-located identity / tag abstraction helpers
# ---------------------------------------------------------------------------

def _tag(el: Any) -> str:
    """Return the local-name of an lxml element's tag, or ``""`` for comments."""
    if hasattr(el, "tag") and isinstance(el.tag, str):
        return el.tag.rsplit("}", 1)[-1] if "}" in el.tag else el.tag
    return ""


def _collapse_text(el: Any) -> str:
    """Collapse all descendant text of an lxml element into one string."""
    return " ".join(t.strip() for t in el.itertext()) if hasattr(el, "itertext") else ""

"""Typed source-tree extraction for U.S. Code annual-edition title htm.

This is a source parsing layer, not replay. It reads the govinfo USCODE annual
edition (``application/xhtml+xml``, well-formed XHTML 1.0 Transitional) into a
typed per-section tree: section number, heading, normalized statutory text, and
the raw ``source-credit`` lineage (the witness denominator parsed in
:mod:`lawvm.us_federal.usc_witness`).

Source shape (per section, document order, flat siblings in one ``<div>``)::

    <!-- documentid:11_362 ... -->
    <!-- itempath:/110/CHAPTER 3/SUBCHAPTER IV/Sec. 362 -->
    <!-- expcite:TITLE 11-BANKRUPTCY!@!CHAPTER 3-...!@!Sec. 362 -->
    <!-- field-start:head --> <h3 class="section-head">&sect;362. Automatic stay</h3>
    <!-- field-start:statute -->  <p class="statutory-body">(a) ...</p>
                                  <p class="statutory-body-1em">(1) ...</p> ...
    <!-- field-end:statute -->
    <!-- field-start:sourcecredit --> <p class="source-credit">(Pub. L. ...)</p>
    <!-- field-end:sourcecredit -->
    <!-- field-start:notes --> ... EDITORIAL, excluded ...

Statutory text = ``statutory-body*`` paragraphs between ``field-start:statute``
and ``field-end:statute``. Everything in ``field-start:notes`` / ``analysis`` /
``note-*`` and the ``source-credit`` itself is editorial and excluded from the
statutory comparison surface.

MVP granularity is SECTION-LEVEL: each section yields one address
(``title``→``section``) and its full normalized statutory text — the oracle
surface a dry-run compares against. A STRETCH subsection split is provided by
:func:`split_statutory_subsections`, which maps leading ``(a)``/``(1)``/``(A)``/
``(i)`` markers to the pinned USC address convention by enumerator TOKEN TYPE
(the OLRC's CSS indent depth is unreliable under run-in nesting), splits run-in
heads (``(b)(1)``) into their levels, and emits a typed
``us_usc_subsection_parse_ambiguous`` finding where structure is unclear rather
than guessing.
"""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator

import lxml.html as LH
from lxml import etree

from lawvm.core.ir import LegalAddress

# Editorial paragraph/heading classes never part of statutory text.
_EDITORIAL_CLASSES = {"note-head", "note-body", "analysis", "subchapter-head", "chapter-head"}

# A section number from a ``section-head``: "§362. Automatic stay" → "362";
# repealed stubs are bracketed: "[§304. Repealed. ...]" → "304". The number may
# carry a letter suffix (e.g. "362A") and a dashed numeric tail (e.g. "49c–1",
# "1715z–13a", "278g–3a"): a digit-rooted insert section numbered between two
# parent sections. The OLRC renders the visible head with an EN-DASH (U+2013,
# ``&ndash;``) while the structural ``itempath``/``expcite``/``href`` use an ASCII
# hyphen — the dash glyph is captured VERBATIM (not normalized), because the
# pinned address convention preserves whatever dash the source carries: the USLM
# ``href`` the amendatory side parses (``/us/usc/t12/s1715z–13a``) overwhelmingly
# renders this same en-dash, so the oracle key and the lowered-op address agree
# only when both keep the en-dash unchanged. The dash class mirrors the
# amendatory target parsers (``[-‐‑‒–]``: hyphen, U+2010, U+2011, U+2012, U+2013).
# Ranges ("§§") are not used in these titles.
_SECTION_HEAD_RE = re.compile(
    r"^\[?\s*§+\s*(?P<num>[0-9]+[A-Za-z]*(?:[-‐‑‒–][0-9]+[A-Za-z]*)?)\."
)

# Leading enumerator markers for subsection splitting (STRETCH). Each statutory
# paragraph that opens a new structural unit starts with one of these in the
# leading text. A single paragraph may open MORE than one level at once — the
# OLRC consolidated Code routinely renders the head of a nested unit "run-in" on
# one line (``(b)(1) If the trustee ...`` is subsection (b) AND its paragraph (1)
# on a single ``statutory-body`` line). So a paragraph's leading text can carry a
# run of markers, each opening one level deeper than the last.
#
# The OLRC also separates the parent marker from a run-in child by a catchline
# and an em/en-dash: ``(c) Structure.—(1) The Service ...``.  A second regex
# finds that child so it is treated as a structural opener, not body prose.
_MARKER_RE = re.compile(r"^\((?P<token>[0-9A-Za-z]+)\)")
_MARKER_AFTER_DASH_RE = re.compile(
    r"^\s*[A-Za-z][A-Za-z\s,.&;]*?[—–—-]\s*\((?P<token>[0-9A-Za-z]+)\)"
)
# Structural-marker scanner used inside synthetic_usc_section: finds markers
# anywhere in the text, unlike _MARKER_RE which is anchored to the start. Hoisted
# per AGENTS.md §2.4 backtracking discipline — synthetic_usc_section is called
# per synthetic node and the pattern was compiled per call.
_SCANNER_MARKER_RE = re.compile(r"(?<!\S)\((?P<token>[0-9A-Za-z]+)\)")

# The pinned USC enumeration ladder. Level == index. The OLRC's CSS indent class
# is NOT a reliable level signal (run-in nesting flattens children to depth 0, so
# a paragraph (2) under a run-in (b)(1) renders at the same ``statutory-body``
# depth as a subsection), so the level is derived from the enumerator TOKEN TYPE
# against this fixed ladder:
#   0 subsection   lowercase letter   (a)
#   1 paragraph    arabic digit       (1)
#   2 subparagraph uppercase letter   (A)
#   3 clause       lowercase roman    (i)
#   4 subclause    uppercase roman    (I)
#   5 item         doubled lowercase  (aa)
#   6 sub-item     doubled uppercase  (AA)
# A bare lowercase single letter is ambiguous between subsection-letter and
# lowercase-roman (``i``/``v``/``x``/...); an uppercase single letter likewise
# between subparagraph-letter and uppercase-roman. The open-ancestor stack
# disambiguates (a ``(i)`` opening a child of ``(A)`` is a clause; following
# ``(h)`` it is the 9th subsection). When it cannot, the node is flagged
# ``us_usc_subsection_parse_ambiguous`` and never guessed.
_USC_LADDER = (
    "subsection",
    "paragraph",
    "subparagraph",
    "clause",
    "subclause",
    "item",
    "sub-item",
)
_LEVEL_SUBSECTION = 0
_LEVEL_PARAGRAPH = 1
_LEVEL_SUBPARAGRAPH = 2
_LEVEL_CLAUSE = 3
_LEVEL_SUBCLAUSE = 4
_LEVEL_ITEM = 5
_LEVEL_SUBITEM = 6

# Retained for the legacy CSS-indent → kind mapping (diagnostics / histograms).
_INDENT_KIND = {
    0: "subsection",
    1: "paragraph",
    2: "subparagraph",
    3: "clause",
    4: "subclause",
    5: "item",
}

_SUBSECTION_PARSE_AMBIGUOUS = "us_usc_subsection_parse_ambiguous"

_ROMAN_VALUES = (
    (1000, "m"), (900, "cm"), (500, "d"), (400, "cd"),
    (100, "c"), (90, "xc"), (50, "l"), (40, "xl"),
    (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i"),
)
_ROMAN_RE = re.compile(r"^m{0,4}(cm|cd|d?c{0,3})(xc|xl|l?x{0,3})(ix|iv|v?i{0,3})$")
_LOWER_ALPHA_RE = re.compile(r"^[a-z]+$")
_UPPER_ALPHA_RE = re.compile(r"^[A-Z]+$")
# A paragraph enumerator inserted between two numbered paragraphs: ``(51A)``,
# ``(4A)`` — a digit root with a single trailing letter. Digit-rooted, so always a
# paragraph (never a letter level).
_DIGIT_LETTER_PARAGRAPH_RE = re.compile(r"^(?P<num>[0-9]+)[A-Za-z]$")


def _int_to_roman(value: int) -> str:
    out: list[str] = []
    for amount, symbol in _ROMAN_VALUES:
        while value >= amount:
            out.append(symbol)
            value -= amount
    return "".join(out)


def _roman_to_int(token: str) -> int | None:
    """Strict roman→int. Returns None for non-canonical romans (``iiii``, ``vv``)."""
    lowered = token.lower()
    if not lowered or _ROMAN_RE.match(lowered) is None:
        return None
    values = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
    total = 0
    prev = 0
    for ch in reversed(lowered):
        v = values[ch]
        if v < prev:
            total -= v
        else:
            total += v
            prev = v
    # Round-trip guard: only accept the canonical spelling for that integer.
    return total if _int_to_roman(total) == lowered else None


def _homogeneous_letter_ordinal(token: str, *, upper: bool) -> int | None:
    """Ordinal of a USC letter enumerator (``a``→1 ... ``z``→26, ``aa``→27 ...).

    USC doubles a single letter for the deepest letter levels (``(aa)``, ``(bb)``).
    Mixed-letter runs (``ab``) are not valid enumerators → None.
    """
    pattern = _UPPER_ALPHA_RE if upper else _LOWER_ALPHA_RE
    if pattern.match(token) is None or len(set(token)) != 1:
        return None
    base_ord = ord("A") if upper else ord("a")
    base = ord(token[0]) - base_ord + 1
    return base + 26 * (len(token) - 1)


def _marker_interpretations(token: str) -> tuple[tuple[int, int], ...]:
    """All ``(level, ordinal)`` the enumerator token can denote on the USC ladder.

    A digit is unambiguously a paragraph. A digit with a trailing letter suffix
    (``4A``, ``51D``) is also unambiguously a paragraph: the USC numbers a paragraph
    inserted between ``(51)`` and ``(52)`` as ``(51A)``, ``(51B)``, ... — a digit-
    rooted enumerator, never a letter level. A single lowercase letter is ambiguous
    between a subsection-letter and a lowercase-roman clause; uppercase likewise
    between subparagraph-letter and uppercase-roman subclause. Doubled letters
    (``aa``/``AA``) denote the item / sub-item levels.
    """
    if token.isdigit():
        return ((_LEVEL_PARAGRAPH, int(token)),)
    digit_letter = _DIGIT_LETTER_PARAGRAPH_RE.match(token)
    if digit_letter is not None:
        # ``51D`` → paragraph; ordinal is the numeric root (the lettered insert sits
        # at the same paragraph level as its root number, placed as a reopen-sibling).
        return ((_LEVEL_PARAGRAPH, int(digit_letter.group("num"))),)
    out: list[tuple[int, int]] = []
    if _LOWER_ALPHA_RE.match(token) is not None:
        letter = _homogeneous_letter_ordinal(token, upper=False)
        roman = _roman_to_int(token)
        if len(token) == 1 and letter is not None:
            out.append((_LEVEL_SUBSECTION, letter))
        if roman is not None:
            out.append((_LEVEL_CLAUSE, roman))
        if len(token) == 2 and token[0] == token[1] and letter is not None:
            out.append((_LEVEL_ITEM, letter))
    elif _UPPER_ALPHA_RE.match(token) is not None:
        letter = _homogeneous_letter_ordinal(token, upper=True)
        roman = _roman_to_int(token)
        if len(token) == 1 and letter is not None:
            out.append((_LEVEL_SUBPARAGRAPH, letter))
        if roman is not None:
            out.append((_LEVEL_SUBCLAUSE, roman))
        if len(token) == 2 and token[0] == token[1] and letter is not None:
            out.append((_LEVEL_SUBITEM, letter))
    return tuple(out)


def _resolve_marker_level_with_score(
    token: str,
    stack: list[tuple[int, int]],
    *,
    run_in_child: bool,
) -> tuple[int, int, int] | None:
    """Resolve an enumerator token to ``(score, level, ordinal)``.

    Lower score is a better structural fit against ``stack``.  Returns ``None``
    when the token is unrecognised or when two different levels tie for the best
    score.  The score is exposed so callers (e.g. dash-separated run-in detection)
    can require a clean fit before treating an ambiguous parent letter as a
    structural container.
    """
    interps = _marker_interpretations(token)
    if not interps:
        return None
    frontier = stack[-1][0] if stack else -1
    # Score each candidate; lower score = better fit. Distinct levels tying at the
    # best score == genuine ambiguity → refuse.
    scored: list[tuple[int, int, int]] = []
    for level, ordinal in interps:
        if run_in_child:
            if level == frontier + 1 and ordinal == 1:
                score = 0
            elif level > frontier:
                score = 3
            else:
                continue
        else:
            is_sibling = any(sl == level and so == ordinal - 1 for sl, so in stack)
            is_reopen = any(sl == level for sl, so in stack)
            # A sibling continuation of the FRONTIER (deepest open level) is the
            # cleanest fit. A sibling continuation that matches a SHALLOWER open
            # ancestor (``level < frontier``) requires closing the frontier subtree
            # to jump back up — structurally weaker than descending one clean level
            # into the just-opened frontier. This is the roman/letter discriminator:
            # under an open ``(A)`` (frontier = subparagraph) a ``(i)`` is the clause
            # first-child, NOT the 9th subsection reopening a far-shallower ``(h)``.
            if is_sibling and level >= frontier:
                score = 0  # clean next-sibling continuation of the deepest open level
            elif level == frontier + 1 and ordinal == 1:
                score = 1  # clean first child of the deepest open level
            elif is_sibling:
                score = 2  # next-sibling of a shallower ancestor (closes the frontier subtree)
            elif is_reopen:
                score = 3  # sibling of an open level with a gap (renumbered/skipped)
            elif level > frontier:
                score = 4  # a deeper child jump (>1 level) — irregular but placeable
            else:
                score = 5  # a shallower reopen of a not-currently-open level
        scored.append((score, level, ordinal))
    if not scored:
        return None
    scored.sort()
    best_score = scored[0][0]
    best_levels = {lvl for sc, lvl, _o in scored if sc == best_score}
    if len(best_levels) > 1:
        return None  # ambiguous: two different levels fit equally well
    return scored[0]


def _resolve_marker_level(
    token: str,
    stack: list[tuple[int, int]],
    *,
    run_in_child: bool,
) -> tuple[int, int] | None:
    """Resolve an enumerator token to one ``(level, ordinal)`` against the open stack.

    ``stack`` is the list of ``(level, ordinal)`` open ancestors, shallow→deep.
    ``run_in_child`` is True when this token follows another marker on the SAME
    line (``(b)(1)``): it must open a child of the marker before it.

    Returns the chosen ``(level, ordinal)``, or ``None`` when the token is not a
    recognised enumerator OR is genuinely ambiguous between two levels that score
    equally (the caller flags it and never guesses).
    """
    scored = _resolve_marker_level_with_score(token, stack, run_in_child=run_in_child)
    if scored is None:
        return None
    return scored[1], scored[2]


def _localname(el: Any) -> str:
    tag = getattr(el, "tag", el)
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


def _is_footnote_ref_sup(el: Any) -> bool:
    """True for an OLRC footnote-reference superscript ``<sup><a href="#X_target">N</a></sup>``.

    The OLRC annual edition tags an editorial footnote reference (``So in original.
    Probably should be ...``) as a ``<sup>`` wrapping an ``<a>`` whose ``href``
    points at the footnote target anchor (``#..._target``). The visible glyph is a
    bare digit that is NOT part of the statutory text — folding it in turns
    ``the number`` into ``number 1`` and manufactures a spurious before/after
    divergence. Every ``<sup><a>`` in these editions is such a reference (verified
    across titles 7/10/42); the ``_target`` href is the precise, conservative test.
    """
    if _localname(el) != "sup":
        return False
    for child in el:
        if _localname(child) == "a" and (child.get("href", "") or "").endswith("_target"):
            return True
    return False


def _element_text(el: etree._Element) -> str:
    """Concatenated descendant text of an element (entities already decoded).

    The text inside an OLRC footnote-reference superscript (:func:`_is_footnote_ref_sup`)
    is dropped — it is an editorial reference marker, never statutory text — while
    the superscript's TAIL (text following it in the parent) is preserved. Comments
    and processing-instructions (the OLRC sprinkles ``<!-- PDFPage:NNN -->`` page-break
    markers mid-paragraph) contribute only their tail, exactly as ``itertext()`` does.
    """
    parts: list[str] = []
    if isinstance(el.text, str):
        parts.append(el.text)
    for child in el:
        if not isinstance(child.tag, str):
            if isinstance(child.tail, str):
                parts.append(child.tail)
            continue
        if _is_footnote_ref_sup(child):
            if isinstance(child.tail, str):
                parts.append(child.tail)
            continue
        parts.append(_element_text(child))
        if isinstance(child.tail, str):
            parts.append(child.tail)
    return "".join(parts)


def _normalize_text(text: str) -> str:
    return " ".join(text.split())


def _statutory_indent_depth(css_class: str) -> int | None:
    """Indent depth for a ``statutory-body*`` class, or None if not statutory.

    ``statutory-body`` is depth 0; ``statutory-body-Nem`` is depth N. The
    ``statutory-body-block*`` / ``statutory-body-flush*`` variants are
    continuation/flush runs with no fresh enumerator — depth is irrelevant to the
    subsection split (they attach to the open unit), so they map to ``-1``.
    """
    if not css_class.startswith("statutory-body"):
        return None
    if css_class == "statutory-body":
        return 0
    m = re.match(r"^statutory-body-(\d+)em$", css_class)
    if m is not None:
        return int(m.group(1))
    # block / flush / hang continuation lines: not a fresh enumerator level.
    return -1


@dataclass(frozen=True)
class UscStatutoryParagraph:
    """One ``statutory-body*`` paragraph: its indent depth and normalized text."""

    indent_depth: int
    css_class: str
    text: str


@dataclass(frozen=True)
class UscSubsectionNode:
    """A STRETCH subsection/paragraph/clause node split from the statutory body.

    ``address`` is the full pinned-convention address (title→section→...→leaf).
    ``text`` is the normalized text of this node's own leading paragraph plus any
    continuation/flush lines that attach to it before the next sibling/child
    enumerator. ``ambiguous`` carries a typed finding reason when the split could
    not be made cleanly (the node is still emitted, flagged, never guessed-away).
    """

    address: LegalAddress
    label: str
    kind: str
    indent_depth: int
    text: str


@dataclass(frozen=True)
class UscSectionNote:
    """One editorial note block under a section: a ``note-head`` and its bodies.

    The USC annual edition carries the temporal mechanics of a section (effective
    dates, termination dates, applicability, sunset/reversion language) in the
    editorial notes, NOT in the statutory text. Each note block is a ``note-head``
    label (e.g. "Effective Date of 2022 Amendment", "Amendments", "Termination
    Date") followed by one or more ``note-body`` paragraphs. This carrier exposes
    those blocks for the sunset/temporal detector WITHOUT folding any note text
    into :attr:`UscSection.statutory_text` — the statutory comparison surface is
    unchanged.
    """

    head: str
    bodies: tuple[str, ...]

    @property
    def text(self) -> str:
        """Head + bodies joined, for substring witness scans."""
        return _normalize_text(" ".join((self.head, *self.bodies)))


@dataclass(frozen=True)
class UscSection:
    """One USC section: address, heading, statutory text, raw source-credit."""

    title: int
    section: str
    heading: str
    address: LegalAddress
    statutory_text: str
    source_credit_raw: str
    repealed: bool
    paragraphs: tuple[UscStatutoryParagraph, ...]
    # Editorial note blocks (note-head + note-body*) following the statutory text.
    # NOT part of the statutory comparison surface; carried for the sunset/temporal
    # detector (effective/termination/applicability/reversion language lives here).
    notes: tuple[UscSectionNote, ...] = ()
    # Structural-trail context from the ``itempath``/``expcite`` comments
    # (chapter / subchapter); containers only, not part of the replay address.
    chapter: str = ""
    subchapter: str = ""

    def to_jsonable(self, *, include_paragraphs: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "title": self.title,
            "section": self.section,
            "heading": self.heading,
            "address": str(self.address),
            "statutory_text_len": len(self.statutory_text),
            "source_credit_present": bool(self.source_credit_raw),
            "repealed": self.repealed,
            "chapter": self.chapter,
            "subchapter": self.subchapter,
        }
        if include_paragraphs:
            payload["paragraphs"] = [
                {"indent_depth": p.indent_depth, "class": p.css_class, "text": p.text}
                for p in self.paragraphs
            ]
        return payload


@dataclass
class UscSourceShapeReport:
    """Honest source-shape report for one parsed USC title document."""

    title: int
    year: str
    section_count: int
    repealed_count: int
    sections_without_source_credit: list[str] = field(default_factory=list)
    sections_without_statutory_text: list[str] = field(default_factory=list)
    findings: list[dict[str, str]] = field(default_factory=list)

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "year": self.year,
            "section_count": self.section_count,
            "repealed_count": self.repealed_count,
            "sections_without_source_credit": self.sections_without_source_credit,
            "sections_without_statutory_text": self.sections_without_statutory_text,
            "findings": self.findings,
        }


@dataclass(frozen=True)
class UscSourceDocument:
    title: int
    year: str
    locator: str
    sections: tuple[UscSection, ...]
    report: UscSourceShapeReport

    def section_by_number(self, number: str) -> UscSection | None:
        for section in self.sections:
            if section.section == number:
                return section
        return None

    def to_jsonable(self, *, include_sections: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "title": self.title,
            "year": self.year,
            "locator": self.locator,
            "report": self.report.to_jsonable(),
        }
        if include_sections:
            payload["sections"] = [s.to_jsonable() for s in self.sections]
        return payload


def usc_section_address(title: int, section: str) -> LegalAddress:
    """Pinned section-level address: ``(("title", "11"), ("section", "362"))``."""
    return LegalAddress(path=(("title", str(int(title))), ("section", section)))


def _comment_value(text: str, prefix: str) -> str:
    """Return the value of a ``prefix:value`` header comment, normalized."""
    body = text.strip()
    if not body.startswith(prefix):
        return ""
    return body[len(prefix):].strip()


def _chapter_subchapter_from_expcite(expcite: str) -> tuple[str, str]:
    """Extract chapter/subchapter container labels from an ``expcite`` comment.

    ``expcite`` is ``TITLE 11-BANKRUPTCY!@!CHAPTER 3-CASE ...!@!SUBCHAPTER IV-...
    !@!Sec. 362``. Containers are returned as bare numbers ("3", "IV"); absent
    levels are empty.
    """
    chapter = ""
    subchapter = ""
    for part in expcite.split("!@!"):
        part = part.strip()
        mc = re.match(r"^CHAPTER\s+([0-9A-Za-z]+)\b", part)
        if mc is not None:
            chapter = mc.group(1)
            continue
        ms = re.match(r"^SUBCHAPTER\s+([0-9A-Za-z]+)\b", part)
        if ms is not None:
            subchapter = ms.group(1)
    return chapter, subchapter


def parse_usc_title_document(
    htm_bytes: bytes,
    *,
    title: int | None = None,
    year: str = "",
    locator: str = "",
) -> UscSourceDocument:
    """Parse a USCODE annual-edition title htm into a typed section tree.

    ``title`` defaults to the ``AUTHORITIES-USC-TITLE-ENUM`` header comment when
    not given. Sections are read in document order; statutory text is the
    concatenation of ``statutory-body*`` paragraphs between
    ``field-start:statute`` and ``field-end:statute``; ``source-credit`` is the
    raw paragraph text; editorial notes/analysis are excluded.
    """
    root = LH.document_fromstring(htm_bytes)

    if title is None:
        title = _extract_title_enum(htm_bytes)
    if title is None:
        raise ValueError("USC title number not given and not found in htm header")

    # The section content lives as flat siblings inside the first content div.
    heads = [
        el
        for el in root.iter("h3")
        if isinstance(el.tag, str) and el.get("class") == "section-head"
    ]
    container = heads[0].getparent() if heads else None
    if not heads or container is None:
        report = UscSourceShapeReport(title=title, year=year, section_count=0, repealed_count=0)
        report.findings.append(
            {
                "rule_id": "us_usc_no_sections_found",
                "reason": "no <h3 class='section-head'> elements in document",
            }
        )
        return UscSourceDocument(
            title=title, year=year, locator=locator, sections=(), report=report
        )

    sections = _walk_sections(container, title=title)

    report = UscSourceShapeReport(
        title=title,
        year=year,
        section_count=len(sections),
        repealed_count=sum(1 for s in sections if s.repealed),
    )
    seen: dict[str, str] = {}
    for s in sections:
        if not s.source_credit_raw and not s.repealed:
            report.sections_without_source_credit.append(s.section)
        if not s.statutory_text and not s.repealed:
            report.sections_without_statutory_text.append(s.section)
        if s.section in seen:
            report.findings.append(
                {
                    "rule_id": "us_usc_duplicate_section_number",
                    "section": s.section,
                    "reason": "duplicate section number within title",
                }
            )
        seen[s.section] = s.heading

    return UscSourceDocument(
        title=title, year=year, locator=locator, sections=tuple(sections), report=report
    )


def _extract_title_enum(htm_bytes: bytes) -> int | None:
    m = re.search(rb"<!--\s*AUTHORITIES-USC-TITLE-ENUM:\s*(\d+)\s*-->", htm_bytes[:8192])
    if m is None:
        return None
    return int(m.group(1))


def _walk_sections(container: etree._Element, *, title: int) -> list[UscSection]:
    """Walk the flat child list, grouping each section's parts by field markers.

    State machine over document-order siblings:
      * a ``documentid``/``itempath``/``expcite`` comment refreshes pending
        structural context,
      * ``field-start:statute`` / ``field-end:statute`` gate statutory bodies,
      * ``field-start:sourcecredit`` / ``field-end:sourcecredit`` gate the credit,
      * an ``<h3 class='section-head'>`` opens a new section.
    """
    sections: list[UscSection] = []

    current: dict[str, Any] | None = None
    pending_expcite = ""
    in_statute = False
    in_sourcecredit = False

    def _flush() -> None:
        if current is None:
            return
        paragraphs = tuple(current["paragraphs"])
        statutory_text = _normalize_text(
            " ".join(p.text for p in paragraphs)
        )
        chapter, subchapter = _chapter_subchapter_from_expcite(current["expcite"])
        notes = tuple(
            UscSectionNote(head=head, bodies=tuple(bodies))
            for head, bodies in current["notes"]
        )
        sections.append(
            UscSection(
                title=title,
                section=current["section"],
                heading=current["heading"],
                address=usc_section_address(title, current["section"]),
                statutory_text=statutory_text,
                source_credit_raw=current["source_credit"],
                repealed=current["repealed"],
                paragraphs=paragraphs,
                notes=notes,
                chapter=chapter,
                subchapter=subchapter,
            )
        )

    for el in container:
        if isinstance(el, etree._Comment):
            ctext = (el.text or "").strip()
            if ctext.startswith("expcite:"):
                pending_expcite = _comment_value(ctext, "expcite:")
            elif ctext == "field-start:statute":
                in_statute = True
            elif ctext == "field-end:statute":
                in_statute = False
            elif ctext == "field-start:sourcecredit":
                in_sourcecredit = True
            elif ctext == "field-end:sourcecredit":
                in_sourcecredit = False
            continue

        if not isinstance(el.tag, str):
            continue

        css = el.get("class", "")

        if _localname(el) == "h3" and css == "section-head":
            _flush()
            head_text = _normalize_text(_element_text(el))
            number, repealed = _parse_section_head(head_text)
            current = {
                "section": number,
                "heading": _section_heading(head_text),
                "repealed": repealed,
                "expcite": pending_expcite,
                "paragraphs": [],
                "source_credit": "",
                # list[tuple[head, list[body]]] in document order.
                "notes": [],
            }
            in_statute = False
            in_sourcecredit = False
            continue

        if current is None:
            continue

        if in_statute and css.startswith("statutory-body"):
            depth = _statutory_indent_depth(css)
            current["paragraphs"].append(
                UscStatutoryParagraph(
                    indent_depth=depth if depth is not None else -1,
                    css_class=css,
                    text=_normalize_text(_element_text(el)),
                )
            )
            continue

        if in_sourcecredit and css == "source-credit":
            current["source_credit"] = _normalize_text(_element_text(el))
            continue

        # Editorial note blocks: a ``note-head`` opens a block, ``note-body``
        # paragraphs attach to the open block. These are NOT statutory text; they
        # carry the section's temporal mechanics for the sunset detector. Gated to
        # outside the statute/sourcecredit runs so it cannot capture statutory body.
        if not in_statute and not in_sourcecredit:
            if css == "note-head":
                current["notes"].append((_normalize_text(_element_text(el)), []))
                continue
            if css == "note-body" and current["notes"]:
                current["notes"][-1][1].append(_normalize_text(_element_text(el)))
                continue

    _flush()
    return sections


def _parse_section_head(head_text: str) -> tuple[str, bool]:
    """Return (section_number, repealed) from a section-head text."""
    m = _SECTION_HEAD_RE.match(head_text)
    number = m.group("num") if m is not None else head_text
    repealed = bool(
        re.search(r"\b(Repealed|Omitted|Transferred|Renumbered|Vacant)\b", head_text)
    )
    return number, repealed


def _section_heading(head_text: str) -> str:
    """The human heading following the section number ('§362. Automatic stay')."""
    m = _SECTION_HEAD_RE.match(head_text)
    if m is None:
        return head_text
    return head_text[m.end():].strip().rstrip("]").strip()


# An "amend ... to read as follows" payload supplies the WHOLE replacement
# section as a USLM quotedText — and that quotedText opens with the section's own
# catchline (``§ 2196. Manufacturing engineering education program``) before the
# first statutory body unit, which is wrapped in nested curly quotes (``“(a) …``).
# The OLRC consolidated comparison surface
# (:attr:`UscSection.statutory_text`) carries NEITHER the catchline (it is parsed
# into :attr:`UscSection.heading`, not the body) NOR the nested quotes. Projecting
# a whole-section replacement payload onto that body-only surface therefore means
# dropping the leading catchline, exactly as the oracle parse does on its side.
#
# The body opens at the first curly OPEN double-quote (``“``): USC headings never
# contain a ``“``, so that character unambiguously marks the catchline/body
# boundary. The strip is gated on the catchline carrying the SAME section number
# as the target (``§ <num>.``) so it can only ever remove this section's own
# catchline, never a leading reference that happens to start with ``§``.
_SECTION_CATCHLINE_PREFIX_RE = re.compile(
    r"^\s*\[?\s*§+\s*(?P<num>[0-9]+[A-Za-z]*(?:[-‐‑‒–][0-9]+[A-Za-z]*)?)\.\s*"
)


def strip_replacement_section_catchline(payload: str, section_number: str) -> str | None:
    """Strip a leading ``§ <num>. <heading>`` catchline from a replacement payload.

    Returns the payload with the catchline removed when ALL of the following hold,
    else ``None`` (the caller keeps the payload verbatim — never guesses a cut):

    * the payload opens with a ``§ <num>.`` catchline whose number equals
      ``section_number`` (this section's own catchline, not a cross-reference), and
    * a curly open double-quote (``“``) — the nested quotedText body marker — occurs
      after that catchline.

    The removed span is exactly ``§ <num>. <heading>`` up to (not including) the
    first ``“``. This mirrors the oracle-side parse, which puts the catchline in
    :attr:`UscSection.heading` and never in :attr:`UscSection.statutory_text`; it is
    a comparison-surface projection of a faithful materialization, not a repair of
    the text to the oracle. When the catchline cannot be delimited this way (no
    body-marker quote, or the number does not match) the payload is returned
    untouched via ``None`` rather than risk cutting into a heading that itself
    contains periods (``§ 810. Art. 10. Restraint …``).
    """
    m = _SECTION_CATCHLINE_PREFIX_RE.match(payload)
    if m is None or m.group("num") != section_number:
        return None
    body_marker = payload.find("“", m.end())
    if body_marker == -1:
        return None
    return payload[body_marker:]


def synthetic_usc_section(
    *,
    title: int,
    section: str,
    text: str,
    heading: str = "",
) -> UscSection:
    """Build a best-effort :class:`UscSection` from plain text.

    Used to seed a synthetic before-section for a section that does not exist in
    the before edition but is created by a window-level Public Law INSERT, so that
    later sub-section operations (e.g. an amend-to-read of paragraph (1)) can locate
    nodes within the newly created section. The text is split into
    :class:`UscStatutoryParagraph` paragraphs at structural ``(token)`` markers so
    :func:`split_statutory_subsections` can run on it.

    This is a fallback construction, not a source parse: it assumes markers that
    open a fresh unit are not preceded by non-whitespace text. It never fabricates
    structural information beyond what the text itself carries.
    """
    normalized = _normalize_text(text)
    paragraphs: list[UscStatutoryParagraph] = []
    # Build a paragraph-breach scan surface where curly/straight quote boundaries act
    # like whitespace: replacement payloads often wrap structural units in nested
    # quotes (``“In this subchapter:“(1) Debtor...``). We scan this surface for
    # markers but slice the ORIGINAL normalized text so quote glyphs remain in the
    # stored paragraph text (the marker substring is still locatable inside the
    # original text).
    scan_surface = (
        normalized.replace("\"", " ")
        .replace("“", " ")
        .replace("”", " ")
        .replace("‘", " ")
        .replace("’", " ")
    )
    marker_re = _SCANNER_MARKER_RE
    prev_end = 0
    for m in marker_re.finditer(scan_surface):
        body = normalized[prev_end : m.start()].strip()
        if body:
            if paragraphs:
                # Continuation text attaches to the currently open node.
                last = paragraphs[-1]
                paragraphs[-1] = UscStatutoryParagraph(
                    indent_depth=last.indent_depth,
                    css_class=last.css_class,
                    text=_normalize_text(f"{last.text} {body}"),
                )
            else:
                # Leading body text before the first marker.
                paragraphs.append(
                    UscStatutoryParagraph(indent_depth=-1, css_class="", text=body)
                )
        next_m = marker_re.search(scan_surface, m.end())
        end = next_m.start() if next_m is not None else len(normalized)
        node_text = _normalize_text(normalized[m.start() : end])
        # A nested/straight quote that sits immediately before the next structural
        # marker (or at the end of the payload) is a source wrapper, not part of the
        # node's statutory text. Drop it so node replacements do not swallow the
        # boundary between adjacent units.
        node_text = node_text.rstrip('"\u201c\u201d\u2018\u2019').strip()
        paragraphs.append(
            UscStatutoryParagraph(
                indent_depth=0,
                css_class="",
                text=node_text,
            )
        )
        prev_end = end
    tail = normalized[prev_end:].strip()
    if tail and paragraphs:
        last = paragraphs[-1]
        paragraphs[-1] = UscStatutoryParagraph(
            indent_depth=last.indent_depth,
            css_class=last.css_class,
            text=_normalize_text(f"{last.text} {tail}"),
        )
    if not paragraphs:
        paragraphs.append(
            UscStatutoryParagraph(indent_depth=-1, css_class="", text=normalized)
        )
    return UscSection(
        title=title,
        section=section,
        heading=heading,
        address=usc_section_address(title, section),
        statutory_text=normalized,
        source_credit_raw="",
        repealed=False,
        paragraphs=tuple(paragraphs),
        notes=(),
        chapter="",
        subchapter="",
    )


def split_statutory_subsections(
    section: UscSection,
) -> tuple[tuple[UscSubsectionNode, ...], list[dict[str, str]]]:
    """Split a section's statutory body into subsection/paragraph/clause nodes.

    Maps each enumerated ``statutory-body*`` paragraph to the pinned USC address
    convention by its leading ``(token)`` marker plus indent depth:

        depth 0 ``(a)`` -> subsection, 1 ``(1)`` -> paragraph,
        2 ``(A)`` -> subparagraph, 3 ``(i)`` -> clause, 4+ deeper.

    Returns ``(nodes, findings)``. A node whose indent depth jumps by more than
    one level from the current open ancestor, or a non-leading-marker paragraph
    that is not a recognised continuation/flush line, is emitted with a typed
    ``us_usc_subsection_parse_ambiguous`` finding rather than guessed into a
    fabricated parent. This is conservative MVP-stretch structure: clean ladders
    parse; irregular shapes are flagged, never silently restructured.
    """
    nodes: list[UscSubsectionNode] = []
    findings: list[dict[str, str]] = []

    # Stack of (level, ordinal) open ancestors, shallow→deep; the address is
    # title/section + each open ancestor's (ladder-kind, label-token).
    stack: list[tuple[int, int]] = []
    # The label token for each open ancestor level, parallel to ``stack``.
    label_stack: list[str] = []
    base_path = section.address.path

    def _attach_continuation(text: str) -> None:
        """Append a flush/continuation line's text to the currently-open node."""
        if nodes:
            last = nodes[-1]
            nodes[-1] = UscSubsectionNode(
                address=last.address,
                label=last.label,
                kind=last.kind,
                indent_depth=last.indent_depth,
                text=_normalize_text(f"{last.text} {text}"),
            )
        else:
            findings.append(
                {
                    "rule_id": _SUBSECTION_PARSE_AMBIGUOUS,
                    "section": section.section,
                    "reason": "statutory text precedes the first enumerated marker",
                    "text_preview": text[:80],
                }
            )

    for para in section.paragraphs:
        # A paragraph's leading text may carry a RUN of markers (``(b)(1) ...``),
        # each opening one level deeper than the last. Peel the leading run.
        run_tokens: list[str] = []
        rest = para.text
        m = _MARKER_RE.match(rest)
        while m is not None:
            run_tokens.append(m.group("token"))
            rest = rest[m.end():]
            # Only continue the run when the next marker DIRECTLY abuts the prior
            # one (no intervening character): ``(b)(1)`` is a run-in head; ``(a) The
            # court ...`` (prose after the marker) is a single enumerator. This
            # abutment is exactly what distinguishes a run-in head from ordinary
            # body text that merely contains parenthesised cross-references.
            m = _MARKER_RE.match(rest)

        # The OLRC also renders the parent and child markers separated by a short
        # catchline and an em/en-dash: ``(c) Structure.—(1) The Service ...``.  When
        # the first marker is followed by such a dash + marker, treat the second as
        # a run-in child structural opener so amendments targeting ``paragraph:(1)``
        # can be located.  Conservatively require the parent marker to resolve as
        # a clean structural container: either it is unambiguous about its level,
        # or it is the natural next sibling/first-child of the deepest open level.
        # Ambiguous letters that only fit as a shallower reopen (e.g. the trailing
        # ``(i)`` in a deep ladder) keep their dash child as prose.
        if run_tokens and not rest.lstrip().startswith("("):
            dash_m = _MARKER_AFTER_DASH_RE.match(rest)
            if dash_m is not None:
                child_token = dash_m.group("token")
                # Only treat as structural when the candidate child's token level
                # cleanly follows the parent marker's level (e.g. (a)->(1) or
                # (1)->(A)).  Otherwise it is likely cross-reference prose.
                parent_token = run_tokens[-1]
                child_interps = _marker_interpretations(child_token)
                if len(child_interps) == 1:
                    parent_resolved = _resolve_marker_level(
                        parent_token, stack, run_in_child=False
                    )
                    if parent_resolved is not None:
                        parent_level, _parent_ordinal = parent_resolved
                        child_level = child_interps[0][0]
                        if child_level > parent_level:
                            run_tokens.append(child_token)

        if not run_tokens:
            # A flush, unindented block paragraph (e.g. ``Paragraph (4) shall not
            # be construed...``) is a structural sibling under the current open
            # unit, not a continuation of its deepest child. Closing to the parent
            # keeps amendments like ``inserting after paragraph (10)`` from being
            # spliced inside the prior node's intro text.
            if para.css_class == "statutory-body-block" and stack:
                parent_level = stack[-1][0] if len(stack) == 1 else stack[-2][0]
                while stack and stack[-1][0] > parent_level:
                    stack.pop()
                    label_stack.pop()
                new_level = parent_level + 1
                new_path = base_path + tuple(
                    (
                        _USC_LADDER[lvl] if lvl < len(_USC_LADDER) else f"level{lvl}",
                        lbl,
                    )
                    for (lvl, _o), lbl in zip(stack, label_stack, strict=True)
                ) + (
                    (
                        _USC_LADDER[new_level]
                        if new_level < len(_USC_LADDER)
                        else f"level{new_level}",
                        "",
                    ),
                )
                nodes.append(
                    UscSubsectionNode(
                        address=LegalAddress(path=new_path),
                        label="",
                        kind=_USC_LADDER[new_level]
                        if new_level < len(_USC_LADDER)
                        else f"level{new_level}",
                        indent_depth=new_level,
                        text=_normalize_text(para.text),
                    )
                )
                stack.append((new_level, 0))
                label_stack.append("")
                continue
            # Continuation / flush / block line, or a paragraph with no leading
            # enumerator: attach text to the currently-open node.
            _attach_continuation(para.text)
            continue

        opened_any = False
        for index, token in enumerate(run_tokens):
            resolved = _resolve_marker_level(
                token, stack, run_in_child=(index > 0)
            )
            if resolved is None:
                findings.append(
                    {
                        "rule_id": _SUBSECTION_PARSE_AMBIGUOUS,
                        "section": section.section,
                        "reason": "enumerator marker is unrecognised or ambiguous between levels",
                        "marker": token,
                        "text_preview": para.text[:80],
                    }
                )
                # Stop processing this line's run: the remaining tokens depend on a
                # level we could not place. Whatever opened so far still carries the
                # full line text via the node appended below.
                break
            level, ordinal = resolved
            # Pop ancestors at or deeper than this level.
            while stack and stack[-1][0] >= level:
                stack.pop()
                label_stack.pop()
            stack.append((level, ordinal))
            label_stack.append(token)
            kind = _USC_LADDER[level] if level < len(_USC_LADDER) else f"level{level}"
            path = base_path + tuple(
                (_USC_LADDER[lvl] if lvl < len(_USC_LADDER) else f"level{lvl}", lbl)
                for (lvl, _o), lbl in zip(stack, label_stack, strict=True)
            )
            nodes.append(
                UscSubsectionNode(
                    address=LegalAddress(path=path),
                    label=token,
                    kind=kind,
                    indent_depth=level,
                    # Every marker on a run-in line shares the line's full text: the
                    # container ``(b)`` and its run-in child ``(b)(1)`` both anchor on
                    # the same ``(b)(1) ...`` span, which is faithful for locating.
                    text=_normalize_text(para.text),
                )
            )
            opened_any = True
        if not opened_any:
            _attach_continuation(para.text)

    return tuple(nodes), findings


def iter_section_oracle_rows(
    document: UscSourceDocument,
) -> Iterable[tuple[LegalAddress, str, str]]:
    """Yield (address, normalized_statutory_text, source_credit_raw) per section.

    The section-level oracle surface a dry-run replay compares against.
    """
    for section in document.sections:
        yield section.address, section.statutory_text, section.source_credit_raw


def iter_section_notes(
    section: UscSection,
) -> Iterable[UscSectionNote]:
    """Yield the editorial note blocks (note-head + bodies) for one section.

    Convenience iterator over :attr:`UscSection.notes`. The note blocks carry the
    temporal mechanics (effective/termination/applicability/reversion language)
    the sunset detector consults; they are never part of the statutory comparison
    surface.
    """
    yield from section.notes


def summarize_indent_classes(document: UscSourceDocument) -> dict[str, int]:
    """Histogram of statutory-body indent classes across the document."""
    counter: Counter[str] = Counter()
    for section in document.sections:
        for para in section.paragraphs:
            counter[para.css_class] += 1
    return dict(sorted(counter.items()))


# ---------------------------------------------------------------------------
# USLM 1.0 release-point XML parser
# ---------------------------------------------------------------------------
#
# The OLRC release-point XML (``xmlns="http://xml.house.gov/schemas/uslm/1.0"``,
# NOT the PLAW 2.x ``http://schemas.gpo.gov/xml/uslm`` USLM GPO namespace used
# in :mod:`lawvm.us_federal.import_release`) carries a USC title as a real
# nested tree: ``<section>`` → ``<subsection>`` → ``<paragraph>`` →
# ``<subparagraph>`` → ``<clause>`` → ``<subclause>``. That structural nesting
# is authoritative, so the USLM parser does NOT run the indent-depth heuristic
# :func:`split_statutory_subsections` (which recovers nesting from ``(a)`` /
# ``(1)`` markers + CSS indent depth on the flat OLRC annual-edition htm). It
# walks the XML element nesting directly via :func:`split_uslm_subsections`.
#
# Quoted-text pseudo-sections (sections of the Code as it once stood, embedded
# in an amendment note as a former-text witness) appear inside ``<notes>`` /
# ``<note>`` blocks with NO ``identifier`` attribute (or one that is not a USC
# identifier). They are editorial evidence about former text, never live USC,
# so the section iterator SKIPS the ``<notes>`` / ``<note>`` subtrees entirely
# rather than relying on identifier-shape sniffing — a quoted-text ghost MAY
# carry a USC-shaped identifier (``/us/usc/t10/s6`` from a former-text quote)
# and would otherwise duplicate a live section's number.

# USLM 1.0 namespace (OLRC release-point; see note above on namespace choice).
USLM_1_0_NS = "http://xml.house.gov/schemas/uslm/1.0"
_USLM_NS_PREFIX = f"{{{USLM_1_0_NS}}}"

# Element local names that mark nesting levels INSIDE a <section>, paired with
# the USC enumeration-ladder index (0 = subsection, 1 = paragraph, ...). Each
# such element opens one deeper level of the structural split.
_USLM_LEVEL_ELEMENTS: tuple[str, ...] = (
    "subsection",
    "paragraph",
    "subparagraph",
    "clause",
    "subclause",
    "item",
    "sub-item",
)
_USLM_LEVEL_INDEX: dict[str, int] = {name: i for i, name in enumerate(_USLM_LEVEL_ELEMENTS)}

# Direct children of <section> that are NOT statutory text body — they are
# section metadata (the ``<num>`` §101 marker, the ``<heading>`` catchline),
# editorial lineage (``<sourceCredit>``, ``<notes>``, ``<note>``), or
# navigation (``<toc>``). Excluded from the section's statutory-text
# concatenation and from the paragraph list. Subsection-level ``<heading>``
# (e.g. ``In General.—``) is INCLUDED in statutory text — it is body text
# that the OLRC annual-edition htm renders inline on the ``(a)`` line.
_USLM_SECTION_SKIP_DIRECT_CHILDREN: frozenset[str] = frozenset(
    {"num", "heading", "sourceCredit", "notes", "note", "toc"}
)

# ``status`` attribute values that mark a section as no-longer-in-force.
# Reproduces the existing parser's ``Repealed|Omitted|Transferred|Renumbered
# |Vacant`` head-text check, with the USLM ``status`` attribute as the native
# first source and the head-text regex as a fallback (handles editions that
# didn't set the status attribute but bracketed the head with ``[Repealed. ...]``).
_USLM_REPEALED_STATUSES: frozenset[str] = frozenset(
    {"repealed", "omitted", "transferred", "renumbered", "vacant"}
)

# Bounded LRU cache for parsed USLM trees. A USLM title is ~35 MB, so the cap
# is intentionally small: 4 trees 140 MB maximum (per AGENTS.md §2.7
# source-root cache lifecycle). The cache is keyed on the SHA-256 of the
# bytes (data identity, not object identity) so multiple ``archive.get``
# calls for the same locator reuse the same parsed root. Clearing on overflow
# is crude but bounded — the realistic workload is one USLM title per run.
_USLM_TREE_CACHE_MAX = 4
_uslm_tree_cache: dict[str, ET.Element] = {}


def _uslm_localname(tag: object) -> str:
    """Local element name, stripping the ``{namespace}`` prefix from a USLM tag.

    ``Element.tag`` is either a ``str`` (possibly namespaced as
    ``"{ns}localname"``), a function (for comments/PIs, checked via
    ``isinstance(tag, str)`` upstream), or another non-string value. Returns
    the empty string for non-string tags.
    """
    if not isinstance(tag, str):
        return ""
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def _uslm_subtree_text(el: ET.Element) -> str:
    """Concatenated descendant text of a USLM element, in document order.

    Equivalent to :func:`_element_text` for lxml but written against
    :mod:`xml.etree.ElementTree` (stdlib, the parser the USLM release-point
    path uses per the small well-formed XML assumption). Comments and processing
    instructions contribute their TAIL (text after them in the parent) exactly
    as ``itertext`` would — preserving the ``<!-- PDFPage:N -->`` carcasses that
    appear mid-paragraph in some USLM exports.
    """
    parts: list[str] = []
    if isinstance(el.text, str):
        parts.append(el.text)
    for child in el:
        if not isinstance(child.tag, str):
            if isinstance(child.tail, str):
                parts.append(child.tail)
            continue
        parts.append(_uslm_subtree_text(child))
        if isinstance(child.tail, str):
            parts.append(child.tail)
    return "".join(parts)


def _iter_uslm_section_elements(root: ET.Element) -> Iterator[ET.Element]:
    """Yield every live USC ``<section>`` element in USLM document order.

    Short-circuits at ``<notes>`` and ``<note>`` subtrees — those contain
    quoted-text pseudo-sections (former-text witnesses from amendment notes)
    that carry no live USC authority and would otherwise produce
    ``us_usc_duplicate_section_number`` findings for every repealed/replaced
    section in the title. The identifier-shape sniff (``/us/usc/t``) is the
    secondary filter, NOT the primary exclusion — some ghosts DO carry a
    USC-shaped identifier (e.g. ``/us/usc/t10/s6`` quoted inside the title's
    misc-notes block) and only the notes-skip excludes them.
    """
    q_section = f"{_USLM_NS_PREFIX}section"
    q_notes = f"{_USLM_NS_PREFIX}notes"
    q_note = f"{_USLM_NS_PREFIX}note"

    def _walk(el: ET.Element) -> Iterator[ET.Element]:
        for child in el:
            if not isinstance(child.tag, str):
                continue
            if child.tag == q_notes or child.tag == q_note:
                continue
            if (
                child.tag == q_section
                and (child.get("identifier") or "").startswith("/us/usc/t")
            ):
                yield child
            yield from _walk(child)

    return _walk(root)


def _parse_uslm_tree(uslm_bytes: bytes) -> ET.Element:
    """Parse USLM XML bytes with a bounded cache keyed on SHA-256.

    The cache lives at module scope and is bounded by
    :data:`_USLM_TREE_CACHE_MAX`. ``archive.get`` returns a fresh bytes object
    every call, so the cache key is the bytes content identity (SHA-256) —
    object identity (``id``) would miss the reuse case.
    """
    digest = hashlib.sha256(uslm_bytes).hexdigest()
    cached = _uslm_tree_cache.get(digest)
    if cached is not None:
        return cached
    tree = ET.fromstring(uslm_bytes)
    if len(_uslm_tree_cache) >= _USLM_TREE_CACHE_MAX:
        _uslm_tree_cache.clear()
    _uslm_tree_cache[digest] = tree
    return tree


def _uslm_section_number(section_el: ET.Element) -> str:
    """Bare USC section number from a ``<section>`` element.

    Prefers the ``<num value="...">`` attribute (e.g. ``101``, ``949p–1``)
    and falls back to the trailing ``sNNN`` segment of the ``identifier``
    URL (``/us/usc/t10/s949p–1`` → ``949p–1``). The returned token is
    verbatim — en-dashes (U+2013), letter suffixes (``16131a``), and dotted
    decimals are preserved, so the same token round-trips through the
    amendatory target parsers and the :func:`usc_section_address` pinned
    convention.
    """
    q_num = f"{_USLM_NS_PREFIX}num"
    for child in section_el:
        if child.tag == q_num:
            value = child.get("value", "")
            if value:
                return value
            break
    ident = section_el.get("identifier", "")
    m = re.search(r"/s([^/]+)$", ident)
    if m is not None:
        return m.group(1)
    return ""


def _uslm_section_heading(section_el: ET.Element) -> str:
    """Normalized section heading text (the catchline, e.g. ``Definitions``)."""
    q_heading = f"{_USLM_NS_PREFIX}heading"
    for child in section_el:
        if child.tag == q_heading:
            return _normalize_text(_uslm_subtree_text(child))
    return ""


def _uslm_section_source_credit(section_el: ET.Element) -> str:
    """Normalized raw source-credit text from ``<sourceCredit>`` if present."""
    q_source_credit = f"{_USLM_NS_PREFIX}sourceCredit"
    for child in section_el:
        if child.tag == q_source_credit:
            return _normalize_text(_uslm_subtree_text(child))
    return ""


def _uslm_section_status_repealed(section_el: ET.Element, heading: str) -> bool:
    """True if the section is repealed/omitted/transferred/renumbered/vacant.

    Source signals, in priority order: the ``status`` attribute on the
    ``<section>`` element (USLM native — e.g. ``status="repealed"``), then the
    head-text regex used by the annual-edition htm parser for editions that
    bracket the head as ``[§304. Repealed. ...]``.
    """
    status = section_el.get("status", "")
    if status and status.lower() in _USLM_REPEALED_STATUSES:
        return True
    return bool(re.search(r"\b(Repealed|Omitted|Transferred|Renumbered|Vacant)\b", heading))


def _uslm_section_paragraphs(section_el: ET.Element) -> tuple[UscStatutoryParagraph, ...]:
    """Build one :class:`UscStatutoryParagraph` per direct statutory child.

    Mirrors the annual-edition parser's per-paragraph shape (one record per
    structural unit) so downstream consumers that walk
    :attr:`UscSection.paragraphs` keep working unchanged. Each paragraph holds
    the full subtree text of its source element (including the ``(a)`` marker
    from the embedded ``<num>``), so :func:`split_statutory_subsections` can
    still be applied — but the USLM-canonical structural walk is
    :func:`split_uslm_subsections`, which reads the XML nesting directly.

    Direct children classified as non-statutory (``num``, ``heading``,
    ``sourceCredit``, ``notes``, ``note``, ``toc``) are excluded. ``<content>``
    (a section's direct body, for sections without structural nesting) is
    emitted at indent depth 0. Unrecognized direct children are emitted at
    indent depth 0 with a ``uslm-<localname>`` css_class so they surface in the
    indent-class histogram rather than silently vanishing.
    """
    paragraphs: list[UscStatutoryParagraph] = []
    for child in section_el:
        if not isinstance(child.tag, str):
            continue
        local = _uslm_localname(child.tag)
        if local in _USLM_SECTION_SKIP_DIRECT_CHILDREN:
            continue
        text = _normalize_text(_uslm_subtree_text(child))
        if not text:
            continue
        if local in _USLM_LEVEL_INDEX:
            depth = _USLM_LEVEL_INDEX[local]
            css_class = f"uslm-{local}"
        elif local == "content":
            depth = 0
            css_class = "uslm-content"
        else:
            depth = 0
            css_class = f"uslm-{local}"
        paragraphs.append(
            UscStatutoryParagraph(indent_depth=depth, css_class=css_class, text=text)
        )
    return tuple(paragraphs)


def parse_uslm_title_document(
    uslm_bytes: bytes,
    *,
    title: int,
    year: str = "",
    locator: str = "",
) -> UscSourceDocument:
    """Parse a USLM 1.0 USC title document into a typed section tree.

    The release-point USLM (``xmlns="http://xml.house.gov/schemas/uslm/1.0"``
    — note this is the OLRC USLM 1.0 namespace, NOT the PLAW 2.x GPO USLM
    namespace at ``http://schemas.gpo.gov/xml/uslm`` kept distinct in
    :mod:`lawvm.us_federal.import_release`) produces the SAME
    :class:`UscSourceDocument` shape :func:`parse_usc_title_document` returns
    from the annual-edition htm, so downstream consumers (the dry-run kernel,
    the sunset detector, the witness denominator parser) can swap between
    sources without a separate type.

    For each ``<section identifier="/us/usc/tN/sNNN">`` in the document
    (excluding ``<notes>`` / ``<note>`` subtrees — see
    :func:`_iter_uslm_section_elements`), the parser:

      * derives the section number from ``<num value="...">`` (verbatim —
        en-dashes and letter suffixes preserved);
      * extracts the heading from ``<heading>``;
      * extracts the source-credit from ``<sourceCredit>`` if present;
      * builds :class:`UscStatutoryParagraph` records from the direct
        statutory children (one per ``<subsection>`` / ``<paragraph>`` /
        direct ``<content>``);
      * flags ``repealed`` from the ``status="repealed"`` attribute or the
        head-text ``Repealed|Omitted|Transferred|Renumbered|Vacant`` regex.

    ``title`` is required — the USLM root does not carry the integer title
    number in a single canonical attribute (``<docNumber>`` exists in ``<meta>``
    but is metadata-layer, not structural).
    """
    root = _parse_uslm_tree(uslm_bytes)
    sections: list[UscSection] = []
    findings: list[dict[str, str]] = []
    seen: dict[str, str] = {}

    for section_el in _iter_uslm_section_elements(root):
        section_number = _uslm_section_number(section_el)
        heading = _uslm_section_heading(section_el)
        repealed = _uslm_section_status_repealed(section_el, heading)
        source_credit = _uslm_section_source_credit(section_el)
        paragraphs = _uslm_section_paragraphs(section_el)

        if not section_number:
            findings.append(
                {
                    "rule_id": "us_uslm_section_without_number",
                    "identifier": section_el.get("identifier", ""),
                    "reason": (
                        "<section> has no <num value='...'> and no "
                        "identifier-suffix section number"
                    ),
                }
            )
            continue

        # Statutory text = normalized concatenation of paragraph texts (each
        # paragraph is itself a normalized subtree, so we re-join with " " and
        # re-normalize to collapse the inter-paragraph whitespace uniformly).
        statutory_text = _normalize_text(" ".join(p.text for p in paragraphs))

        sections.append(
            UscSection(
                title=title,
                section=section_number,
                heading=heading,
                address=usc_section_address(title, section_number),
                statutory_text=statutory_text,
                source_credit_raw=source_credit,
                repealed=repealed,
                paragraphs=paragraphs,
                notes=(),
                chapter="",
                subchapter="",
            )
        )

        if section_number in seen:
            findings.append(
                {
                    "rule_id": "us_usc_duplicate_section_number",
                    "section": section_number,
                    "reason": "duplicate section number within title (USLM parse)",
                }
            )
        seen[section_number] = heading

    report = UscSourceShapeReport(
        title=title,
        year=year,
        section_count=len(sections),
        repealed_count=sum(1 for s in sections if s.repealed),
    )
    for s in sections:
        if not s.source_credit_raw and not s.repealed:
            report.sections_without_source_credit.append(s.section)
        if not s.statutory_text and not s.repealed:
            report.sections_without_statutory_text.append(s.section)
    report.findings.extend(findings)

    return UscSourceDocument(
        title=title, year=year, locator=locator, sections=tuple(sections), report=report
    )


def split_uslm_subsections(
    section: UscSection, uslm_bytes: bytes
) -> tuple[tuple[UscSubsectionNode, ...], list[dict[str, str]]]:
    """Walk the USLM XML element of one section to produce structural nodes.

    Replaces :func:`split_statutory_subsections` (the indent-depth heuristic
    that recovers nesting from ``(a)`` / ``(1)`` / ``(A)`` markers + CSS
    indent on the flat OLRC annual-edition htm) for USLM sources: this walker
    reads the XML's own ``<subsection>`` → ``<paragraph>`` → ``<subparagraph>``
    nesting directly. The pinned USC address convention is identical
    (:func:`usc_section_address` plus ladder-kind/label pairs).

    For each ``<subsection>`` / ``<paragraph>`` / ``<subparagraph>`` /
    ``<clause>`` / ``<subclause>`` / ``<item>`` / ``<sub-item>`` element under
    the section, one :class:`UscSubsectionNode` is emitted with:

      * ``address`` — :func:`usc_section_address` extended with the
        ``(kind, label)`` path through the open-ancestor stack;
      * ``label`` — the ``<num value="...">`` token (e.g. ``"a"``, ``"1"``,
        ``"A"``); empty string when no ``<num>`` is present, with a typed
        ``us_uslm_node_without_num`` finding rather than a fabricated label;
      * ``kind`` — the element local name;
      * ``indent_depth`` — the structural nesting level (subsection = 0,
        paragraph = 1, ... — matches :data:`_USLM_LEVEL_ELEMENTS` order);
      * ``text`` — the normalized direct text of the element (its ``<num>``,
        ``<heading>``, ``<chapeau>``, ``<content>`` text — NOT descendant
        subsections'/paragraphs' content, which get their own nodes).

    Returns ``(nodes, findings)``. The section's own ``<section>`` element is
    located in the blob by identifier match (``/us/usc/tN/sNNN``); if missing,
    a typed ``us_uslm_section_not_located_in_blob`` finding is emitted and an
    empty node list is returned — never a guessed blob section.
    """
    root = _parse_uslm_tree(uslm_bytes)
    section_el = _find_uslm_section_element(root, section.title, section.section)
    if section_el is None:
        return (
            (),
            [
                {
                    "rule_id": "us_uslm_section_not_located_in_blob",
                    "section": section.section,
                    "title": str(section.title),
                    "reason": (
                        "no <section> in the USLM blob carries this section's identifier"
                    ),
                }
            ],
        )
    return _walk_uslm_sub_section_tree(section_el, section.address)


def _find_uslm_section_element(
    root: ET.Element, title: int, section_number: str
) -> ET.Element | None:
    """Locate the ``<section>`` element for ``title`` / ``section_number``.

    The USLM ``identifier`` is ``/us/usc/t{title}/s{section}`` with the
    section number carried verbatim (en-dashes and letter suffixes preserved
    on both sides), so a single string compare is authoritative. The walk
    skips ``<notes>`` / ``<note>`` subtrees for the same reason
    :func:`_iter_uslm_section_elements` does — quoted-text ghosts may carry
    USC-shaped identifiers.
    """
    target = f"/us/usc/t{int(title)}/s{section_number}"
    q_section = f"{_USLM_NS_PREFIX}section"
    q_notes = f"{_USLM_NS_PREFIX}notes"
    q_note = f"{_USLM_NS_PREFIX}note"

    def _walk(el: ET.Element) -> ET.Element | None:
        for child in el:
            if not isinstance(child.tag, str):
                continue
            if child.tag == q_notes or child.tag == q_note:
                continue
            if child.tag == q_section and child.get("identifier") == target:
                return child
            found = _walk(child)
            if found is not None:
                return found
        return None

    return _walk(root)


def _walk_uslm_sub_section_tree(
    section_el: ET.Element, base_address: LegalAddress
) -> tuple[tuple[UscSubsectionNode, ...], list[dict[str, str]]]:
    """Depth-first walk over a ``<section>``'s structural descendants.

    The walk is forward-only and visit-ordered by document position. Each
    structural child emits a node BEFORE descending into its own structural
    children, so the node list is in pre-order (parent-before-child), matching
    :func:`split_statutory_subsections`'s open-ancestor stack discipline.
    """
    nodes: list[UscSubsectionNode] = []
    findings: list[dict[str, str]] = []
    q_num = f"{_USLM_NS_PREFIX}num"

    def _text_of_node(el: ET.Element) -> str:
        """Direct text of a USLM structural element EXCLUDING child structure.

        Includes the node's own ``<num>``, ``<heading>``, ``<chapeau>``,
        ``<content>`` text and any direct PIs/comments' tails, but skips the
        subtrees of child ``<subsection>`` / ``<paragraph>`` / ... elements
        (those get their own nodes). This mirrors what the indent-depth
        heuristic stores per ``<p class='statutory-body'>`` line: the unit's
        own opening text, never its descendants'.
        """
        parts: list[str] = []
        if isinstance(el.text, str):
            parts.append(el.text)
        for child in el:
            if not isinstance(child.tag, str):
                if isinstance(child.tail, str):
                    parts.append(child.tail)
                continue
            local = _uslm_localname(child.tag)
            if local in _USLM_LEVEL_INDEX:
                if isinstance(child.tail, str):
                    parts.append(child.tail)
                continue
            parts.append(_uslm_subtree_text(child))
            if isinstance(child.tail, str):
                parts.append(child.tail)
        return _normalize_text("".join(parts))

    def _walk(el: ET.Element, parent_path: tuple[tuple[str, str], ...]) -> None:
        for child in el:
            if not isinstance(child.tag, str):
                continue
            local = _uslm_localname(child.tag)
            if local not in _USLM_LEVEL_INDEX:
                continue
            kind = local
            this_level = _USLM_LEVEL_INDEX[local]

            label = ""
            num_found = False
            for gc in child:
                if gc.tag == q_num:
                    value = gc.get("value", "")
                    if value:
                        label = value
                        num_found = True
                    break
            if not num_found:
                findings.append(
                    {
                        "rule_id": "us_uslm_node_without_num",
                        "kind": kind,
                        "identifier": child.get("identifier", ""),
                        "reason": f"<{kind}> has no <num value='...'>; label left empty",
                        "text_preview": _text_of_node(child)[:80],
                    }
                )

            path = parent_path + ((kind, label),)
            nodes.append(
                UscSubsectionNode(
                    address=LegalAddress(path=base_address.path + path),
                    label=label,
                    kind=kind,
                    indent_depth=this_level,
                    text=_text_of_node(child),
                )
            )
            _walk(child, path)

    _walk(section_el, ())
    return tuple(nodes), findings

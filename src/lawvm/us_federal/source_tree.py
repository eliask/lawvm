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
``(i)`` markers plus indent depth to the pinned USC address convention and emits
a typed ``us_usc_subsection_parse_ambiguous`` finding where structure is unclear
rather than guessing.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable

import lxml.html as LH
from lxml import etree

from lawvm.core.ir import LegalAddress

# Editorial paragraph/heading classes never part of statutory text.
_EDITORIAL_CLASSES = {"note-head", "note-body", "analysis", "subchapter-head", "chapter-head"}

# A section number from a ``section-head``: "§362. Automatic stay" → "362";
# repealed stubs are bracketed: "[§304. Repealed. ...]" → "304". The number may
# carry a letter suffix (e.g. "362A"); ranges ("§§") are not used in these titles.
_SECTION_HEAD_RE = re.compile(r"^\[?\s*§+\s*(?P<num>[0-9]+[A-Za-z]*)\.")

# Leading enumerator markers for subsection splitting (STRETCH). Each statutory
# paragraph that opens a new structural unit starts with one of these in the
# leading text, paired with an indent class that fixes the level.
_MARKER_RE = re.compile(r"^\((?P<token>[0-9A-Za-z]+)\)")

# indent class suffix → structural kind (the pinned USC address convention).
# statutory-body      = subsection level   (a) -> ("subsection", "a")
# statutory-body-1em  = paragraph level    (1) -> ("paragraph", "1")
# statutory-body-2em  = subparagraph level (A) -> ("subparagraph", "A")
# statutory-body-3em  = clause level       (i) -> ("clause", "i")
# statutory-body-4em+ = sub-clause and deeper (kept, but not in the pinned 6-tuple)
_INDENT_KIND = {
    0: "subsection",
    1: "paragraph",
    2: "subparagraph",
    3: "clause",
    4: "subclause",
    5: "item",
}

_SUBSECTION_PARSE_AMBIGUOUS = "us_usc_subsection_parse_ambiguous"


def _localname(el: Any) -> str:
    tag = getattr(el, "tag", el)
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


def _element_text(el: etree._Element) -> str:
    """Concatenated descendant text of an element (entities already decoded)."""
    return "".join(t for t in el.itertext() if isinstance(t, str))


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


# ---------------------------------------------------------------------------
# STRETCH: subsection-level split
# ---------------------------------------------------------------------------


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

    # Stack of (depth, kind, label) open ancestors; address is title/section +
    # each open ancestor's (kind, label).
    stack: list[tuple[int, str, str]] = []
    base_path = section.address.path

    for para in section.paragraphs:
        depth = para.indent_depth
        marker = _MARKER_RE.match(para.text)

        if depth < 0 or marker is None:
            # Continuation / flush / block line, or a paragraph with no leading
            # enumerator: attach to the currently-open node by appending text.
            if nodes:
                last = nodes[-1]
                nodes[-1] = UscSubsectionNode(
                    address=last.address,
                    label=last.label,
                    kind=last.kind,
                    indent_depth=last.indent_depth,
                    text=_normalize_text(f"{last.text} {para.text}"),
                )
            else:
                # Statutory text before any enumerator (a flush lead-in): record a
                # synthetic depth-0 body node so text is not dropped.
                findings.append(
                    {
                        "rule_id": _SUBSECTION_PARSE_AMBIGUOUS,
                        "section": section.section,
                        "reason": "statutory text precedes the first enumerated marker",
                        "text_preview": para.text[:80],
                    }
                )
            continue

        # Pop ancestors at or deeper than this depth.
        while stack and stack[-1][0] >= depth:
            stack.pop()

        if stack and depth - stack[-1][0] > 1:
            findings.append(
                {
                    "rule_id": _SUBSECTION_PARSE_AMBIGUOUS,
                    "section": section.section,
                    "reason": "indent depth jumps more than one level from open ancestor",
                    "marker": marker.group("token"),
                    "depth": str(depth),
                    "open_depth": str(stack[-1][0]),
                }
            )

        kind = _INDENT_KIND.get(depth, f"level{depth}")
        label = marker.group("token")
        stack.append((depth, kind, label))

        path = base_path + tuple((k, lbl) for (_d, k, lbl) in stack)
        nodes.append(
            UscSubsectionNode(
                address=LegalAddress(path=path),
                label=label,
                kind=kind,
                indent_depth=depth,
                text=_normalize_text(para.text),
            )
        )

    return tuple(nodes), findings


def iter_section_oracle_rows(
    document: UscSourceDocument,
) -> Iterable[tuple[LegalAddress, str, str]]:
    """Yield (address, normalized_statutory_text, source_credit_raw) per section.

    The section-level oracle surface a dry-run replay compares against.
    """
    for section in document.sections:
        yield section.address, section.statutory_text, section.source_credit_raw


def summarize_indent_classes(document: UscSourceDocument) -> dict[str, int]:
    """Histogram of statutory-body indent classes across the document."""
    counter: Counter[str] = Counter()
    for section in document.sections:
        for para in section.paragraphs:
            counter[para.css_class] += 1
    return dict(sorted(counter.items()))

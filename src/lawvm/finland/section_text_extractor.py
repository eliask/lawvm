"""Finland AKN oracle section-text extractor.

Walks the parsed AKN tree of a consolidated Finlex statute and extracts
per-section text. Produces one SectionText per <section> element found
in the body.

Design discipline (AGENTS.md):
  §1.10: no bare try/except — exceptions caught only at bounded XML parse
         boundary in `extract_sections_text`.
  §1.11: substring guards before expensive XML operations; compiled regexes
         at module scope with bounded quantifiers.
  §1.13: text extraction walks the parsed XML tree — NO regex over raw XML
         for AKN structure. The eId-to-section-key conversion uses regex
         only on the already-extracted eId attribute string, not on raw XML.

Phase: Phase 3 (Parse) + Phase 4 (Extract).
Jurisdiction: Finland.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional

from lawvm.core.section_text import SectionText

# ---------------------------------------------------------------------------
# AKN namespace
# ---------------------------------------------------------------------------

_AKN_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"

# Namespaced tag constants (avoids repeated f-string construction in hot path)
_TAG_SECTION = f"{{{_AKN_NS}}}section"
_TAG_NUM = f"{{{_AKN_NS}}}num"
_TAG_HEADING = f"{{{_AKN_NS}}}heading"
_TAG_REF = f"{{{_AKN_NS}}}ref"
_TAG_BODY = f"{{{_AKN_NS}}}body"
_TAG_MAIN_BODY = f"{{{_AKN_NS}}}mainBody"
_TAG_FRBR_SUBTYPE = f"{{{_AKN_NS}}}FRBRsubtype"
_TAG_FRBR_DATE = f"{{{_AKN_NS}}}FRBRdate"

# ---------------------------------------------------------------------------
# Module-scope compiled patterns (AGENTS.md §1.11)
# ---------------------------------------------------------------------------

# Extract chapter number from eId: 'chp_N' component.
# Bounded: \d{1,6} safe for any chapter number.
_EID_CHN_RE = re.compile(r"chp_(\d{1,6})(?:__|$)", re.IGNORECASE)

# Extract section number+letter from eId: 'sec_Na' optionally followed by
# version suffix 'vYYYYNNNN' or end-of-string/double-underscore.
# Bounded: [a-z]? is a single optional letter; v\d{8} caps version suffix.
_EID_SEC_RE = re.compile(r"sec_(\d{1,6}[a-z]?)(?:v\d{1,10})?(?:__|$)", re.IGNORECASE)

# ---------------------------------------------------------------------------
# Extraction result type (AGENTS.md §1.8 — no silent lane disappearance)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SectionTextExtractionDiagnostic:
    """Diagnostic record emitted when section extraction encounters a problem.

    rule_id:  Stable identifier for the diagnostic condition.
    phase:    'parse' | 'extract'.
    reason:   Human-readable description.
    blocking: True if the diagnostic stopped further extraction for this statute.
    """

    rule_id: str
    phase: str
    reason: str
    blocking: bool


@dataclass(frozen=True)
class SectionTextExtractionResult:
    """Result of extracting section texts from one oracle XML document.

    sections:     Extracted SectionText records (may be empty on failure or
                  if the statute has no <section> elements).
    diagnostics:  Typed diagnostic records — never silently dropped.
    """

    sections: List[SectionText] = field(default_factory=list)
    diagnostics: List[SectionTextExtractionDiagnostic] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _eid_to_section_key(eid: str) -> str:
    """Convert AKN eId attribute value to a stable section_key string.

    Maps:
      'part_1__chp_1__sec_1'         -> 'chapter:1/section:1'
      'part_1__chp_1__sec_3av20190809' -> 'chapter:1/section:3a'
      'chp_2__sec_6'                 -> 'chapter:2/section:6'
      'sec_1'                        -> 'section:1'
      ''                             -> ''

    Version suffixes (vYYYYNNNN) are stripped; they denote the amendment
    that last modified this section, not a separate section.
    """
    sec_m = _EID_SEC_RE.search(eid)
    if sec_m is None:
        return ""
    sec_num = sec_m.group(1).lower()
    chp_m = _EID_CHN_RE.search(eid)
    if chp_m is not None:
        return f"chapter:{chp_m.group(1)}/section:{sec_num}"
    return f"section:{sec_num}"


def _element_body_text(section_el: ET.Element[str]) -> str:
    """Extract clean body text from a <section> element.

    Walks the parsed XML tree to collect all visible text — equivalent to
    innerText in browsers. The <ref> elements are transparent: their
    displayed text is included, but the href markup is discarded.

    This function deliberately uses ET.Element.itertext() which follows
    AGENTS.md §1.13 — tree walk, not regex over raw XML.

    We skip text inside <num> and <heading> children at the immediate
    level (those are stored in section_label / heading_text), but recurse
    into subsections, paragraphs, etc. for body content.
    """
    parts: List[str] = []

    def _collect(el: ET.Element[str], skip_tags: frozenset[str]) -> None:
        local = el.tag.split("}")[-1] if "}" in el.tag else el.tag
        if local in skip_tags:
            return
        # Collect this element's text
        if el.text:
            parts.append(el.text)
        # Recurse into children
        for child in el:
            _collect(child, frozenset())  # only top-level num/heading skipped
        # Collect tail text
        if el.tail:
            parts.append(el.tail)

    # Skip immediate <num> and <heading> children of this section
    _top_skip = frozenset({"num", "heading"})
    # Collect text of section element itself (its direct text before children)
    if section_el.text:
        parts.append(section_el.text)
    for child in section_el:
        _collect(child, _top_skip)

    raw = "".join(parts)
    # Normalize whitespace (collapse runs, strip leading/trailing)
    return " ".join(raw.split())


def _parse_date(date_str: str) -> Optional[date]:
    """Parse ISO date string YYYY-MM-DD, return None on failure."""
    if not date_str or len(date_str) < 10:
        return None
    parts = date_str[:10].split("-")
    if len(parts) != 3:
        return None
    year_s, mon_s, day_s = parts
    if not (year_s.isdigit() and mon_s.isdigit() and day_s.isdigit()):
        return None
    return date(int(year_s), int(mon_s), int(day_s))


def _read_consolidated_date(tree: ET.Element[str]) -> Optional[date]:
    """Extract dateConsolidated from the FRBR metadata, or None."""
    for el in tree.iter(_TAG_FRBR_DATE):
        if el.get("name") == "dateConsolidated":
            return _parse_date(el.get("date", ""))
    return None


def _frbr_subtype(tree: ET.Element[str]) -> str:
    """Return FRBRsubtype value from the document, or '' if absent."""
    el = tree.find(f".//{_TAG_FRBR_SUBTYPE}")
    return el.get("value", "") if el is not None else ""


# ---------------------------------------------------------------------------
# Public extraction entry point
# ---------------------------------------------------------------------------


def extract_sections_text(
    xml_bytes: bytes,
    statute_id: str,
) -> SectionTextExtractionResult:
    """Extract per-section oracle text from consolidated Finlex AKN bytes.

    Args:
        xml_bytes:   Raw AKN XML bytes from store.read_oracle(statute_id).
        statute_id:  Statute ID (e.g. '2003/434') for SectionText rows.

    Returns:
        SectionTextExtractionResult with sections and diagnostics.

    AGENTS.md §1.13: tree walk, not regex over raw XML for structure.
    AGENTS.md §1.11: substring guard before XML parse.
    """
    # Substring guard (AGENTS.md §1.11): skip statutes with no section elements
    if b"<section" not in xml_bytes and b":section" not in xml_bytes:
        return SectionTextExtractionResult(
            sections=[],
            diagnostics=[
                SectionTextExtractionDiagnostic(
                    rule_id="fi_sections_text_no_section_elements",
                    phase="extract",
                    reason="No <section> elements found in oracle XML (quick guard)",
                    blocking=False,
                )
            ],
        )

    # XML parse boundary (AGENTS.md §1.10: single bounded except)
    tree: ET.Element[str]
    try:
        tree = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        return SectionTextExtractionResult(
            sections=[],
            diagnostics=[
                SectionTextExtractionDiagnostic(
                    rule_id="fi_sections_text_xml_parse_failed",
                    phase="parse",
                    reason=f"XML parse error: {exc}",
                    blocking=True,
                )
            ],
        )

    # Reject non-consolidated oracles (e.g. raw amendment acts).
    # The brief specifies FRBRsubtype != "statute-consolidated" is rejected.
    subtype = _frbr_subtype(tree)
    if subtype and subtype != "statute-consolidated":
        return SectionTextExtractionResult(
            sections=[],
            diagnostics=[
                SectionTextExtractionDiagnostic(
                    rule_id="fi_sections_text_wrong_frbr_subtype",
                    phase="parse",
                    reason=(
                        f"FRBRsubtype={subtype!r}; only 'statute-consolidated' "
                        "oracles are projected"
                    ),
                    blocking=True,
                )
            ],
        )

    # Extract consolidated date for valid_at_start
    valid_at_start = _read_consolidated_date(tree)

    # Find body element
    body = tree.find(f".//{_TAG_BODY}")
    if body is None:
        body = tree.find(f".//{_TAG_MAIN_BODY}")
    if body is None:
        return SectionTextExtractionResult(
            sections=[],
            diagnostics=[
                SectionTextExtractionDiagnostic(
                    rule_id="fi_sections_text_no_body_element",
                    phase="extract",
                    reason="No <body> or <mainBody> element found",
                    blocking=True,
                )
            ],
        )

    # Walk all <section> elements in the body (AGENTS.md §1.13: tree walk)
    sections: List[SectionText] = []
    for section_el in body.iter(_TAG_SECTION):
        eId = section_el.get("eId", "")
        section_key = _eid_to_section_key(eId)
        if not section_key:
            # eId missing or unparseable — skip but don't fail
            continue

        # Extract <num> text (section label)
        num_el = section_el.find(_TAG_NUM)
        section_label = (num_el.text or "").strip() if num_el is not None else ""

        # Extract <heading> text if present
        heading_el = section_el.find(_TAG_HEADING)
        heading_text = (heading_el.text or "").strip() if heading_el is not None else ""

        # Extract body text (all visible text, no AKN markup)
        body_text = _element_body_text(section_el)

        char_count = len(body_text)

        sections.append(
            SectionText(
                statute_id=statute_id,
                section_key=section_key,
                section_label=section_label,
                heading_text=heading_text,
                body_text=body_text,
                char_count=char_count,
                source_span_byte_offset=None,  # not computed in this projection
                source_span_len=None,
                valid_at_start=valid_at_start,
                valid_at_end=None,
            )
        )

    diagnostics: List[SectionTextExtractionDiagnostic] = []
    if not sections:
        # Body exists but no extractable sections — warn but not blocking
        diagnostics.append(
            SectionTextExtractionDiagnostic(
                rule_id="fi_sections_text_zero_sections_extracted",
                phase="extract",
                reason="Body element found but zero sections extracted",
                blocking=False,
            )
        )

    return SectionTextExtractionResult(
        sections=sections,
        diagnostics=diagnostics,
    )

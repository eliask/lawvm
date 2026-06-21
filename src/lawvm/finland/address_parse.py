"""address_parse — Finnish legal address value type + leading-path recovery.

This module owns the canonical :class:`ParsedLegalAddress` value type emitted by
the legal-address recognizers, plus a narrow leading-structural-path recovery
helper for flat ``hcontainer`` bodies.

The structural free-text address PARSER that used to live here
(``parse_legal_addresses``) has been demoted: it was the last parallel weaker
regex sub-ref grammar in the FI tree and is fully superseded by the shared
grammar driver :func:`lawvm.finland.references.freetext_addresses.scan_legal_addresses`,
which parses every site's structure through the shared johtolause grammar and is
a verified place-level superset. New consumers must call that recognizer; this
module is now only the value type + the (regex-bounded) leading-path probe.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List

# ---------------------------------------------------------------------------
# Data type
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ParsedLegalAddress:
    """Structured legal address parsed from Finnish text.

    Attributes:
        section:    Section number label, e.g. "6", "24a".  Empty string
                    means this address has no section context (e.g. a
                    standalone momentti reference).
        subsection: Subsection (momentti) number, or None.
        item:       Item (kohta) label, e.g. "3", "a".  None if absent.
        subitem:    Sub-item (alakohta) label, e.g. "a".  None if absent.
                    Per Lainkirjoittajan opas: "6 §:n 2 momentin 1 kohdan
                    a alakohta".
        chapter:    Chapter number label, e.g. "3", "5a".  None means this
                    address is not a chapter reference.
        special:    "heading", "intro", or "" for whole-node addresses.
    """

    section: str = ""
    subsection: int | None = None
    item: str | None = None
    subitem: str | None = None
    chapter: str | None = None
    special: str = ""


# ---------------------------------------------------------------------------
# Leading-structural-path recovery (flat hcontainer bodies)
# ---------------------------------------------------------------------------

_WHITESPACE_RE = re.compile(r"\s+")
_LEADING_SECTION_RE = re.compile(r"(\d+[a-z]?)\s*§", re.IGNORECASE)
_LEADING_PART_RE = re.compile(
    r"([IVXLCM]+|\d+(?:\s*[a-z])?)\s+(?:osa|osasto)\b",
    re.IGNORECASE,
)
_LEADING_CHAPTER_RE = re.compile(
    r"([IVXLCM]+|\d+(?:\s*[a-z])?)\s+luku\b",
    re.IGNORECASE,
)


def _norm_section(raw: str) -> str:
    """Normalize a section token: strip spaces, lowercase letter suffix."""
    return _WHITESPACE_RE.sub("", raw.strip()).lower()


def parse_leading_structural_address_path(text: str) -> List[tuple[str, str]]:
    """Best-effort parse of the leading structural address in raw statute text.

    This is a narrow recovery helper for Finland bodies that still arrive as
    flat `hcontainer` text rather than already-addressable structural nodes.
    It looks only at the leading heading region and returns the first
    structural path it can identify.
    """
    prefix = " ".join((text or "").split())
    if not prefix:
        return []

    window = prefix[:240]

    # lawvm-regex: prefilter leading structural-address-path probe over flat-hcontainer leading heading text, returns a structural path not amendment ops
    section_m = _LEADING_SECTION_RE.search(window)
    if section_m is not None:
        leading = window[: section_m.start()]
        path: List[tuple[str, str]] = []

        # lawvm-regex: prefilter leading-path part-arm probe over flat-hcontainer heading text
        part_m = list(_LEADING_PART_RE.finditer(leading))
        if part_m:
            path.append(("part", _norm_section(part_m[-1].group(1))))

        # lawvm-regex: prefilter leading-path chapter-arm probe over flat-hcontainer heading text
        chapter_m = list(_LEADING_CHAPTER_RE.finditer(leading))
        if chapter_m:
            path.append(("chapter", _norm_section(chapter_m[-1].group(1))))

        path.append(("section", _norm_section(section_m.group(1))))
        return path

    # Chapter/part-only bodies are rare, but keep them recoverable too.
    # lawvm-regex: prefilter leading-path part-only fallback probe over flat-hcontainer heading text
    part_m = list(_LEADING_PART_RE.finditer(window))
    if part_m:
        return [("part", _norm_section(part_m[-1].group(1)))]

    # lawvm-regex: prefilter leading-path chapter-only fallback probe over flat-hcontainer heading text
    chapter_m = list(_LEADING_CHAPTER_RE.finditer(window))
    if chapter_m:
        return [("chapter", _norm_section(chapter_m[-1].group(1)))]

    return []

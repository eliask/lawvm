"""Commencement lookup for the provisions of an affecting act.

A UK effect feed marks a structural effect ``prospective`` when it has not been
commenced, but that flag is stale/incomplete: an effect whose affecting provision
has in fact been brought into force is still reported prospective with no
commencement record (e.g. ``ukpga/1996/46`` s.17, in force 2009-10-31, still flags
its amendments to ``ukpga/1968/20`` prospective). The deterministic, non-guessing
signal is the affecting act's own per-provision ``RestrictStartDate`` (the date a
provision's version starts being in force). This module reads that signal.

This is the primitive under the §6.8 prospective resolver: given an effect, find
the affecting provisions it cites, look up their start dates in the affecting act,
and report whether they are in force as of a point in time. The resolver
(apply-gating) is built on top of this.
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Any, Optional

from lxml import etree as ET

from lawvm.roman import arabic_to_roman, roman_to_arabic

_LEG_NS = "http://www.legislation.gov.uk/namespaces/legislation"

# Affecting-provision references inside an affecting_provisions string.
#
# Sections are still indexed at section level because legislation.gov.uk often
# carries the start date on subsection descendants even when the feed cites the
# section. Schedules are not flattened that way: a schedule root, Part, and
# paragraph may carry different start dates, so schedule descendants get separate
# keys and a "Sch. N" reference does not inherit dates from "Sch. N para. M".
_SECTION_REF_RE = re.compile(r"\bs{1,2}\.?\s*(\d+[A-Za-z]*)", re.I)
_SCHEDULE_REF_RE = re.compile(r"\bSch(?:s|ed|edule)?s?\.?\s*(\d+[A-Za-z]*)", re.I)
_SCHEDULE_PART_REF_RE = re.compile(
    r"\b(?:Pt|Part)\.?\s*([IVXLCDM]+|\d+[A-Za-z]*)",
    re.I,
)
_SCHEDULE_PARAGRAPH_REF_RE = re.compile(
    r"\bpara(?:graph)?\.?\s*([A-Za-z]?\d+[A-Za-z]*|p\d+[A-Za-z]*)",
    re.I,
)
_TOP_LEVEL_AFTER_SCHEDULE_RE = re.compile(
    r"\b(?:s{1,2}|art(?:icle)?s?|reg(?:ulation)?s?)\.?\s*\d",
    re.I,
)


def _normalize_schedule_part_label(label: str) -> str:
    """Normalize Part refs so source ``Pt. 3`` can match XML ``part/III``."""
    text = label.strip()
    if text.isdigit():
        value = int(text)
        if 1 <= value <= 3999:
            return arabic_to_roman(value).lower()
    roman_value = roman_to_arabic(text)
    if roman_value is not None:
        return arabic_to_roman(roman_value).lower()
    return text.lower()


def _normalize_schedule_paragraph_label(label: str) -> str:
    """Normalize XML ``paragraph/p13`` and source ``para. 13`` to one key."""
    text = label.strip().lower()
    if len(text) > 1 and text.startswith("p") and text[1].isdigit():
        return text[1:]
    return text


def parse_affecting_provision_refs(affecting_provisions: str) -> dict[str, set[str]]:
    """Return provision refs cited in an effect's affecting-provisions string."""
    text = str(affecting_provisions or "")
    sections = {m.group(1).lower() for m in _SECTION_REF_RE.finditer(text)}
    schedules: set[str] = set()
    schedule_parts: set[str] = set()
    schedule_paragraphs: set[str] = set()
    schedule_matches = tuple(_SCHEDULE_REF_RE.finditer(text))
    for index, match in enumerate(schedule_matches):
        schedule_label = match.group(1).lower()
        next_start = (
            schedule_matches[index + 1].start()
            if index + 1 < len(schedule_matches)
            else len(text)
        )
        trail = text[match.end() : next_start]
        top_level_match = _TOP_LEVEL_AFTER_SCHEDULE_RE.search(trail)
        if top_level_match is not None:
            trail = trail[: top_level_match.start()]
        paragraph_match = _SCHEDULE_PARAGRAPH_REF_RE.search(trail)
        if paragraph_match is not None:
            schedule_paragraphs.add(
                f"{schedule_label}/{_normalize_schedule_paragraph_label(paragraph_match.group(1))}"
            )
            continue
        part_match = _SCHEDULE_PART_REF_RE.search(trail)
        if part_match is not None:
            schedule_parts.add(
                f"{schedule_label}/{_normalize_schedule_part_label(part_match.group(1))}"
            )
            continue
        schedules.add(schedule_label)
    return {
        "section": sections,
        "schedule": schedules,
        "schedule_part": schedule_parts,
        "schedule_paragraph": schedule_paragraphs,
    }


def _provision_kind_for_id(id_uri: str) -> Optional[tuple[str, str]]:
    """Map a provision IdURI tail to ``(kind, label)`` for supported provisions."""
    m = re.search(r"/section/(\w+)\b", id_uri)
    if m:
        return "section", m.group(1).lower()
    m = re.search(r"/schedules?/([^/]+)/paragraph/([^/]+)\b", id_uri)
    if m:
        return (
            "schedule_paragraph",
            f"{m.group(1).lower()}/{_normalize_schedule_paragraph_label(m.group(2))}",
        )
    m = re.search(r"/schedules?/([^/]+)/part/([^/]+)\b", id_uri)
    if m:
        return (
            "schedule_part",
            f"{m.group(1).lower()}/{_normalize_schedule_part_label(m.group(2))}",
        )
    m = re.search(r"/schedules?/([^/]+)$", id_uri)
    if m:
        return "schedule", m.group(1).lower()
    return None


@lru_cache(maxsize=64)
def _start_date_index(xml_bytes: bytes) -> dict[tuple[str, str], str]:
    """Index ``(kind, label) -> earliest RestrictStartDate`` for the affecting act.

    Cached on the raw bytes so repeated lookups against one affecting act in a
    compile run parse the document once.
    """
    index: dict[tuple[str, str], str] = {}
    try:
        root = ET.fromstring(xml_bytes)
    except ET.XMLSyntaxError:
        return index
    for el in root.iter():
        start_date = el.get("RestrictStartDate")
        if not start_date:
            continue
        id_uri = el.get("IdURI") or el.get("DocumentURI") or ""
        kind_label = _provision_kind_for_id(id_uri)
        if kind_label is None:
            continue
        existing = index.get(kind_label)
        # keep the earliest start date seen for a provision root (version with the
        # oldest in-force date — the point from which the provision first operated)
        if existing is None or start_date < existing:
            index[kind_label] = start_date
    return index


def affecting_provision_start_dates(
    affecting_provisions: str,
    affecting_act_xml: Optional[bytes],
) -> list[str]:
    """Return the resolved ``RestrictStartDate`` values for the cited provisions."""
    if not affecting_act_xml:
        return []
    refs = parse_affecting_provision_refs(affecting_provisions)
    index = _start_date_index(affecting_act_xml)
    dates: list[str] = []
    for kind, labels in refs.items():
        for label in sorted(labels):
            date = index.get((kind, label))
            if date:
                dates.append(date)
    return dates


def affecting_provision_in_force(
    affecting_provisions: str,
    affecting_act_xml: Optional[bytes],
    *,
    as_of: str,
) -> Optional[bool]:
    """Tri-state: are the cited affecting provisions in force as of ``as_of``?

    - ``True``  — every resolved affecting provision has a start date ≤ ``as_of``.
    - ``False`` — at least one resolved affecting provision starts after ``as_of``.
    - ``None``  — no affecting provision could be resolved (unknown; do not guess).
    """
    dates = affecting_provision_start_dates(affecting_provisions, affecting_act_xml)
    if not dates:
        return None
    return all(date <= as_of for date in dates)


def get_affecting_act_xml(affecting_act_id: str, archive: Any) -> Optional[bytes]:
    """Fetch the affecting act's current XML bytes from the archive, if present."""
    if not affecting_act_id:
        return None
    archive_get = getattr(archive, "get", None)
    if not callable(archive_get):
        return None
    return archive_get(f"https://www.legislation.gov.uk/{affecting_act_id}/data.xml")

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

_LEG_NS = "http://www.legislation.gov.uk/namespaces/legislation"

# Top-level affecting-provision references inside an affecting_provisions string,
# e.g. "s. 17(2)(b) Sch. 7 Pt. 3" -> section 17 and schedule 7. Sub-references
# ((2)(b), Pt. 3) are below the granularity at which RestrictStartDate is carried,
# so the section/schedule-level start date governs.
_SECTION_REF_RE = re.compile(r"\bs{1,2}\.?\s*(\d+[A-Za-z]*)", re.I)
_SCHEDULE_REF_RE = re.compile(r"\bSch(?:s|ed|edule)?s?\.?\s*(\d+[A-Za-z]*)", re.I)


def parse_affecting_provision_refs(affecting_provisions: str) -> dict[str, set[str]]:
    """Return ``{"section": {labels}, "schedule": {labels}}`` cited in the string."""
    text = str(affecting_provisions or "")
    sections = {m.group(1).lower() for m in _SECTION_REF_RE.finditer(text)}
    schedules = {m.group(1).lower() for m in _SCHEDULE_REF_RE.finditer(text)}
    return {"section": sections, "schedule": schedules}


def _provision_kind_for_id(id_uri: str) -> Optional[tuple[str, str]]:
    """Map a provision IdURI tail to ``(kind, label)`` for section/schedule roots."""
    m = re.search(r"/section/(\w+)\b", id_uri)
    if m:
        return "section", m.group(1).lower()
    m = re.search(r"/schedules?/(\w+)\b", id_uri)
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
        for label in labels:
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

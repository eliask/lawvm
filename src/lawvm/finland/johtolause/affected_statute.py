"""Affected-statute head recognition for Finnish johtolause routing.

This module owns the pre-operative statute identity phrase such as
``16 päivänä elokuuta 1958 annetun rakennuslain (70/58)``.  It is deliberately
separate from ``citation_routing`` so routing decisions consume a typed surface
object instead of reimplementing johtolause grammar fragments inline.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import re

_TARGET_ZONE_CUT_RE = re.compile(
    r"\bsellais(?:ena|ina)\s+kuin\b|\bsiihen\s+myöhemmin\b",
    re.IGNORECASE,
)
_AFFECTED_HEAD_RE = re.compile(
    r"(?:(?P<day>\d{1,2})\s+päivänä\s+(?P<month>[a-zäöå]+)\s+(?P<year>\d{4})\s+)?"
    r"annetun\s+(?P<title>[^()]{1,220}?)\s*\(\s*(?P<num>\d+)\s*/\s*(?P<cite_year>\d{2,4})\s*\)",
    re.IGNORECASE,
)
_CITATION_RE = re.compile(r"\(\s*(\d+)\s*/\s*(\d{2,4})\s*\)")

_FI_MONTH_GENITIVE_TO_NUMBER: dict[str, int] = {
    "tammikuuta": 1,
    "helmikuuta": 2,
    "maaliskuuta": 3,
    "huhtikuuta": 4,
    "toukokuuta": 5,
    "kesäkuuta": 6,
    "heinäkuuta": 7,
    "elokuuta": 8,
    "syyskuuta": 9,
    "lokakuuta": 10,
    "marraskuuta": 11,
    "joulukuuta": 12,
}


@dataclass(frozen=True, slots=True)
class AffectedStatuteHead:
    """Typed surface for the statute identity phrase before operative targets."""

    text: str
    title_phrase: str
    instrument: str
    issue_date: date | None
    cited_num: str
    cited_year: str


@dataclass(frozen=True, slots=True)
class DelegatedAuthorityLeadIn:
    """Typed surface for ``säädetään ... nojalla`` authority preambles."""

    text: str
    authority_citations: tuple[str, ...]


def target_zone(text: str) -> str:
    """Return the part of a johtolause where target-statute citations live."""

    compact = re.sub(r"\s+", " ", text or "")
    cut = _TARGET_ZONE_CUT_RE.search(compact)
    return compact[: cut.start()] if cut else compact


def _parse_finnish_date(day_s: str | None, month_s: str | None, year_s: str | None) -> date | None:
    if not day_s or not month_s or not year_s:
        return None
    month = _FI_MONTH_GENITIVE_TO_NUMBER.get(month_s.lower())
    if month is None:
        return None
    try:
        return date(int(year_s), month, int(day_s))
    except ValueError:
        return None


def instrument_from_text(text: str) -> str:
    """Return coarse Finnish instrument kind from inflected statute prose."""

    norm = re.sub(r"\s+", " ", (text or "").lower())
    if re.search(r"(?:^|\s|[-])\w*(?:asetus|asetuk(?:sen|sesta|seen|sessa))\b", norm):
        return "asetus"
    if re.search(r"(?:^|\s|[-])\w*(?:päätös|päätök(?:sen|sestä|seen|sessä))\b", norm):
        return "päätös"
    if re.search(r"(?:^|\s|[-])\w*(?:laki|lain)\b", norm):
        return "laki"
    return ""


def parse_affected_statute_head(johto: str) -> AffectedStatuteHead | None:
    """Parse the affected-statute identity head, if present."""

    zone = target_zone(johto)
    match = _AFFECTED_HEAD_RE.search(zone)
    if match is None:
        return None
    title_phrase = re.sub(r"\s+", " ", match.group("title") or "").strip()
    return AffectedStatuteHead(
        text=match.group(0),
        title_phrase=title_phrase,
        instrument=instrument_from_text(title_phrase),
        issue_date=_parse_finnish_date(match.group("day"), match.group("month"), match.group("year")),
        cited_num=match.group("num"),
        cited_year=match.group("cite_year"),
    )


def parse_delegated_authority_lead_in(johto: str) -> DelegatedAuthorityLeadIn | None:
    """Parse ``säädetään ... nojalla`` authority lead-ins used by delegated decrees."""

    zone = target_zone(johto)
    lower = zone.lower()
    if "nojalla" not in lower or re.search(r"\bsäädetään\b", lower) is None:
        return None
    before_nojalla = lower.split("nojalla", 1)[0]
    if any(keyword in before_nojalla for keyword in ("muutetaan", "kumotaan", "lisätään", "siirretään")):
        return None
    citations = tuple(f"{num}/{year}" for num, year in _CITATION_RE.findall(zone))
    return DelegatedAuthorityLeadIn(text=zone, authority_citations=citations)

"""Affected-statute head recognition for Finnish johtolause routing.

This module owns the pre-operative statute identity phrase such as
``16 päivänä elokuuta 1958 annetun rakennuslain (70/58)``.  It is deliberately
separate from ``citation_routing`` so routing decisions consume a typed surface
object instead of reimplementing johtolause grammar fragments inline.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from functools import lru_cache
import re

from lawvm.finland.fi_dates import parse_fi_day_month_year
from lawvm.finland.morphology import MorphNumber, generate_forms, head_entry

_TARGET_ZONE_CUT_RE = re.compile(
    # Historical OCR/source typo seen in 1978/676: ``selaisena kuin``.  It
    # still marks a version-provenance clause, not the amended statute target.
    r"\bsell?ais(?:ena|ina)(?:\s+kuin|,\s+kuin)\b|\bsiihen\s+myöhemmin\b",
    re.IGNORECASE,
)
_WHITESPACE_RE = re.compile(r"\s+")
_AFFECTED_HEAD_RE = re.compile(
    r"(?:(?P<day>\d{1,2})\s{1,4}+päivänä\s{1,4}+(?P<month>[a-zäöå]{1,15})\s{1,4}+(?P<year>\d{4})\s{1,4}+)?"
    r"annetun\s{1,4}+(?P<title>[^()]{1,220}?)\s{0,4}+\(\s{0,4}+(?P<num>\d{1,5})\s{0,4}+/\s{0,4}+(?P<cite_year>\d{2,4})\s{0,4}+\)",
    re.IGNORECASE,
)
_AFFECTED_HEAD_TITLE_DATE_RE = re.compile(
    r"(?P<title>[^(),;]{1,220}?)\s{1,4}+"
    r"(?P<day>\d{1,2})\s{1,4}+päivänä\s{1,4}+"
    r"(?P<month>[a-zäöå]{1,15})\s{1,4}+"
    r"(?P<year>\d{4})\s{1,4}+annetun\s{1,4}+"
    r"(?P<instrument>lain|asetuksen|(?:valtioneuvoston\s{1,4}+)?päätöksen)\s{0,4}+"
    r"\(\s{0,4}+(?P<num>\d{1,5})\s{0,4}+/\s{0,4}+(?P<cite_year>\d{2,4})\s{0,4}+\)",
    re.IGNORECASE,
)
_CITATION_RE = re.compile(r"\(\s*(\d+)\s*/\s*(\d{2,4})\s*\)")
_NOJALLA_RE = re.compile(r"\bnojalla\b", re.IGNORECASE)

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


@dataclass(frozen=True, slots=True)
class TargetCitation:
    """Citation token found in the target-identifying zone of a johtolause."""

    num: str
    year: str
    normalized_id: str | None

    def matches_statute_id(self, statute_id: str) -> bool:
        try:
            year_str, num_str = statute_id.split("/")
            parent_num = int(num_str)
        except (ValueError, AttributeError):
            return False
        try:
            return int(self.num) == parent_num and self.year in (year_str, year_str[-2:])
        except ValueError:
            return False


@dataclass(frozen=True, slots=True)
class JohtolauseRoutingSurface:
    """Typed citation-routing surface extracted from one Finnish johtolause."""

    target_zone: str
    affected_head: AffectedStatuteHead | None
    delegated_authority: DelegatedAuthorityLeadIn | None
    target_citations: tuple[TargetCitation, ...]

    def references_statute(self, statute_id: str) -> bool:
        if not self.target_citations:
            return True
        try:
            year_str, num_str = statute_id.split("/")
            int(num_str)
        except (ValueError, AttributeError):
            return True
        if not year_str:
            return True
        return any(citation.matches_statute_id(statute_id) for citation in self.target_citations)

    def normalized_target_ids(self) -> tuple[str, ...]:
        out: list[str] = []
        seen: set[str] = set()
        for citation in self.target_citations:
            norm = citation.normalized_id
            if norm and norm not in seen:
                seen.add(norm)
                out.append(norm)
        return tuple(out)


def _strip_delegated_authority_prefix_when_target_follows(compact: str) -> str:
    """Drop an authority ``nojalla`` lead-in when a later target citation exists.

    Example: ``lisätään [authority statute] nojalla, sellaisena kuin ...,
    [target statute] (575/88) uusi 25 a §``.  The first citation is enabling
    authority, not the amended statute.  Pure ``säädetään ... nojalla`` clauses
    have no later target citation, so they are left intact for authority-skip
    routing.
    """

    match = _NOJALLA_RE.search(compact)
    if match is None:
        return compact
    tail = compact[match.end() :].lstrip()

    candidate = tail[1:].lstrip() if tail.startswith(",") else tail
    candidate_lower = candidate.lower()
    provenance_match = _TARGET_ZONE_CUT_RE.match(candidate_lower)
    if provenance_match is not None:
        comma_after_provenance = candidate.find(",", provenance_match.end())
        if comma_after_provenance != -1:
            candidate = candidate[comma_after_provenance + 1 :].lstrip()
        else:
            return compact
    elif not tail.startswith(","):
        return compact

    if _CITATION_RE.search(candidate) is None:
        return compact
    return candidate


def target_zone(text: str) -> str:
    """Return the part of a johtolause where target-statute citations live."""

    compact = _WHITESPACE_RE.sub(" ", text or "")
    compact = _strip_delegated_authority_prefix_when_target_follows(compact)
    cut = _TARGET_ZONE_CUT_RE.search(compact)
    return compact[: cut.start()] if cut else compact


def _parse_finnish_date(day_s: str | None, month_s: str | None, year_s: str | None) -> date | None:
    return parse_fi_day_month_year(day_s, month_s, year_s)


def normalize_source_citation_id(raw: str, source_year: int) -> str | None:
    """Normalize textual source citations like ``631/2022`` or ``631/22``."""

    raw = _WHITESPACE_RE.sub("", (raw or ""))
    match = re.fullmatch(r"(\d{1,5})/(\d{2,4})", raw)
    if not match:
        return None
    left, right = match.groups()
    num = int(left)
    if len(right) == 4:
        return f"{right}/{num}"
    year_two = int(right)
    source_century = (source_year // 100) * 100
    full_year = source_century + year_two
    if full_year > source_year:
        full_year -= 100
    return f"{full_year}/{num}"


# Closed map: instrument-head LEMMA -> coarse instrument kind this module emits.
# Only the three coarse kinds the routing layer compares against are produced;
# every other known head (sopimus/säädös/määräys/...) and every unknown head
# yields the honest "" (unknown), never a silently-guessed kind.
_INSTRUMENT_HEAD_KIND: dict[str, str] = {
    "laki": "laki",
    "asetus": "asetus",
    "päätös": "päätös",
}

# Token that is the last whitespace/hyphen/dash-delimited word of a title phrase.
# Hyphen and Finnish en-dash are treated as compound separators so that a head
# like ``alkoholi–lain`` exposes its real head token ``lain``.
_HEAD_TOKEN_SEP_RE = re.compile(r"[\s\-–—]+")
# Strip trailing/leading non-word punctuation clinging to a head token.
_TOKEN_TRIM_RE = re.compile(r"^[^\wäöå]+|[^\wäöå]+$", re.IGNORECASE)


@lru_cache(maxsize=1)
def _instrument_suffix_table() -> tuple[tuple[str, str], ...]:
    """Build ``(inflected_surface, instrument_kind)`` pairs, longest surface first.

    Every reference_v1 case form (sg + pl) of each closed instrument-head lemma
    is enumerated by M1 generation and paired with the coarse kind in
    :data:`_INSTRUMENT_HEAD_KIND`.  Sorting longest-surface-first lets the
    matcher prefer the most specific inflected form when one form is a suffix of
    another, and makes a compound tail (``rakennus`` + ``lain``) resolve to the
    head's kind without substring false positives.
    """
    surfaces: dict[str, str] = {}
    for lemma, kind in _INSTRUMENT_HEAD_KIND.items():
        entry = head_entry(lemma)
        for form in generate_forms(entry, numbers=(MorphNumber.SG, MorphNumber.PL)):
            if form.certainty != "deterministic" or not form.surface:
                continue
            surface = form.surface.casefold()
            # A surface shared by two kinds would be ambiguous; the closed
            # instrument set has none, but guard so a future addition fails loud
            # rather than picking arbitrarily.
            if surfaces.get(surface, kind) != kind:
                surfaces[surface] = ""  # mark ambiguous -> not a confident kind
            else:
                surfaces[surface] = kind
    pairs = [(s, k) for s, k in surfaces.items() if k]
    pairs.sort(key=lambda sk: len(sk[0]), reverse=True)
    return tuple(pairs)


def _classify_token(token: str) -> str:
    """Return the instrument kind for a single normalized head token, else ""."""

    if not token:
        return ""
    for surface, kind in _instrument_suffix_table():
        if token == surface:
            return kind
        # Compound tail: the inflected head form is a suffix of a compound word
        # (``rakennus`` + ``lain``).  Require a real letter before the boundary
        # so a coincidental ending never masquerades as an instrument.
        if token.endswith(surface) and len(token) > len(surface):
            return kind
    return ""


def instrument_from_text(text: str) -> str:
    """Return coarse Finnish instrument kind from inflected statute prose.

    Morphology-driven (not suffix-substring matching): each word of the phrase is
    matched against the FULL reference_v1 paradigm of the closed instrument-head
    lemmas via M1 generation, so any inflected form (genitive ``-lain``, illative
    ``lakiin``, inessive ``laissa``, plural ``lakeja`` ...) maps to the same
    coarse kind.  This avoids the consonant-gradation bug class of substring
    matching (where ``'asetus' not in 'asetuksen'`` forced a fragile hand-list of
    inflected variants).

    The instrument-bearing head is the LAST instrument word in the phrase,
    scanned right-to-left: in ``... annetun liikenneministeriön päätöksen`` the
    affected statute is the trailing ``päätöksen`` (the one bearing the citation),
    not an earlier ``asetuksen`` that is merely a referent of ``soveltamisesta``.
    Trailing non-instrument postmodifiers (``lain 5 §:n``) are skipped so the
    grammatical head is still recovered.  An unanalyzable / out-of-vocabulary head
    returns the honest ``""`` (unknown), never a silently-guessed kind.
    """

    norm = re.sub(r"\s+", " ", (text or "")).strip()
    if not norm:
        return ""
    tokens = _HEAD_TOKEN_SEP_RE.split(norm)
    for raw in reversed(tokens):
        kind = _classify_token(_TOKEN_TRIM_RE.sub("", raw).casefold())
        if kind:
            return kind
    return ""


def parse_affected_statute_head(johto: str) -> AffectedStatuteHead | None:
    """Parse the affected-statute identity head, if present."""

    zone = target_zone(johto)
    title_date_match = _AFFECTED_HEAD_TITLE_DATE_RE.search(zone)
    match = None if title_date_match is not None else _AFFECTED_HEAD_RE.search(zone)
    if match is None and title_date_match is None:
        return None
    if match is not None:
        title_phrase = re.sub(r"\s+", " ", match.group("title") or "").strip()
        text = match.group(0)
        day = match.group("day")
        month = match.group("month")
        year = match.group("year")
        num = match.group("num")
        cite_year = match.group("cite_year")
    else:
        assert title_date_match is not None
        title = re.sub(r"\s+", " ", title_date_match.group("title") or "").strip()
        title = re.sub(
            r"^(?:eduskunnan päätöksen mukaisesti\s+)?"
            r"(?:muutetaan|kumotaan|lisätään|siirretään)\s+",
            "",
            title,
            flags=re.IGNORECASE,
        ).strip()
        instrument = title_date_match.group("instrument")
        title_phrase = f"{title} annetun {instrument}".strip()
        text = title_date_match.group(0)
        day = title_date_match.group("day")
        month = title_date_match.group("month")
        year = title_date_match.group("year")
        num = title_date_match.group("num")
        cite_year = title_date_match.group("cite_year")
    return AffectedStatuteHead(
        text=text,
        title_phrase=title_phrase,
        instrument=instrument_from_text(title_phrase),
        issue_date=_parse_finnish_date(day, month, year),
        cited_num=num,
        cited_year=cite_year,
    )


def parse_delegated_authority_lead_in(johto: str) -> DelegatedAuthorityLeadIn | None:
    """Parse ``... nojalla`` authority lead-ins used by delegated instruments."""

    zone = target_zone(johto)
    lower = zone.lower()
    if "nojalla" not in lower:
        return None
    before_nojalla = lower.split("nojalla", 1)[0]
    if any(keyword in before_nojalla for keyword in ("muutetaan", "kumotaan", "lisätään", "siirretään")):
        return None
    citations = tuple(f"{num}/{year}" for num, year in _CITATION_RE.findall(zone))
    return DelegatedAuthorityLeadIn(text=zone, authority_citations=citations)


def parse_routing_surface(johto: str, source_year: int | None = None) -> JohtolauseRoutingSurface:
    """Parse the subset of johtolause grammar used by citation routing."""

    zone = target_zone(johto)
    citations: list[TargetCitation] = []
    for num, year in _CITATION_RE.findall(zone):
        normalized = normalize_source_citation_id(f"{num}/{year}", source_year) if source_year is not None else None
        citations.append(TargetCitation(num=num, year=year, normalized_id=normalized))
    return JohtolauseRoutingSurface(
        target_zone=zone,
        affected_head=parse_affected_statute_head(johto),
        delegated_authority=parse_delegated_authority_lead_in(johto),
        target_citations=tuple(citations),
    )

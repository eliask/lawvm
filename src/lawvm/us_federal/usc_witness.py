"""Witness extraction from USC ``source-credit`` amendment lineage.

Each USC section carries a ``source-credit`` paragraph enumerating every Public
Law that enacted or amended it, e.g.::

    (Pub. L. 95–598, Nov. 6, 1978, 92 Stat. 2549; Pub. L. 116–260, div. FF,
     title X, §1001, Dec. 27, 2020, 134 Stat. 2145; ...)

This is the witness-anchored coverage denominator: the set of (section, Public
Law) amendment witnesses for a title, without OLRC classification tables. This
module parses each ``source-credit`` into typed
:class:`UscPublicLawWitness` rows and provides per-title witness aggregation and
a Congress/PL-number window counter.

Parsing is deliberately tolerant of the dense citation grammar (``div.``,
``title``, multiple ``§§``, multiple ``Stat.`` pages, en-dash page ranges) but
NEVER fabricates a (congress, law_number): a ``Pub. L.`` token that does not
yield a clean ``congress–number`` head is recorded as a typed unparsed finding
rather than guessed.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Iterable

from lawvm.us_federal.source_tree import UscSection, UscSourceDocument

# A Public-Law head: "Pub. L. 116–260" / "Pub. L. 95-598". The dash is an en-dash
# in the source but normalized text may carry either; accept both.
_PUBLIC_LAW_HEAD_RE = re.compile(
    r"Pub\.\s*L\.\s*(?P<congress>\d+)[–—\-](?P<number>\d+)"
)

# Pinpoint section(s) cited within a credit segment: "§1001" or "§§211, 226(a)".
_PINPOINT_RE = re.compile(r"§§?\s*([0-9A-Za-z][0-9A-Za-z()\-.,\s]*?)(?=,\s*[A-Z][a-z]{2,}\.|,\s*\d{4}|$)")

# A date inside a credit segment: "Dec. 27, 2020" / "Nov. 6, 1978".
_DATE_RE = re.compile(
    r"(?P<month>Jan|Feb|Mar|Apr|May|June|July|Aug|Sept|Sep|Oct|Nov|Dec)\.?\s+"
    r"(?P<day>\d{1,2}),\s*(?P<year>\d{4})"
)

# Statutes at Large cite: "134 Stat. 2145" (first/lead page of the segment).
_STAT_RE = re.compile(r"(?<!\d)(?P<vol>\d+)\s+Stat\.\s+(?P<page>\d+[0-9A-Za-z–\-]*)")


@dataclass(frozen=True)
class UscPublicLawWitness:
    """One Public Law amendment witness parsed from a ``source-credit`` segment.

    ``congress`` / ``law_number`` form the Public Law identity (e.g. 116-260).
    ``pinpoints`` are the cited amending ``§`` tokens within that law (may be
    empty for the original enactment credit). ``date_iso`` is the enactment date
    (``YYYY-MM-DD``) when parseable; ``statutes_at_large`` is the lead
    ``vol Stat. page`` cite.
    """

    congress: int
    law_number: int
    pinpoints: tuple[str, ...]
    date_iso: str
    date_text: str
    statutes_at_large: str
    raw_segment: str

    @property
    def public_law_label(self) -> str:
        return f"Public Law {self.congress}-{self.law_number}"

    @property
    def public_law_key(self) -> tuple[int, int]:
        return (self.congress, self.law_number)

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "congress": self.congress,
            "law_number": self.law_number,
            "public_law": self.public_law_label,
            "pinpoints": list(self.pinpoints),
            "date_iso": self.date_iso,
            "date_text": self.date_text,
            "statutes_at_large": self.statutes_at_large,
        }


@dataclass
class UscWitnessReport:
    """Per-title witness aggregation across all sections."""

    title: int
    year: str
    section_count: int
    section_witnesses: dict[str, tuple[UscPublicLawWitness, ...]] = field(default_factory=dict)
    unparsed: list[dict[str, str]] = field(default_factory=list)

    def all_witnesses(self) -> Iterable[tuple[str, UscPublicLawWitness]]:
        for section, witnesses in self.section_witnesses.items():
            for witness in witnesses:
                yield section, witness

    def public_law_pairs(self) -> set[tuple[str, tuple[int, int]]]:
        """The set of (section, (congress, law_number)) amendment witnesses."""
        return {
            (section, witness.public_law_key)
            for section, witness in self.all_witnesses()
        }

    def distinct_public_laws(self) -> set[tuple[int, int]]:
        return {witness.public_law_key for _section, witness in self.all_witnesses()}

    def total_witness_citations(self) -> int:
        return sum(len(ws) for ws in self.section_witnesses.values())

    def count_in_window(
        self,
        *,
        min_congress: int | None = None,
        max_congress: int | None = None,
        congress: int | None = None,
    ) -> int:
        """Count (section, Public Law) witnesses within a Congress window.

        ``congress`` pins a single Congress; otherwise ``min_congress`` /
        ``max_congress`` bound the (inclusive) range. Counts distinct
        (section, congress, law_number) pairs so a law cited twice in one
        section's credit (different pinpoints) counts once per section.
        """
        lo = congress if congress is not None else min_congress
        hi = congress if congress is not None else max_congress
        count = 0
        for section, key in self.public_law_pairs():
            c = key[0]
            if lo is not None and c < lo:
                continue
            if hi is not None and c > hi:
                continue
            count += 1
        return count

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "year": self.year,
            "section_count": self.section_count,
            "distinct_public_laws": len(self.distinct_public_laws()),
            "total_witness_citations": self.total_witness_citations(),
            "unparsed": self.unparsed,
        }


def _month_to_num(month: str) -> str | None:
    table = {
        "Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04", "May": "05",
        "June": "06", "Jun": "06", "July": "07", "Jul": "07", "Aug": "08",
        "Sept": "09", "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12",
    }
    return table.get(month)


def _date_iso(date_text: str) -> str:
    m = _DATE_RE.search(date_text)
    if m is None:
        return ""
    mm = _month_to_num(m.group("month"))
    if not mm:
        return ""
    return f"{m.group('year')}-{mm}-{int(m.group('day')):02d}"


def _split_credit_segments(credit: str) -> list[str]:
    """Split a source-credit into per-Public-Law segments.

    The credit is one parenthesized list of ``Pub. L. ...`` segments separated by
    ``;``. We split on ``Pub. L.`` heads so each segment begins at a public-law
    token (a leading ``§`` enactment-section before the first ``Pub. L.`` is
    dropped — it belongs to the title-enactment credit, not a section witness).
    """
    body = credit.strip()
    if body.startswith("(") and body.endswith(")"):
        body = body[1:-1]
    # Split before each "Pub. L." head, keeping the head with its segment.
    parts = re.split(r"(?=Pub\.\s*L\.)", body)
    return [p.strip().rstrip(";").strip() for p in parts if "Pub. L." in p]


def parse_source_credit_witnesses(
    credit: str, *, section: str = ""
) -> tuple[tuple[UscPublicLawWitness, ...], list[dict[str, str]]]:
    """Parse one ``source-credit`` string into typed Public Law witnesses.

    Returns ``(witnesses, unparsed)``. A ``Pub. L.`` segment whose head does not
    yield a clean ``congress–number`` is recorded in ``unparsed`` (typed finding)
    rather than guessed.
    """
    witnesses, unparsed_rows = _parse_source_credit_witnesses_cached(credit, section)
    return witnesses, [
        {
            "rule_id": rule_id,
            "section": row_section,
            "reason": reason,
            "segment": segment,
        }
        for rule_id, row_section, reason, segment in unparsed_rows
    ]


@lru_cache(maxsize=65536)
def _parse_source_credit_witnesses_cached(
    credit: str,
    section: str,
) -> tuple[
    tuple[UscPublicLawWitness, ...],
    tuple[tuple[str, str, str, str], ...],
]:
    witnesses: list[UscPublicLawWitness] = []
    unparsed: list[tuple[str, str, str, str]] = []

    if not credit.strip():
        return (), ()

    for segment in _split_credit_segments(credit):
        head = _PUBLIC_LAW_HEAD_RE.search(segment)
        if head is None:
            unparsed.append(
                (
                    "us_usc_source_credit_unparsed_public_law",
                    section,
                    "Pub. L. segment lacks a clean congress-number head",
                    segment[:120],
                )
            )
            continue

        congress = int(head.group("congress"))
        number = int(head.group("number"))

        tail = segment[head.end():]
        pinpoints = _extract_pinpoints(tail)
        date_text = ""
        dm = _DATE_RE.search(segment)
        if dm is not None:
            date_text = dm.group(0)
        stat = ""
        sm = _STAT_RE.search(segment)
        if sm is not None:
            stat = f"{sm.group('vol')} Stat. {sm.group('page')}"

        witnesses.append(
            UscPublicLawWitness(
                congress=congress,
                law_number=number,
                pinpoints=pinpoints,
                date_iso=_date_iso(date_text),
                date_text=date_text,
                statutes_at_large=stat,
                raw_segment=_normalize(segment),
            )
        )

    return tuple(witnesses), tuple(unparsed)


def _extract_pinpoints(tail: str) -> tuple[str, ...]:
    """Extract amending ``§`` pinpoint tokens from a credit segment tail.

    Stops at the date (which begins the trailing ``Month Day, Year, vol Stat.``).
    A segment with no ``§`` (the bare original-enactment credit) yields ``()``.
    """
    # Cut the tail at the first date so Stat. pages are not mistaken for pinpoints.
    dm = _DATE_RE.search(tail)
    scope = tail[: dm.start()] if dm is not None else tail
    pinpoints: list[str] = []
    for m in re.finditer(r"§§?\s*", scope):
        rest = scope[m.end():]
        # A pinpoint token: digits/letters/parens up to the next §, comma-major
        # boundary, or the segment end. Keep it compact; this is a witness label,
        # not a parsed address.
        tok = re.match(r"[0-9A-Za-z][0-9A-Za-z()]*(?:\([0-9A-Za-z]+\))*", rest)
        if tok is not None and tok.group(0):
            pinpoints.append(tok.group(0))
    return tuple(pinpoints)


def _normalize(text: str) -> str:
    return " ".join(text.split())


def extract_title_witnesses(document: UscSourceDocument) -> UscWitnessReport:
    """Build the per-title witness report from a parsed USC source document."""
    report = UscWitnessReport(
        title=document.title,
        year=document.year,
        section_count=len(document.sections),
    )
    for section in document.sections:
        witnesses, unparsed = parse_source_credit_witnesses(
            section.source_credit_raw, section=section.section
        )
        if witnesses:
            report.section_witnesses[section.section] = witnesses
        report.unparsed.extend(unparsed)
    return report


def section_public_law_witnesses(
    section: UscSection,
) -> tuple[UscPublicLawWitness, ...]:
    """Convenience: the Public Law witnesses for a single section."""
    witnesses, _unparsed = parse_source_credit_witnesses(
        section.source_credit_raw, section=section.section
    )
    return witnesses


def witness_congress_histogram(report: UscWitnessReport) -> dict[int, int]:
    """Histogram of distinct (section, PL) witnesses by Congress."""
    counter: Counter[int] = Counter()
    for _section, key in report.public_law_pairs():
        counter[key[0]] += 1
    return dict(sorted(counter.items()))

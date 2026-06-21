"""Finnish date vocabulary shared by Finland-local parsers.

This module owns only lexical month names and mechanical day/month/year
construction. Grammar ownership stays with the caller.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional

FI_MONTH_PARTITIVE_TO_NUMBER: dict[str, int] = {
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

FI_MONTH_GENITIVE_TO_NUMBER: dict[str, int] = {
    partitive[:-2] + "n": number
    for partitive, number in FI_MONTH_PARTITIVE_TO_NUMBER.items()
}


def fi_partitive_month_number(
    month_name: str | None,
    *,
    tolerate_finlex_typos: bool = False,
) -> int | None:
    """Return the month number for a Finnish partitive month token.

    ``tolerate_finlex_typos`` folds only observed mechanical source typos:
    doubled ``t`` (``joulukuutta``) and hyphenation artifacts
    (``joulukuu-ta``). Unknown month names remain unknown.
    """
    if not month_name:
        return None
    folded = month_name.lower()
    if tolerate_finlex_typos:
        folded = folded.replace("-", "")
    month = FI_MONTH_PARTITIVE_TO_NUMBER.get(folded)
    if month is None and tolerate_finlex_typos and folded.endswith("kuutta"):
        month = FI_MONTH_PARTITIVE_TO_NUMBER.get(folded[:-3] + "ta")
    return month


def parse_fi_day_month_year(
    day: str | None,
    month_name: str | None,
    year: str | None,
    *,
    tolerate_finlex_typos: bool = False,
) -> Optional[dt.date]:
    """Parse ``N päivänä/päivään <month> YYYY`` captures into a date."""
    if not day or not month_name or not year:
        return None
    month = fi_partitive_month_number(
        month_name,
        tolerate_finlex_typos=tolerate_finlex_typos,
    )
    if month is None:
        return None
    try:
        return dt.date(int(year), month, int(day))
    except ValueError:
        return None


class FiDateForm(str, Enum):
    """The morphological form a Finnish date was lexed in.

    The form is semantically load-bearing for the lifecycle-minting callers:
    the essive ``päivänä`` appears in commencement clauses, the allative
    ``päivään`` in expiry clauses, the dotted ``D.M.YYYY`` is form-neutral, and
    ``vuoden YYYY loppuun`` is the year-end shorthand resolving to December 31.
    Callers discriminate commencement vs expiry on this field, so it must never
    be discarded.
    """

    ESSIVE = "essive"
    ALLATIVE = "allative"
    DOTTED = "dotted"
    YEAR_END = "year_end"


@dataclass(frozen=True)
class FiDateMatch:
    """A Finnish date lexed out of clause prose, with its load-bearing form."""

    value: dt.date
    form: FiDateForm
    start: int
    end: int


# Day-month-year forms. ``päivänä`` (essive) and ``päivään`` (allative) share
# the same NN-month-YYYY shape and differ only in the suffix vowel; the suffix
# selects the form, which the callers read. The day/suffix spacing is a plain
# ``\s+`` (no leading-dot tolerance) to mirror exactly the legacy inline
# date regexes this recognizer replaces; the whole-law fixed-term extractor
# keeps its own dotted-day tolerance separately.
# The suffix vowel classes ``päivän[aä]`` (essive) and ``päivä[äa]n`` (allative)
# fold the observed a/ä mechanical typos exactly as the legacy inline regexes
# did, so this recognizer reproduces them byte-for-byte. Two day-suffix spacings
# are offered: the strict ``\s+`` (the temporal/effect-lowering legacy shape) and
# a ``\.?\s*`` variant that additionally tolerates the dotted-ordinal day
# ``15.päivänä`` accepted by the fixed-term commencement regexes.
_ESSIVE_DATE_RE = re.compile(
    r"(?P<day>\d{1,2})\s+päivän[aä]\s+(?P<month>[a-zäöå]+)\s+(?P<year>\d{4})",
    re.IGNORECASE,
)
_ALLATIVE_DATE_RE = re.compile(
    r"(?P<day>\d{1,2})\s+päivä[äa]n\s+(?P<month>[a-zäöå]+)\s+(?P<year>\d{4})",
    re.IGNORECASE,
)
_ESSIVE_DATE_DOTTED_DAY_RE = re.compile(
    r"(?P<day>\d{1,2})\.?\s*päivän[aä]\s+(?P<month>[a-zäöå]+)\s+(?P<year>\d{4})",
    re.IGNORECASE,
)
_ALLATIVE_DATE_DOTTED_DAY_RE = re.compile(
    r"(?P<day>\d{1,2})\.?\s*päivä[äa]n\s+(?P<month>[a-zäöå]+)\s+(?P<year>\d{4})",
    re.IGNORECASE,
)
_DOTTED_DATE_RE = re.compile(
    r"(?P<day>\d{1,2})\.\s?(?P<month>\d{1,2})\.\s?(?P<year>\d{4})",
)
_YEAR_END_RE = re.compile(
    r"vuoden\s+(?P<year>\d{4})\s+loppuun",
    re.IGNORECASE,
)


def _essive_candidate(
    text: str, *, tolerate_finlex_typos: bool, tolerate_dotted_day: bool
) -> Optional[FiDateMatch]:
    pattern = _ESSIVE_DATE_DOTTED_DAY_RE if tolerate_dotted_day else _ESSIVE_DATE_RE
    m = pattern.search(text)
    if m is None:
        return None
    value = parse_fi_day_month_year(
        m.group("day"),
        m.group("month"),
        m.group("year"),
        tolerate_finlex_typos=tolerate_finlex_typos,
    )
    if value is None:
        return None
    return FiDateMatch(value, FiDateForm.ESSIVE, m.start(), m.end())


def _allative_candidate(
    text: str, *, tolerate_finlex_typos: bool, tolerate_dotted_day: bool
) -> Optional[FiDateMatch]:
    pattern = _ALLATIVE_DATE_DOTTED_DAY_RE if tolerate_dotted_day else _ALLATIVE_DATE_RE
    m = pattern.search(text)
    if m is None:
        return None
    value = parse_fi_day_month_year(
        m.group("day"),
        m.group("month"),
        m.group("year"),
        tolerate_finlex_typos=tolerate_finlex_typos,
    )
    if value is None:
        return None
    return FiDateMatch(value, FiDateForm.ALLATIVE, m.start(), m.end())


def _dotted_candidate(text: str) -> Optional[FiDateMatch]:
    m = _DOTTED_DATE_RE.search(text)
    if m is None:
        return None
    try:
        value = dt.date(int(m.group("year")), int(m.group("month")), int(m.group("day")))
    except ValueError:
        return None
    return FiDateMatch(value, FiDateForm.DOTTED, m.start(), m.end())


def _year_end_candidate(text: str) -> Optional[FiDateMatch]:
    m = _YEAR_END_RE.search(text)
    if m is None:
        return None
    try:
        value = dt.date(int(m.group("year")), 12, 31)
    except ValueError:
        return None
    return FiDateMatch(value, FiDateForm.YEAR_END, m.start(), m.end())


_ALL_FORMS: frozenset[FiDateForm] = frozenset(FiDateForm)


def match_fi_date(
    text: str,
    *,
    forms: frozenset[FiDateForm] | set[FiDateForm] | None = None,
    tolerate_finlex_typos: bool = False,
    tolerate_dotted_day: bool = False,
) -> Optional[FiDateMatch]:
    """Lex the first Finnish date out of ``text``, returning value AND form.

    This is the single shared owner of "what is a Finnish date in legislative
    prose". It recognises four forms (see :class:`FiDateForm`):

    * essive  ``NN päivänä Kkkuuta YYYY``  (commencement clauses)
    * allative ``NN päivään Kkkuuta YYYY`` (expiry clauses)
    * dotted  ``D.M.YYYY``                 (form-neutral)
    * year-end ``vuoden YYYY loppuun``     (-> December 31 of YYYY)

    The form is returned because callers discriminate on it (commencement vs
    expiry). ``forms`` restricts which forms are eligible — a caller that knows
    its clause carries only an allative expiry date passes ``{ALLATIVE}`` so an
    incidental essive citation date elsewhere in the clause cannot shadow it
    (this preserves the legacy per-site form selection exactly). When several
    eligible forms match, the one occurring EARLIEST in the text wins.

    Returns ``None`` when no eligible date is present or the lexed components do
    not form a valid calendar date (unknown month, impossible day) — the caller
    turns ``None`` into a typed residual rather than silently minting a wrong
    date.
    """
    allowed = _ALL_FORMS if forms is None else frozenset(forms)
    candidates: list[FiDateMatch] = []
    if FiDateForm.ESSIVE in allowed:
        c = _essive_candidate(
            text,
            tolerate_finlex_typos=tolerate_finlex_typos,
            tolerate_dotted_day=tolerate_dotted_day,
        )
        if c is not None:
            candidates.append(c)
    if FiDateForm.ALLATIVE in allowed:
        c = _allative_candidate(
            text,
            tolerate_finlex_typos=tolerate_finlex_typos,
            tolerate_dotted_day=tolerate_dotted_day,
        )
        if c is not None:
            candidates.append(c)
    if FiDateForm.DOTTED in allowed:
        c = _dotted_candidate(text)
        if c is not None:
            candidates.append(c)
    if FiDateForm.YEAR_END in allowed:
        c = _year_end_candidate(text)
        if c is not None:
            candidates.append(c)
    if not candidates:
        return None
    candidates.sort(key=lambda m: m.start)
    return candidates[0]

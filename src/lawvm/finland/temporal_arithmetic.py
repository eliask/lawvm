"""Named temporal-arithmetic rules pinned to Finnish statutory authority.

Duration-form whole-law validity ("on voimassa kahden vuoden ajan sen
voimaantulosta") cannot be computed with ad hoc Python date arithmetic: the
controlling computation rule lives in laki säädettyjen määräaikain
laskemisesta (150/1930), which is pinned in the corpus
(``finlex://sd-cons/1930/150/fin@20050128/main.xml``). Every computed end
date carries the rule object's ``rule_id`` and ``authority`` in its bound
provenance so the arithmetic is never an unattributed guess.

Pinned source text, 150/1930 §3 (corpus oracle, verbatim):

    "Aika, joka on määrätty viikkoina, kuukausina tai vuosina nimitetyn
    päivän jälkeen, päättyy sinä viikon tai määräkuukauden päivänä, joka
    nimeltään tahi järjestysnumeroltaan vastaa sanottua päivää. Jos vastaavaa
    päivää ei ole siinä kuussa, jona määräaika päättyisi, pidetään sen
    kuukauden viimeinen päivä määräajan loppupäivänä."

I.e. a period of months/years from a named day D ends on the CORRESPONDING
day-of-month of the terminal month; if the terminal month has no such day,
the LAST day of that month is the period's end day (month-end fallback).

Applied to whole-law validity (doctrine brief, Class 1): the law is in force
ON its commencement day, so a validity of N years/months lapses at the START
of the corresponding day C — ``expires_on = C`` (exclusive cutoff) and
``valid_until = C - 1 day`` (inclusive last in-force day). Example pinned in
the doctrine brief: a law commencing 22 Jul 2024 that is "voimassa vuoden
voimaantulosta" runs to 21 Jul 2025 inclusive and lapses at the start of
22 Jul 2025.

Scope caveat (recorded on every computed bound, never silent): 150/1930 §1
scopes the statute to procedural deadlines ("määräaika noudatettavaksi
tuomioistuimessa tahi muun viranomaisen luona"). Applying its §3 arithmetic
to whole-law validity is a RECORDED INFERENCE under general Finnish
määräaika doctrine, not a grammar fact of the drafting guides.

Deliberately NOT implemented:
  - §2 day-counted periods (different rule; no corpus duration row uses days);
  - §3 week periods (corresponding weekday; no corpus row uses weeks);
  - §5 holiday deferral: it defers the day on which a procedural ACT may
    still be performed; a statute's validity lapse is not an act, so the
    lapse is NOT pushed past holidays.
Duration forms outside the implemented year/month input domain stay typed
residue (TEMPORAL.DURATION_ARITHMETIC_AUTHORITY_MISSING).
"""

from __future__ import annotations

import calendar
import datetime as dt
from dataclasses import dataclass

FI_150_1930_AUTHORITY = "fi/150/1930"

# §1 scope caveat, recorded verbatim-adjacent on every computed bound.
FI_150_1930_SCOPE_CAVEAT = (
    "150/1930 §1 scopes the statute to procedural deadlines (määräaika "
    "noudatettavaksi tuomioistuimessa tahi muun viranomaisen luona); "
    "applying its §3 corresponding-day arithmetic to whole-law validity is "
    "a recorded inference, not a grammar fact"
)

RULE_FI_DURATION_CORRESPONDING_DAY = "fi_duration_year_month_corresponding_day"

# Elided-year year-end ("tulee voimaan ... <year> ... ja on voimassa vuoden
# loppuun"): not 150/1930 arithmetic — a narrow same-sentence inference that
# the elided year is the commencement year (doctrine brief, Class 1). Always
# marked high_confidence_inference, never a grammar fact.
RULE_FI_ELIDED_YEAR_END = "fi_elided_year_end_from_same_sentence_commencement_year"


@dataclass(frozen=True)
class TemporalArithmeticRule:
    """One named, authority-pinned temporal computation rule."""

    authority: str
    rule_id: str
    input_kind: str
    scope_caveat: str


FI_DURATION_CORRESPONDING_DAY_RULE = TemporalArithmeticRule(
    authority=FI_150_1930_AUTHORITY,
    rule_id=RULE_FI_DURATION_CORRESPONDING_DAY,
    input_kind="duration_from_commencement",
    scope_caveat=FI_150_1930_SCOPE_CAVEAT,
)


@dataclass(frozen=True)
class ComputedValidityEnd:
    """A validity end computed under a named rule, with full provenance."""

    valid_until: dt.date
    expires_on: dt.date
    rule: TemporalArithmeticRule
    commencement: dt.date
    duration_spec: str


def corresponding_day_after_months(start: dt.date, months: int) -> dt.date:
    """150/1930 §3: the day of the terminal month corresponding to ``start``.

    If the terminal month has no corresponding day (e.g. 31 Aug + 6 months →
    February), the last day of the terminal month is the end day.
    """
    if months <= 0:
        raise ValueError(f"period must be positive; got {months} months")
    total = start.month - 1 + months
    year = start.year + total // 12
    month = total % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    return dt.date(year, month, min(start.day, last_day))


def duration_validity_end(
    commencement: dt.date, *, years: int = 0, months: int = 0
) -> ComputedValidityEnd:
    """Whole-law validity end for a year/month duration from commencement.

    Corresponding day ``C = commencement + period`` per 150/1930 §3 (with
    month-end fallback); the law is in force on the commencement day itself,
    so the validity lapses at the start of C: ``expires_on = C`` and
    ``valid_until = C - 1 day``.
    """
    if years < 0 or months < 0:
        raise ValueError(f"period components must be non-negative; got {years}y {months}m")
    total_months = years * 12 + months
    period_end = corresponding_day_after_months(commencement, total_months)
    parts = (f"{years}Y" if years else "") + (f"{months}M" if months else "")
    return ComputedValidityEnd(
        valid_until=period_end - dt.timedelta(days=1),
        expires_on=period_end,
        rule=FI_DURATION_CORRESPONDING_DAY_RULE,
        commencement=commencement,
        duration_spec=f"P{parts}",
    )

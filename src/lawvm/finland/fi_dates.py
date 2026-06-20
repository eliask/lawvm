"""Finnish date vocabulary shared by Finland-local parsers.

This module owns only lexical month names and mechanical day/month/year
construction. Grammar ownership stays with the caller.
"""
from __future__ import annotations

import datetime as dt
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

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


def parse_fi_day_month_year(
    day: str | None,
    month_name: str | None,
    year: str | None,
) -> Optional[dt.date]:
    """Parse ``N päivänä/päivään <month> YYYY`` captures into a date."""
    if not day or not month_name or not year:
        return None
    month = FI_MONTH_PARTITIVE_TO_NUMBER.get(month_name.lower())
    if month is None:
        return None
    try:
        return dt.date(int(year), month, int(day))
    except ValueError:
        return None

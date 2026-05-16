"""NZ source date parsing helpers.

These helpers normalize source date strings for witness lookup only. They do
not establish commencement, effect, or point-in-time validity.
"""

from __future__ import annotations

import re


def nz_date_text_to_iso(text: str) -> str:
    """Normalize simple NZ source dates to ``YYYY-MM-DD``.

    Returns an empty string for unsupported shapes so callers keep the missing
    witness explicit instead of guessing.
    """

    normalized = " ".join(text.split())
    iso_match = _ISO_DATE_RE.match(normalized)
    if iso_match is not None:
        return iso_match.group(1)
    match = _DATE_TEXT_RE.match(normalized)
    if match is None:
        return ""
    month = _MONTHS.get(match.group("month").lower())
    day = int(match.group("day"))
    if month is None or day < 1 or day > 31:
        return ""
    return f"{match.group('year')}-{month}-{day:02d}"


_ISO_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:\b|$)")
_DATE_TEXT_RE = re.compile(r"^(?P<day>\d{1,2}) (?P<month>[A-Za-z]+) (?P<year>\d{4})(?:\b|$)")
_MONTHS = {
    "january": "01",
    "february": "02",
    "march": "03",
    "april": "04",
    "may": "05",
    "june": "06",
    "july": "07",
    "august": "08",
    "september": "09",
    "october": "10",
    "november": "11",
    "december": "12",
}

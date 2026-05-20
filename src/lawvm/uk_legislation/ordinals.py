"""UK ordinal parsing helpers."""
from __future__ import annotations

import re


_ORDINAL_WORDS = {
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "fifth": 5,
    "sixth": 6,
    "seventh": 7,
    "eighth": 8,
    "ninth": 9,
    "tenth": 10,
}


def _uk_ordinal_to_int(raw: str) -> int | None:
    token = " ".join(str(raw or "").lower().split()).strip(" .")
    if not token:
        return None
    if token.endswith("ly"):
        token = token[:-2]
    if token in _ORDINAL_WORDS:
        return _ORDINAL_WORDS[token]
    match = re.fullmatch(r"(\d+)(?:st|nd|rd|th)?", token)
    if match:
        return int(match.group(1))
    return None

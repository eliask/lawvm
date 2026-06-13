"""Johtolause scope mention extraction helpers.

The uncovered-body fallback uses these labels as a guard: source body sections
not mentioned in the operative preamble should not silently enter replay.
"""

from __future__ import annotations

import functools
import re

from lawvm.finland.helpers import _norm_num_token

_SECTION_REF_RE = re.compile(
    r"(\d+\s*[a-z]?)(?:[-\u2014\u2013\u2015](\d+\s*[a-z]?))?\s*§",
    re.I,
)
_SECTION_LIST_RE = re.compile(
    r"((?:\d+\s*[a-z]?(?:[-\u2014\u2013\u2015]\d+\s*[a-z]?)?)"
    r"(?:\s*(?:,|ja|sekä)\s*(?:\d+\s*[a-z]?(?:[-\u2014\u2013\u2015]\d+\s*[a-z]?)?))+)\s*§",
    re.I,
)
_SECTION_LIST_SPLIT_RE = re.compile(r"\s*(?:,|ja|sekä)\s*")
_SECTION_RANGE_SEGMENT_RE = re.compile(
    r"(\d+\s*[a-z]?)[-\u2014\u2013\u2015](\d+\s*[a-z]?)",
    re.I,
)
_ALPHA_SUFFIX_LABEL_RE = re.compile(r"(\d+)([a-z])")


@functools.lru_cache(maxsize=8192)
def expand_johto_section_label_range(start: str, end: str) -> tuple[str, ...]:
    """Expand a johto-mentioned section range into normalized labels.

    Supports purely numeric ranges (``17-21 §``) and same-base alpha suffix
    ranges (``21 a-21 d §``). Unknown shapes fall back to the normalized
    endpoints rather than guessing intermediate labels.
    """
    start_norm = _norm_num_token(start)
    end_norm = _norm_num_token(end)
    if not start_norm or not end_norm:
        return tuple(label for label in (start_norm, end_norm) if label)

    if start_norm.isdigit() and end_norm.isdigit():
        s_int, e_int = int(start_norm), int(end_norm)
        if 0 < e_int - s_int < 500:
            return tuple(str(i) for i in range(s_int, e_int + 1))
        return (start_norm, end_norm)

    start_match = _ALPHA_SUFFIX_LABEL_RE.fullmatch(start_norm)
    end_match = _ALPHA_SUFFIX_LABEL_RE.fullmatch(end_norm)
    if start_match and end_match and start_match.group(1) == end_match.group(1):
        start_ord = ord(start_match.group(2))
        end_ord = ord(end_match.group(2))
        if 0 <= end_ord - start_ord < 26:
            base = start_match.group(1)
            return tuple(f"{base}{chr(code)}" for code in range(start_ord, end_ord + 1))

    return (start_norm, end_norm)


def collect_johto_mentioned_section_labels(johto_text: str) -> set[str]:
    return set(collect_johto_mentioned_section_labels_frozenset(johto_text))


@functools.lru_cache(maxsize=8192)
def collect_johto_mentioned_section_labels_frozenset(johto_text: str) -> frozenset[str]:
    labels: set[str] = set()
    for match in _SECTION_REF_RE.finditer(johto_text):
        start = match.group(1)
        end = match.group(2)
        if end:
            labels.update(expand_johto_section_label_range(start, end))
        else:
            norm = _norm_num_token(start)
            if norm:
                labels.add(norm)

    for match in _SECTION_LIST_RE.finditer(johto_text):
        for segment in _SECTION_LIST_SPLIT_RE.split(match.group(1)):
            segment = segment.strip()
            if not segment:
                continue
            range_match = _SECTION_RANGE_SEGMENT_RE.fullmatch(segment)
            if range_match:
                labels.update(
                    expand_johto_section_label_range(
                        range_match.group(1),
                        range_match.group(2),
                    )
                )
                continue
            labels.add(_norm_num_token(segment))
    return frozenset(labels)

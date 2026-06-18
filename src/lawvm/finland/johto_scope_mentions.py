"""Johtolause scope mention extraction helpers.

The uncovered-body fallback uses these labels as a guard: source body sections
not mentioned in the operative preamble should not silently enter replay.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass

from lawvm.finland.address_parse import parse_legal_addresses
from lawvm.finland.helpers import _norm_num_token

_DASH_CHARS = r"[-\u2013\u2014\u2015]"  # hyphen, en-dash, em-dash, horizontal bar
_SECTION_REF_RE = re.compile(
    r"(\d{1,4}+\s{0,3}+[a-z]?)(?:[-\u2014\u2013\u2015](\d{1,4}+\s{0,3}+[a-z]?))?\s*§",
    re.I,
)
_SECTION_LIST_RE = re.compile(
    r"((?:\d{1,4}+\s{0,3}+[a-z]?(?:[-\u2014\u2013\u2015]\d{1,4}+\s{0,3}+[a-z]?)?)"
    r"(?:\s*(?:,|ja|sekä)\s*(?:\d{1,4}+\s{0,3}+[a-z]?(?:[-\u2014\u2013\u2015]\d{1,4}+\s{0,3}+[a-z]?)?))+)\s*§",
    re.I,
)
_SECTION_LIST_SPLIT_RE = re.compile(r"\s*(?:,|ja|sekä)\s*")
_SECTION_RANGE_SEGMENT_RE = re.compile(
    r"(\d{1,4}+\s{0,3}+[a-z]?)[-\u2014\u2013\u2015](\d{1,4}+\s{0,3}+[a-z]?)",
    re.I,
)
_ALPHA_SUFFIX_LABEL_RE = re.compile(r"(\d+)([a-z])")
_NEW_CHAPTER_RE = re.compile(
    r"(?:lisätään\s+(?:lakiin\s+)?|uusi\s+)"
    r"(\d{1,4}+\s{0,3}+[a-z]?)"
    r"(?:\s*" + _DASH_CHARS + r"\s*(\d{1,4}+\s{0,3}+[a-z]?))?"
    r"\s+luku",
    re.I,
)
_MOVE_DESTINATION_CHAPTER_RE = re.compile(
    r"\bsiirretään\b[^§\n]{0,200}?(?:lakiin\s+)?(\d{1,4}+\s{0,3}+[a-z]?)\s+lukuun",
    re.I,
)
_MOVE_SECTION_TO_CHAPTER_RE = re.compile(
    r"(\d{1,4}+\s{0,3}+[a-z]?)\s*§[^§\n]{0,120}?\bsiirretään\b[^§\n]{0,200}?"
    r"(?:lakiin\s+)?(\d{1,4}+\s{0,3}+[a-z]?)\s+lukuun",
    re.I,
)
_MUUTETAAN_RE = re.compile(r"\bmuutetaan\b", re.I)
_LUKU_RE = re.compile(r"\bluku\b", re.I)
_CHAPTER_NUMBER_RE = re.compile(
    r"(\d+\s*(?:[a-z](?![a-z]))?)(?:\s*" + _DASH_CHARS + r"\s*(\d+\s*(?:[a-z](?![a-z]))?))?",
    re.I,
)
_SECTION_OR_GENITIVE_CHAPTER_RE = re.compile(r"§|luvun", re.I)


@dataclass(frozen=True, slots=True)
class MovedSectionDestination:
    section_label: str
    destination_chapter_label: str


@dataclass(frozen=True, slots=True)
class JohtoChapterScopeMentions:
    new_chapter_labels: frozenset[str]
    replaced_chapter_labels: frozenset[str]
    moved_destination_chapter_labels: frozenset[str]
    moved_section_destinations: tuple[MovedSectionDestination, ...]


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


def collect_johto_moment_targets(johto_text: str) -> dict[str, frozenset[int]]:
    """Map johto-mentioned section labels to explicit momentti ordinals.

    Uncovered-body omission merges need these targets when the preamble names
    ``N §:n M momentti`` but compile emits no paragraph-scoped AmendmentOps.
    """
    targets: dict[str, set[int]] = {}
    for addr in parse_legal_addresses(johto_text):
        if (
            not addr.section
            or addr.subsection is None
            or addr.item is not None
            or addr.special
        ):
            continue
        section = _norm_num_token(addr.section)
        if section:
            targets.setdefault(section, set()).add(addr.subsection)
    return {section: frozenset(moments) for section, moments in targets.items()}


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


def collect_johto_chapter_scope_mentions(johto_text: str) -> JohtoChapterScopeMentions:
    """Extract chapter-level ownership clues from a Finnish johtolause."""
    new_chapter_labels: set[str] = set()
    replaced_chapter_labels: set[str] = set()
    moved_destination_chapter_labels: set[str] = set()
    moved_section_destinations: list[MovedSectionDestination] = []

    for match in _NEW_CHAPTER_RE.finditer(johto_text):
        start_label = _norm_num_token(match.group(1)).removesuffix("luku")
        end_label = _norm_num_token(match.group(2)).removesuffix("luku") if match.group(2) else None
        if start_label and end_label and start_label.isdigit() and end_label.isdigit():
            s_int, e_int = int(start_label), int(end_label)
            if 0 < e_int - s_int < 100:
                new_chapter_labels.update(str(i) for i in range(s_int, e_int + 1))
        elif start_label:
            new_chapter_labels.add(start_label)

    for match in _MOVE_DESTINATION_CHAPTER_RE.finditer(johto_text):
        dest_chapter = _norm_num_token(match.group(1)).removesuffix("luku")
        if dest_chapter:
            moved_destination_chapter_labels.add(dest_chapter)

    for match in _MOVE_SECTION_TO_CHAPTER_RE.finditer(johto_text):
        source_label = _norm_num_token(match.group(1))
        dest_chapter = _norm_num_token(match.group(2)).removesuffix("luku")
        if source_label and dest_chapter:
            moved_section_destinations.append(
                MovedSectionDestination(
                    section_label=source_label,
                    destination_chapter_label=dest_chapter,
                )
            )

    if _MUUTETAAN_RE.search(johto_text):
        for luku_match in _LUKU_RE.finditer(johto_text):
            start = max(0, luku_match.start() - 200)
            prefix = johto_text[start : luku_match.start()]
            for range_match in _CHAPTER_NUMBER_RE.finditer(prefix):
                between = prefix[range_match.end() :]
                if _SECTION_OR_GENITIVE_CHAPTER_RE.search(between):
                    continue
                start_chapter = _norm_num_token(range_match.group(1)).removesuffix("luku")
                end_chapter = (
                    _norm_num_token(range_match.group(2)).removesuffix("luku")
                    if range_match.group(2)
                    else None
                )
                if start_chapter and end_chapter and start_chapter.isdigit() and end_chapter.isdigit():
                    s_int, e_int = int(start_chapter), int(end_chapter)
                    if 0 < e_int - s_int < 100:
                        replaced_chapter_labels.update(str(i) for i in range(s_int, e_int + 1))
                elif start_chapter:
                    replaced_chapter_labels.add(start_chapter)

    return JohtoChapterScopeMentions(
        new_chapter_labels=frozenset(new_chapter_labels),
        replaced_chapter_labels=frozenset(replaced_chapter_labels),
        moved_destination_chapter_labels=frozenset(moved_destination_chapter_labels),
        moved_section_destinations=tuple(moved_section_destinations),
    )

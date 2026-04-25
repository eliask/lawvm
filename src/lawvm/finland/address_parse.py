"""address_parse — shared Finnish legal address pattern parser.

Provides regex-based parsing of Finnish legal address patterns from text
fragments.  Used by both the VTS extractor and can be used as a complement
to the token-based johtolause PEG grammar for text-level address extraction.

Patterns handled:

  Section-level (whole section):
    "28 §"          → ParsedLegalAddress(section="28")
    "24 ja 24 a §"  → [ParsedLegalAddress(section="24"), ParsedLegalAddress(section="24a")]
    "12–14 §"       → [section="12", section="13", section="14"]

  Section + subsection:
    "6 §:n 1 momentti"        → ParsedLegalAddress(section="6", subsection=1)
    "6 §:n 1 ja 2 momentti"   → [section="6" sub=1, section="6" sub=2]

  Section + subsection + item:
    "6 §:n 1 momentin 3 kohta" → ParsedLegalAddress(section="6", subsection=1, item="3")

  Section + subsection + item + sub-item (alakohta):
    "6 §:n 2 momentin 1 kohdan a alakohta"
        → ParsedLegalAddress(section="6", subsection=2, item="1", subitem="a")

  Standalone subsections (no explicit § prefix in fragment — caller provides context):
    "2 ja 3 momentti"         → [ParsedLegalAddress(subsection=2), ParsedLegalAddress(subsection=3)]

  Section + special (heading/intro):
    "3 §:n otsikko"           → ParsedLegalAddress(section="3", special="heading")
    "3 §:n johdantokappale"   → ParsedLegalAddress(section="3", special="intro")

  Chapter-level:
    "3 luku"                  → ParsedLegalAddress(chapter="3")
    "2–5 luku"                → [chapter="2", chapter="3", chapter="4", chapter="5"]
    "2, 4 ja 5 luku"          → [chapter="2", chapter="4", chapter="5"]

Only patterns with reasonable confidence are emitted.  The caller is
responsible for deciding how to use sub-section-level addresses (e.g.
VTS intentionally skips them for repeal ops, but still uses this module
for section-level and chapter-level extraction).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List

# ---------------------------------------------------------------------------
# Data type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParsedLegalAddress:
    """Structured legal address parsed from Finnish text.

    Attributes:
        section:    Section number label, e.g. "6", "24a".  Empty string
                    means this address has no section context (e.g. a
                    standalone momentti reference).
        subsection: Subsection (momentti) number, or None.
        item:       Item (kohta) label, e.g. "3", "a".  None if absent.
        subitem:    Sub-item (alakohta) label, e.g. "a".  None if absent.
                    Per Lainkirjoittajan opas: "6 §:n 2 momentin 1 kohdan
                    a alakohta".
        chapter:    Chapter number label, e.g. "3", "5a".  None means this
                    address is not a chapter reference.
        special:    "heading", "intro", or "" for whole-node addresses.
    """

    section: str = ""
    subsection: int | None = None
    item: str | None = None
    subitem: str | None = None
    chapter: str | None = None
    special: str = ""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# Single section token: numeral with optional letter suffix (spaces stripped later)
_SEC_TOKEN = r"\d+(?:\s*[a-z])?"

# Numeric range between two section tokens (joined by an en-dash / em-dash / hyphen)
_SEC_RANGE = rf"{_SEC_TOKEN}\s*[–—―\-]\s*{_SEC_TOKEN}"

# Either a range or a single token
_SEC_ITEM = rf"(?:{_SEC_RANGE}|{_SEC_TOKEN})"

# Comma/ja-separated list of section items, terminated by § (bare, not §:n)
_MULTI_SEC_RE = re.compile(
    rf"({_SEC_ITEM}(?:\s*(?:,|ja)\s*{_SEC_ITEM})*)\s*§(?!:)",
    re.IGNORECASE,
)

# Section with genitive §:n or pykälän followed by subsection/special content.
# "pykälän" (genitive of "pykälä") appears in VTS repeal clauses where the
# drafter spelled out the word instead of using the § symbol.
_SEC_GEN_RE = re.compile(
    rf"({_SEC_TOKEN})\s*(?:§:n|pykälän)\s+(.+?)(?=\s+(?:{_SEC_ITEM})\s*§|$)",
    re.IGNORECASE | re.DOTALL,
)

# Subsection list (momentti) — standalone, no § prefix in the pattern itself
# E.g. "2 ja 3 momentti" or "1 momentti"
_SUBSEC_STANDALONE_RE = re.compile(
    r"(\d+(?:\s*(?:,|ja)\s*\d+)*)\s+momentti\b",
    re.IGNORECASE,
)

# Subsection in genitive form + item + optional sub-item:
#   "1 momentin 3 kohta"
#   "1 momentin 3 kohdan a alakohta"
_SUBSEC_KOHTA_ALAKOHTA_RE = re.compile(
    r"(\d+)\s+momentin\s+(\d+(?:\s*[a-z])?)\s+kohdan\s+(\d*\s*[a-z]?)\s+alakohta\b",
    re.IGNORECASE,
)

_SUBSEC_KOHTA_RE = re.compile(
    r"(\d+)\s+momentin\s+(\d+(?:\s*[a-z])?)\s+kohta\b",
    re.IGNORECASE,
)

# Subsection in genitive form: "1 momentin ..." without a following kohta
_SUBSEC_GEN_RE = re.compile(
    r"(\d+)\s+momentin?\b",
    re.IGNORECASE,
)

# --- Chapter ("N luku") patterns ---

# Chapter token: numeral with optional letter suffix
_CH_TOKEN = r"\d+(?:\s*[a-z])?"

# Range: "2–5 luku"
_CH_RANGE_RE = re.compile(
    r"(\d+)\s*[–—―\-]\s*(\d+)\s+luku\b",
    re.IGNORECASE,
)

# Comma/ja-separated list terminated by "luku": "2, 4 ja 5 luku"
_CH_LIST_RE = re.compile(
    rf"((?:{_CH_TOKEN}\s*(?:,|ja)\s*)+{_CH_TOKEN})\s+luku\b",
    re.IGNORECASE,
)

# Single: "5 luku"
_CH_SINGLE_RE = re.compile(
    rf"({_CH_TOKEN})\s+luku\b",
    re.IGNORECASE,
)

# Heading / intro keywords in Finnish
_HEADING_WORDS = frozenset({"otsikko", "otsikon", "väliotsikko", "väliotsikon"})
_INTRO_WORDS = frozenset({"johdantokappale", "johdantolause", "johdantokappaleen"})


def _norm_section(raw: str) -> str:
    """Normalize a section token: strip spaces, lowercase letter suffix."""
    return re.sub(r"\s+", "", raw.strip()).lower()


def _expand_sec_range(start: str, end: str) -> List[str]:
    """Expand a section range like '12'–'14', '33a'–'33c', or '52a'–'55'."""
    if start.isdigit() and end.isdigit():
        s, e = int(start), int(end)
        if s <= e:
            return [str(i) for i in range(s, e + 1)]
        return [start]
    m_s = re.fullmatch(r"(\d+)([a-z])", start, re.IGNORECASE)
    m_e = re.fullmatch(r"(\d+)([a-z])", end, re.IGNORECASE)
    if m_s and m_e and m_s.group(1) == m_e.group(1):
        base = m_s.group(1)
        s_c = m_s.group(2).lower()
        e_c = m_e.group(2).lower()
        if ord(s_c) <= ord(e_c):
            return [f"{base}{chr(c)}" for c in range(ord(s_c), ord(e_c) + 1)]
    if m_s and end.isdigit():
        s_n = int(m_s.group(1))
        e_n = int(end)
        if s_n < e_n:
            return [start] + [str(i) for i in range(s_n + 1, e_n + 1)]
    return [start]


def _expand_sec_item(raw_item: str) -> List[str]:
    """Expand a single section item (range or single token) to normalized labels."""
    raw_item = raw_item.strip()
    range_m = re.fullmatch(
        rf"({_SEC_TOKEN})\s*[–—―\-]\s*({_SEC_TOKEN})",
        raw_item,
        re.IGNORECASE,
    )
    if range_m:
        s = _norm_section(range_m.group(1))
        e = _norm_section(range_m.group(2))
        return _expand_sec_range(s, e)
    return [_norm_section(raw_item)]


def _parse_genitive_tail(section: str, tail: str) -> List[ParsedLegalAddress]:
    """Parse the content after '6 §:n ' → subsection, item, special refs.

    Returns a list of ParsedLegalAddress with the given section filled in.
    Returns empty list if the tail cannot be parsed.
    """
    tail = tail.strip()
    addresses: List[ParsedLegalAddress] = []

    # "1 momentin 3 kohdan a alakohta" — subsection + item + sub-item
    m = _SUBSEC_KOHTA_ALAKOHTA_RE.match(tail)
    if m:
        sub = int(m.group(1))
        item = _norm_section(m.group(2))
        subitem = _norm_section(m.group(3))
        return [ParsedLegalAddress(section=section, subsection=sub, item=item, subitem=subitem)]

    # "1 momentin 3 kohta[n ...]" — subsection + item
    m = _SUBSEC_KOHTA_RE.match(tail)
    if m:
        sub = int(m.group(1))
        item = _norm_section(m.group(2))
        # check for trailing johdantolause/otsikko
        rest = tail[m.end():].strip()
        special = ""
        first_word = rest.split()[0].lower() if rest else ""
        if first_word in _INTRO_WORDS:
            special = "intro"
        elif first_word in _HEADING_WORDS:
            special = "heading"
        return [ParsedLegalAddress(section=section, subsection=sub, item=item, special=special)]

    # "1 ja 2 momentti" — subsection list (nominative)
    m = _SUBSEC_STANDALONE_RE.match(tail)
    if m:
        for part in re.split(r"\s*(?:,|ja)\s*", m.group(1)):
            part = part.strip()
            if part.isdigit():
                addresses.append(ParsedLegalAddress(section=section, subsection=int(part)))
        if addresses:
            rest = tail[m.end():].strip()
            if rest:
                addresses.extend(parse_legal_addresses(rest))
            return addresses

    # "1 momentin johdantokappale" — subsection + intro
    m = _SUBSEC_GEN_RE.match(tail)
    if m:
        sub = int(m.group(1))
        rest = tail[m.end():].strip()
        first_word = rest.split()[0].lower() if rest else ""
        if first_word in _INTRO_WORDS:
            return [ParsedLegalAddress(section=section, subsection=sub, special="intro")]
        if first_word in _HEADING_WORDS:
            return [ParsedLegalAddress(section=section, subsection=sub, special="heading")]
        # plain genitive momentti with no following noun — still a subsection ref
        return [ParsedLegalAddress(section=section, subsection=sub)]

    # "otsikko" / "väliotsikko" — section heading
    first_word = tail.split()[0].lower() if tail else ""
    if first_word in _HEADING_WORDS:
        return [ParsedLegalAddress(section=section, special="heading")]
    if first_word in _INTRO_WORDS:
        return [ParsedLegalAddress(section=section, special="intro")]

    return []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_legal_addresses(text: str) -> List[ParsedLegalAddress]:
    """Parse Finnish legal address patterns from *text*.

    Returns a list of ParsedLegalAddress objects in the order they appear.
    Section-level references, subsection references, and item references are
    all captured.  Patterns that cannot be classified are silently skipped.

    Examples::

        >>> parse_legal_addresses("28 §")
        [ParsedLegalAddress(section='28', ...)]

        >>> parse_legal_addresses("6 §:n 1 momentti")
        [ParsedLegalAddress(section='6', subsection=1, ...)]

        >>> parse_legal_addresses("24 ja 24 a §")
        [ParsedLegalAddress(section='24', ...), ParsedLegalAddress(section='24a', ...)]

        >>> parse_legal_addresses("6 §:n 1 momentin 3 kohta")
        [ParsedLegalAddress(section='6', subsection=1, item='3', ...)]

        >>> parse_legal_addresses("2 ja 3 momentti")
        [ParsedLegalAddress(subsection=2, ...), ParsedLegalAddress(subsection=3, ...)]
    """
    addresses: List[ParsedLegalAddress] = []
    consumed: set[int] = set()

    # --- Pass 1: section genitive refs (§:n or pykälän ...) ---
    # These have higher specificity than bare § refs; process first and mark
    # their character spans consumed so Pass 2 doesn't re-match the numeral.
    # "pykälän" (genitive of "pykälä") appears in VTS repeal clauses where the
    # drafter spelled out the word instead of using the § symbol.
    for m in re.finditer(
        rf"({_SEC_TOKEN})\s*(?:§:n|pykälän)\b",
        text,
        re.IGNORECASE,
    ):
        if m.start() in consumed:
            continue
        consumed.update(range(m.start(), m.end()))

        section = _norm_section(m.group(1))
        # Collect the rest of this address: everything up to the next § (bare
        # or genitive), comma, semicolon, or sentence boundary.
        tail_start = m.end()
        tail_end = len(text)
        # Stop at next § unless it is coordinated into the same phrase
        # ("6 §:n 2 ja 3 momentti sekä 10 a–10 f §"), in which case the tail
        # parser must still see the later section sign.
        next_sec = re.search(
            rf"\b{_SEC_TOKEN}\s*§",
            text[tail_start:],
            re.IGNORECASE,
        )
        if next_sec:
            _candidate_tail_end = tail_start + next_sec.start()
            _coordinated_tail = text[tail_start:tail_start + next_sec.end()]
            if not re.search(
                rf"(?:,|ja|sekä)\s+{_SEC_ITEM}\s*§\s*$",
                _coordinated_tail,
                re.IGNORECASE,
            ):
                tail_end = _candidate_tail_end
        for stop in re.finditer(r"[;.]", text[tail_start:tail_end]):
            tail_end = tail_start + stop.start()
            break

        tail = text[tail_start:tail_end].strip()
        parsed = _parse_genitive_tail(section, tail)
        if parsed:
            addresses.extend(parsed)
            consumed.update(range(m.start(), tail_end))
        else:
            # Unable to parse tail — emit a bare section address
            addresses.append(ParsedLegalAddress(section=section))

    # --- Pass 2: bare § section refs (not §:n) ---
    for m in _MULTI_SEC_RE.finditer(text):
        if m.start() in consumed:
            continue
        raw_list = m.group(1)
        for raw_item in re.split(r"\s*(?:,|ja)\s*", raw_list):
            for label in _expand_sec_item(raw_item):
                if label:
                    addresses.append(ParsedLegalAddress(section=label))
        consumed.update(range(m.start(), m.end()))

    # --- Pass 3: standalone momentti refs (no § context in this fragment) ---
    for m in _SUBSEC_STANDALONE_RE.finditer(text):
        if m.start() in consumed:
            continue
        for part in re.split(r"\s*(?:,|ja)\s*", m.group(1)):
            part = part.strip()
            if part.isdigit():
                addresses.append(ParsedLegalAddress(subsection=int(part)))
        consumed.update(range(m.start(), m.end()))

    # --- Pass 4: chapter refs ("N luku") ---
    # Handle three shapes (most-specific first; track consumed spans):
    #   range:     "2–5 luku"
    #   list:      "2, 4 ja 5 luku"
    #   single:    "5 luku"
    consumed_ch: set[int] = set()

    # 4a. Ranges: "N–M luku"
    for m in _CH_RANGE_RE.finditer(text):
        if m.start() in consumed or m.start() in consumed_ch:
            continue
        consumed_ch.update(range(m.start(), m.end()))
        start_n, end_n = int(m.group(1)), int(m.group(2))
        if start_n <= end_n:
            for num in range(start_n, end_n + 1):
                addresses.append(ParsedLegalAddress(chapter=str(num)))

    # 4b. Comma/ja lists: "2, 4 ja 5 luku"
    for m in _CH_LIST_RE.finditer(text):
        if m.start() in consumed or m.start() in consumed_ch:
            continue
        consumed_ch.update(range(m.start(), m.end()))
        for token in re.split(r"\s*(?:,|ja)\s*", m.group(1)):
            norm = _norm_section(token)
            if norm:
                addresses.append(ParsedLegalAddress(chapter=norm))

    # 4c. Singles: "N luku" — only if not already consumed by range/list
    for m in _CH_SINGLE_RE.finditer(text):
        if m.start() in consumed or m.start() in consumed_ch:
            continue
        consumed_ch.update(range(m.start(), m.end()))
        norm = _norm_section(m.group(1))
        if norm:
            addresses.append(ParsedLegalAddress(chapter=norm))

    return addresses


def parse_leading_structural_address_path(text: str) -> List[tuple[str, str]]:
    """Best-effort parse of the leading structural address in raw statute text.

    This is a narrow recovery helper for Finland bodies that still arrive as
    flat `hcontainer` text rather than already-addressable structural nodes.
    It looks only at the leading heading region and returns the first
    structural path it can identify.
    """
    prefix = " ".join((text or "").split())
    if not prefix:
        return []

    window = prefix[:240]

    section_m = re.search(r"(\d+[a-z]?)\s*§", window, re.IGNORECASE)
    if section_m is not None:
        leading = window[: section_m.start()]
        path: List[tuple[str, str]] = []

        part_m = list(
            re.finditer(r"([IVXLCM]+|\d+(?:\s*[a-z])?)\s+(?:osa|osasto)\b", leading, re.IGNORECASE)
        )
        if part_m:
            path.append(("part", _norm_section(part_m[-1].group(1))))

        chapter_m = list(re.finditer(r"([IVXLCM]+|\d+(?:\s*[a-z])?)\s+luku\b", leading, re.IGNORECASE))
        if chapter_m:
            path.append(("chapter", _norm_section(chapter_m[-1].group(1))))

        path.append(("section", _norm_section(section_m.group(1))))
        return path

    # Chapter/part-only bodies are rare, but keep them recoverable too.
    part_m = list(
        re.finditer(r"([IVXLCM]+|\d+(?:\s*[a-z])?)\s+(?:osa|osasto)\b", window, re.IGNORECASE)
    )
    if part_m:
        return [("part", _norm_section(part_m[-1].group(1)))]

    chapter_m = list(re.finditer(r"([IVXLCM]+|\d+(?:\s*[a-z])?)\s+luku\b", window, re.IGNORECASE))
    if chapter_m:
        return [("chapter", _norm_section(chapter_m[-1].group(1)))]

    return []

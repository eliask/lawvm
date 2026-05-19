"""Kumotaan (repeal) extraction helpers for Finnish johtolause text.

Extracted from grafter.py — pure regex functions on johtolause strings.
No lxml, no corpus access, no grafter state.
"""
from __future__ import annotations

import functools
import re
from typing import Dict, List, Optional, Set

from lawvm.core.payload_surface import TargetUnitKind
from lawvm.finland.helpers import _expand_section_range


def _strip_source_provenance_tail(kumotaan_text: str) -> str:
    """Drop trailing ``sellaisena/sellaisina kuin ...`` provenance citations.

    Kumotaan clauses often append source-history qualifiers like
    ``sellaisina kuin ne ovat ... asetuksessa 1282/2000``. Those extra
    statute references do not change the repeal targets; they only identify
    the amendment source of the current wording. Strip that tail before
    applying the multi-statute guard and extracting targets.
    """
    return re.split(r",\s*sellais[a-zäöå\s]*kuin\b", kumotaan_text, maxsplit=1, flags=re.I)[0]


@functools.lru_cache(maxsize=8192)
def _expand_kumotaan_section_range_tuple(start: str, end: str) -> tuple[str, ...]:
    """Expand a repeal section range, including same-base letter suffix ranges.

    ``helpers._expand_section_range()`` already handles pure numeric ranges.
    ``kumotaan`` clauses also use same-base letter ranges such as ``10 d–10 i``;
    those should expand to each lettered section individually so the repeal
    extractor can suppress every target in the range.
    """
    start_norm = re.sub(r"\s+", "", start).lower()
    end_norm = re.sub(r"\s+", "", end).lower()

    expanded = tuple(_expand_section_range(f"{start_norm}-{end_norm}"))
    if len(expanded) != 1 or expanded[0] != f"{start_norm}-{end_norm}":
        return expanded

    m_start = re.fullmatch(r"(\d+)([a-z])?", start_norm, flags=re.I)
    m_end = re.fullmatch(r"(\d+)([a-z])?", end_norm, flags=re.I)
    if not m_start or not m_end:
        return expanded
    if m_start.group(1) != m_end.group(1):
        return expanded
    if not m_start.group(2) or not m_end.group(2):
        return expanded
    if m_start.group(2) > m_end.group(2):
        return expanded

    base = m_start.group(1)
    return tuple(f"{base}{chr(code)}" for code in range(ord(m_start.group(2)), ord(m_end.group(2)) + 1))


def _expand_kumotaan_section_range(start: str, end: str) -> List[str]:
    """Expand a repeal section range, including same-base letter suffix ranges."""
    return list(_expand_kumotaan_section_range_tuple(start, end))


def _extract_muutetaan_section_refs(johto: str) -> Set[str]:
    """Extract whole-section labels from the muutetaan clause of a johtolause."""
    return set(_extract_muutetaan_section_refs_frozenset(johto))


@functools.lru_cache(maxsize=8192)
def _extract_muutetaan_section_refs_frozenset(johto: str) -> frozenset[str]:
    """Extract whole-section labels from the muutetaan clause of a johtolause.

    Used to detect the "recycle-and-rename" pattern where the same section
    number appears in BOTH the kumotaan clause (repealing the old text) AND
    the muutetaan clause (introducing new text under the same number).  In
    that case the muutetaan wins: the section should NOT be treated as a
    permanent repeal.

    Returns a set of normalised section labels (e.g. {'44', '42', '41'}).
    Only extracts whole-section targets; momentti/kohta-level refs are ignored.
    """
    text = johto.lower()
    # Find muutetaan clause — stops at seuraavasti, kumotaan or lisätään.
    # Critically, lisätään must be a stop word so that section numbers from
    # the lisätään clause (e.g. "lisätään 1 luvun 4 §:ään") are not falsely
    # detected as muutetaan targets — which would trigger the recycle guard
    # and prevent kumotaan expiry override for those section numbers.
    muutetaan_match = re.search(
        r'\bmuutetaan\b(.*?)(?:seuraavasti\b|\blisätään\b|$)',
        text, re.DOTALL
    )
    if not muutetaan_match:
        return frozenset()

    muutetaan_text = _strip_source_provenance_tail(muutetaan_match.group(1))

    # Guard: multi-statute muutetaan clauses reference sections from different
    # statutes — skip to avoid false positives.
    statute_refs = re.findall(r'\d+/\d{2,4}', muutetaan_text)
    if len(set(statute_refs)) > 1 and muutetaan_text.count("§") > 1:
        return frozenset()

    labels: Set[str] = set()
    # Match whole-section refs: N §, N a §, range N–M § — skip momentti refs
    for m in re.finditer(
        r'(\d+(?:\s*[a-z])?)\s*(?:[–—―\-]\s*(\d+(?:\s*[a-z])?))?'
        r'\s*§(?!:)',
        muutetaan_text,
    ):
        start = re.sub(r'\s+', '', m.group(1).strip())
        end = m.group(2)
        if end:
            end = re.sub(r'\s+', '', end.strip())
            from lawvm.finland.helpers import _expand_section_range
            for expanded in _expand_section_range(f"{start}-{end}"):
                labels.add(expanded)
        else:
            labels.add(start)
    return frozenset(labels)


def _extract_muutetaan_chapter_section_map(johto: str) -> Dict[Optional[str], List[str]]:
    """Extract section-level refs with chapter context from muutetaan clauses.

    Returns a dict mapping chapter labels to whole-section refs in the muutetaan
    clause.  Used for chapter-aware recycle guard to prevent false-positive matches
    when the same section number appears in different chapters across kumotaan and
    muutetaan.

    Returns {None: [sections]} for global (non-chapter-scoped) refs,
    or an empty dict if the clause cannot be parsed.

    Example: 'muutetaan lain 6 luvun 4 §, 5 luvun 7 §'
    Returns: {'6': ['4'], '5': ['7']}
    """
    text = johto.lower()
    muutetaan_match = re.search(
        r'\bmuutetaan\b(.*?)(?:seuraavasti\b|\blisätään\b|$)',
        text, re.DOTALL,
    )
    if not muutetaan_match:
        return {}

    muutetaan_text = _strip_source_provenance_tail(muutetaan_match.group(1))

    # Guard: multi-statute muutetaan clauses reference sections from different statutes
    statute_refs = re.findall(r'\d+/\d{2,4}', muutetaan_text)
    if len(set(statute_refs)) > 1 and muutetaan_text.count("§") > 1:
        return {}

    # Find chapter markers: "N luvun" or "N a luvun"
    chapter_marker_re = re.compile(r'(\d+(?:\s*[a-z])?)\s+luvun\b')
    markers = list(chapter_marker_re.finditer(muutetaan_text))

    if not markers:
        # No chapter markers — fall back to global extraction
        global_sections = _extract_sections_from_block(muutetaan_text)
        return {None: global_sections} if global_sections else {}

    # Split text into chapter-scoped blocks
    blocks: List[tuple[Optional[str], str]] = []
    if markers[0].start() > 0:
        preamble = muutetaan_text[:markers[0].start()]
        blocks.append((None, preamble))
    for i, m in enumerate(markers):
        chapter_label = re.sub(r'\s+', '', m.group(1).strip())
        block_start = m.end()
        block_end = markers[i + 1].start() if i + 1 < len(markers) else len(muutetaan_text)
        blocks.append((chapter_label, muutetaan_text[block_start:block_end]))

    result: Dict[Optional[str], List[str]] = {}
    for chapter_label, block_text in blocks:
        sections = _extract_sections_from_block(block_text)
        if sections:
            existing = result.setdefault(chapter_label, [])
            seen = set(existing)
            for s in sections:
                if s not in seen:
                    existing.append(s)
                    seen.add(s)

    return result


def _extract_kumotaan_section_refs(johto: str) -> List[str]:
    """Extract section-level repeal references from kumotaan clauses."""
    return list(_extract_kumotaan_section_refs_tuple(johto))


@functools.lru_cache(maxsize=8192)
def _extract_kumotaan_section_refs_tuple(johto: str) -> tuple[str, ...]:
    """Extract section-level repeal references from kumotaan clauses.

    Catches kumotaan section references that the PEG parser might miss,
    especially in complex multi-verb johtolause. Only extracts whole-section
    repeals (not subsection/momentti-level).

    Example: 'kumotaan lain (123/2000) 5 §, 7–9 § ja 12 a §'
    Returns: ['5', '7', '8', '9', '12a']
    """
    text = johto.lower()
    # Find kumotaan clause boundary — stops at muutetaan/lisätään/seuraavasti
    kumotaan_match = re.search(
        r'kumotaan\b(.*?)(?:muutetaan|lisätään|seuraavasti|sekä\s+muutetaan|sekä\s+lisätään|$)',
        text, re.DOTALL
    )
    if not kumotaan_match:
        return ()

    full_body = kumotaan_match.group(1)
    kumotaan_text = _strip_source_provenance_tail(full_body)

    # Guard: skip multi-statute kumotaan clauses (e.g. "Tällä lailla
    # kumotaan: 1) lain (610/1986) 16 §; 2) lain (386/1995) 7 §").
    # These reference sections from different statutes — section numbers
    # would be applied to the wrong master. Detect by counting distinct
    # statute references (NNN/YYYY or NNN/YY patterns).
    statute_refs = re.findall(r'\d+/\d{2,4}', kumotaan_text)
    if len(set(statute_refs)) > 1 and kumotaan_text.count("§") > 1:
        return ()

    def _sections_from_block(block: str) -> List[str]:
        """Extract whole-section refs from a single already-stripped block."""
        result: List[str] = []
        # Multi-token: "N, M ja K §"
        for m in re.finditer(
            r'((?:'
            r'\d+(?:\s*[a-z])?'
            r'(?:\s*[–—―\-]\s*\d+(?:\s*[a-z])?)?'
            r'\s*(?:,|ja)\s*'
            r')+'
            r'\d+(?:\s*[a-z])?'
            r'(?:\s*[–—―\-]\s*\d+(?:\s*[a-z])?)?'
            r')\s*§(?!:)',
            block,
        ):
            for token in re.split(r'\s*(?:,|ja)\s*', m.group(1)):
                norm = re.sub(r'\s+', '', token.strip())
                if norm:
                    range_match = re.fullmatch(
                        r'(\d+(?:[a-z])?)[–—―\-](\d+(?:[a-z])?)',
                        norm,
                        flags=re.I,
                    )
                    if range_match:
                        result.extend(
                            _expand_kumotaan_section_range(
                                range_match.group(1), range_match.group(2)
                            )
                        )
                    else:
                        result.append(norm)
        # Single-token: "N §" or "N a §"
        for m in re.finditer(
            r'(\d+(?:\s*[a-z])?)\s*(?:[–—―\-]\s*(\d+(?:\s*[a-z])?))?'
            r'\s*§(?!:)',
            block,
        ):
            start = re.sub(r'\s+', '', m.group(1).strip())
            end = m.group(2)
            if end:
                end = re.sub(r'\s+', '', end.strip())
                result.extend(_expand_kumotaan_section_range(start, end))
            else:
                result.append(start)
        return result

    # Extract WHOLE-SECTION references only: N §, N a §, N–M §, N ja M §
    # Skip references followed by ":n" (subsection qualifier like "16 §:n 3 momentti")
    # — those target subsections, not whole sections.
    sections: List[str] = _sections_from_block(kumotaan_text)

    # Multi-item kumotaan lists: "1) text1, sellaisina kuin ...; sekä 2) text2, ..."
    # The provenance strip only removes the tail of the first item, losing
    # any continuation items that appear after it.  Scan the full body for
    # "; (sekä) N)" markers and extract from each continuation separately.
    for cont_m in re.finditer(
        r';\s*(?:sekä\s+)?\d+\)\s*(.*?)(?=;\s*(?:sekä\s+)?\d+\)|\Z)',
        full_body,
        re.DOTALL | re.I,
    ):
        cont_text = _strip_source_provenance_tail(cont_m.group(1))
        # Only process if no multi-statute ambiguity
        cont_refs = re.findall(r'\d+/\d{2,4}', cont_text)
        if len(set(cont_refs)) > 1 and cont_text.count("§") > 1:
            continue
        sections.extend(_sections_from_block(cont_text))

    deduped: List[str] = []
    seen: Set[str] = set()
    for sec in sections:
        if sec not in seen:
            deduped.append(sec)
            seen.add(sec)

    return tuple(deduped)


def _extract_kumotaan_chapter_section_map(johto: str) -> Dict[Optional[str], List[str]]:
    """Extract section-level repeal refs with chapter context from kumotaan clauses.

    When the kumotaan clause is chapter-scoped (e.g. "1 luvun 5 §, 2 luvun 11 §"),
    returns a dict mapping each chapter label to its fully-repealed sections.
    Returns {None: [sections]} for global (non-chapter-scoped) repeals,
    or an empty dict if the clause cannot be parsed.

    This companion to _extract_kumotaan_section_refs exists to prevent cross-chapter
    contamination when the same section number is fully repealed in one chapter but
    only partially repealed (momentti/kohta level) in another.

    Example: '1 luvun 5 §, 7 § ... 5 luvun 2—4 §'
    Returns: {'1': ['5', '7'], '5': ['2', '3', '4']}
    """
    text = johto.lower()
    kumotaan_match = re.search(
        r'kumotaan\b(.*?)(?:muutetaan|lisätään|seuraavasti|sekä\s+muutetaan|sekä\s+lisätään|$)',
        text, re.DOTALL
    )
    if not kumotaan_match:
        return {}

    kumotaan_text = _strip_source_provenance_tail(kumotaan_match.group(1))

    statute_refs = re.findall(r'\d+/\d{2,4}', kumotaan_text)
    if len(set(statute_refs)) > 1 and kumotaan_text.count("§") > 1:
        return {}

    # Find chapter markers: "N luvun" or "N a luvun" etc.
    chapter_marker_re = re.compile(r'(\d+(?:\s*[a-z])?)\s+luvun\b')
    markers = list(chapter_marker_re.finditer(kumotaan_text))

    if not markers:
        # No chapter markers — fall back to global extraction
        global_sections = _extract_kumotaan_section_refs(johto)
        return {None: global_sections} if global_sections else {}

    # Split text into chapter-scoped blocks
    blocks: List[tuple[Optional[str], str]] = []
    # Text before the first chapter marker (global context)
    if markers[0].start() > 0:
        preamble = kumotaan_text[:markers[0].start()]
        blocks.append((None, preamble))
    for i, m in enumerate(markers):
        chapter_label = re.sub(r'\s+', '', m.group(1).strip())
        block_start = m.end()
        block_end = markers[i + 1].start() if i + 1 < len(markers) else len(kumotaan_text)
        blocks.append((chapter_label, kumotaan_text[block_start:block_end]))

    result: Dict[Optional[str], List[str]] = {}
    for chapter_label, block_text in blocks:
        sections = _extract_sections_from_block(block_text)
        if sections:
            existing = result.setdefault(chapter_label, [])
            seen = set(existing)
            for s in sections:
                if s not in seen:
                    existing.append(s)
                    seen.add(s)

    return result


def _extract_sections_from_block(block_text: str) -> List[str]:
    """Extract whole-section repeal labels from a single chapter block of kumotaan text."""
    return list(_extract_sections_from_block_tuple(block_text))


@functools.lru_cache(maxsize=8192)
def _extract_sections_from_block_tuple(block_text: str) -> tuple[str, ...]:
    """Extract whole-section repeal labels from a single chapter block of kumotaan text."""
    sections: List[str] = []
    for m in re.finditer(
        r'((?:'
        r'\d+(?:\s*[a-z])?'
        r'(?:\s*[–—―\-]\s*\d+(?:\s*[a-z])?)?'
        r'\s*(?:,|ja)\s*'
        r')+'
        r'\d+(?:\s*[a-z])?'
        r'(?:\s*[–—―\-]\s*\d+(?:\s*[a-z])?)?'
        r')\s*§(?!:)',
        block_text,
    ):
        for token in re.split(r'\s*(?:,|ja)\s*', m.group(1)):
            norm = re.sub(r'\s+', '', token.strip())
            if norm:
                range_match = re.fullmatch(
                    r'(\d+(?:[a-z])?)[–—―\-](\d+(?:[a-z])?)',
                    norm, flags=re.I,
                )
                if range_match:
                    for expanded in _expand_kumotaan_section_range(
                        range_match.group(1), range_match.group(2)
                    ):
                        sections.append(expanded)
                else:
                    sections.append(norm)
    for m in re.finditer(
        r'(\d+(?:\s*[a-z])?)\s*(?:[–—―\-]\s*(\d+(?:\s*[a-z])?))?'
        r'\s*§(?!:)',
        block_text
    ):
        start = re.sub(r'\s+', '', m.group(1).strip())
        end = m.group(2)
        if end:
            end = re.sub(r'\s+', '', end.strip())
            for expanded in _expand_kumotaan_section_range(start, end):
                sections.append(expanded)
        else:
            sections.append(start)

    deduped: List[str] = []
    seen: Set[str] = set()
    for sec in sections:
        if sec not in seen:
            deduped.append(sec)
            seen.add(sec)
    return tuple(deduped)


def _extract_kumotaan_subsection_refs(johto: str) -> Dict[str, List[str]]:
    """Extract subsection-level repeal refs from kumotaan clauses.

    Handles the pattern "N §:n M momentti" and "N §:n M–P momentti" and
    "N §:n M ja P momentti" where specific subsection numbers are repealed
    without replacing the whole section.

    Example: 'kumotaan ... (324/1959) 9 §:n 2–5 momentti'
    Returns: {'9': ['2', '3', '4', '5']}

    Example: 'kumotaan ... 26 §:n 2 ja 3 momentti'
    Returns: {'26': ['2', '3']}

    Deliberately skips deeper-level refs like "§:n M momentin N kohta" to
    avoid false positives on item-level repeals (those are handled by the PEG
    parser).

    Multi-statute kumotaan clauses (referencing more than one parent statute)
    are skipped because subsection numbers could belong to different parents.
    """
    text = johto.lower()
    kumotaan_match = re.search(
        r'kumotaan\b(.*?)(?:muutetaan|lisätään|seuraavasti|sekä\s+muutetaan|sekä\s+lisätään|$)',
        text, re.DOTALL
    )
    if not kumotaan_match:
        return {}

    full_body = kumotaan_match.group(1)
    kumotaan_text = _strip_source_provenance_tail(full_body)

    statute_refs = re.findall(r'\d+/\d{2,4}', kumotaan_text)
    if len(set(statute_refs)) > 1:
        return {}

    result: Dict[str, List[str]] = {}

    def _expand_subsection_list(raw: str) -> List[str]:
        """Expand a subsection list like '2–5' or '2 ja 3' into label list."""
        labels: List[str] = []
        # Split on ja/sekä/comma separators
        for part in re.split(r'\s*(?:,|ja|sekä)\s*', raw.strip()):
            part = part.strip()
            if not part:
                continue
            # Check for en-dash or em-dash range
            range_m = re.fullmatch(r'(\d+)\s*[–—―\-]\s*(\d+)', part)
            if range_m:
                start_n = int(range_m.group(1))
                end_n = int(range_m.group(2))
                if 0 < start_n <= end_n <= 30:
                    labels.extend(str(n) for n in range(start_n, end_n + 1))
            else:
                norm = part.strip()
                if re.fullmatch(r'\d+', norm):
                    labels.append(norm)
        return labels

    # Pattern: "N §:n M–P momentti" or "N §:n M momentti" or "N §:n M ja P momentti"
    # Skip "N §:n M momentin K kohta" (deeper level — has 'momentin' + number after)
    # The section number is \d+\s*[a-z]? (possibly lettered like "12 a")
    for m in re.finditer(
        r'(\d+(?:\s*[a-z])?)\s*§:n\s+'
        r'([\d\s,–—―\-]+'                   # subsection list (numbers/ranges/commas/ja/sekä)
        r'(?:\s*(?:ja|sekä)\s*[\d\s,–—―\-]+)*)'  # continuations with ja/sekä
        r'\s*momentti(?!\s*n\b)',            # "momentti" NOT followed by "n" (momentin = genitive → deeper)
        kumotaan_text,
        re.I,
    ):
        sec_label = re.sub(r'\s+', '', m.group(1)).lower()
        sub_raw = m.group(2)
        sub_labels = _expand_subsection_list(sub_raw)
        if sec_label and sub_labels:
            if sec_label not in result:
                result[sec_label] = []
            for lbl in sub_labels:
                if lbl not in result[sec_label]:
                    result[sec_label].append(lbl)

    return result


def _extract_kumotaan_container_refs(johto: str) -> Dict[TargetUnitKind, List[str]]:
    """Extract whole-container repeal refs from kumotaan clauses.

    Supports simple chapter/part references such as `2 a luku` and `3 osa`.
    These are needed especially for generic-preamble sec_1 repeal acts where
    the operative effect is encoded as prose rather than PEG-friendly ops.
    """
    text = johto.lower()
    kumotaan_match = re.search(
        r'kumotaan\b(.*?)(?:muutetaan|lisätään|seuraavasti|sekä\s+muutetaan|sekä\s+lisätään|$)',
        text, re.DOTALL
    )
    if not kumotaan_match:
        return {"chapter": [], "part": []}

    kumotaan_text = _strip_source_provenance_tail(kumotaan_match.group(1))
    statute_refs = re.findall(r'\d+/\d{2,4}', kumotaan_text)
    if len(set(statute_refs)) > 1:
        return {"chapter": [], "part": []}

    out: Dict[TargetUnitKind, List[str]] = {"chapter": [], "part": []}
    for kind, suffix in (("chapter", "luku"), ("part", "osa")):
        seen: Set[str] = set()
        vals: List[str] = []
        for m in re.finditer(r'(\d+(?:\s*[a-z])?)\s+' + suffix + r'\b', kumotaan_text):
            norm = re.sub(r'\s+', '', m.group(1).strip())
            if norm and norm not in seen:
                vals.append(norm)
                seen.add(norm)
        out[kind] = vals
    return out

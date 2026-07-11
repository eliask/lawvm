"""Kumotaan (repeal) extraction helpers for Finnish johtolause text.

Extracted from grafter.py — pure functions on johtolause strings.
No lxml, no corpus access, no grafter state.

The whole-section enumeration of a repeal/amend block (section / range /
coordination / letter-suffix) is parsed by the shared johtolause grammar
(``references.sections.parse_body_provision_tail``), NOT by a parallel regex.
Regex survives here only as (a) clause-boundary / multi-statute / provenance
ANCHORS and (b) the ``§(?!:)`` whole-section SITE ANCHOR that delimits each
candidate run before handing its structure to the grammar — both the allowed
lexer-primitive floor (a cheap site anchor that hands the structural tail to a
real recognizer), never the structural parser.
"""
from __future__ import annotations

import functools
import re
from lawvm.core.regex_safety import compile_classifier_regex
from dataclasses import dataclass
from typing import Dict, List, Optional, Set

from lawvm.core.payload_surface import TargetUnitKind
from lawvm.finland.provenance_tail import strip_source_provenance_tail
from lawvm.finland.references.sections import (
    parse_body_provision_tail_spanned,
)


_CHAPTER_MARKER_RE = compile_classifier_regex(r'(\d{1,4}\s{1,4}[a-z]|\d{1,4})\s{1,8}luvun\b', classifier_id="fi.kumotaan.chapter_marker_re")


# Whole-section SITE ANCHOR (scaffold, NOT a structural parser): a bare ``§``
# that is NOT colon-qualified (``§(?!:)``). The candidate run immediately before
# the marker is recovered by a bounded scanner and validated by the shared
# grammar below.
# The trailing ``§:`` exclusion is the whole-section discriminator the grammar
# does not yet replicate — ``N §:n M momentti`` / ``N §:n edellä oleva
# väliotsikko`` are sub-provision/heading repeals, never whole-section repeals.
# The anchor only delimits the run SURFACE; its section/range/coordination/
# letter-suffix STRUCTURE is enumerated by the grammar (parse_body_provision_tail).
_WHOLE_SECTION_SITE_RE = compile_classifier_regex(r"§(?!:)", classifier_id="fi.kumotaan.whole_section_site_re")
_WHOLE_SECTION_SITE_SCAN_WINDOW = 240
_WS_RE = re.compile(r"\s+")

# De-glue coordination joiners fused to a following digit (``ja18`` → ``ja 18``)
# and a letter-suffix fused to ``ja`` + digit (``16 aja 18`` → ``16 a ja 18``),
# both attested source typos. This is a SITE-SCAN NORMALIZATION (anchor floor),
# mirroring ``freetext_addresses._GLUED_JOINER_DIGIT_RE``; structure is still
# parsed by the grammar over the de-glued tokens.
_GLUED_JOINER_DIGIT_RE = re.compile(r'\b(ja|sekä|ynnä|tai)(?=\d)', re.IGNORECASE)
_GLUED_LETTER_JOINER_RE = re.compile(r'([a-zäöå])ja(?=\s*\d)', re.IGNORECASE)


def _deglue_run(run: str) -> str:
    return _GLUED_LETTER_JOINER_RE.sub(r'\1 ja ', _GLUED_JOINER_DIGIT_RE.sub(r'\1 ', run))


def _whole_section_run_before_site(block: str, site_start: int) -> tuple[str, int] | None:
    """Return the grammar-consumed candidate run before a bare ``§`` marker."""
    window_start = max(0, site_start - _WHOLE_SECTION_SITE_SCAN_WINDOW)
    left = block[window_start:site_start].rstrip()
    for offset, ch in enumerate(left):
        if not ch.isdigit():
            continue
        candidate = left[offset:].strip()
        if "§" in candidate:
            continue
        tail = _deglue_run(candidate + " §")
        normalized_tail = _WS_RE.sub(" ", tail).strip()
        parsed = parse_body_provision_tail_spanned(tail)
        if parsed.targets and parsed.consumed_text == normalized_tail:
            return candidate, window_start + offset
    return None


def _grammar_whole_section_labels(block: str) -> List[str]:
    """Whole-section repeal labels in a block, enumerated by the shared grammar.

    Replaces the parallel regex section enumerator: a ``§(?!:)`` site anchor
    delimits each candidate whole-section run, and the grammar
    (:func:`...references.sections.parse_body_provision_tail`) expands its
    section / range / coordination / letter-suffix structure. Only WHOLE-section
    targets are kept (``subsection_num``/``item_label``/``subitem_label`` all
    ``None``) — momentti/kohta/alakohta precision is the grammar's job to model
    and is excluded here exactly as the regex's ``§(?!:)`` did.

    Order-preserving and de-duplicated, matching the prior regex contract.
    """
    out: List[str] = []
    seen: Set[str] = set()
    # lawvm-regex: owning_parser whole-section SITE anchor; section STRUCTURE handed to the shared references.sections grammar
    for m in _WHOLE_SECTION_SITE_RE.finditer(block):
        site_match = _whole_section_run_before_site(block, m.start())
        if site_match is None:
            continue
        run, run_start = site_match
        current_site = parse_body_provision_tail_spanned(_deglue_run(block[run_start:]))
        if current_site.targets:
            first = current_site.targets[0]
            if (
                first.section_label
                and (first.subsection_num is not None or first.item_label is not None or first.subitem_label is not None)
            ):
                continue
        parsed = parse_body_provision_tail_spanned(_deglue_run(run + " §"))
        for target in parsed.targets:
            if (
                target.subsection_num is None
                and target.item_label is None
                and target.subitem_label is None
                and target.section_label
            ):
                if target.section_label not in seen:
                    seen.add(target.section_label)
                    out.append(target.section_label)
    return out


@dataclass(frozen=True)
class KumotaanRecycleGuardResult:
    """Typed witness for kumotaan-vs-muutetaan recycle suppression.

    ``filtered_labels`` preserves the historical executable behavior: labels
    appearing in both the repeal and replacement surfaces are excluded from the
    later expiry override so the replacement text is not born-expired.
    """

    original_labels: tuple[str, ...]
    filtered_labels: tuple[str, ...]
    recycled_labels: tuple[str, ...]
    chapter_aware: bool
    kumotaan_chapter_map: tuple[tuple[str | None, tuple[str, ...]], ...]
    muutetaan_chapter_map: tuple[tuple[str | None, tuple[str, ...]], ...]

    @property
    def fired(self) -> bool:
        return bool(self.recycled_labels)

    def finding_detail(self) -> dict[str, object]:
        return {
            "rule_id": "fi_kumotaan_muutetaan_recycle_guard",
            "original_kumotaan_labels": self.original_labels,
            "filtered_kumotaan_labels": self.filtered_labels,
            "recycled_labels": self.recycled_labels,
            "chapter_aware": self.chapter_aware,
            "kumotaan_chapter_map": self.kumotaan_chapter_map,
            "muutetaan_chapter_map": self.muutetaan_chapter_map,
        }


@dataclass(frozen=True, slots=True)
class KumotaanItemTarget:
    """Typed item-level target extracted from a pure ``kumotaan`` clause."""

    section_label: str
    subsection_label: str | None
    item_label: str
    chapter_label: str | None = None


def _freeze_chapter_section_map(
    mapping: Dict[Optional[str], List[str]],
) -> tuple[tuple[str | None, tuple[str, ...]], ...]:
    def _sort_key(item: tuple[str | None, List[str]]) -> tuple[int, str]:
        chapter, _ = item
        return (0, "") if chapter is None else (1, chapter)

    return tuple(
        (chapter, tuple(sections))
        for chapter, sections in sorted(mapping.items(), key=_sort_key)
    )


def kumotaan_recycle_guard_result(johto: str) -> KumotaanRecycleGuardResult:
    """Return the owned recycle-guard decision for a kumotaan clause."""
    kumotaan_labels = tuple(_extract_kumotaan_section_refs(johto))
    kumotaan_chapter_map = _extract_kumotaan_chapter_section_map(johto)
    muutetaan_chapter_map: Dict[Optional[str], List[str]] = {}
    recycled: Set[str] = set()
    chapter_aware = False

    if kumotaan_labels:
        muutetaan_chapter_map = _extract_muutetaan_chapter_section_map(johto)
        kum_has_chapters = bool(
            kumotaan_chapter_map and any(k is not None for k in kumotaan_chapter_map)
        )
        mut_has_chapters = bool(
            muutetaan_chapter_map and any(k is not None for k in muutetaan_chapter_map)
        )
        chapter_aware = kum_has_chapters and mut_has_chapters
        if chapter_aware:
            for chapter, kum_sections in kumotaan_chapter_map.items():
                mut_sections = {
                    section.lower()
                    for section in muutetaan_chapter_map.get(chapter, [])
                }
                for section in kum_sections:
                    if section.lower() in mut_sections:
                        recycled.add(section)
        else:
            muutetaan_sections = _extract_muutetaan_section_refs(johto)
            recycled = {
                label
                for label in kumotaan_labels
                if label.lower() in muutetaan_sections
            }

    filtered = tuple(
        label
        for label in kumotaan_labels
        if label not in recycled
    )
    return KumotaanRecycleGuardResult(
        original_labels=kumotaan_labels,
        filtered_labels=filtered,
        recycled_labels=tuple(sorted(recycled)),
        chapter_aware=chapter_aware,
        kumotaan_chapter_map=_freeze_chapter_section_map(kumotaan_chapter_map),
        muutetaan_chapter_map=_freeze_chapter_section_map(muutetaan_chapter_map),
    )


# The provenance-tail stripper is the shared FI primitive (canonical home:
# ``finland.provenance_tail``). Aliased under the historical private name so the
# eight call sites below are unchanged.
_strip_source_provenance_tail = strip_source_provenance_tail


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
    # lawvm-regex: owning_parser clause-boundary segmenter for the muutetaan sub-clause; structure delegated to the grammar
    muutetaan_match = re.search(
        r'\bmuutetaan\b(.*?)(?:seuraavasti\b|\blisätään\b|$)',
        text, re.DOTALL
    )
    if not muutetaan_match:
        return frozenset()

    muutetaan_text = _strip_source_provenance_tail(muutetaan_match.group(1))

    # Guard: multi-statute muutetaan clauses reference sections from different
    # statutes — skip to avoid false positives.
    # lawvm-regex: prefilter multi-statute GUARD (counts distinct statute ids to bail on ambiguity); not a producer
    statute_refs = re.findall(r'\d+/\d{2,4}', muutetaan_text)
    if len(set(statute_refs)) > 1 and muutetaan_text.count("§") > 1:
        return frozenset()

    # Whole-section refs (N §, N a §, range N–M §, coordinated N, M ja K §) —
    # enumerated by the grammar; momentti/kohta refs (``§:n …``) are excluded by
    # the ``§(?!:)`` site anchor / the grammar's sub-provision modeling.
    return frozenset(_grammar_whole_section_labels(muutetaan_text))


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
    # lawvm-regex: owning_parser clause-boundary segmenter for the muutetaan sub-clause
    muutetaan_match = re.search(
        r'\bmuutetaan\b(.*?)(?:seuraavasti\b|\blisätään\b|$)',
        text, re.DOTALL,
    )
    if not muutetaan_match:
        return {}

    muutetaan_text = _strip_source_provenance_tail(muutetaan_match.group(1))

    # Guard: multi-statute muutetaan clauses reference sections from different statutes
    # lawvm-regex: prefilter multi-statute GUARD; counts distinct statute ids, not a producer
    statute_refs = re.findall(r'\d+/\d{2,4}', muutetaan_text)
    if len(set(statute_refs)) > 1 and muutetaan_text.count("§") > 1:
        return {}

    # Find chapter markers: "N luvun" or "N a luvun"
    # lawvm-regex: owning_parser chapter-scope marker for the chapter-aware recycle map
    markers = list(_CHAPTER_MARKER_RE.finditer(muutetaan_text))

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
    # lawvm-regex: owning_parser clause-boundary segmenter for the kumotaan sub-clause
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
    # lawvm-regex: prefilter multi-statute GUARD; counts distinct statute ids, not a producer
    statute_refs = re.findall(r'\d+/\d{2,4}', kumotaan_text)
    if len(set(statute_refs)) > 1 and kumotaan_text.count("§") > 1:
        return ()

    # Extract WHOLE-SECTION references only: N §, N a §, N–M §, N ja M §.
    # The grammar enumerates section/range/coordination/letter-suffix structure;
    # the ``§(?!:)`` site anchor skips ``§:n``-qualified (subsection/heading)
    # refs exactly as the prior regex did.
    sections: List[str] = _grammar_whole_section_labels(kumotaan_text)

    # Multi-item kumotaan lists: "1) text1, sellaisina kuin ...; sekä 2) text2, ..."
    # The provenance strip only removes the tail of the first item, losing
    # any continuation items that appear after it.  Scan the full body for
    # "; (sekä) N)" markers and extract from each continuation separately.
    # lawvm-regex: owning_parser multi-item kumotaan list segmenter (`1) ...; sekä 2) ...`)
    for cont_m in re.finditer(
        r';\s*(?:sekä\s+)?\d+\)\s*(.*?)(?=;\s*(?:sekä\s+)?\d+\)|\Z)',
        full_body,
        re.DOTALL | re.I,
    ):
        cont_text = _strip_source_provenance_tail(cont_m.group(1))
        # Only process if no multi-statute ambiguity
        # lawvm-regex: prefilter per-item multi-statute GUARD; counts distinct statute ids, not a producer
        cont_refs = re.findall(r'\d+/\d{2,4}', cont_text)
        if len(set(cont_refs)) > 1 and cont_text.count("§") > 1:
            continue
        sections.extend(_grammar_whole_section_labels(cont_text))

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
    # lawvm-regex: owning_parser clause-boundary segmenter for the kumotaan sub-clause
    kumotaan_match = re.search(
        r'kumotaan\b(.*?)(?:muutetaan|lisätään|seuraavasti|sekä\s+muutetaan|sekä\s+lisätään|$)',
        text, re.DOTALL
    )
    if not kumotaan_match:
        return {}

    kumotaan_text = _strip_source_provenance_tail(kumotaan_match.group(1))

    # lawvm-regex: prefilter multi-statute GUARD; counts distinct statute ids, not a producer
    statute_refs = re.findall(r'\d+/\d{2,4}', kumotaan_text)
    if len(set(statute_refs)) > 1 and kumotaan_text.count("§") > 1:
        return {}

    # Find chapter markers: "N luvun" or "N a luvun" etc.
    # lawvm-regex: owning_parser chapter-scope marker for the chapter-aware recycle map
    markers = list(_CHAPTER_MARKER_RE.finditer(kumotaan_text))

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
    """Extract whole-section repeal labels from a single chapter block of kumotaan text.

    Section/range/coordination/letter-suffix structure is enumerated by the
    shared grammar (:func:`_grammar_whole_section_labels`); the ``§(?!:)`` site
    anchor scopes the block to whole-section (not ``§:n``-qualified) sites.
    """
    return tuple(_grammar_whole_section_labels(block_text))


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
    # lawvm-regex: owning_parser clause-boundary segmenter for the kumotaan sub-clause
    kumotaan_match = re.search(
        r'kumotaan\b(.*?)(?:muutetaan|lisätään|seuraavasti|sekä\s+muutetaan|sekä\s+lisätään|$)',
        text, re.DOTALL
    )
    if not kumotaan_match:
        return {}

    full_body = kumotaan_match.group(1)
    kumotaan_text = _strip_source_provenance_tail(full_body)
    # Historical Finlex XML can preserve line-break hyphenation inside the unit
    # word itself: ``mo- mentti``. Normalize only that provision-unit artifact
    # before recognizing typed subsection targets.
    # lawvm-regex: owning_parser source-hyphenation normalizer for the momentti unit token
    kumotaan_text = re.sub(r'\bmo\s*-\s*mentti\b', 'momentti', kumotaan_text)

    # lawvm-regex: prefilter multi-statute GUARD; counts distinct statute ids, not a producer
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
    # lawvm-regex: owning_parser momentti-level repeal recognizer (`N §:n M momentti` sub-provision the whole-section anchor excludes)
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


def _extract_kumotaan_item_refs(johto: str) -> tuple[KumotaanItemTarget, ...]:
    """Extract item-level repeal refs from kumotaan clauses using the shared grammar.

    Handles pure clauses such as ``kumotaan ... 2 §:n 4 kohta`` and
    ``2 §:n 1 momentin 4 kohta``. The clause boundary is only a lexer-level
    window; target structure is delegated to ``references.sections`` so this
    function does not authorize legal targets from substring shape alone.
    """
    text = johto.lower()
    # lawvm-regex: owning_parser clause-boundary segmenter for the kumotaan sub-clause; target structure delegated to references.sections
    kumotaan_match = re.search(
        r'kumotaan\b(.*?)(?:muutetaan|lisätään|seuraavasti|sekä\s+muutetaan|sekä\s+lisätään|$)',
        text,
        re.DOTALL,
    )
    if not kumotaan_match:
        return ()

    full_body = kumotaan_match.group(1)
    kumotaan_text = _strip_source_provenance_tail(full_body)
    # lawvm-regex: prefilter multi-statute GUARD; counts distinct statute ids, not a producer
    statute_refs = re.findall(r'\d+/\d{2,4}', kumotaan_text)
    if len(set(statute_refs)) > 1:
        return ()

    targets: list[KumotaanItemTarget] = []
    seen: set[tuple[str | None, str, str | None, str]] = set()
    for offset, ch in enumerate(kumotaan_text):
        if not ch.isdigit():
            continue
        parsed = parse_body_provision_tail_spanned(_deglue_run(kumotaan_text[offset:]))
        if not parsed.targets:
            continue
        consumed = parsed.consumed_text.strip()
        if not consumed or "§" not in consumed:
            continue
        for target in parsed.targets:
            if not target.section_label or target.item_label is None:
                continue
            key = (
                target.chapter,
                target.section_label.lower(),
                str(target.subsection_num) if target.subsection_num is not None else None,
                target.item_label.lower(),
            )
            if key in seen:
                continue
            seen.add(key)
            targets.append(
                KumotaanItemTarget(
                    section_label=target.section_label.lower(),
                    subsection_label=str(target.subsection_num) if target.subsection_num is not None else None,
                    item_label=target.item_label.lower(),
                    chapter_label=target.chapter.lower() if target.chapter else None,
                )
            )
    return tuple(targets)


def _extract_kumotaan_container_refs(johto: str) -> Dict[TargetUnitKind, List[str]]:
    """Extract whole-container repeal refs from kumotaan clauses.

    Supports simple chapter/part references such as `2 a luku` and `3 osa`.
    These are needed especially for generic-preamble sec_1 repeal acts where
    the operative effect is encoded as prose rather than PEG-friendly ops.
    """
    text = johto.lower()
    # lawvm-regex: owning_parser clause-boundary segmenter for the kumotaan sub-clause
    kumotaan_match = re.search(
        r'kumotaan\b(.*?)(?:muutetaan|lisätään|seuraavasti|sekä\s+muutetaan|sekä\s+lisätään|$)',
        text, re.DOTALL
    )
    if not kumotaan_match:
        return {"chapter": [], "part": []}

    kumotaan_text = _strip_source_provenance_tail(kumotaan_match.group(1))
    # lawvm-regex: prefilter multi-statute GUARD; counts distinct statute ids, not a producer
    statute_refs = re.findall(r'\d+/\d{2,4}', kumotaan_text)
    if len(set(statute_refs)) > 1:
        return {"chapter": [], "part": []}

    out: Dict[TargetUnitKind, List[str]] = {"chapter": [], "part": []}
    for kind, suffix in (("chapter", "luku"), ("part", "osa")):
        seen: Set[str] = set()
        vals: List[str] = []
        # lawvm-regex: owning_parser chapter/part container repeal recognizer (dynamic label-interpolated pattern, left inline per §1.11)
        for m in re.finditer(r'(\d+(?:\s*[a-z])?)\s+' + suffix + r'\b', kumotaan_text):
            norm = re.sub(r'\s+', '', m.group(1).strip())
            if norm and norm not in seen:
                vals.append(norm)
                seen.add(norm)
        out[kind] = vals
    return out

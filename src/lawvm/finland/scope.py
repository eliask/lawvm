"""Target/scope reasoning helpers for the Finnish frontend.

These helpers interpret chapter-scoped johtolause structure against the live
replay tree. They are separate from extraction and separate from deterministic
apply, so they belong in their own module rather than inside grafter.py.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from dataclasses import replace as dc_replace
from functools import lru_cache
from typing import TYPE_CHECKING, List, Optional, Sequence, Set, Tuple

import lxml.etree as etree

from lawvm.core.ir import IRNode
from lawvm.core.ir import LegalOperation as _LegalOperation
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.finland.body_pairing import ObservedBodyUnit, build_observed_body_inventory
from lawvm.finland.helpers import _norm_num_token
from lawvm.finland.johto_scope_mentions import collect_johto_chapter_scope_mentions
from lawvm.finland.ops import (
    ScopeConfidence,
    ScopeResolutionConfidence,
    ScopeResolutionSource,
    _lo_path_dict,
    _lo_with_path_update,
    lo_with_added_scope_tag,
    lo_with_scope_confidence,
)

if TYPE_CHECKING:
    from lawvm.finland.statute import ReplayState


# --- same-label MOVE clause ANCHORs (grammar-subordinate typed residue) -------
#
# The SIIRTAA (siirretään ... N lukuun) move family is modelled by the grammar
# move recognizers (johtolause/grammar/moves.py: recognize_inline_move_tail /
# recognize_relabel_from_context). When the clause-level grammar parser owns the
# johtolause it sets ``move_clause_target_unit_kind`` on the moved op, and the
# carrier check in ``strip_unjustified_chapter_scope_from_unique_sections``
# (``lo.move_clause_target_unit_kind in {"chapter", "part"}``) is the PRIMARY,
# grammar-authoritative signal — these anchors are only consulted AFTER it.
#
# They cannot be routed onto the grammar today: the clause-level parser DECLINES
# the plural ``joista … § … siirretään N lukuun`` move-coordination shape
# (``parse_clause`` falls to ``legacy_reference_fallback`` with
# ``"section näistä/niistä provenance leak"``), so the moved-section→destination
# map this guard needs is not produced grammar-owned for that shape. Until the
# grammar owns the coordination, these stay as a bounded label-collection
# residue floor: every quantifier is explicitly bounded (``\s{0,8}``,
# ``\d{1,4}``, ``[^§]{0,120}``), so the patterns are provably linear; the
# residual "nested backtracking quantifiers" flag is the benign-linear false
# positive (bounded × bounded), exactly as for ``kumotaan._WHOLE_SECTION_SITE_RE``.
_SAME_LABEL_MOVE_CLAUSE_RE = re.compile(
    r"joista\s{1,8}([^§]{0,120})\s{0,8}§\s{1,8}(?:samalla\s{1,8})?siirretään\s{1,8}(\d{1,4}\s{0,8}[a-z]?)\s{1,8}lukuun",
    flags=re.I,
)
_SINGULAR_SAME_LABEL_MOVE_CLAUSE_RE = re.compile(
    r"(\d{1,4}\s{0,8}[a-z]?)\s{0,8}§\s{0,8},?\s{0,8}joka\s{1,8}(?:samalla\s{1,8})?siirretään\s{1,8}(\d{1,4}\s{0,8}[a-z]?)\s{1,8}lukuun",
    flags=re.I,
)

# Module-scope constants for restrict_sec1_fallback_to_parent hot path
_FI_NUMBERED_ITEM_RE = re.compile(r"^\d+\)\s*", re.M)
_FI_CUT_RE = re.compile(r"\bsellais(?:ena|ina)\s+kuin\b|\bsiitä\s+on\b", re.I)
_FI_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÅÄÖ])")
_FI_SCOPE_VERB_RE = re.compile(r"\b(?:kumotaan|muutetaan|lisätään|siirretään)\b[: ]*", re.I)


@dataclass(frozen=True, slots=True)
class _ChapterSectionIndex:
    chapter_sections_by_part: dict[str | None, dict[str, set[str]]]
    section_chapters_by_part: dict[str | None, dict[str, set[str]]]

    def has_section_in_chapter(
        self,
        section_label: str,
        chapter_label: str,
        *,
        part_label: str | None = None,
    ) -> bool:
        section_norm = _norm_num_token(section_label)
        chapter_norm = _norm_num_token(chapter_label)
        part_norm = _norm_num_token(part_label) if part_label else None
        return section_norm in self.chapter_sections_by_part.get(part_norm, {}).get(chapter_norm, set())

    def section_chapters(
        self,
        section_label: str,
        *,
        part_label: str | None = None,
    ) -> set[str]:
        section_norm = _norm_num_token(section_label)
        part_norm = _norm_num_token(part_label) if part_label else None
        return set(self.section_chapters_by_part.get(part_norm, {}).get(section_norm, set()))

    def has_section_in_different_chapter(
        self,
        section_label: str,
        chapter_label: str,
        *,
        part_label: str | None = None,
    ) -> bool:
        chapter_norm = _norm_num_token(chapter_label)
        return any(
            candidate != chapter_norm
            for candidate in self.section_chapters(section_label, part_label=part_label)
        )


_CHAPTER_SECTION_INDEX_CACHE: dict[tuple[int, int], tuple[IRNode, _ChapterSectionIndex]] = {}
_CHAPTER_SECTION_INDEX_BY_PROVISION_INDEX_CACHE: dict[int, tuple[object, _ChapterSectionIndex]] = {}
_PART_SCOPED_CHAPTERS_CACHE: dict[tuple[int, int, str | None], tuple[IRNode, list[IRNode]]] = {}


@lru_cache(maxsize=65536)
def _chapter_section_scope_for_path(
    path: tuple[tuple[str, str], ...],
) -> tuple[str | None, str, str] | None:
    if len(path) < 2 or path[-1][0] != "section" or not path[-1][1]:
        return None
    parent_kind, parent_label = path[-2]
    if parent_kind != "chapter" or not parent_label:
        return None
    part_norm = None
    for kind, label in path[:-2]:
        if kind == "part" and label:
            part_norm = _norm_num_token(label)
    return part_norm, _norm_num_token(parent_label), _norm_num_token(path[-1][1])


def _chapter_section_index(master: "ReplayState") -> _ChapterSectionIndex:
    cache_key = (id(master.ir), getattr(master, "revision", 0))
    cached = _CHAPTER_SECTION_INDEX_CACHE.get(cache_key)
    if cached is not None and cached[0] is master.ir:
        return cached[1]

    chapter_sections_by_part: dict[str | None, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    section_chapters_by_part: dict[str | None, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))

    provision_index = getattr(master, "provision_index", None)
    if provision_index is not None:
        provision_index_cache_key = id(provision_index)
        cached_by_provision_index = _CHAPTER_SECTION_INDEX_BY_PROVISION_INDEX_CACHE.get(
            provision_index_cache_key
        )
        if (
            cached_by_provision_index is not None
            and cached_by_provision_index[0] is provision_index
        ):
            index = cached_by_provision_index[1]
            _CHAPTER_SECTION_INDEX_CACHE[cache_key] = (master.ir, index)
            return index
        for (_kind, _label_norm), paths in provision_index.items():
            if _kind != "section":
                continue
            for path in paths:
                scope = _chapter_section_scope_for_path(path)
                if scope is None:
                    continue
                part_norm, chapter_norm, section_norm = scope
                chapter_sections_by_part[None][chapter_norm].add(section_norm)
                section_chapters_by_part[None][section_norm].add(chapter_norm)
                if part_norm is not None:
                    chapter_sections_by_part[part_norm][chapter_norm].add(section_norm)
                    section_chapters_by_part[part_norm][section_norm].add(chapter_norm)
    else:
        def _walk(node: IRNode, current_part: str | None) -> None:
            next_part = current_part
            if node.kind is IRNodeKind.PART and node.label:
                next_part = _norm_num_token(node.label)
            if node.kind is IRNodeKind.CHAPTER and node.label:
                chapter_norm = _norm_num_token(node.label)
                for child in node.children:
                    if child.kind is IRNodeKind.SECTION and child.label:
                        section_norm = _norm_num_token(child.label)
                        chapter_sections_by_part[None][chapter_norm].add(section_norm)
                        section_chapters_by_part[None][section_norm].add(chapter_norm)
                        if next_part is not None:
                            chapter_sections_by_part[next_part][chapter_norm].add(section_norm)
                            section_chapters_by_part[next_part][section_norm].add(chapter_norm)
            for child in node.children:
                _walk(child, next_part)

        _walk(master.ir, None)
    index = _ChapterSectionIndex(
        chapter_sections_by_part={
            part: dict(chapters)
            for part, chapters in chapter_sections_by_part.items()
        },
        section_chapters_by_part={
            part: dict(sections)
            for part, sections in section_chapters_by_part.items()
        },
    )
    if len(_CHAPTER_SECTION_INDEX_CACHE) > 512:
        _CHAPTER_SECTION_INDEX_CACHE.clear()
    _CHAPTER_SECTION_INDEX_CACHE[cache_key] = (master.ir, index)
    if provision_index is not None:
        if len(_CHAPTER_SECTION_INDEX_BY_PROVISION_INDEX_CACHE) > 512:
            _CHAPTER_SECTION_INDEX_BY_PROVISION_INDEX_CACHE.clear()
        _CHAPTER_SECTION_INDEX_BY_PROVISION_INDEX_CACHE[id(provision_index)] = (
            provision_index,
            index,
        )
    return index


@lru_cache(maxsize=1024)
def _fi_statute_citation_re(parent_id: str) -> "re.Pattern[str] | None":
    """Compile (cached) a statute-citation bracketed-reference pattern for parent_id."""
    try:
        year_str, num_str = parent_id.split("/")
        num = int(num_str)
    except (ValueError, AttributeError):
        return None
    return re.compile(
        rf"\(\s*{num}\s*/\s*(?:{re.escape(year_str)}|{re.escape(year_str[-2:])})\s*\)",
        re.IGNORECASE,
    )


def fi_statute_citation_spans(text: str, parent_id: str) -> tuple[tuple[int, int], ...]:
    """Return character spans for lexical citations to ``parent_id`` in ``text``."""
    if not text or not parent_id:
        return ()
    ref_re = _fi_statute_citation_re(parent_id)
    if ref_re is None:
        return ()
    return tuple((match.start(), match.end()) for match in ref_re.finditer(text))


def duplicate_section_labels_across_chapters(master_ir: IRNode) -> Set[str]:
    counts: dict[str, set[str]] = {}

    def _collect(node: IRNode) -> None:
        if node.kind == IRNodeKind.CHAPTER and node.label:
            for child in node.children:
                if child.kind == IRNodeKind.SECTION and child.label:
                    counts.setdefault(child.label, set()).add(node.label)
        for child in node.children:
            _collect(child)

    _collect(master_ir)
    return {label for label, chapters in counts.items() if len(chapters) > 1}


def _same_label_move_sections_for_chapter(johto: str, chapter: str) -> Set[str]:
    # johto is already Zs-normalized by _normalize_fi_parse_text upstream.
    cleaned = re.sub(r"\s+", " ", johto or "").lower()
    wanted_chapter = _norm_num_token(str(chapter)).removesuffix("luku")
    matches: Set[str] = set()
    mentions = collect_johto_chapter_scope_mentions(johto or "")
    for moved in mentions.moved_section_destinations:
        if _norm_num_token(moved.destination_chapter_label).removesuffix("luku") == wanted_chapter:
            matches.add(_norm_num_token(moved.section_label))
    for labels_text, dest_chapter in _SAME_LABEL_MOVE_CLAUSE_RE.findall(cleaned):
        if _norm_num_token(dest_chapter).removesuffix("luku") != wanted_chapter:
            continue
        for match in re.finditer(r"\d+(?:\s*[a-z](?![a-z]))?", labels_text, flags=re.I):
            matches.add(_norm_num_token(match.group(0)))
    for section_label, dest_chapter in _SINGULAR_SAME_LABEL_MOVE_CLAUSE_RE.findall(cleaned):
        if _norm_num_token(dest_chapter).removesuffix("luku") != wanted_chapter:
            continue
        matches.add(_norm_num_token(section_label))
    return matches


def _duplicate_section_labels(master: "ReplayState") -> Set[str]:
    return master.duplicate_section_labels


def chapter_chunks_from_johtolause(johto: str) -> List[Tuple[str, str]]:
    # johto is already Zs-normalized by _normalize_fi_parse_text upstream.
    text = re.sub(r"\s+", " ", johto or "")
    citation_cut_re = re.compile(r"\bsellais(?:ena|ina)\s+kuin\b", flags=re.I)

    def _match_is_inside_prior_law_citation(match: re.Match[str]) -> bool:
        prefix = text[: match.start()]
        citation = None
        for citation_match in citation_cut_re.finditer(prefix):
            citation = citation_match
        if citation is None:
            return False
        last_scope_verb_end = 0
        for verb_match in _FI_SCOPE_VERB_RE.finditer(prefix):
            last_scope_verb_end = verb_match.end()
        return citation.start() >= last_scope_verb_end

    matches = list(
        match
        for match in re.finditer(
            r"((?:\d+\s*[a-z]?\s*,\s*)*\d+\s*[a-z]?(?:\s+ja\s+\d+\s*[a-z]?)?)\s+lu(?:ku|vun)\b",
            text,
            flags=re.I,
        )
        if not _match_is_inside_prior_law_citation(match)
    )
    chunks: List[Tuple[str, str]] = []
    for idx, match in enumerate(matches):
        cluster = match.group(1)
        labels = [
            _norm_num_token(token.strip().lower())
            for token in re.split(r"\s*,\s*|\s+ja\s+", cluster)
            if re.fullmatch(r"\d+[a-z]?", _norm_num_token(token.strip()), flags=re.I)
        ]
        if not labels:
            continue
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        # A nominative "N luku" governed by a preceding "kumotaan" is a chapter
        # *repeal target* (``kumotaan ... 14 luku``). Sections repealed in the
        # same kumotaan clause (``kumotaan 3 ja 4 luku, 47 § sekä 48 §``) belong
        # to that chapter, so the chunk must still cover them. But a later scope
        # verb (``muutetaan``/``lisätään``/``siirretään``) starts an unrelated op
        # group whose sections must not be dragged into the repealed chapter —
        # e.g. ``kumotaan ... 14 luku, muutetaan ... lisätään ... uusi 176 §``,
        # where the new 176 § belongs to its own home chapter, not 14. End the
        # repealed chapter's chunk at the first such verb.
        if _chapter_match_is_repeal_target(text, match):
            verb_boundary = re.search(
                r"\b(?:muutetaan|lisätään|siirretään|korvataan)\b",
                text[start:end],
                re.I,
            )
            if verb_boundary is not None:
                end = start + verb_boundary.start()
        citation_boundary = citation_cut_re.search(text[start:end])
        if citation_boundary is not None:
            end = start + citation_boundary.start()
        chunks.append((labels[-1], text[start:end]))
    return chunks


def _chapter_match_is_repeal_target(text: str, match: "re.Match[str]") -> bool:
    """Return True when a "N luku" match is a kumotaan-governed repeal target.

    The chapter is a repeal target when its surface form is the nominative
    ``N luku`` (not the genitive ``N luvun``/illative ``N lukuun`` that head a
    scope) and the nearest governing amendment verb before it is ``kumotaan``,
    with no intervening scope verb (``muutetaan``/``lisätään``/``siirretään``).
    """
    if not re.match(
        r"\d+\s*[a-z]?(?:\s*,\s*\d+\s*[a-z]?|\s+ja\s+\d+\s*[a-z]?)*\s+luku\b",
        text[match.start():],
        re.I,
    ):
        return False
    prefix = text[: match.start()]
    last_verb = None
    for verb_match in re.finditer(
        r"\b(kumotaan|muutetaan|lisätään|siirretään|korvataan)\b", prefix, re.I
    ):
        last_verb = verb_match.group(1).lower()
    return last_verb == "kumotaan"


def find_body_section_chapter(
    muutos_tree: etree._Element,
    section_norm: str,
    *,
    inventory: Sequence[ObservedBodyUnit] | None = None,
) -> str | None:
    """Return the amendment-body chapter label for *section_norm*, if present."""
    observed_units = inventory if inventory is not None else build_observed_body_inventory(muutos_tree)
    for bpu in observed_units:
        if bpu.kind == "section" and _norm_num_token(bpu.label) == section_norm and bpu.chapter_label:
            return bpu.chapter_label
    return None


def _observed_units_from_source(
    *,
    muutos_tree: etree._Element | None,
    inventory: Sequence[ObservedBodyUnit] | None,
) -> Sequence[ObservedBodyUnit]:
    if inventory is not None:
        return inventory
    if muutos_tree is None:
        return ()
    return build_observed_body_inventory(muutos_tree)


def _unit_part_label(unit: ObservedBodyUnit) -> str | None:
    return _norm_num_token(unit.part_label) if unit.part_label else None


def _body_scope_section_units(
    observed_units: Sequence[ObservedBodyUnit],
    *,
    body_chapter: str,
    body_part: str | None,
) -> list[ObservedBodyUnit]:
    body_chapter_norm = _norm_num_token(body_chapter)
    body_part_norm = _norm_num_token(body_part) if body_part else None
    return [
        unit
        for unit in observed_units
        if unit.kind == "section"
        and _norm_num_token(unit.chapter_label) == body_chapter_norm
        and _unit_part_label(unit) == body_part_norm
    ]


def retarget_heading_insert_body_chapter_from_close_live_sibling(
    *,
    muutos_tree: etree._Element | None = None,
    inventory: Sequence[ObservedBodyUnit] | None = None,
    section_norm: str,
    body_chapter: str,
    master: "ReplayState",
) -> str:
    """Retarget a stale heading-only insert wrapper from a very close live sibling."""
    if not re.fullmatch(r"\d+", section_norm):
        return body_chapter

    observed_units = _observed_units_from_source(
        muutos_tree=muutos_tree,
        inventory=inventory,
    )
    scoped_units = _body_scope_section_units(
        observed_units,
        body_chapter=body_chapter,
        body_part=None,
    )
    if not scoped_units:
        return body_chapter

    target_num = int(section_norm)
    for target_unit in scoped_units:
        if _norm_num_token(target_unit.label) != section_norm:
            continue

        close_live_chapters: dict[int, set[str]] = defaultdict(set)
        for sibling in scoped_units:
            sibling_label = _norm_num_token(sibling.label)
            if not re.fullmatch(r"\d+", sibling_label):
                continue
            distance = abs(int(sibling_label) - target_num)
            if distance == 0 or distance > 2:
                continue
            live_path = master.find_section_path(sibling_label, None, None)
            if live_path is None:
                continue
            live_chapter = next((label for kind, label in live_path if kind == "chapter"), None)
            if live_chapter:
                close_live_chapters[distance].add(live_chapter)
        if close_live_chapters:
            nearest_distance = min(close_live_chapters)
            nearest_live_chapters = close_live_chapters[nearest_distance]
            if len(nearest_live_chapters) == 1:
                return next(iter(nearest_live_chapters))
        return body_chapter

    return body_chapter


def retarget_duplicate_body_section_scope_from_close_live_siblings(
    *,
    muutos_tree: etree._Element | None = None,
    inventory: Sequence[ObservedBodyUnit] | None = None,
    section_norm: str,
    body_chapter: str,
    body_part: str | None,
    master: "ReplayState",
) -> tuple[str | None, str] | None:
    """Retarget stale duplicate-labelled body scope from nearby live siblings."""
    target_match = re.fullmatch(r"(\d+)[a-z]?", section_norm, re.I)
    if target_match is None:
        return None

    observed_units = _observed_units_from_source(
        muutos_tree=muutos_tree,
        inventory=inventory,
    )
    scoped_units = _body_scope_section_units(
        observed_units,
        body_chapter=body_chapter,
        body_part=body_part,
    )
    if not scoped_units:
        return None

    target_num = int(target_match.group(1))
    is_letter_suffix_section = section_norm != str(target_num)

    for target_unit in scoped_units:
        sec_label = _norm_num_token(target_unit.label)
        if sec_label != section_norm:
            continue

        # The section being retargeted already has a live home in body_chapter:
        # the body chapter is its real chapter, not a stale duplicate-label
        # scope. Amendment bodies routinely lump sections from several target
        # chapters under one <chapter> element, so a divergent sibling in the
        # same element must not pull a correctly-placed section out of its home.
        target_live_path = master.find_section_path(str(target_num), body_chapter, body_part)
        if target_live_path is not None:
            return None

        if (
            is_letter_suffix_section
            and str(target_num) not in master.duplicate_section_labels
        ):
            stem_live_path = master.find_section_path(str(target_num), None, body_part)
            if stem_live_path is not None:
                stem_live_part = next((label for kind, label in stem_live_path if kind == "part"), None)
                stem_live_chapter = next((label for kind, label in stem_live_path if kind == "chapter"), None)
                if (
                    stem_live_chapter
                    and stem_live_chapter != body_chapter
                    and stem_live_part == body_part
                ):
                    return stem_live_part, stem_live_chapter

        close_live_scopes: dict[int, set[tuple[str | None, str]]] = defaultdict(set)
        body_chapter_corroborated = False
        for sibling in scoped_units:
            sibling_label = _norm_num_token(sibling.label)
            sibling_match = re.fullmatch(r"(\d+)[a-z]?", sibling_label, re.I)
            if sibling_match is None:
                continue
            if sibling_label != sibling_match.group(1):
                continue
            distance = abs(int(sibling_match.group(1)) - target_num)
            if distance > 2:
                continue
            if distance == 0 and not is_letter_suffix_section:
                continue
            live_path = master.find_section_path(sibling_match.group(1), None, body_part)
            if live_path is None:
                continue
            live_part = next((label for kind, label in live_path if kind == "part"), None)
            live_chapter = next((label for kind, label in live_path if kind == "chapter"), None)
            if not live_chapter:
                continue
            if live_chapter == body_chapter and live_part == body_part:
                # A close numeric sibling genuinely lives in body_chapter, so the
                # body chapter is corroborated, not stale. Amendment bodies that
                # lump sections from several target chapters under one <chapter>
                # element (the section's real chapter coming from its own live
                # home, not the XML nesting) must not drag a correctly-placed
                # neighbour out of its chapter on the strength of divergent
                # siblings that happen to be edited in the same body element.
                body_chapter_corroborated = True
                continue
            close_live_scopes[distance].add((live_part, live_chapter))

        if body_chapter_corroborated:
            return None

        if close_live_scopes:
            nearest_distance = min(close_live_scopes)
            nearest_live_scopes = close_live_scopes[nearest_distance]
            if len(nearest_live_scopes) == 1:
                return next(iter(nearest_live_scopes))
        return None

    return None


def body_has_pseudo_chapter_marker(
    muutos_tree: etree._Element,
    chapter_label: str,
    *,
    inventory: Sequence[ObservedBodyUnit] | None = None,
) -> bool:
    """Return True if the amendment body contains a pseudo-chapter marker."""
    observed_units = inventory if inventory is not None else build_observed_body_inventory(muutos_tree)
    for bpu in observed_units:
        if bpu.kind == "chapter" and bpu.label == chapter_label and bpu.source_tag == "section":
            return True
    return False


def body_has_real_chapter_container(
    muutos_tree: etree._Element,
    chapter_label: str,
    *,
    inventory: Sequence[ObservedBodyUnit] | None = None,
) -> bool:
    """Return True when the amendment body contains a real <chapter> container."""
    observed_units = inventory if inventory is not None else build_observed_body_inventory(muutos_tree)
    for bpu in observed_units:
        if bpu.kind == "chapter" and bpu.label == chapter_label and bpu.source_tag == "chapter":
            return True
    return False


def _iter_part_scoped_chapters(
    master: "ReplayState",
    *,
    part_label: str | None = None,
) -> list[IRNode]:
    wanted_part = _norm_num_token(part_label) if part_label else None
    cache_key = (id(master.ir), getattr(master, "revision", 0), wanted_part)
    cached = _PART_SCOPED_CHAPTERS_CACHE.get(cache_key)
    if cached is not None and cached[0] is master.ir:
        return cached[1]

    chapters: list[IRNode] = []

    def _walk(node: IRNode, current_part: str | None) -> None:
        next_part = current_part
        if node.kind is IRNodeKind.PART and node.label:
            next_part = _norm_num_token(node.label)
        if node.kind is IRNodeKind.CHAPTER and node.label:
            if wanted_part is None or next_part == wanted_part:
                chapters.append(node)
        for child in node.children:
            _walk(child, next_part)

    _walk(master.ir, None)
    if len(_PART_SCOPED_CHAPTERS_CACHE) > 1024:
        _PART_SCOPED_CHAPTERS_CACHE.clear()
    _PART_SCOPED_CHAPTERS_CACHE[cache_key] = (master.ir, chapters)
    return chapters


def _master_has_section_in_chapter(
    master: "ReplayState",
    section_label: str,
    chapter_label: str,
    *,
    part_label: str | None = None,
    chapter_section_index: _ChapterSectionIndex | None = None,
) -> bool:
    if chapter_section_index is not None:
        return chapter_section_index.has_section_in_chapter(
            section_label,
            chapter_label,
            part_label=part_label,
        )
    section_norm = _norm_num_token(section_label)
    chapter_norm = _norm_num_token(chapter_label)
    for node in _iter_part_scoped_chapters(master, part_label=part_label):
        if _norm_num_token(node.label or "") != chapter_norm:
            continue
        if any(
            child.kind is IRNodeKind.SECTION
            and child.label
            and _norm_num_token(child.label) == section_norm
            for child in node.children
        ):
            return True
    return False


def _master_has_section_in_stated_part_different_chapter(
    master: "ReplayState",
    section_label: str,
    chapter_label: str,
    *,
    part_label: str | None = None,
    chapter_section_index: _ChapterSectionIndex | None = None,
) -> bool:
    """Return True if the section exists in master within the stated part scope but NOT in the stated chapter.

    This is used to detect "johtolause carry-forward" artifacts where the PEG parser
    assigns a section to a chapter because it follows a chapter heading in the
    johtolause, but the section actually lives in a different chapter of the same
    part.  Unlike _master_has_section_in_chapter, this does NOT require the section
    to be in the stated chapter — it requires the section to be in a DIFFERENT
    chapter but within the same part scope.

    Returns False if the section does not exist in master at all within the stated
    part scope (e.g., the section has been moved to a completely different part),
    which is the signal that the explicit_chunk scope might be a legitimate
    stale-but-needed scope for the retarget mechanism.
    """
    if chapter_section_index is not None:
        return chapter_section_index.has_section_in_different_chapter(
            section_label,
            chapter_label,
            part_label=part_label,
        )
    section_norm = _norm_num_token(section_label)
    chapter_norm = _norm_num_token(chapter_label)
    for node in _iter_part_scoped_chapters(master, part_label=part_label):
        if _norm_num_token(node.label or "") == chapter_norm:
            continue  # Skip the stated chapter itself
        if any(
            child.kind is IRNodeKind.SECTION
            and child.label
            and _norm_num_token(child.label) == section_norm
            for child in node.children
        ):
            return True
    return False


def _unique_section_chapter(
    master: "ReplayState",
    section_label: str,
    *,
    part_label: str | None = None,
    chapter_section_index: _ChapterSectionIndex | None = None,
) -> str | None:
    if chapter_section_index is not None:
        chapters = chapter_section_index.section_chapters(section_label, part_label=part_label)
        if len(chapters) != 1:
            return None
        return next(iter(chapters))
    section_norm = _norm_num_token(section_label)
    chapters: set[str] = set()
    for node in _iter_part_scoped_chapters(master, part_label=part_label):
        for child in node.children:
            if child.kind is IRNodeKind.SECTION and child.label and _norm_num_token(child.label) == section_norm:
                chapters.add(_norm_num_token(node.label))
                break
    if len(chapters) != 1:
        return None
    return next(iter(chapters))


def infer_letter_suffix_section_chapter_from_stem_host(
    master: "ReplayState",
    section_label: str,
    *,
    part_label: str | None = None,
    chapter_section_index: _ChapterSectionIndex | None = None,
) -> str | None:
    """Infer chapter scope for §Nα when only the bare stem §N still lives."""
    section_norm = _norm_num_token(section_label)
    if chapter_section_index is not None:
        if chapter_section_index.section_chapters(section_norm, part_label=part_label):
            return None
    else:
        find_section_path = getattr(master, "find_section_path", None)
        if callable(find_section_path) and find_section_path(section_norm, None, part_label) is not None:
            return None
    stem_match = re.fullmatch(r"(\d+)([a-z])", section_norm, flags=re.I)
    if stem_match is None:
        return None
    stem = stem_match.group(1)
    stem_chapter = _unique_section_chapter(
        master,
        stem,
        part_label=part_label,
        chapter_section_index=chapter_section_index,
    )
    if stem_chapter is None:
        return None
    if not _master_has_section_in_chapter(
        master,
        stem,
        stem_chapter,
        part_label=part_label,
        chapter_section_index=chapter_section_index,
    ):
        return None
    return stem_chapter


def _unique_base_section_chapter(
    master: "ReplayState",
    section_label: str,
    *,
    part_label: str | None = None,
    chapter_section_index: _ChapterSectionIndex | None = None,
) -> str | None:
    norm = _norm_num_token(section_label)
    match = re.fullmatch(r"(\d+)([a-z])", norm, flags=re.I)
    if match is None:
        return None
    base_norm = match.group(1)
    if chapter_section_index is not None:
        chapters = chapter_section_index.section_chapters(base_norm, part_label=part_label)
        if len(chapters) != 1:
            return None
        return next(iter(chapters))
    chapters: set[str] = set()
    for node in _iter_part_scoped_chapters(master, part_label=part_label):
        for child in node.children:
            if child.kind is IRNodeKind.SECTION and child.label and _norm_num_token(child.label) == base_norm:
                chapters.add(_norm_num_token(node.label))
                break
    if len(chapters) != 1:
        return None
    return next(iter(chapters))


def _chapter_chunk_mentions_section_label(chunk: str, section_label: str) -> bool:
    norm = _norm_num_token(section_label)
    m = re.fullmatch(r"(\d+)([a-z]?)", norm, flags=re.I)
    if not m:
        return re.search(rf"\b{re.escape(section_label)}\s*§", chunk, flags=re.I) is not None

    base, suffix = m.groups()

    def _genitive_reference_is_whole_section(match: re.Match[str]) -> bool:
        tail = chunk[match.end() : match.end() + 40]
        return not re.match(
            r"\s+\d+(?:\s+ja\s+\d+)?\s+(?:moment\w*|kohta\b)",
            tail,
            flags=re.I,
        )

    # Whole-section carry-forward must not latch onto subsection-qualified
    # mentions like ``1 §:n 4 momentti`` when choosing the governing chapter
    # chunk for a later plain ``1 §`` op.
    direct_pat = (
        rf"\b{re.escape(base)}\s*{re.escape(suffix)}\s*§(?!\s*:n?\b)"
        if suffix
        else rf"\b{re.escape(base)}\s*§(?!\s*:n?\b)"
    )
    if re.search(direct_pat, chunk, flags=re.I):
        return True
    genitive_pat = (
        rf"\b{re.escape(base)}\s*{re.escape(suffix)}\s*§:n?\b"
        if suffix
        else rf"\b{re.escape(base)}\s*§:n?\b"
    )
    for match in re.finditer(genitive_pat, chunk, flags=re.I):
        if _genitive_reference_is_whole_section(match):
            return True

    if suffix:
        # Handles chains like "5 a ja 8-10 §" where only the terminal label
        # carries the section sign.
        if re.search(
            rf"\b{re.escape(base)}\s*{re.escape(suffix)}\b(?=[^§]{{0,40}}§)",
            chunk,
            flags=re.I,
        ):
            return True
        return False

    wanted = int(base)
    for a, b, c in re.findall(r"(\d+)\s*[–-]\s*(\d+)(?:\s+ja\s+(\d+))?\s*§", chunk, flags=re.I):
        lo_, hi = sorted((int(a), int(b)))
        if lo_ <= wanted <= hi:
            return True
        if c and wanted == int(c):
            return True
    for a, b in re.findall(r"(\d+)\s+ja\s+(\d+)\s*§", chunk, flags=re.I):
        if wanted in {int(a), int(b)}:
            return True
    return False


def _chapter_chunk_mentions_lo(chunk: str, lo: _LegalOperation) -> bool:
    pd = _lo_path_dict(lo)
    sec_label = str(pd.get("section", ""))
    sec = re.escape(sec_label)
    subsec = pd.get("subsection")
    item = pd.get("item")
    special = lo.target.special

    def _moment_in_chunk(target: int) -> bool:
        if re.search(rf"\b{target}\s+moment\w*", chunk, flags=re.I):
            return True
        for a, b in re.findall(r"(\d+)\s*[–-]\s*(\d+)\s+moment\w*", chunk, flags=re.I):
            lo_, hi = sorted((int(a), int(b)))
            if lo_ <= target <= hi:
                return True
        for a, b, c in re.findall(r"(\d+)\s*[–-]\s*(\d+)(?:\s+ja\s+(\d+))?\s+moment\w*", chunk, flags=re.I):
            lo_, hi = sorted((int(a), int(b)))
            if lo_ <= target <= hi:
                return True
            if c and target == int(c):
                return True
        for a, b in re.findall(r"(\d+)\s+ja\s+(\d+)\s+moment\w*", chunk, flags=re.I):
            if target in {int(a), int(b)}:
                return True
        return False

    def _item_in_chunk(target: str) -> bool:
        if re.search(rf"\b{re.escape(target)}\s+kohta\b", chunk, flags=re.I):
            return True
        if target.isdigit():
            wanted = int(target)
            for a, b, c in re.findall(r"(\d+)\s*[–-]\s*(\d+)(?:\s+ja\s+(\d+))?\s+kohta", chunk, flags=re.I):
                lo_, hi = sorted((int(a), int(b)))
                if lo_ <= wanted <= hi:
                    return True
                if c and wanted == int(c):
                    return True
            for a, b in re.findall(r"(\d+)\s+ja\s+(\d+)\s+kohta", chunk, flags=re.I):
                if wanted in {int(a), int(b)}:
                    return True
        return False

    if special == "heading":
        return re.search(rf"\b{sec}\s*§:n?\s+otsikko\b", chunk, flags=re.I) is not None
    if special == "intro":
        if subsec is not None:
            return re.search(
                rf"\b{sec}\s*§:n?\s+{subsec}\s+moment\w*\s+johdantokappale\b",
                chunk,
                flags=re.I,
            ) is not None
        return re.search(rf"\b{sec}\s*§:n?\s+johdantokappale\b", chunk, flags=re.I) is not None
    if subsec is not None and item is not None:
        if _item_in_chunk(str(item)) and re.search(rf"\b{sec}\s*§", chunk, flags=re.I):
            return True
        if not re.search(rf"\b{sec}\s*§:n?\s+{subsec}\s+moment\w*", chunk, flags=re.I):
            return False
        return _item_in_chunk(str(item))
    if subsec is not None:
        if re.search(rf"\b{sec}\s*§", chunk, flags=re.I) and _moment_in_chunk(int(subsec)):
            return True
        return re.search(rf"\b{sec}\s*§:n?\s+{subsec}(?:\s+ja\s+\d+)?\s+moment", chunk, flags=re.I) is not None
    return _chapter_chunk_mentions_section_label(chunk, sec_label)


# --- shared source-plane descendant-scope recognizer (scope.py owner) ---------
#
# The "a parsed amendment formula names scope BELOW a section" predicate is owned
# here, next to the chapter-chunk scope grammar above (``_section_in_chunk`` /
# ``_genitive_reference_is_whole_section`` / ``_moment_in_chunk`` /
# ``_item_in_chunk``). Apply (``apply_runtime_support._section_source_names_descendant_scope``)
# and the merge subsection-shell guard (``merge._source_targets_plain_subsection``)
# previously each owned their own inline ``raw_text`` regex of this same grammar
# (an AMENDMENT-SOURCE → legal-state reach-back per AGENTS.md §1.12). They now call
# the single owner below; scope.py is the canonical parser for this construction
# family, so the regexes live here behind ``owning_parser`` waivers and the callers
# pass the already-typed source text as a plain ``text`` argument.
_SECTION_GENITIVE_DESCENDANT_SCOPE_RE = re.compile(
    r"\b(?P<section>\d+[a-z]?)\s*§:n\b.{0,240}?\b"
    r"(?:moment[a-zåäö]{0,20}|koht[a-zåäö]{0,20}|alakoht[a-zåäö]{0,20})\b",
    re.IGNORECASE,
)
# Subsection/moment target templates: the target label is escaped and
# interpolated per call (mirrors the original merge guard exactly).
_SUBSECTION_MOMENT_INTRO_TPL = r"\b{target}\s+momentin\s+johdanto\w*"
_SUBSECTION_PLAIN_MOMENT_TPL = r"\b{target}\s+momentti\b"


@dataclass(frozen=True, slots=True)
class SourceDescendantScopeResult:
    """Typed outcome of the source descendant-scope predicate.

    ``matched`` is the legal-state-driving boolean (does the source formula name
    descendant scope below ``section_label``).  ``unparsed_cue`` is set when the
    source carried the section-genitive cue (``N §:n``) yet no descendant-scope
    formula for the requested section could be resolved — the residual the apply
    path used to swallow with a silent ``return False``.  It is observability
    only and never changes ``matched``.
    """

    matched: bool
    unparsed_cue: str | None = None


def source_names_descendant_scope_below_section(
    text: str, section_label: str
) -> SourceDescendantScopeResult:
    """Whether a parsed source formula names scope below ``section_label``.

    Reproduces the ``N §:n ... moment/kohta/alakohta`` descendant-scope grammar.
    Returns a typed result so callers can witness the unparsed-cue residual
    instead of conflating it with a clean negative.
    """
    if not text or "§:n" not in text:
        return SourceDescendantScopeResult(matched=False)
    target = _norm_num_token(section_label)
    saw_cue = False
    # lawvm-regex: owning_parser scope.py is the canonical descendant-scope parser
    for match in _SECTION_GENITIVE_DESCENDANT_SCOPE_RE.finditer(text):
        saw_cue = True
        if _norm_num_token(match.group("section")) == target:
            return SourceDescendantScopeResult(matched=True)
    # The source named a section-genitive descendant-scope formula, but for no
    # section matching the target. Surface it as a typed residual rather than a
    # silent ``False`` so the apply caller can witness the unhandled cue.
    if saw_cue:
        return SourceDescendantScopeResult(matched=False, unparsed_cue=text)
    return SourceDescendantScopeResult(matched=False)


def source_targets_plain_subsection_moment(text: str, target_paragraph: int) -> bool:
    """Whether the source formula targets the plain ``N momentti`` subsection.

    The moment-INTRO (``N momentin johdanto``) is an explicit negative: targeting
    the intro is not targeting the plain subsection. Reproduces the merge
    subsection-shell guard predicate.
    """
    normalized = " ".join(text.casefold().split())
    if not normalized:
        return False
    target = re.escape(str(target_paragraph))
    # lawvm-regex: owning_parser scope.py owns the subsection/moment scope grammar
    if re.search(_SUBSECTION_MOMENT_INTRO_TPL.format(target=target), normalized):
        return False
    # lawvm-regex: owning_parser scope.py owns the subsection/moment scope grammar
    return re.search(_SUBSECTION_PLAIN_MOMENT_TPL.format(target=target), normalized) is not None


def _johtolause_explicitly_binds_chapter_section(johto: str, chapter: str, section: str) -> bool:
    def _chapter_pat(label: str) -> str:
        norm = _norm_num_token(str(label))
        m = re.fullmatch(r"(\d+)([a-z])?", norm, flags=re.I)
        if not m:
            return re.escape(str(label))
        num, suffix = m.groups()
        return rf"{re.escape(num)}\s*{re.escape(suffix)}" if suffix else re.escape(num)

    def _section_pat(label: str) -> str:
        norm = _norm_num_token(str(label))
        m = re.fullmatch(r"(\d+)([a-z])?", norm, flags=re.I)
        if not m:
            return re.escape(str(label))
        num, suffix = m.groups()
        if suffix:
            return rf"{re.escape(num)}\s*{re.escape(suffix)}"
        return rf"{re.escape(num)}(?!\s*[a-z])"

    def _section_list_pat(label: str) -> str:
        norm = _norm_num_token(str(label))
        m = re.fullmatch(r"(\d+)([a-z])?", norm, flags=re.I)
        if not m:
            return re.escape(str(label))
        num, suffix = m.groups()
        return rf"{re.escape(num)}\s*{re.escape(suffix)}" if suffix else re.escape(num)

    # johto is already Zs-normalized by _normalize_fi_parse_text upstream.
    text = re.sub(r"\s+", " ", johto or "")
    chapter_pat = _chapter_pat(str(chapter))
    section_pat = _section_pat(str(section))
    section_list_pat = _section_list_pat(str(section))
    chapter_norm = _norm_num_token(str(chapter)).removesuffix("luku")
    for chunk_chapter, chunk in chapter_chunks_from_johtolause(text):
        if (
            _norm_num_token(chunk_chapter).removesuffix("luku") == chapter_norm
            and _chapter_chunk_mentions_section_label(chunk, str(section))
        ):
            return True
    if (
        re.search(
            # Negative lookahead: "X luvun otsikko" means only the chapter heading
            # belongs to chapter X -- the sections listed after "otsikko" are not
            # chapter-scoped by this phrase.
            rf"\b{chapter_pat}\s+luvun\s+(?!otsikko\b)[^§]{{0,120}}\b{section_pat}\b[^§]{{0,40}}§",
            text,
            flags=re.I,
        ) is not None
        or re.search(
            rf"\b{chapter_pat}\s+lukuun\b.{{0,220}}\b{section_pat}\b.{{0,80}}§",
            text,
            flags=re.I,
        ) is not None
    ):
        return True

    # Section may be implicitly covered by an en-dash range (e.g. "8 lukuun uusi 31–33 §"
    # covers §32 even though "32" does not appear literally in the text).
    sec_norm = _norm_num_token(str(section))
    sec_m = re.fullmatch(r"(\d+)([a-z])?", sec_norm, flags=re.I)
    if sec_m and not sec_m.group(2):  # plain integer section only — letter-suffix not a range endpoint
        sec_int = int(sec_m.group(1))
        for chp_m in re.finditer(
            rf"\b{chapter_pat}\s+lukuun\b",
            text,
            flags=re.I,
        ):
            window = text[chp_m.end() : chp_m.end() + 300]
            for rng_m in re.finditer(r"\b(\d+)\s*[–\-]\s*(\d+)\b", window):
                lo_val, hi_val = int(rng_m.group(1)), int(rng_m.group(2))
                if lo_val < sec_int < hi_val:  # strictly interior (endpoints already matched above)
                    # Confirm a § follows the range within reasonable distance
                    after = window[rng_m.end() : rng_m.end() + 80]
                    if re.search(r"§", after):
                        return True

    lower_text = text.lower()
    chapter_tail = f"{_norm_num_token(str(chapter)).removesuffix('luku')} lukuun".lower()
    chapter_idx = lower_text.find(chapter_tail)
    if chapter_idx < 0:
        return False

    move_window = lower_text[max(0, chapter_idx - 80) : chapter_idx]
    if "siirret" not in move_window:
        return False

    prefix = text[max(0, chapter_idx - 200) : chapter_idx]
    if "§" not in prefix:
        return False

    norm_section = _norm_num_token(str(section))
    listed_sections = {
        _norm_num_token(match.group(0))
        for match in re.finditer(r"\d+\s*[a-z]?", prefix, flags=re.I)
    }
    if norm_section not in listed_sections:
        return False

    return re.search(
        rf"\b{section_list_pat}\b(?:\s+ja\s+\d+[a-z]?)?\s*§|\b{section_list_pat}\b(?=[^§]{{0,32}}§)",
        prefix,
        flags=re.I,
    ) is not None


def _johtolause_explicitly_mentions_chaptered_section_target(
    johto: str,
    chapter: str,
    section: str,
) -> bool:
    def _chapter_pat(label: str) -> str:
        norm = _norm_num_token(str(label))
        m = re.fullmatch(r"(\d+)([a-z])?", norm, flags=re.I)
        if not m:
            return re.escape(str(label))
        num, suffix = m.groups()
        return rf"{re.escape(num)}\s*{re.escape(suffix)}" if suffix else re.escape(num)

    def _section_pat(label: str) -> str:
        norm = _norm_num_token(str(label))
        m = re.fullmatch(r"(\d+)([a-z])?", norm, flags=re.I)
        if not m:
            return re.escape(str(label))
        num, suffix = m.groups()
        if suffix:
            return rf"{re.escape(num)}\s*{re.escape(suffix)}"
        return rf"{re.escape(num)}(?!\s*[a-z])"

    text = re.sub(r"\s+", " ", johto or "")
    chapter_pat = _chapter_pat(chapter)
    section_pat = _section_pat(section)
    return re.search(
        rf"\b{chapter_pat}\s+luvu?[n]?\s+{section_pat}\s*§",
        text,
        flags=re.I,
    ) is not None


def strip_unjustified_chapter_scope_from_unique_sections(
    los: List[_LegalOperation],
    johto: str,
    master: "ReplayState",
) -> List[_LegalOperation]:
    explicit_scope_notes = {
        "renumber_clause",
        "renumber_backref_clause",
    }

    def _master_has_any_chapters() -> bool:
        stack = [master.ir]
        while stack:
            node = stack.pop()
            if node.kind == IRNodeKind.CHAPTER and node.label:
                return True
            stack.extend(reversed(node.children))
        return False

    chapter_heading_anchors = {
        _norm_num_token(str(pd["chapter"]))
        for lo in los
        if (pd := _lo_path_dict(lo)).get("chapter") and "section" not in pd
    }

    master_has_any_chapters = _master_has_any_chapters()
    duplicate_labels = _duplicate_section_labels(master)
    result = []
    for lo in los:
        pd = _lo_path_dict(lo)
        section = pd.get("section")
        chapter = pd.get("chapter")
        part = pd.get("part")
        scope_tags = lo.provenance_tags
        scope_confidence = lo.scope_confidence
        special = lo.target.special
        facet = special.value if special is not None else None
        if not master_has_any_chapters:
            if not section or not chapter:
                result.append(lo)
                continue
            has_descendant_target = bool(
                pd.get("subsection")
                or pd.get("item")
                or pd.get("paragraph")
                or facet in {"intro", "heading"}
            )
            if (
                not has_descendant_target
                or explicit_scope_notes.intersection(scope_tags)
                or lo.move_clause_target_unit_kind in {"chapter", "part"}
                or _johtolause_explicitly_binds_chapter_section(johto, str(chapter), str(section))
                or _johtolause_explicitly_mentions_chaptered_section_target(johto, str(chapter), str(section))
            ):
                result.append(lo)
                continue
            section_norm = _norm_num_token(str(section))
            live_path = master.find_section_path(section_norm, None, str(part) if part else None)
            live_chapter = (
                next((label for kind, label in live_path if kind == "chapter"), None)
                if live_path is not None
                else None
            )
            if live_path is None or live_chapter is not None or section_norm in duplicate_labels:
                result.append(lo)
                continue
            lo_new = _lo_with_path_update(lo, chapter=None)
            result.append(lo_with_added_scope_tag(lo_new, "chapter_scope_stripped_flat_unique_descendant"))
            continue
        if not section or not chapter:
            result.append(lo)
            continue
        if (
            (
                "chapter_scope_from_explicit_chunk" in scope_tags
                or (
                    isinstance(scope_confidence, ScopeConfidence)
                    and scope_confidence.source is ScopeResolutionSource.EXPLICIT_CHUNK
                )
            )
            and lo.action is not StructuralAction.INSERT
            and not pd.get("subsection")
            and not pd.get("item")
            and not pd.get("paragraph")
            and facet not in {"intro", "heading"}
            # Preserve explicit_chunk scope UNLESS the section exists in master
            # within the same part scope but in a DIFFERENT chapter — which is the
            # signature of a johtolause carry-forward artifact (PEG grouping pulled
            # the section into a preceding chapter heading's chunk even though the
            # section lives elsewhere in the same part).
            # Do NOT strip when the section is absent from the stated part entirely
            # (e.g., it has moved to a different part); the retarget mechanism in
            # _compile_group relies on the explicit_chunk source flag to find the
            # section's new live path.
            and not _master_has_section_in_stated_part_different_chapter(
                master,
                str(section),
                str(chapter),
                part_label=str(pd["part"]) if pd.get("part") else None,
            )
        ):
            result.append(lo)
            continue
        if explicit_scope_notes.intersection(scope_tags):
            result.append(lo)
            continue
        # PRIMARY: grammar-authoritative move carrier. When the clause-level
        # grammar owned the johtolause it stamped the moved op with this carrier;
        # the same-label-move text anchor below is only the residue fallback for
        # ops the grammar move family did not own (see _SAME_LABEL_MOVE_CLAUSE_RE).
        if lo.move_clause_target_unit_kind in {"chapter", "part"}:
            result.append(lo)
            continue
        section_norm = _norm_num_token(str(section))
        if chapter and section_norm in _same_label_move_sections_for_chapter(johto, str(chapter)):
            result.append(lo)
            continue
        # For subsection-level INSERT ops (path includes 'subsection', 'item', or
        # 'paragraph'), the chapter comes from johtolause carry-forward, not from the
        # section being new there.  The section must already exist somewhere in the
        # master.  If it exists uniquely in a *different* chapter, strip the scope
        # before the johtolause-binding check, which is too broad for comma-separated
        # lists (it matches "1 lukuun...§N" even when §N is a subsection target in a
        # different chapter).
        if lo.action is StructuralAction.INSERT and (
            pd.get("subsection")
            or pd.get("item")
            or pd.get("paragraph")
            or facet in {"intro", "heading"}
        ):
            if _johtolause_explicitly_mentions_chaptered_section_target(
                johto,
                str(chapter),
                str(section),
            ):
                result.append(lo)
                continue
            exact_chapter = _unique_section_chapter(
                master,
                str(section),
                part_label=str(part) if part else None,
            )
            if exact_chapter is not None and _norm_num_token(str(chapter)) != exact_chapter:
                lo_new = _lo_with_path_update(lo, chapter=None)
                strip_tag = (
                    "chapter_scope_stripped_section_facet_insert"
                    if facet in {"intro", "heading"}
                    else "chapter_scope_stripped_subsection_insert"
                )
                result.append(lo_with_added_scope_tag(lo_new, strip_tag))
                continue
        if _johtolause_explicitly_binds_chapter_section(johto, str(chapter), str(section)):
            result.append(lo)
            continue
        if lo.action is StructuralAction.INSERT:
            # If the section doesn't yet exist in the op's stated chapter, this
            # INSERT is genuinely creating a new section there. A section that
            # happens to live in a *different* chapter (e.g. a VÄLIAIKAINEN
            # amendment that placed §4a in ch:15 while the current amendment
            # inserts §4a into ch:3) is not a reason to strip chapter scope.
            if not _master_has_section_in_chapter(
                master,
                str(section),
                str(chapter),
                part_label=str(part) if part else None,
            ):
                result.append(lo)
                continue
            exact_chapter = _unique_section_chapter(
                master,
                str(section),
                part_label=str(part) if part else None,
            )
            if exact_chapter is not None and _norm_num_token(str(chapter)) != exact_chapter:
                lo_new = _lo_with_path_update(lo, chapter=None)
                result.append(lo_with_added_scope_tag(lo_new, "chapter_scope_stripped_unique_section"))
                continue
            base_chapter = _unique_base_section_chapter(
                master,
                str(section),
                part_label=str(part) if part else None,
            )
            if base_chapter is not None and _norm_num_token(str(chapter)) != base_chapter:
                lo_new = _lo_with_path_update(lo, chapter=None)
                result.append(lo_with_added_scope_tag(lo_new, "chapter_scope_stripped_unique_section"))
                continue
        if _master_has_section_in_chapter(
            master,
            str(section),
            str(chapter),
            part_label=str(part) if part else None,
        ):
            result.append(lo)
            continue
        if _norm_num_token(str(chapter)) not in chapter_heading_anchors:
            result.append(lo)
            continue
        if section_norm in duplicate_labels:
            if lo.action is StructuralAction.INSERT:
                result.append(lo)
                continue
            lo_new = _lo_with_path_update(lo, chapter=None)
            result.append(
                lo_with_added_scope_tag(
                    lo_new,
                    "chapter_scope_stripped_duplicate_label_outside_stated_chapter",
                )
            )
            continue
        lo_new = _lo_with_path_update(lo, chapter=None)
        result.append(lo_with_added_scope_tag(lo_new, "chapter_scope_stripped_unique_section"))
    return result


def assign_chapter_scope_from_johtolause(
    los: List[_LegalOperation],
    johto: str,
    master: "ReplayState",
) -> List[_LegalOperation]:
    duplicate_labels = _duplicate_section_labels(master)
    chapter_section_index = _chapter_section_index(master)
    chunks = chapter_chunks_from_johtolause(johto)

    result = list(los)
    cursor = 0
    last_section_norm: Optional[str] = None
    last_section_chapter: Optional[str] = None
    for i, lo in enumerate(los):
        pd = _lo_path_dict(lo)
        if "section" not in pd or pd.get("chapter"):
            continue

        section_label = str(pd["section"])
        section_norm = _norm_num_token(section_label)
        part_label = str(pd["part"]) if pd.get("part") else None
        special = lo.target.special
        facet = special.value if special is not None else None
        if (
            last_section_norm == section_norm
            and last_section_chapter
            and _master_has_section_in_chapter(
                master,
                section_label,
                last_section_chapter,
                part_label=part_label,
                chapter_section_index=chapter_section_index,
            )
        ):
            lo_new = _lo_with_path_update(lo, chapter=last_section_chapter)
            result[i] = lo_with_added_scope_tag(lo_new, "chapter_scope_carry_forward")
            continue

        if lo.action is StructuralAction.INSERT:
            stem_host_chapter = infer_letter_suffix_section_chapter_from_stem_host(
                master,
                section_label,
                part_label=part_label,
                chapter_section_index=chapter_section_index,
            )
            if stem_host_chapter is not None:
                lo_new = _lo_with_path_update(lo, chapter=stem_host_chapter)
                result[i] = lo_with_scope_confidence(
                    lo_with_added_scope_tag(
                        lo_new,
                        "chapter_scope_from_letter_suffix_stem_host",
                    ),
                    ScopeConfidence(
                        tag="chapter_scope_from_letter_suffix_stem_host",
                        source=ScopeResolutionSource.LIVE_STEM_HOST,
                        confidence=ScopeResolutionConfidence.INFERRED,
                        resolved_chapter=stem_host_chapter,
                    ),
                )
                last_section_norm = section_norm
                last_section_chapter = stem_host_chapter
                continue

        if chunks:
            matched_chunk = False
            for idx in range(cursor, len(chunks)):
                chapter_label, chunk = chunks[idx]
                if _chapter_chunk_mentions_lo(chunk, lo):
                    if lo.action is StructuralAction.INSERT and (
                        pd.get("subsection")
                        or pd.get("item")
                        or pd.get("paragraph")
                        or facet in {"intro", "heading"}
                    ):
                        find_section_path = getattr(master, "find_section_path", None)
                        live_path = (
                            find_section_path(section_norm, None, part_label)
                            if callable(find_section_path)
                            else None
                        )
                        live_chapter = (
                            next((label for kind, label in live_path if kind == "chapter"), None)
                            if live_path is not None
                            else None
                        )
                        if (
                            live_path is not None
                            and live_chapter is None
                            and section_norm not in duplicate_labels
                            and not _johtolause_explicitly_mentions_chaptered_section_target(
                                johto,
                                chapter_label,
                                section_label,
                            )
                        ):
                            continue
                    if (
                        lo.action is not StructuralAction.INSERT
                        and not _master_has_section_in_chapter(
                            master,
                            section_label,
                            chapter_label,
                            part_label=part_label,
                            chapter_section_index=chapter_section_index,
                        )
                    ):
                        continue
                    lo_new = _lo_with_path_update(lo, chapter=chapter_label)
                    note = (
                        "chapter_scope_from_preamble"
                        if section_norm in duplicate_labels
                        else "chapter_scope_from_explicit_chunk"
                    )
                    result[i] = lo_with_scope_confidence(
                        lo_with_added_scope_tag(lo_new, note),
                        ScopeConfidence(
                            tag=note,
                            source=(
                                ScopeResolutionSource.PREAMBLE
                                if note == "chapter_scope_from_preamble"
                                else ScopeResolutionSource.EXPLICIT_CHUNK
                            ),
                            confidence=(
                                ScopeResolutionConfidence.INFERRED
                                if note == "chapter_scope_from_preamble"
                                else ScopeResolutionConfidence.EXPLICIT
                            ),
                            resolved_chapter=chapter_label,
                        ),
                    )
                    last_section_norm = section_norm
                    last_section_chapter = chapter_label
                    cursor = idx
                    matched_chunk = True
                    break
            if matched_chunk:
                continue

        if lo.action is StructuralAction.INSERT:
            exact_chapter = _unique_section_chapter(
                master,
                section_label,
                part_label=part_label,
                chapter_section_index=chapter_section_index,
            )
            if exact_chapter is not None:
                lo_new = _lo_with_path_update(lo, chapter=exact_chapter)
                result[i] = lo_with_added_scope_tag(lo_new, "chapter_scope_carry_forward")
                last_section_norm = section_norm
                last_section_chapter = exact_chapter
                continue
            base_chapter = _unique_base_section_chapter(
                master,
                section_label,
                part_label=part_label,
                chapter_section_index=chapter_section_index,
            )
            if base_chapter is not None:
                lo_new = _lo_with_path_update(lo, chapter=base_chapter)
                result[i] = lo_with_added_scope_tag(lo_new, "chapter_scope_carry_forward")
                last_section_norm = section_norm
                last_section_chapter = base_chapter
                continue

        if lo.action is not StructuralAction.INSERT:
            exact_chapter = _unique_section_chapter(
                master,
                section_label,
                part_label=part_label,
                chapter_section_index=chapter_section_index,
            )
            if (
                exact_chapter is not None
                and _master_has_section_in_chapter(
                    master,
                    section_label,
                    exact_chapter,
                    part_label=part_label,
                    chapter_section_index=chapter_section_index,
                )
            ):
                lo_new = _lo_with_path_update(lo, chapter=exact_chapter)
                result[i] = lo_with_added_scope_tag(
                    lo_new,
                    "chapter_scope_from_unique_live_section",
                )
                last_section_norm = section_norm
                last_section_chapter = exact_chapter
                continue
            stem_host_chapter = infer_letter_suffix_section_chapter_from_stem_host(
                master,
                section_label,
                part_label=part_label,
                chapter_section_index=chapter_section_index,
            )
            if stem_host_chapter is not None:
                lo_new = _lo_with_path_update(lo, chapter=stem_host_chapter)
                result[i] = lo_with_added_scope_tag(
                    lo_new,
                    "chapter_scope_from_letter_suffix_stem_host",
                )
                last_section_norm = section_norm
                last_section_chapter = stem_host_chapter
                continue

        last_section_norm = None
        last_section_chapter = None
    return result


def assign_scope_from_renumber_destinations(
    los: List[_LegalOperation],
) -> List[_LegalOperation]:
    """Carry section scope from immediately preceding renumber destinations.

    This handles clauses that first rename a section and then target the new
    label without restating its enclosing chapter/part, e.g. ``5 §:n numero
    159:ksi ... sekä lisätään 159 §:ään uusi 4 momentti``.
    """

    result = list(los)
    _assign_jolloin_renumber_scope_from_companion_targets(result)
    pending_section_destination: tuple[str, Optional[str], Optional[str]] | None = None

    for i, lo in enumerate(los):
        pd = _lo_path_dict(lo)
        section = pd.get("section")
        chapter = pd.get("chapter")
        part = pd.get("part")

        if section and lo.action is not StructuralAction.RENUMBER and pending_section_destination is not None:
            pending_section, carried_chapter, carried_part = pending_section_destination
            if _norm_num_token(section) != pending_section:
                pending_section_destination = None
            else:
                updates: dict[str, str] = {}
                scope_tags = list(lo.provenance_tags)

                if chapter is None and carried_chapter:
                    updates["chapter"] = carried_chapter
                    if "chapter_scope_carry_forward" not in scope_tags:
                        scope_tags.append("chapter_scope_carry_forward")
                if part is None and carried_part:
                    updates["part"] = carried_part
                    if "grouped_part_scope" not in scope_tags:
                        scope_tags.append("grouped_part_scope")
                if updates:
                    lo_new = lo
                    if "chapter" in updates:
                        lo_new = _lo_with_path_update(lo_new, chapter=updates["chapter"])
                    if "part" in updates:
                        lo_new = _lo_with_path_update(lo_new, part=updates["part"])
                    lo_new = dc_replace(lo_new, provenance_tags=tuple(scope_tags))
                    witness = ScopeConfidence(
                        tag=(
                            "grouped_part_scope"
                            if "part" in updates
                            else "chapter_scope_carry_forward"
                        ),
                        source=(
                            ScopeResolutionSource.GROUPED_PART
                            if "part" in updates
                            else ScopeResolutionSource.CARRY_FORWARD
                        ),
                        confidence=ScopeResolutionConfidence.INFERRED,
                        resolved_chapter=updates.get("chapter", chapter),
                    )
                    result[i] = lo_with_scope_confidence(
                        lo_with_added_scope_tag(lo_new, scope_tags[-1]),
                        witness,
                    )
                pending_section_destination = None

        destination = lo.destination
        if section and destination is not None:
            dest_pd = {k: v for k, v in destination.path}
            dest_section = dest_pd.get("section")
            if dest_section:
                pending_section_destination = (
                    _norm_num_token(dest_section),
                    chapter,
                    part,
                )

    return result


def _assign_jolloin_renumber_scope_from_companion_targets(
    los: List[_LegalOperation],
) -> None:
    """Repair ``jolloin`` companion renumber scope from its resolved insert.

    A clause such as ``2 lukuun uusi 7 a ja 9 a § ja 47 §:ään uusi 1 momentti,
    jolloin nykyinen 1 momentti siirtyy 2 momentiksi`` can leave the synthetic
    ``fi.jolloin_renumber`` companion with the chapter of the previous list
    while later scope/payload elaboration resolves the actual insert to the
    correct live section.  Use that same-batch exact leaf witness for the
    companion; do not consult raw prose or global live uniqueness here.
    """

    source_counts: dict[tuple[object | None, Optional[str]], int] = {}
    for lo in los:
        if lo.action is StructuralAction.RENUMBER and lo.witness_rule_id == "fi.jolloin_renumber":
            key = (lo.source, lo.group_id)
            source_counts[key] = source_counts.get(key, 0) + 1

    for i, lo in enumerate(tuple(los)):
        if lo.action is not StructuralAction.RENUMBER:
            continue
        if lo.witness_rule_id != "fi.jolloin_renumber":
            continue
        if source_counts.get((lo.source, lo.group_id), 0) != 1:
            continue
        if "chapter_scope_from_unique_live_section" in lo.provenance_tags:
            continue
        target_pd = _lo_path_dict(lo)
        section = target_pd.get("section")
        subsection = target_pd.get("subsection")
        if not section or not subsection:
            continue
        current_chapter = target_pd.get("chapter")
        current_part = target_pd.get("part")

        companion_scope = _jolloin_companion_scope(
            los[i + 1 :],
            section=section,
            subsection=subsection,
            current_chapter=current_chapter,
            current_part=current_part,
            source=lo.source,
            group_id=lo.group_id,
        )
        if companion_scope is None:
            continue
        chapter, part = companion_scope
        scoped = _lo_with_path_update(lo, chapter=chapter, part=part)
        los[i] = lo_with_added_scope_tag(
            scoped,
            "jolloin_renumber_scope_from_companion_target",
        )


def _jolloin_companion_scope(
    later_ops: Sequence[_LegalOperation],
    *,
    section: str,
    subsection: str,
    current_chapter: Optional[str],
    current_part: Optional[str],
    source: object | None,
    group_id: Optional[str],
) -> tuple[Optional[str], Optional[str]] | None:
    """Return the same-source same-leaf insert container scope for a jolloin pair."""

    matches: list[tuple[Optional[str], Optional[str]]] = []
    for candidate in later_ops:
        if source is not None and candidate.source is not None and candidate.source != source:
            break
        if group_id and candidate.group_id and candidate.group_id != group_id:
            continue
        pd = _lo_path_dict(candidate)
        if pd.get("section") != section or pd.get("subsection") != subsection:
            continue
        if candidate.action is not StructuralAction.INSERT:
            return None
        candidate_chapter = pd.get("chapter")
        candidate_part = pd.get("part")
        if candidate_chapter == current_chapter and candidate_part == current_part:
            return None
        if candidate_chapter is None and candidate_part is None:
            continue
        scope = (candidate_chapter, candidate_part)
        if scope not in matches:
            matches.append(scope)
    if len(matches) != 1:
        return None
    return matches[0]


def restrict_sec1_fallback_to_parent(sec1_text: str, parent_id: str) -> str:
    if not sec1_text or not parent_id:
        return sec1_text
    ref_re = _fi_statute_citation_re(parent_id)
    if ref_re is None:
        return sec1_text

    parts = [
        p.strip()
        for p in re.split(r"(?m)(?=^\s*\d+\)\s*)", sec1_text)
        if p.strip()
    ]
    if len(parts) <= 1:
        generic_refs = re.findall(r"\(\s*\d+\s*/\s*\d{2,4}\s*\)", sec1_text)
        sentence_parts = [p.strip() for p in _FI_SENTENCE_BOUNDARY_RE.split(sec1_text) if p.strip()]
        if len(sentence_parts) > 1:
            parts = sentence_parts
        elif len(generic_refs) > 1 and re.search(r"\bsekä\b", sec1_text, re.I):
            parts = [p.strip() for p in re.split(r"\bsekä\b", sec1_text) if p.strip()]
        else:
            parts = [p.strip() for p in re.split(r"(?<=;)", sec1_text) if p.strip()]

    matched = [part for part in parts if ref_re.search(part)]
    if not matched:
        return sec1_text

    # When a matched intro part ends with ":" it introduces a numbered list
    # (e.g. "kumotaan ... (912/1992): 1) ... 2) ...").  The numbered list
    # items won't themselves carry the statute reference but still belong to
    # the same kumotaan clause.  Collect all following numbered list items
    # (those matching "^\d+\)\s*") that follow a ":" intro.
    expanded: List[str] = list(matched)
    for i, part in enumerate(parts):
        if part not in expanded:
            continue
        if not part.rstrip().endswith(":"):
            continue
        # This is an intro ending in ":": pull in all immediately following
        # numbered-list items that are not already in expanded.
        for following in parts[i + 1:]:
            if following in expanded:
                break
            if not _FI_NUMBERED_ITEM_RE.match(following):
                break
            expanded.append(following)
    matched = expanded

    trimmed: List[str] = []
    for part in matched:
        cut = _FI_CUT_RE.search(part)
        piece = part[:cut.start()].strip() if cut else part.strip()
        trimmed.append(piece)

    lead_in_source = " ".join(trimmed) if any(_FI_SCOPE_VERB_RE.search(part) for part in trimmed) else sec1_text
    lead_in_match = re.match(r"(?is)^(.*?\b(?:kumotaan|muutetaan|lisätään|siirretään)\b[: ]*)", lead_in_source)
    lead_in = lead_in_match.group(1).strip() if lead_in_match else ""
    if lead_in:
        leadless = [
            re.sub(
                r"(?is)^.*?\b(?:kumotaan|muutetaan|lisätään|siirretään)\b[: ]*",
                "",
                part,
            ).strip()
            for part in trimmed
        ]
        body = " sekä ".join(part for part in leadless if part)
    else:
        body = " sekä ".join(part for part in trimmed if part)
    return f"{lead_in} {body}".strip() if lead_in else body


__all__ = [
    "duplicate_section_labels_across_chapters",
    "chapter_chunks_from_johtolause",
    "strip_unjustified_chapter_scope_from_unique_sections",
    "assign_chapter_scope_from_johtolause",
    "assign_scope_from_renumber_destinations",
    "fi_statute_citation_spans",
    "restrict_sec1_fallback_to_parent",
    "SourceDescendantScopeResult",
    "source_names_descendant_scope_below_section",
    "source_targets_plain_subsection_moment",
]

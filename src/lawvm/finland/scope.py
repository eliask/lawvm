"""Target/scope reasoning helpers for the Finnish frontend.

These helpers interpret chapter-scoped johtolause structure against the live
replay tree. They are separate from extraction and separate from deterministic
apply, so they belong in their own module rather than inside grafter.py.
"""
from __future__ import annotations

import re
from dataclasses import replace as dc_replace
from functools import lru_cache
from typing import TYPE_CHECKING, List, Optional, Set, Tuple

from lawvm.core.ir import IRNode
from lawvm.core.ir import LegalOperation as _LegalOperation
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.finland.helpers import _norm_num_token
from lawvm.finland.ops import (
    ScopeConfidence,
    _lo_path_dict,
    _lo_with_path_update,
    lo_with_added_scope_tag,
    lo_with_scope_confidence,
)

if TYPE_CHECKING:
    from lawvm.finland.statute import ReplayState


_SAME_LABEL_MOVE_CLAUSE_RE = re.compile(
    r"joista\s+([^§]{0,120})\s*§\s+(?:samalla\s+)?siirretään\s+(\d+\s*[a-z]?)\s+lukuun",
    flags=re.I,
)
_SINGULAR_SAME_LABEL_MOVE_CLAUSE_RE = re.compile(
    r"(\d+\s*[a-z]?)\s*§\s*,?\s*joka\s+(?:samalla\s+)?siirretään\s+(\d+\s*[a-z]?)\s+lukuun",
    flags=re.I,
)

# Module-scope constants for restrict_sec1_fallback_to_parent hot path
_FI_NUMBERED_ITEM_RE = re.compile(r"^\d+\)\s*", re.M)
_FI_CUT_RE = re.compile(r"\bsellais(?:ena|ina)\s+kuin\b|\bsiitä\s+on\b", re.I)


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
    matches = list(
        re.finditer(
            r"((?:\d+\s*,\s*)*\d+(?:\s+ja\s+\d+)?)\s+lu(?:ku|vun)\b",
            text,
            flags=re.I,
        )
    )
    chunks: List[Tuple[str, str]] = []
    for idx, match in enumerate(matches):
        cluster = match.group(1)
        labels = [
            token.strip().lower()
            for token in re.split(r"\s*,\s*|\s+ja\s+", cluster)
            if re.fullmatch(r"\d+[a-z]?", token.strip(), flags=re.I)
        ]
        if not labels:
            continue
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        chunks.append((labels[-1], text[start:end]))
    return chunks


def _iter_part_scoped_chapters(
    master: "ReplayState",
    *,
    part_label: str | None = None,
) -> list[IRNode]:
    wanted_part = _norm_num_token(part_label) if part_label else None
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
    return chapters


def _master_has_section_in_chapter(
    master: "ReplayState",
    section_label: str,
    chapter_label: str,
    *,
    part_label: str | None = None,
) -> bool:
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
) -> str | None:
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


def _unique_base_section_chapter(
    master: "ReplayState",
    section_label: str,
    *,
    part_label: str | None = None,
) -> str | None:
    norm = _norm_num_token(section_label)
    match = re.fullmatch(r"(\d+)([a-z])", norm, flags=re.I)
    if match is None:
        return None
    base_norm = match.group(1)
    chapters: set[str] = set()
    for node in _iter_part_scoped_chapters(master, part_label=part_label):
        for child in node.children:
            if child.kind is IRNodeKind.SECTION and child.label and _norm_num_token(child.label) == base_norm:
                chapters.add(_norm_num_token(node.label))
                break
    if len(chapters) != 1:
        return None
    return next(iter(chapters))


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

    def _section_in_chunk(target: str) -> bool:
        norm = _norm_num_token(target)
        m = re.fullmatch(r"(\d+)([a-z]?)", norm, flags=re.I)
        if not m:
            return re.search(rf"\b{re.escape(target)}\s*§", chunk, flags=re.I) is not None

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
    return _section_in_chunk(sec_label)


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

    if not _master_has_any_chapters():
        return los

    duplicate_labels = _duplicate_section_labels(master)
    result = []
    for lo in los:
        pd = _lo_path_dict(lo)
        section = pd.get("section")
        chapter = pd.get("chapter")
        part = pd.get("part")
        scope_tags = lo.provenance_tags
        scope_confidence = getattr(lo, "scope_confidence", None)
        special = lo.target.special
        facet = special.value if special is not None else None
        if not section or not chapter:
            result.append(lo)
            continue
        if (
            (
                "chapter_scope_from_explicit_chunk" in scope_tags
                or (
                    isinstance(scope_confidence, ScopeConfidence)
                    and scope_confidence.source == "explicit_chunk"
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
        if getattr(lo, "move_clause_target_unit_kind", None) in {"chapter", "part"}:
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
        if (
            last_section_norm == section_norm
            and last_section_chapter
            and _master_has_section_in_chapter(
                master,
                section_label,
                last_section_chapter,
                part_label=part_label,
            )
        ):
            lo_new = _lo_with_path_update(lo, chapter=last_section_chapter)
            result[i] = lo_with_added_scope_tag(lo_new, "chapter_scope_carry_forward")
            continue

        if lo.action is StructuralAction.INSERT:
            exact_chapter = _unique_section_chapter(
                master,
                section_label,
                part_label=part_label,
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
            )
            if base_chapter is not None:
                lo_new = _lo_with_path_update(lo, chapter=base_chapter)
                result[i] = lo_with_added_scope_tag(lo_new, "chapter_scope_carry_forward")
                last_section_norm = section_norm
                last_section_chapter = base_chapter
                continue

        if not chunks:
            last_section_norm = None
            last_section_chapter = None
            continue

        for idx in range(cursor, len(chunks)):
            chapter_label, chunk = chunks[idx]
            if _chapter_chunk_mentions_lo(chunk, lo):
                if (
                    lo.action is not StructuralAction.INSERT
                    and not _master_has_section_in_chapter(
                        master,
                        section_label,
                        chapter_label,
                        part_label=part_label,
                    )
                ):
                    continue
                lo_new = _lo_with_path_update(lo, chapter=chapter_label)
                note = (
                    "chapter_scope_from_johtolause"
                    if section_norm in duplicate_labels
                    else "chapter_scope_from_explicit_chunk"
                )
                result[i] = lo_with_scope_confidence(
                    lo_with_added_scope_tag(lo_new, note),
                    ScopeConfidence(
                        tag=note,
                        source=(
                            "johtolause"
                            if note == "chapter_scope_from_johtolause"
                            else "explicit_chunk"
                        ),
                        confidence=(
                            "inferred"
                            if note == "chapter_scope_from_johtolause"
                            else "explicit"
                        ),
                        resolved_chapter=chapter_label,
                    ),
                )
                last_section_norm = section_norm
                last_section_chapter = chapter_label
                cursor = idx
                break
        else:
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
                            "grouped_part"
                            if "part" in updates
                            else "carry_forward"
                        ),
                        confidence="inferred",
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
        if len(generic_refs) > 1 and re.search(r"\bsekä\b", sec1_text, re.I):
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

    lead_in_match = re.match(
        r"(?is)^(.*?\b(?:kumotaan|muutetaan|lisätään|siirretään)\b[: ]*)",
        sec1_text,
    )
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
    "restrict_sec1_fallback_to_parent",
]

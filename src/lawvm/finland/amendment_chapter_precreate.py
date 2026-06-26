"""Pre-create amendment-body chapters before Finland replay apply.

These helpers seed chapters found in amendment body XML so later section-level
operations have a structurally valid parent. They are replay preparation, not
operation parsing.
"""

from __future__ import annotations

import logging
import re
from lawvm.core.regex_safety import compile_classifier_regex
from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional

import lxml.etree as etree

from lawvm.core import tree_ops as _tops
from lawvm.core.ir import IRNode
from lawvm.core.mutation_boundary import TreePath
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.helpers import _norm_num_token, _roman_label_to_arabic

if TYPE_CHECKING:
    from lawvm.finland.ops import ResolvedOp
    from lawvm.finland.statute import ReplayState

logger = logging.getLogger(__name__)

FI_CHAPTER_MEMBERSHIP_MIGRATION_RULE_ID = "fi.chapter_membership_migration_from_source_starts"
_CHAPTER_HEADING_ANCHOR_RE = compile_classifier_regex(r"(?P<section>\d{1,4}[a-z]|\d{1,4}\s[a-z]|\d{1,4})\s{0,10}§:n\s+edelle\s+uusi\s+"
    r"(?P<chapter>\d{1,4}[a-z]|\d{1,4}\s[a-z]|\d{1,4})\s+luvun\s+otsikko", re.IGNORECASE, classifier_id="fi.amendment_chapter_precreate.chapter_heading_anchor_re")
_NEW_CHAPTER_AND_HEADING_ANCHOR_RE = compile_classifier_regex(r"uusi\s+(?P<chapter>\d{1,4}[a-z]|\d{1,4}\s[a-z]|\d{1,4})\s+luku\s+ja\s+"
    r"luvun\s+otsikko\s+"
    r"(?P<section>\d{1,4}[a-z]|\d{1,4}\s[a-z]|\d{1,4})\s{0,10}§:n\s+edelle", re.IGNORECASE, classifier_id="fi.amendment_chapter_precreate.new_chapter_and_heading_anchor_re")
_UNNUMBERED_CHAPTER_HEADING_ANCHOR_RE = compile_classifier_regex(r"(?P<section>\d{1,4}[a-z]|\d{1,4}\s[a-z]|\d{1,4})\s{0,10}§:n\s+edelle\s+uusi\s+"
    r"luvun\s+otsikko", re.IGNORECASE, classifier_id="fi.amendment_chapter_precreate.unnumbered_chapter_heading_anchor_re")
_UNNUMBERED_CHAPTER_HEADING_ANCHOR_PHRASE_RE = compile_classifier_regex(r"§:n\s+edelle\s+uusi\s+luvun\s+otsikko", re.IGNORECASE, classifier_id="fi.amendment_chapter_precreate.unnumbered_chapter_heading_anchor_phrase_re")
_ANCHOR_LIST_TOKEN_RE = re.compile(
    r"\d{1,4}\s{0,3}[a-z](?![a-z])|\d{1,4}|,|\bja\b",
    re.IGNORECASE,
)
_SINGULAR_SAME_LABEL_MOVE_CLAUSE_RE = compile_classifier_regex(r"(?P<section>(?:\d{1,4}\s{0,3}[a-z]|\d{1,4}))\s{0,3}§\s{0,3},?\s{0,3}"
    r"joka\s{1,8}(?:samalla\s{1,8}siirretään|siirretään)\s{1,8}"
    r"(?P<chapter>(?:\d{1,4}\s{0,3}[a-z]|\d{1,4}))\s{1,8}lukuun", re.IGNORECASE, classifier_id="fi.amendment_chapter_precreate.singular_same_label_move_clause_re")


@dataclass(frozen=True, slots=True)
class ChapterRef:
    """Created chapter identity with optional enclosing part scope."""

    part_label: str
    chapter_label: str


@dataclass(frozen=True, slots=True)
class ChapterMembershipMigration:
    """One source-owned move of an existing flat section into a new chapter."""

    section_label: str
    part_label: str
    chapter_label: str
    from_path: TreePath
    to_path: TreePath
    from_legal_path: TreePath
    to_legal_path: TreePath

    def as_detail(self) -> dict[str, object]:
        return {
            "rule_id": FI_CHAPTER_MEMBERSHIP_MIGRATION_RULE_ID,
            "family": "ontology_normalization",
            "section_label": self.section_label,
            "part_label": self.part_label,
            "chapter_label": self.chapter_label,
            "from_path": _path_text(self.from_path),
            "to_path": _path_text(self.to_path),
            "from_legal_path": _path_text(self.from_legal_path),
            "to_legal_path": _path_text(self.to_legal_path),
        }


@dataclass(frozen=True, slots=True)
class PrecreatedChaptersResult:
    """Result of pre-creating amendment-body chapters."""

    state: ReplayState
    created_refs: tuple[ChapterRef, ...]


@dataclass(frozen=True, slots=True)
class PrecreateApplyChaptersRequest:
    """Inputs for pre-creating amendment body chapters before apply."""

    state: ReplayState
    resolved: list[ResolvedOp]
    amendment_id: str
    vts_ops_enrich_done: bool
    johto: str = ""
    source_chapters: tuple[SourceChapter, ...] = ()
    source_pseudo_chapters: tuple[SourcePseudoChapter, ...] = ()


@dataclass(frozen=True, slots=True)
class PrecreateApplyChaptersResult:
    """State and chapter refs produced by pre-apply chapter creation."""

    state: ReplayState
    real_chapter_refs: tuple[ChapterRef, ...]
    pseudo_chapter_refs: tuple[ChapterRef, ...]
    membership_migrations: tuple[ChapterMembershipMigration, ...] = ()


@dataclass(frozen=True, slots=True)
class SourceChapter:
    part_label: str
    chapter_label: str
    section_labels: tuple[str, ...]
    num_text: str = ""
    heading_text: str = ""


@dataclass(frozen=True, slots=True)
class SourcePseudoChapter:
    part_label: str
    chapter_label: str
    num_text: str


def _tag(el: etree._Element) -> str:
    return el.tag.rsplit("}", 1)[-1] if isinstance(el.tag, str) else ""


def _path_text(path: TreePath) -> str:
    return "/".join(f"{kind}:{label}" for kind, label in path if label)


def _legal_path(path: TreePath) -> TreePath:
    return tuple(
        (kind, label)
        for kind, label in path
        if label and kind not in {"body", "hcontainer"}
    )


def _section_label_from_num_text(text: str) -> str:
    return _norm_num_token(text).removesuffix("§")


def _source_chapters(muutos_body: etree._Element) -> tuple[SourceChapter, ...]:
    chapters: list[SourceChapter] = []
    for ch_el in muutos_body.findall(".//{*}chapter"):
        ch_num = ch_el.find("{*}num")
        if ch_num is None or not ch_num.text:
            continue
        ch_label = _norm_num_token(ch_num.text).removesuffix("luku")
        if not ch_label:
            continue
        section_labels: list[str] = []
        for sec_el in ch_el.findall("{*}section"):
            sec_num = sec_el.find("{*}num")
            if sec_num is None or not sec_num.text:
                continue
            section_label = _section_label_from_num_text(sec_num.text)
            if section_label:
                section_labels.append(section_label)
        chapters.append(
            SourceChapter(
                part_label=_part_label_for_element(ch_el),
                chapter_label=ch_label,
                section_labels=tuple(section_labels),
                num_text=ch_num.text.strip(),
                heading_text=(
                    ch_heading.text.strip()
                    if (ch_heading := ch_el.find("{*}heading")) is not None
                    and ch_heading.text
                    else ""
                ),
            )
        )
    return tuple(chapters)


def source_chapters_from_tree(muutos_tree: etree._Element) -> tuple[SourceChapter, ...]:
    """Return typed real chapter declarations from an amendment source tree."""
    muutos_body = muutos_tree if _tag(muutos_tree) == "body" else muutos_tree.find(".//{*}body")
    if muutos_body is None:
        return ()
    return _source_chapters(muutos_body)


def source_pseudo_chapters_from_tree(muutos_tree: etree._Element) -> tuple[SourcePseudoChapter, ...]:
    """Return typed pseudo-chapter marker declarations from an amendment source tree."""
    muutos_body = muutos_tree if _tag(muutos_tree) == "body" else muutos_tree.find(".//{*}body")
    if muutos_body is None:
        return ()
    pseudo_chapters: list[SourcePseudoChapter] = []
    for ch_el in muutos_body.findall(".//{*}chapter"):
        for child in ch_el:
            if _tag(child) != "section":
                continue
            num_el = child.find("{*}num")
            if num_el is None or not num_el.text:
                continue
            raw_num = num_el.text.strip()
            if not _norm_num_token(raw_num).endswith("luku"):
                continue
            pseudo_label = _norm_num_token(raw_num).removesuffix("luku")
            if not pseudo_label:
                continue
            pseudo_chapters.append(
                SourcePseudoChapter(
                    part_label=_part_label_for_element(child),
                    chapter_label=pseudo_label,
                    num_text=raw_num,
                )
            )
    return tuple(pseudo_chapters)


def _chapter_heading_anchors(johto: str) -> dict[str, str]:
    anchors: dict[str, str] = {}
    if "luvun otsikko" not in johto or "§:n edelle" not in johto:
        return anchors
    for match in _CHAPTER_HEADING_ANCHOR_RE.finditer(johto):  # lawvm-regex: owning_parser chapter-heading anchor over the amendment's own johtolause (optional E3 johto-anchor consolidation)
        section_label = _section_label_from_num_text(match.group("section"))
        chapter_label = _norm_num_token(match.group("chapter")).removesuffix("luku")
        if section_label and chapter_label:
            anchors[chapter_label] = section_label
    for match in _NEW_CHAPTER_AND_HEADING_ANCHOR_RE.finditer(johto):  # lawvm-regex: owning_parser new-chapter+heading anchor over own johtolause (optional E3 johto-anchor consolidation)
        section_label = _section_label_from_num_text(match.group("section"))
        chapter_label = _norm_num_token(match.group("chapter")).removesuffix("luku")
        if section_label and chapter_label:
            anchors[chapter_label] = section_label
    return anchors


def _unnumbered_chapter_heading_anchor_labels(johto: str) -> frozenset[str]:
    if "luvun otsikko" not in johto or "§:n edelle" not in johto:
        return frozenset()
    labels = {
        _section_label_from_num_text(match.group("section"))
        for match in _UNNUMBERED_CHAPTER_HEADING_ANCHOR_RE.finditer(johto)  # lawvm-regex: owning_parser unnumbered chapter-heading anchor over own johtolause (optional E3 johto-anchor consolidation)
    }
    for match in _UNNUMBERED_CHAPTER_HEADING_ANCHOR_PHRASE_RE.finditer(johto):  # lawvm-regex: owning_parser unnumbered chapter-heading phrase anchor over own johtolause (optional E3 johto-anchor consolidation)
        labels.update(_chapter_heading_anchor_list_labels_before(johto[: match.start()]))
    return frozenset(label for label in labels if label)


def _chapter_heading_anchor_list_labels_before(prefix: str) -> tuple[str, ...]:
    stripped_prefix = prefix.rstrip()
    tokens = list(_ANCHOR_LIST_TOKEN_RE.finditer(stripped_prefix))  # lawvm-regex: owning_parser anchor-list tokenizer over a johtolause-derived prefix (optional E3 johto-anchor consolidation)
    if not tokens:
        return ()

    suffix_tokens: list[str] = []
    expected_end = len(stripped_prefix)
    for token in reversed(tokens):
        if stripped_prefix[token.end() : expected_end].strip():
            break
        suffix_tokens.append(token.group(0))
        expected_end = token.start()
    suffix_tokens.reverse()

    labels: list[str] = []
    expect_label = True
    saw_separator = False
    for token in suffix_tokens:
        token_norm = token.lower()
        is_separator = token_norm == "," or token_norm == "ja"
        if expect_label:
            if is_separator:
                return ()
            labels.append(_section_label_from_num_text(token))
            expect_label = False
            continue
        if not is_separator:
            return ()
        saw_separator = True
        expect_label = True
    if expect_label or not saw_separator:
        return ()
    return tuple(label for label in labels if label)


def _singular_same_label_move_starts(johto: str) -> dict[str, str]:
    """Return explicit ``section moved to chapter`` starts named in the source clause."""
    if "siirret" not in johto or "lukuun" not in johto:
        return {}
    starts: dict[str, str] = {}
    for match in _SINGULAR_SAME_LABEL_MOVE_CLAUSE_RE.finditer(johto):  # lawvm-regex: owning_parser same-label move-clause anchor over own johtolause (optional E3 johto-anchor consolidation)
        section_label = _section_label_from_num_text(match.group("section"))
        chapter_label = _norm_num_token(match.group("chapter")).removesuffix("luku")
        if section_label and chapter_label:
            starts[chapter_label] = section_label
    return starts


def _part_label_for_element(el: etree._Element) -> str:
    parent = el.getparent() if hasattr(el, "getparent") else None
    while parent is not None:
        if _tag(parent) == "part":
            part_num = parent.find("{*}num")
            if part_num is not None and part_num.text:
                raw = _norm_num_token(part_num.text.strip())
                raw = raw.removesuffix("osasto").removesuffix("osa")
                arabic = _roman_label_to_arabic(raw)
                return str(arabic) if arabic is not None else raw
        parent = parent.getparent() if hasattr(parent, "getparent") else None
    return ""


def _globally_unique_chapter_path(
    state: ReplayState,
    chapter_label: str,
) -> Optional[tuple[tuple[str, str], ...]]:
    """Return the chapter path iff exactly one chapter carries this label tree-wide.

    Statutes whose chapters are numbered continuously across parts (e.g.
    Kirkkolaki) keep each chapter label globally unique. When an amendment
    declares such a chapter under a relabelled part scope, the part-scoped
    lookup misses even though the chapter already exists under its original
    part — pre-creating it would duplicate the chapter. A single tree-wide
    match means the part-scoped declaration refers to that same chapter, so it
    is returned instead of seeding a duplicate.

    Per-part chapter-restart statutes (the same ``1 luku`` under several osat)
    yield more than one match and fall through to ``None``, preserving the
    legitimately distinct per-part chapters.
    """
    matches = _tops.find_all(
        state.ir, "chapter", chapter_label, label_index=state.provision_index
    )
    if len(matches) == 1:
        return tuple(matches[0])
    return None


def _find_existing_chapter_path(
    state: ReplayState,
    chapter_label: str,
    part_label: str,
) -> Optional[tuple[tuple[str, str], ...]]:
    if part_label:
        part_path = state.find("part", part_label)
        if part_path is not None:
            chapter_key = ("chapter", _tops.normalized_label_key(chapter_label))
            for chapter_path in state.provision_index.get(chapter_key, []):
                if len(chapter_path) > len(part_path) and chapter_path[: len(part_path)] == part_path:
                    return tuple(chapter_path)
        # The part-scoped lookup missed. Before seeding a duplicate, treat a
        # globally-unique existing chapter as the same unit relocated under a
        # relabelled part (continuous-numbering statutes); only fall through to
        # creation when the label is genuinely absent or per-part ambiguous.
        return _globally_unique_chapter_path(state, chapter_label)
    return state.find("chapter", chapter_label)


def state_has_scoped_chapter(
    state: ReplayState,
    part_label: str,
    chapter_label: str,
) -> bool:
    if part_label:
        part_path = state.find("part", part_label)
        if part_path is None:
            return False
        chapter_key = ("chapter", _tops.normalized_label_key(chapter_label))
        return any(
            len(chapter_path) > len(part_path)
            and chapter_path[: len(part_path)] == part_path
            for chapter_path in state.provision_index.get(chapter_key, [])
        )
    return state.find("chapter", chapter_label) is not None


def precreate_apply_chapters(
    request: PrecreateApplyChaptersRequest,
) -> PrecreateApplyChaptersResult:
    """Pre-create real and pseudo chapters needed by section-level apply ops."""
    if request.vts_ops_enrich_done:
        return PrecreateApplyChaptersResult(
            state=request.state,
            real_chapter_refs=(),
            pseudo_chapter_refs=(),
            membership_migrations=(),
        )
    source_chapters = request.source_chapters
    source_pseudo_chapters = request.source_pseudo_chapters
    if not source_chapters and not source_pseudo_chapters:
        return PrecreateApplyChaptersResult(
            state=request.state,
            real_chapter_refs=(),
            pseudo_chapter_refs=(),
            membership_migrations=(),
        )

    chapterization_labels = _chapterization_required_labels(
        request.state,
        source_chapters,
        request.johto,
    )
    required_real_chapters = {
        (
            _norm_num_token(rop.resolved_target_scope_part_label or "")
            if rop.resolved_target_scope_part_label
            else "",
            rop.resolved_target_chapter_label,
        )
        for rop in request.resolved
        if rop.target_unit_kind == "section"
        and rop.resolved_target_chapter_label
        and not state_has_scoped_chapter(
            request.state,
            (
                _norm_num_token(rop.resolved_target_scope_part_label or "")
                if rop.resolved_target_scope_part_label
                else ""
            ),
            rop.resolved_target_chapter_label,
        )
    }
    required_real_chapters.update(chapterization_labels)
    real_chapters = _pre_create_source_chapters(
        request.state,
        request.amendment_id,
        source_chapters,
        required_labels=required_real_chapters,
    )
    pseudo_chapters = _pre_create_source_pseudo_marker_chapters(
        real_chapters.state,
        request.amendment_id,
        source_pseudo_chapters,
    )
    migrated_state, membership_migrations = _migrate_flat_sections_into_source_chapters(
        pseudo_chapters.state,
        source_chapters,
        request.johto,
        created_refs=real_chapters.created_refs,
    )
    return PrecreateApplyChaptersResult(
        state=migrated_state,
        real_chapter_refs=real_chapters.created_refs,
        pseudo_chapter_refs=pseudo_chapters.created_refs,
        membership_migrations=membership_migrations,
    )


def _chapterization_required_labels(
    state: ReplayState,
    source_chapters: tuple[SourceChapter, ...],
    johto: str,
) -> set[tuple[str, str]]:
    anchors = _chapter_heading_anchors(johto)
    unnumbered_anchor_labels = _unnumbered_chapter_heading_anchor_labels(johto)
    if not anchors and not unnumbered_anchor_labels:
        return set()
    required: set[tuple[str, str]] = set()
    for chapter in source_chapters:
        source_start_label = chapter.section_labels[0] if chapter.section_labels else ""
        if (
            chapter.chapter_label not in anchors
            and source_start_label not in unnumbered_anchor_labels
        ):
            continue
        if state_has_scoped_chapter(state, chapter.part_label, chapter.chapter_label):
            continue
        required.add((chapter.part_label, chapter.chapter_label))
    return required


def _chapter_start_labels(
    *,
    source_chapters: tuple[SourceChapter, ...],
    johto: str,
    created_refs: tuple[ChapterRef, ...],
) -> tuple[tuple[str, str, str], ...]:
    created = {(ref.part_label, ref.chapter_label) for ref in created_refs}
    anchors = _chapter_heading_anchors(johto)
    unnumbered_anchor_labels = _unnumbered_chapter_heading_anchor_labels(johto)
    same_label_move_starts = _singular_same_label_move_starts(johto)
    if not created and not same_label_move_starts:
        return ()
    starts: list[tuple[str, str, str]] = []
    for chapter in source_chapters:
        chapter_ref = (chapter.part_label, chapter.chapter_label)
        move_start = same_label_move_starts.get(chapter.chapter_label)
        source_start = chapter.section_labels[0] if chapter.section_labels else ""
        matching_move_ref_count = sum(
            1
            for candidate in source_chapters
            if candidate.chapter_label == chapter.chapter_label
            and candidate.section_labels
            and candidate.section_labels[0] == move_start
        )
        explicit_existing_move = bool(
            move_start
            and source_start
            and move_start == source_start
            and matching_move_ref_count == 1
            and chapter_ref not in created
        )
        if chapter_ref not in created and not explicit_existing_move:
            continue
        start_label = anchors.get(chapter.chapter_label)
        if (
            start_label is None
            and chapter.section_labels
            and chapter.section_labels[0] in unnumbered_anchor_labels
        ):
            start_label = chapter.section_labels[0]
        if start_label is None and explicit_existing_move:
            start_label = move_start
        if start_label is None and chapter.section_labels:
            start_label = chapter.section_labels[0]
        if start_label:
            starts.append((chapter.part_label, chapter.chapter_label, start_label))
    return tuple(starts)


def _chapter_for_section_label(
    section_label: str,
    starts: tuple[tuple[str, str, str], ...],
) -> tuple[str, str] | None:
    section_key = _tops.default_label_sort_key(section_label)
    sorted_starts = sorted(starts, key=lambda item: _tops.default_label_sort_key(item[2]))
    selected: tuple[str, str] | None = None
    for part_label, chapter_label, start_label in sorted_starts:
        if _tops.default_label_sort_key(start_label) <= section_key:
            selected = (part_label, chapter_label)
        else:
            break
    return selected


def _find_direct_flat_section_path(
    tree: IRNode,
    section_label: str,
) -> TreePath | None:
    parent_path = _tops.find_provisions_parent(tree) or ()
    parent = _tops.resolve(tree, parent_path) if parent_path else tree
    if parent is None:
        return None
    for child in parent.children:
        if child.kind is not IRNodeKind.SECTION or not child.label:
            continue
        if _norm_num_token(child.label) == section_label:
            return tuple(parent_path) + (("section", child.label),)
    return None


def _find_unique_section_path_outside_chapter(
    state: ReplayState,
    section_label: str,
    *,
    destination_chapter: str,
    destination_part: str,
) -> TreePath | None:
    matches = [
        tuple(path)
        for path in state.provision_index.get(("section", _tops.normalized_label_key(section_label)), [])
    ]
    filtered: list[TreePath] = []
    for path in matches:
        labels = {kind: label for kind, label in path if label}
        if labels.get("chapter") == destination_chapter and labels.get("part", "") == destination_part:
            continue
        filtered.append(path)
    if len(filtered) == 1:
        return filtered[0]
    return None


def _first_section_label(node: IRNode) -> str:
    for child in node.children:
        if child.kind is IRNodeKind.SECTION and child.label:
            return _norm_num_token(child.label)
    return ""


def _existing_chapter_start_labels(tree: IRNode) -> tuple[str, ...]:
    starts: list[str] = []

    def walk(node: IRNode) -> None:
        if node.kind is IRNodeKind.CHAPTER:
            first = _first_section_label(node)
            if first:
                starts.append(first)
        for child in node.children:
            walk(child)

    walk(tree)
    return tuple(starts)


def _existing_chapter_start_successors(tree: IRNode) -> dict[str, str]:
    successors: dict[str, str] = {}
    ambiguous_starts: set[str] = set()

    def walk(node: IRNode) -> None:
        if node.kind is IRNodeKind.CHAPTER:
            section_labels = [
                _norm_num_token(child.label)
                for child in node.children
                if child.kind is IRNodeKind.SECTION and child.label
            ]
            if len(section_labels) >= 2:
                first, second = section_labels[0], section_labels[1]
                if first in successors and successors[first] != second:
                    ambiguous_starts.add(first)
                else:
                    successors[first] = second
        for child in node.children:
            walk(child)

    walk(tree)
    for label in ambiguous_starts:
        successors.pop(label, None)
    return successors


def _next_start_after(
    start_label: str,
    starts: tuple[tuple[str, str, str], ...],
    existing_chapter_starts: tuple[str, ...],
    existing_chapter_start_successors: dict[str, str],
) -> str:
    start_key = _tops.default_label_sort_key(start_label)
    candidates = [
        label
        for _part_label, _chapter_label, label in starts
        if _tops.default_label_sort_key(label) > start_key
    ]
    successor = existing_chapter_start_successors.get(start_label)
    if successor and _tops.default_label_sort_key(successor) > start_key:
        candidates.append(successor)
    candidates.extend(
        label
        for label in existing_chapter_starts
        if _tops.default_label_sort_key(label) > start_key
    )
    if not candidates:
        return ""
    return min(candidates, key=_tops.default_label_sort_key)


def _section_label_is_in_chapter_span(
    section_label: str,
    *,
    start_label: str,
    next_start_label: str,
) -> bool:
    section_key = _tops.default_label_sort_key(section_label)
    if section_key < _tops.default_label_sort_key(start_label):
        return False
    if next_start_label and section_key >= _tops.default_label_sort_key(next_start_label):
        return False
    return True


def _chapter_has_section(
    tree: IRNode,
    chapter_path: TreePath,
    section_label: str,
) -> bool:
    chapter = _tops.resolve(tree, chapter_path)
    if chapter is None:
        return False
    return any(
        child.kind is IRNodeKind.SECTION
        and child.label
        and _norm_num_token(child.label) == section_label
        for child in chapter.children
    )


def _flat_section_labels(tree: IRNode) -> tuple[str, ...]:
    parent_path = _tops.find_provisions_parent(tree) or ()
    parent = _tops.resolve(tree, parent_path) if parent_path else tree
    if parent is None:
        return ()
    labels = [
        _norm_num_token(child.label)
        for child in parent.children
        if child.kind is IRNodeKind.SECTION and child.label
    ]
    return tuple(label for label in labels if label)


def _migrate_flat_sections_into_source_chapters(
    state: ReplayState,
    source_chapters: tuple[SourceChapter, ...],
    johto: str,
    *,
    created_refs: tuple[ChapterRef, ...],
) -> tuple[ReplayState, tuple[ChapterMembershipMigration, ...]]:
    starts = _chapter_start_labels(
        source_chapters=source_chapters,
        johto=johto,
        created_refs=created_refs,
    )
    if not starts:
        return state, ()

    migrations: list[ChapterMembershipMigration] = []
    existing_chapter_starts = _existing_chapter_start_labels(state.ir)
    existing_chapter_start_successors = _existing_chapter_start_successors(state.ir)
    candidate_labels = {
        *(_flat_section_labels(state.ir)),
        *(
            _norm_num_token(label)
            for chapter in source_chapters
            for label in chapter.section_labels
            if label
        ),
    }
    for section_label in sorted(candidate_labels, key=_tops.default_label_sort_key):
        destination = _chapter_for_section_label(section_label, starts)
        if destination is None:
            continue
        part_label, chapter_label = destination
        start_label = next(
            start
            for start_part, start_chapter, start in starts
            if (start_part, start_chapter) == destination
        )
        if not _section_label_is_in_chapter_span(
            section_label,
            start_label=start_label,
            next_start_label=_next_start_after(
                start_label,
                starts,
                existing_chapter_starts,
                existing_chapter_start_successors,
            ),
        ):
            continue
        from_path = _find_direct_flat_section_path(state.ir, section_label)
        if from_path is None:
            from_path = _find_unique_section_path_outside_chapter(
                state,
                section_label,
                destination_chapter=chapter_label,
                destination_part=part_label,
            )
        if from_path is None:
            continue
        moving_node = _tops.resolve(state.ir, from_path)
        if moving_node is None:
            continue
        chapter_path = _find_existing_chapter_path(state, chapter_label, part_label)
        if chapter_path is None or _chapter_has_section(state.ir, tuple(chapter_path), section_label):
            continue
        without_section = _tops.remove_at_required(state.ir, from_path)
        after_remove = state.with_ir(without_section)
        chapter_path_after_remove = _find_existing_chapter_path(
            after_remove,
            chapter_label,
            part_label,
        )
        if chapter_path_after_remove is None:
            continue
        to_path = tuple(chapter_path_after_remove) + (("section", moving_node.label or ""),)
        inserted = _tops.insert_sorted_required(
            without_section,
            chapter_path_after_remove,
            moving_node,
        )
        state = state.with_ir(inserted)
        migrations.append(
            ChapterMembershipMigration(
                section_label=section_label,
                part_label=part_label,
                chapter_label=chapter_label,
                from_path=tuple(from_path),
                to_path=to_path,
                from_legal_path=_legal_path(tuple(from_path)),
                to_legal_path=_legal_path(to_path),
            )
        )
    return state, tuple(migrations)


def _chapter_insert_parent(
    state: ReplayState,
    *,
    part_label: str,
    chapter_label: str,
) -> tuple[tuple[str, str], ...]:
    part_path = state.find("part", part_label) if part_label else None
    if part_path is not None:
        return tuple(part_path)
    family = _tops.find_family(state.ir, "chapter", chapter_label)
    if family is not None:
        return family[:-1]
    return ()


def _pre_create_source_chapters(
    state: ReplayState,
    amendment_id: str,
    source_chapters: tuple[SourceChapter, ...],
    *,
    required_labels: Optional[set[tuple[str, str]]] = None,
) -> PrecreatedChaptersResult:
    """Pre-create real chapter nodes from typed amendment source declarations."""
    created_refs: List[ChapterRef] = []

    for source_chapter in source_chapters:
        ch_label = source_chapter.chapter_label
        if not ch_label:
            continue
        part_label = source_chapter.part_label
        chapter_ref = (part_label, ch_label)
        if required_labels is not None and chapter_ref not in required_labels:
            continue
        if _find_existing_chapter_path(state, ch_label, part_label) is not None:
            continue
        num_text = source_chapter.num_text or f"{ch_label} luku"
        ch_children: List[IRNode] = [IRNode(kind=IRNodeKind.NUM, text=num_text)]
        if source_chapter.heading_text:
            ch_children.append(IRNode(kind=IRNodeKind.HEADING, text=source_chapter.heading_text))
        new_ch = IRNode(kind=IRNodeKind.CHAPTER, label=ch_label, children=tuple(ch_children))
        state = state.with_ir(
            _tops.insert_sorted(
                state.ir,
                _chapter_insert_parent(state, part_label=part_label, chapter_label=ch_label),
                new_ch,
            )
        )
        created_refs.append(ChapterRef(part_label=part_label, chapter_label=ch_label))
        logger.debug("  [%s] uncovered chapter CREATE %s/%s", amendment_id, part_label or "-", ch_label)
    return PrecreatedChaptersResult(state=state, created_refs=tuple(created_refs))


def _pre_create_amendment_chapters(
    state: ReplayState,
    muutos_body: etree._Element,
    amendment_id: str,
    *,
    required_labels: Optional[set[tuple[str, str]]] = None,
) -> PrecreatedChaptersResult:
    """Pre-create real chapter nodes from amendment body XML.

    Returns the updated state and created chapter refs. ``part_label`` is
    empty for body-level chapters.
    """
    created_refs: List[ChapterRef] = []

    for ch_el in muutos_body.findall(".//{*}chapter"):
        ch_num = ch_el.find("{*}num")
        if ch_num is None or not ch_num.text:
            continue
        ch_label = _norm_num_token(ch_num.text).removesuffix("luku")
        if not ch_label:
            continue
        part_label = _part_label_for_element(ch_el)
        chapter_ref = (part_label, ch_label)
        if required_labels is not None and chapter_ref not in required_labels:
            continue
        if _find_existing_chapter_path(state, ch_label, part_label) is not None:
            continue
        ch_heading = ch_el.find("{*}heading")
        ch_children: List[IRNode] = [IRNode(kind=IRNodeKind.NUM, text=ch_num.text.strip())]
        if ch_heading is not None and ch_heading.text:
            ch_children.append(IRNode(kind=IRNodeKind.HEADING, text=ch_heading.text.strip()))
        new_ch = IRNode(kind=IRNodeKind.CHAPTER, label=ch_label, children=tuple(ch_children))
        state = state.with_ir(
            _tops.insert_sorted(
                state.ir,
                _chapter_insert_parent(state, part_label=part_label, chapter_label=ch_label),
                new_ch,
            )
        )
        created_refs.append(ChapterRef(part_label=part_label, chapter_label=ch_label))
        logger.debug("  [%s] uncovered chapter CREATE %s/%s", amendment_id, part_label or "-", ch_label)
    return PrecreatedChaptersResult(state=state, created_refs=tuple(created_refs))


def _pre_create_source_pseudo_marker_chapters(
    state: ReplayState,
    amendment_id: str,
    source_pseudo_chapters: tuple[SourcePseudoChapter, ...],
) -> PrecreatedChaptersResult:
    """Pre-create pseudo-marker chapters from typed amendment source declarations."""
    created_refs: List[ChapterRef] = []

    for source_pseudo in source_pseudo_chapters:
        pseudo_label = source_pseudo.chapter_label
        if not pseudo_label:
            continue
        part_label = source_pseudo.part_label
        if _find_existing_chapter_path(state, pseudo_label, part_label) is not None:
            continue
        raw_num = source_pseudo.num_text or f"{pseudo_label} luku"
        ch_children: List[IRNode] = [IRNode(kind=IRNodeKind.NUM, text=raw_num)]
        new_ch = IRNode(kind=IRNodeKind.CHAPTER, label=pseudo_label, children=tuple(ch_children))
        state = state.with_ir(
            _tops.insert_sorted(
                state.ir,
                _chapter_insert_parent(state, part_label=part_label, chapter_label=pseudo_label),
                new_ch,
            )
        )
        created_refs.append(ChapterRef(part_label=part_label, chapter_label=pseudo_label))
        logger.debug("  [%s] pseudo-chapter CREATE %s/%s", amendment_id, part_label or "-", pseudo_label)
    return PrecreatedChaptersResult(state=state, created_refs=tuple(created_refs))


def _pre_create_pseudo_marker_chapters(
    state: ReplayState,
    muutos_body: etree._Element,
    amendment_id: str,
) -> PrecreatedChaptersResult:
    """Pre-create letter-suffix chapters introduced via pseudo-marker sections.

    Some Finland amendment XML encodes a new sub-chapter (e.g. ``7 a luku``) as
    a ``<section><num>7 a luku</num>...</section>`` inside a regular chapter
    element rather than as a proper ``<chapter>`` element.
    """
    created_refs: List[ChapterRef] = []

    for ch_el in muutos_body.findall(".//{*}chapter"):
        for child in ch_el:
            child_tag = child.tag
            if not isinstance(child_tag, str):
                continue
            if etree.QName(child_tag).localname != "section":
                continue
            num_el = child.find("{*}num")
            if num_el is None or not num_el.text:
                continue
            raw_num = num_el.text.strip()
            if not _norm_num_token(raw_num).endswith("luku"):
                continue
            pseudo_label = _norm_num_token(raw_num).removesuffix("luku")
            if not pseudo_label:
                continue
            part_label = _part_label_for_element(child)
            if _find_existing_chapter_path(state, pseudo_label, part_label) is not None:
                continue
            ch_children: List[IRNode] = [IRNode(kind=IRNodeKind.NUM, text=raw_num)]
            new_ch = IRNode(kind=IRNodeKind.CHAPTER, label=pseudo_label, children=tuple(ch_children))
            state = state.with_ir(
                _tops.insert_sorted(
                    state.ir,
                    _chapter_insert_parent(state, part_label=part_label, chapter_label=pseudo_label),
                    new_ch,
                )
            )
            created_refs.append(ChapterRef(part_label=part_label, chapter_label=pseudo_label))
            logger.debug("  [%s] pseudo-chapter CREATE %s/%s", amendment_id, part_label or "-", pseudo_label)
    return PrecreatedChaptersResult(state=state, created_refs=tuple(created_refs))

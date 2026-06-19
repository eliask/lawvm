"""Pre-create amendment-body chapters before Finland replay apply.

These helpers seed chapters found in amendment body XML so later section-level
operations have a structurally valid parent. They are replay preparation, not
operation parsing.
"""

from __future__ import annotations

import logging
import re
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
_CHAPTER_HEADING_ANCHOR_RE = re.compile(
    r"(?P<section>\d{1,4}[a-z]|\d{1,4}\s[a-z]|\d{1,4})\s{0,10}§:n\s+edelle\s+uusi\s+"
    r"(?P<chapter>\d{1,4}[a-z]|\d{1,4}\s[a-z]|\d{1,4})\s+luvun\s+otsikko",
    re.IGNORECASE,
)
_UNNUMBERED_CHAPTER_HEADING_ANCHOR_RE = re.compile(
    r"(?P<section>\d{1,4}[a-z]|\d{1,4}\s[a-z]|\d{1,4})\s{0,10}§:n\s+edelle\s+uusi\s+"
    r"luvun\s+otsikko",
    re.IGNORECASE,
)


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
    muutos_tree: etree._Element
    amendment_id: str
    vts_ops_enrich_done: bool
    johto: str = ""


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
            )
        )
    return tuple(chapters)


def _chapter_heading_anchors(johto: str) -> dict[str, str]:
    anchors: dict[str, str] = {}
    if "luvun otsikko" not in johto or "§:n edelle" not in johto:
        return anchors
    for match in _CHAPTER_HEADING_ANCHOR_RE.finditer(johto):
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
        for match in _UNNUMBERED_CHAPTER_HEADING_ANCHOR_RE.finditer(johto)
    }
    return frozenset(label for label in labels if label)


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
        part_node = _tops.resolve(state.ir, part_path) if part_path is not None else None
        if part_path is not None and part_node is not None:
            chapter_path = _tops.find(part_node, "chapter", chapter_label)
            if chapter_path is not None:
                return part_path + chapter_path
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
        part_node = _tops.resolve(state.ir, part_path) if part_path else None
        if part_node is None:
            return False
        return _tops.find(part_node, "chapter", chapter_label) is not None
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
    muutos_body = request.muutos_tree.find(".//{*}body")
    if muutos_body is None:
        return PrecreateApplyChaptersResult(
            state=request.state,
            real_chapter_refs=(),
            pseudo_chapter_refs=(),
            membership_migrations=(),
        )

    chapterization_labels = _chapterization_required_labels(
        request.state,
        muutos_body,
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
    real_chapters = _pre_create_amendment_chapters(
        request.state,
        muutos_body,
        request.amendment_id,
        required_labels=required_real_chapters,
    )
    pseudo_chapters = _pre_create_pseudo_marker_chapters(
        real_chapters.state,
        muutos_body,
        request.amendment_id,
    )
    migrated_state, membership_migrations = _migrate_flat_sections_into_source_chapters(
        pseudo_chapters.state,
        muutos_body,
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
    muutos_body: etree._Element,
    johto: str,
) -> set[tuple[str, str]]:
    anchors = _chapter_heading_anchors(johto)
    unnumbered_anchor_labels = _unnumbered_chapter_heading_anchor_labels(johto)
    if not anchors and not unnumbered_anchor_labels:
        return set()
    required: set[tuple[str, str]] = set()
    for chapter in _source_chapters(muutos_body):
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
    muutos_body: etree._Element,
    johto: str,
    created_refs: tuple[ChapterRef, ...],
) -> tuple[tuple[str, str, str], ...]:
    created = {(ref.part_label, ref.chapter_label) for ref in created_refs}
    if not created:
        return ()
    anchors = _chapter_heading_anchors(johto)
    unnumbered_anchor_labels = _unnumbered_chapter_heading_anchor_labels(johto)
    starts: list[tuple[str, str, str]] = []
    for chapter in _source_chapters(muutos_body):
        chapter_ref = (chapter.part_label, chapter.chapter_label)
        if chapter_ref not in created:
            continue
        start_label = anchors.get(chapter.chapter_label)
        if (
            start_label is None
            and chapter.section_labels
            and chapter.section_labels[0] in unnumbered_anchor_labels
        ):
            start_label = chapter.section_labels[0]
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


def _next_start_after(
    start_label: str,
    starts: tuple[tuple[str, str, str], ...],
    existing_chapter_starts: tuple[str, ...],
) -> str:
    start_key = _tops.default_label_sort_key(start_label)
    candidates = [
        label
        for _part_label, _chapter_label, label in starts
        if _tops.default_label_sort_key(label) > start_key
    ]
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
    muutos_body: etree._Element,
    johto: str,
    *,
    created_refs: tuple[ChapterRef, ...],
) -> tuple[ReplayState, tuple[ChapterMembershipMigration, ...]]:
    starts = _chapter_start_labels(
        muutos_body=muutos_body,
        johto=johto,
        created_refs=created_refs,
    )
    if not starts:
        return state, ()

    migrations: list[ChapterMembershipMigration] = []
    existing_chapter_starts = _existing_chapter_start_labels(state.ir)
    candidate_labels = {
        *(_flat_section_labels(state.ir)),
        *(
            _norm_num_token(label)
            for chapter in _source_chapters(muutos_body)
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
            next_start_label=_next_start_after(start_label, starts, existing_chapter_starts),
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

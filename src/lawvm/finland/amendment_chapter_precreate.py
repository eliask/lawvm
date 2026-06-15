"""Pre-create amendment-body chapters before Finland replay apply.

These helpers seed chapters found in amendment body XML so later section-level
operations have a structurally valid parent. They are replay preparation, not
operation parsing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional

import lxml.etree as etree

from lawvm.core import tree_ops as _tops
from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.helpers import _norm_num_token, _roman_label_to_arabic

if TYPE_CHECKING:
    from lawvm.finland.ops import ResolvedOp
    from lawvm.finland.statute import ReplayState

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ChapterRef:
    """Created chapter identity with optional enclosing part scope."""

    part_label: str
    chapter_label: str


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


@dataclass(frozen=True, slots=True)
class PrecreateApplyChaptersResult:
    """State and chapter refs produced by pre-apply chapter creation."""

    state: ReplayState
    real_chapter_refs: tuple[ChapterRef, ...]
    pseudo_chapter_refs: tuple[ChapterRef, ...]


def _tag(el: etree._Element) -> str:
    return el.tag.rsplit("}", 1)[-1] if isinstance(el.tag, str) else ""


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
        )
    muutos_body = request.muutos_tree.find(".//{*}body")
    if muutos_body is None:
        return PrecreateApplyChaptersResult(
            state=request.state,
            real_chapter_refs=(),
            pseudo_chapter_refs=(),
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
    return PrecreateApplyChaptersResult(
        state=pseudo_chapters.state,
        real_chapter_refs=real_chapters.created_refs,
        pseudo_chapter_refs=pseudo_chapters.created_refs,
    )


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
    return (("body", ""),) if state.ir.kind is IRNodeKind.BODY else ()


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

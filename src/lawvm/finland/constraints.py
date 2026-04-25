"""Op constraint predicates — filter AmendmentOps before application.

Each constraint is ``(op, all_ops, ctx) → (keep: bool, reason: str)``.
Returning ``(False, reason)`` drops the op.  The ``_FilterCtx`` dataclass
bundles all ambient data constraints may inspect.

Also contains ``_find_muutos_node``, a shared lxml helper used both here
(by ``_c_false_positive_reference``) and in grafter's ``_find_muutos_ir``.

No XMLStatute dependency — depends only on lxml (read-only), IRNode, and
AmendmentOp.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

import lxml.etree as etree

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.payload_surface import TargetUnitKind
from lawvm.finland.ops import AmendmentOp, FailedOp
from lawvm.finland.payload_normalize import SubsectionSlotAssignmentResult, SubsectionSlotMap
from lawvm.finland.helpers import _norm_num_token
from lawvm.finland.replay_notices import replay_print

DEBUG = False  # set to True for per-constraint debug output

_PART_CROSS_HEADING_RE = re.compile(
    r"^(?P<label>[IVXLCDM]+|\d+[a-z]?)\s+(?:osa|osasto)$",
    flags=re.I,
)

_CONSTRAINT_REASON_CODES: dict[str, str] = {
    "_c_language_variant": "ELAB.REJECTED_LANGUAGE_VARIANT_ONLY",
    "_c_false_positive_reference": "ELAB.REJECTED_FALSE_POSITIVE_REFERENCE",
    "_c_no_source_payload": "ELAB.REJECTED_NO_SOURCE_PAYLOAD",
    "_c_no_heading_payload": "ELAB.REJECTED_NO_HEADING_PAYLOAD",
    "_c_whole_section_subsumes_children": "ELAB.REJECTED_WHOLE_SECTION_SUBSUMES_CHILDREN",
    "_c_replace_when_insert_same_paragraph": "ELAB.REJECTED_REPLACE_SHADOWED_BY_INSERT",
    "_c_language_variant_replace_shadowed_by_sparse_insert": "ELAB.REJECTED_LANGUAGE_VARIANT_REPLACE_SHADOWED_BY_SPARSE_INSERT",
    "_c_language_variant_plain_replace_shadowed_by_sparse_item_payload": "ELAB.REJECTED_LANGUAGE_VARIANT_PLAIN_REPLACE_SHADOWED_BY_SPARSE_ITEM_PAYLOAD",
    "_c_internal_list_update_not_whole_section_replace": "ELAB.REJECTED_INTERNAL_LIST_UPDATE",
    "_c_phantom_subsection": "ELAB.REJECTED_PHANTOM_SUBSECTION",
}


# ---------------------------------------------------------------------------
# Shared lxml helper (also re-exported into grafter for _find_muutos_ir)
# ---------------------------------------------------------------------------


def _find_muutos_node(
    muutos_tree: "etree._Element",
    target_unit_kind: TargetUnitKind,
    target_norm: str,
    target_chapter: Optional[str] = None,
    target_part: Optional[str] = None,
):
    """Return the lxml element in *muutos_tree* matching the target address.

    Used by both ``_find_muutos_ir`` (grafter) and ``_c_false_positive_reference``
    (constraints) to locate amendment section nodes.
    """
    target_unit_kind_text = str(target_unit_kind or "")
    _TAG = {"section": "section", "chapter": "chapter", "part": "part"}
    _SUFFIX = {"section": "", "chapter": "luku", "part": "osa"}
    wanted_tag = _TAG[target_unit_kind_text]
    suffix = _SUFFIX[target_unit_kind_text]

    def _num_norm(el):
        n = el.find("{*}num")
        return re.sub(r"[^\d\w]", "", n.text).lower() if n is not None and n.text else ""

    def _localname(el: "etree._Element") -> str:
        tag = el.tag
        return str(tag).rsplit("}", 1)[-1] if isinstance(tag, str) else ""

    def _child_num_text(el: "etree._Element") -> str:
        num_el = el.find("{*}num")
        return (num_el.text or "").strip() if num_el is not None and num_el.text else ""

    def _child_heading(el: "etree._Element") -> Optional["etree._Element"]:
        heading = el.find("{*}heading")
        return heading if heading is not None else el.find("heading")

    def _direct_text(el: "etree._Element") -> str:
        return " ".join("".join(str(_t) for _t in el.itertext()).split())

    def _qualified_tag(el: "etree._Element", local: str) -> str:
        tag = el.tag
        if isinstance(tag, str) and tag.startswith("{") and "}" in tag:
            ns = tag[1:].split("}", 1)[0]
            return f"{{{ns}}}{local}"
        return local

    def _normalize_chapter_label(raw: str) -> str:
        return _norm_num_token(raw).removesuffix("luku")

    def _normalize_part_label(raw: str) -> str:
        return _norm_num_token(raw).removesuffix("osasto").removesuffix("osa")

    def _part_label_from_cross_heading(el: "etree._Element") -> str:
        if _localname(el) != "crossHeading":
            return ""
        match = _PART_CROSS_HEADING_RE.match(_direct_text(el))
        if match is None:
            return ""
        return _normalize_part_label(match.group("label"))

    def _is_pseudo_chapter_marker_section(el: "etree._Element") -> bool:
        if _localname(el) != "section":
            return False
        raw_num = _child_num_text(el)
        return bool(raw_num) and _norm_num_token(raw_num).endswith("luku")

    def _chapter_has_pseudo_markers(chapter_el: "etree._Element") -> bool:
        return any(_is_pseudo_chapter_marker_section(child) for child in chapter_el)

    def _logical_chapter_segments(chapter_el: "etree._Element") -> list[tuple[str, str, Optional["etree._Element"], list["etree._Element"]]]:
        raw_num = _child_num_text(chapter_el)
        current_label = _normalize_chapter_label(raw_num)
        current_num_text = raw_num
        current_heading = _child_heading(chapter_el)
        current_children: list[etree._Element] = []
        segments: list[tuple[str, str, Optional[etree._Element], list[etree._Element]]] = []

        for child in chapter_el:
            if _localname(child) in {"num", "heading"}:
                continue
            if _is_pseudo_chapter_marker_section(child):
                segments.append((current_label, current_num_text, current_heading, current_children))
                current_num_text = _child_num_text(child)
                current_label = _normalize_chapter_label(current_num_text)
                current_heading = _child_heading(child)
                current_children = []
                continue
            current_children.append(child)

        segments.append((current_label, current_num_text, current_heading, current_children))
        return [segment for segment in segments if segment[0]]

    def _synthesized_chapter(
        template_chapter: "etree._Element",
        *,
        num_text: str,
        heading_el: Optional["etree._Element"],
        children: list["etree._Element"],
    ) -> "etree._Element":
        chapter = copy.deepcopy(template_chapter)
        for child in list(chapter):
            chapter.remove(child)
        num_source = template_chapter.find("{*}num")
        if num_source is not None:
            copied_num = copy.deepcopy(num_source)
            copied_num.text = num_text
            chapter.append(copied_num)
        if heading_el is not None:
            chapter.append(copy.deepcopy(heading_el))
        for child in children:
            chapter.append(copy.deepcopy(child))
        return chapter

    def _synthesized_part(
        template_parent: "etree._Element",
        *,
        num_text: str,
        heading_text: str,
        children: list["etree._Element"],
    ) -> "etree._Element":
        part = template_parent.makeelement(_qualified_tag(template_parent, "part"))
        num_el = template_parent.makeelement(_qualified_tag(template_parent, "num"))
        num_el.text = num_text
        part.append(num_el)
        if heading_text:
            heading_el = template_parent.makeelement(_qualified_tag(template_parent, "heading"))
            heading_el.text = heading_text
            part.append(heading_el)
        for child in children:
            part.append(copy.deepcopy(child))
        return part

    def _logical_part_nodes(root: "etree._Element") -> list["etree._Element"]:
        logical_parts: list[etree._Element] = []
        for parent in root.iter():
            current_label = ""
            current_num_text = ""
            current_heading = ""
            current_children: list[etree._Element] = []

            for child in parent:
                part_label = _part_label_from_cross_heading(child)
                if part_label:
                    if current_label and current_children:
                        logical_parts.append(
                            _synthesized_part(
                                parent,
                                num_text=current_num_text,
                                heading_text=current_heading,
                                children=current_children,
                            )
                        )
                    current_label = part_label
                    current_num_text = _direct_text(child)
                    current_heading = ""
                    current_children = []
                    continue

                if not current_label:
                    continue

                if _localname(child) == "crossHeading" and not current_children:
                    current_heading = _direct_text(child)
                    continue

                current_children.append(child)

            if current_label and current_children:
                logical_parts.append(
                    _synthesized_part(
                        parent,
                        num_text=current_num_text,
                        heading_text=current_heading,
                        children=current_children,
                    )
                )

        return logical_parts

    def _all_part_nodes(root: "etree._Element") -> list["etree._Element"]:
        return list(root.findall(".//{*}part")) + _logical_part_nodes(root)

    def _logical_chapter_node(chapter_el: "etree._Element", wanted_label: str) -> Optional["etree._Element"]:
        segments = _logical_chapter_segments(chapter_el)
        for label, num_text, heading_el, children in segments:
            if label != wanted_label:
                continue
            if not _chapter_has_pseudo_markers(chapter_el) and label == _normalize_chapter_label(_child_num_text(chapter_el)):
                return chapter_el
            return _synthesized_chapter(
                chapter_el,
                num_text=num_text,
                heading_el=heading_el,
                children=children,
            )
        return None

    def _find_section_in_logical_chapter(
        chapter_el: "etree._Element",
        wanted_chapter: str,
        wanted_section: str,
    ) -> tuple[bool, Optional["etree._Element"]]:
        logical = _logical_chapter_node(chapter_el, wanted_chapter)
        if logical is None:
            return False, None
        for sec in logical.findall("./{*}section"):
            if _num_norm(sec) == wanted_section:
                return True, sec
        return True, None

    # Part+chapter-scoped section search
    if target_unit_kind_text == "section" and target_part and target_chapter:
        found_part_chapter = False
        target_part_norm = _normalize_part_label(target_part)
        for part in _all_part_nodes(muutos_tree):
            part_num_el = part.find("{*}num")
            if _normalize_part_label(part_num_el.text if part_num_el is not None else "") != target_part_norm:
                continue
            for ch in part.findall("./{*}chapter"):
                found_part_chapter, sec = _find_section_in_logical_chapter(ch, target_chapter, target_norm)
                if found_part_chapter:
                    if sec is not None:
                        return sec
                    break
        return None

    # Chapter-scoped section search
    if target_unit_kind_text == "section" and target_chapter:
        found_target_chapter = False
        for ch in muutos_tree.findall(".//{*}chapter"):
            found_target_chapter, sec = _find_section_in_logical_chapter(ch, target_chapter, target_norm)
            if found_target_chapter:
                if sec is not None:
                    return sec
                break
        # If the source payload explicitly has the requested chapter but does
        # not contain the requested section under it, do not fall back to an
        # unrelated same-numbered section elsewhere in the amendment body.
        if found_target_chapter:
            return None

    if target_unit_kind_text == "chapter" and target_part:
        target_part_norm = _normalize_part_label(target_part)
        for part in _all_part_nodes(muutos_tree):
            part_num_el = part.find("{*}num")
            if _normalize_part_label(part_num_el.text if part_num_el is not None else "") != target_part_norm:
                continue
            for chapter in part.findall("./{*}chapter"):
                logical = _logical_chapter_node(chapter, target_norm)
                if logical is not None:
                    return logical
        return None

    if target_unit_kind_text == "part":
        target_part_norm = _normalize_part_label(target_norm)
        for part in _all_part_nodes(muutos_tree):
            part_num_el = part.find("{*}num")
            if _normalize_part_label(part_num_el.text if part_num_el is not None else "") == target_part_norm:
                return part

    if target_unit_kind_text == "chapter":
        for chapter in muutos_tree.findall(".//{*}chapter"):
            logical = _logical_chapter_node(chapter, target_norm)
            if logical is not None:
                return logical

    nodes = muutos_tree.findall(f".//{{*}}{wanted_tag}")
    # Exact match (with kind suffix)
    for node in nodes:
        if _num_norm(node) == f"{target_norm}{suffix}":
            return node
    # Letter-suffix fallback: 5a → try base 5
    if target_unit_kind_text == "section":
        m = re.match(r"^(\d+)[a-z]$", target_norm)
        if m:
            for node in nodes:
                if _num_norm(node) == m.group(1):
                    return node
    # Only sections get the single-node fallback, and only when the lone node is
    # unlabeled. If the source payload explicitly says `9 a §`, it is too dangerous
    # to silently reuse it for an unrelated target such as `4 §`: mixed
    # language-variant amendments can otherwise bind the wrong payload and replay
    # foreign text into the target section.
    if target_unit_kind_text == "section" and len(nodes) == 1:
        sole_norm = _num_norm(nodes[0])
        return nodes[0] if not sole_norm else None
    return None


# ---------------------------------------------------------------------------
# Johtolause predicates (needed by _FilterCtx lazy properties)
# ---------------------------------------------------------------------------


def _is_language_variant_only_johtolause(johto: str) -> bool:
    """Return True if the johtolause only amends a Swedish-language variant."""
    text = (johto or "").lower()
    return "ruotsinkielinen sanamuoto" in text or "svenskspråkiga lydelse" in text


def _johtolause_mentions_section(johto: str, section_label: str) -> bool:
    """Return True if *johto* explicitly mentions *section_label* (§ reference)."""
    # johto is already Zs-normalized by _normalize_fi_parse_text upstream.
    text = johto or ""
    m = re.fullmatch(r"(\d+)([a-z]?)", section_label, flags=re.I)
    if m:
        label_pat = rf"{re.escape(m.group(1))}\s*{re.escape(m.group(2))}" if m.group(2) else re.escape(m.group(1))
    else:
        label_pat = re.escape(section_label)
    if re.search(rf"(?<!\d){label_pat}\s*§", text, flags=re.I) is not None:
        return True
    m2 = re.search(rf"(?<!\d){label_pat}\s*(?:[,;—–\-]|\bja\b|\bsekä\b)", text, flags=re.I)
    if m2 and "§" in text[m2.start() :]:
        return True
    return False


# ---------------------------------------------------------------------------
# Filter context
# ---------------------------------------------------------------------------


@dataclass
class _FilterCtx:
    """Ambient data shared by all constraint predicates for a single group."""

    muutos_ir: Optional[IRNode]
    muutos_tree: "etree._Element"
    johto: str
    slot_assignment: Optional[SubsectionSlotAssignmentResult] = None
    subsec_map: Optional[SubsectionSlotMap] = None
    _has_heading: Optional[bool] = None
    _is_lang_variant: Optional[bool] = None

    def __post_init__(self) -> None:
        if self.subsec_map is None and self.slot_assignment is not None:
            self.subsec_map = self.slot_assignment.subsec_map

    def mapped_subsection_for(self, op: AmendmentOp) -> Optional[IRNode]:
        if self.slot_assignment is not None:
            return self.slot_assignment.resolve_for_op(op)
        return None

    def has_mapped_subsection(self, op: AmendmentOp) -> bool:
        return self.mapped_subsection_for(op) is not None

    @property
    def has_subsection_mapping(self) -> bool:
        return self.slot_assignment is not None

    @property
    def has_amendment_section(self) -> bool:
        return self.muutos_ir is not None

    @property
    def has_heading(self) -> bool:
        if self._has_heading is None:
            self._has_heading = self.muutos_ir is not None and any(c.kind == IRNodeKind.HEADING for c in self.muutos_ir.children)
        return self._has_heading

    @property
    def is_lang_variant(self) -> bool:
        if self._is_lang_variant is None:
            self._is_lang_variant = _is_language_variant_only_johtolause(self.johto)
        return self._is_lang_variant


# ---------------------------------------------------------------------------
# Constraint predicates
# ---------------------------------------------------------------------------


def _c_language_variant(op: AmendmentOp, all_ops: List[AmendmentOp], ctx: _FilterCtx) -> Tuple[bool, str]:
    """Drop section REPLACE/INSERT ops that only amend another language variant."""
    if ctx.has_amendment_section or not ctx.is_lang_variant:
        return True, ""
    if op.op_type in {"REPEAL", "RENUMBER"} or op.target_unit_kind != "section":
        return True, ""
    if op.target_special:
        return True, ""
    if op.target_section:
        return False, "language-variant-only, no fin payload"
    return True, ""


def _c_false_positive_reference(op: AmendmentOp, all_ops: List[AmendmentOp], ctx: _FilterCtx) -> Tuple[bool, str]:
    """Drop ops that reflect internal cross-references, not real targets."""
    if ctx.has_amendment_section:
        return True, ""
    target_section = op.target_section
    if (
        op.target_unit_kind == "section"
        and op.op_type != "REPEAL"
        and target_section
        and not op.target_special
        and _find_muutos_node(ctx.muutos_tree, op.target_unit_kind, _norm_num_token(target_section)) is None
        and not _johtolause_mentions_section(ctx.johto, target_section)
    ):
        return False, "cross-reference false positive"
    return True, ""


def _c_no_source_payload(op: AmendmentOp, all_ops: List[AmendmentOp], ctx: _FilterCtx) -> Tuple[bool, str]:
    """Drop replace/insert section ops when no amendment section node exists."""
    if ctx.has_amendment_section:
        return True, ""
    if op.target_unit_kind == "section" and op.op_type in ("REPLACE", "INSERT"):
        return False, "no source payload node"
    return True, ""


def _c_no_heading_payload(op: AmendmentOp, all_ops: List[AmendmentOp], ctx: _FilterCtx) -> Tuple[bool, str]:
    """Drop heading ops when amendment section has no <heading>."""
    if not ctx.has_amendment_section:
        return True, ""
    if (
        op.target_unit_kind == "section"
        and op.op_type in ("REPLACE", "INSERT")
        and op.target_special == "otsikko"
        and not ctx.has_heading
    ):
        return False, "no heading payload"
    return True, ""


def _c_whole_section_subsumes_children(
    op: AmendmentOp, all_ops: List[AmendmentOp], ctx: _FilterCtx
) -> Tuple[bool, str]:
    """Drop child ops when a whole-section REPLACE already covers the section.

    This covers paragraph/item children, whole-section INSERTs that would be
    redundant under the same section, and explicit heading/intro payloads.

    Explicit child repeals are different: even under a same-group section
    replace, they carry independent executable intent and must not disappear
    silently.
    """
    if not ctx.has_amendment_section:
        return True, ""
    if (
        op.target_unit_kind == "section"
        and op.op_type == "REPEAL"
        and (op.target_paragraph is not None or op.target_item is not None)
        and not op.target_special
    ):
        return True, ""
    # Sparse whole-section payloads are not the same as a dense full-section
    # replace.  If the amendment body carries omission markers, keep child
    # section ops alive so the later sparse merge machinery can preserve
    # explicit plain moments instead of letting the broad replace subsume them.
    if ctx.muutos_ir is not None and any(child.kind == IRNodeKind.OMISSION for child in ctx.muutos_ir.children):
        return True, ""
    has_mapped_child_ops = any(
        other.target_unit_kind == "section"
        and other.op_type in ("REPLACE", "REPEAL", "INSERT")
        and (other.target_paragraph is not None or other.target_item is not None)
        and not other.target_special
        and ctx.has_mapped_subsection(other)
        for other in all_ops
    )
    if has_mapped_child_ops:
        return True, ""
    has_section_facet_replace = any(
        other.target_unit_kind == "section"
        and other.op_type == "REPLACE"
        and (other.target_special in {"otsikko", "johd"} or getattr(other, "preserve_explicit_heading_facet", False))
        for other in all_ops
    )
    if has_section_facet_replace:
        return True, ""
    has_whole = any(
        o.target_unit_kind == "section"
        and o.op_type == "REPLACE"
        and not o.target_paragraph
        and not o.target_item
        and not o.target_special
        for o in all_ops
    )
    if not has_whole:
        return True, ""
    if ctx.muutos_ir is not None:
        payload_sub_labels = {
            str(child.label).strip()
            for child in ctx.muutos_ir.children
            if child.kind == IRNodeKind.SUBSECTION and child.label
        }
        if payload_sub_labels and any(
            other.target_unit_kind == "section"
            and other.op_type == "RENUMBER"
            and other.target_paragraph is not None
            and not other.target_item
            and not other.target_special
            and str(other.target_paragraph) in payload_sub_labels
            for other in all_ops
        ):
            return True, ""
        if payload_sub_labels and any(
            other.target_unit_kind == "section"
            and other.op_type == "INSERT"
            and other.target_paragraph is not None
            and not other.target_item
            and not other.target_special
            and str(other.target_paragraph) not in payload_sub_labels
            for other in all_ops
        ):
            return True, ""
    has_item_level_children = any(
        o.target_unit_kind == "section"
        and o.op_type == "REPLACE"
        and o.target_paragraph is not None
        and bool(o.target_item)
        and not o.target_special
        for o in all_ops
    )
    if (
        op.target_unit_kind == "section"
        and op.op_type in ("REPLACE", "REPEAL", "INSERT")
        and (op.target_paragraph is not None or op.target_item is not None)
        and not op.target_special
    ):
        if has_item_level_children and op.op_type in ("REPLACE", "INSERT"):
            return True, ""
        return False, "covered by whole-section replace"
    if (
        op.target_unit_kind == "section"
        and op.op_type == "REPLACE"
        and op.target_special in {"otsikko", "johd"}
    ):
        return False, "covered by whole-section replace"
    return True, ""


def _c_replace_when_insert_same_paragraph(
    op: AmendmentOp, all_ops: List[AmendmentOp], ctx: _FilterCtx
) -> Tuple[bool, str]:
    """Drop REPLACE when a same-paragraph INSERT is confirmed to carry the same slot.

    If subsection mapping is not yet available, collapse is deferred so the
    later payload-to-slot assignment can decide whether the REPLACE and INSERT
    really share a subsection.
    """
    if op.op_type != "REPLACE" or op.target_paragraph is None or op.target_item:
        return True, ""
    key = (op.target_unit_kind, op.target_section, op.target_paragraph, op.target_item)
    same_target_inserts = [
        o
        for o in all_ops
        if (
            o.op_type == "INSERT"
            and o.target_paragraph is not None
            and not o.target_item
            and (o.target_unit_kind, o.target_section, o.target_paragraph, o.target_item) == key
        )
    ]
    if not same_target_inserts:
        return True, ""

    # When a real amendment body exists, defer this collapse until subsection
    # payload mapping has been built. Some clauses intentionally pair
    # `REPLACE N mom` with `INSERT N mom`, where the insert occupies the new
    # slot and the replaced live moment shifts forward to N+1.
    if ctx.has_amendment_section and not ctx.has_subsection_mapping:
        return True, ""

    if ctx.has_subsection_mapping:
        replace_sub = ctx.mapped_subsection_for(op)
        if replace_sub is None:
            return True, ""
        for insert_op in same_target_inserts:
            insert_sub = ctx.mapped_subsection_for(insert_op)
            if insert_sub is replace_sub:
                return False, "same-paragraph INSERT exists"
        return True, ""

    for o in all_ops:
        if o in same_target_inserts:
            return False, "same-paragraph INSERT exists"
    return True, ""


def _c_language_variant_replace_shadowed_by_sparse_insert(
    op: AmendmentOp, all_ops: List[AmendmentOp], ctx: _FilterCtx
) -> Tuple[bool, str]:
    """Drop subsection replaces when a mixed language-variant clause only carries insert payload.

    Some amendment clauses combine:
    - a Swedish-language wording change for an existing moment
    - a real Finnish insert for a later new moment in the same section

    The Finnish body can then contain just:
    - omission
    - one sparse subsection payload

    In that shape, binding the lone subsection to the earlier REPLACE duplicates
    the new inserted text into the replaced live moment. Treat the payload as
    belonging only to the insert and drop the shadowed REPLACE.
    """
    if (
        not ctx.has_amendment_section
        or op.op_type != "REPLACE"
        or op.target_unit_kind != "section"
        or op.target_paragraph is None
        or op.target_item
        or op.target_special
    ):
        return True, ""
    if not ctx.is_lang_variant:
        return True, ""

    insert_targets = [
        other.target_paragraph
        for other in all_ops
        if (
            other.op_type == "INSERT"
            and other.target_unit_kind == op.target_unit_kind
            and other.target_section == op.target_section
            and other.target_chapter == op.target_chapter
            and other.target_paragraph is not None
            and not other.target_item
            and not other.target_special
        )
    ]
    if not insert_targets or op.target_paragraph >= min(insert_targets):
        return True, ""

    muutos_children = ctx.muutos_ir.children if ctx.muutos_ir is not None else []
    amend_subs = [child for child in muutos_children if child.kind == IRNodeKind.SUBSECTION]
    has_omission = any(child.kind == IRNodeKind.OMISSION for child in muutos_children)
    if has_omission and len(amend_subs) == 1:
        return False, "language-variant replace shadowed by sparse insert payload"
    return True, ""


def _c_language_variant_plain_replace_shadowed_by_sparse_item_payload(
    op: AmendmentOp, all_ops: List[AmendmentOp], ctx: _FilterCtx
) -> Tuple[bool, str]:
    """Drop language-variant context replaces that hijack a sparse item payload."""
    if (
        not ctx.has_amendment_section
        or not ctx.is_lang_variant
        or not ctx.has_subsection_mapping
        or op.op_type != "REPLACE"
        or op.target_unit_kind != "section"
        or op.target_item
    ):
        return True, ""
    if op.target_paragraph is None and op.target_special != "johd":
        return True, ""
    if op.target_special and op.target_special != "johd":
        return True, ""

    muutos_children = ctx.muutos_ir.children if ctx.muutos_ir is not None else ()
    amend_subs = [child for child in muutos_children if child.kind == IRNodeKind.SUBSECTION]
    if not amend_subs:
        return True, ""

    mapped_sub = ctx.mapped_subsection_for(op)
    if mapped_sub is None:
        return True, ""

    same_scope_context_ops = [
        other
        for other in all_ops
        if (
            other.op_type == "REPLACE"
            and other.target_unit_kind == op.target_unit_kind
            and other.target_section == op.target_section
            and other.target_chapter == op.target_chapter
            and not other.target_item
            and (
                (other.target_paragraph is not None and not other.target_special)
                or other.target_special == "johd"
            )
            and ctx.mapped_subsection_for(other) is mapped_sub
        )
    ]
    if not same_scope_context_ops:
        return True, ""
    if op.target_special != "johd" and len(same_scope_context_ops) <= 1:
        return True, ""

    shared_item_ops = [
        other
        for other in all_ops
        if (
            other.op_type == "REPLACE"
            and other.target_unit_kind == op.target_unit_kind
            and other.target_section == op.target_section
            and other.target_chapter == op.target_chapter
            and other.target_paragraph is not None
            and bool(other.target_item)
            and not other.target_special
            and ctx.mapped_subsection_for(other) is mapped_sub
        )
    ]
    if not shared_item_ops:
        return True, ""

    return False, "language-variant context replace shadowed by sparse item payload"


def _c_internal_list_update_not_whole_section_replace(
    op: AmendmentOp, all_ops: List[AmendmentOp], ctx: _FilterCtx
) -> Tuple[bool, str]:
    """Drop literal whole-section replaces for `§:ssä olevaa ... luetteloa` clauses."""
    if (
        op.op_type != "REPLACE"
        or op.target_unit_kind != "section"
        or op.target_paragraph is not None
        or op.target_item
        or op.target_special
    ):
        return True, ""
    johto = (ctx.johto or "").lower().replace("\xa0", " ")
    section_pat = re.escape(str(op.target_section))
    if re.search(rf"(?<!\d){section_pat}\s*§\s*:ssä\s+olevaa\b", johto) and "luetteloa" in johto:
        return False, "internal section list update is not a safe whole-section replace"
    return True, ""


def _c_phantom_subsection(op: AmendmentOp, all_ops: List[AmendmentOp], ctx: _FilterCtx) -> Tuple[bool, str]:
    """Drop subsection replace/insert ops with no mapped payload."""
    if not ctx.has_subsection_mapping:
        return True, ""
    if (
        op.target_unit_kind == "section"
        and op.op_type in ("REPLACE", "INSERT")
        and op.target_paragraph is not None
        and not op.target_special
        and not ctx.has_mapped_subsection(op)
    ):
        return False, "missing subsection payload"
    return True, ""


# The constraint list — order doesn't matter (all are evaluated independently)
_OP_CONSTRAINTS = [
    _c_language_variant,
    _c_false_positive_reference,
    _c_no_source_payload,
    _c_no_heading_payload,
    _c_whole_section_subsumes_children,
    _c_replace_when_insert_same_paragraph,
    _c_language_variant_replace_shadowed_by_sparse_insert,
    _c_language_variant_plain_replace_shadowed_by_sparse_item_payload,
    _c_internal_list_update_not_whole_section_replace,
    _c_phantom_subsection,
]


def _filter_ops_by_constraints(
    group_ops: List[AmendmentOp],
    ctx: _FilterCtx,
    rejected_ops_out: List[FailedOp],
) -> List[AmendmentOp]:
    """Apply all constraints in one pass, dropping ops that violate any."""
    filtered: List[AmendmentOp] = []
    for op in group_ops:
        keep = True
        for constraint in _OP_CONSTRAINTS:
            ok, reason = constraint(op, group_ops, ctx)
            if not ok:
                if DEBUG:
                    replay_print(f"  [{op.source_statute}] {op.description()} → SKIP ({reason})")
                rejected_ops_out.append(
                    FailedOp.from_scope(
                        amendment_id=op.source_statute or "",
                        description=op.description(),
                        reason=f"{constraint.__name__}: {reason}",
                        reason_code=_CONSTRAINT_REASON_CODES.get(constraint.__name__, ""),
                        target_section=op.target_section or "",
                        target_unit_kind=op.target_unit_kind,
                        target_chapter=op.target_chapter,
                    )
                )
                keep = False
                break
        if keep:
            filtered.append(op)
    return filtered

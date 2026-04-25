"""Finland-specific XML -> IR normalization helpers.

The generic core parser intentionally collapses unknown tags to text. For
Finlex AKN, that loses operative row structure in subsection-local tables such
as court-location schedules. This wrapper preserves those tables as paragraph
rows so replay can target them deterministically.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, cast

import lxml.etree as etree

from lawvm.core.ir import IRNode
from lawvm.core.ir_helpers import kind_for_tag as _kind_for_tag
from lawvm.xml_ingest import (
    _POSITIONAL_LABEL_KINDS,
    _STRUCTURAL_TAGS,
    _TEXT_LEAF_TAGS,
    _absorb_orphaned_subsections_into_preceding_section,
    _collapse_text,
    _merge_split_numbered_paragraph_continuations,
    _node_label,
    _paragraph_has_num,
    _rehome_orphaned_letter_paragraphs,
    _tag,
    _xml_table_to_ir,
)
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.helpers import _norm_num_token, _norm_row_anchor_text
from lawvm.finland.profile.normalize import (
    apply_section_rules,
    apply_subsection_pre_rules,
    apply_subsection_post_rules_a,
    apply_subsection_post_rules_b,
    apply_subsection_post_rules_c,
    _apply_recover_embedded_numbered_paragraphs,
    _apply_recover_intro_labeled_paragraphs,
    _apply_nest_lettered_subparagraphs,
    _apply_nest_repeated_alpha_subparagraphs_under_alpha_parents,
    _apply_nest_repeated_digit_subparagraphs,
    _apply_fi_renest_flat_digit_item_subsections,
    _apply_fi_renest_flat_dash_item_subsections,
    _apply_fi_renest_flat_dot_item_subsections,
    _apply_hoist_trailing_wrapup_paragraph,
    _apply_split_trailing_content_only_paragraphs_into_subsections,
    _apply_fi_merge_split_intro_item_subsections,
    _apply_fi_split_inner_omission_paragraph_subsections,
    _apply_fi_split_subsection_at_numbered_list_restart,
    _apply_hoist_inline_content_omissions,
    _apply_fi_split_embedded_section_restarts,
)


_HEADER_TOKENS = ("käräjäoikeus", "kanslia", "istunnot")


# _paragraph_has_introducer_signal is imported from lawvm.finland.profile.normalize above.

def _norm_anchor(text: str) -> str:
    return " ".join(text.lower().split())


def _table_row_cells(tr_el: etree._Element) -> List[str]:
    cells: List[str] = []
    for child in tr_el:
        if _tag(child) != "td":
            continue
        cells.append(_collapse_text(child).strip())
    return cells


def _looks_like_header_row(cells: List[str]) -> bool:
    lowered = [_norm_anchor(cell) for cell in cells if cell.strip()]
    return all(token in " ".join(lowered) for token in _HEADER_TOKENS)


def _parse_table_subsection(
    el: etree._Element,
    label_postprocessor=None,
) -> Optional[IRNode]:
    if _tag(el) != "subsection":
        return None
    content_els: List[etree._Element] = [child for child in el if _tag(child) == "content"]
    for child in el:
        if _tag(child) != "paragraph":
            continue
        nested_content = next((grandchild for grandchild in child if _tag(grandchild) == "content"), None)
        if nested_content is not None:
            content_els.append(nested_content)
    if not content_els:
        return None
    if not any(any(_tag(child) == "table" for child in content_el) for content_el in content_els):
        return None

    has_court_table_header = False
    for content_el in content_els:
        for child in content_el:
            if _tag(child) != "table":
                continue
            for tr_el in child:
                if _tag(tr_el) != "tr":
                    continue
                cells = _table_row_cells(tr_el)
                if _looks_like_header_row(cells):
                    has_court_table_header = True
                    break
            if has_court_table_header:
                break
        if has_court_table_header:
            break
    if not has_court_table_header:
        return None

    label = _node_label(el, "subsection", label_postprocessor)
    attrs = {k.split("}")[-1]: v for k, v in el.attrib.items()}

    intro_parts: List[str] = []
    paragraphs: List[IRNode] = []
    para_idx = 0

    for content_el in content_els:
        for child in content_el:
            child_tag = _tag(child)
            if _kind_for_tag(child_tag) == IRNodeKind.TABLE:
                current_anchor = ""
                current_parts: List[str] = []
                for tr_el in child:
                    if _tag(tr_el) != "tr":
                        continue
                    cells = _table_row_cells(tr_el)
                    if not any(cell.strip() for cell in cells):
                        continue
                    row_text = " ".join(cell for cell in cells if cell.strip()).strip()
                    if not row_text:
                        continue
                    first_cell = cells[0].strip() if cells else ""
                    if not paragraphs and not current_parts and _looks_like_header_row(cells):
                        intro_parts.append(row_text)
                        continue
                    if first_cell:
                        if current_parts:
                            para_idx += 1
                            paragraphs.append(
                                IRNode(
                                    kind=IRNodeKind.PARAGRAPH,
                                    label=str(para_idx),
                                    attrs={"row_anchor": _norm_row_anchor_text(current_anchor)},
                                    children=(IRNode(kind=IRNodeKind.CONTENT, text=" ".join(current_parts).strip()),),
                                )
                            )
                        current_anchor = first_cell
                        current_parts = [row_text]
                        continue
                    if current_parts:
                        continuation = " ".join(cell for cell in cells[1:] if cell.strip()).strip()
                        if continuation:
                            current_parts.append(continuation)
                    else:
                        intro_parts.append(row_text)
                if current_parts:
                    para_idx += 1
                    paragraphs.append(
                        IRNode(
                            kind=IRNodeKind.PARAGRAPH,
                            label=str(para_idx),
                            attrs={"row_anchor": _norm_row_anchor_text(current_anchor)},
                            children=(IRNode(kind=IRNodeKind.CONTENT, text=" ".join(current_parts).strip()),),
                        )
                    )
                continue
            text = _collapse_text(child).strip()
            if text:
                intro_parts.append(text)

    if not paragraphs:
        return None

    new_children: List[IRNode] = []
    intro_text = " ".join(part for part in intro_parts if part).strip()
    if intro_text:
        new_children.append(IRNode(kind=IRNodeKind.INTRO, text=intro_text))
    new_children.extend(paragraphs)
    return IRNode(kind=IRNodeKind.SUBSECTION, label=label, attrs=attrs, children=tuple(new_children))


# Backward-compat re-export: calls the registry implementation in profile.normalize.
def _recover_embedded_numbered_paragraphs(children):  # type: ignore[return]
    return _apply_recover_embedded_numbered_paragraphs(children)

# Backward-compat re-export: calls the registry implementation in profile.normalize.
def _recover_intro_labeled_paragraphs(children):  # type: ignore[return]
    return _apply_recover_intro_labeled_paragraphs(children)

# Backward-compat re-export: calls the registry implementation in profile.normalize.
def _nest_lettered_subparagraphs(children):  # type: ignore[return]
    return _apply_nest_lettered_subparagraphs(children)

# Backward-compat re-export: calls the registry implementation in profile.normalize.
def _nest_repeated_alpha_subparagraphs_under_alpha_parents(children):  # type: ignore[return]
    return _apply_nest_repeated_alpha_subparagraphs_under_alpha_parents(children)

# Backward-compat re-export: calls the registry implementation in profile.normalize.
def _nest_repeated_digit_subparagraphs(children):  # type: ignore[return]
    return _apply_nest_repeated_digit_subparagraphs(children)

# Backward-compat re-export: calls the registry implementation in profile.normalize.
def _fi_renest_flat_digit_item_subsections(children):  # type: ignore[return]
    return _apply_fi_renest_flat_digit_item_subsections(children)

# Backward-compat re-export: calls the registry implementation in profile.normalize.
def _fi_renest_flat_dash_item_subsections(children):  # type: ignore[return]
    return _apply_fi_renest_flat_dash_item_subsections(children)

# Backward-compat re-export: calls the registry implementation in profile.normalize.
def _fi_renest_flat_dot_item_subsections(children):  # type: ignore[return]
    return _apply_fi_renest_flat_dot_item_subsections(children)

# Backward-compat re-export: calls the registry implementation in profile.normalize.
def _hoist_trailing_wrapup_paragraph(children):  # type: ignore[return]
    return _apply_hoist_trailing_wrapup_paragraph(children)

# Backward-compat re-export: calls the registry implementation in profile.normalize.
def _split_trailing_content_only_paragraphs_into_subsections(children):  # type: ignore[return]
    return _apply_split_trailing_content_only_paragraphs_into_subsections(children)

# _subsection_leaf_text is imported from lawvm.finland.profile.normalize above.

# _subsection_has_structured_children is imported from lawvm.finland.profile.normalize above.

# Backward-compat re-export: calls the registry implementation in profile.normalize.
def _fi_merge_split_intro_item_subsections(children):  # type: ignore[return]
    return _apply_fi_merge_split_intro_item_subsections(children)

# Backward-compat re-export: calls the registry implementation in profile.normalize.
def _fi_split_inner_omission_paragraph_subsections(children):  # type: ignore[return]
    return _apply_fi_split_inner_omission_paragraph_subsections(children)

# Backward-compat re-export: calls the registry implementation in profile.normalize.
def _fi_split_subsection_at_numbered_list_restart(children):  # type: ignore[return]
    return _apply_fi_split_subsection_at_numbered_list_restart(children)

# Backward-compat re-export: calls the registry implementation in profile.normalize.
def _hoist_inline_content_omissions(children):  # type: ignore[return]
    return _apply_hoist_inline_content_omissions(children)

def fi_xml_to_ir_node(
    el: etree._Element,
    label_postprocessor=None,
) -> IRNode:
    special = _parse_table_subsection(el, label_postprocessor)
    if special is not None:
        return special

    tag_str = _tag(el)
    tag = _kind_for_tag(tag_str)
    label = _node_label(el, tag_str, label_postprocessor)
    attrs = {k.split("}")[-1]: v for k, v in el.attrib.items()}

    # Note: item-style subsection reclassification (subsection with N) num and
    # letter-labeled paragraph children → paragraph/subparagraph) was moved to
    # the explicit source normalization phase in source_normalize.py.
    # fi_xml_to_ir_node is now a raw structural parse only.

    if tag == IRNodeKind.HCONTAINER and attrs.get("name") == "omission":
        # Pro Q1 architectural decision: omission is a payload-surface marker,
        # NOT a replay operation.  An <hcontainer name="omission"/> (or
        # <p class="omission"/>) in an amendment source XML means "this portion
        # of the existing law is elided from this source artifact — the content
        # still exists."  The omission node is preserved as IRNodeKind.OMISSION
        # metadata so that downstream merge functions can correctly fill the
        # elided slots from the prior-law snapshot.  It does NOT by itself blank
        # the semantic IR.  For oracle comparison, divergences caused by
        # omission-elision encoding (e.g. Finlex's positional-slot reuse for
        # new subsections) are handled in the oracle dedup layer
        # (_dedup_versioned_children in tools/section_keys.py).
        return IRNode(kind=IRNodeKind.OMISSION, label=label, attrs=attrs)

    # Skip image blocks entirely — they appear in consolidated AKN but carry no
    # legal text content (only img src attributes or optional caption markup).
    # Finlex oracle consolidation omits them from text extraction.
    if tag == IRNodeKind.BLOCK and attrs.get("name") == "image":
        return IRNode(kind=IRNodeKind.BLOCK, label=label, attrs=attrs)

    if tag in _TEXT_LEAF_TAGS:
        # Preserve <table> children structurally inside text-leaf elements.
        # Skip <block name="image"> elements entirely.
        has_table = any(_tag(child) == "table" for child in el)
        has_image_block = any(
            _tag(child) == "block" and child.get("name") == "image"
            for child in el
        )
        omission_children: List[IRNode] = []

        if has_table or has_image_block:
            text_parts: List[str] = []
            table_children: List[IRNode] = []
            for child in el:
                child_tag = _tag(child)
                child_attrs = {k.split("}")[-1]: v for k, v in child.attrib.items()}

                child_kind = _kind_for_tag(child_tag)
                if child_kind == IRNodeKind.TABLE:
                    # Table becomes a structured child; its text is NOT also added
                    # to text_parts to avoid duplication in irnode_to_text().
                    table_children.append(_xml_table_to_ir(child))
                elif child_kind == IRNodeKind.BLOCK and child_attrs.get("name") == "image":
                    # Skip image blocks entirely — they contain no legal text.
                    # (Table pattern already handled above.)
                    pass
                elif child_kind == IRNodeKind.P and child_attrs.get("class") == "omission":
                    omission_children.append(IRNode(kind=IRNodeKind.OMISSION, attrs=child_attrs))
                elif child_kind == IRNodeKind.HCONTAINER and child_attrs.get("name") == "omission":
                    omission_children.append(IRNode(kind=IRNodeKind.OMISSION, attrs=child_attrs))
                else:
                    ct = _collapse_text(child).strip()
                    if ct:
                        text_parts.append(ct)
            if el.text and el.text.strip():
                text_parts.insert(0, el.text.strip())
            return IRNode(
                kind=tag,
                label=label,
                text=" ".join(text_parts),
                attrs=attrs,
                children=tuple(table_children + omission_children),
            )
        for child in el:
            child_tag = _tag(child)
            child_attrs = {k.split("}")[-1]: v for k, v in child.attrib.items()}
            child_kind = _kind_for_tag(child_tag)
            if child_kind == IRNodeKind.P and child_attrs.get("class") == "omission":
                omission_children.append(IRNode(kind=IRNodeKind.OMISSION, attrs=child_attrs))
            elif child_kind == IRNodeKind.HCONTAINER and child_attrs.get("name") == "omission":
                omission_children.append(IRNode(kind=IRNodeKind.OMISSION, attrs=child_attrs))
        return IRNode(
            kind=tag,
            label=label,
            text=_collapse_text(el),
            attrs=attrs,
            children=tuple(omission_children),
        )

    if tag == IRNodeKind.TABLE:
        return _xml_table_to_ir(el)

    children: List[IRNode] = []
    for child in el:
        child_tag = _tag(child)
        child_kind = _kind_for_tag(child_tag)
        if child_kind == IRNodeKind.TABLE:
            children.append(_xml_table_to_ir(child))
        elif child_kind in _STRUCTURAL_TAGS or child_kind in _TEXT_LEAF_TAGS:
            children.append(fi_xml_to_ir_node(child, label_postprocessor))
        else:
            text = _collapse_text(child)
            if text:
                children.append(IRNode(kind=cast(IRNodeKind, child_kind or child_tag), text=text))

    if tag in _STRUCTURAL_TAGS:
        if tag in (IRNodeKind.BODY, IRNodeKind.CHAPTER, IRNodeKind.PART, IRNodeKind.HCONTAINER):
            children = _absorb_orphaned_subsections_into_preceding_section(children)
            children = _apply_fi_split_embedded_section_restarts(children)
        if tag == IRNodeKind.SECTION:
            children = apply_section_rules(children)
        if tag == IRNodeKind.SUBSECTION:
            # Recover labels encoded in <intro> text BEFORE the positional counter
            # runs, so intro-labeled paragraphs get the correct explicit label and
            # the counter does not assign them a wrong ordinal.
            children = apply_subsection_pre_rules(children)
        counters: Dict[str | IRNodeKind, int] = {}
        for i, child in enumerate(children):
            if child.label is None and child.kind in _POSITIONAL_LABEL_KINDS:
                counters[child.kind] = counters.get(child.kind, 0) + 1
                children[i] = IRNode(
                    kind=child.kind,
                    label=str(counters[child.kind]),
                    text=child.text,
                    attrs=child.attrs,
                    children=child.children,
                )
        if tag == IRNodeKind.SUBSECTION:
            children = apply_subsection_post_rules_a(children)
            children = _merge_split_numbered_paragraph_continuations(children)
            children = apply_subsection_post_rules_b(children)
            children = _rehome_orphaned_letter_paragraphs(children)
            children = apply_subsection_post_rules_c(children)

    return IRNode(
        kind=cast(IRNodeKind, tag or tag_str),
        label=label,
        text="" if tag in _STRUCTURAL_TAGS else _collapse_text(el),
        attrs=attrs,
        children=tuple(children),
    )


def detect_unnumbered_paragraph_peers(
    subsection: IRNode,
    section_address: str,
) -> List[tuple[str, str, str, str]]:
    """Detect unnumbered paragraph peers in a subsection.

    Finnish legal ontology disallows unnumbered paragraph siblings of numbered
    paragraphs. This function detects the pattern and returns a list of
    violations.

    Args:
        subsection: An IRNode of kind SUBSECTION
        section_address: The address of the parent section (e.g. "chapter:1/section:3")

    Returns:
        A list of tuples: (eId, para_intro_text, preceding_numbered_eIds, following_numbered_eIds)
        Each tuple describes one unnumbered peer.
    """
    if subsection.kind != IRNodeKind.SUBSECTION:
        return []

    # Collect paragraphs and check for unnumbered peers
    paragraphs = [c for c in subsection.children if c.kind == IRNodeKind.PARAGRAPH]
    if not paragraphs:
        return []

    # Check if there are any numbered paragraphs
    numbered_paras = [p for p in paragraphs if _paragraph_has_num(p)]
    if not numbered_paras:
        # All unnumbered or no paragraphs — not a violation
        return []

    violations: List[tuple[str, str, str, str]] = []
    for i, para in enumerate(paragraphs):
        if _paragraph_has_num(para):
            continue
        # This paragraph has no <num> but there are numbered siblings
        eId = para.attrs.get("eId", "")
        intro_text = ""
        intro_child = next((c for c in para.children if c.kind == IRNodeKind.INTRO), None)
        if intro_child:
            intro_text = (intro_child.text or "").strip()[:80]
        else:
            intro_text = (para.text or "").strip()[:80]

        # Find preceding and following numbered eIds
        preceding_nums = [
            p.attrs.get("eId", "") for p in paragraphs[:i] if _paragraph_has_num(p)
        ]
        following_nums = [
            p.attrs.get("eId", "") for p in paragraphs[i + 1 :] if _paragraph_has_num(p)
        ]
        preceding_str = ",".join(preceding_nums) if preceding_nums else ""
        following_str = ",".join(following_nums) if following_nums else ""

        violations.append((eId, intro_text, preceding_str, following_str))

    return violations


def detect_label_eid_divergence(
    subsection: IRNode,
    section_address: str,
) -> List[tuple[str, str]]:
    """Detect label/eId mismatches in paragraph children of a subsection.

    When a paragraph's label (e.g., "2") doesn't match the numeric suffix of its eId
    (e.g., eId ends in "...para_3"), this indicates potential content loss or
    reordering in the source.

    Args:
        subsection: An IRNode of kind SUBSECTION
        section_address: The address of the parent section

    Returns:
        A list of tuples: (label, eId)
        Each tuple describes one mismatched paragraph.
    """
    if subsection.kind != IRNodeKind.SUBSECTION:
        return []

    paragraphs = [c for c in subsection.children if c.kind == IRNodeKind.PARAGRAPH]
    divergences: List[tuple[str, str]] = []

    for para in paragraphs:
        if para.label is None:
            continue
        eId = para.attrs.get("eId", "")
        if not eId:
            continue
        # Extract the trailing numeric part of the eId (e.g., "3" from "...para_3")
        # Handle forms like para_2, para_2_2, para_2v20211030, etc.
        match = re.search(r"__para_(\d+)", eId)
        if not match:
            continue
        eId_num = match.group(1)
        label_normalized = _norm_num_token(str(para.label))
        # For comparison, extract just the numeric part of the label
        label_match = re.match(r"(\d+)", label_normalized)
        if not label_match:
            continue
        label_num = label_match.group(1)

        if label_num != eId_num:
            divergences.append((str(para.label), eId))

    return divergences


__all__ = ["fi_xml_to_ir_node", "detect_unnumbered_paragraph_peers", "detect_label_eid_divergence"]

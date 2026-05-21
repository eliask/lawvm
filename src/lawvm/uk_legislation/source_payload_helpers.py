from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any, Callable, Optional

from lawvm.core.ir import IRNodeKind, LegalAddress
from lawvm.uk_legislation.addressing import (
    _addr_container,
    _addr_field,
    _addr_leaf_kind,
    _addr_leaf_label,
    _schedule_target_levels,
)
from lawvm.uk_legislation.source_context import _first_amendment_container
from lawvm.uk_legislation.uk_grafter import _LEG_NS, _clean_num
from lawvm.uk_legislation.xml_helpers import (
    _direct_structural_num,
    _tag,
    _text_content,
)


UK_FLAT_P1PARA_SCHEDULE_PARAGRAPH_INSERT_RULE_ID = (
    "uk_effect_flat_p1para_schedule_paragraph_insert_payload_lowered"
)
UK_NONADDRESSABLE_SCHEDULE_PART_INSERT_TARGET_RULE_ID = (
    "uk_effect_nonaddressable_schedule_part_insert_target_normalized"
)


def infer_source_payload_from_target(
    *,
    target: LegalAddress,
    extracted_text: Optional[str],
    effect_id: str,
    use_metadata_fallback: bool,
) -> dict[str, Any]:
    inferred_kind = "content"
    inferred_label = None
    container = _addr_container(target)
    target_section = _addr_field(target, "section") or _addr_field(target, "schedule")
    target_part = _addr_field(target, "part")
    target_chapter = _addr_field(target, "chapter")
    schedule_paragraph = None
    schedule_subparagraph = None
    schedule_items: list[str] = []
    if container == "schedule":
        schedule_paragraph, schedule_subparagraph, schedule_items = _schedule_target_levels(target)
        target_subsection = schedule_subparagraph
        target_item = schedule_items[-1] if schedule_items else None
    else:
        paragraphs = [label for kind, label in target.path if kind == "paragraph"]
        subsection_field = _addr_field(target, "subsection")
        if subsection_field:
            target_subsection = subsection_field
            target_item = paragraphs[0] if paragraphs else None
        else:
            target_subsection = paragraphs[0] if paragraphs else None
            target_item = paragraphs[1] if len(paragraphs) >= 2 else None

    if container == "schedule" and not target_subsection and not target_item:
        if schedule_paragraph:
            inferred_kind = "paragraph"
            inferred_label = schedule_paragraph
        else:
            inferred_kind = "schedule"
            inferred_label = target_section
    elif container == "schedule" and target_item:
        inferred_kind = "item"
        inferred_label = target_item
    elif container == "schedule" and target_subsection:
        inferred_kind = "subparagraph"
        inferred_label = target_subsection
    elif target_item:
        inferred_kind = "paragraph"
        inferred_label = target_item
    elif target_subsection:
        inferred_kind = "subsection"
        inferred_label = target_subsection
    elif target_section:
        inferred_kind = "section"
        inferred_label = target_section
    elif target_chapter:
        inferred_kind = "chapter"
        inferred_label = target_chapter
    elif target_part:
        inferred_kind = "part"
        inferred_label = target_part

    inferred_text = extracted_text or ""
    if use_metadata_fallback and not inferred_text:
        inferred_text = f"[inserted by metadata source only: {effect_id}]"
    return {
        "kind": inferred_kind,
        "label": inferred_label,
        "text": inferred_text,
        "children": [],
    }


def _direct_payload_text(el: ET.Element) -> str:
    """Collect direct/local text for extracted payload compilation only."""
    structural_tags = {
        "part",
        "chapter",
        "euchapter",
        "p1group",
        "section",
        "p1",
        "article",
        "eusection",
        "conventionrights",
        "pblock",
        "p2",
        "p3",
        "p4",
        "subsection",
        "paragraph",
        "schedule",
    }
    transparent_tags = {
        "pnumber",
        "number",
        "title",
        "commentaryref",
        "blockamendment",
        "inlineamendment",
    }
    editorial_tags = {"commentary", "citation", "citationsubref"}

    def _collect_local(node: ET.Element) -> list[str]:
        parts: list[str] = []
        if node.text:
            parts.append(node.text)
        for child in node:
            ct = _tag(child).lower()
            if ct in editorial_tags:
                pass
            elif ct in structural_tags or ct in transparent_tags:
                pass
            else:
                parts.extend(_collect_local(child))
            if child.tail:
                parts.append(child.tail)
        return parts

    return " ".join(" ".join(_collect_local(el)).split())


def _inserted_section_p1group_heading_text(
    actual_el: ET.Element,
    extracted_el: ET.Element,
    target: LegalAddress,
) -> Optional[str]:
    """Return a source-owned P1group title for an inserted provision payload.

    UK affecting XML often encodes an inserted section as:

        P1group/Title + P1/Pnumber

    When the effect row targets only the inserted P1, lowering must not later
    use the live parent P1group heading carrier because that parent may also
    contain neighbouring provisions. This helper only accepts the direct source
    wrapper whose child is the exact target provision.
    """
    if _tag(actual_el) not in {"P1", "Section", "Article", "Rule"}:
        return None
    if (_addr_leaf_kind(target) or "") not in {"section", "paragraph"}:
        return None
    target_label = _addr_leaf_label(target) or ""
    actual_label = _direct_structural_num(actual_el)
    if not target_label or _clean_num(actual_label) != _clean_num(target_label):
        return None
    parent_map = {child: parent for parent in extracted_el.iter() for child in parent}
    parent = parent_map.get(actual_el)
    if parent is None or _tag(parent) != "P1group":
        return None
    title_el = parent.find(f"./{{{_LEG_NS}}}Title")
    if title_el is None:
        return None
    heading_text = _text_content(title_el)
    return heading_text or None


def _prepend_inserted_section_heading_carrier(
    content_ir: dict[str, Any],
    *,
    heading_text: str,
    source_rule_id: str = "uk_inserted_section_p1group_heading_carrier",
) -> bool:
    """Prepend an explicit heading child to a provision payload if absent."""
    if str(content_ir.get("kind") or "") not in {IRNodeKind.SECTION.value, IRNodeKind.PARAGRAPH.value}:
        return False
    children = list(content_ir.get("children") or [])
    if any(str(child.get("kind") or "") == IRNodeKind.HEADING.value for child in children):
        return False
    heading_child = {
        "kind": IRNodeKind.HEADING.value,
        "label": None,
        "text": heading_text,
        "attrs": {
            "source_tag": "P1group",
            "source_rule_id": source_rule_id,
        },
        "children": [],
    }
    content_ir["children"] = [heading_child, *children]
    return True


def _flat_p1para_schedule_paragraph_insert_payload(
    extracted_el: Optional[ET.Element],
    target: LegalAddress,
    *,
    fallback_target_eid: Callable[[LegalAddress], str],
) -> Optional[dict[str, Any]]:
    """Return a source-owned paragraph payload from a flat BlockAmendment/P1para.

    Some UK affecting XML encodes ``after paragraph X insert`` payloads as a
    bare ``P1para`` with direct ``Text`` runs rather than a nested ``P1``.  This
    helper accepts only the narrow shape where one direct text run begins with
    the target paragraph label.  Other text runs are reported as unresolved
    heading/cross-heading surface, not smuggled into the paragraph body.
    """

    if _addr_container(target) != "schedule" or _addr_leaf_kind(target) != "paragraph":
        return None
    target_label = _addr_leaf_label(target) or ""
    if not target_label:
        return None
    amendment = _first_amendment_container(extracted_el)
    if amendment is None or _tag(amendment) != "BlockAmendment":
        return None
    p1paras = [child for child in list(amendment) if _tag(child) == "P1para"]
    if len(p1paras) != 1:
        return None
    p1para = p1paras[0]
    for descendant in p1para.iter():
        if descendant is p1para:
            continue
        if _tag(descendant) in {
            "P1",
            "P2",
            "P3",
            "P4",
            "Part",
            "Chapter",
            "Pblock",
            "P1group",
            "Schedule",
        }:
            return None
    direct_texts = [
        " ".join(_text_content(child).split())
        for child in list(p1para)
        if _tag(child) == "Text" and _text_content(child).strip()
    ]
    if len(direct_texts) < 2:
        return None
    paragraph_text = ""
    paragraph_label = ""
    heading_texts: list[str] = []
    for text in direct_texts:
        match = re.match(r"^\(?(?P<label>[0-9]+[A-Za-z]?)\)?(?:\.|\s)+(?P<body>.+)$", text)
        if match and _clean_num(match.group("label")) == _clean_num(target_label):
            paragraph_label = match.group("label")
            paragraph_text = match.group("body").strip()
        else:
            heading_texts.append(text)
    if not paragraph_label or not paragraph_text:
        return None
    paragraph_target = LegalAddress(path=target.path, special=target.special)
    return {
        "kind": IRNodeKind.PARAGRAPH.value,
        "label": paragraph_label,
        "text": paragraph_text,
        "attrs": {
            "eId": fallback_target_eid(paragraph_target),
            "source_rule_id": UK_FLAT_P1PARA_SCHEDULE_PARAGRAPH_INSERT_RULE_ID,
        },
        "children": [],
        "_lawvm_detail": {
            "paragraph_label": paragraph_label,
            "paragraph_text_preview": paragraph_text[:240],
            "unresolved_heading_texts": heading_texts,
            "source_container": "BlockAmendment/P1para",
        },
    }

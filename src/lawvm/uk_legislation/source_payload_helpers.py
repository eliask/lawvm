from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any, Callable, Optional

from lawvm.core.ir import IRNodeKind, LegalAddress
from lawvm.uk_legislation.addressing import (
    _addr_container,
    _addr_leaf_kind,
    _addr_leaf_label,
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

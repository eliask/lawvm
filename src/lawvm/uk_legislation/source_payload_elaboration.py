from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any, Optional

from lawvm.core.ir import IRNode, LegalAddress
from lawvm.core.semantic_types import IRNodeKind
from lawvm.uk_legislation.addressing import _addr_container, _addr_leaf_kind, _addr_leaf_label
from lawvm.uk_legislation.canonicalize import uk_kind_matches
from lawvm.uk_legislation.mutable_ir import UKMutableNode
from lawvm.uk_legislation.target_parser import _parse_affected_target, _split_metadata_provisions
from lawvm.uk_legislation.uk_grafter import _LEG_NS, _clean_num, _extract_num
from lawvm.uk_legislation.xml_helpers import _clone_element, _direct_structural_num, _structural_children, _tag, _text_content


def _crossheading_and_structural_replacement_heading_text(
    *,
    affected_ref: str,
    extracted_el: Optional[ET.Element],
    target: LegalAddress,
) -> Optional[str]:
    """Return title text for explicit ``paragraph X and cross-heading`` replacements."""
    if extracted_el is None or "cross-heading" not in affected_ref.lower():
        return None
    target_label = _clean_num(_addr_leaf_label(target) or "")
    if not target_label:
        return None

    for amendment in extracted_el.iter():
        if _tag(amendment) not in {"BlockAmendment", "InlineAmendment"}:
            continue
        for wrapper in amendment.iter():
            if _tag(wrapper) not in {"P1group", "Pblock"}:
                continue
            title_el = wrapper.find(f"./{{{_LEG_NS}}}Title")
            if title_el is None:
                continue
            heading_text = _text_content(title_el)
            if not heading_text:
                continue
            structural_children = _structural_children(wrapper)
            if not structural_children:
                continue
            first_child_label = _clean_num(_direct_structural_num(structural_children[0]))
            if first_child_label == target_label:
                return heading_text
    return None


def _retarget_instruction_element_to_target(
    extracted_el: ET.Element,
    target: LegalAddress,
    extracted_text: Optional[str],
) -> Optional[ET.Element]:
    """Retarget instruction paragraphs like '27 Section 100 ...' to the real target.

    Some archive-backed affects extracts hand us the amending schedule paragraph
    itself (`P1`, `P2`, `P3`) rather than a nested replacement node. When the
    direct structural number on that element is the amending paragraph number,
    but the text explicitly introduces a different target provision, using the
    raw element as payload fabricates a subtree rooted at the amendment
    paragraph number. In those cases, clone the element and rewrite its direct
    number to the actual affected target label before parsing it as a payload.
    """
    target_kind = _addr_leaf_kind(target)
    target_label = _addr_leaf_label(target)
    if not target_kind or not target_label or not extracted_text:
        return None

    direct_num = _direct_structural_num(extracted_el)
    if not direct_num or _clean_num(direct_num) == _clean_num(target_label):
        return None

    text = " ".join(extracted_text.split())
    label_rx = re.escape(target_label)
    target_patterns = {
        "section": rf"\bsection\s+{label_rx}\b",
        "article": rf"\barticle\s+{label_rx}\b",
        "rule": rf"\brule\s+{label_rx}\b",
        "subsection": rf"\bsubsection\s*\({label_rx}\)\b",
        "paragraph": rf"\bparagraph\s*\({label_rx}\)\b",
        "subparagraph": rf"\bsub-?paragraph\s*\({label_rx}\)\b",
        "item": rf"\bitem\s*\({label_rx}\)\b",
    }
    pattern = target_patterns.get(target_kind)
    if pattern is None or not re.search(pattern, text, re.I):
        return None

    clone = _clone_element(extracted_el)
    num_el = clone.find(f"./{{{_LEG_NS}}}Pnumber")
    if num_el is None:
        num_el = clone.find(f"./{{{_LEG_NS}}}Number")
    if num_el is None:
        return None
    num_el.text = target_label
    for child in list(num_el):
        child.tail = ""
    return clone


def _expand_sibling_targets_from_extracted(
    prov_str: str,
    extracted_el: Optional[ET.Element],
) -> Optional[list[str]]:
    """Expand refs like 'Sch. para. 5(7)(8)' when the payload has sibling nodes.

    Some legislation.gov.uk effects feeds compress multiple inserted sibling
    subparagraphs/items into one metadata ref while the BlockAmendment carries
    separate direct children for each inserted sibling.  In that case we expand
    the ref into one target per direct child so replay does not silently keep
    only the first or last sibling.
    """
    if extracted_el is None or _tag(extracted_el) not in ("BlockAmendment", "InlineAmendment"):
        return None

    structural_tags = {
        "Part",
        "Chapter",
        "EUChapter",
        "Pblock",
        "P1group",
        "Section",
        "P1",
        "Article",
        "Rule",
        "Subsection",
        "P2",
        "P3",
        "Schedule",
    }
    group_structural_children = {
        "P2group": {"P2", "Subsection"},
        "P3group": {"P3"},
        "P4group": {"P4"},
    }

    child_nums: list[str] = []
    child_raw_nums: list[str] = []
    source_children: list[ET.Element] = []
    for child in list(extracted_el):
        child_tag = _tag(child)
        if child_tag in group_structural_children:
            for group_child in list(child):
                if _tag(group_child) in group_structural_children[child_tag]:
                    source_children.append(group_child)
            continue
        if child_tag not in structural_tags:
            continue
        source_children.append(child)

    for child in source_children:
        num_el = child.find(f"./{{{_LEG_NS}}}Pnumber")
        if num_el is None:
            num_el = child.find(f"./{{{_LEG_NS}}}Number")
        raw_num = _extract_num(num_el)
        clean_num = _clean_num(raw_num)
        if clean_num:
            child_nums.append(clean_num)
            child_raw_nums.append(raw_num)

    if len(child_nums) < 2:
        return None

    range_groups = re.match(
        r"^(.*?)\(([0-9A-Z]+)\)-\(([0-9A-Z]+)\)$",
        prov_str.strip(),
        re.I,
    )
    if range_groups:
        prefix = range_groups.group(1).rstrip()
        start_group = _clean_num(range_groups.group(2))
        end_group = _clean_num(range_groups.group(3))
        if child_nums[0] == start_group and child_nums[-1] == end_group:
            return [f"{prefix}({raw_num})" for raw_num in child_raw_nums]

    paren_groups = re.findall(r"\(([0-9A-Z]+)\)", prov_str, re.I)
    if len(paren_groups) < 2:
        return None

    trailing_raw = paren_groups[-len(child_nums) :]
    trailing_clean = [_clean_num(group) for group in trailing_raw]
    if trailing_clean != child_nums:
        return None

    base = prov_str.rstrip()
    for _ in range(len(child_nums)):
        base = re.sub(r"\([0-9A-Z]+\)\s*$", "", base, flags=re.I).rstrip()

    return [f"{base}({raw_num})" for raw_num in child_raw_nums]


def _substituted_series_new_sibling_insert_detail(
    *,
    effect_type: str,
    original_target_refs: list[str],
    target_index: int,
    target_ref: str,
    target: LegalAddress,
    content_ir: Optional[dict[str, Any]],
) -> Optional[dict[str, str]]:
    """Return observation detail when a substituted-for row includes new sibling payloads."""
    raw = (effect_type or "").strip()
    if not raw.lower().startswith("substituted for ") or raw.lower() == "substituted for words":
        return None
    if target_index <= 0 or len(original_target_refs) < 2:
        return None
    anchor_refs = _split_metadata_provisions(raw[len("substituted for ") :].strip())
    if len(anchor_refs) != 1:
        return None
    try:
        anchor_target = _parse_affected_target(anchor_refs[0])
        first_target = _parse_affected_target(original_target_refs[0])
    except ValueError:
        return None
    if tuple(anchor_target.path) != tuple(first_target.path):
        return None
    if tuple(anchor_target.path[:-1]) != tuple(target.path[:-1]):
        return None
    anchor_leaf_kind = _addr_leaf_kind(anchor_target)
    target_leaf_kind = _addr_leaf_kind(target)
    if not anchor_leaf_kind or anchor_leaf_kind != target_leaf_kind:
        return None
    anchor_leaf = re.sub(r"[^0-9a-z]+", "", str(_addr_leaf_label(anchor_target) or "").lower())
    target_leaf = re.sub(r"[^0-9a-z]+", "", str(_addr_leaf_label(target) or "").lower())
    if not anchor_leaf or not target_leaf or anchor_leaf == target_leaf:
        return None
    if not (
        (anchor_leaf.isdigit() and target_leaf.isdigit())
        or (
            bool(re.fullmatch(r"\d+[a-z]*", anchor_leaf, re.I))
            and bool(re.fullmatch(r"\d+[a-z]*", target_leaf, re.I))
        )
        or (
            bool(re.fullmatch(r"[a-z]+", anchor_leaf, re.I))
            and bool(re.fullmatch(r"[a-z]+", target_leaf, re.I))
            and {len(anchor_leaf), len(target_leaf)} <= {1, 2}
        )
    ):
        return None
    if not _source_payload_matches_target_leaf(content_ir, target):
        return None
    return {
        "original_target_ref": anchor_refs[0],
        "target_ref": target_ref,
        "target": str(target),
        "source_payload_kind": str(content_ir.get("kind") or "") if content_ir else "",
        "source_payload_label": str(content_ir.get("label") or "") if content_ir else "",
    }


def _extract_crossheading_payload_from_extracted(
    affected_provisions: str,
    extracted_el: Optional[ET.Element],
) -> Optional[IRNode]:
    """Return a standalone crossheading payload for refs ending in 'and cross-heading'."""
    if extracted_el is None or "cross-heading" not in affected_provisions.lower():
        return None

    candidate = extracted_el
    if _tag(candidate) in ("BlockAmendment", "InlineAmendment") and len(list(candidate)) == 1:
        candidate = list(candidate)[0]
    if _tag(candidate) != "Pblock":
        return None

    title_el = candidate.find(f"./{{{_LEG_NS}}}Title")
    if title_el is None:
        return None
    title = _text_content(title_el)
    if not title:
        return None
    return IRNode(kind=IRNodeKind.CROSSHEADING, label=None, text=title, children=())


def _with_trailing_subordinate_siblings(
    actual_el: ET.Element,
    amendment_container: Optional[ET.Element],
) -> ET.Element:
    """Attach direct trailing subordinate siblings for block-amendment payloads.

    Some affecting-act extracts encode a replaced paragraph as a `P3` followed by
    sibling `P4` nodes rather than nested children. For payload compilation we
    want those subordinate rows preserved under the selected parent payload.
    """
    if amendment_container is None or _tag(amendment_container) not in {"BlockAmendment", "InlineAmendment"}:
        return actual_el
    tag = _tag(actual_el)
    subordinate_tag = {"P2": "P3", "P3": "P4"}.get(tag)
    if subordinate_tag is None:
        return actual_el
    children = list(amendment_container)
    try:
        idx = children.index(actual_el)
    except ValueError:
        return actual_el
    trailing = []
    for sibling in children[idx + 1 :]:
        sibling_tag = _tag(sibling)
        if sibling_tag == subordinate_tag:
            trailing.append(_clone_element(sibling))
            continue
        if sibling_tag in {"CommentaryRef", "Commentary"}:
            continue
        break
    if not trailing:
        return actual_el
    clone = _clone_element(actual_el)
    for sibling in trailing:
        clone.append(sibling)
    return clone


def _is_non_substantive_structural_payload(node: Optional[UKMutableNode]) -> bool:
    """Return True for placeholder structural payloads like '77 . . . .'.

    These appear in some affecting-act extracts as numbering plus dot leaders
    with no operative content. Replaying them as real inserts creates bogus
    wrapper nodes that are later hard to distinguish from genuine structure.
    """
    if node is None:
        return False
    if str(node.kind).lower() not in {
        "section",
        "subsection",
        "paragraph",
        "subparagraph",
        "item",
        "article",
        "rule",
        "content",
    }:
        return False
    text = (node.text or "").strip()
    if node.children:
        if text and re.sub(r"[.\s]+", "", re.sub(r"^\s*[0-9A-Z]+\s*", "", text, flags=re.I)) != "":
            return False
        return all(_is_non_substantive_structural_payload(child) for child in node.children)
    if not text:
        return True
    stripped = re.sub(r"^\s*[0-9A-Z]+\s*", "", text, flags=re.I)
    stripped = re.sub(r"[.\s]+", "", stripped)
    return stripped == ""


def _source_payload_matches_target_leaf(content_ir: Optional[dict[str, Any]], target: LegalAddress) -> bool:
    if content_ir is None:
        return False
    target_kind = _addr_leaf_kind(target) or ""
    target_label = _addr_leaf_label(target) or ""
    payload_kind = str(content_ir.get("kind") or "")
    payload_label = str(content_ir.get("label") or "")
    if not target_kind or not target_label:
        return False
    return uk_kind_matches(
        node_kind=payload_kind,
        target_kind=target_kind,
        node_label=_clean_num(payload_label),
        target_label=_clean_num(target_label),
    ) and _clean_num(payload_label) == _clean_num(target_label)


def _is_broad_schedule_flat_replace_payload(
    *,
    target: LegalAddress,
    payload_node: Optional[UKMutableNode],
    actual_source_el: Optional[ET.Element],
) -> bool:
    """Return True when a broad schedule/part replace would erase descendants.

    UK effects feeds can point at `Sch. N` while the extracted source node is a
    naked table row or amount entry. Lowering that flat text as a whole-schedule
    replacement is payload smuggling: it deletes all unclaimed parts/tables.
    """
    if payload_node is None:
        return False
    if _addr_container(target) != "schedule":
        return False
    target_leaf_kind = str(_addr_leaf_kind(target) or "").lower()
    if target_leaf_kind not in {"schedule", "part"}:
        return False
    payload_kind = str(payload_node.kind or "").lower()
    if payload_kind != target_leaf_kind:
        return False
    if payload_node.children:
        return False
    if actual_source_el is not None and _tag(actual_source_el) in {"Schedule", "Part"}:
        return False
    return bool((payload_node.text or "").strip())

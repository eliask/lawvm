from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any, Optional, Sequence

from lawvm.core.ir import LegalAddress
from lawvm.uk_legislation.addressing import _addr_leaf_kind, _addr_leaf_label
from lawvm.uk_legislation.canonicalize import canonicalize_uk_address
from lawvm.uk_legislation.provision_extractor import _instruction_text_before_amendment_container
from lawvm.uk_legislation.source_context import (
    _source_ancestor_chain,
    _source_parent_range_label,
    _unique_source_ancestor_chain_by_tag_text,
)
from lawvm.uk_legislation.source_fragment_context import _source_local_instruction_text_for_carried_payload
from lawvm.uk_legislation.target_parser import _parse_affected_target
from lawvm.uk_legislation.xml_helpers import _direct_structural_num, _tag


UK_SOURCE_PARENT_SUBSTITUTION_RANGE_PAYLOAD_RULE_ID = (
    "uk_effect_source_parent_substitution_range_payload_lowered"
)
UK_SOURCE_PARENT_AT_END_ADDED_PAYLOAD_RULE_ID = (
    "uk_effect_source_parent_at_end_added_payload_lowered"
)
UK_AFTER_PARAGRAPH_INSERT_LABELLED_SERIES_RULE_ID = (
    "uk_effect_after_paragraph_insert_labelled_series_lowered"
)

SOURCE_PARENT_SCHEDULE_ENTRY_INSERT_RE = re.compile(
    r"\b(?:before|after)\s+(?:the\s+)?entry\s+"
    r"(?:relating\s+to|relation\s+to|for)\s+.+?"
    r"(?:,?\s+there\s+is\s+inserted|\s+insert\b)",
    flags=re.I | re.S,
)

_SOURCE_PARENT_SUBSTITUTION_RANGE_RE = re.compile(
    r"\bfor\s+(?P<kind>sub-?paragraphs?|paragraphs?|subsections?)\s+"
    r"\((?P<start>[0-9A-Za-z]+)\)\s+to\s+\((?P<end>[0-9A-Za-z]+)\)\s+"
    r"(?:(?:there\s+(?:is|are)\s+)?substituted|substitute)\b",
    flags=re.I,
)
_SOURCE_PARENT_AT_END_ADDED_RE = re.compile(
    r"\bat\s+the\s+end\b\s+(?:there\s+(?:is|are)\s+)?(?:added|inserted|insert)\b",
    flags=re.I,
)

_UK_SOURCE_PAYLOAD_TAG_KIND = {
    "Section": "section",
    "P1": "section",
    "Article": "section",
    "Rule": "section",
    "Subsection": "subsection",
    "P2": "subsection",
    "P3": "paragraph",
    "P4": "subparagraph",
}

_UK_AFTER_PARAGRAPH_INSERT_LABELLED_SERIES_REF_RE = re.compile(
    r"^\s*s\.\s*(?P<section>[0-9A-Za-z]+)\s*"
    r"\((?P<subsection>[0-9A-Za-z]+)\)\s*"
    r"\((?P<start>[a-z])\)\s*-\s*\((?P<end>[a-z])\)\s+and\s+semicolon\s*$",
    flags=re.I,
)
_UK_AFTER_PARAGRAPH_INSERT_LABELLED_SERIES_TEXT_RE = re.compile(
    r"^\s*(?P<row_label>[a-z])\s+(?:(?P=row_label)\s+)?after\s+paragraph\s+\((?P<anchor>[a-z])\),\s*"
    r"insert\s+(?P<payload>.+?)\s*$",
    flags=re.I | re.S,
)
_UK_LABELLED_SERIES_ITEM_RE = re.compile(
    r"(?:^|;\s+(?:or\s+)?)(?P<label>[a-z])\s+",
    flags=re.I,
)


def _source_parent_instruction_with_payload(
    *,
    extracted_el: Optional[ET.Element],
    source_root: Optional[ET.Element],
    extracted_text: Optional[str],
    instruction_pattern: re.Pattern[str],
) -> Optional[dict[str, str]]:
    """Combine a payload-only extracted amendment with its parent instruction."""
    payload_text = " ".join((extracted_text or "").split()).strip()
    if not payload_text or extracted_el is None or _tag(extracted_el) not in {"BlockAmendment", "InlineAmendment"}:
        return None
    ancestors = _source_ancestor_chain(source_root, extracted_el)
    for ancestor_index, ancestor in enumerate(ancestors):
        instruction_text = _instruction_text_before_amendment_container(ancestor)
        instruction_text = " ".join(instruction_text.split()).strip()
        if not instruction_text or instruction_pattern.search(instruction_text) is None:
            continue
        source_parent_id = str(ancestor.get("id") or "")
        if not source_parent_id:
            source_parent_id = next(
                (str(candidate.get("id")) for candidate in ancestors[ancestor_index + 1 :] if candidate.get("id")),
                "",
            )
        return {
            "combined_text": f"{instruction_text} {payload_text}",
            "source_parent_id": source_parent_id,
            "source_parent_instruction": instruction_text,
        }
    return None


def _replace_last_parenthetical_label(ref: str, old_label: str, new_label: str) -> str:
    labels = list(re.finditer(r"\(([0-9A-Za-z]+)\)", ref))
    if not labels:
        return ""
    last = labels[-1]
    if _source_parent_range_label(last.group(1)) != _source_parent_range_label(old_label):
        return ""
    return f"{ref[:last.start()]}({new_label}){ref[last.end():]}"


def _source_parent_substitution_range_payload(
    *,
    extracted_el: Optional[ET.Element],
    source_root: Optional[ET.Element],
    extracted_text: Optional[str],
    target_refs: Sequence[str],
) -> Optional[dict[str, Any]]:
    """Prove a payload-only BlockAmendment from its source-local range formula."""
    payload_text = " ".join((extracted_text or "").split()).strip()
    if not payload_text or not target_refs:
        return None
    if extracted_el is None or _tag(extracted_el) not in {"BlockAmendment", "InlineAmendment"}:
        return None
    all_structural_children = [
        child
        for child in list(extracted_el)
        if _tag(child) in {"P1", "P2", "P3", "P4", "Section", "Subsection", "Paragraph"}
    ]
    if not all_structural_children:
        return None
    first_payload_tag = _tag(all_structural_children[0])
    structural_children = [child for child in all_structural_children if _tag(child) == first_payload_tag]
    if not structural_children:
        return None
    payload_labels = tuple(
        label
        for label in (_source_parent_range_label(_direct_structural_num(child)) for child in structural_children)
        if label
    )
    if not payload_labels:
        return None
    target_ref = target_refs[0]
    try:
        targets = tuple(_parse_affected_target(ref) for ref in target_refs)
    except ValueError:
        return None
    target_labels = tuple(_source_parent_range_label(_addr_leaf_label(target) or "") for target in targets)
    if not target_labels or any(not label for label in target_labels):
        return None
    if payload_labels != target_labels[: len(payload_labels)]:
        return None

    ancestors = _source_ancestor_chain(source_root, extracted_el)
    if not ancestors:
        ancestors = _unique_source_ancestor_chain_by_tag_text(source_root, extracted_el)
    for ancestor_index, ancestor in enumerate(ancestors):
        instruction_text = _instruction_text_before_amendment_container(ancestor)
        instruction_text = " ".join(instruction_text.split()).strip()
        if not instruction_text:
            instruction_text = " ".join(_source_local_instruction_text_for_carried_payload(ancestor).split()).strip()
        match = _SOURCE_PARENT_SUBSTITUTION_RANGE_RE.search(instruction_text)
        if match is None:
            continue
        start_label = _source_parent_range_label(match.group("start"))
        end_label = _source_parent_range_label(match.group("end"))
        if start_label != target_labels[0]:
            continue
        if not re.fullmatch(r"[a-z]", start_label, re.I) or not re.fullmatch(r"[a-z]", end_label, re.I):
            continue
        start_ord = ord(start_label.lower())
        end_ord = ord(end_label.lower())
        if end_ord < start_ord:
            continue
        payload_end_ord = ord(payload_labels[-1].lower())
        if payload_end_ord < start_ord or payload_end_ord > end_ord:
            continue
        expected_payload_labels = tuple(chr(label_ord) for label_ord in range(start_ord, payload_end_ord + 1))
        if payload_labels != expected_payload_labels:
            continue
        trailing_refs = tuple(
            ref
            for ref in (
                _replace_last_parenthetical_label(target_refs[-1], payload_labels[-1], chr(label_ord))
                for label_ord in range(payload_end_ord + 1, end_ord + 1)
            )
            if ref
        )
        source_parent_id = str(ancestor.get("id") or "")
        if not source_parent_id:
            source_parent_id = next(
                (str(candidate.get("id")) for candidate in ancestors[ancestor_index + 1 :] if candidate.get("id")),
                "",
            )
        return {
            "rule_id": UK_SOURCE_PARENT_SUBSTITUTION_RANGE_PAYLOAD_RULE_ID,
            "source_parent_id": source_parent_id,
            "source_parent_instruction": instruction_text,
            "target_ref": target_ref,
            "target": str(targets[0]),
            "start_label": start_label,
            "end_label": end_label,
            "trailing_refs": trailing_refs,
            "payload_label": payload_labels[0],
            "payload_labels": payload_labels,
            "payload_tag": _tag(structural_children[0]),
            "payload_tags": tuple(_tag(child) for child in structural_children[: len(payload_labels)]),
        }
    return None


def _source_parent_at_end_added_payload(
    *,
    extracted_el: Optional[ET.Element],
    source_root: Optional[ET.Element],
    extracted_text: Optional[str],
    target_refs: Sequence[str],
) -> Optional[dict[str, Any]]:
    """Prove a payload-only BlockAmendment insertion from its parent formula."""
    payload_text = " ".join((extracted_text or "").split()).strip()
    if not payload_text or len(target_refs) != 1:
        return None
    if extracted_el is None or _tag(extracted_el) not in {"BlockAmendment", "InlineAmendment"}:
        return None
    structural_children = [
        child
        for child in list(extracted_el)
        if _tag(child) in _UK_SOURCE_PAYLOAD_TAG_KIND
    ]
    if len(structural_children) != 1:
        return None
    payload_el = structural_children[0]
    payload_label = _source_parent_range_label(_direct_structural_num(payload_el))
    payload_kind = _UK_SOURCE_PAYLOAD_TAG_KIND.get(_tag(payload_el), "")
    if not payload_label or not payload_kind:
        return None
    target_ref = target_refs[0]
    try:
        target = canonicalize_uk_address(_parse_affected_target(target_ref))
    except ValueError:
        return None
    target_label = _source_parent_range_label(_addr_leaf_label(target) or "")
    target_kind = _addr_leaf_kind(target) or ""
    if payload_label != target_label:
        return None
    if payload_kind != target_kind:
        return None

    ancestors = _source_ancestor_chain(source_root, extracted_el)
    if not ancestors:
        ancestors = _unique_source_ancestor_chain_by_tag_text(source_root, extracted_el)
    for ancestor_index, ancestor in enumerate(ancestors):
        instruction_text = _instruction_text_before_amendment_container(ancestor)
        instruction_text = " ".join(instruction_text.split()).strip()
        if not instruction_text:
            instruction_text = " ".join(_source_local_instruction_text_for_carried_payload(ancestor).split()).strip()
        if _SOURCE_PARENT_AT_END_ADDED_RE.search(instruction_text) is None:
            continue
        source_parent_id = str(ancestor.get("id") or "")
        if not source_parent_id:
            source_parent_id = next(
                (str(candidate.get("id")) for candidate in ancestors[ancestor_index + 1 :] if candidate.get("id")),
                "",
            )
        return {
            "rule_id": UK_SOURCE_PARENT_AT_END_ADDED_PAYLOAD_RULE_ID,
            "source_parent_id": source_parent_id,
            "source_parent_instruction": instruction_text,
            "target_ref": target_ref,
            "target": str(target),
            "payload_label": payload_label,
            "payload_kind": payload_kind,
            "payload_tag": _tag(payload_el),
        }
    return None


def _source_after_paragraph_insert_labelled_series(
    *,
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    affected_provisions: str,
) -> Optional[dict[str, Any]]:
    """Lower `after paragraph (b), insert ; c ...; d ...; or e ...` rows."""
    if extracted_el is None or _tag(extracted_el) not in {"P3", "P4"}:
        return None
    ref_match = _UK_AFTER_PARAGRAPH_INSERT_LABELLED_SERIES_REF_RE.match(affected_provisions or "")
    if ref_match is None:
        return None
    text = " ".join((extracted_text or "").split()).strip()
    text_match = _UK_AFTER_PARAGRAPH_INSERT_LABELLED_SERIES_TEXT_RE.match(text)
    if text_match is None:
        return None
    section = ref_match.group("section")
    subsection = ref_match.group("subsection")
    start_label = _source_parent_range_label(ref_match.group("start"))
    end_label = _source_parent_range_label(ref_match.group("end"))
    anchor_label = _source_parent_range_label(text_match.group("anchor"))
    if not all(re.fullmatch(r"[a-z]", label, re.I) for label in (start_label, end_label, anchor_label)):
        return None
    if ord(anchor_label) + 1 != ord(start_label) or ord(end_label) < ord(start_label):
        return None
    payload_text = text_match.group("payload").strip()
    if not payload_text.startswith(";"):
        return None
    payload_tail = payload_text[1:].strip()
    matches = list(_UK_LABELLED_SERIES_ITEM_RE.finditer(payload_tail))
    if not matches:
        return None
    payloads: list[dict[str, str]] = []
    for index, match in enumerate(matches):
        label = _source_parent_range_label(match.group("label"))
        next_start = matches[index + 1].start() if index + 1 < len(matches) else len(payload_tail)
        item_text = payload_tail[match.end() : next_start].strip()
        item_text = item_text.rstrip(" .")
        if not label or not item_text:
            return None
        payloads.append(
            {
                "label": label,
                "text": item_text,
                "target_ref": f"s. {section}({subsection})({label})",
                "target": f"section:{section}/subsection:{subsection}/paragraph:{label}",
            }
        )
    expected_labels = tuple(chr(label_ord) for label_ord in range(ord(start_label), ord(end_label) + 1))
    if tuple(payload["label"] for payload in payloads) != expected_labels:
        return None
    anchor_target = LegalAddress(
        path=(
            ("section", section),
            ("subsection", subsection),
            ("paragraph", anchor_label),
        )
    )
    return {
        "rule_id": UK_AFTER_PARAGRAPH_INSERT_LABELLED_SERIES_RULE_ID,
        "source_id": str(extracted_el.get("id") or ""),
        "source_instruction": text[: text_match.start("payload")].strip(),
        "target_ref": affected_provisions,
        "section": section,
        "subsection": subsection,
        "anchor_label": anchor_label,
        "anchor_target": str(anchor_target),
        "start_label": start_label,
        "end_label": end_label,
        "semicolon_target": str(anchor_target),
        "payloads": tuple(payloads),
    }

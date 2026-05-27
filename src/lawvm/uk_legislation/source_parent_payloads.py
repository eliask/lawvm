from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any, Optional, Sequence

from lawvm.core.ir import LegalAddress
from lawvm.core.semantic_types import IRNodeKind
from lawvm.uk_legislation.addressing import _addr_leaf_kind, _addr_leaf_label
from lawvm.uk_legislation.canonicalize import canonicalize_uk_address
from lawvm.uk_legislation.provision_extractor import _instruction_text_before_amendment_container
from lawvm.uk_legislation.source_context import (
    _first_amendment_container,
    _source_ancestor_chain,
    _source_parent_range_label,
    _unique_source_ancestor_chain_by_tag_text,
)
from lawvm.uk_legislation.source_fragment_context import _source_local_instruction_text_for_carried_payload
from lawvm.uk_legislation.target_parser import _parse_affected_target
from lawvm.uk_legislation.xml_helpers import _direct_structural_num, _tag
from lawvm.uk_legislation.xml_helpers import _text_content


UK_SOURCE_PARENT_SUBSTITUTION_RANGE_PAYLOAD_RULE_ID = (
    "uk_effect_source_parent_substitution_range_payload_lowered"
)
UK_SOURCE_PARENT_AT_END_ADDED_PAYLOAD_RULE_ID = (
    "uk_effect_source_parent_at_end_added_payload_lowered"
)
UK_SOURCE_PARENT_WHOLE_SCHEDULE_INSERT_RULE_ID = (
    "uk_effect_source_parent_whole_schedule_insert_inferred"
)
UK_AFTER_PARAGRAPH_INSERT_LABELLED_SERIES_RULE_ID = (
    "uk_effect_after_paragraph_insert_labelled_series_lowered"
)
UK_AFTER_PARAGRAPH_INSERT_SINGLE_LABEL_RULE_ID = (
    "uk_effect_after_paragraph_insert_single_label_lowered"
)
UK_AFTER_PARAGRAPH_INSERT_BLOCK_AMENDMENT_RULE_ID = (
    "uk_effect_after_paragraph_insert_block_amendment_lowered"
)
UK_AFTER_SECTION_SUBSECTION_RANGE_INSERT_BLOCK_AMENDMENT_RULE_ID = (
    "uk_effect_after_section_subsection_range_insert_block_amendment_lowered"
)
UK_AT_END_SECTION_SUBSECTION_INSERT_BLOCK_AMENDMENT_RULE_ID = (
    "uk_effect_at_end_section_subsection_insert_block_amendment_lowered"
)
UK_SOURCE_CARRIED_STRUCTURED_TAIL_SUBSTITUTION_RULE_ID = (
    "uk_effect_source_carried_structured_tail_substitution_lowered"
)

SOURCE_PARENT_SCHEDULE_ENTRY_INSERT_RE = re.compile(
    r"\b(?:before|after)\s+(?:the\s+)?entry\s+"
    r"(?:relating\s+to|relation\s+to|for)\s+.+?"
    r"(?:,?\s+there\s+is\s+inserted|\s+insert\b)",
    flags=re.I | re.S,
)
SOURCE_PARENT_TABLE_ENTRY_INSERT_RE = re.compile(
    r"\b(?:(?:before|after)\s+(?:the\s+)?entry\s+"
    r"(?:relating\s+to|relation\s+to|for)\s+.+?"
    r"(?:,?\s+there\s+(?:is|are|shall\s+be)\s+inserted|\s+insert\b)|"
    r"(?:before|after)\s+(?:the\s+)?entry\s+in\s+(?:the\s+)?"
    r"(?:(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\s+column|"
    r"column\s+\d+)\s+"
    r"(?:relating\s+to|relation\s+to|for)\s+.+?"
    r"(?:,?\s+there\s+(?:is|are|shall\s+be)\s+inserted|\s+insert\b)|"
    r"(?:following\s+)?entry(?:\s+[“\"'‘].*?[”\"'’])?\s+"
    r"(?:shall\s+be|is|are)\s+inserted\s+"
    r"(?:before|after)\s+(?:the\s+)?entry\s+"
    r"(?:relating\s+to|relation\s+to|for)\b|"
    r"entry\s+[“\"'‘].*?[”\"'’]\s+"
    r"(?:shall\s+be|is|are)\s+inserted\b.*?"
    r"(?:before|after)\s+(?:the\s+)?entry\s+"
    r"(?:relating\s+to|relation\s+to|for)\b|"
    r"in\s+(?:the\s+)?"
    r"(?:(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\s+column|"
    r"column\s+\d+)"
    r"(?:\s+of\s+(?:(?:the|that)\s+)?table)?\b.*?"
    r"(?:\bat\s+the\s+end\s+of\s+(?:the\s+)?"
    r"(?:(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\s+column|"
    r"column\s+\d+)"
    r"(?:\s+of\s+(?:(?:the|that)\s+)?table)?\b|\bat\s+the\s+end\b)"
    r".*?\binsert(?:ed)?\b|"
    r"in\s+(?:the\s+)?"
    r"(?:(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\s+column|"
    r"column\s+\d+)"
    r"(?:\s+of\s+(?:(?:the|that)\s+)?table)?\b.*?"
    r"\binsert(?:ed)?\s+at\s+the\s+end\b|"
    r"in\s+(?:the\s+)?"
    r"(?:(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\s+column|"
    r"column\s+\d+)"
    r"(?:\s+of\s+(?:(?:the|that)\s+)?table)?\b.*?"
    r"\bafter\s+(?:the\s+)?final\s+entry\b.*?"
    r"(?:there\s+(?:is|are|shall\s+be)\s+)?insert(?:ed)?\b|"
    r"in\s+(?:the\s+)?table\b.*?\bat\s+the\s+end\s+of\s+(?:the\s+)?"
    r"(?:(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\s+column|"
    r"column\s+\d+)"
    r".*?\binsert(?:ed)?\b|"
    r"words\s+[“\"'‘].*?[”\"'’]\s+"
    r"(?:shall\s+be|is|are)\s+inserted\b.*?"
    r"(?:before|after)\s+[“\"'‘].*?[”\"'’])",
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
_SOURCE_PARENT_WHOLE_SCHEDULE_INSERT_RE = re.compile(
    r"\b(?:following\s+)?Schedule\s+is\s+inserted\s+after\s+Schedule\b",
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
_UK_AFTER_PARAGRAPH_INSERT_SINGLE_LABEL_REF_RE = re.compile(
    r"^\s*s\.\s*(?P<section>[0-9A-Za-z]+)\s*"
    r"\((?P<subsection>[0-9A-Za-z]+)\)\s*"
    r"\((?P<label>[a-z]+)\)\s*$",
    flags=re.I,
)
_UK_SECTION_SUBSECTION_REF_RE = re.compile(
    r"^\s*s\.\s*(?P<section>[0-9A-Za-z]+)\s*"
    r"\((?P<subsection>[0-9A-Za-z]+)\)\s*$",
    flags=re.I,
)
_UK_AFTER_PARAGRAPH_INSERT_SINGLE_LABEL_TEXT_RE = re.compile(
    r"^\s*after\s+paragraph\s+\((?P<anchor>[a-z]+)\),?\s*"
    r"insert\s*[—–-]?\s*(?P<label>[a-z]+)\s+(?P<text>.+?)\s*$",
    flags=re.I | re.S,
)
_UK_AFTER_PARAGRAPH_INSERT_BLOCK_AMENDMENT_INSTRUCTION_RE = re.compile(
    r"\bafter\s+paragraph\s+\((?P<anchor>[a-z]+)\),?\s*insert\s*[—–-]?\s*$",
    flags=re.I | re.S,
)
_UK_AFTER_SECTION_SUBSECTION_RANGE_INSERT_REF_RE = re.compile(
    r"^\s*s\.\s*(?P<section>[0-9A-Za-z]+)\s*"
    r"\((?P<start>[0-9A-Za-z]+)\s*-\s*(?P<end>[0-9A-Za-z]+)\)\s*$",
    flags=re.I,
)
_UK_AFTER_SECTION_SUBSECTION_BLOCK_AMENDMENT_INSTRUCTION_RE = re.compile(
    r"\bafter\s+section\s+(?P<section>[0-9A-Za-z]+)\s*"
    r"\((?P<anchor>[0-9A-Za-z]+)\),?\s*"
    r"(?:there\s+(?:is|are|shall\s+be)\s+)?insert(?:ed)?\s*[—–-]?\s*$",
    flags=re.I | re.S,
)
_UK_AT_END_SECTION_SUBSECTION_BLOCK_AMENDMENT_INSTRUCTION_RE = re.compile(
    r"\bat\s+the\s+end\s+of\s+section\s+(?P<section>[0-9A-Za-z]+)\s*"
    r"\((?P<anchor>[0-9A-Za-z]+)\),?\s*"
    r"(?:there\s+(?:is|are|shall\s+be)\s+)?insert(?:ed)?\s*[—–-]?\s*$",
    flags=re.I | re.S,
)
_UK_LABELLED_SERIES_ITEM_RE = re.compile(
    r"(?:^|;\s+(?:or\s+)?)(?P<label>[a-z])\s+",
    flags=re.I,
)
_UK_INLINE_LABELLED_SERIES_ITEM_RE = re.compile(
    r"(?:^|[,;]\s+(?:or\s+)?)(?P<label>[a-z])\s+",
    flags=re.I,
)
_UK_INLINE_LABELLED_SERIES_FIRST_ITEM_RE = re.compile(
    r"^\s*(?P<label>[a-z])\s+",
    flags=re.I,
)
_UK_SOURCE_CARRIED_STRUCTURED_TAIL_SUBSTITUTION_RE = re.compile(
    r"^\s*(?:(?:[0-9A-Za-z]+|[ivxlcdm]+)\s+){0,2}"
    r"in\s+subsection\s+\((?P<subsection>[0-9A-Za-z]+)\)"
    r"(?:\s+\(.*?\))?,?\s+"
    r"for\s+the\s+words\s+from\s+[“\"'‘](?P<anchor>.*?)[”\"'’]\s+"
    r"to\s+the\s+end(?:\s+of\s+the\s+subsection)?\s+"
    r"substitute\s*[“\"'‘]?(?P<payload>.+?)[”\"'’]?\s*\.?\s*$",
    flags=re.I | re.S,
)
_UK_SOURCE_CARRIED_STRUCTURED_SUBPARAGRAPH_TAIL_SUBSTITUTION_RE = re.compile(
    r"^\s*(?:(?:[0-9A-Za-z]+|[ivxlcdm]+)\s+){0,2}"
    r"in\s+paragraph\s+(?P<paragraph>[0-9A-Za-z]+)(?:\s+\([^)]*\))?,?\s+"
    r"in\s+sub-?paragraph\s+\((?P<subparagraph>[0-9A-Za-z]+)\),?\s+"
    r"for\s+the\s+words\s+from\s+[“\"'‘](?P<anchor>.*?)[”\"'’]\s+"
    r"to\s+the\s+end,?\s+"
    r"substitute\s*[“\"'‘]?[—–-]\s*(?P<payload>.+?)[”\"'’]?\s*\.?\s*$",
    flags=re.I | re.S,
)
_UK_SOURCE_CARRIED_STRUCTURED_SUBPARAGRAPH_EFFECT_CONTEXT_TAIL_SUBSTITUTION_RE = re.compile(
    r"^\s*(?P<row_label>[0-9A-Za-z]+|[ivxlcdm]+)\s+"
    r"(?:(?P=row_label)\s+)?"
    r"in\s+sub-?paragraph\s+\((?P<subparagraph>[0-9A-Za-z]+)\),?\s+"
    r"for\s+the\s+words\s+from\s+[“\"'‘](?P<anchor>.*?)[”\"'’]\s+"
    r"to\s+the\s+end,?\s+"
    r"substitute\s*[“\"'‘]?[—–-]\s*(?P<payload>.+?)[”\"'’]?\s*\.?\s*$",
    flags=re.I | re.S,
)
_UK_AFFECTING_TERMINAL_LABEL_RE = re.compile(
    r"\((?P<label>[0-9A-Za-z]+|[ivxlcdm]+)\)\s*$",
    flags=re.I,
)


def _next_alpha_label(label: str) -> str:
    chars = list(label.strip().lower())
    if not chars or any(not ("a" <= char <= "z") for char in chars):
        return ""
    index = len(chars) - 1
    while index >= 0:
        if chars[index] != "z":
            chars[index] = chr(ord(chars[index]) + 1)
            return "".join(chars)
        chars[index] = "a"
        index -= 1
    return "a" + "".join(chars)


def _next_same_stem_alnum_label(label: str) -> str:
    value = label.strip().lower()
    if re.fullmatch(r"[0-9]+", value):
        return f"{value}a"
    match = re.fullmatch(r"(?P<stem>[0-9]+)(?P<suffix>[a-z]+)", value)
    if match is None:
        return ""
    next_suffix = _next_alpha_label(match.group("suffix"))
    if not next_suffix or len(next_suffix) != len(match.group("suffix")):
        return ""
    return f"{match.group('stem')}{next_suffix}"


def _next_numeric_label(label: str) -> str:
    value = label.strip().lower()
    if not re.fullmatch(r"[0-9]+", value):
        return ""
    return str(int(value) + 1)


def _same_stem_alnum_range(start_label: str, end_label: str) -> tuple[str, ...]:
    start = start_label.strip().lower()
    end = end_label.strip().lower()
    if not start or not end:
        return ()
    labels = [start]
    while labels[-1] != end:
        next_label = _next_same_stem_alnum_label(labels[-1])
        if not next_label or len(labels) > 100:
            return ()
        labels.append(next_label)
    return tuple(labels)


def _source_carried_top_level_alpha_matches(payload_tail: str) -> list[re.Match[str]]:
    """Return contiguous top-level alpha labels without consuming nested roman lists."""
    first = _UK_INLINE_LABELLED_SERIES_FIRST_ITEM_RE.match(payload_tail)
    if first is None:
        return []
    matches = [first]
    expected = _next_alpha_label(_source_parent_range_label(first.group("label")))
    search_start = first.end()
    while expected:
        pattern = re.compile(
            rf"[,;]\s+(?:(?:and|or)\s+)?(?P<label>{re.escape(expected)})\s+",
            flags=re.I,
        )
        match = pattern.search(payload_tail, search_start)
        if match is None:
            break
        matches.append(match)
        expected = _next_alpha_label(_source_parent_range_label(match.group("label")))
        search_start = match.end()
    return matches


def _could_match_source_parent_schedule_entry_insert(text: str) -> bool:
    """Cheap necessary-condition guard for the broad schedule-entry regex."""
    lowered = text.lower()
    return (
        "entry" in lowered
        and "insert" in lowered
        and ("before" in lowered or "after" in lowered)
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
        if (
            instruction_pattern is SOURCE_PARENT_SCHEDULE_ENTRY_INSERT_RE
            and not _could_match_source_parent_schedule_entry_insert(instruction_text)
        ):
            continue
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


def _source_parent_prefix_with_child_text(
    *,
    extracted_el: Optional[ET.Element],
    source_root: Optional[ET.Element],
    extracted_text: Optional[str],
    instruction_pattern: re.Pattern[str],
) -> Optional[dict[str, str]]:
    """Combine a source-local parent lead-in with only the current child text."""
    child_text = " ".join((extracted_text or "").split()).strip()
    if not child_text or extracted_el is None:
        return None
    child_label = _source_parent_range_label(_direct_structural_num(extracted_el))
    if (
        child_label
        and child_text.lower().startswith(child_label.lower())
        and len(child_text) > len(child_label)
        and not child_text[len(child_label)].isspace()
    ):
        child_text = f"{child_text[:len(child_label)]} {child_text[len(child_label):]}"
    extracted_id = str(extracted_el.get("id") or "")
    ancestors = _source_ancestor_chain(source_root, extracted_el)
    for ancestor_index, ancestor in enumerate(ancestors):
        parts: list[str] = []
        if ancestor.text:
            parts.append(ancestor.text)
        found_child = False
        for child in list(ancestor):
            same_child = child is extracted_el or (
                bool(extracted_id) and str(child.get("id") or "") == extracted_id
            )
            if same_child:
                parts.append(child_text)
                found_child = True
                break
            if _tag(child) in {"Text", "AppendText"}:
                parts.append(_text_content(child))
            if child.tail:
                parts.append(child.tail)
        if not found_child:
            continue
        combined_text = " ".join(" ".join(parts).split()).strip()
        if not combined_text or instruction_pattern.search(combined_text) is None:
            continue
        source_parent_id = str(ancestor.get("id") or "")
        if not source_parent_id:
            source_parent_id = next(
                (str(candidate.get("id")) for candidate in ancestors[ancestor_index + 1 :] if candidate.get("id")),
                "",
            )
        return {
            "combined_text": combined_text,
            "source_parent_id": source_parent_id,
            "source_parent_instruction": combined_text.removesuffix(child_text).strip(),
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


def _source_parent_whole_schedule_insert_payload(
    *,
    extracted_el: Optional[ET.Element],
    source_root: Optional[ET.Element],
    extracted_text: Optional[str],
    target_refs: Sequence[str],
) -> Optional[dict[str, Any]]:
    """Prove an empty-type whole Schedule insert from its parent formula."""
    payload_text = " ".join((extracted_text or "").split()).strip()
    if not payload_text or len(target_refs) != 1:
        return None
    if extracted_el is None or _tag(extracted_el) not in {"BlockAmendment", "InlineAmendment"}:
        return None
    schedule_children = [child for child in list(extracted_el) if _tag(child) == "Schedule"]
    if len(schedule_children) != 1:
        return None
    payload_el = schedule_children[0]
    payload_label = _source_parent_range_label(_direct_structural_num(payload_el))
    if not payload_label:
        return None
    target_ref = target_refs[0]
    try:
        target = canonicalize_uk_address(_parse_affected_target(target_ref))
    except ValueError:
        return None
    if (_addr_leaf_kind(target) or "") != "schedule":
        return None
    target_label = _source_parent_range_label(_addr_leaf_label(target) or "")
    if payload_label != target_label:
        return None

    ancestors = _source_ancestor_chain(source_root, extracted_el)
    if not ancestors:
        ancestors = _unique_source_ancestor_chain_by_tag_text(source_root, extracted_el)
    for ancestor_index, ancestor in enumerate(ancestors):
        instruction_text = _instruction_text_before_amendment_container(ancestor)
        instruction_text = " ".join(instruction_text.split()).strip()
        if _SOURCE_PARENT_WHOLE_SCHEDULE_INSERT_RE.search(instruction_text) is None:
            continue
        source_parent_id = str(ancestor.get("id") or "")
        if not source_parent_id:
            source_parent_id = next(
                (str(candidate.get("id")) for candidate in ancestors[ancestor_index + 1 :] if candidate.get("id")),
                "",
            )
        return {
            "rule_id": UK_SOURCE_PARENT_WHOLE_SCHEDULE_INSERT_RULE_ID,
            "source_parent_id": source_parent_id,
            "source_parent_instruction": instruction_text,
            "target_ref": target_ref,
            "target": str(target),
            "payload_label": payload_label,
            "payload_kind": "schedule",
            "payload_tag": "Schedule",
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


def _source_after_paragraph_insert_single_label(
    *,
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    affected_provisions: str,
) -> Optional[dict[str, Any]]:
    """Lower `after paragraph (aa) insert- ab ...` rows to a sibling insert."""
    if extracted_el is None or _tag(extracted_el) not in {"P3", "P4"}:
        return None
    ref_match = _UK_AFTER_PARAGRAPH_INSERT_SINGLE_LABEL_REF_RE.match(
        affected_provisions or ""
    )
    if ref_match is None:
        return None
    raw_text = " ".join((extracted_text or "").split()).strip()
    row_label = _source_parent_range_label(_direct_structural_num(extracted_el))
    text = raw_text
    if row_label:
        text = re.sub(
            rf"^\s*(?:{re.escape(row_label)}\s+){{1,2}}(?=after\s+paragraph\b)",
            "",
            text,
            count=1,
            flags=re.I,
        ).strip()
    text_match = _UK_AFTER_PARAGRAPH_INSERT_SINGLE_LABEL_TEXT_RE.match(text)
    if text_match is None:
        return None
    section = ref_match.group("section")
    subsection = ref_match.group("subsection")
    target_label = _source_parent_range_label(ref_match.group("label"))
    source_label = _source_parent_range_label(text_match.group("label"))
    anchor_label = _source_parent_range_label(text_match.group("anchor"))
    if not target_label or target_label != source_label:
        return None
    if _next_alpha_label(anchor_label) != target_label:
        return None
    payload_text = " ".join(text_match.group("text").split()).strip()
    payload_text = re.sub(r"\s*;\s*(?:and\s*)?\.?\s*$", "", payload_text, flags=re.I)
    payload_text = payload_text.rstrip(".").strip()
    if not payload_text:
        return None
    anchor_target = LegalAddress(
        path=(
            ("section", section),
            ("subsection", subsection),
            ("paragraph", anchor_label),
        )
    )
    target_ref = f"s. {section}({subsection})({target_label})"
    return {
        "rule_id": UK_AFTER_PARAGRAPH_INSERT_SINGLE_LABEL_RULE_ID,
        "source_id": str(extracted_el.get("id") or ""),
        "source_instruction": text[: text_match.start("text")].strip(),
        "target_ref": affected_provisions,
        "section": section,
        "subsection": subsection,
        "anchor_label": anchor_label,
        "anchor_target": str(anchor_target),
        "source_row_label": row_label,
        "payload": {
            "label": target_label,
            "text": payload_text,
            "target_ref": target_ref,
            "target": f"section:{section}/subsection:{subsection}/paragraph:{target_label}",
        },
    }


def _source_after_paragraph_insert_block_amendment(
    *,
    extracted_el: Optional[ET.Element],
    affected_provisions: str,
) -> Optional[dict[str, Any]]:
    """Lower `after paragraph (d) insert— <BlockAmendment>` rows."""
    if extracted_el is None or _tag(extracted_el) not in {"P2", "P3", "P4"}:
        return None
    ref_match = _UK_AFTER_PARAGRAPH_INSERT_SINGLE_LABEL_REF_RE.match(
        affected_provisions or ""
    )
    if ref_match is None:
        return None
    instruction_text = " ".join(
        _instruction_text_before_amendment_container(extracted_el).split()
    ).strip()
    instruction_match = _UK_AFTER_PARAGRAPH_INSERT_BLOCK_AMENDMENT_INSTRUCTION_RE.search(
        instruction_text
    )
    if instruction_match is None:
        return None
    amendment = _first_amendment_container(extracted_el)
    if amendment is None or _tag(amendment) != "BlockAmendment":
        return None
    payload_children = tuple(
        child
        for child in list(amendment)
        if _tag(child) in {"P1", "P2", "P3", "P4", "P5", "P6", "Paragraph"}
    )
    if not payload_children:
        return None
    target_label = _source_parent_range_label(ref_match.group("label"))
    first_label = _source_parent_range_label(_direct_structural_num(payload_children[0]))
    if not target_label or first_label != target_label:
        return None
    section = ref_match.group("section")
    subsection = ref_match.group("subsection")
    anchor_label = _source_parent_range_label(instruction_match.group("anchor"))
    if not anchor_label:
        return None

    def _label_stripped_text(node: ET.Element, label: str) -> str:
        text = " ".join(_text_content(node).split()).strip()
        if label:
            text = re.sub(rf"^\s*{re.escape(label)}\s+", "", text, count=1).strip()
        return text

    def _visible_structural_label(node: ET.Element) -> str:
        return str(_direct_structural_num(node) or "").strip().strip("()").lower()

    child_payloads: list[dict[str, Any]] = []
    for child in payload_children[1:]:
        child_label = _visible_structural_label(child)
        child_text = _label_stripped_text(child, child_label)
        if not child_label or not child_text:
            return None
        child_payloads.append(
            {
                "kind": IRNodeKind.SUBPARAGRAPH.value,
                "label": child_label,
                "text": child_text,
                "attrs": {},
                "children": [],
            }
        )
    payload_text = _label_stripped_text(payload_children[0], first_label)
    if not payload_text:
        return None
    target_ref = f"s. {section}({subsection})({target_label})"
    return {
        "rule_id": UK_AFTER_PARAGRAPH_INSERT_BLOCK_AMENDMENT_RULE_ID,
        "source_id": str(extracted_el.get("id") or ""),
        "source_instruction": instruction_text,
        "target_ref": affected_provisions,
        "section": section,
        "subsection": subsection,
        "anchor_label": anchor_label,
        "anchor_target": f"section:{section}/subsection:{subsection}/paragraph:{anchor_label}",
        "source_row_label": _source_parent_range_label(_direct_structural_num(extracted_el)),
        "payload": {
            "kind": IRNodeKind.PARAGRAPH.value,
            "label": target_label,
            "text": payload_text,
            "attrs": {},
            "children": child_payloads,
            "target_ref": target_ref,
            "target": f"section:{section}/subsection:{subsection}/paragraph:{target_label}",
        },
    }


def _source_after_section_subsection_range_insert_block_amendment(
    *,
    extracted_el: Optional[ET.Element],
    affected_provisions: str,
) -> Optional[dict[str, Any]]:
    """Lower `After section N(M) insert <BlockAmendment>` subsection ranges."""
    if extracted_el is None or _tag(extracted_el) not in {"P1", "P2"}:
        return None
    ref_match = _UK_AFTER_SECTION_SUBSECTION_RANGE_INSERT_REF_RE.match(
        affected_provisions or ""
    )
    if ref_match is None:
        return None
    instruction_text = " ".join(
        _instruction_text_before_amendment_container(extracted_el).split()
    ).strip()
    instruction_match = (
        _UK_AFTER_SECTION_SUBSECTION_BLOCK_AMENDMENT_INSTRUCTION_RE.search(
            instruction_text
        )
    )
    if instruction_match is None:
        return None
    section = ref_match.group("section")
    if _source_parent_range_label(instruction_match.group("section")) != (
        _source_parent_range_label(section)
    ):
        return None
    anchor_label = _source_parent_range_label(instruction_match.group("anchor"))
    start_label = _source_parent_range_label(ref_match.group("start"))
    end_label = _source_parent_range_label(ref_match.group("end"))
    expected_labels = _same_stem_alnum_range(start_label, end_label)
    if not anchor_label or not expected_labels:
        return None
    if _next_same_stem_alnum_label(anchor_label) != expected_labels[0]:
        return None
    amendment = _first_amendment_container(extracted_el)
    if amendment is None or _tag(amendment) != "BlockAmendment":
        return None
    payload_children = tuple(child for child in list(amendment) if _tag(child) == "P2")
    payload_labels = tuple(
        _source_parent_range_label(_direct_structural_num(child))
        for child in payload_children
    )
    if payload_labels != expected_labels:
        return None
    return {
        "rule_id": UK_AFTER_SECTION_SUBSECTION_RANGE_INSERT_BLOCK_AMENDMENT_RULE_ID,
        "source_id": str(extracted_el.get("id") or ""),
        "source_instruction": instruction_text,
        "target_ref": affected_provisions,
        "section": section,
        "anchor_label": anchor_label,
        "anchor_target": f"section:{section}/subsection:{anchor_label}",
        "start_label": start_label,
        "end_label": end_label,
        "payload_labels": expected_labels,
        "payload_targets": tuple(
            f"section:{section}/subsection:{label}" for label in expected_labels
        ),
    }


def _source_at_end_section_subsection_insert_block_amendment(
    *,
    extracted_el: Optional[ET.Element],
    affected_provisions: str,
) -> Optional[dict[str, Any]]:
    """Lower `At the end of section N(M) insert <BlockAmendment>` rows."""
    if extracted_el is None or _tag(extracted_el) not in {"P1", "P2"}:
        return None
    ref_match = _UK_SECTION_SUBSECTION_REF_RE.match(affected_provisions or "")
    if ref_match is None:
        return None
    instruction_text = " ".join(
        _instruction_text_before_amendment_container(extracted_el).split()
    ).strip()
    instruction_match = _UK_AT_END_SECTION_SUBSECTION_BLOCK_AMENDMENT_INSTRUCTION_RE.search(
        instruction_text
    )
    if instruction_match is None:
        return None
    section = ref_match.group("section")
    anchor_label = _source_parent_range_label(ref_match.group("subsection"))
    if _source_parent_range_label(instruction_match.group("section")) != (
        _source_parent_range_label(section)
    ):
        return None
    if _source_parent_range_label(instruction_match.group("anchor")) != anchor_label:
        return None
    amendment = _first_amendment_container(extracted_el)
    if amendment is None or _tag(amendment) != "BlockAmendment":
        return None
    payload_children = tuple(child for child in list(amendment) if _tag(child) == "P2")
    if not payload_children:
        return None
    payload_labels = tuple(
        _source_parent_range_label(_direct_structural_num(child))
        for child in payload_children
    )
    expected_labels: list[str] = []
    next_label = anchor_label
    for _child in payload_children:
        next_label = _next_numeric_label(next_label)
        if not next_label:
            return None
        expected_labels.append(next_label)
    if payload_labels != tuple(expected_labels):
        return None
    return {
        "rule_id": UK_AT_END_SECTION_SUBSECTION_INSERT_BLOCK_AMENDMENT_RULE_ID,
        "source_id": str(extracted_el.get("id") or ""),
        "source_instruction": instruction_text,
        "target_ref": affected_provisions,
        "section": section,
        "anchor_label": anchor_label,
        "anchor_target": f"section:{section}/subsection:{anchor_label}",
        "start_label": payload_labels[0],
        "end_label": payload_labels[-1],
        "payload_labels": payload_labels,
        "payload_targets": tuple(
            f"section:{section}/subsection:{label}" for label in payload_labels
        ),
    }


def _source_carried_structured_tail_substitution(
    *,
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    affected_provisions: str,
    affecting_provisions: str = "",
) -> Optional[dict[str, Any]]:
    """Lower parent-tail word substitutions carrying visible child labels.

    This deliberately handles only the tight subsection -> paragraph form:
    the effect target must be the same subsection named by source, and every
    replacement child label must be visibly present in the source row.
    """
    if extracted_el is None or _tag(extracted_el) not in {"P1", "P2", "P3", "P4"}:
        return None
    text = " ".join((extracted_text or "").split()).strip()
    if not text:
        return None
    text_match = _UK_SOURCE_CARRIED_STRUCTURED_TAIL_SUBSTITUTION_RE.match(text)
    subparagraph_text_match = _UK_SOURCE_CARRIED_STRUCTURED_SUBPARAGRAPH_TAIL_SUBSTITUTION_RE.match(text)
    subparagraph_effect_context_match = (
        _UK_SOURCE_CARRIED_STRUCTURED_SUBPARAGRAPH_EFFECT_CONTEXT_TAIL_SUBSTITUTION_RE.match(text)
    )
    if text_match is None and subparagraph_text_match is None and subparagraph_effect_context_match is None:
        return None
    try:
        target = canonicalize_uk_address(_parse_affected_target(affected_provisions))
    except ValueError:
        return None
    payload_kind = "paragraph"
    target_prefix = ""
    target_ref_prefix = ""
    target_label_key = ""
    affecting_row_label = ""
    active_match: re.Match[str]
    source_scope_context = "explicit_source"
    if text_match is not None:
        active_match = text_match
        if _addr_leaf_kind(target) != "subsection":
            return None
        source_subsection = _source_parent_range_label(text_match.group("subsection"))
        target_subsection = _source_parent_range_label(_addr_leaf_label(target) or "")
        if not source_subsection or source_subsection != target_subsection:
            return None
        section_label = ""
        for kind, label in target.path:
            if str(kind or "").lower() == "section":
                section_label = str(label or "")
                break
        if not section_label:
            return None
        target_prefix = f"section:{section_label}/subsection:{source_subsection}"
        target_ref_prefix = f"s. {section_label}({source_subsection})"
        target_label_key = "subsection"
        anchor = " ".join(text_match.group("anchor").split()).strip()
        payload_tail = " ".join(text_match.group("payload").split()).strip()
    else:
        if subparagraph_text_match is None and subparagraph_effect_context_match is None:
            return None
        active_match = (
            subparagraph_text_match
            if subparagraph_text_match is not None
            else subparagraph_effect_context_match
        )
        if _addr_leaf_kind(target) != "subparagraph":
            return None
        source_paragraph = ""
        if subparagraph_text_match is not None:
            source_paragraph = _source_parent_range_label(subparagraph_text_match.group("paragraph"))
        source_subparagraph = _source_parent_range_label(active_match.group("subparagraph"))
        target_paragraph = ""
        for kind, label in target.path:
            if str(kind or "").lower() == "paragraph":
                target_paragraph = _source_parent_range_label(str(label or ""))
                break
        target_subparagraph = _source_parent_range_label(_addr_leaf_label(target) or "")
        if subparagraph_text_match is not None and source_paragraph != target_paragraph:
            return None
        if not source_subparagraph or source_subparagraph != target_subparagraph:
            return None
        if not source_paragraph:
            if not target_paragraph:
                return None
            source_row_label = _source_parent_range_label(_direct_structural_num(extracted_el))
            matched_row_label = _source_parent_range_label(active_match.group("row_label"))
            affecting_label_match = _UK_AFFECTING_TERMINAL_LABEL_RE.search(
                affecting_provisions or ""
            )
            affecting_row_label = (
                _source_parent_range_label(affecting_label_match.group("label"))
                if affecting_label_match is not None
                else ""
            )
            if (
                not source_row_label
                or source_row_label != matched_row_label
                or source_row_label != affecting_row_label
            ):
                return None
            source_paragraph = target_paragraph
            source_scope_context = "explicit_source_with_effect_target_context"
        if not source_paragraph:
            return None
        schedule_label = ""
        for kind, label in target.path:
            if str(kind or "").lower() == "schedule":
                schedule_label = str(label or "")
                break
        if not schedule_label:
            return None
        payload_kind = "item"
        target_prefix = f"schedule:{schedule_label}/paragraph:{source_paragraph}/subparagraph:{source_subparagraph}"
        target_ref_prefix = f"Sch. {schedule_label} para. {source_paragraph}({source_subparagraph})"
        target_label_key = "subparagraph"
        anchor = " ".join(active_match.group("anchor").split()).strip()
        payload_tail = " ".join(active_match.group("payload").split()).strip()
    if not anchor or not payload_tail:
        return None
    payload_tail = re.sub(r"^[—–-]\s*", "", payload_tail).strip()
    prefix_match = re.match(r"^.+?[—–-]\s*(?=[a-z]\s+)", payload_tail, flags=re.I | re.S)
    if prefix_match is not None:
        payload_tail = payload_tail[prefix_match.end() :].strip()
    matches = _source_carried_top_level_alpha_matches(payload_tail)
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
                "target_ref": f"{target_ref_prefix}({label})",
                "target": f"{target_prefix}/{payload_kind}:{label}",
            }
        )
    start_ord = ord(payloads[0]["label"])
    expected_labels = tuple(chr(label_ord) for label_ord in range(start_ord, start_ord + len(payloads)))
    if tuple(payload["label"] for payload in payloads) != expected_labels:
        return None
    return {
        "rule_id": UK_SOURCE_CARRIED_STRUCTURED_TAIL_SUBSTITUTION_RULE_ID,
        "source_id": str(extracted_el.get("id") or ""),
        "source_instruction": text[: active_match.start("payload")].strip(),
        "target_ref": affected_provisions,
        "target": str(target),
        target_label_key: _source_parent_range_label(_addr_leaf_label(target) or ""),
        "source_anchor": anchor,
        "trim_selector": f"TEXT_FROM_{anchor}_TO_END",
        "payload_kind": payload_kind,
        "source_scope_context": source_scope_context,
        "affecting_row_label": affecting_row_label,
        "payloads": tuple(payloads),
    }

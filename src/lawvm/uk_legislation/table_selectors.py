from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any, Optional

from lawvm.core.ir import IRNode, LegalAddress
from lawvm.core.semantic_types import IRNodeKind
from lawvm.uk_legislation.addressing import (
    _addr_container,
    _addr_leaf_kind,
    _addr_leaf_label,
    _uk_kind_value,
)
from lawvm.uk_legislation.nlp_parser import parse_fragment_substitution
from lawvm.uk_legislation.ordinals import _uk_ordinal_to_int
from lawvm.uk_legislation.schedule_list_selectors import _strip_schedule_entry_payload
from lawvm.uk_legislation.source_context import (
    _first_amendment_container,
    _source_ancestor_chain,
    _source_previous_table_entry_label_context,
    _source_previous_table_entry_relating_context,
    _source_text_before_extracted_child,
)
from lawvm.uk_legislation.source_fragment_context import (
    _source_local_instruction_text_for_carried_payload,
)
from lawvm.uk_legislation.uk_grafter import _clean_num, _parse_table as _parse_uk_table_payload
from lawvm.uk_legislation.xml_helpers import _tag, _text_content


UK_TABLE_ENTRY_INLINE_TEXT_RULE_ID = "uk_effect_table_entry_inline_text_insertion"
UK_TABLE_ENTRY_RELATING_TEXT_RULE_ID = "uk_effect_table_entry_relating_text_patch"
UK_TABLE_ENTRY_RELATING_COLUMN_TEXT_RULE_ID = "uk_effect_table_entry_relating_column_text_patch"
UK_TABLE_ENTRY_FOR_COLUMN_TEXT_RULE_ID = "uk_effect_table_entry_for_column_text_patch"
UK_TABLE_ENTRY_LABEL_TEXT_RULE_ID = "uk_effect_table_entry_label_text_patch"
UK_TABLE_ENTRY_LABEL_COLUMN_TEXT_RULE_ID = "uk_effect_table_entry_label_column_text_patch"
UK_TABLE_ENTRY_LABELS_COLUMN_TEXT_RULE_ID = "uk_effect_table_entry_labels_column_text_patch"
UK_TABLE_ENTRY_DEICTIC_LABEL_COLUMN_TEXT_RULE_ID = (
    "uk_effect_table_entry_deictic_label_column_text_patch"
)
UK_TABLE_COLUMN_HEADING_TEXT_RULE_ID = "uk_effect_table_column_heading_text_patch"
UK_TABLE_COLUMN_TEXT_PATCH_RULE_ID = "uk_effect_table_column_text_patch"
UK_TABLE_COLUMN_ENTRY_TEXT_RULE_ID = "uk_effect_table_column_entry_text_patch"
UK_TABLE_COLUMN_ENTRY_OMISSION_TEXT_RULE_ID = (
    "uk_effect_table_column_entry_omission_text_patch"
)
UK_SOURCE_PARENT_TABLE_COLUMN_ENTRY_OMISSION_TEXT_RULE_ID = (
    "uk_effect_source_parent_table_column_entry_omission_text_patch"
)
UK_TABLE_ENTRY_TEXT_RULE_ID = "uk_effect_table_entry_text_patch"
UK_TABLE_COLUMN_INSERT_RULE_ID = "uk_effect_table_column_insert"
UK_TABLE_ENTRY_ROW_INSERT_RULE_ID = "uk_effect_table_entry_row_insert"
UK_TABLE_ENTRY_ROW_REPLACE_RULE_ID = "uk_effect_table_entry_row_replace"
UK_TABLE_ENTRY_INSTRUCTION_REJECTED_RULE_ID = "uk_effect_table_entry_instruction_rejected"
UK_EMBEDDED_TABLE_STRUCTURAL_SUBSTITUTION_RULE_ID = (
    "uk_effect_embedded_table_payload_structural_substitution_preserved"
)
UK_EMBEDDED_TABLE_STRUCTURAL_INSERTION_RULE_ID = (
    "uk_effect_embedded_table_payload_structural_insertion_preserved"
)
UK_SCHEDULE_TABLE_END_ROWS_RULE_ID = "uk_effect_schedule_table_end_rows_lowered"


def _normalized_element_text(el: ET.Element) -> str:
    return " ".join(" ".join(el.itertext()).split()).strip()


def _inserted_table_payload_rows(extracted_el: Optional[ET.Element]) -> tuple[tuple[str, ...], ...]:
    """Return row/cell text from a source-carried inserted table payload."""
    if extracted_el is None:
        return ()
    rows: list[tuple[str, ...]] = []
    for row in extracted_el.iter():
        if _tag(row) != "tr":
            continue
        cells = tuple(
            text
            for text in (
                _normalized_element_text(cell)
                for cell in list(row)
                if _tag(cell) in {"td", "th"}
            )
            if text
        )
        if cells:
            rows.append(cells)
    return tuple(rows)


def _source_names_containing_target_for_table_cell(text: str, target: LegalAddress) -> bool:
    """Return true when source text explicitly names the broad table carrier."""
    if not text or not target.path:
        return False
    leaf_kind = _addr_leaf_kind(target)
    leaf_label = _addr_leaf_label(target)
    if leaf_kind == "subsection" and str(leaf_label or "").lower() == "table":
        for kind, label in reversed(target.path[:-1]):
            if kind == "section" and label:
                leaf_kind = kind
                leaf_label = label
                break
    if leaf_kind != "section" or not leaf_label:
        return False
    return (
        re.search(
            rf"\bin\s+section\s+{re.escape(leaf_label)}\b",
            text,
            flags=re.I,
        )
        is not None
    )


def _source_or_parent_names_containing_target_for_table_cell(
    *,
    text: str,
    target: LegalAddress,
    extracted_el: Optional[ET.Element],
    source_root: Optional[ET.Element],
) -> tuple[bool, str]:
    if _source_names_containing_target_for_table_cell(text, target):
        return True, ""
    matched_parent_without_id = False
    for ancestor in _source_ancestor_chain(source_root, extracted_el):
        ancestor_text = _normalized_element_text(ancestor)
        if _source_names_containing_target_for_table_cell(ancestor_text, target):
            source_parent_id = str(ancestor.get("id") or "")
            if source_parent_id:
                return True, source_parent_id
            matched_parent_without_id = True
    return matched_parent_without_id, ""


def _uk_table_entry_inline_text_selector(
    *,
    target_ref: str,
    target: LegalAddress,
    extracted_text: Optional[str],
    extracted_el: Optional[ET.Element] = None,
    source_root: Optional[ET.Element] = None,
) -> dict[str, Any] | None:
    """Extract a deterministic base-table cell selector from inline table-entry wording."""
    text = " ".join((extracted_text or "").split())
    target_names_table = "table" in " ".join((target_ref, str(target))).lower()
    source_names_containing_target = _source_names_containing_target_for_table_cell(text, target)
    if not text or (
        not target_names_table
        and "table" not in text.lower()
    ):
        return None
    column_entry_patch = _uk_table_column_entry_text_patch_claim(
        target_ref=target_ref,
        target=target,
        extracted_text=text,
        extracted_el=extracted_el,
        source_root=source_root,
    )
    if column_entry_patch is not None:
        return {
            key: value
            for key, value in column_entry_patch.items()
            if key not in {"text_patch_original", "text_patch_replacement"}
        }
    table_entry_patch = _uk_table_entry_text_patch_claim(
        target_ref=target_ref,
        target=target,
        extracted_text=text,
    )
    if table_entry_patch is not None:
        return {
            key: value
            for key, value in table_entry_patch.items()
            if key not in {"text_patch_original", "text_patch_replacement"}
        }
    table_target_column_patch = _uk_table_target_column_text_patch_claim(
        target_ref=target_ref,
        target=target,
        extracted_text=text,
        extracted_el=extracted_el,
        source_root=source_root,
    )
    if table_target_column_patch is not None:
        return {
            key: value
            for key, value in table_target_column_patch.items()
            if key not in {"text_patch_original", "text_patch_replacement"}
        }
    fragments = parse_fragment_substitution(text)
    primary_fragment = fragments[0] if len(fragments) == 1 else None
    original_text = str(primary_fragment.get("original") or "") if primary_fragment is not None else ""
    heading_match = re.search(
        r"\bin\s+the\s+heading\s+of\s+the\s+"
        r"(?P<column>first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|\d+(?:st|nd|rd|th)?)\s+"
        r"column\b",
        text,
        re.I,
    )
    if heading_match is not None and original_text:
        column_index = _uk_ordinal_to_int(heading_match.group("column"))
        if column_index is not None and column_index >= 1:
            return {
                "rule_id": UK_TABLE_COLUMN_HEADING_TEXT_RULE_ID,
                "selector_mode": "unique_column_text",
                "column_index": column_index,
                "match_text": original_text,
                "table_label": "",
                "original_target": str(target),
                "target_ref": target_ref,
            }
    source_table_relating_column_match = re.search(
        r"\bin\s+(?:the\s+)?"
        r"(?P<column_ordinal>first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)"
        r"\s+column\s+of\s+(?:the\s+)?table"
        r"(?:\s+in\s+section\s+[0-9A-Za-z]+)?"
        r",?\s+in\s+the\s+entry\s+"
        r"(?:for|relating\s+to)\s+(?:the\s+)?(?P<relating>.*?),\s+"
        r"(?:after|for|omit|insert|[“\"'‘])\b",
        text,
        re.I,
    )
    if source_table_relating_column_match is not None:
        relating_text = " ".join(
            source_table_relating_column_match.group("relating").split()
        ).strip(" ,;.")
        column_index = _uk_ordinal_to_int(
            source_table_relating_column_match.group("column_ordinal")
        )
        if not original_text:
            quoted_match = re.search(r"[“\"'‘](?P<original>.*?)[”\"'’]", text)
            if quoted_match is not None:
                original_text = " ".join(
                    (quoted_match.group("original") or "").split()
                ).strip()
        if original_text and relating_text and column_index is not None and column_index >= 1:
            return {
                "rule_id": UK_TABLE_ENTRY_RELATING_COLUMN_TEXT_RULE_ID,
                "selector_mode": "unique_relating_cell",
                "relating_text": relating_text,
                "column_index": column_index,
                "table_label": "",
                "original_target": str(target),
                "target_ref": target_ref,
                "source_names_containing_target": source_names_containing_target,
            }
    relating_match = re.search(
        r"\bin\s+the\s+entry\s+relating\s+to\s+(?:the\s+)?(?P<relating>.*?)(?:,\s+for\b|,\s+after\b|,\s+omit\b|,\s+insert\b|$)",
        text,
        re.I,
    )
    if relating_match is not None and original_text:
        relating_text = " ".join(relating_match.group("relating").split()).strip(" ,;.")
        if relating_text:
            return {
                "rule_id": UK_TABLE_ENTRY_RELATING_TEXT_RULE_ID,
                "selector_mode": "unique_relating_text",
                "relating_text": relating_text,
                "match_text": original_text,
                "table_label": "",
                "original_target": str(target),
                "target_ref": target_ref,
                "source_names_containing_target": source_names_containing_target,
            }
    relating_column_match = re.search(
        r"\bin\s+the\s+entry\s+(?:for|relating\s+to)\s+(?:the\s+)?(?P<relating>.*?),\s+in\s+"
        r"(?:(?:the\s+)?(?P<column_ordinal>first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\s+column|"
        r"column\s+(?P<column_number>\d+))\b",
        text,
        re.I,
    )
    if relating_column_match is not None:
        relating_text = " ".join(relating_column_match.group("relating").split()).strip(" ,;.")
        column_token = relating_column_match.group("column_ordinal") or relating_column_match.group("column_number")
        column_index = _uk_ordinal_to_int(column_token or "")
        if relating_text and column_index is not None and column_index >= 1:
            return {
                "rule_id": UK_TABLE_ENTRY_RELATING_COLUMN_TEXT_RULE_ID,
                "selector_mode": "unique_relating_cell",
                "relating_text": relating_text,
                "column_index": column_index,
                "table_label": "",
                "original_target": str(target),
                "target_ref": target_ref,
                "source_names_containing_target": source_names_containing_target,
            }
    column_first_entry_for_match = re.search(
        r"\bin\s+(?:the\s+)?"
        r"(?:(?:the\s+)?(?P<column_ordinal>first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\s+column|"
        r"column\s+(?P<column_number>\d+))\s+of\s+(?:the\s+)?table\b"
        r".*?\bin\s+the\s+entry\s+for\s+(?:the\s+)?"
        r"(?P<relating>.*?)(?:,\s*)?\s+(?:after|omit|insert|for\s+[“\"'‘])\b",
        text,
        re.I,
    )
    if column_first_entry_for_match is not None:
        relating_text = " ".join(
            column_first_entry_for_match.group("relating").split()
        ).strip(" ,;.")
        column_token = (
            column_first_entry_for_match.group("column_ordinal")
            or column_first_entry_for_match.group("column_number")
        )
        column_index = _uk_ordinal_to_int(column_token or "")
        if relating_text and column_index is not None and column_index >= 1:
            return {
                "rule_id": UK_TABLE_ENTRY_FOR_COLUMN_TEXT_RULE_ID,
                "selector_mode": "unique_relating_cell",
                "relating_text": relating_text,
                "column_index": column_index,
                "table_label": "",
                "original_target": str(target),
                "target_ref": target_ref,
                "source_names_containing_target": source_names_containing_target,
            }
    entry_labels_column_match = re.search(
        r"\bin\s+entries\s+(?P<entries>[0-9A-Z]+(?:\s*(?:,|and)\s*[0-9A-Z]+)+),?\s+in\s+"
        r"(?:(?:the\s+)?(?P<column_ordinal>first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\s+column|"
        r"column\s+(?P<column_number>\d+))\b",
        text,
        re.I,
    )
    if entry_labels_column_match is not None:
        entry_labels = tuple(
            _clean_num(label)
            for label in re.split(r"\s*(?:,|and)\s*", entry_labels_column_match.group("entries"))
            if _clean_num(label)
        )
        column_token = (
            entry_labels_column_match.group("column_ordinal")
            or entry_labels_column_match.group("column_number")
        )
        column_index = _uk_ordinal_to_int(column_token or "")
        if len(entry_labels) >= 2 and column_index is not None and column_index >= 1:
            return {
                "rule_id": UK_TABLE_ENTRY_LABELS_COLUMN_TEXT_RULE_ID,
                "selector_mode": "unique_entry_cells",
                "entry_labels": entry_labels,
                "column_index": column_index,
                "table_label": "",
                "original_target": str(target),
                "target_ref": target_ref,
            }
    deictic_entry_column_match = re.search(
        r"\bin\s+that\s+entry,?\s+in\s+"
        r"(?:(?:the\s+)?(?P<column_ordinal>first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\s+column|"
        r"column\s+(?P<column_number>\d+))\b",
        text,
        re.I,
    )
    if deictic_entry_column_match is not None:
        column_token = (
            deictic_entry_column_match.group("column_ordinal")
            or deictic_entry_column_match.group("column_number")
        )
        column_index = _uk_ordinal_to_int(column_token or "")
        entry_context = _source_previous_table_entry_label_context(
            extracted_el=extracted_el,
            source_root=source_root,
            rule_id=UK_TABLE_ENTRY_DEICTIC_LABEL_COLUMN_TEXT_RULE_ID,
        )
        entry_label = _clean_num(entry_context.get("entry_label") or "")
        if entry_label and column_index is not None and column_index >= 1:
            return {
                "rule_id": UK_TABLE_ENTRY_DEICTIC_LABEL_COLUMN_TEXT_RULE_ID,
                "selector_mode": "unique_entry_cell",
                "entry_label": entry_label,
                "column_index": column_index,
                "table_label": "",
                "original_target": str(target),
                "target_ref": target_ref,
                **entry_context,
            }
    entry_column_match = re.search(
        r"\bin\s+entry\s+(?P<entry>[0-9A-Z]+),?\s+in\s+"
        r"(?:(?:the\s+)?(?P<column_ordinal>first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\s+column|"
        r"column\s+(?P<column_number>\d+))\b",
        text,
        re.I,
    )
    if entry_column_match is not None:
        entry_label = _clean_num(entry_column_match.group("entry"))
        column_token = entry_column_match.group("column_ordinal") or entry_column_match.group("column_number")
        column_index = _uk_ordinal_to_int(column_token or "")
        if entry_label and column_index is not None and column_index >= 1:
            return {
                "rule_id": UK_TABLE_ENTRY_LABEL_COLUMN_TEXT_RULE_ID,
                "selector_mode": "unique_entry_cell",
                "entry_label": entry_label,
                "column_index": column_index,
                "table_label": "",
                "original_target": str(target),
                "target_ref": target_ref,
            }
    entry_label_match = re.search(
        r"\bin\s+entry\s+(?P<entry>[0-9A-Z]+)\s+in\s+the\s+table\b",
        text,
        re.I,
    )
    if entry_label_match is not None and original_text:
        entry_label = _clean_num(entry_label_match.group("entry"))
        if entry_label:
            return {
                "rule_id": UK_TABLE_ENTRY_LABEL_TEXT_RULE_ID,
                "selector_mode": "unique_entry_text",
                "entry_label": entry_label,
                "match_text": original_text,
                "table_label": "",
                "original_target": str(target),
                "target_ref": target_ref,
            }
    match = re.search(
        r"\bin\s+the\s+(?P<column>first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|\d+(?:st|nd|rd|th)?)\s+column,\s+"
        r"in\s+the\s+(?P<entry>first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|\d+(?:st|nd|rd|th)?)\s+entry\s+"
        r"relating\s+to\s+(?:the\s+)?(?P<relating>.*?)(?:,\s+after\b|,\s+for\b|,\s+omit\b|,\s+insert\b|$)",
        text,
        re.I,
    )
    if match is None:
        return None
    column_index = _uk_ordinal_to_int(match.group("column"))
    entry_index = _uk_ordinal_to_int(match.group("entry"))
    relating_text = " ".join(match.group("relating").split()).strip(" ,;.")
    if column_index is None or entry_index is None or column_index < 1 or entry_index < 1 or not relating_text:
        return None
    table_match = re.search(r"\btable\s+([0-9A-Za-z]+)\b", target_ref, re.I)
    return {
        "rule_id": UK_TABLE_ENTRY_INLINE_TEXT_RULE_ID,
        "column_index": column_index,
        "entry_index": entry_index,
        "relating_text": relating_text,
        "table_label": table_match.group(1) if table_match is not None else "",
        "original_target": str(target),
        "target_ref": target_ref,
    }


def _uk_table_column_entry_text_patch_claim(
    *,
    target_ref: str,
    target: LegalAddress,
    extracted_text: Optional[str],
    extracted_el: Optional[ET.Element] = None,
    source_root: Optional[ET.Element] = None,
) -> dict[str, Any] | None:
    """Extract quoted table-column entry text patches without inventing rows."""
    text = " ".join((extracted_text or "").split())
    if not text:
        return None
    target_names_table = "table" in " ".join((target_ref, str(target))).lower()
    if not target_names_table and "table" not in text.lower():
        return None
    source_names_containing_target, source_parent_id = (
        _source_or_parent_names_containing_target_for_table_cell(
            text=text,
            target=target,
            extracted_el=extracted_el,
            source_root=source_root,
        )
    )
    if not target_names_table and not ("table" in text.lower() and source_names_containing_target):
        return None
    column_match = re.search(
        r"\bin\s+(?:the\s+)?"
        r"(?:(?P<column_ordinal>first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\s+column|"
        r"column\s+(?P<column_number>\d+))\b",
        text,
        re.I,
    )
    if column_match is None:
        return None
    column_token = column_match.group("column_ordinal") or column_match.group("column_number")
    column_index = _uk_ordinal_to_int(column_token or "")
    if column_index is None or column_index < 1:
        return None
    quoted = r"[“\"'‘](?P<{name}>.*?)[”\"'’]"
    substitution_match = re.search(
        r"\bfor\s+(?:the\s+)?entry\s+"
        + quoted.format(name="original")
        + r"\s+(?:there\s+(?:is|are|shall\s+be)\s+substituted|substitute[ds]?)\s+"
        + r"(?:the\s+)?entry\s+"
        + quoted.format(name="replacement"),
        text,
        re.I,
    )
    if substitution_match is not None:
        original = " ".join(substitution_match.group("original").split()).strip()
        replacement = " ".join(substitution_match.group("replacement").split()).strip()
        if original and replacement:
            return {
                "rule_id": UK_TABLE_COLUMN_ENTRY_TEXT_RULE_ID,
                "selector_mode": "unique_column_text",
                "column_index": column_index,
                "match_text": original,
                "table_label": "",
                "original_target": str(target),
                "target_ref": target_ref,
                "source_names_containing_target": source_names_containing_target,
                "source_parent_id": source_parent_id,
                "table_column_entry_action": "replace_entry",
                "replacement_text": replacement,
                "text_patch_original": original,
                "text_patch_replacement": replacement,
            }
    return None


def _uk_table_column_entry_omission_text_patch_claim(
    *,
    target_ref: str,
    target: LegalAddress,
    extracted_text: Optional[str],
    extracted_el: Optional[ET.Element] = None,
    source_root: Optional[ET.Element] = None,
) -> dict[str, Any] | None:
    """Extract direct single-cell table-entry omissions without deleting rows."""
    text = " ".join((extracted_text or "").split())
    if not text:
        return None
    target_names_table = "table" in " ".join((target_ref, str(target))).lower()
    source_names_containing_target, source_parent_id = (
        _source_or_parent_names_containing_target_for_table_cell(
            text=text,
            target=target,
            extracted_el=extracted_el,
            source_root=source_root,
        )
    )
    if not target_names_table and not ("table" in text.lower() and source_names_containing_target):
        return None
    column = (
        r"(?:(?P<column_ordinal>first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)"
        r"\s+column|column\s+(?P<column_number>\d+))"
    )
    entry = r"(?:entry\s+(?:relating\s+to|for)\s+(?P<entry>.*?))"
    patterns = (
        r"\bin\s+(?:the\s+)?" + column + r"(?:\s+of\s+(?:the\s+)?table)?\b"
        r".*?\b(?:omit|repeal)\s+(?:the\s+)?" + entry + r"\s*(?:[.;,]|$)",
        r"\bin\s+(?:the\s+)?" + column + r"(?:\s+of\s+(?:the\s+)?table)?\b"
        r".*?\b(?:the\s+)?" + entry + r"\s+(?:is|shall\s+be)\s+(?:omitted|repealed)\b",
    )
    match: re.Match[str] | None = None
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match is not None:
            break
    if match is None:
        return _uk_source_parent_table_column_entry_omission_text_patch_claim(
            target_ref=target_ref,
            target=target,
            extracted_text=text,
            extracted_el=extracted_el,
            source_root=source_root,
            target_names_table=target_names_table,
            source_names_containing_target=source_names_containing_target,
            source_parent_id=source_parent_id,
        )
    if re.search(r"\bthat\s+(?:act|schedule|column)\b", text, flags=re.I):
        return None
    if re.search(r"\bentries\b", text, flags=re.I):
        return None
    if not re.search(r"\b(?:omit|omitted|repeal|repealed)\b", text, flags=re.I):
        return None
    column_token = match.group("column_ordinal") or match.group("column_number")
    column_index = _uk_ordinal_to_int(column_token or "")
    entry_text = " ".join((match.group("entry") or "").split()).strip(" ,;.")
    if (
        column_index is None
        or column_index < 1
        or not entry_text
        or re.search(r"\bthat\s+(?:act|schedule|column)\b", entry_text, flags=re.I)
    ):
        return None
    return {
        "rule_id": UK_TABLE_COLUMN_ENTRY_OMISSION_TEXT_RULE_ID,
        "selector_mode": "unique_column_text",
        "column_index": column_index,
        "match_text": entry_text,
        "match_scope": "full_cell",
        "table_label": "",
        "original_target": str(target),
        "target_ref": target_ref,
        "source_names_containing_target": source_names_containing_target,
        "source_parent_id": source_parent_id,
        "table_column_entry_action": "delete_entry_text",
        "text_patch_original": entry_text,
        "text_patch_replacement": "",
    }


def _uk_source_parent_table_column_entry_omission_text_patch_claim(
    *,
    target_ref: str,
    target: LegalAddress,
    extracted_text: str,
    extracted_el: Optional[ET.Element],
    source_root: Optional[ET.Element],
    target_names_table: bool,
    source_names_containing_target: bool,
    source_parent_id: str,
) -> dict[str, Any] | None:
    """Extract one child row from a parent `omit the entries relating to-` group."""
    if extracted_el is None or re.search(r"\bthat\s+(?:act|schedule|column)\b", extracted_text, flags=re.I):
        return None
    entry_text = _strip_source_row_leading_label(extracted_text, extracted_el).strip(" ,;.")
    if (
        not entry_text
        or re.search(r"\b(?:omit|omitted|repeal|repealed|insert|substitut)\b", entry_text, flags=re.I)
        or re.search(r"\bthat\s+(?:act|schedule|column)\b", entry_text, flags=re.I)
    ):
        return None
    for ancestor in _source_ancestor_chain(source_root, extracted_el):
        lead_text = " ".join(
            (
                _source_local_instruction_text_for_carried_payload(ancestor)
                or _source_text_before_extracted_child(ancestor, extracted_el)
            ).split()
        ).strip()
        if not lead_text:
            continue
        parent_names_target = _source_names_containing_target_for_table_cell(
            _normalized_element_text(ancestor),
            target,
        )
        if not target_names_table and not parent_names_target:
            continue
        match = re.search(
            r"\bin\s+(?:the\s+)?"
            r"(?:(?P<column_ordinal>first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)"
            r"\s+column|column\s+(?P<column_number>\d+))\b"
            r".*?\bomit\s+(?:the\s+)?entries\s+relating\s+to\s*[—–-]?\s*$",
            lead_text,
            flags=re.I,
        )
        if match is None:
            continue
        column_token = match.group("column_ordinal") or match.group("column_number")
        column_index = _uk_ordinal_to_int(column_token or "")
        if column_index is None or column_index < 1:
            continue
        resolved_parent_id = str(ancestor.get("id") or ancestor.get("eId") or source_parent_id)
        return {
            "rule_id": UK_SOURCE_PARENT_TABLE_COLUMN_ENTRY_OMISSION_TEXT_RULE_ID,
            "selector_mode": "unique_column_text",
            "column_index": column_index,
            "match_text": entry_text,
            "match_scope": "full_cell",
            "table_label": "",
            "original_target": str(target),
            "target_ref": target_ref,
            "source_names_containing_target": source_names_containing_target or parent_names_target,
            "source_parent_id": resolved_parent_id,
            "source_parent_instruction": lead_text,
            "source_parent_mode": "grouped_entries_relating_to",
            "table_column_entry_action": "delete_entry_text",
            "text_patch_original": entry_text,
            "text_patch_replacement": "",
        }
    return None


def _strip_source_row_leading_label(text: str, extracted_el: ET.Element) -> str:
    label = ""
    for child in extracted_el:
        if _tag(child) == "Pnumber":
            label = " ".join(_text_content(child).split()).strip()
            break
    stripped = " ".join(text.split()).strip()
    if label:
        while stripped.lower().startswith(label.lower()):
            remainder = stripped[len(label) :]
            if remainder and not remainder[0].isspace():
                break
            next_stripped = remainder.strip()
            if next_stripped == stripped:
                break
            stripped = next_stripped
        if stripped:
            return stripped
    return re.sub(r"^\s*(?:[a-z]|[ivxlcdm]+|\d+)\s+", "", stripped, count=1, flags=re.I)


def _uk_table_entry_text_patch_claim(
    *,
    target_ref: str,
    target: LegalAddress,
    extracted_text: Optional[str],
) -> dict[str, Any] | None:
    """Extract a quoted table-entry replacement without assuming a column."""
    text = " ".join((extracted_text or "").split())
    if not text:
        return None
    target_names_table = "table" in " ".join((target_ref, str(target))).lower()
    if not target_names_table:
        return None
    if re.search(r"\bcolumn\b", text, re.I) is not None:
        return None
    quoted = r"[“\"'‘](?P<{name}>.*?)[”\"'’]"
    substitution_match = re.search(
        r"\bfor\s+(?:the\s+)?entry\s+"
        + quoted.format(name="original")
        + r"\s+(?:there\s+(?:is|are|shall\s+be)\s+substituted|substitute[ds]?)\s+"
        + quoted.format(name="replacement"),
        text,
        re.I,
    )
    if substitution_match is None:
        return None
    original = " ".join(substitution_match.group("original").split()).strip()
    replacement = " ".join(substitution_match.group("replacement").split()).strip()
    if not original or not replacement:
        return None
    return {
        "rule_id": UK_TABLE_ENTRY_TEXT_RULE_ID,
        "selector_mode": "unique_table_text",
        "match_text": original,
        "replacement_text": replacement,
        "table_entry_action": "replace_entry_text",
        "original_target": str(target),
        "target_ref": target_ref,
        "text_patch_original": original,
        "text_patch_replacement": replacement,
    }


def _uk_table_target_column_text_patch_claim(
    *,
    target_ref: str,
    target: LegalAddress,
    extracted_text: Optional[str],
    extracted_el: Optional[ET.Element] = None,
    source_root: Optional[ET.Element] = None,
) -> dict[str, Any] | None:
    """Extract quoted text patches scoped only to one named table column."""
    text = " ".join((extracted_text or "").split())
    if not text:
        return None
    target_names_table = "table" in " ".join((target_ref, str(target))).lower()
    if not target_names_table and "table" not in text.lower():
        return None
    source_names_containing_target, source_parent_id = (
        _source_or_parent_names_containing_target_for_table_cell(
            text=text,
            target=target,
            extracted_el=extracted_el,
            source_root=source_root,
        )
    )
    if not target_names_table and not ("table" in text.lower() and source_names_containing_target):
        return None
    if re.search(r"\b(?:entry|entries)\b", text, re.I):
        return None
    column_match = re.search(
        r"\bin\s+(?:the\s+)?"
        r"(?:(?P<column_ordinal>first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\s+column|"
        r"column\s+(?P<column_number>\d+))\b",
        text,
        re.I,
    )
    if column_match is None:
        return None
    column_token = column_match.group("column_ordinal") or column_match.group("column_number")
    column_index = _uk_ordinal_to_int(column_token or "")
    if column_index is None or column_index < 1:
        return None
    fragments = parse_fragment_substitution(text)
    original = str(fragments[0].get("original") or "").strip() if len(fragments) == 1 else ""
    replacement = str(fragments[0].get("replacement") or "").strip() if len(fragments) == 1 else ""
    table_column_text_action = (
        "delete_text"
        if original
        and replacement == ""
        and re.search(r"\b(?:omit|omitted|delete|deleted)\b", text, re.I)
        else "replace_text"
    )
    if not original:
        quoted = r"[“\"'‘](?P<{name}>.*?)[”\"'’]"
        words_before_column_match = re.search(
            r"\bfor\s+(?:the\s+)?words?\s+"
            + quoted.format(name="original")
            + r"\s+in\s+(?:the\s+)?(?:(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\s+column|column\s+\d+)\s+of\s+(?:the\s+)?Table\s+"
            + r"(?:there\s+(?:is|are|shall\s+be)\s+substituted|substitute[ds]?)\s+"
            + quoted.format(name="replacement"),
            text,
            re.I,
        )
        if words_before_column_match is not None:
            original = " ".join(words_before_column_match.group("original").split()).strip()
            replacement = " ".join(words_before_column_match.group("replacement").split()).strip()
    if not original:
        quoted = r"[“\"'‘](?P<{name}>.*?)[”\"'’]"
        references_match = re.search(
            r"\bfor\s+(?:the\s+)?references?\s+to\s+(?P<original>.+?)\s+"
            r"substitute[ds]?\s+"
            + quoted.format(name="replacement"),
            text,
            re.I,
        )
        if references_match is not None:
            original = " ".join(references_match.group("original").split()).strip(" ,;.")
            replacement = " ".join(references_match.group("replacement").split()).strip()
    if not original:
        quoted_omission_match = re.search(
            r"\b(?:omit|delete)\s+[“\"'‘](?P<original>.*?)[”\"'’]",
            text,
            re.I,
        )
        passive_omission_match = re.search(
            r"[“\"'‘](?P<original>.*?)[”\"'’]\s+"
            r"(?:is|are|shall\s+be)\s+(?:omitted|deleted)",
            text,
            re.I,
        )
        omit_match = quoted_omission_match or passive_omission_match
        if omit_match is not None:
            original = " ".join(
                (omit_match.group("original") or "").split()
            ).strip()
            replacement = ""
            table_column_text_action = "delete_text"
    if not original or (not replacement and table_column_text_action != "delete_text"):
        return None
    return {
        "rule_id": UK_TABLE_COLUMN_TEXT_PATCH_RULE_ID,
        "selector_mode": "unique_column_text",
        "column_index": column_index,
        "match_text": original,
        "table_label": "",
        "original_target": str(target),
        "target_ref": target_ref,
        "source_names_containing_target": source_names_containing_target,
        "source_parent_id": source_parent_id,
        "table_column_text_action": table_column_text_action,
        "replacement_text": replacement,
        "text_patch_original": original,
        "text_patch_replacement": replacement,
    }


def _uk_table_entry_row_insert_selector(
    *,
    target_ref: str,
    target: LegalAddress,
    extracted_text: Optional[str],
    extracted_el: Optional[ET.Element] = None,
    source_root: Optional[ET.Element] = None,
) -> dict[str, Any] | None:
    """Extract an explicit table-row insertion selector from ordinal entry wording."""
    text = " ".join((extracted_text or "").split())
    source_names_table = "table" in text.lower()
    implicit_subsection_entry_group = re.search(
        r"\bafter\s+(?:the\s+)?entry\s+"
        r"(?:relating\s+to|for)\s+(?:the\s+)?(?P<relating>.+?)\s+"
        r"insert(?:ed)?\s*[—–-]?\s*(?P<payload>.*)$",
        text,
        re.I,
    )
    if not text or (
        "table" not in " ".join((target_ref, str(target), text)).lower()
        and not (_addr_leaf_kind(target) == "subsection" and implicit_subsection_entry_group is not None)
    ):
        return None
    target_names_table = "table" in f"{target_ref} {target}".lower()
    deictic_previous_entry_insert = re.search(
        r"\bafter\s+that\s+entry\s+insert(?:ed)?\s*[—–-]?",
        text,
        re.I,
    )
    if target_names_table and deictic_previous_entry_insert is not None:
        entry_context = _source_previous_table_entry_relating_context(
            extracted_el=extracted_el,
            source_root=source_root,
            rule_id=UK_TABLE_ENTRY_ROW_INSERT_RULE_ID,
        )
        relating_text = " ".join(str(entry_context.get("relating_text") or "").split()).strip(" ,;.")
        if relating_text:
            group_payload = _uk_single_logical_table_entry_group_payload(extracted_el)
            return {
                "rule_id": UK_TABLE_ENTRY_ROW_INSERT_RULE_ID,
                "selector_mode": "relating_entry",
                "direction": "after",
                "column_index": 1,
                "entry_index": 1,
                "relating_text": relating_text,
                "source_payload_mode": "logical_table_entry_group"
                if group_payload is not None
                else "single_table_row",
                **(
                    {"logical_payload_row_count": len(group_payload.children)}
                    if group_payload is not None
                    else {}
                ),
                "source_names_table": source_names_table,
                "original_target": str(target),
                "target_ref": target_ref,
                **entry_context,
            }
    entry_label_match = re.search(
        r"\bafter\s+entry\s+(?P<anchor>[0-9A-Z]+)\s+in\s+the\s+table\s+"
        r"insert(?:ed)?\s*[—–-]?",
        text,
        re.I,
    )
    table_match = re.search(r"\btable\s+([0-9A-Za-z]+)\b", target_ref, re.I)
    if entry_label_match is not None:
        anchor_entry_label = _clean_num(entry_label_match.group("anchor"))
        if anchor_entry_label:
            return {
                "rule_id": UK_TABLE_ENTRY_ROW_INSERT_RULE_ID,
                "selector_mode": "entry_label",
                "direction": "after",
                "anchor_entry_label": anchor_entry_label,
                "table_label": table_match.group(1) if table_match is not None else "",
                "source_names_table": source_names_table,
                "original_target": str(target),
                "target_ref": target_ref,
            }
    entry_for_insert_match = re.search(
        r"\b(?P<direction>after|before)\s+(?:the\s+)?entry\s+"
        r"(?:for|relating\s+to)\s+(?:the\s+)?(?P<anchor>.+?)\s+"
        r"insert(?:ed)?\s*[—–-]?\s*(?P<payload>.+)$",
        text,
        re.I,
    )
    entry_for_source_names_column = re.search(
        r"\b(?:(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\s+column|column\s+\d+)\b",
        text,
        re.I,
    )
    entry_for_source_names_each_column = re.search(
        r"\beach\s+(?:of\s+the\s+)?columns?\b",
        text,
        re.I,
    )
    if (
        entry_for_insert_match is not None
        and entry_for_source_names_column is None
        and entry_for_source_names_each_column is None
    ):
        relating_text = " ".join(entry_for_insert_match.group("anchor").split()).strip(
            " ,;.“”\"'‘’"
        )
        inserted_text = _strip_schedule_entry_payload(
            entry_for_insert_match.group("payload")
        )
        if relating_text and inserted_text:
            return {
                "rule_id": UK_TABLE_ENTRY_ROW_INSERT_RULE_ID,
                "selector_mode": "relating_entry",
                "direction": entry_for_insert_match.group("direction").lower(),
                "column_index": 1,
                "entry_index": 1,
                "relating_text": relating_text,
                "inserted_text": inserted_text,
                "table_label": table_match.group(1) if table_match is not None else "",
                "source_names_table": source_names_table,
                "original_target": str(target),
                "target_ref": target_ref,
            }
    each_column_entry_for_insert_match = re.search(
        r"\beach\s+(?:of\s+the\s+)?columns?\b.*?"
        r"\b(?P<direction>after|before)\s+(?:the\s+)?entry\s+"
        r"(?:for|relating\s+to|relation\s+to)\s+(?:the\s+)?(?P<anchor>.+?)\s+"
        r"(?:there\s+(?:shall\s+be|is|are)\s+inserted|insert(?:ed)?)\s*"
        r"(?:the\s+following\s+entry)?\s*[—–-]?\s*(?P<payload>.+)$",
        text,
        re.I,
    )
    if each_column_entry_for_insert_match is not None:
        relating_text = " ".join(
            each_column_entry_for_insert_match.group("anchor").split()
        ).strip(" ,;.“”\"'‘’")
        inserted_text = _strip_schedule_entry_payload(
            each_column_entry_for_insert_match.group("payload")
        )
        if relating_text and inserted_text:
            return {
                "rule_id": UK_TABLE_ENTRY_ROW_INSERT_RULE_ID,
                "selector_mode": "each_column_entry",
                "direction": each_column_entry_for_insert_match.group("direction").lower(),
                "column_index": 1,
                "entry_index": 1,
                "relating_text": relating_text,
                "inserted_text": inserted_text,
                "source_payload_mode": "each_column_entry_text",
                "table_label": table_match.group(1) if table_match is not None else "",
                "source_names_table": source_names_table,
                "original_target": str(target),
                "target_ref": target_ref,
            }
    entry_in_column_relating_insert_match = re.search(
        r"\b(?P<direction>after|before)\s+(?:the\s+)?entry\s+in\s+(?:the\s+)?"
        r"(?:(?P<column_ordinal>first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\s+column|"
        r"column\s+(?P<column_number>\d+))\s+"
        r"(?:for|relating\s+to|relation\s+to)\s+(?:the\s+)?(?P<anchor>.+?)\s+"
        r"(?:there\s+(?:shall\s+be|is|are)\s+inserted|insert(?:ed)?)\s*[—–-]?\s*"
        r"(?P<payload>.+)$",
        text,
        re.I,
    )
    if entry_in_column_relating_insert_match is not None:
        column_token = (
            entry_in_column_relating_insert_match.group("column_ordinal")
            or entry_in_column_relating_insert_match.group("column_number")
        )
        column_index = _uk_ordinal_to_int(column_token or "")
        relating_text = " ".join(
            entry_in_column_relating_insert_match.group("anchor").split()
        ).strip(" ,;.“”\"'‘’")
        inserted_text = _strip_schedule_entry_payload(
            entry_in_column_relating_insert_match.group("payload")
        )
        if column_index is not None and column_index >= 1 and relating_text and inserted_text:
            return {
                "rule_id": UK_TABLE_ENTRY_ROW_INSERT_RULE_ID,
                "selector_mode": "column_entry",
                "direction": entry_in_column_relating_insert_match.group("direction").lower(),
                "column_index": column_index,
                "entry_index": 1,
                "relating_text": relating_text,
                "inserted_text": inserted_text,
                "source_payload_mode": "column_entry_text",
                "table_label": table_match.group(1) if table_match is not None else "",
                "source_names_table": source_names_table,
                "original_target": str(target),
                "target_ref": target_ref,
            }
    numbered_target_table_match = re.search(
        r"\bafter\s+entry\s+(?P<anchor>[0-9A-Z]+)\s+"
        r"insert(?:ed)?\s*[—–-]?",
        text,
        re.I,
    )
    if target_names_table and numbered_target_table_match is not None:
        anchor_entry_label = _clean_num(numbered_target_table_match.group("anchor"))
        if anchor_entry_label:
            return {
                "rule_id": UK_TABLE_ENTRY_ROW_INSERT_RULE_ID,
                "selector_mode": "entry_label",
                "direction": "after",
                "anchor_entry_label": anchor_entry_label,
                "table_label": table_match.group(1) if table_match is not None else "",
                "source_payload_mode": "table_rows",
                "source_names_table": source_names_table,
                "original_target": str(target),
                "target_ref": target_ref,
            }
    column_entry_insert_match = re.search(
        r"\bin\s+(?:the\s+)?"
        r"(?:(?P<column_ordinal>first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\s+column|"
        r"column\s+(?P<column_number>\d+))\s+of\s+(?:the\s+)?table\b"
        r".*?\b(?P<direction>after|before)\s+(?:the\s+)?entry\s+[“\"'‘](?P<anchor>.*?)[”\"'’]\s+"
        r"(?:there\s+(?:shall\s+be|is|are)\s+inserted\s+|insert(?:ed)?\s+)"
        r"(?:the\s+)?entry\s+[“\"'‘](?P<payload>.*?)[”\"'’]",
        text,
        re.I,
    )
    if column_entry_insert_match is not None:
        column_token = (
            column_entry_insert_match.group("column_ordinal")
            or column_entry_insert_match.group("column_number")
        )
        column_index = _uk_ordinal_to_int(column_token or "")
        relating_text = " ".join(column_entry_insert_match.group("anchor").split()).strip(" ,;.")
        inserted_text = " ".join(column_entry_insert_match.group("payload").split()).strip(" ,")
        if column_index is not None and column_index >= 1 and relating_text and inserted_text:
            return {
                "rule_id": UK_TABLE_ENTRY_ROW_INSERT_RULE_ID,
                "selector_mode": "column_entry",
                "direction": column_entry_insert_match.group("direction").lower(),
                "column_index": column_index,
                "entry_index": 1,
                "relating_text": relating_text,
                "inserted_text": inserted_text,
                "source_payload_mode": "column_entry_text",
                "table_label": table_match.group(1) if table_match is not None else "",
                "source_names_table": source_names_table,
                "original_target": str(target),
                "target_ref": target_ref,
            }
    column_relating_entry_insert_match = re.search(
        r"\bin\s+(?:the\s+)?"
        r"(?:(?P<column_ordinal>first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\s+column|"
        r"column\s+(?P<column_number>\d+))"
        r"(?:\s+of\s+(?:(?:the|that)\s+)?table)?\b"
        r".*?\b(?P<direction>after|before)\s+(?:the\s+)?entry\s+"
        r"(?:for|relating\s+to)\s+(?:the\s+)?(?P<anchor>.+?)\s+"
        r"(?:insert(?:ed)?|there\s+(?:shall\s+be|is|are)\s+inserted)\s*[—–-]?\s*"
        r"(?P<payload>.+)$",
        text,
        re.I,
    )
    if column_relating_entry_insert_match is not None:
        column_token = (
            column_relating_entry_insert_match.group("column_ordinal")
            or column_relating_entry_insert_match.group("column_number")
        )
        column_index = _uk_ordinal_to_int(column_token or "")
        relating_text = " ".join(column_relating_entry_insert_match.group("anchor").split()).strip(
            " ,;.“”\"'‘’"
        )
        inserted_text = _strip_schedule_entry_payload(
            column_relating_entry_insert_match.group("payload")
        )
        if column_index is not None and column_index >= 1 and relating_text and inserted_text:
            return {
                "rule_id": UK_TABLE_ENTRY_ROW_INSERT_RULE_ID,
                "selector_mode": "column_entry",
                "direction": column_relating_entry_insert_match.group("direction").lower(),
                "column_index": column_index,
                "entry_index": 1,
                "relating_text": relating_text,
                "inserted_text": inserted_text,
                "source_payload_mode": "column_entry_text",
                "table_label": table_match.group(1) if table_match is not None else "",
                "source_names_table": source_names_table,
                "original_target": str(target),
                "target_ref": target_ref,
            }
    column_passive_entry_insert_match = re.search(
        r"\bin\s+(?:the\s+)?"
        r"(?:(?P<column_ordinal>first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\s+column|"
        r"column\s+(?P<column_number>\d+))"
        r"(?:\s+of\s+(?:(?:the|that)\s+)?table)?\b"
        r".*?(?:following\s+)?entry\s+(?:shall\s+be|is|are)\s+inserted\s+"
        r"(?P<direction>after|before)\s+(?:the\s+)?entry\s+"
        r"(?:for|relating\s+to)\s+(?:the\s+)?(?P<anchor>.+?)\s*[—–-]\s*"
        r"(?P<payload>.+)$",
        text,
        re.I,
    )
    if column_passive_entry_insert_match is not None:
        column_token = (
            column_passive_entry_insert_match.group("column_ordinal")
            or column_passive_entry_insert_match.group("column_number")
        )
        column_index = _uk_ordinal_to_int(column_token or "")
        relating_text = " ".join(column_passive_entry_insert_match.group("anchor").split()).strip(
            " ,;.“”\"'‘’"
        )
        inserted_text = _strip_schedule_entry_payload(
            column_passive_entry_insert_match.group("payload")
        )
        if column_index is not None and column_index >= 1 and relating_text and inserted_text:
            return {
                "rule_id": UK_TABLE_ENTRY_ROW_INSERT_RULE_ID,
                "selector_mode": "column_entry",
                "direction": column_passive_entry_insert_match.group("direction").lower(),
                "column_index": column_index,
                "entry_index": 1,
                "relating_text": relating_text,
                "inserted_text": inserted_text,
                "source_payload_mode": "column_entry_text",
                "table_label": table_match.group(1) if table_match is not None else "",
                "source_names_table": source_names_table,
                "original_target": str(target),
                "target_ref": target_ref,
            }
    distributed_column_entry_insert_match = re.search(
        r"\bentry\s+[“\"'‘](?P<payload>.*?)[”\"'’]\s+"
        r"(?:shall\s+be|is|are)\s+inserted\s*[—–-]?\s*"
        r".*?\bin\s+(?:the\s+)?"
        r"(?:(?P<column_ordinal>first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\s+column|"
        r"column\s+(?P<column_number>\d+))"
        r"(?:\s+of\s+(?:(?:the|that)\s+)?table)?\b"
        r"\s+(?P<direction>after|before)\s+(?:the\s+)?entry\s+"
        r"(?:for|relating\s+to)\s+(?:the\s+)?(?P<anchor>.+?)\s*[,.;]?\s*$",
        text,
        re.I,
    )
    if distributed_column_entry_insert_match is not None:
        column_token = (
            distributed_column_entry_insert_match.group("column_ordinal")
            or distributed_column_entry_insert_match.group("column_number")
        )
        column_index = _uk_ordinal_to_int(column_token or "")
        relating_text = " ".join(
            distributed_column_entry_insert_match.group("anchor").split()
        ).strip(" ,;.“”\"'‘’")
        inserted_text = _strip_schedule_entry_payload(
            distributed_column_entry_insert_match.group("payload")
        )
        if column_index is not None and column_index >= 1 and relating_text and inserted_text:
            return {
                "rule_id": UK_TABLE_ENTRY_ROW_INSERT_RULE_ID,
                "selector_mode": "column_entry",
                "direction": distributed_column_entry_insert_match.group("direction").lower(),
                "column_index": column_index,
                "entry_index": 1,
                "relating_text": relating_text,
                "inserted_text": inserted_text,
                "source_payload_mode": "column_entry_text",
                "table_label": table_match.group(1) if table_match is not None else "",
                "source_names_table": source_names_table,
                "original_target": str(target),
                "target_ref": target_ref,
            }
    distributed_column_words_insert_match = re.search(
        r"\bwords\s+[“\"'‘](?P<payload>.*?)[”\"'’]\s+"
        r"(?:shall\s+be|is|are)\s+inserted\s*[—–-]?\s*"
        r".*\bin\s+(?:the\s+)?"
        r"(?:(?P<column_ordinal>first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\s+column|"
        r"column\s+(?P<column_number>\d+))"
        r"(?:\s+of\s+(?:(?:the|that)\s+)?table)?\b"
        r",?\s+(?P<direction>after|before)\s+[“\"'‘](?P<anchor>.*?)[”\"'’]"
        r"\s*[,.;]?\s*(?:and|or)?\s*$",
        text,
        re.I,
    )
    if distributed_column_words_insert_match is not None:
        column_token = (
            distributed_column_words_insert_match.group("column_ordinal")
            or distributed_column_words_insert_match.group("column_number")
        )
        column_index = _uk_ordinal_to_int(column_token or "")
        relating_text = " ".join(distributed_column_words_insert_match.group("anchor").split()).strip(
            " ,;.“”\"'‘’"
        )
        inserted_text = _strip_schedule_entry_payload(
            distributed_column_words_insert_match.group("payload")
        )
        if column_index is not None and column_index >= 1 and relating_text and inserted_text:
            return {
                "rule_id": UK_TABLE_ENTRY_ROW_INSERT_RULE_ID,
                "selector_mode": "column_entry",
                "direction": distributed_column_words_insert_match.group("direction").lower(),
                "column_index": column_index,
                "entry_index": 1,
                "relating_text": relating_text,
                "inserted_text": inserted_text,
                "source_payload_mode": "column_entry_text",
                "table_label": table_match.group(1) if table_match is not None else "",
                "source_names_table": source_names_table,
                "original_target": str(target),
                "target_ref": target_ref,
            }
    column_end_entry_insert_match = re.search(
        r"\bat\s+the\s+end\s+of\s+(?:the\s+)?"
        r"(?:(?P<column_ordinal>first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\s+column|"
        r"column\s+(?P<column_number>\d+))"
        r"(?:\s+of\s+(?:(?:the|that)\s+)?table)?\b"
        r".*?(?:insert(?:ed)?|there\s+(?:shall\s+be|is|are)\s+inserted)\s*[—–-]?\s*"
        r"(?P<payload>.+)$",
        text,
        re.I,
    )
    if column_end_entry_insert_match is None:
        column_end_entry_insert_match = re.search(
            r"\bin\s+(?:the\s+)?"
            r"(?:(?P<column_ordinal>first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\s+column|"
            r"column\s+(?P<column_number>\d+))"
            r"(?:\s+of\s+(?:(?:the|that)\s+)?table)?\b"
            r".*?\bat\s+the\s+end\b"
            r".*?(?:insert(?:ed)?|there\s+(?:shall\s+be|is|are)\s+inserted)\s*[—–-]?\s*"
            r"(?P<payload>.+)$",
            text,
            re.I,
        )
    if column_end_entry_insert_match is None:
        column_end_entry_insert_match = re.search(
            r"\bin\s+(?:the\s+)?"
            r"(?:(?P<column_ordinal>first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\s+column|"
            r"column\s+(?P<column_number>\d+))"
            r"(?:\s+of\s+(?:(?:the|that)\s+)?table)?\b"
            r".*?\binsert(?:ed)?\s+at\s+the\s+end\s*[—–-]?\s*"
            r"(?P<payload>.+)$",
            text,
            re.I,
        )
    if column_end_entry_insert_match is not None:
        column_token = (
            column_end_entry_insert_match.group("column_ordinal")
            or column_end_entry_insert_match.group("column_number")
        )
        column_index = _uk_ordinal_to_int(column_token or "")
        inserted_text = _strip_schedule_entry_payload(
            column_end_entry_insert_match.group("payload")
        )
        if column_index is not None and column_index >= 1 and inserted_text:
            source_list_payload = _uk_column_entry_list_row_payload(
                extracted_el,
                source_root=source_root,
                column_index=column_index,
            )
            if (
                source_list_payload is None
                and _column_end_payload_needs_owned_list(inserted_text)
            ):
                return None
            source_payload_mode = (
                "column_entry_list_rows"
                if source_list_payload is not None
                else "column_entry_text"
            )
            return {
                "rule_id": UK_TABLE_ENTRY_ROW_INSERT_RULE_ID,
                "selector_mode": "column_final_entry",
                "direction": "after",
                "column_index": column_index,
                "entry_index": 1,
                "relating_text": "final entry",
                "inserted_text": inserted_text,
                "source_payload_mode": source_payload_mode,
                "table_label": table_match.group(1) if table_match is not None else "",
                "source_names_table": source_names_table,
                "original_target": str(target),
                "target_ref": target_ref,
            }
    column_final_entry_insert_match = re.search(
        r"\bin\s+(?:the\s+)?"
        r"(?:(?P<column_ordinal>first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\s+column|"
        r"column\s+(?P<column_number>\d+))"
        r"(?:\s+of\s+(?:(?:the|that)\s+)?table)?\b"
        r".*?\bafter\s+(?:the\s+)?final\s+entry\s+"
        r"(?:insert(?:ed)?|there\s+(?:shall\s+be|is|are)\s+inserted)\s*[—–-]?\s*"
        r"(?P<payload>.+)$",
        text,
        re.I,
    )
    if column_final_entry_insert_match is not None:
        column_token = (
            column_final_entry_insert_match.group("column_ordinal")
            or column_final_entry_insert_match.group("column_number")
        )
        column_index = _uk_ordinal_to_int(column_token or "")
        inserted_text = _strip_schedule_entry_payload(
            column_final_entry_insert_match.group("payload")
        )
        if column_index is not None and column_index >= 1 and inserted_text:
            return {
                "rule_id": UK_TABLE_ENTRY_ROW_INSERT_RULE_ID,
                "selector_mode": "column_final_entry",
                "direction": "after",
                "column_index": column_index,
                "entry_index": 1,
                "relating_text": "final entry",
                "inserted_text": inserted_text,
                "source_payload_mode": "column_entry_text",
                "table_label": table_match.group(1) if table_match is not None else "",
                "source_names_table": source_names_table,
                "original_target": str(target),
                "target_ref": target_ref,
            }
    each_column_final_entry_insert_match = re.search(
        r"\bin\s+each\s+column\s+of\s+(?:the\s+)?table\b"
        r".*?\bafter\s+(?:the\s+)?final\s+entry\s+"
        r"(?:insert(?:ed)?|there\s+(?:shall\s+be|is|are)\s+inserted)\s*[—–-]?\s*"
        r"(?P<payload>.+)$",
        text,
        re.I,
    )
    if each_column_final_entry_insert_match is not None:
        inserted_text = _strip_schedule_entry_payload(
            each_column_final_entry_insert_match.group("payload")
        )
        if inserted_text:
            return {
                "rule_id": UK_TABLE_ENTRY_ROW_INSERT_RULE_ID,
                "selector_mode": "each_column_final_entry",
                "direction": "after",
                "column_index": 1,
                "entry_index": 1,
                "relating_text": "final entry",
                "inserted_text": inserted_text,
                "source_payload_mode": "each_column_entry_text",
                "table_label": table_match.group(1) if table_match is not None else "",
                "source_names_table": source_names_table,
                "original_target": str(target),
                "target_ref": target_ref,
            }
    if implicit_subsection_entry_group is not None and not source_names_table:
        relating_text = " ".join(implicit_subsection_entry_group.group("relating").split()).strip(" ,;.")
        inserted_text = _strip_schedule_entry_payload(implicit_subsection_entry_group.group("payload"))
        if relating_text:
            return {
                "rule_id": UK_TABLE_ENTRY_ROW_INSERT_RULE_ID,
                "selector_mode": "entry_group_heading",
                "direction": "after",
                "relating_text": relating_text,
                "inserted_text": inserted_text,
                "source_payload_mode": "table_rows",
                "source_names_table": False,
                "original_target": str(target),
                "target_ref": target_ref,
            }
    match = re.search(
        r"\bafter\s+the\s+"
        r"(?P<entry>first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|\d+(?:st|nd|rd|th)?)\s+"
        r"entry\s+in\s+the\s+"
        r"(?P<column>first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|\d+(?:st|nd|rd|th)?)\s+"
        r"column\s+relating\s+to\s+(?:the\s+)?(?P<relating>.+?)\s+"
        r"insert(?:ed)?\s*[—–-]?\s*(?P<payload>.+)$",
        text,
        re.I,
    )
    if match is not None:
        entry_index = _uk_ordinal_to_int(match.group("entry"))
        column_index = _uk_ordinal_to_int(match.group("column"))
        relating_text = " ".join(match.group("relating").split()).strip(" ,;.")
        inserted_text = _strip_schedule_entry_payload(match.group("payload"))
        if (
            entry_index is None
            or column_index is None
            or entry_index < 1
            or column_index < 2
            or not relating_text
            or not inserted_text
        ):
            return None
        return {
            "rule_id": UK_TABLE_ENTRY_ROW_INSERT_RULE_ID,
            "selector_mode": "ordinal_column",
            "direction": "after",
            "column_index": column_index,
            "entry_index": entry_index,
            "relating_text": relating_text,
            "inserted_text": inserted_text,
            "table_label": table_match.group(1) if table_match is not None else "",
            "source_names_table": source_names_table,
            "original_target": str(target),
            "target_ref": target_ref,
        }
    relating_match = re.search(
        r"\bafter\s+(?:the\s+)?entry\s+in\s+the\s+table\s+"
        r"relating\s+to\s+(?:the\s+)?(?P<relating>.+?)\s+"
        r"insert(?:ed)?\s*[—–-]?\s*(?P<payload>.+)$",
        text,
        re.I,
    )
    if relating_match is None:
        return None
    relating_text = " ".join(relating_match.group("relating").split()).strip(" ,;.")
    inserted_text = _strip_schedule_entry_payload(relating_match.group("payload"))
    if not relating_text or not inserted_text:
        return None
    return {
        "rule_id": UK_TABLE_ENTRY_ROW_INSERT_RULE_ID,
        "selector_mode": "relating_entry",
        "direction": "after",
        "column_index": 1,
        "entry_index": 1,
        "relating_text": relating_text,
        "inserted_text": inserted_text,
        "table_label": table_match.group(1) if table_match is not None else "",
        "source_names_table": source_names_table,
        "original_target": str(target),
        "target_ref": target_ref,
    }


def _uk_table_entry_row_replace_selector(
    *,
    target_ref: str,
    target: LegalAddress,
    extracted_text: Optional[str],
) -> dict[str, Any] | None:
    """Extract a source-owned table-entry replacement selector."""
    text = " ".join((extracted_text or "").split())
    if not text:
        return None
    target_surface = f"{target_ref} {target}".lower()
    if "table" not in target_surface and "table" not in text.lower():
        return None
    match = re.search(
        r"\bfor\s+the\s+entries\s+relating\s+to\s+(?:the\s+)?(?P<anchors>.+?)\s+"
        r"substitut(?:e|ed)\s*[—–-]?",
        text,
        re.I,
    )
    if match is None:
        return None
    anchors_text = " ".join(match.group("anchors").split()).strip(" ,;.")
    relating_texts = tuple(
        re.sub(r"^the\s+", "", part.strip(" ,;."), flags=re.I)
        for part in re.split(r"\s+and\s+|,\s*", anchors_text)
        if part.strip(" ,;.")
    )
    if len(relating_texts) < 2:
        return None
    return {
        "rule_id": UK_TABLE_ENTRY_ROW_REPLACE_RULE_ID,
        "selector_mode": "relating_entries",
        "relating_texts": relating_texts,
        "source_payload_mode": "table_rows",
        "source_names_table": "table" in text.lower(),
        "original_target": str(target),
        "target_ref": target_ref,
    }


def _uk_table_column_text_patch_selector(
    *,
    target_ref: str,
    target: LegalAddress,
    extracted_text: Optional[str],
) -> dict[str, Any] | None:
    """Extract a unique-column preimage selector for broad schedule/part table text patches."""
    text = " ".join((extracted_text or "").split())
    if not text:
        return None
    if "table" in " ".join((target_ref, str(target))).lower():
        return None
    if re.search(r"\b(?:entry|entries)\b", text, flags=re.I):
        return None
    target_kind = target.path[-1][0] if target.path else ""
    if target_kind not in {"schedule", "part"}:
        return None
    column_match = re.search(
        r"\bin\s+column\s+(?P<column>first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|\d+(?:st|nd|rd|th)?)\b",
        text,
        flags=re.I,
    )
    if column_match is None:
        return None
    column_index = _uk_ordinal_to_int(column_match.group("column"))
    if column_index is None or column_index < 1:
        return None
    fragments = parse_fragment_substitution(text)
    if not fragments:
        return None
    match_text = str(fragments[0].get("original") or "").strip()
    if not match_text:
        return None
    return {
        "rule_id": UK_TABLE_COLUMN_TEXT_PATCH_RULE_ID,
        "selector_mode": "unique_column_text",
        "column_index": column_index,
        "match_text": match_text,
        "target_ref": target_ref,
        "original_target": str(target),
    }


def _uk_broad_table_entry_instruction(
    *,
    target_ref: str,
    target: LegalAddress,
    extracted_text: Optional[str],
    extracted_el: Optional[ET.Element] = None,
    effect_type: str = "",
) -> dict[str, Any] | None:
    """Detect table-entry instructions that are unsafe as broad host mutations."""
    text = " ".join((extracted_text or "").split())
    if not text:
        return None
    target_surface = f"{target_ref} {target}".lower()
    target_names_table = "table" in target_surface
    norm = text.lower()
    if "corresponding entry" in norm:
        return None
    if not target_names_table and not re.search(r"\b(?:table|column|columns)\b", norm):
        return None
    has_entry_text = (
        re.search(r"\b(?:entry|entries)\b", norm) is not None
        or (target_names_table and re.search(r"\bafter\s+(?:that\s+)?entry\s+[0-9A-Za-z]+\b", norm) is not None)
        or (target_names_table and re.search(r"\bafter\s+that\s+entry\b", norm) is not None)
        or (
            target_names_table
            and re.search(r"\bafter\s+the\s+entry\s+in\s+the\s+table\s+relating\s+to\b", norm) is not None
        )
        or (
            target_names_table
            and re.search(r"\bat\s+the\s+appropriate\s+place\b", norm) is not None
        )
    )
    has_column_instruction = (
        re.search(r"\bin\s+column\s+\d+\b|\bin\s+the\s+\w+\s+column\b", norm) is not None
        or (
            target_names_table
            and re.search(
                r"\bbetween\s+the\s+\w+\s+and\s+\w+\s+columns?\b",
                norm,
            )
            is not None
        )
    )
    if not has_entry_text and not has_column_instruction:
        return None
    effect_type_norm = str(effect_type or "").strip().lower()
    source_supplies_action = re.search(
        r"\b(?:insert|inserted|substitute|substituted|omit|omitted|repeal|repealed|amend|amended|add|added)\b",
        norm,
    ) is not None
    effect_supplies_entry_action = effect_type_norm in {
        "entry inserted",
        "entry repealed",
        "entry omitted",
    }
    if not source_supplies_action and not effect_supplies_entry_action:
        return None
    if re.search(r"\bafter\s+that\s+entry\b", norm):
        entry_shape = "deictic_table_entry"
    elif re.search(r"\b(?:after|before)\s+entry\s+[0-9A-Za-z]+\b", norm):
        entry_shape = "numbered_entry"
    elif re.search(r"\bafter\s+the\s+entry\s+in\s+the\s+table\s+relating\s+to\b", norm):
        entry_shape = "relating_entry"
    elif target_names_table and re.search(r"\b(?:the\s+)?(?:entry|entries)\s+relating\s+to\b", norm):
        entry_shape = "relating_entry"
    elif target_names_table and re.search(r"\bat\s+the\s+appropriate\s+place\b", norm):
        entry_shape = "appropriate_place_table_entry"
    elif re.search(r"\bbetween\s+the\s+\w+\s+and\s+\w+\s+columns?\b", norm):
        entry_shape = "between_columns"
    elif re.search(r"\b(?:in|after|before)\s+(?:the\s+)?(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|\d+(?:st|nd|rd|th)?)\s+entry\b", norm):
        entry_shape = "ordinal_entry"
    elif re.search(r"\bentry\s+number\s+\d+\b", norm):
        entry_shape = "numbered_entry"
    elif re.search(r"\bentries?\s+specified\b", norm):
        entry_shape = "specified_entries"
    elif has_column_instruction:
        entry_shape = "column_instruction"
    else:
        return None
    return {
        "target_ref": target_ref,
        "target": str(target),
        "entry_shape": entry_shape,
        "inserted_table_rows": _inserted_table_payload_rows(extracted_el),
        "source_action": (
            "source_text"
            if source_supplies_action
            else f"effect_type:{effect_type_norm}"
        ),
    }


def _uk_embedded_table_payload_structural_substitution(
    *,
    target_ref: str,
    target: LegalAddress,
    extracted_text: Optional[str],
) -> dict[str, Any] | None:
    """Detect paragraph-level substitution sources whose payload embeds a table.

    This is not a table-entry instruction even though the flattened source text
    contains "table", "entry", and "column" words. The executable source action
    is the structural substitution before the amendment payload.
    """
    text = " ".join((extracted_text or "").split())
    if not text:
        return None
    target_surface = f"{target_ref} {target}".lower()
    if "table" in target_surface:
        return None
    if _addr_leaf_kind(target) != "paragraph":
        return None
    norm = text.lower()
    if not re.search(r"\b(?:table|column|columns|entry|entries)\b", norm):
        return None
    if not re.search(
        r"\bfor\s+paragraph\s+\(?[0-9A-Za-z]+\)?\s+(?:there\s+is\s+)?substitut(?:e|ed)\b",
        norm,
    ):
        return None
    return {
        "target_ref": target_ref,
        "target": str(target),
        "source_action": "paragraph_substitution",
    }


def _uk_embedded_table_payload_structural_insertion(
    *,
    target_ref: str,
    target: LegalAddress,
    extracted_text: Optional[str],
) -> dict[str, Any] | None:
    """Detect structural insertion sources whose payload embeds table vocabulary.

    Large UK inserted blocks can contain the words "table", "entry", or
    "column" inside the new provision body. That vocabulary is payload text, not
    a table-entry amendment instruction, when the source action is an explicit
    structural insertion and the affected target is one of the inserted
    provisions.
    """
    text = " ".join((extracted_text or "").split())
    if not text:
        return None
    target_surface = f"{target_ref} {target}".lower()
    if "table" in target_surface:
        return None
    target_kind = _addr_leaf_kind(target)
    target_label = _addr_leaf_label(target)
    if target_kind not in {"section", "paragraph", "article"} or not target_label:
        return None
    norm = text.lower()
    if not re.search(r"\b(?:table|column|columns|entry|entries)\b", norm):
        return None
    if not re.search(
        r"\b(?:after|before)\s+(?:section|s\.|paragraph|para\.|article|art\.)\s+"
        r"[0-9a-z().]+\s+insert(?:ed)?\b",
        norm,
    ):
        return None
    if re.search(rf"\b{re.escape(target_label.lower())}\b", norm) is None:
        return None
    return {
        "target_ref": target_ref,
        "target": str(target),
        "source_action": "structural_insertion",
        "target_kind": target_kind,
    }


def _uk_parent_target_before_table_marker(target: LegalAddress) -> LegalAddress | None:
    path: list[tuple[str, str | None]] = []
    for kind, label in target.path:
        kind_norm = str(kind or "").lower()
        label_norm = str(label or "").lower()
        if kind_norm in {"table", "cell", "row"} or label_norm == "table":
            break
        path.append((kind, label))
    if not path or len(path) == len(target.path):
        return None
    return LegalAddress(path=tuple(path), special=target.special)


def _uk_schedule_table_end_rows_selector(
    *,
    target_ref: str,
    target: LegalAddress,
    extracted_text: Optional[str],
) -> dict[str, Any] | None:
    """Detect source-owned table rows inserted at the end of a schedule table."""
    if _addr_container(target) != "schedule" or _addr_leaf_kind(target) != "schedule":
        return None
    text = " ".join((extracted_text or "").split())
    if not text:
        return None
    raw_target_label = _addr_leaf_label(target)
    if not raw_target_label:
        return None
    target_label = re.escape(raw_target_label)
    if not re.search(
        rf"\bat\s+the\s+end\s+of\s+schedule\s+{target_label}\b.*\binsert\b",
        text,
        re.I,
    ):
        return None
    return {
        "rule_id": UK_SCHEDULE_TABLE_END_ROWS_RULE_ID,
        "direction": "end",
        "target": str(target),
        "target_ref": target_ref,
        "source_phrase": "at the end of schedule",
    }


def _uk_schedule_list_entry_table_payload(extracted_el: Optional[ET.Element]) -> IRNode | None:
    amendment = _first_amendment_container(extracted_el)
    if amendment is None or _tag(amendment) != "BlockAmendment":
        return None
    tables = [
        el
        for el in amendment.iter()
        if el is not amendment and _tag(el).lower() == "table"
    ]
    if len(tables) != 1:
        return None
    table_node = _parse_uk_table_payload(tables[0], None, force_active=True)
    if table_node is None or _uk_kind_value(table_node.kind) != "table":
        return None
    row_children = [
        child
        for child in table_node.children
        if _uk_kind_value(child.kind) == "row"
    ]
    if not row_children:
        return None
    return table_node.to_irnode()


def _source_root_element_by_id(
    source_root: Optional[ET.Element],
    extracted_el: Optional[ET.Element],
) -> Optional[ET.Element]:
    if source_root is None or extracted_el is None:
        return extracted_el
    extracted_id = str(extracted_el.get("id") or "")
    if not extracted_id:
        return extracted_el
    for candidate in source_root.iter():
        if str(candidate.get("id") or "") == extracted_id:
            return candidate
    return extracted_el


def _uk_column_entry_list_row_payload(
    extracted_el: Optional[ET.Element],
    *,
    source_root: Optional[ET.Element] = None,
    column_index: int,
) -> IRNode | None:
    extracted_el = _source_root_element_by_id(source_root, extracted_el)
    amendment = _first_amendment_container(extracted_el)
    if amendment is None or _tag(amendment) != "BlockAmendment" or column_index < 1:
        return None
    lists = [
        el
        for el in amendment.iter()
        if el is not amendment and _tag(el) == "UnorderedList"
    ]
    if len(lists) != 1:
        return None
    rows: list[IRNode] = []
    for item in list(lists[0]):
        if _tag(item) != "ListItem":
            continue
        text = _strip_schedule_entry_payload(_normalized_element_text(item))
        if not text:
            continue
        rows.append(
            IRNode(
                kind=IRNodeKind.ROW,
                label=None,
                attrs={"source_rule_id": "uk_table_column_entry_list_row"},
                children=tuple(
                    IRNode(
                        kind=IRNodeKind.CELL,
                        label=None,
                        text=text if cell_index == column_index else "",
                        attrs={
                            "source_rule_id": "uk_table_column_entry_list_cell",
                            "column_index": str(cell_index),
                        },
                    )
                    for cell_index in range(1, column_index + 1)
                ),
            )
        )
    if not rows:
        return None
    return IRNode(
        kind=IRNodeKind.TABLE,
        label=None,
        attrs={
            "source_rule_id": "uk_table_column_entry_list_rows_payload",
            "target_column_index": str(column_index),
        },
        children=tuple(rows),
    )


def _column_end_payload_needs_owned_list(inserted_text: str) -> bool:
    text = " ".join(inserted_text.split()).strip()
    return bool(
        re.search(
            r";\s+(?:section|sections|regulations|paragraph|paragraphs|chapter)\b",
            text,
            re.I,
        )
        or re.search(
            r"\.\s+(?:Section|Sections|Regulations|Paragraph|Paragraphs|Chapter)\b",
            text,
            re.I,
        )
    )


def _uk_single_table_row_payload(extracted_el: Optional[ET.Element]) -> IRNode | None:
    table_node = _uk_schedule_list_entry_table_payload(extracted_el)
    if table_node is None:
        return None
    rows = tuple(
        child
        for child in table_node.children
        if _uk_kind_value(child.kind).lower() == "row"
    )
    if len(rows) != 1:
        return None
    return rows[0]


def _uk_single_logical_table_entry_group_payload(extracted_el: Optional[ET.Element]) -> IRNode | None:
    """Return one logical table entry encoded as multiple source rows via rowspan."""
    table_node = _uk_schedule_list_entry_table_payload(extracted_el)
    if table_node is None:
        return None
    rows = tuple(
        child for child in table_node.children if _uk_kind_value(child.kind).lower() == "row"
    )
    if len(rows) <= 1:
        return None
    first_row_cells = tuple(
        child
        for child in rows[0].children
        if _uk_kind_value(child.kind).lower() in {"cell", "header_cell"}
    )
    if len(first_row_cells) < 2:
        return None
    first_cell = first_row_cells[0]
    try:
        rowspan = int(str(first_cell.attrs.get("rowspan") or "1"))
    except ValueError:
        rowspan = 1
    try:
        morerows = int(str(first_cell.attrs.get("morerows") or "0"))
    except ValueError:
        morerows = 0
    if morerows:
        rowspan = max(rowspan, morerows + 1)
    if rowspan != len(rows):
        return None
    for row in rows[1:]:
        row_cells = tuple(
            child
            for child in row.children
            if _uk_kind_value(child.kind).lower() in {"cell", "header_cell"}
        )
        if not row_cells:
            return None
    return table_node


def _uk_single_table_column_payload(extracted_el: Optional[ET.Element]) -> IRNode | None:
    """Return a source-owned one-column table payload, if exactly one is present."""
    table_node = _uk_schedule_list_entry_table_payload(extracted_el)
    if table_node is None:
        return None
    rows = tuple(child for child in table_node.children if _uk_kind_value(child.kind).lower() == "row")
    if not rows:
        return None
    for row in rows:
        cells = tuple(
            child
            for child in row.children
            if _uk_kind_value(child.kind).lower() in {"cell", "header_cell"}
        )
        if len(cells) != 1:
            return None
    return table_node


def _uk_table_column_insert_selector(
    *,
    target_ref: str,
    target: LegalAddress,
    extracted_text: Optional[str],
    extracted_el: Optional[ET.Element],
) -> dict[str, Any] | None:
    """Extract a source-owned ``between columns`` table-column insert selector."""
    target_surface = f"{target_ref} {target}".lower()
    if "table" not in target_surface:
        return None
    text = " ".join((extracted_text or "").split())
    if not text:
        return None
    match = re.search(
        r"\bbetween\s+the\s+"
        r"(?P<after>first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|\d+(?:st|nd|rd|th)?)\s+"
        r"and\s+"
        r"(?P<before>first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|\d+(?:st|nd|rd|th)?)\s+"
        r"columns?\b",
        text,
        re.I,
    )
    if match is None or re.search(r"\binsert(?:ed)?\b", text, re.I) is None:
        return None
    after_column_index = _uk_ordinal_to_int(match.group("after"))
    before_column_index = _uk_ordinal_to_int(match.group("before"))
    if (
        after_column_index is None
        or before_column_index is None
        or after_column_index < 1
        or before_column_index != after_column_index + 1
    ):
        return None
    payload = _uk_single_table_column_payload(extracted_el)
    if payload is None:
        return None
    payload_rows = tuple(child for child in payload.children if _uk_kind_value(child.kind).lower() == "row")
    return {
        "rule_id": UK_TABLE_COLUMN_INSERT_RULE_ID,
        "selector_mode": "between_columns",
        "after_column_index": after_column_index,
        "before_column_index": before_column_index,
        "source_payload_mode": "single_column_table",
        "payload_row_count": len(payload_rows),
        "target_ref": target_ref,
        "original_target": str(target),
    }

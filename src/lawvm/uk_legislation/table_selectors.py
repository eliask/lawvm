from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any, Optional

from lawvm.core.ir import IRNode, LegalAddress
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
    _source_previous_table_entry_label_context,
    _source_previous_table_entry_relating_context,
)
from lawvm.uk_legislation.uk_grafter import _clean_num, _parse_table as _parse_uk_table_payload
from lawvm.uk_legislation.xml_helpers import _tag


UK_TABLE_ENTRY_INLINE_TEXT_RULE_ID = "uk_effect_table_entry_inline_text_insertion"
UK_TABLE_ENTRY_RELATING_TEXT_RULE_ID = "uk_effect_table_entry_relating_text_patch"
UK_TABLE_ENTRY_RELATING_COLUMN_TEXT_RULE_ID = "uk_effect_table_entry_relating_column_text_patch"
UK_TABLE_ENTRY_LABEL_TEXT_RULE_ID = "uk_effect_table_entry_label_text_patch"
UK_TABLE_ENTRY_LABEL_COLUMN_TEXT_RULE_ID = "uk_effect_table_entry_label_column_text_patch"
UK_TABLE_ENTRY_LABELS_COLUMN_TEXT_RULE_ID = "uk_effect_table_entry_labels_column_text_patch"
UK_TABLE_ENTRY_DEICTIC_LABEL_COLUMN_TEXT_RULE_ID = (
    "uk_effect_table_entry_deictic_label_column_text_patch"
)
UK_TABLE_COLUMN_HEADING_TEXT_RULE_ID = "uk_effect_table_column_heading_text_patch"
UK_TABLE_COLUMN_TEXT_PATCH_RULE_ID = "uk_effect_table_column_text_patch"
UK_TABLE_COLUMN_INSERT_RULE_ID = "uk_effect_table_column_insert"
UK_TABLE_ENTRY_ROW_INSERT_RULE_ID = "uk_effect_table_entry_row_insert"
UK_TABLE_ENTRY_INSTRUCTION_REJECTED_RULE_ID = "uk_effect_table_entry_instruction_rejected"
UK_SCHEDULE_TABLE_END_ROWS_RULE_ID = "uk_effect_schedule_table_end_rows_lowered"


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
    if not text or "table" not in " ".join((target_ref, str(target))).lower():
        return None
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

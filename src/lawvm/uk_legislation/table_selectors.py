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
from lawvm.uk_legislation.ordinals import _uk_ordinal_to_int
from lawvm.uk_legislation.source_context import _first_amendment_container
from lawvm.uk_legislation.uk_grafter import _parse_table as _parse_uk_table_payload
from lawvm.uk_legislation.xml_helpers import _tag


UK_TABLE_COLUMN_INSERT_RULE_ID = "uk_effect_table_column_insert"
UK_SCHEDULE_TABLE_END_ROWS_RULE_ID = "uk_effect_schedule_table_end_rows_lowered"


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

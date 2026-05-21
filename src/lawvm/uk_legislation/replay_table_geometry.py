"""UK replay table geometry helpers."""
from __future__ import annotations

from typing import Any

from lawvm.uk_legislation.addressing import _uk_kind_value
from lawvm.uk_legislation.mutable_ir import UKMutableNode
from lawvm.uk_legislation.replay_text import _compact_normalized_text
from lawvm.uk_legislation.uk_grafter import _clean_num


def strip_uk_identity_attrs_recursive(node: UKMutableNode) -> None:
    for key in ("eId", "id"):
        node.attrs.pop(key, None)
    for child in node.children:
        strip_uk_identity_attrs_recursive(child)


def uk_table_cell_span(cell: UKMutableNode) -> tuple[int, int]:
    try:
        rowspan = int(str(cell.attrs.get("rowspan") or "1"))
    except ValueError:
        rowspan = 1
    try:
        morerows = int(str(cell.attrs.get("morerows") or "0"))
    except ValueError:
        morerows = 0
    if morerows:
        rowspan = max(rowspan, morerows + 1)
    try:
        colspan = int(str(cell.attrs.get("colspan") or "1"))
    except ValueError:
        colspan = 1
    return max(rowspan, 1), max(colspan, 1)


def expanded_uk_table_rows(table: UKMutableNode) -> list[dict[int, UKMutableNode]]:
    return [row_cells for _, row_cells in expanded_uk_table_rows_with_physical_index(table)]


def expanded_uk_table_rows_with_physical_index(
    table: UKMutableNode,
) -> list[tuple[int, dict[int, UKMutableNode]]]:
    rows: list[tuple[int, dict[int, UKMutableNode]]] = []
    active_rowspans: dict[int, tuple[int, UKMutableNode]] = {}
    for row_index, row in enumerate(table.children):
        if _uk_kind_value(row.kind).lower() != "row":
            continue
        row_cells: dict[int, UKMutableNode] = {
            col: cell for col, (_, cell) in active_rowspans.items()
        }
        next_rowspans: dict[int, tuple[int, UKMutableNode]] = {
            col: (remaining - 1, cell)
            for col, (remaining, cell) in active_rowspans.items()
            if remaining > 1
        }
        col = 1
        for cell in row.children:
            cell_kind = _uk_kind_value(cell.kind).lower()
            if cell_kind not in {"cell", "header_cell"}:
                continue
            while col in row_cells:
                col += 1
            rowspan, colspan = uk_table_cell_span(cell)
            for offset in range(colspan):
                current_col = col + offset
                row_cells[current_col] = cell
                if rowspan > 1:
                    next_rowspans[current_col] = (rowspan - 1, cell)
            col += colspan
        if row_cells:
            rows.append((row_index, row_cells))
        active_rowspans = next_rowspans
    return rows


def uk_table_column_payload_cells(
    new_node: UKMutableNode,
) -> tuple[list[UKMutableNode], str, dict[str, object]]:
    if _uk_kind_value(new_node.kind).lower() != "table":
        return [], "payload_not_table", {"payload_kind": _uk_kind_value(new_node.kind)}
    payload_rows = [
        row for row in new_node.children if _uk_kind_value(row.kind).lower() == "row"
    ]
    if not payload_rows:
        return [], "payload_has_no_rows", {"payload_row_count": 0}
    payload_cells: list[UKMutableNode] = []
    for row_index, row in enumerate(payload_rows):
        row_cells = [
            child
            for child in row.children
            if _uk_kind_value(child.kind).lower() in {"cell", "header_cell"}
        ]
        if len(row_cells) != 1:
            return [], "payload_not_single_column", {
                "payload_row_index": row_index,
                "payload_cell_count": len(row_cells),
                "payload_row_count": len(payload_rows),
            }
        cloned = UKMutableNode.from_irnode(row_cells[0].to_irnode())
        strip_uk_identity_attrs_recursive(cloned)
        payload_cells.append(cloned)
    return payload_cells, "", {"payload_row_count": len(payload_rows)}


def uk_table_column_insert_plans(
    table: UKMutableNode,
) -> list[dict[str, object]]:
    plans: list[dict[str, object]] = []
    active_rowspans: dict[int, tuple[int, UKMutableNode]] = {}
    for row_index, row in enumerate(table.children):
        if _uk_kind_value(row.kind).lower() != "row":
            continue
        row_cells: dict[int, UKMutableNode] = {
            col: cell for col, (_, cell) in active_rowspans.items()
        }
        next_rowspans: dict[int, tuple[int, UKMutableNode]] = {
            col: (remaining - 1, cell)
            for col, (remaining, cell) in active_rowspans.items()
            if remaining > 1
        }
        col = 1
        owned_ranges: list[tuple[int, int, UKMutableNode, int]] = []
        for physical_index, cell in enumerate(row.children):
            cell_kind = _uk_kind_value(cell.kind).lower()
            if cell_kind not in {"cell", "header_cell"}:
                continue
            while col in row_cells:
                col += 1
            rowspan, colspan = uk_table_cell_span(cell)
            start_col = col
            end_col = col + colspan - 1
            owned_ranges.append((start_col, end_col, cell, physical_index))
            for offset in range(colspan):
                current_col = col + offset
                row_cells[current_col] = cell
                if rowspan > 1:
                    next_rowspans[current_col] = (rowspan - 1, cell)
            col += colspan
        if row_cells:
            plans.append(
                {
                    "row_index": row_index,
                    "row": row,
                    "row_cells": row_cells,
                    "owned_ranges": owned_ranges,
                }
            )
        active_rowspans = next_rowspans
    return plans


def uk_table_selector_tables(
    node: UKMutableNode,
    selector: dict[str, Any],
) -> tuple[list[UKMutableNode], dict[str, Any]]:
    tables = [
        child
        for child in node.children
        if _uk_kind_value(child.kind).lower() == "table"
    ]
    if tables or not bool(selector.get("allow_implicit_subsection_one_table")):
        return tables, {"table_carrier": "target"}
    subsection_ones = [
        child
        for child in node.children
        if _uk_kind_value(child.kind).lower() == "subsection" and _clean_num(child.label or "") == "1"
    ]
    if len(subsection_ones) != 1:
        return [], {"table_carrier": "implicit_subsection_one", "subsection_one_count": len(subsection_ones)}
    tables = [
        child
        for child in subsection_ones[0].children
        if _uk_kind_value(child.kind).lower() == "table"
    ]
    return tables, {"table_carrier": "implicit_subsection_one", "subsection_one_count": 1}


def resolve_unique_uk_table_relating_cell(
    node: UKMutableNode,
    selector: dict[str, Any],
) -> tuple[UKMutableNode | None, str, dict[str, Any]]:
    try:
        column_index = int(selector.get("column_index") or 0)
    except (TypeError, ValueError):
        return None, "invalid_selector", {}
    relating_norm = _compact_normalized_text(str(selector.get("relating_text") or ""))
    if column_index < 1 or not relating_norm:
        return None, "invalid_selector", {}

    tables, carrier_detail = uk_table_selector_tables(node, selector)
    if len(tables) != 1:
        return None, "table_not_unique", {"table_count": len(tables), **carrier_detail}

    matching_cells: list[UKMutableNode] = []
    matching_rows: list[str] = []
    for row_cells in expanded_uk_table_rows(tables[0]):
        row_texts = [
            str(row_cells[col].text or "")
            for col in sorted(row_cells)
            if str(row_cells[col].text or "")
        ]
        if not any(_compact_normalized_text(text).find(relating_norm) >= 0 for text in row_texts):
            continue
        target_cell = row_cells.get(column_index)
        if target_cell is None:
            continue
        if not matching_cells or matching_cells[-1] is not target_cell:
            matching_cells.append(target_cell)
            matching_rows.append(" | ".join(row_texts)[:240])
    if len(matching_cells) == 1:
        return matching_cells[0], "", {
            "matching_cell_count": 1,
            "matched_row": matching_rows[0] if matching_rows else "",
            **carrier_detail,
        }
    reason = "relating_cell_not_found" if not matching_cells else "relating_cell_ambiguous"
    return None, reason, {
        "matching_cell_count": len(matching_cells),
        "matching_rows": tuple(matching_rows[:5]),
        **carrier_detail,
    }


def resolve_unique_uk_table_entry_text_cell(
    node: UKMutableNode,
    selector: dict[str, Any],
) -> tuple[UKMutableNode | None, str, dict[str, Any]]:
    match_norm = _compact_normalized_text(str(selector.get("match_text") or ""))
    selector_mode = str(selector.get("selector_mode") or "")
    relating_norm = _compact_normalized_text(str(selector.get("relating_text") or ""))
    entry_label_norm = _compact_normalized_text(str(selector.get("entry_label") or ""))
    if not match_norm or (
        selector_mode == "unique_relating_text" and not relating_norm
    ) or (selector_mode == "unique_entry_text" and not entry_label_norm):
        return None, "invalid_selector", {}

    tables, carrier_detail = uk_table_selector_tables(node, selector)
    if len(tables) != 1:
        return None, "table_not_unique", {"table_count": len(tables), **carrier_detail}

    matching_cells: list[UKMutableNode] = []
    matching_rows: list[str] = []
    for row_cells in expanded_uk_table_rows(tables[0]):
        row_texts = [
            str(row_cells[col].text or "")
            for col in sorted(row_cells)
            if str(row_cells[col].text or "")
        ]
        if selector_mode == "unique_relating_text" and not any(
            _compact_normalized_text(text).find(relating_norm) >= 0
            or _compact_normalized_text(text).find(match_norm) >= 0
            for text in row_texts
        ):
            continue
        if selector_mode == "unique_entry_text" and not any(
            _compact_normalized_text(text) == entry_label_norm for text in row_texts
        ):
            continue
        for cell in row_cells.values():
            if _compact_normalized_text(cell.text or "").find(match_norm) < 0:
                continue
            if not matching_cells or matching_cells[-1] is not cell:
                matching_cells.append(cell)
                matching_rows.append(" | ".join(row_texts)[:240])
    if len(matching_cells) == 1:
        return matching_cells[0], "", {
            "matching_cell_count": 1,
            "matched_row": matching_rows[0] if matching_rows else "",
            **carrier_detail,
        }
    reason = "cell_text_not_found" if not matching_cells else "cell_text_ambiguous"
    return None, reason, {
        "matching_cell_count": len(matching_cells),
        "matching_rows": tuple(matching_rows[:5]),
        **carrier_detail,
    }


def resolve_unique_uk_table_entry_cell(
    node: UKMutableNode,
    selector: dict[str, Any],
) -> tuple[UKMutableNode | None, str, dict[str, Any]]:
    try:
        column_index = int(selector.get("column_index") or 0)
    except (TypeError, ValueError):
        return None, "invalid_selector", {}
    entry_label_norm = _compact_normalized_text(str(selector.get("entry_label") or ""))
    if column_index < 1 or not entry_label_norm:
        return None, "invalid_selector", {}

    tables, carrier_detail = uk_table_selector_tables(node, selector)
    if len(tables) != 1:
        return None, "table_not_unique", {"table_count": len(tables), **carrier_detail}

    matching_cells: list[UKMutableNode] = []
    matching_rows: list[str] = []
    for row_cells in expanded_uk_table_rows(tables[0]):
        if not any(
            _compact_normalized_text(cell.text or "") == entry_label_norm
            for cell in row_cells.values()
        ):
            continue
        target_cell = row_cells.get(column_index)
        if target_cell is None:
            continue
        if not matching_cells or matching_cells[-1] is not target_cell:
            matching_cells.append(target_cell)
            matching_rows.append(
                " | ".join(
                    str(row_cells[col].text or "")
                    for col in sorted(row_cells)
                    if str(row_cells[col].text or "")
                )[:240]
            )
    if len(matching_cells) == 1:
        return matching_cells[0], "", {
            "matching_cell_count": 1,
            "matched_row": matching_rows[0] if matching_rows else "",
            **carrier_detail,
        }
    reason = "entry_cell_not_found" if not matching_cells else "entry_cell_ambiguous"
    return None, reason, {
        "matching_cell_count": len(matching_cells),
        "matching_rows": tuple(matching_rows[:5]),
        **carrier_detail,
    }


def resolve_unique_uk_table_column_text_cell(
    node: UKMutableNode,
    selector: dict[str, Any],
) -> tuple[UKMutableNode | None, str, dict[str, Any]]:
    try:
        column_index = int(selector.get("column_index") or 0)
    except (TypeError, ValueError):
        return None, "invalid_selector", {}
    match_norm = _compact_normalized_text(str(selector.get("match_text") or ""))
    if column_index < 1 or not match_norm:
        return None, "invalid_selector", {}

    tables, carrier_detail = uk_table_selector_tables(node, selector)
    if len(tables) != 1:
        return None, "table_not_unique", {"table_count": len(tables), **carrier_detail}

    matching_cells: list[UKMutableNode] = []
    matching_rows: list[str] = []
    for row_cells in expanded_uk_table_rows(tables[0]):
        target_cell = row_cells.get(column_index)
        if target_cell is None:
            continue
        if _compact_normalized_text(target_cell.text or "").find(match_norm) < 0:
            continue
        if not matching_cells or matching_cells[-1] is not target_cell:
            matching_cells.append(target_cell)
            matching_rows.append(
                " | ".join(
                    str(row_cells[col].text or "")
                    for col in sorted(row_cells)
                    if str(row_cells[col].text or "")
                )[:240]
            )

    if len(matching_cells) == 1:
        return matching_cells[0], "", {
            "matching_cell_count": 1,
            "matched_row": matching_rows[0] if matching_rows else "",
            **carrier_detail,
        }
    reason = "cell_text_not_found" if not matching_cells else "cell_text_ambiguous"
    return None, reason, {
        "matching_cell_count": len(matching_cells),
        "matching_rows": tuple(matching_rows[:5]),
        **carrier_detail,
    }

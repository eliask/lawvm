"""UK replay table geometry helpers."""
from __future__ import annotations

from lawvm.uk_legislation.addressing import _uk_kind_value
from lawvm.uk_legislation.mutable_ir import UKMutableNode


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

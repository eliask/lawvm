"""UK replay table geometry helpers."""
from __future__ import annotations

from lawvm.uk_legislation.addressing import _uk_kind_value
from lawvm.uk_legislation.mutable_ir import UKMutableNode


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
